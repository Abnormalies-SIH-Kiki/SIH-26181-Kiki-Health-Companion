"""
LLM response generation module for KikiFast voice assistant.
Streams responses via LiteLLM and yields complete sentences as they form.

Everything that can be pre-initialized is done at import time for minimum latency.
"""

import os
import sys
import json
import re
import time
import threading
import queue

import requests  # lean SSE streaming straight to the local llama-server

# --- Pre-initialize at import time ---
# 1. Load API keys from .env BEFORE importing litellm so they're ready immediately
from dotenv import load_dotenv
load_dotenv("/home/vaibhav/KikiESP32/gateway/legacy_kiki/.env")

# --- Vertex AI auth (matches apiusage.py) ---
# Use the service-account key file (NOT gcloud ADC). litellm loads this JSON and
# builds scoped service_account credentials from it
# (scopes=["https://www.googleapis.com/auth/cloud-platform"]) — the exact same
# auth apiusage.py performs with service_account.Credentials.from_service_account_file.
_VERTEX_SA_FILE = os.getenv("VERTEX_SERVICE_ACCOUNT_FILE", "")
_VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "")
_VERTEX_LOCATION = "global"  # gemini-3.x preview models are served global-only

# Load the service-account JSON key once at import, if there is one. The key file
# is never committed, so on a fresh checkout these stay None and the Vertex
# fallback is simply unavailable.
vertex_credentials = None
vertex_credentials_json = None
if _VERTEX_SA_FILE and os.path.exists(_VERTEX_SA_FILE):
    with open(_VERTEX_SA_FILE, 'r') as file:
        vertex_credentials = json.load(file)
    vertex_credentials_json = json.dumps(vertex_credentials)

# 2. litellm is imported LAZILY (only used as a CLOUD FALLBACK; importing it
# eagerly costs multiple seconds of startup on the Pi).
_completion = None


def _get_completion():
    global _completion
    if _completion is None:
        from litellm import completion as _c
        _completion = _c
    return _completion

# 3. Cache config at module level (read once)
from tools_and_config.config_loader import get_llm_config
_LLM_CFG = get_llm_config()
_MODEL = _LLM_CFG["model"]
_FALLBACK_MODEL = _LLM_CFG.get("fallback_model")
_FALLBACK_TIMEOUT = _LLM_CFG.get("fallback_timeout", 5)
_TEMPERATURE = _LLM_CFG.get("temperature", 1.0)
_REASONING_EFFORT = _LLM_CFG.get("reasoning_effort", "low")

# --- Local speaking-model (llama.cpp / llama-server) config ---
# This is the PRIMARY path: the user's heavily-optimized llama.cpp box reached over
# Tailscale. It speaks an OpenAI-compatible /v1/chat/completions endpoint and was
# launched with --cache-prompt, so it transparently reuses the KV cache for any
# shared prompt prefix (the big static system prompt) → near-instant prefill on
# repeat turns. We stream raw SSE with `requests` to shave every bit of overhead.
_USE_LOCAL = _LLM_CFG.get("use_local_speaking", False)
# --- Speaking provider selection ---
# "local" (default) = the llama.cpp box below (KV-cache warmed, speculative turns).
# "groq"            = stream every reply from Groq's Qwen instead (no box, no KV
#                     cache/warmup — pure network+generation latency). One config
#                     value flips the whole speaking path; everything else (tools,
#                     speculative turns, instant-vision) keeps working. See
#                     _stream_groq_speaking + llm.groq_speaking config.
_SPEAKING_PROVIDER = str(_LLM_CFG.get("speaking_provider", "local")).lower()
_GROQ_SPEAK_CFG = _LLM_CFG.get("groq_speaking", {}) or {}
_OPENROUTER_CFG = _LLM_CFG.get("openrouter_speaking", {}) or {}
_OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
_CEREBRAS_CFG = _LLM_CFG.get("cerebras_speaking", {}) or {}
_CEREBRAS_KEY = os.getenv("CEREBRAS_API_KEY")
_LOCAL_API_BASE = _LLM_CFG.get("local_api_base", "http://127.0.0.1:8080/v1").rstrip("/")
_LOCAL_URL = f"{_LOCAL_API_BASE}/chat/completions"
_LOCAL_MODEL = _LLM_CFG.get("local_model", "kiki-local")
_LOCAL_MAX_TOKENS = _LLM_CFG.get("local_max_tokens", 1200)
_TOOL_CFG = _LLM_CFG.get("tool_calling", {})
_SEND_TOOLS = _LLM_CFG.get("send_tools", False) and _TOOL_CFG.get("enabled", True)
_FIRST_SENTENCE_EAGER = _LLM_CFG.get("first_sentence_eager", True)
# Only this curated subset is exposed to the FAST speaking model — fewer schemas
# = smaller prefill + more reliable tool-calling on gemma.
_MAIN_TOOL_NAMES = _LLM_CFG.get("main_tools", ["search_web", "get_current_time", "play_music"])

# --- Prompt-reprocess diagnostic ---
# The speaking turn should be a near-pure KV-cache hit: only the new user
# question gets prefilled, everything else (system prompt, memory, brain
# findings) is already warm. We verify this against the SERVER's own timings:
#   timings.prompt_n = prompt tokens FRESHLY prefilled this request (cache MISS)
#   timings.cache_n  = prompt tokens reused from the KV cache (cache HIT)
# If prompt_n is large, the warm prefix was NOT actually warm (a background
# task evicted it, the rewarm hadn't finished, or the prefix diverged) and the
# user paid a full re-prefill. When exit_on_prompt_reprocess is on we print the
# diagnostic and hard-exit so the cache miss can't hide.
_EXIT_ON_REPROCESS = _LLM_CFG.get("exit_on_prompt_reprocess", False)
_REPROCESS_ALERT_TOKENS = _LLM_CFG.get("prompt_reprocess_alert_tokens", 250)
_timings_warned = {"no_timings": False}

# Central single-slot coordinator for the local box (lets us preempt background
# work — summary/vision — the instant the user needs the model to speak).
from core import local_llm

# Reuse the coordinator's keep-alive session for the speaking path too.
_SESSION = local_llm.SESSION

# 4. Import tools once. Only the curated subset is sent to the speaking model.
from tools_and_config.tools import TOOLS, execute_tool
from core.runtime_controls import get_active_mode, parse_spoken_control

# Instant-vision routing: live-image questions ("does my shirt look good?",
# "look at this phone, should I buy it?") are answered by Groq's multimodal Qwen
# instead of the blind local box (see core/vision/instant_vision.py). Imported
# lazily-safe (module has no heavy deps at import; the camera/cv2 import happens
# only when a query actually routes there).
from core.vision import instant_vision

_MAIN_TOOLS = [t for t in TOOLS if t.get("function", {}).get("name") in _MAIN_TOOL_NAMES] if TOOLS else []

# Latin boundaries retain their whitespace guard so a chunk ending in ``3.``
# is not split before a following ``14`` arrives. Devanagari danda is an
# unambiguous terminator, so it can flush at the current stream end without
# waiting for another model token.
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+|(?<=[।॥])(?:\s+|$)')

# Defensive scrub for any reasoning/thinking markers the model might leak as
# spoken content (channel markers, <think>/<thought> tags, [thought] blocks).
# The real fix is the prompt (don't ask it to plan), but this guarantees these
# tokens are never vocalized.
_THINK_RE = re.compile(
    r'<\|?channel\|?>?\s*\w*\s*<\|?(?:message|channel)\|?>?'  # <|channel>thought<channel|> style
    r'|</?think(?:ing)?>|</?thought>|\[/?thought\]|\[/?think\]',
    re.IGNORECASE | re.DOTALL,
)

def _scrub_reasoning(text):
    """Remove reasoning/thinking marker tokens from a sentence before TTS."""
    return _THINK_RE.sub("", text).strip()

# For the FIRST sentence only, we also flush on a clause boundary (, ; : — newline)
# once we have enough characters, so TTS can start talking sooner.
_CLAUSE_RE = re.compile(r'(?<=[,;:—])\s+|\n')
# A completed leading expression tag ("[laughter] ", "[question-en] [sigh]...")
# is a self-contained audio unit: flush it to TTS the moment it closes instead
# of waiting for the min-chars window. The lookahead requires the char AFTER
# the "]" to have streamed in, so a tag is never split mid-name.
_LEADING_TAG_RE = re.compile(r"^\s*(\[[A-Za-z][A-Za-z-]{1,30}\])(?=\s|\[)")

# Silent inline tags: <neck:left>, <oled:shy>, ... These carry no audio at all,
# so a flushed fragment made only of them has not actually started the reply.
_SILENT_TAG_RE = re.compile(r"<[^<>]{1,80}>|\[[A-Za-z][A-Za-z-]{1,30}\]")
_SPOKEN_WORD_RE = re.compile(r"\w", re.UNICODE)


def _has_spoken_word(text):
    """True if `text` contains anything the voice will actually say.

    Guards the eager first-sentence flush: a reply that OPENS with a silent tag
    ("<neck:center> Yeah, I saw that.") would otherwise have "<neck:center>"
    flushed as its first "sentence" by the min-chars split, clearing
    first_sentence_pending — which disarms the eager path even though nothing
    audible was produced, so the real first words then wait for a full
    terminator. The bracket-tag branch above avoids this by keeping the flag;
    this lets every other branch do the same.
    """
    return bool(_SPOKEN_WORD_RE.search(_SILENT_TAG_RE.sub("", text or "")))
_FIRST_FLUSH_MIN_CHARS = _LLM_CFG.get("first_sentence_eager_min_chars", 12)
_FIRST_FLUSH_CLAUSE_MIN_CHARS = _LLM_CFG.get("first_sentence_eager_clause_min_chars", 5)

print(f"[LLM] Pre-initialized: local={_USE_LOCAL} url={_LOCAL_URL} "
      f"model={_LOCAL_MODEL if _USE_LOCAL else _MODEL} temp={_TEMPERATURE}")


def _extract_sentences(buffer):
    """
    Extract complete English/Hindi sentences from the buffer.
    Returns (list_of_complete_sentences, remaining_buffer).
    """
    parts = _SENTENCE_RE.split(buffer)
    if len(parts) <= 1:
        return [], buffer

    complete = parts[:-1]
    remaining = parts[-1]
    return complete, remaining


def _effective_main_tools():
    """Return (names, schemas) for the active mode.

    A mode may override the speaking-path tool set via
    ``assistant_modes.modes.<mode>.main_tools`` (e.g. senior mode adds the care
    tools). Falls back to the global ``llm.main_tools``. Cache-safe: a mode
    switch already replaces message_history[0] and re-warms, so the changed
    tools instruction is baked into the new warm prefix.
    """
    override = _mode_tool_override()
    if override is not None:
        schemas = [t for t in TOOLS if t.get("function", {}).get("name") in override] if TOOLS else []
        return override, schemas
    return _MAIN_TOOL_NAMES, _MAIN_TOOLS


def _mode_tool_override():
    """The active mode's explicit ``main_tools`` list, or None.

    Distinguishing "this mode scoped its own tools" from "this mode uses the
    global list" matters for enforcement: the global list is a PROMPT budget,
    not a permission boundary — it deliberately omits live handlers like
    `get_current_time` that cost more in the warm prefix than they are worth,
    and refusing those would be a regression. An explicit per-mode list IS a
    boundary, because that is the whole reason a roleplay mode declares one.
    """
    try:
        from core.runtime_controls import get_active_mode
        from tools_and_config.config_loader import get_full_config
        mode = get_active_mode()
        modes = get_full_config().get("assistant_modes", {}).get("modes", {})
        override = modes.get(mode, {}).get("main_tools")
        if isinstance(override, list) and override:
            return override
    except Exception:
        pass
    return None


# JSON types worth spending prefix characters on in a tool signature. Strings
# are the default and are left unannotated.
_SHORT_TYPES = {"integer": "int", "number": "num", "boolean": "bool",
                "array": "list", "object": "obj"}


def _get_tools_instruction():
    main_names, main_tools = _effective_main_tools()
    if not main_tools:
        return ""

    local_hints = {
        "search_web": "Current web information.",
        "execute_shell_command": "Run a local system command.",
        "get_current_time": "Current local time and date.",
        "recall_memory": "MUST use for past conversations or memories. Never guess.",
        "play_music": "Play requested music.",
        "like_current_song": "Like the exact current song.",
        "play_liked_songs": "Play the liked playlist.",
        "play_last_song": "Replay the last exact song.",
        "control_music": "Pause, resume, next, or previous.",
        "look_at_scene": ("Live-camera questions about people, objects, text, outfits, "
                          "or the room. Output ONLY this."),
        "update_knowledge": "Save or update a memory.",
        "switch_voice": "Switch to a named TTS voice.",
        "switch_mode": "Switch the configured personality prompt and mode voice.",
        "set_followups": "Enable/disable the open-mic follow-up window.",
        "adjust_volume": "Increase, decrease, or set speaker volume.",
        # Without a hint a tool falls back to its full schema description —
        # these two spent ~410 chars of warm prefix between them saying what
        # fits in ~150.
        "set_timer": "Countdown timer. Total seconds preferred; 'five minutes' also works.",
        "set_person_real_name": ("A stranger (a `guest_...` name) tells you their real "
                                 "name. Confirm the spelling first, then call."),
        "get_care_plan": "Senior mode: read reminders/exercises/contacts to confirm.",
        "update_care_plan": "Senior mode: add/edit/remove reminders, exercises, contacts.",
        "alert_family": "Senior mode: email family on distress/emergency/medical need.",
        "complex_query": ("WhatsApp, email, Notion, reminders, files, audio, or "
                          "multi-step work. Pass the request verbatim; output ONLY this."),
    }
    # These lines used to be unconditional, so a mode that dropped
    # recall_memory was still ordered to call it — and did, emitting a tool it
    # had never been given. For a roleplay character that is exactly the leak
    # the context gate exists to prevent.
    available = set(main_names)
    instruction = "\n\n## TOOLS\n"
    if "recall_memory" in available:
        instruction += (
            "MANDATORY: For past conversations, memories, what was discussed, or what someone "
            "previously said, call `recall_memory`. Never guess.\n"
            "`recall_memory` searches conversations, knowledge and your dated background research.\n"
            # Scope the mandate, or it eats ordinary conversation. Measured on
            # the box with Kiki's real prompt: "tell me a long story now
            # please" answered in 2898 characters without this block, and with
            # it returned 106 characters -- a recall_memory call for "stories
            # or tales Vaibhav likes" -- instead of a story. The word MANDATORY
            # is doing that: it reads as "reach for the tool whenever memory
            # could conceivably help", which is nearly every sentence a friend
            # says. The user then hears a stub answer built from a tool result
            # rather than the reply they asked for.
            "This applies to QUESTIONS ABOUT THE PAST -- what was said, when, by whom. "
            "It does NOT apply to anything you are asked to make up, think, or explain: "
            "stories, jokes, opinions, ideas, descriptions and idle chat are yours to "
            "answer directly, at whatever length the moment deserves. Never call a tool "
            "to decide what to say.\n"
        )
    if "search_web" in available:
        instruction += "Use `search_web` for current facts that are not already in memory.\n"
    for tool in main_tools:
        fn = tool.get("function", {})
        name = fn.get("name")
        desc = local_hints.get(name, fn.get("description", ""))
        parameters = fn.get("parameters", {})
        required = set(parameters.get("required", []))
        properties = parameters.get("properties", {})
        # Non-string params carry their type. Nothing in this instruction used
        # to show a value that was not a quoted string, so the model quoted
        # numbers too — `adjust_volume({"amount":"60"})` on 2026-07-29 — and
        # the schema gate rejected a call the handler would have run. Strings
        # stay bare: they are the overwhelming majority and every character
        # here lives in the warm prefix.
        signature = ", ".join(
            (key if key in required else f"{key}?")
            + (f":{_SHORT_TYPES[t]}"
               if (t := properties.get(key, {}).get("type")) in _SHORT_TYPES else "")
            for key in properties
        )
        instruction += f"- `{name}({signature})`: {desc}\n"

    if "complex_query" in set(main_names):
        # The 12 WhatsApp tools used to live here. They were removed from the
        # speaking catalog: they inflated the warm prefix on every turn, and one
        # tool call per turn could never finish a real WhatsApp task. Keep this
        # to ONE line — the per-tool hint above already describes the routing,
        # and every character here is re-prefilled on every single turn.
        instruction += (
            "Never claim you sent or scheduled something yourself; "
            "`complex_query` does it and reports back.\n"
        )

    # The worked example must name a tool this mode ACTUALLY has. It was
    # hardcoded to recall_memory, which taught modes without it to emit it.
    examples = [
        ("recall_memory", "What did we discuss about discrete structures?",
         {"query": "discrete structures"}),
        ("search_web", "Who won the match last night?",
         {"query": "match result last night"}),
        ("play_music", "Play something by Coldplay.", {"song": "Coldplay"}),
    ]
    example = next((e for e in examples if e[0] in available), None)

    instruction += (
        "\nIf a tool is needed, output its call FIRST and stop. Do not answer before the result.\n"
        # Asked for the same thing twice more on 2026-07-29, Kiki re-used her
        # own previous wording ("Playing Maafi again") and called nothing.
        "Asked again = call again; never re-use an earlier answer, and never claim an action "
        "you did not just call.\n"
        # `"count": 5` is the whole type lesson: it is the only unquoted value
        # anywhere in this instruction, and its absence is why the model sent
        # adjust_volume `"amount": "60"`. Prose would cost 5x the characters.
        "Exact format:\n"
        "<tool_call>{\"name\": \"tool_name\", \"arguments\": {\"param_name\": \"value\", \"count\": 5}}</tool_call>\n"
    )
    if example:
        name, utterance, args = example
        instruction += (
            f"Example for '{utterance}':\n"
            f"<tool_call>{{\"name\": \"{name}\", \"arguments\": "
            f"{json.dumps(args)}}}</tool_call>\n"
        )
    return instruction


_MEMORY_HISTORY_RE = re.compile(
    r"\b(?:what|which)\s+(?:did|have)\s+(?:we|i|you)\s+"
    r"(?:discuss|talk|say|tell|mention|cover|decide|do)\b"
    r"|\b(?:did|have)\s+we\s+(?:discuss|talk|cover|decide)\b"
    r"|\bwhat\s+do\s+you\s+(?:remember|know)\s+about\s+(?:me|us)\b"
    r"|\b(?:do|can)\s+you\s+remember\b.*\b(?:i|we|my|our|me|us|told|said|discussed|talked|last|before)\b"
    r"|\bhow\s+did\s+my\b.*\b(?:test|exam|interview|project|presentation|result)\b",
    re.IGNORECASE,
)


def _should_auto_recall_memory(text):
    """Conservative autobiographical-memory intent check.

    Obvious recall requests are routed in code so the small speaking model can
    never replace a missed tool decision with a confident hallucination.
    """
    text = str(text or "").strip()
    if not text:
        return False
    lower = text.lower()
    if _MEMORY_HISTORY_RE.search(lower):
        return True
    explicit_phrases = (
        "search your memory", "search memory", "recall your memory",
        "what do you remember about", "from your memory",
        "in your memory", "our memories", "funny memories", "funniest memories",
        "past conversation", "previous conversation", "past discussion",
        "previous discussion", "last time we talked",
        "we talked about", "we discussed", "till date", "so far about me",
    )
    return (any(phrase in lower for phrase in explicit_phrases)
            or bool(re.search(r"\b(?:find|show|give|share)\b.{0,40}\bmemories\b", lower)))


def _last_user_text(messages):
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            return " ".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            ).strip()
        return str(content).strip()
    return ""


def _auto_memory_tool_event(messages, verify_prefill, use_fallback):
    """Return a synthetic recall tool turn for unmistakable memory requests."""
    # The EFFECTIVE list, not the global one: a mode that drops recall_memory
    # (every roleplay character does — Kiki's memories are not theirs) would
    # otherwise still have this router fire it in code.
    if (not verify_prefill or use_fallback or not _SEND_TOOLS
            or "recall_memory" not in _effective_main_tools()[0]):
        return None
    query = _last_user_text(messages)
    if not _should_auto_recall_memory(query):
        return None
    arguments = json.dumps({"query": query}, ensure_ascii=False)
    raw = (
        '<tool_call>{"name": "recall_memory", "arguments": '
        + arguments + "}</tool_call>"
    )
    call = {
        "id": f"call_memory_{int(time.time())}",
        "name": "recall_memory",
        "arguments": arguments,
    }
    return raw, {"calls": [call], "assistant_text": raw, "auto_routed": True}


# --- complex_query routing -------------------------------------------------
# The speaking model CAN emit complex_query itself, but the 12 WhatsApp tools
# were removed from its catalog — so if it misses the decision there is no
# WhatsApp tool left for it to fall back on, and the failure mode is a
# confident hallucination ("sure, I sent it!") for a message that never left.
# This code-level router is the safety net, exactly like _auto_memory_tool_event.

# Surfaces that ONLY the action agent can reach now.
_AGENT_SURFACE_RE = re.compile(
    r"\bwhat'?s\s?app\b|\bwhatsapp\b"
    r"|\b(?:g[- ]?mail|gmail|inbox|e-?mail)\b"
    r"|\bnotion\b"
    r"|\b(?:message[sd]?|text(?:s|ed)?|chats?|group)\b"
    r"|\bvoice\s+note\b|\bvoice\s+message\b|\baudio\s+(?:note|message|recording)\b"
    # Attachments: "send this file to my burrito time" names no messaging
    # surface at all, but there is no other way to deliver a file.
    r"|\b(?:file|document|attachment|photo|picture|screenshot|pdf|recording)\b",
    re.IGNORECASE,
)

# "send/forward/share <something> to <someone>" is agent work whatever the noun
# is — delivering anything to a person needs contact resolution plus a send.
_AGENT_SEND_TO_RE = re.compile(
    r"\b(?:send|forward|share)\b[^.?!]{0,60}?\bto\b\s+\w",
    re.IGNORECASE,
)

# Verbs that mean "go and use a surface", whether to act on it or to read it.
# Reading is included because the 12 WhatsApp read tools ALSO left the speaking
# catalog — "summarize my chat with burgito" has no local tool to fall back on.
_AGENT_ACTION_RE = re.compile(
    # act
    r"\bsend\b|\bforward\b|\breply\b|\brespond\s+to\b|\bmessage\b"
    r"|\bdraft\b|\bcompose\b|\bemail\b"
    r"|\bremind\b|\breminder\b|\bschedule\b"
    r"|\brecord\b"
    # read / summarize — these were missing, and they are the MOST common
    # phrasings for WhatsApp ("summarize my chat with X", "what's the summary
    # of the burgito chat", "catch me up on the group").
    r"|\bcheck\b|\bread\b|\bsummar(?:ise|ize|y)\b|\brecap\b"
    r"|\bcatch\s+me\s+up\b|\bcatch\s+up\b"
    r"|\bwhat(?:'?s|\s+is)?\s+(?:happening|going\s+on|new)\b|\bany\s+(?:new|unread)\b"
    r"|\bwho\s+(?:messaged|texted|sent)\b|\bunread\b|\bdid\s+anyone\b"
    r"|\blast\s+(?:few\s+)?(?:messages?|texts?)\b"
    r"|\btell\s+me\s+(?:about|what)\b|\bwhat\s+did\b.{0,30}\bsay\b",
    re.IGNORECASE,
)

# Things that look like the above but are NOT agent work — these keep ordinary
# conversation, music and memory recall on the fast local path.
_AGENT_NEGATIVE_RE = re.compile(
    r"\bplay\b|\bsong\b|\bmusic\b|\bvolume\b|\bdance\b"
    r"|\bwhat did we (?:discuss|talk)\b|\bdo you remember\b"
    r"|\bwhat time\b|\bweather\b"
    r"|\b(?:switch|change)\s+(?:to\s+)?(?:\w+\s+)?mode\b",
    re.IGNORECASE,
)


def _should_route_complex_query(text):
    """True when a request needs the multi-step action agent.

    Deliberately conservative: it must name a surface only the agent can reach
    (WhatsApp / email / Notion / voice note) AND ask for something to be done.
    """
    text = str(text or "").strip()
    if len(text) < 8:
        return False
    # Negatives win outright — a false positive costs EVERY normal turn several
    # seconds, which is far worse than the agent occasionally not auto-firing
    # (the speaking model can still choose complex_query itself).
    if _AGENT_NEGATIVE_RE.search(text):
        return False
    if _AGENT_SEND_TO_RE.search(text):
        return True
    if not _AGENT_SURFACE_RE.search(text):
        return False
    return bool(_AGENT_ACTION_RE.search(text))


def _auto_complex_query_tool_event(messages, verify_prefill, use_fallback):
    """Return a synthetic complex_query turn for unmistakable action requests."""
    if (not verify_prefill or use_fallback or not _SEND_TOOLS
            or "complex_query" not in _effective_main_tools()[0]):
        return None
    request = _last_user_text(messages)
    if not _should_route_complex_query(request):
        return None
    arguments = json.dumps({"request": request}, ensure_ascii=False)
    raw = ('<tool_call>{"name": "complex_query", "arguments": '
           + arguments + "}</tool_call>")
    call = {
        "id": f"call_complex_{int(time.time())}",
        "name": "complex_query",
        "arguments": arguments,
    }
    return raw, {"calls": [call], "assistant_text": raw, "auto_routed": True}


def _auto_control_tool_event(messages, verify_prefill, use_fallback):
    """Return a synthetic tool turn for an unmistakable spoken control."""
    if not verify_prefill or use_fallback or not _SEND_TOOLS:
        return None
    parsed = parse_spoken_control(_last_user_text(messages))
    if parsed is None:
        return None
    name, args = parsed
    if name not in _effective_main_tools()[0]:
        return None
    arguments = json.dumps(args, ensure_ascii=False)
    raw = (
        '<tool_call>{"name": ' + json.dumps(name)
        + ', "arguments": ' + arguments + "}</tool_call>"
    )
    call = {
        "id": f"call_control_{int(time.time())}",
        "name": name,
        "arguments": arguments,
    }
    return raw, {"calls": [call], "assistant_text": raw, "auto_routed": True}


def _normalize_messages_for_local(messages):
    """
    Flatten messages into the plain {role, content:str} shape the local server
    prefers, and — crucially — keep that shape byte-for-byte stable across turns
    so llama.cpp's --cache-prompt keeps hitting the cached prefix.

    - The system prompt is stored as a list of content parts; we join the text.
    - Image parts are dropped (vision injection to the speaking model is disabled).
    - tool/assistant-with-tool messages are skipped while tools are off.
    - The tools instruction becomes its OWN system message, and the active
      mode's character is repeated after the leading system block. Both
      positions are fixed for the session, so the cached prefix is unaffected.
    """
    out = []
    first_system_idx = -1
    lead_system_end = -1  # end of the leading run of system messages
    for m in messages:
        role = m.get("role")
        if role == "tool":
            continue  # no tool round-trips on the local path while tools are off
        content = m.get("content")

        if isinstance(content, list):
            texts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            content = " ".join(t for t in texts if t).strip()

        if not content:
            continue
        
        out.append({"role": role, "content": content})
        if role == "system" and first_system_idx == -1:
            first_system_idx = len(out) - 1

    # The leading run of system messages: the persona and the memory context,
    # i.e. everything built before the first conversational turn.
    lead_system_end = -1
    for idx, message in enumerate(out):
        if message.get("role") != "system":
            break
        lead_system_end = idx

    # The character is repeated at the END of the leading system block — after
    # the protocol and the memory dump, so the last thing the model reads before
    # the conversation is who it is. Measured: appending the 2159-char tools
    # instruction to the persona message flattened `rohan` into a generic
    # assistant; this restored the exact bare-chat voice at full context.
    if lead_system_end != -1:
        try:
            from core.runtime_controls import get_persona_reinforcement
            reinforcement = get_persona_reinforcement()
        except Exception:
            reinforcement = ""
        if reinforcement:
            out.insert(lead_system_end + 1,
                       {"role": "system", "content": reinforcement})

    # Tool instructions go in their own system message directly after the
    # persona, rather than concatenated onto it — 2159 chars of imperative
    # protocol glued to a 253-char character prompt made the protocol both the
    # bulk and the most recent part of the model's identity message.
    if first_system_idx != -1 and _SEND_TOOLS:
        tools_instr = _get_tools_instruction()
        if tools_instr:
            out.insert(first_system_idx + 1,
                       {"role": "system", "content": tools_instr.strip()})

    return out


class _LocalUnavailable(Exception):
    """Raised when the local server can't be reached BEFORE any token streamed,
    so the caller can cleanly fall back to the cloud model."""


class _InstantVisionUnavailable(Exception):
    """Raised when the Groq live-image path can't produce anything (no camera
    frame, or every key failed) BEFORE the first token — so stream_response
    falls through to the normal local speaking path and still answers."""


class _GroqSpeakingUnavailable(Exception):
    """Raised when the Groq PRIMARY speaking path fails BEFORE the first token
    (all keys throttled/down), so stream_response can fall back to the local box
    or the cloud model instead of leaving the turn silent."""


class _CerebrasUnavailable(Exception):
    """Raised when the Cerebras PRIMARY speaking path fails BEFORE the first
    token, so stream_response falls back to the warm local box rather than
    leaving the turn silent."""


class _OpenRouterUnavailable(Exception):
    """Raised when the OpenRouter PRIMARY speaking path fails BEFORE the first
    token — most often the shared upstream provider pool returning 429 — so
    stream_response falls back to the warm local box instead of going silent."""


def speaking_is_groq():
    """True when the speaking brain is Groq (llm.speaking_provider == 'groq')."""
    return _SPEAKING_PROVIDER == "groq"


def speaking_is_local():
    """True when replies are generated on the local box.

    This is the gate for SPECULATIVE turns: a spec turn is a full duplicate
    generation, which is free on the box's own single slot but on any paid /
    rate-limited cloud provider doubles spend and burns the per-minute budget
    that the real turn needs. So speculation runs on `local` only.
    """
    return _SPEAKING_PROVIDER == "local"


def _scan_cloud_deltas(delta_iter, state, vision_switch=None, tag="cloud"):
    """Turn a raw content-delta iterator into the SAME event stream the local
    path emits: ("sentence", s) / ("tool_calls", {...}) / ("done", full).

    This is the one place the speaking protocol lives for every CLOUD provider
    (Groq, OpenRouter) — the `<tool_call>` scanner, the `look_at_scene` vision
    switch, and the eager first-clause flush that lets TTS start talking before
    the sentence finishes. `state["emitted"]` tells the caller whether anything
    streamed, so it can distinguish "provider is down" (fall back) from "stream
    died mid-reply" (keep what we have). On a vision switch it returns WITHOUT
    a "done" event, exactly like _stream_local does.
    """
    buffer = ""
    full_content = ""
    first_sentence_pending = True
    in_tool = False
    tool_buffer = ""
    _t0 = time.time()
    # Thinking is ENABLED on the OpenRouter path, and some models emit it INLINE
    # as <think>...</think> inside content rather than on a separate field (this
    # is exactly what gemma does on the local box — hence local_llm.strip_thinking).
    # Filter it here, tag-split-safe across deltas, so reasoning is never spoken
    # by TTS and never stored in message_history.
    think_state = {"in": False, "carry": ""}

    for raw_chunk in delta_iter:
        if not raw_chunk:
            continue
        if not state["emitted"]:
            state["emitted"] = True
            print(f"[T] {tag}_first_token +{time.time() - _t0:.3f}s")
        chunk = instant_vision._think_filter(raw_chunk, think_state)
        if not chunk:
            continue
        full_content += chunk
        state["full"] = full_content
        print(chunk, end="", flush=True)

        i = 0
        while i < len(chunk):
            char = chunk[i]
            if not in_tool:
                buffer += char
                if buffer.endswith("<tool_call>"):
                    text_before = buffer[:-len("<tool_call>")]
                    if text_before:
                        sentences, remaining = _extract_sentences(text_before)
                        for s in sentences:
                            s = _scrub_reasoning(s)
                            if s:
                                first_sentence_pending = False
                                yield ("sentence", s)
                        remaining = _scrub_reasoning(remaining)
                        if remaining:
                            first_sentence_pending = False
                            yield ("sentence", remaining)
                    buffer = ""
                    in_tool = True
                    tool_buffer = ""
                else:
                    # Eager first-clause flush (same rules as the local path)
                    # so TTS starts talking as soon as possible.
                    if first_sentence_pending and _FIRST_SENTENCE_EAGER:
                        m_tag = _LEADING_TAG_RE.match(buffer)
                        if m_tag:
                            buffer = buffer[m_tag.end():]
                            yield ("sentence", m_tag.group(1))
                            continue
                        m = _CLAUSE_RE.search(buffer)
                        if m and len(buffer) >= _FIRST_FLUSH_CLAUSE_MIN_CHARS:
                            head = _scrub_reasoning(buffer[:m.start()])
                            if head:
                                # Silent-tag-only fragment: nothing audible was
                                # produced, so stay armed for the real words.
                                first_sentence_pending = not _has_spoken_word(head)
                                buffer = buffer[m.end():]
                                yield ("sentence", head)
                                continue
                        elif len(buffer) >= _FIRST_FLUSH_MIN_CHARS:
                            last_space_idx = buffer.rfind(" ")
                            if last_space_idx != -1 and last_space_idx >= _FIRST_FLUSH_MIN_CHARS:
                                head = _scrub_reasoning(buffer[:last_space_idx])
                                if head:
                                    first_sentence_pending = not _has_spoken_word(head)
                                    buffer = buffer[last_space_idx + 1:]
                                    yield ("sentence", head)
                                    continue

                    sentences, remaining = _extract_sentences(buffer)
                    if sentences:
                        buffer = remaining
                        for s in sentences:
                            s = _scrub_reasoning(s)
                            if s:
                                first_sentence_pending = False
                                yield ("sentence", s)
            else:
                tool_buffer += char
                if tool_buffer.endswith("</tool_call>"):
                    tool_json = tool_buffer[:-len("</tool_call>")].strip()
                    in_tool = False
                    tool_buffer = ""
                    try:
                        parsed = json.loads(tool_json)
                        name = parsed.get("name")
                        args = parsed.get("arguments", {})
                        args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                        # look_at_scene → hand the turn to the Groq VISION
                        # model (with a camera frame), same as the local path.
                        if (vision_switch is not None
                                and name == instant_vision.VISION_TOOL_NAME):
                            print(f"\n[InstantVision] {tag} speaking model emitted "
                                  f"{name}() → routing to Groq vision")
                            vision_switch["on"] = True
                            return
                        tc = {"id": f"call_{int(time.time())}", "name": name,
                              "arguments": args_str}
                        print(f"\n[Parsed Tool Call from {tag} stream] {name}({args_str})")
                        yield ("tool_calls", {"calls": [tc], "assistant_text": full_content})
                    except Exception as e:
                        print(f"\n[Error parsing tool JSON] {tool_json}: {e}")
            i += 1

    if not in_tool:
        tail = _scrub_reasoning(buffer)
        if tail:
            yield ("sentence", tail)
    print("\n")
    yield ("done", full_content)


def _stream_groq_speaking(messages, verify_prefill=True, abort_event=None,
                          vision_switch=None):
    """PRIMARY speaking path when ``llm.speaking_provider == 'groq'``.

    Groq's Qwen over the shared cloud protocol. Context is capped for the
    model's tokens-per-minute ceiling — on Groq that 8K TPM per key is the
    binding constraint, so this cap is the single biggest latency lever.
    """
    cfg = _GROQ_SPEAK_CFG
    # The local normalizer flattens content AND injects the <tool_call> tools
    # instruction into the first system message (identical protocol).
    normalized = _normalize_messages_for_local(messages)
    capped = instant_vision.cap_normalized_messages(
        normalized, int(cfg.get("max_context_tokens", 6000)))

    print(f"\n--- Starting GROQ speaking stream ({cfg.get('model')}) ---\n")
    state = {"emitted": False, "full": ""}
    try:
        yield from _scan_cloud_deltas(
            instant_vision.iter_deltas(capped, cfg, abort_event=abort_event),
            state, vision_switch=vision_switch, tag="groq")
    except Exception as e:
        if not state["emitted"]:
            raise _GroqSpeakingUnavailable(str(e))
        print(f"\n[GroqSpeak] stream ended early: {e}")
        yield ("done", state["full"])


def _stream_cerebras_speaking(messages, verify_prefill=True, abort_event=None,
                              vision_switch=None):
    """PRIMARY speaking path when ``llm.speaking_provider == 'cerebras'``.

    Talks to Cerebras' OpenAI-compatible endpoint DIRECTLY (no OpenRouter in the
    middle, so none of the shared-pool 429s that made that route unusable).
    Measured **0.89s to first token** on a ~4.2k-token context, with the whole
    reply arriving in a single burst.

    Streamed with raw `requests` SSE on the shared keep-alive session — the same
    lean approach as `_stream_local_inner` — rather than an SDK, to keep the hot
    path free of extra layers.

    COST NOTE — images are NEVER sent here. `_normalize_messages_for_local`
    drops image parts, and periodic scene context arrives as Gemini-written
    TEXT, so ordinary turns bill zero image tokens. Pictures only ever go out on
    the explicit `look_at_scene` tool call, which is handled by the vision path.
    """
    cfg = _CEREBRAS_CFG
    if not _CEREBRAS_KEY:
        raise _CerebrasUnavailable("CEREBRAS_API_KEY not set")

    normalized = _normalize_messages_for_local(messages)
    max_ctx = int(cfg.get("max_context_tokens", 0) or 0)
    if max_ctx > 0:
        normalized = instant_vision.cap_normalized_messages(normalized, max_ctx)

    model = cfg.get("model", "gemma-4-31b")
    url = cfg.get("api_base", "https://api.cerebras.ai/v1").rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": normalized,
        "stream": True,
        "temperature": cfg.get("temperature", _TEMPERATURE),
        "max_tokens": int(cfg.get("max_completion_tokens", 1024)),
    }
    print(f"\n--- Starting CEREBRAS speaking stream ({model}) ---\n")

    def _deltas():
        try:
            resp = _SESSION.post(
                url, json=payload, stream=True,
                headers={"Authorization": f"Bearer {_CEREBRAS_KEY}"},
                timeout=(3, cfg.get("request_timeout", 30)),
            )
            resp.raise_for_status()
        except Exception as e:
            raise _CerebrasUnavailable(str(e))

        try:
            for raw in resp.iter_lines():
                if abort_event is not None and abort_event.is_set():
                    resp.close()
                    return
                if not raw:
                    continue
                line = raw.decode("utf-8", "ignore")
                if not line.startswith("data: "):
                    continue
                body = line[6:].strip()
                if body == "[DONE]":
                    break
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    continue
                usage = data.get("usage")
                if usage:
                    # image_tokens is surfaced so surplus image spend is visible
                    # rather than silent — it must stay 0 on ordinary turns.
                    print(f"\n[Cerebras] tokens prompt={usage.get('prompt_tokens')} "
                          f"completion={usage.get('completion_tokens')} "
                          f"image={usage.get('image_tokens', 0)}")
                choices = data.get("choices") or []
                if not choices:
                    continue
                piece = (choices[0].get("delta") or {}).get("content") or ""
                if piece:
                    yield piece
        finally:
            try:
                resp.close()
            except Exception:
                pass

    state = {"emitted": False, "full": ""}
    try:
        yield from _scan_cloud_deltas(_deltas(), state,
                                      vision_switch=vision_switch, tag="cerebras")
    except Exception as e:
        if not state["emitted"]:
            raise _CerebrasUnavailable(str(e))
        print(f"\n[Cerebras] stream ended early: {e}")
        yield ("done", state["full"])


def _stream_openrouter_speaking(messages, verify_prefill=True, abort_event=None,
                                vision_switch=None):
    """PRIMARY speaking path when ``llm.speaking_provider == 'openrouter'``.

    Streams gemma-4-31b via OpenRouter pinned to a preferred provider (Cerebras
    by default — measured ~0.9-1.7s to first token at 260-1700 tok/s, so the
    whole reply effectively lands at once). Uses the shared cloud protocol, so
    tools/vision/history behave exactly as on every other path.

    Unlike Groq there is no tokens-per-minute squeeze to design around (131K-262K
    context here), so the conversation is sent uncapped by default. The real
    failure mode is the shared upstream pool returning 429; that raises
    _OpenRouterUnavailable before the first token and the caller falls back to
    the warm local box.
    """
    cfg = _OPENROUTER_CFG
    normalized = _normalize_messages_for_local(messages)
    max_ctx = int(cfg.get("max_context_tokens", 0) or 0)
    if max_ctx > 0:
        normalized = instant_vision.cap_normalized_messages(normalized, max_ctx)

    model = cfg.get("model", "openrouter/google/gemma-4-31b-it")
    providers = list(cfg.get("providers") or [])
    extra = {}
    if providers:
        extra["provider"] = {"order": providers,
                             "allow_fallbacks": bool(cfg.get("allow_fallbacks", False))}

    # THINKING IS ENABLED on this path and deliberately never suppressed. The
    # reasoning tokens are requested here; _deltas below keeps them OUT of the
    # spoken stream (see below), so enabling thinking costs nothing at the mic.
    _reason_cfg = cfg.get("reasoning", {}) or {}
    if _reason_cfg.get("enabled", True):
        effort = _reason_cfg.get("effort", "high")
        extra["reasoning"] = ({"effort": effort} if effort else {})
        extra["include_reasoning"] = True

    print(f"\n--- Starting OPENROUTER speaking stream ({model}"
          f"{' via ' + '/'.join(providers) if providers else ''}"
          f"{' +thinking' if extra.get('include_reasoning') else ''}) ---\n")

    def _deltas():
        resp = _get_completion()(
            model=model, messages=normalized, stream=True,
            temperature=cfg.get("temperature", _TEMPERATURE),
            max_tokens=int(cfg.get("max_completion_tokens", 1024)),
            api_key=_OPENROUTER_KEY,
            timeout=cfg.get("request_timeout", 20),
            **({"extra_body": extra} if extra else {}),
        )
        reasoned = 0
        for chunk in resp:
            if abort_event is not None and abort_event.is_set():
                return
            try:
                delta = chunk.choices[0].delta
            except (IndexError, AttributeError):
                continue
            # Reasoning arrives on its own field — consume it so the model can
            # think, but NEVER yield it: it must not be spoken by TTS nor land
            # in message_history. Providers that don't support hidden thinking
            # simply never populate this.
            rc = (getattr(delta, "reasoning_content", None)
                  or getattr(delta, "reasoning", None) or "")
            if rc:
                reasoned += len(rc)
            piece = getattr(delta, "content", None) or ""
            if piece:
                yield piece
        if reasoned:
            print(f"\n[OpenRouter] thinking: {reasoned} reasoning chars (not spoken)")

    state = {"emitted": False, "full": ""}
    try:
        yield from _scan_cloud_deltas(_deltas(), state,
                                      vision_switch=vision_switch, tag="openrouter")
    except Exception as e:
        if not state["emitted"]:
            raise _OpenRouterUnavailable(str(e))
        print(f"\n[OpenRouter] stream ended early: {e}")
        yield ("done", state["full"])


# Whether the most recent PRIMARY turn was answered by the Groq instant-vision
# path (the local box was NOT touched). main.py reads this to force a real
# rewarm after such a turn: the reply was never prefilled server-side, so the
# usual after_speaking=True (--prefill-after-response owns it) shortcut would
# wrongly mark a stale prefix warm. Reset at the top of every primary turn.
_INSTANT_VISION_LAST = {"used": False}


def last_turn_used_instant_vision():
    """True if the last primary turn was served by the Groq live-image path."""
    return _INSTANT_VISION_LAST["used"]


def _stream_instant_vision(messages, abort_event=None):
    """Answer a live-image question by streaming Groq's Qwen VLM.

    Grabs a fresh camera frame, builds a TPM-capped request (system prompt +
    trimmed history + image), and yields ("sentence", s) as sentences form,
    then ("done", full). The box is never touched. The image is NOT added to
    message_history — only the text reply is (via the normal post-turn append),
    so the next local turn's KV-cache prefix stays clean.

    Raises _InstantVisionUnavailable if it can't get a frame or a first token,
    letting the caller fall back to the local path.
    """
    cfg = instant_vision._cfg()

    # Grab the sharpest of a few fresh frames (best chance of reading labels).
    try:
        image_b64 = instant_vision.capture_best_frame_b64(cfg)
    except Exception as e:
        raise _InstantVisionUnavailable(f"camera error: {e}")
    if not image_b64:
        raise _InstantVisionUnavailable("no camera frame")

    groq_messages = instant_vision.build_capped_messages(messages, image_b64, cfg)
    print(f"\n--- Starting INSTANT-VISION stream (Groq {cfg.get('model', 'qwen/qwen3.6-27b')}) ---\n")

    buffer = ""
    full_content = ""
    first = True
    first_sentence_pending = True
    try:
        for delta in instant_vision.iter_deltas(groq_messages, cfg, abort_event=abort_event):
            first = False
            full_content += delta
            print(delta, end="", flush=True)
            buffer += delta

            # Eager first-clause flush so TTS starts talking sooner (mirrors the
            # local path's first-sentence behaviour).
            if first_sentence_pending and _FIRST_SENTENCE_EAGER:
                m = _CLAUSE_RE.search(buffer)
                if m and len(buffer) >= _FIRST_FLUSH_CLAUSE_MIN_CHARS:
                    head = _scrub_reasoning(buffer[:m.start()])
                    if head:
                        first_sentence_pending = False
                        buffer = buffer[m.end():]
                        yield ("sentence", head)
                        continue

            sentences, buffer = _extract_sentences(buffer)
            for s in sentences:
                s = _scrub_reasoning(s)
                if s:
                    first_sentence_pending = False
                    yield ("sentence", s)
    except Exception as e:
        # Failed before producing anything → let the caller fall back to local.
        if first:
            raise _InstantVisionUnavailable(str(e))
        print(f"\n[InstantVision] stream ended early: {e}")

    tail = _scrub_reasoning(buffer)
    if tail:
        yield ("sentence", tail)
    print("\n")

    # The local box sat IDLE this whole turn (Groq did the work). Use the
    # remaining idle time — this answer is still streaming out through TTS — to
    # prefill the NEXT turn's prefix (user question + this reply) NOW, so the
    # following LOCAL turn is a cache hit instead of paying the box's slow
    # (~13 tok/s) prefill AFTER the user speaks. Without this, the box is still
    # warm only at the pre-image prefix, and the next turn re-prefills the whole
    # image exchange while the user waits. main.py's post-turn register_history
    # registers this SAME prefix, so it just no-ops (hash match).
    # Use the STRIPPED reply — main.py stores final_assistant_raw.strip(), so the
    # normalized prefix must match byte-for-byte or its register_history warms a
    # second time (defeating the head start).
    reply_for_history = full_content.strip()
    _aborted = abort_event is not None and abort_event.is_set()
    if reply_for_history and not _aborted:
        try:
            warm_prefix = _normalize_messages_for_local(
                list(messages) + [{"role": "assistant", "content": reply_for_history}])
            local_llm.update_speaking_prefix(warm_prefix)
            local_llm.schedule_rewarm()
            print("[InstantVision] pre-warming box for next turn "
                  "(overlaps TTS playback of this answer)")
        except Exception as e:
            print(f"[InstantVision] pre-warm skipped: {e}")

    yield ("done", full_content)


def warmup(messages=None):
    """
    Prime the local box's KV cache with the EXACT prefix real turns use, so the
    first real user turn doesn't pay the cold prefill (~7s).

    Thin wrapper over the coordinator's rewarm mechanism: register the normalized
    prefix and fire a prefill-only request (max_tokens=1). Preemptible — if the
    user speaks during startup, their request aborts the warmup and finishes the
    prefill from the partial cache llama.cpp already built (cache_prompt).

    Call again (cheap) whenever the prefix changes, e.g. after startup workers
    inject their context into message_history.

    Still runs under the GROQ speaking provider: the box is completely idle then,
    so warming it is free and makes it a HOT STANDBY. That matters — when every
    Groq key is in 429 cooldown we fall back to the box, and an unwarmed box pays
    a full cold prefill (~5900 tokens at ~13 tok/s = ~45s, measured). Warm, the
    same fallback answers in 1-2s.
    """
    if not _USE_LOCAL:
        return
    try:
        t0 = time.time()
        if messages is not None:
            # Thread-safe copy of the live messages list
            base = _normalize_messages_for_local(list(messages))
        else:
            base = [{"role": "system", "content": _LLM_CFG.get("system_prompt", "You are Kiki.")}]
        local_llm.update_speaking_prefix(base)
        local_llm.rewarm()
        n_sys = sum(1 for m in base if m.get("role") == "system")
        print(f"[LLM] Warmup complete — cached {n_sys} system message(s) on local box "
              f"({time.time() - t0:.1f}s).")
    except Exception as e:
        print(f"[LLM] Warmup failed (box offline?): {e}")


def _stream_local(messages, verify_prefill=True, abort_event=None, vision_switch=None):
    """
    Stream sentences from the local llama-server via OpenAI-compatible SSE.

    Yields ("sentence", str) as sentences form and finally ("done", full_text).
    Raises _LocalUnavailable if the connection fails before the first token
    (so stream_response can fall back to Gemini).

    verify_prefill: run the prompt-reprocess diagnostic on this request. The
    tool-result FOLLOW-UP turn passes False — it legitimately prefills the new
    result note, so a large prompt_n there is expected, not a cache failure.

    abort_event: cooperative kill switch for SPECULATIVE turns — checked per
    SSE chunk; when set, the HTTP response is closed (freeing the single slot)
    and the stream ends without a "done" event.
    """
    # The user is talking to us now → block auto-rewarm and free the box: kill
    # any shared background task (summary / vision) still running on
    # the single-slot local server. note_user_activity() marks the conversation
    # HOT, reserving the box for speaking (background work reroutes to cloud).
    local_llm.note_user_activity()
    local_llm.note_speaking(True)
    local_llm.preempt_background()

    normalized = _normalize_messages_for_local(messages)
    state = {"full": ""}
    try:
        yield from _stream_local_inner(normalized, state, verify_prefill=verify_prefill,
                                       abort_event=abort_event, vision_switch=vision_switch)
    finally:
        local_llm.note_speaking(False)
        # Prefix registration & rewarm happen in register_history(), called by
        # main.py AFTER it appends the final (movement/tool-tag-stripped)
        # assistant message to message_history. Registering here with the RAW
        # streamed text caused a byte-level mismatch with what the next turn
        # actually sends → SWA cache divergence → repeated full re-prefills.


# --- Conversation snapshot for the action agent ----------------------------
# The complex_query router synthesises its tool call from the user's WORDS
# alone, so without this the agent has no idea what "send it to him" refers to,
# or that "him" was named three turns ago.
#
# Deliberately a module snapshot rather than a tool argument: a tool call's
# arguments become assistant text that gets registered into the speaking prefix
# (register_history, below), so pasting a conversation in there would rewrite
# the warm prefix on every routed turn and break the §4 cache contract. Reading
# it out-of-band costs the speaking path nothing.
_SNAPSHOT_SPACE_RE = re.compile(r"\s+")
_CONVERSATION_SNAPSHOT: list = []


def _note_conversation(messages) -> None:
    """Record the live history for out-of-band readers. Never raises.

    Keeps SYSTEM messages too. They are not noise: main.py files every tool
    result under that role, and so does the compacted memory the summariser
    writes back. Dropping them cost the agent both the results of Kiki's own
    tool calls (the music URL, the search hit, the email body) and everything
    older than the last compaction.

    Index 0 — the persona — is preserved so callers can skip it by position.
    """
    try:
        _CONVERSATION_SNAPSHOT[:] = [
            {"role": m.get("role"), "content": m.get("content")}
            for m in list(messages)[:1] + list(messages)[1:][-160:]
        ]
    except Exception:
        pass


def conversation_snapshot(max_chars: int = 28000,
                          per_record_chars: int = 1200) -> str:
    """Recent history as prose: spoken turns, tool calls AND tool results."""
    from core.brain.history_view import as_text
    return as_text(_CONVERSATION_SNAPSHOT, max_chars, per_record_chars)


def conversation_artifacts(limit: int = 12) -> list[str]:
    """Links, paths and chat ids seen recently — newest first."""
    from core.brain.history_view import harvest_artifacts
    return harvest_artifacts(_CONVERSATION_SNAPSHOT, limit)


def persona_brief(max_chars: int = 1000) -> str:
    """The identity opening of Kiki's system prompt — who she is, whose home.

    An excerpt, not the whole 7.9k prompt: the rest is behavioural rules for
    free-form conversation (moods, when to refuse, how to be sarcastic) that
    would only distract a model whose job this turn is to call tools correctly.
    The agent's summary is re-voiced by the speaking model, which still has the
    full persona.
    """
    text = _SNAPSHOT_SPACE_RE.sub(" ", str(_LLM_CFG.get("system_prompt") or "")).strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max(0, max_chars)]
    # Back up to a sentence end so the excerpt does not trail off mid-word.
    cut = max(clipped.rfind(". "), clipped.rfind("! "), clipped.rfind("? "))
    return clipped[:cut + 1] if cut > max_chars * 0.5 else clipped


def register_history(messages, after_speaking=False):
    """
    Register the live message_history as the speaking prefix and re-warm it.

    Call AFTER appending the assistant reply (and any tool-result system notes)
    to message_history. The rewarm re-sends the conversation as a PREFILL
    request, converting the reply's generated tokens into prefilled ones with
    proper SWA checkpoints — and because the prefix is derived from the SAME
    list the next turn will send, the byte-for-byte match guarantees a cache
    hit (next turn only prefills the new user message).

    after_speaking: True when called right after a SPEAKING turn. If the box
    runs --prefill-after-response it already prefilled the reply server-side, so
    the client rewarm is redundant — we just record the prefix as warm instead
    of firing a duplicate prefill. Background/idle callers leave this False (the
    server doesn't know about their context, so the client must rewarm).

    Runs under the GROQ provider too, keeping the idle box warm as a fallback
    (see warmup). Callers must pass after_speaking=False in that case — the box
    did not generate the reply, so there is no server-side prefill to defer to.
    """
    if not _USE_LOCAL:
        return
    try:
        prefix = _normalize_messages_for_local(list(messages))
        local_llm.update_speaking_prefix(prefix)
        if after_speaking and not local_llm.rewarm_after_speaking_enabled():
            # Server's --prefill-after-response owns this prefill. Mark warm so a
            # later no-op rewarm is skipped, but don't duplicate the prefill pass.
            local_llm.mark_prefix_warm()
        else:
            local_llm.schedule_rewarm()
    except Exception as e:
        print(f"[LLM] register_history failed: {e}")


def hot_inject(messages, message):
    """Append a message to the live conversation history AND immediately re-warm
    the speaking prefix, so anything injected outside a speaking turn (face
    events, worker results, peeping, background brain findings) is prompt-cached
    on the box before the user's next turn — making that turn a cache hit.

    Safe to call any time: register_history's rewarm is coalesced and skipped
    while a speaking request is in flight, and the speaking path snapshots a
    normalized copy of the history at call time, so a concurrent append can't
    corrupt an in-flight request. The re-warmed prefix stays a valid byte-prefix
    of the next turn's request (KV-cache rule #2)."""
    messages.append(message)
    register_history(messages)


def _evaluate_prompt_timings(timings):
    """Inspect the server's per-request prompt timings to confirm the speaking
    turn was a KV-cache hit (only the new user question prefilled).

    `timings.prompt_n` = prompt tokens the server FRESHLY processed (cache miss).
    `timings.cache_n`  = prompt tokens reused from cache (present on newer
    llama.cpp builds; absent → we report prompt_n alone).

    Prints a one-line prefill report every turn; if prompt_n exceeds the alert
    threshold and exit_on_prompt_reprocess is set, prints a loud diagnostic and
    hard-exits the process (the user asked to catch this the instant it happens).
    """
    if not timings:
        if not _timings_warned["no_timings"]:
            _timings_warned["no_timings"] = True
            print("[Prefill] ⚠ Server sent no `timings` object — cannot verify the "
                  "prompt was cached. Add `timings_per_token: true` support / include "
                  "`timings` (prompt_n, cache_n) in the streamed response on the box.")
        return
    prompt_n = timings.get("prompt_n")
    cache_n = timings.get("cache_n")
    if prompt_n is None:
        return
    cache_str = f"{cache_n}" if cache_n is not None else "?"
    # cache_restored_pos (newer box build): the pos a context checkpoint was
    # restored from. -1/None = no checkpoint restore (direct slot continuation).
    restored = timings.get("cache_restored_pos")
    restored_str = ""
    if restored is not None and restored >= 0:
        restored_str = f" restored@{restored}"
    prompt_ms = timings.get("prompt_ms")
    rate = ""
    if isinstance(prompt_ms, (int, float)) and prompt_n:
        rate = f" ({prompt_n / (prompt_ms / 1000):.0f} tok/s)"
    print(f"[Prefill] prompt_n={prompt_n} (freshly prefilled) "
          f"cache_n={cache_str} (reused from KV cache) "
          f"prompt_ms={prompt_ms if prompt_ms is not None else '?'}{rate}{restored_str}")

    if _EXIT_ON_REPROCESS and prompt_n > _REPROCESS_ALERT_TOKENS:
        import os
        print("\n" + "=" * 70)
        print("‼️  PROMPT RE-PROCESSED — the warm prefix was NOT ready for this turn.")
        print(f"    The server prefilled {prompt_n} prompt tokens "
              f"(> alert threshold {_REPROCESS_ALERT_TOKENS}).")
        print(f"    cache_n (reused) = {cache_str}.")
        print("    A near-instant turn should prefill only the new user question.")
        print("    Causes: a background task evicted the prefix and its rewarm")
        print("    hadn't finished, or the prefix diverged byte-for-byte.")
        print("    (Disable with llm.exit_on_prompt_reprocess=false.)")
        print("=" * 70 + "\n")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(3)


def _stream_local_inner(normalized, state, verify_prefill=True, abort_event=None,
                        vision_switch=None):
    payload = {
        "model": _LOCAL_MODEL,
        "messages": normalized,
        "stream": True,
        "temperature": _TEMPERATURE,
        "max_tokens": _LOCAL_MAX_TOKENS,
        "cache_prompt": True,  # llama.cpp: reuse KV cache for the shared prefix
        # The server runs WITHOUT --reasoning-budget so background callers can set a
        # per-request budget; the SPEAKING path must therefore explicitly say
        # "no thinking" or gemma would think on every voice turn.
        "thinking_budget_tokens": 0,
        # Ask the box to attach prompt/cache timings to the streamed chunks so we
        # can verify this turn was a cache hit (see _evaluate_prompt_timings).
        "timings_per_token": True,
    }

    print(f"\n--- Starting LOCAL stream ({_LOCAL_URL}) ---\n")

    try:
        resp = _SESSION.post(_LOCAL_URL, json=payload, stream=True, timeout=(3, 120))
        resp.raise_for_status()
    except Exception as e:
        # Nothing streamed yet → safe to fall back.
        raise _LocalUnavailable(str(e))

    buffer = ""
    full_content = ""
    first_sentence_pending = True
    tool_calls = []
    last_timings = None  # server prompt/cache timings (keep the latest seen)

    in_tool = False
    tool_buffer = ""
    _t_post = time.time()
    _t_first_token = None

    # Speculative turns: the per-line abort check below can't fire during the
    # prefill (no SSE lines flow yet), so a watcher closes the socket the
    # moment the abort event sets — the server drops the request mid-prefill
    # and the single slot frees immediately (same trick preempt_background
    # uses). The watcher exits when the stream finishes normally.
    _stream_done = None
    if abort_event is not None:
        _stream_done = threading.Event()

        def _abort_watcher():
            # Exits on: abort fired, stream finished, or response closed by
            # any other path (consumer dropped the generator).
            while not _stream_done.is_set() and not getattr(resp.raw, "closed", True):
                if abort_event.wait(timeout=0.1):
                    try:
                        resp.close()
                    except Exception:
                        pass
                    return

        threading.Thread(target=_abort_watcher, daemon=True, name="spec-abort-watch").start()

    _lines = resp.iter_lines()
    while True:
        try:
            raw = next(_lines)
        except StopIteration:
            break
        except Exception:
            # The abort watcher closed the socket under us (mid-prefill or
            # between tokens) — that's a clean speculative abort, not an error.
            if abort_event is not None and abort_event.is_set():
                print("\n[LLM] Speculative stream aborted (socket closed)")
                _stream_done.set()
                return
            if _stream_done is not None:
                _stream_done.set()
            raise
        if abort_event is not None and abort_event.is_set():
            # Speculative turn invalidated (speech resumed). Close the response
            # so llama-server drops the request and frees the slot; the caller
            # schedules a rewarm of the real prefix.
            try:
                resp.close()
            except Exception:
                pass
            print("\n[LLM] Speculative stream aborted")
            _stream_done.set()
            return
        if not raw:
            continue
        line = raw.decode("utf-8", "ignore")
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        t = data.get("timings")
        if t:
            last_timings = t

        choices = data.get("choices")
        if not choices:
            continue
        delta = choices[0].get("delta", {}) or {}

        chunk = delta.get("content") or ""
        if not chunk:
            continue

        if _t_first_token is None:
            _t_first_token = time.time()
            print(f"\n[T] llm_first_token +{_t_first_token - _t_post:.3f}s after POST")

        full_content += chunk
        print(chunk, end="", flush=True)

        i = 0
        while i < len(chunk):
            char = chunk[i]
            if not in_tool:
                buffer += char
                if buffer.endswith("<tool_call>"):
                    # Extract and yield any text before <tool_call>
                    text_before = buffer[:-len("<tool_call>")]
                    if text_before:
                        sentences, remaining = _extract_sentences(text_before)
                        for s in sentences:
                            s = _scrub_reasoning(s)
                            if s:
                                first_sentence_pending = False
                                yield ("sentence", s)
                        remaining = _scrub_reasoning(remaining)
                        if remaining:
                            first_sentence_pending = False
                            yield ("sentence", remaining)
                    buffer = ""
                    in_tool = True
                    tool_buffer = ""
                else:
                    # Eager first-sentence flush
                    if first_sentence_pending and _FIRST_SENTENCE_EAGER:
                        # Completed leading tag → flush instantly (keeps
                        # first_sentence_pending so the words after it still
                        # get the eager clause/min-chars flush).
                        m_tag = _LEADING_TAG_RE.match(buffer)
                        if m_tag:
                            buffer = buffer[m_tag.end():]
                            yield ("sentence", m_tag.group(1))
                            continue
                        m = _CLAUSE_RE.search(buffer)
                        if m and len(buffer) >= _FIRST_FLUSH_CLAUSE_MIN_CHARS:
                            head = _scrub_reasoning(buffer[:m.start()])
                            if head:
                                # Silent-tag-only fragment: nothing audible was
                                # produced, so stay armed for the real words.
                                first_sentence_pending = not _has_spoken_word(head)
                                buffer = buffer[m.end():]
                                yield ("sentence", head)
                                continue
                        elif len(buffer) >= _FIRST_FLUSH_MIN_CHARS:
                            last_space_idx = buffer.rfind(" ")
                            if last_space_idx != -1 and last_space_idx >= _FIRST_FLUSH_MIN_CHARS:
                                head = _scrub_reasoning(buffer[:last_space_idx])
                                if head:
                                    first_sentence_pending = not _has_spoken_word(head)
                                    buffer = buffer[last_space_idx + 1:]
                                    yield ("sentence", head)
                                    continue

                    # Normal sentence extraction
                    sentences, remaining = _extract_sentences(buffer)
                    if sentences:
                        buffer = remaining
                        for s in sentences:
                            s = _scrub_reasoning(s)
                            if s:
                                first_sentence_pending = False
                                yield ("sentence", s)
            else:
                tool_buffer += char
                if tool_buffer.endswith("</tool_call>"):
                    tool_json = tool_buffer[:-len("</tool_call>")].strip()
                    in_tool = False
                    tool_buffer = ""
                    try:
                        parsed = json.loads(tool_json)
                        name = parsed.get("name")
                        args = parsed.get("arguments", {})
                        if isinstance(args, dict):
                            args_str = json.dumps(args)
                        else:
                            args_str = str(args)

                        # look_at_scene = the model asking to use its live vision.
                        # Don't surface it as a normal tool call — flag it and end
                        # the local stream (no "done"); stream_response then routes
                        # the turn to the Groq multimodal model.
                        if (vision_switch is not None
                                and name == instant_vision.VISION_TOOL_NAME):
                            print(f"\n[InstantVision] local model emitted "
                                  f"{name}() → routing to Groq vision")
                            vision_switch["on"] = True
                            if _stream_done is not None:
                                _stream_done.set()
                            try:
                                resp.close()
                            except Exception:
                                pass
                            return

                        tc = {
                            "id": f"call_{int(time.time())}",
                            "name": name,
                            "arguments": args_str
                        }
                        tool_calls.append(tc)
                        print(f"\n[Parsed Tool Call from Stream] {name}({args_str})")
                        # Yield the tool call event
                        yield ("tool_calls", {"calls": [tc], "assistant_text": full_content})
                    except Exception as e:
                        print(f"\n[Error parsing tool JSON] {tool_json}: {e}")
            i += 1

    # Yield remaining buffer
    if _stream_done is not None:
        _stream_done.set()   # stream over — let the abort watcher exit

    tail = _scrub_reasoning(buffer)
    if tail:
        yield ("sentence", tail)

    print("\n")

    # Verify this speaking turn was a KV-cache hit (only the new question was
    # prefilled). Hard-exits on a re-process when the diagnostic is enabled.
    # Skipped for the tool-result follow-up (it legitimately prefills the note).
    if verify_prefill:
        _evaluate_prompt_timings(last_timings)

    state["full"] = full_content
    yield ("done", full_content)


# Per-tool caps on the result text handed to the follow-up turn. The default
# (1500) keeps raw dumps from blowing the prefill budget; complex_query returns
# a finished answer rather than raw data, so it gets room to be a full reply.
_TOOL_RESULT_LIMITS = {"complex_query": 2600}


def execute_tool_calls(calls):
    """
    Run parsed tool calls (from the 'tool_calls' stream event) and return a
    human-readable results string. The caller injects this back as a system note
    and asks the model to speak it — this sidesteps gemma's flaky `tool` role
    while still delivering the result as a natural follow-up utterance.
    """
    # execute_tool() dispatches off the GLOBAL handler registry, so a tool the
    # active mode was never given still runs if the model names it. That is not
    # hypothetical: a roleplay mode with no memory tools emitted recall_memory
    # anyway, which would have handed a character Vaibhav's private memories.
    #
    # Enforced ONLY for modes that declare their own main_tools. The global
    # llm.main_tools is a prompt-budget choice, not a permission boundary — it
    # omits live handlers like get_current_time purely to keep the warm prefix
    # small, so refusing those would break default mode for no benefit.
    # Agents always keep the full registry; this bound is the SPEAKING path.
    override = _mode_tool_override()
    allowed = set(override) if override else None

    results = []
    for c in calls:
        name = c.get("name")
        if not name:
            continue
        if allowed is not None and name not in allowed:
            print(f"[Tool] REFUSED {name}: not in the '{get_active_mode()}' "
                  f"mode's catalog")
            results.append(
                f"- {name}: (unavailable — you cannot do that right now; "
                f"answer from the conversation instead)")
            continue
        try:
            args = json.loads(c.get("arguments") or "{}")
        except Exception:
            args = {}
        print(f"[Tool] Executing {name}({args}) in background...")
        try:
            res = execute_tool(name, args)
        except Exception as e:
            res = f"(error running {name}: {e})"
        # Cap verbose results (e.g. web search dumps) so the follow-up turn's
        # prefill stays small and we don't blow the conversation token budget.
        # complex_query is the exception: its result is not raw data to be
        # distilled, it is the finished spoken answer (already bounded by
        # action_agent.summary_max_chars), so clipping it here would truncate
        # the reply mid-sentence.
        res = str(res)
        limit = _TOOL_RESULT_LIMITS.get(name, 1500)
        if len(res) > limit:
            res = res[:limit] + " …(truncated)"
        results.append(f"- {name}: {res}")
    return "\n".join(results)


def _fetch_chunks(response, q):
    try:
        for chunk in response:
            q.put(("chunk", chunk))
        q.put(("done", None))
    except Exception as e:
        q.put(("error", e))

def stream_response(messages, use_fallback=False, verify_prefill=True,
                    abort_event=None, local_only=False):
    """
    Stream LLM response and yield complete sentences as they form.
    Matches the streaming logic from gemini_test.py exactly.
    Uses pre-cached config for zero overhead.

    verify_prefill: when False (the tool-result follow-up), the prompt-reprocess
    diagnostic is skipped — that turn legitimately prefills the new result note.

    abort_event / local_only: used by SPECULATIVE turns. abort_event kills the
    local stream cooperatively; local_only prevents a discarded speculative run
    from burning cloud quota — the adopted/real turn does its own fallback.
    """
    # Snapshot the live history for the action agent BEFORE any routing, so it
    # is there whether complex_query is reached by the code router below or
    # emitted by the model itself. Costs one list comprehension per turn and
    # never touches the prompt the box sees.
    if verify_prefill:
        _note_conversation(messages)

    auto_control = _auto_control_tool_event(messages, verify_prefill, use_fallback)
    if auto_control is not None:
        raw, event = auto_control
        call = event["calls"][0]
        print(f"[Tool] Auto-routed spoken control: {call['name']}({call['arguments']})")
        yield ("tool_calls", event)
        yield ("done", raw)
        return

    auto_memory = _auto_memory_tool_event(messages, verify_prefill, use_fallback)
    if auto_memory is not None:
        raw, event = auto_memory
        print(f"[Tool] Auto-routed memory recall: {_last_user_text(messages)!r}")
        yield ("tool_calls", event)
        yield ("done", raw)
        return

    # Multi-step / WhatsApp / email / Notion work → the action agent. Routed in
    # code because those tools no longer exist in the speaking catalog, so a
    # missed model decision would otherwise produce a confident fake success.
    auto_complex = _auto_complex_query_tool_event(messages, verify_prefill, use_fallback)
    if auto_complex is not None:
        raw, event = auto_complex
        print(f"[Tool] Auto-routed complex query: {_last_user_text(messages)!r}")
        yield ("tool_calls", event)
        yield ("done", raw)
        return

    # --- INSTANT-VISION PATH: live-image questions → Groq multimodal Qwen ---
    # Only on the PRIMARY user turn (verify_prefill), never on the tool-result
    # follow-up (verify_prefill=False), the cloud fallback (use_fallback), or a
    # speculative pre-generation (local_only — those run blind on the box). If
    # Groq can't get a frame/token, we fall through to the local path and still
    # answer. abort_event (IR barge-in) still cuts the Groq stream.
    is_primary_turn = verify_prefill and not use_fallback and not local_only
    if is_primary_turn:
        # New primary turn: clear the previous turn's routing marker.
        _INSTANT_VISION_LAST["used"] = False
    if (is_primary_turn and instant_vision.enabled()
            and instant_vision.is_instant_image_query(_last_user_text(messages))):
        print(f"[InstantVision] Routing live-image query to Groq: "
              f"{_last_user_text(messages)!r}")
        try:
            yield from _stream_instant_vision(messages, abort_event=abort_event)
            _INSTANT_VISION_LAST["used"] = True
            return
        except _InstantVisionUnavailable as e:
            print(f"[InstantVision] unavailable ({e}); falling back to local path.")

    # --- PRIMARY PATH (Groq): stream every reply from Groq's Qwen ---
    # Selected by llm.speaking_provider == "groq". Same <tool_call>/vision_switch
    # handling as the local path below, so tools, follow-ups and speculative
    # turns are identical. If Groq is fully unavailable (all keys down) it falls
    # back to the local box (if configured) or the cloud model.
    if _SPEAKING_PROVIDER in ("groq", "openrouter", "cerebras") and not use_fallback:
        _cloud_stream = {"groq": _stream_groq_speaking,
                         "openrouter": _stream_openrouter_speaking,
                         "cerebras": _stream_cerebras_speaking}[_SPEAKING_PROVIDER]
        vision_switch = {"on": False}
        try:
            yield from _cloud_stream(messages, verify_prefill=verify_prefill,
                                     abort_event=abort_event,
                                     vision_switch=vision_switch)
            if vision_switch["on"]:
                if not is_primary_turn:
                    yield ("vision_requested", None)
                    return
                _INSTANT_VISION_LAST["used"] = True
                try:
                    yield from _stream_instant_vision(messages, abort_event=abort_event)
                except _InstantVisionUnavailable as e:
                    print(f"[InstantVision] unavailable after look_at_scene ({e}).")
                    apology = "Hmm, I can't see clearly right now."
                    yield ("sentence", apology)
                    yield ("done", apology)
            return
        except (_GroqSpeakingUnavailable, _OpenRouterUnavailable,
                _CerebrasUnavailable) as e:
            print(f"\n[CloudSpeak] {_SPEAKING_PROVIDER} unavailable ({e}).")
            if local_only:
                yield ("local_unavailable", str(e))
                return
            if not _USE_LOCAL:
                # No local box to fall back to → try the cloud model, else give up.
                if _FALLBACK_MODEL:
                    print(f"Falling back to cloud {_FALLBACK_MODEL}...\n")
                    yield from stream_response(messages, use_fallback=True)
                else:
                    yield ("done", "")
                return
            # _USE_LOCAL is set → fall through to the local box path below.
            print("Falling back to local box...\n")

    # --- PRIMARY PATH: local llama.cpp speaking model ---
    # vision_switch is the SMART-MODEL fallback for the regex above: if the local
    # model decides mid-stream that the question needs live vision, it emits a
    # look_at_scene tool call; _stream_local sets vision_switch["on"] and ends
    # WITHOUT a "done", and we hand the turn to Groq (transparently — the caller
    # just sees sentences + done, same as any other turn).
    if _USE_LOCAL and not use_fallback:
        vision_switch = {"on": False}
        try:
            yield from _stream_local(messages, verify_prefill=verify_prefill,
                                     abort_event=abort_event, vision_switch=vision_switch)
            if vision_switch["on"]:
                if not is_primary_turn:
                    # Speculative pre-gen: don't fire the camera/Groq. Tell the
                    # spec runner this was a vision turn so it isn't adopted; the
                    # real turn re-runs and routes to Groq.
                    yield ("vision_requested", None)
                    return
                _INSTANT_VISION_LAST["used"] = True
                try:
                    yield from _stream_instant_vision(messages, abort_event=abort_event)
                except _InstantVisionUnavailable as e:
                    print(f"[InstantVision] unavailable after look_at_scene ({e}).")
                    apology = "Hmm, I can't see clearly right now."
                    yield ("sentence", apology)
                    yield ("done", apology)
            return
        except _LocalUnavailable as e:
            print(f"\n[LLM] Local server unavailable ({e}).")
            if local_only:
                yield ("local_unavailable", str(e))
                return
            if _FALLBACK_MODEL:
                print(f"Falling back to cloud {_FALLBACK_MODEL}...\n")
                yield from stream_response(messages, use_fallback=True)
                return
            # No fallback configured — give up gracefully.
            yield ("done", "")
            return

    # --- FALLBACK PATH: cloud model via LiteLLM (Gemini) ---
    model_to_use = _FALLBACK_MODEL if use_fallback and _FALLBACK_MODEL else _MODEL

    print(f"\n--- Starting {model_to_use} Stream ---\n")

    try:
        response = _get_completion()(
            model=model_to_use,
            messages=messages,
            tools=TOOLS if (_SEND_TOOLS and TOOLS) else None,
            stream=True,
            temperature=_TEMPERATURE,
            vertex_credentials=vertex_credentials_json if "vertex_ai" in model_to_use else None,
            vertex_project=_VERTEX_PROJECT if "vertex_ai" in model_to_use else None,
            vertex_location=_VERTEX_LOCATION if "vertex_ai" in model_to_use else None,
        )
    except Exception as e:
        print(f"\n[LLM] Error starting {model_to_use}: {e}")
        if not use_fallback and _FALLBACK_MODEL:
            print(f"Falling back to {_FALLBACK_MODEL}...\n")
            yield from stream_response(messages, use_fallback=True)
            return
        else:
            yield ("done", "")
            return

    q = queue.Queue()
    t = threading.Thread(target=_fetch_chunks, args=(response, q), daemon=True)
    t.start()
    
    start_time = time.time()

    buffer = ""
    full_content = ""
    tool_calls = []

    while True:
        if not full_content and not use_fallback and _FALLBACK_MODEL:
            elapsed = time.time() - start_time
            remaining = _FALLBACK_TIMEOUT - elapsed
            if remaining <= 0:
                print(f"\n[LLM] {model_to_use} timed out waiting for content. Falling back...\n")
                yield from stream_response(messages, use_fallback=True)
                return
            
            try:
                msg_type, data = q.get(timeout=remaining)
            except queue.Empty:
                print(f"\n[LLM] {model_to_use} timed out waiting for content. Falling back...\n")
                yield from stream_response(messages, use_fallback=True)
                return
        else:
            msg_type, data = q.get()

        if msg_type == "done":
            break
        elif msg_type == "error":
            print(f"\n[LLM] Stream error: {data}")
            if not use_fallback and _FALLBACK_MODEL and not full_content:
                print(f"Falling back to {_FALLBACK_MODEL}...\n")
                yield from stream_response(messages, use_fallback=True)
                return
            else:
                break
                
        # msg_type == "chunk"
        chunk = data
        delta = chunk.choices[0].delta

        # 1. Stream the Model's "Thoughts" (Reasoning) - Exactly like gemini_test.py
        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
            thought_chunk = delta.reasoning_content
            print(f"\033[90m[Thought]: {thought_chunk}\033[0m", end="", flush=True)

        # 2. Stream the Content
        if delta.content:
            content_chunk = delta.content
            full_content += content_chunk
            print(content_chunk, end="", flush=True)

            # --- KikiFast sentence-level yielding ---
            buffer += content_chunk
            sentences, buffer = _extract_sentences(buffer)
            for s in sentences:
                s = s.strip()
                if s:
                    yield ("sentence", s)

        # 3. Collect Tool Calls
        if delta.tool_calls:
            for tc in delta.tool_calls:
                index = tc.index
                if len(tool_calls) <= index:
                    tool_calls.append(tc)
                else:
                    tool_calls[index].function.arguments += tc.function.arguments

    # Flush remaining buffer
    if buffer.strip():
        yield ("sentence", buffer.strip())

    print("\n") # newline after stream

    # 4. Handle Parallel Tool Calls
    if tool_calls:
        print("--- Processing Tool Calls ---")
        
        messages.append({
            "role": "assistant",
            "content": full_content,
            "tool_calls": tool_calls
        })

        for tool_call in tool_calls:
            function_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            
            print(f"Calling tool: {function_name} with {args}")
            result = execute_tool(function_name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": function_name,
                "content": result
            })

        # Recursive call to get the final summary (streaming)
        yield from stream_response(messages, use_fallback=use_fallback)
        return

    yield ("done", full_content)


if __name__ == "__main__":
    cfg = _LLM_CFG
    messages = [
        {"role": "system", "content": cfg["system_prompt"]},
        {"role": "user", "content": "What's the weather like in Tokyo?"}
    ]

    for evt, data in stream_response(messages):
        if evt == "sentence":
            print(f"\n[Sentence] → {data}")
        elif evt == "done":
            print(f"\n[Done] Full: {data}")
