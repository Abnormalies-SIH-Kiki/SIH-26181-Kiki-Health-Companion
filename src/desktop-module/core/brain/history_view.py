"""One readable rendering of Kiki's conversation history.

`message_history` is shaped for the local box, not for reading. A single tool
turn writes three entries:

    user      "play some music"
    assistant '<tool_call>{"name":"play_music","arguments":{...}}</tool_call>'
    system    'Here is the result of a quick lookup ...:\\nNow playing X - https://...'
    assistant "Playing X for you."

Two consumers need that as prose rather than protocol — the action agent
(which must resolve "send the music link") and the background summariser
(which writes Kiki's long-term memory). Both used to drop the `system` row, so
**the URL existed only in the one line neither of them read**, and it vanished
for good at the next compaction. That is the whole reason this module exists:
render once, correctly, and let both callers share it.

Nothing here touches the messages the box sees, so the KV-cache contract (§4)
is unaffected.
"""

from __future__ import annotations

import json
import re

# Both branches of main.tool_result_note end with this sentence, and the raw
# tool output follows it. Split there to recover the payload without the
# instruction wrapper. `tool_note_marker_is_current` in the tests fails loudly
# if that wording is ever edited, rather than silently degrading to noise.
TOOL_NOTE_MARKER = "do not think out loud:\n"

# The compacted memory the summariser writes back (prompts.previous_summary_context).
MEMORY_PREFIX = "Your memories"

_SPACE_RE = re.compile(r"\s+")
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
# Silent inline tags the model emits: <neck:left>, <neck:left:30>, <oled:shy>.
# (This used to match a paired <neck>…</neck> form that is never actually
# emitted, so neck tags were leaking into the agent-facing history view.)
_NECK_RE = re.compile(r"<(?:neck|oled):[a-z_]+(?::\d+)?>", re.IGNORECASE)

_URL_RE = re.compile(r"https?://[^\s<>\"'\])]+")
_PATH_RE = re.compile(r"/(?:home|tmp|var|media|mnt)/[^\s<>\"',;]+\.[A-Za-z0-9]{2,5}")
_JID_RE = re.compile(r"\b\d{5,}@(?:s\.whatsapp\.net|lid|g\.us)\b")


def _clean(text: str) -> str:
    return _SPACE_RE.sub(" ", str(text or "")).strip()


def describe_tool_calls(text: str) -> tuple[str, list[str]]:
    """Split assistant text into what was SAID and what was CALLED.

    The raw `<tool_call>{"name":...}</tool_call>` tag is grammar the box was
    trained on; to any other reader it is noise that also masquerades as
    speech. Before this, a snapshot line read
    `Kiki: <tool_call>{"name":"play_music",...}</tool_call>` — which the agent
    had every reason to treat as something Kiki said out loud.
    """
    calls = []
    for blob in _TOOL_CALL_RE.findall(text or ""):
        try:
            parsed = json.loads(blob)
            name = str(parsed.get("name") or "?")
            args = parsed.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args = {"": args}
            shown = ", ".join(
                f'{k}="{_clean(str(v))[:60]}"' for k, v in list(args.items())[:3]
                if str(v).strip())
            calls.append(f"{name}({shown})")
        except (json.JSONDecodeError, TypeError, AttributeError):
            calls.append("a tool")
    spoken = _NECK_RE.sub(" ", _TOOL_CALL_RE.sub(" ", text or ""))
    return _clean(spoken), calls


def tool_result_payload(content: str) -> str:
    """The raw tool output inside a tool-result system note."""
    text = str(content or "")
    idx = text.find(TOOL_NOTE_MARKER)
    return text[idx + len(TOOL_NOTE_MARKER):] if idx != -1 else text


def is_tool_result(content: str) -> bool:
    return TOOL_NOTE_MARKER in str(content or "")


def render(messages, skip_system_prompt: bool = True) -> list[dict]:
    """History as ``{"kind", "text"}`` records, oldest first.

    kinds: ``user``, ``kiki``, ``tool_call``, ``tool_result``, ``memory``,
    ``time``, ``context``.
    """
    out: list[dict] = []
    for index, msg in enumerate(list(messages or [])):
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, list):   # the system prompt's content-parts shape
            content = " ".join(p.get("text", "") for p in content
                               if isinstance(p, dict) and p.get("type") == "text")
        content = str(content or "")
        if not content.strip():
            continue

        if role == "user":
            out.append({"kind": "user", "text": _clean(content)})
        elif role == "assistant":
            spoken, calls = describe_tool_calls(content)
            for call in calls:
                out.append({"kind": "tool_call", "text": call})
            if spoken:
                out.append({"kind": "kiki", "text": spoken})
        elif role == "system":
            if index == 0 and skip_system_prompt:
                continue        # the persona; callers supply it separately
            if is_tool_result(content):
                out.append({"kind": "tool_result",
                            "text": _clean(tool_result_payload(content))})
            elif content.startswith(MEMORY_PREFIX):
                out.append({"kind": "memory", "text": _clean(content)})
            elif "right now it's" in content.lower():
                out.append({"kind": "time", "text": _clean(content)})
            else:
                out.append({"kind": "context", "text": _clean(content)})
    return out


_LABELS = {
    "user": "Vaibhav: ",
    "kiki": "Kiki: ",
    "tool_call": "[Kiki used ",
    "tool_result": "[result] ",
    "memory": "[earlier, from memory] ",
    "time": "[time] ",
    "context": "[context] ",
}


def as_lines(records, per_record_chars: int = 1200) -> list[str]:
    lines = []
    for rec in records:
        text = rec["text"][:per_record_chars]
        if rec["kind"] == "tool_call":
            lines.append(f"[Kiki used {text}]")
        else:
            lines.append(_LABELS.get(rec["kind"], "") + text)
    return lines


def as_text(messages, max_chars: int, per_record_chars: int = 1200,
            skip_system_prompt: bool = True) -> str:
    """Rendered history, newest-last, trimmed from the FRONT to fit.

    Front-trimming is deliberate: the newest turns are what "it", "him" and
    "that link" point at, so they must be the last thing dropped.
    """
    lines = as_lines(render(messages, skip_system_prompt), per_record_chars)
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        if total + len(line) + 1 > max_chars and kept:
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(reversed(kept))


def harvest_artifacts(messages, limit: int = 12) -> list[str]:
    """Links, file paths and chat ids seen recently, newest first.

    The durable half of the fix. Even when a long tool result gets clipped, the
    thing a follow-up request actually needs — "send the music link", "send
    that article to namita" — survives here as a short labelled line.
    """
    found: list[str] = []
    seen: set[str] = set()
    for rec in reversed(render(messages)):
        if rec["kind"] not in ("tool_result", "kiki", "context"):
            continue
        text = rec["text"]
        for pattern in (_URL_RE, _PATH_RE, _JID_RE):
            for hit in pattern.findall(text):
                hit = hit.rstrip(".,;:)")
                if hit in seen:
                    continue
                seen.add(hit)
                # A little surrounding text is what makes the link identifiable
                # as "the music one" rather than just a URL.
                where = text[:max(0, text.find(hit))][-70:].strip()
                found.append(f"{hit}   ({where})" if where else hit)
                if len(found) >= limit:
                    return found
    return found
