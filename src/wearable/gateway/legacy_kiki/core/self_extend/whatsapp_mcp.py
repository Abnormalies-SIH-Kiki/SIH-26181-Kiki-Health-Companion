"""Long-lived, latency-isolated client for the bundled WhatsApp MCP server.

The MCP stdio process and WhatsApp bridge live outside Kiki's asyncio/speaking
path. Startup is fire-and-forget; a tool call waits only when WhatsApp itself is
explicitly requested. The module intentionally imports the MCP SDK inside its
background thread so importing Kiki's normal voice stack stays cheap.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

_SPACE_RE = re.compile(r"\s+")


_ROOT = Path(__file__).resolve().parents[2]
_WHATSAPP_ROOT = _ROOT / "whatsapp-mcp"
_BRIDGE_DIR = _WHATSAPP_ROOT / "whatsapp-bridge"
_MCP_DIR = _WHATSAPP_ROOT / "whatsapp-mcp-server"
_MCP_MAIN = _MCP_DIR / "main.py"
_LOG_DIR = _ROOT / "logs"
_BRIDGE_PID_FILE = _LOG_DIR / "whatsapp-bridge.pid"
_EXPECTED_TOOLS = {
    "search_contacts",
    "list_messages",
    "list_chats",
    "get_chat",
    "get_direct_chat_by_contact",
    "get_contact_chats",
    "get_last_interaction",
    "get_message_context",
    "send_message",
    "send_file",
    "send_audio_message",
    "download_media",
}


def _tcp_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, TypeError, ValueError):
        return 0


def launch_whatsapp_bridge_background(config: Optional[dict] = None):
    """Start the Go bridge without waiting for compilation or WhatsApp login.

    Safe to call from both ``kiki_boot.py`` and the MCP client fallback. A PID
    file prevents a second ``go run`` while the first one is still compiling.
    """
    cfg = dict(config or {})
    if not cfg.get("enabled", True) or not cfg.get("start_bridge", True):
        return None

    host = str(cfg.get("bridge_host", "127.0.0.1"))
    port = int(cfg.get("bridge_port", 8080))
    if _tcp_open(host, port):
        return None
    existing_pid = _read_pid(_BRIDGE_PID_FILE)
    if _pid_alive(existing_pid):
        return None

    go_bin = str(cfg.get("go_binary") or shutil.which("go") or "go")
    built_binary = _BRIDGE_DIR / "whatsapp-bridge"
    if built_binary.is_file() and os.access(built_binary, os.X_OK):
        command = [str(built_binary)]
    else:
        command = [go_bin, "run", "main.go"]
    nice_bin = shutil.which("nice")
    if nice_bin:
        command = [nice_bin, "-n", str(int(cfg.get("bridge_nice", 10)))] + command

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = Path(cfg.get("bridge_log_file") or _LOG_DIR / "whatsapp-bridge.log")
    log_handle = None
    try:
        log_handle = open(log_path, "ab", buffering=0)
        process = subprocess.Popen(
            command,
            cwd=str(_BRIDGE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _BRIDGE_PID_FILE.write_text(str(process.pid))
        print(f"[WhatsApp] Bridge startup queued (PID {process.pid})")
        return process
    except Exception as exc:
        print(f"[WhatsApp] Bridge startup failed without blocking Kiki: {exc}")
        return None
    finally:
        if log_handle is not None:
            log_handle.close()


def _text_content(result) -> str:
    parts = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _truncate_values(value: Any, string_limit: int = 1200) -> Any:
    if isinstance(value, dict):
        return {str(key): _truncate_values(item, string_limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_values(item, string_limit) for item in value]
    if isinstance(value, str) and len(value) > string_limit:
        return value[:string_limit].rstrip() + f"…[+{len(value) - string_limit} chars]"
    return value


class WhatsAppMCPClient:
    """Own one MCP server/session on a daemon event-loop thread."""

    def __init__(self, config: Optional[dict] = None):
        self.config = dict(config or {})
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._session = None
        self._async_call_lock = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._state_lock = threading.RLock()
        self._last_error = ""
        self._bridge_process = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._session is not None

    @property
    def last_error(self) -> str:
        return self._last_error

    def configure(self, config: Optional[dict]):
        if config:
            with self._state_lock:
                self.config.update(config)

    def start(self):
        if not self.config.get("enabled", True):
            return
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="whatsapp-mcp",
                daemon=True,
            )
            self._thread.start()

    def _thread_main(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            self._last_error = str(exc)
            print(f"[WhatsApp] MCP client stopped: {exc}")
        finally:
            self._session = None
            self._loop = None
            self._ready.clear()

    async def _serve(self):
        # Delayed imports keep the foreground import/boot path independent of
        # the MCP SDK and any dependency initialization.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._loop = asyncio.get_running_loop()
        self._async_call_lock = asyncio.Lock()
        self._bridge_process = launch_whatsapp_bridge_background(self.config)

        python_bin = str(self.config.get("python_binary") or sys.executable)
        params = StdioServerParameters(
            command=python_bin,
            args=[str(_MCP_MAIN)],
            cwd=str(_MCP_DIR),
            env=os.environ.copy(),
        )
        log_path = Path(
            self.config.get("mcp_log_file") or _LOG_DIR / "whatsapp-mcp.log")
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                timeout = timedelta(
                    seconds=float(self.config.get("call_timeout_seconds", 20)))
                async with ClientSession(
                    read_stream, write_stream, read_timeout_seconds=timeout
                ) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    names = {tool.name for tool in listed.tools}
                    missing = sorted(_EXPECTED_TOOLS - names)
                    if missing:
                        raise RuntimeError(
                            "WhatsApp MCP missing tools: " + ", ".join(missing))
                    self._session = session
                    self._last_error = ""
                    self._ready.set()
                    print(f"[WhatsApp] MCP ready ({len(names)} tools)")
                    while not self._stop.is_set():
                        await asyncio.sleep(0.25)

    async def _call_async(self, name: str, arguments: dict, timeout: float):
        if self._session is None:
            raise RuntimeError("WhatsApp MCP session is not ready")
        async with self._async_call_lock:
            result = await self._session.call_tool(
                name,
                arguments=arguments,
                read_timeout_seconds=timedelta(seconds=timeout),
            )
        text = _text_content(result)
        if getattr(result, "isError", False):
            raise RuntimeError(text or f"WhatsApp tool {name} failed")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        if structured is not None:
            return structured
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return None

    def call_tool(self, name: str, arguments: Optional[dict] = None,
                  timeout: Optional[float] = None) -> Any:
        if name not in _EXPECTED_TOOLS:
            raise ValueError(f"Unknown WhatsApp MCP tool: {name}")
        self.start()
        call_timeout = float(
            timeout or self.config.get("call_timeout_seconds", 20))
        ready_timeout = min(
            call_timeout,
            float(self.config.get("ready_timeout_seconds", 8)),
        )
        if not self._ready.wait(max(0.05, ready_timeout)):
            detail = f": {self._last_error}" if self._last_error else ""
            raise TimeoutError(f"WhatsApp MCP is still starting{detail}")
        loop = self._loop
        if loop is None:
            raise RuntimeError("WhatsApp MCP event loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(
            self._call_async(name, dict(arguments or {}), call_timeout), loop)
        try:
            return future.result(timeout=call_timeout + 1)
        except FuturesTimeoutError:
            # CANCEL, don't just stop waiting. The coroutine holds
            # _async_call_lock until the server replies, so an abandoned slow
            # call (a cold media download) keeps every later WhatsApp request
            # queued behind it — that is how one image-heavy chat stalled the
            # session for over a minute after its caller had already given up.
            future.cancel()
            raise

    def stop(self):
        self._stop.set()
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
        process = self._bridge_process
        if process is not None:
            try:
                # ``go run`` execs the compiled bridge as a child. Terminating
                # only the wrapper leaves that child (and :8080) orphaned, so
                # stop the dedicated session/process group we created.
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass


_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def get_whatsapp_mcp(config: Optional[dict] = None) -> WhatsAppMCPClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = WhatsAppMCPClient(config)
        else:
            _CLIENT.configure(config)
        return _CLIENT


def start_whatsapp_mcp_background(full_config: Optional[dict] = None):
    cfg = (full_config or {}).get("whatsapp", {}) if full_config else {}
    client = get_whatsapp_mcp(cfg)
    client.start()
    return client


# --- Recipient resolution --------------------------------------------------
# Two problems this has to solve at once:
#   1. `search_contacts` filters groups out at the SQL level
#      (`AND jid NOT LIKE '%@g.us'`), so a group name could NEVER resolve
#      through it. Group chats only appear via `list_chats`.
#   2. Names reach us through speech recognition, so they are frequently
#      mangled — "burrito time" for the group "Burgito". A substring LIKE
#      query cannot bridge that; fuzzy scoring can.

# Below this, a name is not considered a match at all.
_FUZZY_ACCEPT = 0.62
# A win by less than this over the runner-up is a tie → ask instead of guessing.
_FUZZY_MARGIN = 0.08
# Filler words that carry no identifying signal in a spoken chat name.
_NAME_STOPWORDS = frozenset({
    "the", "a", "an", "my", "our", "group", "chat", "team",
    "to", "in", "on", "at", "of", "and",
})


def _normalize_name(value: str) -> str:
    return _SPACE_RE.sub(" ", str(value or "").casefold().strip())


def _name_tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _normalize_name(value))
            if t and t not in _NAME_STOPWORDS]


def _name_score(query: str, name: str) -> float:
    """Similarity of a spoken name to a real chat name, in 0..1.

    Blends whole-string similarity with best-per-token similarity so that both
    "burrito time" → "Burgito" (token-level, one word dropped) and
    "burgito tyme" → "Burgito Time" (string-level typo) score well.
    """
    qnorm, nnorm = _normalize_name(query), _normalize_name(name)
    if not qnorm or not nnorm:
        return 0.0
    if qnorm == nnorm:
        return 1.0

    whole = SequenceMatcher(None, qnorm, nnorm).ratio()
    # Containment is a strong signal LIKE would already have caught — but only
    # at a WORD boundary. Mid-word containment made "Amit" score 0.90 against
    # "namitha" (it is a substring of it), which tied with Namita's 0.92 and
    # got the send refused as ambiguous. Shared rule, see whatsapp_contacts.
    from core.self_extend.whatsapp_contacts import contained_at_word_boundary
    if contained_at_word_boundary(qnorm, nnorm):
        whole = max(whole, 0.9)

    qtokens, ntokens = _name_tokens(query), _name_tokens(name)
    if not qtokens or not ntokens:
        return whole

    # Every query token gets credit for its best match among the real name's
    # tokens. Extra tokens in the QUERY are not penalised, so "burrito time"
    # still matches the group "Burgito".
    per_token = []
    for qt in qtokens:
        best = max(SequenceMatcher(None, qt, nt).ratio() for nt in ntokens)
        per_token.append(best)
    token_score = sum(per_token) / len(per_token)
    score = max(whole, 0.35 * whole + 0.65 * token_score)

    # Extra tokens in the CANDIDATE do get a small penalty, so asking for
    # "Bharat" ranks the contact "Bharat" above the group "Bharat Fans"
    # instead of tying with it and forcing a needless clarifying question.
    unmatched = max(0, len(ntokens) - len(qtokens))
    if unmatched:
        score *= max(0.75, 1.0 - 0.08 * unmatched)
    return score


def _candidate_pool(client: WhatsAppMCPClient, raw: str,
                    timeout: Optional[float]) -> list[dict]:
    """Contacts AND chats (groups included) that could be `raw`.

    Cheap targeted lookups first; a broad recent-chats sweep only if those come
    back empty, because a mangled name will not match any LIKE query.
    """
    pool: list[dict] = []
    seen: set[str] = set()

    def add(items, source):
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("jid") or item.get("phone_number") or "").strip()
            name = str(item.get("name") or "").strip()
            if not jid or jid in seen or not name:
                continue
            seen.add(jid)
            pool.append({"jid": jid, "name": name, "source": source,
                         "is_group": jid.endswith("@g.us")})

    def safe(tool, args):
        try:
            return client.call_tool(tool, args, timeout=timeout)
        except Exception as exc:
            print(f"[WhatsApp] {tool} failed during resolution: {exc}")
            return None

    # The ADDRESS BOOK first. messages.db stores direct chats as bare numbers
    # (measured: 292 DMs, 0 with a human name), so a person's name exists ONLY
    # in the bridge's whatsmeow_contacts store. Without this, "send a message
    # to Namita" can never resolve no matter how good the fuzzy matching is.
    try:
        from core.self_extend import whatsapp_contacts
        for item in whatsapp_contacts.resolve_name(raw, limit=8):
            # Keep config-sourced entries distinguishable: a manual override is
            # how the user disambiguates a name several contacts share, so it
            # has to beat the ambiguity check rather than add to it.
            add([{"jid": item["jid"], "name": item["name"]}],
                "config_contact" if item["source"] == "config" else "address_book")
    except Exception as exc:
        print(f"[WhatsApp] address book unavailable: {exc}")

    add(safe("search_contacts", {"query": raw}), "contact")
    add(safe("list_chats", {"query": raw, "limit": 20,
                            "include_last_message": False}), "chat")

    # Individual tokens next: "burrito time" won't LIKE-match "Burgito", but a
    # two-word name where only ONE word was misheard still can.
    if not pool:
        for token in _name_tokens(raw)[:3]:
            if len(token) < 3:
                continue
            add(safe("list_chats", {"query": token, "limit": 20,
                                    "include_last_message": False}), "chat")

    # Last resort: score against everything recently active. This is the branch
    # that actually rescues "burrito time" → "Burgito".
    if not pool:
        add(safe("list_chats", {"query": None, "limit": 80,
                                "include_last_message": False,
                                "sort_by": "last_active"}), "chat")
    return pool


def fuzzy_chat_search(query: str, limit: int = 10,
                      timeout: Optional[float] = None) -> list[dict]:
    """Chats matching a possibly-misheard name, best first.

    `list_chats` alone does a substring LIKE, so a searched-for "burrito"
    returns nothing for the group actually named "burgito time" and the agent
    burns a whole turn discovering that. This ranks the same candidate pool the
    send path uses, so the FIRST lookup succeeds.
    """
    client = get_whatsapp_mcp()
    pool = _candidate_pool(client, str(query or ""), timeout)
    scored = sorted(
        ((_name_score(query, item["name"]), item) for item in pool),
        key=lambda pair: pair[0], reverse=True)
    return [
        {"jid": item["jid"], "name": item["name"],
         "is_group": item["is_group"], "match_score": round(score, 2)}
        for score, item in scored[:limit] if score > 0.45
    ]


def _resolve_recipient(client: WhatsAppMCPClient, recipient: str,
                       timeout: Optional[float]) -> tuple[Optional[str], Optional[dict]]:
    """Turn a spoken name into a JID, or explain why it is ambiguous.

    Returns (jid, None) on success or (None, {"error", "matches"}) so the
    caller/agent can ask which one was meant rather than messaging the wrong
    person.
    """
    raw = str(recipient or "").strip()
    if not raw:
        return None, {"error": "Recipient is required."}
    compact = raw.replace("+", "").replace(" ", "").replace("-", "")
    if "@" in raw or compact.isdigit():
        return compact if compact.isdigit() else raw, None

    pool = _candidate_pool(client, raw, timeout)
    if not pool:
        return None, {"error": f"No WhatsApp contact or group matched '{raw}'.",
                      "matches": []}

    # Rank by similarity, then prefer a real CHAT over an address-book-only
    # entry at equal score: the address book holds ~2400 names, most of whom
    # Vaibhav has never messaged, and a conversation that actually exists is
    # the likelier target than a namesake in his contacts.
    _rank = {"chat": 2, "contact": 1, "config_contact": 0, "address_book": 0}
    scored = sorted(
        ((_name_score(raw, item["name"]), item) for item in pool),
        key=lambda pair: (pair[0], _rank.get(pair[1]["source"], 0)), reverse=True)

    # A manual `whatsapp.contacts` entry is the user stating who they mean, so
    # it settles the question outright — that is the whole point of adding one
    # for a name several contacts share (three people called Nikhil).
    configured = [item for score, item in scored
                  if item["source"] == "config_contact" and score >= _FUZZY_ACCEPT]
    if configured:
        print(f"[WhatsApp] Resolved {raw!r} → {configured[0]['name']!r} "
              f"(from whatsapp.contacts)")
        return configured[0]["jid"], None

    # An exact name match is never ambiguous, however many near-misses exist —
    # asking "did you mean Bharat or Bharat Fans?" when the user said exactly
    # "Bharat" is worse than useless.
    exact = [item for score, item in scored if score >= 1.0]
    if len(exact) == 1:
        return exact[0]["jid"], None

    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0

    if best_score >= _FUZZY_ACCEPT and (best_score - runner_up) >= _FUZZY_MARGIN:
        kind = "group" if best["is_group"] else "contact"
        if _normalize_name(raw) != _normalize_name(best["name"]):
            print(f"[WhatsApp] Resolved {raw!r} → {kind} {best['name']!r} "
                  f"(score {best_score:.2f})")
        return best["jid"], None

    preview = [
        {"name": item["name"], "jid": item["jid"],
         "is_group": item["is_group"], "score": round(score, 2)}
        for score, item in scored[:6] if score > 0.3
    ]
    if not preview:
        return None, {"error": f"No WhatsApp contact or group matched '{raw}'.",
                      "matches": []}
    return None, {
        "error": (f"'{raw}' is ambiguous — more than one chat is a close match. "
                  f"Ask which one was meant instead of guessing."),
        "matches": preview,
    }


def _resolve_contact_argument(client: WhatsAppMCPClient, value: str,
                              timeout: Optional[float],
                              prefer_phone: bool) -> tuple[Optional[str], Optional[dict]]:
    raw = str(value or "").strip()
    compact = raw.replace("+", "").replace(" ", "").replace("-", "")
    if "@" in raw or compact.isdigit():
        return compact if compact.isdigit() else raw, None
    resolved, error = _resolve_recipient(client, raw, timeout)
    if error or not resolved:
        return resolved, error
    if prefer_phone:
        return resolved.split("@", 1)[0], None
    return resolved, None


_NUMERIC_NAME_RE = re.compile(r"^\+?\d[\d\s\-]*$")


def _label_people(value: Any) -> Any:
    """Attach human names to the bare identifiers the MCP returns.

    Message rows carry ``sender`` as an opaque ``@lid`` number, and a direct
    chat's ``name`` is just that number again — so an un-enriched chat summary
    reads as a list of phone numbers and the model has no idea who said what.
    The address book turns those into "Nikhil" (verified 8/8 on live senders).

    Enriches in place-ish (returns new structures), never removes the original
    identifier, and never raises: a missing address book just means the raw
    numbers survive, exactly as before.
    """
    try:
        from core.self_extend import whatsapp_contacts
    except Exception:
        return value

    def walk(node):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node

        out = {key: walk(item) for key, item in node.items()}

        sender = out.get("sender")
        if isinstance(sender, str) and sender:
            if out.get("is_from_me"):
                out["sender_name"] = "Vaibhav"
            else:
                found = whatsapp_contacts.display_name(sender)
                if found:
                    out["sender_name"] = found

        # A direct chat whose name is just its own number: show who it is.
        # Message rows carry this as `chat_name`, chat rows as `name`. Missing
        # `chat_name` was not cosmetic: the agent read Namita's chat, saw every
        # row labelled "93638927347889", assumed it had the wrong chat, and
        # burned an entire extra turn re-fetching the identical messages.
        jid = out.get("jid") or out.get("chat_jid")
        for key in ("name", "chat_name"):
            chat_name = out.get(key)
            if (isinstance(chat_name, str)
                    and _NUMERIC_NAME_RE.match(chat_name.strip())
                    and isinstance(jid, str) and not jid.endswith("@g.us")):
                found = whatsapp_contacts.display_name(jid) or \
                    whatsapp_contacts.display_name(chat_name)
                if found:
                    out[key] = found
                    out.setdefault("number", chat_name)
        return out

    try:
        return walk(value)
    except Exception as exc:
        print(f"[WhatsApp] name labelling skipped: {exc}")
        return value


def call_whatsapp_tool_data(name: str, arguments: Optional[dict] = None,
                            timeout: Optional[float] = None,
                            resolve_recipient: bool = False,
                            resolve_contact: bool = False) -> Any:
    client = get_whatsapp_mcp()
    args = dict(arguments or {})
    try:
        if resolve_recipient and name in {
            "send_message", "send_file", "send_audio_message",
        }:
            resolved, error = _resolve_recipient(
                client, str(args.get("recipient", "")), timeout)
            if error:
                return error
            args["recipient"] = resolved
        if resolve_contact:
            key_and_mode = {
                "list_messages": ("sender_phone_number", True),
                "get_direct_chat_by_contact": ("sender_phone_number", True),
                "get_contact_chats": ("jid", True),
                "get_last_interaction": ("jid", False),
            }.get(name)
            if key_and_mode and args.get(key_and_mode[0]):
                key, prefer_phone = key_and_mode
                resolved, error = _resolve_contact_argument(
                    client, str(args[key]), timeout, prefer_phone)
                if error:
                    return error
                args[key] = resolved
        return _label_people(
            _truncate_values(client.call_tool(name, args, timeout=timeout)))
    except Exception as exc:
        return {"error": str(exc), "tool": name}


def call_whatsapp_tool_json(name: str, arguments: Optional[dict] = None,
                            timeout: Optional[float] = None,
                            resolve_recipient: bool = False,
                            resolve_contact: bool = False) -> str:
    data = call_whatsapp_tool_data(
        name, arguments, timeout=timeout,
        resolve_recipient=resolve_recipient,
        resolve_contact=resolve_contact)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
