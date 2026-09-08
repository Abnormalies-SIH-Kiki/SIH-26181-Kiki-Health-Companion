"""
Kiki's fast research + action agent (the `complex_query` tool).

WHY THIS EXISTS
---------------
The speaking model can emit exactly ONE tool call per turn, so a request like
"check the recent messages at burgito and create a reminder if there's an event
tomorrow" is structurally impossible on the speaking path — it needs
list_chats → list_messages → schedule_worker → report, with the output of each
step deciding the next.

This module is the same pattern that makes `look_at_scene` feel seamless
(§5.2a): a specialised cloud handler behind a normal tool call, returning into
the existing tool-follow-up machinery. main.py does not need to know it ran.

WHAT IT IS NOT
--------------
* It never touches the local llama.cpp box. The single speaking slot stays
  owned by the foreground voice path (§4).
* It is not the slow reasoning router (`generate_llm_resp`) used by Unified
  Idle Mind — that one optimises for quality over seconds. This optimises for
  finishing a spoken request inside ~10s.

BUDGET
------
main.py bounds a tool call at `llm.tool_calling.exec_timeout_s` (15s by
default, overridable per tool). The agent therefore enforces its OWN wall-clock
deadline slightly under that override and returns partial findings instead of
being cut off mid-action — a half-reported success ("I sent it") when the send
never happened is the worst possible outcome.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from typing import Optional

from tools_and_config.config_loader import get_full_config


# --- Tool policy -----------------------------------------------------------
# The agent gets far more than the speaking model, but NOT everything. Physical
# motion, music and self-extension are excluded: they are either unsafe to fire
# from a background loop or have their own dedicated voice paths.
AGENT_TOOLS = (
    # WhatsApp (the 12 that were removed from the speaking catalog)
    "search_contacts", "list_messages", "list_chats", "get_chat",
    "get_direct_chat_by_contact", "get_contact_chats", "get_last_interaction",
    "get_message_context", "send_message", "send_file", "send_audio_message",
    "download_media",
    # WhatsApp media understanding + voice capture
    "read_whatsapp_image", "record_voice_note",
    # Live data
    "search_web", "get_current_time",
    "read_gmail", "read_gmail_message", "read_gmail_thread",
    "search_notion", "read_notion",
    # Memory + scheduling
    "recall_memory", "update_knowledge",
    "set_timer", "schedule_worker", "cancel_worker", "list_workers",
    # Escape hatch for Gmail/Notion WRITES and any other connected MCP
    "self_extend_tool_call",
    "execute_python_code",
)

# Never runnable from this agent, even if a future config adds them.
BLOCKED_TOOLS = {
    "move", "dance", "follow_me", "track_person", "remember_me",
    "play_music", "look_at_scene", "execute_shell_command",
    "switch_mode", "switch_voice", "adjust_volume", "set_followups",
    "self_extend_run_task", "self_extend_create_mcp_server",
    "self_extend_mcp_add", "self_extend_mcp_remove",
    "complex_query",   # no recursion
}

_DEFAULTS = {
    "enabled": True,
    "max_turns": 6,
    # A complex_query is routed here BECAUSE it needs real actions. A model
    # that "completes" without touching a single tool has invented its answer —
    # observed live: gpt-oss on Groq reported a detailed WhatsApp conversation
    # about taco night that did not exist. run_agent_loop rejects the
    # completion and forces a real call instead.
    "min_tool_calls": 1,
    "max_tool_calls": 10,
    "max_calls_per_turn": 4,
    "max_prompt_chars": 14000,
    "max_tool_result_chars": 1500,
    "deadline_seconds": 22.0,
    "summary_max_chars": 2000,
    # How much of Kiki's world the agent is told about. See _background().
    "persona_chars": 1000,
    "history_chars": 28000,
    "history_record_chars": 1200,
    "artifact_limit": 12,
}


def _cfg() -> dict:
    cfg = dict(_DEFAULTS)
    user = get_full_config().get("action_agent", {}) or {}
    for key, value in user.items():
        if key in cfg:
            cfg[key] = value
    return cfg


def _catalog() -> str:
    """Compact one-line-per-tool catalog (name + signature + short purpose).

    Deliberately compact rather than full JSON schemas: the whole prompt is
    resent on every agent turn, and on the Groq fallback path it competes with
    an 8000-token-per-minute budget.
    """
    from tools_and_config.tools import TOOLS

    lines = []
    for tool in TOOLS:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if name not in AGENT_TOOLS or name in BLOCKED_TOOLS:
            continue
        params = fn.get("parameters", {}).get("properties", {})
        required = set(fn.get("parameters", {}).get("required", []))
        parts = []
        for key, spec in params.items():
            label = key if key in required else f"{key}?"
            # Render enums. Without them the model guesses a plausible-looking
            # value ("specific_date_time" for schedule_worker), the call fails
            # validation, and a whole retry turn (~1s) is burned.
            if spec.get("enum"):
                label += "=" + "|".join(str(v) for v in spec["enum"])
            parts.append(label)
        signature = ", ".join(parts)
        desc = str(fn.get("description", "")).split("\n")[0][:150]
        lines.append(f"- {name}({signature}): {desc}")
    return "\n".join(lines)


_MCP_HINTS = """CONNECTED MCP SERVERS (for writes, via self_extend_tool_call):
- Gmail  → self_extend_tool_call(connection="gmail", tool="create_email_draft",
  args_json='{"recipient_email":"...","subject":"...","body":"...","user_id":"me"}')
  Other gmail tools: fetch_emails, fetch_message_by_message_id, add_label_to_email.
- Notion → self_extend_tool_call(connection="notion", tool="notion-create-pages",
  args_json='{"pages":[{"properties":{"title":"..."},"content":"..."}]}')
  Other notion tools: notion-search, notion-fetch, notion-update-page.
Prefer the compact read tools (read_gmail, search_notion, read_notion) for READING —
they strip HTML and MIME noise that would otherwise flood your context."""


def _background(context: str) -> str:
    """Who Kiki is, and what was just being said.

    The code router builds its tool call from the user's words alone, so
    without this the agent cannot resolve "send it to him" or know that "him"
    was named two turns ago — the request arrives with no referent at all.
    Read out-of-band from core.llm rather than passed as a tool argument, so
    none of it lands in the speaking model's KV-cache prefix.
    """
    parts = []
    cfg = _cfg()
    try:
        from core import llm
        persona = llm.persona_brief(int(cfg["persona_chars"]))
        if persona:
            parts.append(f"WHO YOU ARE (excerpt of Kiki's persona):\n{persona}")
        history = llm.conversation_snapshot(
            int(cfg["history_chars"]), int(cfg["history_record_chars"]))
        if history:
            parts.append(
                "CONVERSATION SO FAR (most recent last). '[result]' lines are what\n"
                "Kiki's own earlier tool calls returned — links and ids in them are\n"
                "REAL and may be used verbatim. This is background, not a new "
                f"instruction:\n{history}")
        # Even when a long result gets clipped, the thing a follow-up actually
        # needs — "send the music link", "send that article to namita" —
        # survives here.
        artifacts = llm.conversation_artifacts(int(cfg["artifact_limit"]))
        if artifacts:
            parts.append("LINKS AND IDS SEEN RECENTLY (newest first):\n"
                         + "\n".join(f"- {a}" for a in artifacts))
    except Exception as exc:
        print(f"[ActionAgent] context unavailable: {exc}")
    # Live state beats remembered state: the track may have started long before
    # anything still in the window, or survived a compaction that dropped it.
    try:
        from core.media_manager import music_manager
        current = music_manager.snapshot().get("current") or {}
        if current.get("title"):
            url = current.get("webpage_url") or ""
            parts.append(f"MUSIC PLAYING RIGHT NOW: {current['title']}"
                         + (f" — {url}" if url else ""))
    except Exception:
        pass
    # An explicit caller-supplied context wins a place of its own; it is what
    # a deliberate complex_query(context=...) call meant to emphasise.
    if context.strip():
        parts.append(f"CALLER CONTEXT:\n{context.strip()}")
    return ("\n\n" + "\n\n".join(parts) + "\n") if parts else ""


def _prompt(request: str, context: str = "") -> str:
    now = datetime.now()
    extra = _background(context)
    return f"""You are Kiki's fast action agent. Vaibhav asked for something that takes
several steps. Carry it out end to end, then report what ACTUALLY happened.

REQUEST: {request}

TIME: {now.strftime('%A %Y-%m-%d %H:%M')} (local)
{extra}
HOW TO WORK:
- The request was spoken mid-conversation, so it may lean on what came before:
  "send it to him", "the one from yesterday", "that link". Resolve those from
  CONVERSATION SO FAR before acting. If the referent genuinely is not there,
  say so rather than picking someone at random.
- Be decisive and fast. Batch calls into one tool_calls array ONLY when they are
  independent. If one call needs a value another call returns (a jid, an id, a
  file path), make them in SEPARATE turns. Never pass a made-up placeholder.
- To SEND, pass the name straight to send_message / send_file /
  send_audio_message — they resolve "namitha" to the right person themselves,
  against Vaibhav's real address book. Do NOT spend a turn on search_contacts
  first; that is pure delay. If the name is genuinely ambiguous they come back
  with a list of candidates — then, and only then, stop and ask which one.
- To READ a chat you do need its jid: get it from list_chats, which already
  corrects misheard names, and use the exact jid it returns. Never invent one.
- Names arrive through speech recognition and are often wrong. "burrito time"
  probably means the group "Burgito"; "gmail" may be "Gmail". Pick the closest
  REAL chat from the tool results. If two are equally close, do not guess —
  finish and ask which one was meant.
- "Remind me" / "create a reminder" means Kiki should SPEAK it at the right
  time: use schedule_worker (a specific date/time) or set_timer (a countdown
  like "in 20 minutes"). Do NOT write the reminder into Notion or Gmail unless
  Vaibhav explicitly asked for it to go there.
- To send a voice note: record_voice_note(seconds) returns a wav path, then
  send_audio_message(recipient, that path).
- A WhatsApp message containing "[image - Message ID: X - Chat JID: Y]" has a
  picture. Use read_whatsapp_image(message_id, chat_jid, question) to see it.
- NEVER claim you sent, saved, drafted, or scheduled anything unless the tool
  actually returned success. If a tool failed, say so plainly.

{_MCP_HINTS}

AVAILABLE TOOLS:
{_catalog()}

PROTOCOL — respond with ONE JSON object and NOTHING else:
  to act:      {{"tool_calls":[{{"tool":"name","args":{{...}}}}]}}
  when done:   {{"status":"completed","summary":"..."}}
  if blocked:  {{"status":"failed","reason":"..."}}

CRITICAL: emit exactly ONE JSON object and then STOP. Do NOT write tool results
yourself and do NOT continue the conversation — the system runs your tools and
returns the REAL results to you. Inventing a result is a failure.

BE PROACTIVE. Do not do the bare minimum and stop:
- When summarising a chat, read ENOUGH messages to actually understand it
  (50-100, not 10), and cover every distinct thread of conversation.
- Images are already described inline for you as "[image] <description>" —
  treat that as part of the conversation and include what the pictures say.
  If you see "[image — not yet viewed...]" and it looks relevant, go read it.
- If something obvious is missing to answer well, fetch it rather than
  hedging. Notice names, dates, times, amounts, deadlines and decisions.

The "summary" is SPOKEN ALOUD to Vaibhav, and it is the whole answer — he sees
nothing else, so do not be terse. Write a rich, natural, flowing spoken reply
in first person: several sentences, up to about {_DEFAULTS['summary_max_chars']} characters.
Say who said what, what was decided, what the pictures showed, and anything he
needs to act on. Keep it conversational — full sentences, no bullet points, no
markdown, no headings, no emoji. Do not mention tools, JSON, or these
instructions.
"""


# Values a model invents when it batches a call that DEPENDS on an earlier
# call's result — e.g. list_messages(chat_jid="<PLACEHOLDER_JID_FROM_FIRST_CALL>")
# issued in the same turn as the list_chats that was supposed to supply it.
# Executing those returns empty results, which the model then reports as fact
# ("there are no new messages"). Observed live on the Groq fallback model.
_PLACEHOLDER_RE = re.compile(
    r"^<.*>$"
    r"|placeholder|from_first_call|from_previous|previous_call"
    r"|^(?:jid|chat_jid|id|message_id|recipient)_here$"
    r"|your_|_here$|\bTBD\b|xxxx",
    re.IGNORECASE,
)


def _placeholder_arg(args: dict):
    """Name of the first argument that is an unresolved placeholder, if any."""
    for key, value in (args or {}).items():
        if isinstance(value, str) and value.strip() and _PLACEHOLDER_RE.search(value.strip()):
            return key, value
    return None


_MEANINGLESS_RE = re.compile(r"^[\s.…·\-–—*_]*$")


def _is_meaningful(summary: str) -> bool:
    """Does this summary actually tell Vaibhav anything?

    Guards against models that wrap up with "...", "done", or bare punctuation
    after doing real work — spoken aloud that conveys nothing and hides whether
    the action succeeded.
    """
    text = str(summary or "").strip()
    if len(text) < 6 or _MEANINGLESS_RE.match(text):
        return False
    # Two real words is the bar: "Sent it." is a fine spoken reply, while
    # "...", "ok" and "done" are not answers to anything.
    return len(re.findall(r"[A-Za-zऀ-ॿ]{2,}", text)) >= 2


def _tool_executor(name: str, args: dict) -> str:
    """Run one tool under the agent's allowlist."""
    from tools_and_config.tools import execute_tool

    lname = str(name or "").strip()
    if lname in BLOCKED_TOOLS or lname not in AGENT_TOOLS:
        return (f"BLOCKED: '{lname}' is not available to the action agent. "
                f"Use one of the listed tools.")

    found = _placeholder_arg(args)
    if found is not None:
        key, value = found
        print(f"[ActionAgent] ⛔ Refused {lname}: placeholder {key}={value!r}")
        # Refusing is what stops a garbage-in/empty-out call from becoming a
        # confident false statement in the spoken summary.
        return (f"NOT EXECUTED: '{key}' was {value!r}, which is a placeholder, not a "
                f"real value. You batched a call that depends on an earlier call's "
                f"result. Call the tools ONE AT A TIME: wait for the real value, "
                f"then call {lname} with it.")

    return execute_tool(lname, args)


def _progress_reporter():
    """LCD/OLED narration of what the agent is doing right now.

    `run_agent_loop` calls this with ("thinking"|"tool"|"done", detail) and
    swallows any exception, so a missing panel can never affect a run.
    """
    from core.lcd_display import lcd_manager

    pretty = {
        "search_contacts": "Finding contact", "list_chats": "Finding chat",
        "list_messages": "Reading msgs", "get_chat": "Reading chat",
        "get_message_context": "Reading msgs", "get_last_interaction": "Reading msgs",
        "get_contact_chats": "Reading chats", "get_direct_chat_by_contact": "Finding chat",
        "send_message": "Sending msg", "send_file": "Sending file",
        "send_audio_message": "Sending audio", "download_media": "Downloading",
        "read_whatsapp_image": "Reading image", "record_voice_note": "Recording",
        "search_web": "Searching web", "read_gmail": "Reading Gmail",
        "read_gmail_message": "Reading Gmail", "read_gmail_thread": "Reading Gmail",
        "search_notion": "Notion search", "read_notion": "Reading Notion",
        "recall_memory": "Recalling", "update_knowledge": "Saving memory",
        "set_timer": "Setting timer", "schedule_worker": "Scheduling",
        "self_extend_tool_call": "Calling MCP",
        "execute_python_code": "Running code",
    }

    def report(phase, detail=""):
        if phase == "tool":
            lcd_manager.update_status("Working", pretty.get(detail, detail)[:16])
        elif phase == "thinking":
            lcd_manager.update_status("Working", "thinking...")
        elif phase == "done":
            lcd_manager.update_status("Working", "wrapping up")

    return report


async def run_complex_query(request: str, context: str = "") -> str:
    """Execute a multi-step request and return one spoken-ready summary.

    Called from the `complex_query` tool. The returned string is injected into
    the speaking model's follow-up prompt, so it must be short and speakable.
    """
    from core.agent_loop import run_agent_loop
    from core.brain import fast_cloud
    from core.observability import get_recorder

    cfg = _cfg()
    if not cfg.get("enabled", True):
        return "The action agent is disabled in config."

    request = str(request or "").strip()
    if not request:
        return "No request was provided."

    deadline = float(cfg["deadline_seconds"])
    stop_event = threading.Event()
    timer = threading.Timer(deadline, stop_event.set)
    timer.daemon = True
    timer.start()

    started = time.time()
    recorder = get_recorder()
    sid = recorder.start_session(
        "action_agent", name=request[:120],
        model=fast_cloud.active_model(), deadline=deadline)

    print(f"[ActionAgent] ▶ {request!r} via {fast_cloud.active_model()} "
          f"(deadline {deadline:.0f}s)")

    def llm_fn(prompt_text: str) -> str:
        # A tripped deadline must not start another request.
        if stop_event.is_set():
            return ""
        try:
            return fast_cloud.complete(prompt_text, stop_event=stop_event)
        except fast_cloud.FastCloudUnavailable as exc:
            print(f"[ActionAgent] all providers failed: {exc}")
            return ""

    try:
        ok, result, _speak, _final, tools_used = await run_agent_loop(
            _prompt(request, context),
            llm_fn=llm_fn,
            max_turns=int(cfg["max_turns"]),
            label="ActionAgent",
            stop_event=stop_event,
            min_tool_calls=int(cfg["min_tool_calls"]),
            max_tool_calls=int(cfg["max_tool_calls"]),
            max_calls_per_turn=int(cfg["max_calls_per_turn"]),
            max_prompt_chars=int(cfg["max_prompt_chars"]),
            max_tool_result_chars=int(cfg["max_tool_result_chars"]),
            session_id=sid,
            tool_executor=_tool_executor,
            progress_fn=_progress_reporter(),
        )
    except Exception as exc:
        print(f"[ActionAgent] crashed: {exc}")
        ok, result, tools_used = False, f"internal error: {exc}", []
    finally:
        timer.cancel()

    elapsed = time.time() - started
    timed_out = stop_event.is_set()
    print(f"[ActionAgent] ◀ {'ok' if ok else 'incomplete'} in {elapsed:.1f}s "
          f"({len(tools_used)} tool(s): {', '.join(tools_used) or 'none'})"
          + (" [DEADLINE]" if timed_out else ""))

    recorder.end_session(
        sid, status="done" if ok else "failed", result=str(result)[:800],
        tools_used=tools_used, seconds=round(elapsed, 2), timed_out=timed_out)

    summary = str(result or "").strip()
    limit = int(cfg["summary_max_chars"])
    if len(summary) > limit:
        summary = summary[:limit].rstrip() + "…"

    # A summary that says nothing is not a success. Some models wrap up with
    # "...", "done" or bare punctuation after doing real work; speaking that
    # aloud tells Vaibhav nothing and hides whether the action happened.
    if ok and not _is_meaningful(summary):
        print(f"[ActionAgent] ⚠ Degenerate summary {summary!r} — reporting as incomplete.")
        ok = False
        summary = (f"finished the steps but did not report what happened "
                   f"(tools used: {', '.join(tools_used) or 'none'})")

    if ok and summary:
        return summary
    # Be explicit about failure. A vague result here would let the speaking
    # model invent a confident success ("done, sent it!") for an action that
    # never happened.
    if timed_out:
        return (f"ACTION INCOMPLETE (took too long). Progress so far: "
                f"{summary or 'nothing completed'}. Tell Vaibhav it did not "
                f"finish and do not claim it succeeded.")
    return (f"ACTION FAILED: {summary or 'unknown error'}. Tell Vaibhav plainly "
            f"that it did not work and do not claim it succeeded.")
