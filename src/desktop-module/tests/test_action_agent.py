"""The complex_query action agent: routing, catalog, budgets and honesty.

The single most important property here is the LAST section: when the agent
does not finish, the string handed back to the speaking model must make a
false success impossible. Kiki saying "sure, I sent it" for a message that
never left is a worse outcome than any latency regression.
"""

import asyncio
import json
import threading
import time

import pytest

from core.brain import action_agent, fast_cloud


WHATSAPP_TOOLS = {
    "search_contacts", "list_messages", "list_chats", "get_chat",
    "get_direct_chat_by_contact", "get_contact_chats", "get_last_interaction",
    "get_message_context", "send_message", "send_file", "send_audio_message",
    "download_media",
}


# --- Catalog wiring --------------------------------------------------------

def test_whatsapp_left_the_speaking_catalog_but_kept_its_tools():
    """Context-bloat removal must not delete the tools agents still need."""
    from tools_and_config.tools import TOOLS, _ASYNC_TOOL_HANDLERS
    from tools_and_config.config_loader import get_full_config

    cfg = get_full_config()
    main_tools = set(cfg["llm"]["main_tools"])
    senior = set(cfg["assistant_modes"]["modes"]["senior"]["main_tools"])

    assert not (WHATSAPP_TOOLS & main_tools)
    assert not (WHATSAPP_TOOLS & senior)
    assert "complex_query" in main_tools and "complex_query" in senior

    names = {t["function"]["name"] for t in TOOLS}
    assert WHATSAPP_TOOLS <= names
    assert WHATSAPP_TOOLS <= set(_ASYNC_TOOL_HANDLERS)


def test_new_tools_are_registered_with_schemas_and_handlers():
    from tools_and_config.tools import _ASYNC_TOOL_HANDLERS, _TOOL_SCHEMAS_BY_NAME

    for name in ("complex_query", "read_whatsapp_image", "record_voice_note"):
        assert name in _TOOL_SCHEMAS_BY_NAME, f"{name} has no schema"
        assert name in _ASYNC_TOOL_HANDLERS, f"{name} has no handler"


def test_speaking_instruction_no_longer_lists_whatsapp_tools(monkeypatch):
    import core.llm as llm

    # Pin the mode: the instruction is now built from the ACTIVE mode's
    # main_tools, and roleplay modes deliberately carry a reduced catalog. This
    # asserts the default speaking catalog, not whatever mode happens to boot.
    monkeypatch.setattr("core.runtime_controls.get_active_mode", lambda: "default")

    instruction = llm._get_tools_instruction()
    assert "complex_query" in instruction
    for name in WHATSAPP_TOOLS:
        assert name not in instruction, f"{name} still bloats the warm prefix"


def test_roleplay_modes_get_their_own_reduced_catalog(monkeypatch):
    """A character must not be handed Kiki's memory tools."""
    import core.llm as llm

    monkeypatch.setattr("core.runtime_controls.get_active_mode", lambda: "rohan")

    names, _ = llm._effective_main_tools()

    assert "recall_memory" not in names
    assert "update_knowledge" not in names
    # ...but the user must always be able to talk their way back out.
    assert "switch_mode" in names


def test_agent_catalog_exposes_whatsapp_and_excludes_physical_tools():
    catalog = action_agent._catalog()
    for name in WHATSAPP_TOOLS | {"read_whatsapp_image", "record_voice_note"}:
        assert f"- {name}(" in catalog, f"agent cannot reach {name}"
    for name in ("dance", "move", "play_music", "follow_me", "look_at_scene"):
        assert f"- {name}(" not in catalog, f"{name} must not be agent-callable"


def test_agent_cannot_recurse_into_itself():
    assert "complex_query" in action_agent.BLOCKED_TOOLS
    assert action_agent._tool_executor("complex_query", {}).startswith("BLOCKED")
    assert action_agent._tool_executor("dance", {}).startswith("BLOCKED")


def test_idle_mind_cannot_seize_mic_and_only_delegates_care_to_agent():
    from core.brain import unified_idle_mind as uim

    assert "record_voice_note" in uim._IDLE_BLOCKED_TOOLS
    assert "complex_query" not in uim._IDLE_BLOCKED_TOOLS
    policy = uim._SessionPolicy(uim._default_state())
    allowed, reason = policy.guard(
        "complex_query", {"request": "summarize WhatsApp"},
        {"mode": "reflect"}, 0, ())
    assert not allowed and "care-plan" in reason
    policy = uim._SessionPolicy(uim._default_state())
    allowed, reason = policy.guard(
        "complex_query", {
            "request": "CARE_PLAN_DELEGATION: formulate a daily walking routine",
            "context": "Repeated walks at 18:00 on four days",
        }, {"mode": "reflect"}, 0, ())
    assert allowed, reason
    # Re-reading an image later is legitimate — it must stay duplicate-exempt.
    assert "read_whatsapp_image" in uim._FRESH_DATA_TOOLS


# --- Code-level routing ----------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "can you check the recent messages at burgito time and create a reminder if there are any events tomorrow",
    "research about the cockroach janta party protests and send it to my whatsapp",
    "send this file to my burrito time",
    "send what I am speaking right now as an audio recording to mom",
    "prepare a draft in my gmail about the internship application",
    "check my email and tell me if anything needs a reply",
    "reply to bharat on whatsapp saying I'll be late",
    "forward that message to the burgito group",
])
def test_action_requests_route_to_the_agent(utterance):
    assert action_agent is not None
    import core.llm as llm
    assert llm._should_route_complex_query(utterance), utterance


@pytest.mark.parametrize("utterance", [
    # These four were reported as silently not routing at all: the action regex
    # only had "act" verbs (send/draft/remind), so every READ phrasing missed —
    # and the WhatsApp read tools had just left the speaking catalog, leaving
    # nothing to answer with.
    "summarize my chat with burgito time",
    "tell me the chat summary for studio website",
    "what is the summary of my burgito chat",
    "catch me up on the burgito group",
    "what is happening in the ares team group",
    "who messaged me today",
    "did anyone text me",
    "any new messages",
    "recap my whatsapp chats",
    "what did namita say on whatsapp",
    "last few messages from the burgito group",
])
def test_read_and_summarize_phrasings_route_to_the_agent(utterance):
    import core.llm as llm
    assert llm._should_route_complex_query(utterance), utterance


@pytest.mark.parametrize("utterance", [
    # "summarize"/"what's happening" must NOT hijack ordinary conversation —
    # they only count alongside a surface only the agent can reach.
    "summarize the french revolution for me",
    "tell me about quantum physics",
    "what is happening with the stock market",
    "tell me a joke",
])
def test_summarize_without_a_messaging_surface_stays_local(utterance):
    import core.llm as llm
    assert not llm._should_route_complex_query(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "what's the weather like today",
    "play some music",
    "what did we discuss about discrete structures",
    "hey kiki how are you",
    "what time is it",
    "do you remember what I told you last week",
    "switch to funny mode",
    "turn the volume up",
    "does my shirt look good",
    "hi",
])
def test_ordinary_conversation_stays_on_the_fast_path(utterance):
    """A false positive here costs every normal turn ~6 seconds."""
    import core.llm as llm
    assert not llm._should_route_complex_query(utterance), utterance


def test_router_emits_a_wellformed_tool_event(monkeypatch):
    import core.llm as llm

    # Default-mode routing: roleplay modes drop complex_query on purpose.
    monkeypatch.setattr("core.runtime_controls.get_active_mode", lambda: "default")

    request = "send this file to my burrito time"
    routed = llm._auto_complex_query_tool_event(
        [{"role": "user", "content": request}],
        verify_prefill=True, use_fallback=False)
    assert routed is not None
    raw, event = routed
    assert "<tool_call>" in raw and "complex_query" in raw
    call = event["calls"][0]
    assert call["name"] == "complex_query"
    # The request must reach the agent verbatim — the agent needs the exact
    # wording to recover a misheard chat name.
    assert json.loads(call["arguments"])["request"] == request


def test_router_is_disabled_on_followups_and_fallback_turns():
    import core.llm as llm

    messages = [{"role": "user", "content": "send this to my burgito group"}]
    assert llm._auto_complex_query_tool_event(
        messages, verify_prefill=False, use_fallback=False) is None
    assert llm._auto_complex_query_tool_event(
        messages, verify_prefill=True, use_fallback=True) is None


# --- Provider layer --------------------------------------------------------

def test_first_json_object_survives_the_gpt_oss_failure_mode():
    """gpt-oss-120b emits its call, then FABRICATES the result and continues.

    Measured live. Truncation is what makes the loop immune to it.
    """
    hallucinated = (
        '{"tool_calls":[{"tool":"list_chats","args":{"query":"Burgito"}}]}'
        '{"role":"tool","content":"{\\"chats\\":[{\\"jid\\":\\"fake@g.us\\"}]}"}'
        '{"status":"completed","summary":"Sent a reminder to the group."}'
    )
    kept = json.loads(fast_cloud.first_json_object(hallucinated))
    assert kept == {"tool_calls": [{"tool": "list_chats",
                                    "args": {"query": "Burgito"}}]}


def test_first_json_object_handles_fences_and_braces_in_strings():
    fenced = '```json\n{"status":"completed","summary":"done"}\n```'
    assert json.loads(fast_cloud.first_json_object(fenced))["status"] == "completed"
    tricky = '{"summary":"a } { b"} trailing prose'
    assert json.loads(fast_cloud.first_json_object(tricky))["summary"] == "a } { b"


def test_cerebras_gets_full_context_and_groq_gets_compacted():
    """The provider asymmetry is the whole point of the two-provider design."""
    cfg = fast_cloud._cfg()
    assert int(cfg["cerebras"]["max_prompt_chars"]) == 0, "Cerebras must be uncapped"
    groq_cap = int(cfg["groq"]["max_prompt_chars"])
    assert 0 < groq_cap <= 12000, "Groq must be capped for its 8K/min budget"

    long_prompt = "HEAD-RULES-AND-CATALOG " * 2000
    assert fast_cloud._trim_prompt(long_prompt, 0) == long_prompt
    trimmed = fast_cloud._trim_prompt(long_prompt, groq_cap)
    assert len(trimmed) <= groq_cap + 120
    # Head and tail must survive: the head holds the task and tool catalog,
    # the tail holds the newest tool results.
    assert trimmed.startswith("HEAD-RULES-AND-CATALOG")
    assert trimmed.rstrip().endswith("HEAD-RULES-AND-CATALOG")
    assert "compressed" in trimmed


def test_agent_deadline_is_below_the_main_py_tool_timeout():
    """If main.py gave up first, the agent could never report partial progress."""
    from tools_and_config.config_loader import get_full_config

    cfg = get_full_config()
    deadline = float(cfg["action_agent"]["deadline_seconds"])
    override = float(cfg["llm"]["tool_calling"]["exec_timeout_overrides"]["complex_query"])
    assert deadline < override, f"agent deadline {deadline}s >= main.py {override}s"

    from tools_and_config.tools import _EXEC_TIMEOUT_BY_TOOL
    assert _EXEC_TIMEOUT_BY_TOOL["complex_query"] >= deadline


# --- Loop behaviour with a scripted model ----------------------------------

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _script(monkeypatch, responses, tool_results=None):
    """Drive the agent with a fixed sequence of model replies."""
    calls = []
    replies = list(responses)

    def fake_complete(prompt, provider=None, stop_event=None):
        return replies.pop(0) if replies else '{"status":"completed","summary":"done"}'

    def fake_execute(name, args):
        calls.append((name, args))
        return (tool_results or {}).get(name, "OK")

    monkeypatch.setattr(fast_cloud, "complete", fake_complete)
    monkeypatch.setattr(action_agent, "_tool_executor", fake_execute)
    return calls


def test_successful_run_returns_a_speakable_summary(monkeypatch):
    calls = _script(monkeypatch, [
        '{"tool_calls":[{"tool":"list_chats","args":{"query":"burgito"}}]}',
        '{"status":"completed","summary":"I checked Burgito and set a reminder for 7 pm tomorrow."}',
    ])
    out = run(action_agent.run_complex_query("check burgito and remind me"))
    assert out == "I checked Burgito and set a reminder for 7 pm tomorrow."
    assert calls == [("list_chats", {"query": "burgito"})]
    assert "ACTION" not in out


def test_summary_is_capped_so_it_stays_speakable(monkeypatch):
    _script(monkeypatch, [
        '{"status":"completed","summary":"' + "x" * 5000 + '"}',
    ])
    out = run(action_agent.run_complex_query("do a thing"))
    limit = action_agent._cfg()["summary_max_chars"]
    assert len(out) <= limit + 1


# --- Honesty on failure (the property that matters most) -------------------

def test_failure_after_a_real_attempt_surfaces_the_reason(monkeypatch):
    _script(monkeypatch, [
        '{"tool_calls":[{"tool":"list_chats","args":{"query":"burgito"}}]}',
        '{"status":"failed","reason":"no chat matched burgito"}',
    ])
    out = run(action_agent.run_complex_query("send it to burgito"))
    assert out.startswith("ACTION FAILED")
    assert "do not claim it succeeded" in out
    assert "no chat matched burgito" in out


def test_giving_up_before_trying_any_tool_forces_a_real_attempt(monkeypatch):
    """min_tool_calls also blocks premature failure, not just fabricated success."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=3)
    replies = [
        '{"status":"failed","reason":"I do not think I can do this"}',
        '{"tool_calls":[{"tool":"list_chats","args":{"query":"burgito"}}]}',
        '{"status":"completed","summary":"I found the burgito group and sent it."}',
    ]
    used = []
    monkeypatch.setattr(fast_cloud, "complete",
                        lambda p, provider=None, stop_event=None: replies.pop(0))
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a: used.append(n) or "OK")
    out = run(action_agent.run_complex_query("send it to burgito"))
    assert used == ["list_chats"], "gave up without attempting anything"
    assert out == "I found the burgito group and sent it."


def test_dead_providers_do_not_produce_a_fake_success(monkeypatch):
    def boom(prompt, provider=None, stop_event=None):
        raise fast_cloud.FastCloudUnavailable("cerebras: down; groq: down")

    monkeypatch.setattr(fast_cloud, "complete", boom)
    out = run(action_agent.run_complex_query("send a message to mom"))
    assert out.startswith("ACTION FAILED")
    assert "do not claim it succeeded" in out


def _override_cfg(monkeypatch, **overrides):
    """Patch the merged config, not _DEFAULTS — config.json wins over defaults."""
    base = dict(action_agent._cfg())
    base.update(overrides)
    monkeypatch.setattr(action_agent, "_cfg", lambda: base)
    return base


def test_deadline_returns_partial_progress_not_a_claim_of_success(monkeypatch):
    """A timeout mid-action must never read as 'done'."""
    _override_cfg(monkeypatch, deadline_seconds=0.4)

    def slow(prompt, provider=None, stop_event=None):
        time.sleep(0.6)
        return '{"tool_calls":[{"tool":"send_message","args":{"recipient":"a","message":"b"}}]}'

    sent = []
    monkeypatch.setattr(fast_cloud, "complete", slow)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a: sent.append(n) or "OK")

    out = run(action_agent.run_complex_query("send a long thing"))
    assert out.startswith("ACTION INCOMPLETE")
    assert "do not claim it succeeded" in out


def test_empty_request_is_rejected_without_calling_the_cloud(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not call the model for an empty request")

    monkeypatch.setattr(fast_cloud, "complete", boom)
    assert "No request" in run(action_agent.run_complex_query("   "))


def test_batched_dependent_call_is_refused_not_executed_with_garbage(monkeypatch):
    """Observed live on the Groq fallback: the model issued
    list_messages(chat_jid="<PLACEHOLDER_JID_FROM_FIRST_CALL>") in the SAME turn
    as the list_chats meant to supply that jid. Executing it returned nothing,
    and the model reported "there are no new messages" as fact."""
    for bad in ("<PLACEHOLDER_JID_FROM_FIRST_CALL>", "<jid>", "your_chat_id",
                "chat_jid_here", "TBD"):
        result = action_agent._tool_executor("list_messages", {"chat_jid": bad})
        assert result.startswith("NOT EXECUTED"), f"{bad!r} was executed"
        assert "ONE AT A TIME" in result

    # A real jid must still go through.
    assert action_agent._placeholder_arg(
        {"chat_jid": "120363000000000001@g.us"}) is None


def test_completing_with_zero_tool_calls_is_rejected(monkeypatch):
    """A complex_query that touches no tool has invented its answer.

    Observed live: the Groq model described a WhatsApp conversation about taco
    night, memes and guacamole that did not exist.
    """
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=2)
    replies = [
        '{"status":"completed","summary":"They were planning a taco night with guacamole."}',
        '{"tool_calls":[{"tool":"list_chats","args":{"query":"burgito"}}]}',
    ]
    used = []

    def fake(prompt, provider=None, stop_event=None):
        return replies.pop(0) if replies else '{"status":"completed","summary":"I read the chat and it was about lunch."}'

    monkeypatch.setattr(fast_cloud, "complete", fake)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a: used.append(n) or "OK")
    run(action_agent.run_complex_query("what are the burgito messages about"))
    assert used, "the fabricated completion was accepted without any tool call"


@pytest.mark.parametrize("summary,meaningful", [
    ("...", False), ("", False), ("done", False), ("ok", False), ("— — —", False),
    ("Sent it.", True),
    ("I checked the burgito group and set a reminder.", True),
    ("मैंने संदेश भेज दिया", True),
])
def test_degenerate_summaries_are_detected(summary, meaningful):
    assert action_agent._is_meaningful(summary) is meaningful


def test_degenerate_summary_is_not_reported_as_success(monkeypatch):
    _script(monkeypatch, [
        '{"tool_calls":[{"tool":"list_chats","args":{"query":"x"}}]}',
        '{"status":"completed","summary":"..."}',
    ])
    out = run(action_agent.run_complex_query("check my messages"))
    assert out.startswith("ACTION FAILED")
    assert "did not report what happened" in out


def test_optional_params_are_dropped_on_a_400(monkeypatch):
    """qwen3.6-27b hard-400s on reasoning_effort=low; gpt-oss requires it.

    Without this retry every qwen call became an "empty response".
    """
    sent = []

    class Resp:
        def __init__(self, code, text=""):
            self.status_code, self.text = code, text

        def json(self):
            return {"choices": [{"message": {"content": '{"status":"completed"}'}}],
                    "usage": {}}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        if "reasoning_effort" in json:
            return Resp(400, "`reasoning_effort` must be one of `none` or `default`")
        return Resp(200)

    monkeypatch.setattr(fast_cloud.SESSION, "post", fake_post)
    out = fast_cloud._post("u", "k", {"model": "m", "reasoning_effort": "low"}, 5)
    assert out["choices"][0]["message"]["content"] == '{"status":"completed"}'
    assert len(sent) == 2 and "reasoning_effort" not in sent[1]


def test_a_429_rotates_instead_of_retrying_lean(monkeypatch):
    """Only a 400 is a parameter problem — a 429 must propagate so the caller
    rotates keys rather than burning a second call on an exhausted one."""
    calls = []

    class Resp:
        status_code, text = 429, "rate limited"

        def json(self):
            return {}

    monkeypatch.setattr(fast_cloud.SESSION, "post",
                        lambda *a, **k: calls.append(1) or Resp())
    with pytest.raises(RuntimeError, match="429"):
        fast_cloud._post("u", "k", {"model": "m", "reasoning_effort": "low"}, 5)
    assert len(calls) == 1, "a 429 must not trigger the lean retry"


def test_tool_call_budget_is_enforced(monkeypatch):
    """Stops a runaway loop from blowing the latency target and the token budget."""
    _override_cfg(monkeypatch, max_tool_calls=3, max_turns=8)

    counter = {"n": 0}

    def greedy(prompt, provider=None, stop_event=None):
        counter["n"] += 1
        return json.dumps({"tool_calls": [
            {"tool": "search_web", "args": {"query": f"q{counter['n']}"}}]})

    calls = []
    monkeypatch.setattr(fast_cloud, "complete", greedy)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a: calls.append(a) or "OK")

    run(action_agent.run_complex_query("keep searching forever"))
    assert len(calls) <= 3, f"budget breached: {len(calls)} calls"


# --- Inline image summarisation bounds ------------------------------------
# These guard the bug that cost the most: an image-heavy chat turned a summary
# into a 106-second hang. Three separate causes, each fixed here.

def test_image_pass_slices_before_doing_per_image_work(monkeypatch):
    """The cap must apply BEFORE any per-image work.

    Originally the code checked every image row (disk lookup, then vision) and
    only sliced afterwards, so an active group with dozens of pictures did
    unbounded work outside the timeout.
    """
    import tools_and_config.tools as T

    looked_at = []
    monkeypatch.setattr(T, "_whatsapp_image_config", lambda: (True, 3, 5.0))
    monkeypatch.setattr(T, "_local_media_path",
                        lambda mid, jid: looked_at.append(mid) or None)

    rows = [{"id": f"m{i}", "chat_jid": "g@g.us", "media_type": "image",
             "content": "", "timestamp": f"2026-07-26T10:{i:02d}:00"}
            for i in range(30)]
    asyncio.new_event_loop().run_until_complete(T._describe_images_inline(rows))
    assert len(looked_at) <= 3, f"did per-image work on {len(looked_at)} images"


def test_image_pass_is_bounded_and_degrades_to_no_images(monkeypatch):
    """On timeout the chat is still summarised, just without the pictures."""
    import tools_and_config.tools as T

    monkeypatch.setattr(T, "_whatsapp_image_config", lambda: (True, 3, 0.3))
    monkeypatch.setattr(T, "_local_media_path", lambda mid, jid: "/tmp/x.jpg")
    monkeypatch.setattr(
        "core.vision.instant_vision.describe_image_file",
        lambda *a, **k: time.sleep(5) or "never gets here")

    rows = [{"id": "m1", "chat_jid": "g@g.us", "media_type": "image",
             "content": "", "timestamp": "2026-07-26T10:00:00"},
            {"id": "t1", "chat_jid": "g@g.us", "media_type": "",
             "content": "a real message", "timestamp": "2026-07-26T10:01:00"}]

    started = time.perf_counter()
    out = asyncio.new_event_loop().run_until_complete(
        T._describe_images_inline(rows))
    assert time.perf_counter() - started < 3.0, "image pass was not bounded"
    assert out[1]["content"] == "a real message", "text messages must survive"
    assert "not viewed" in out[0]["content"]


def test_image_work_never_runs_on_the_shared_executor():
    """Stragglers on the default executor queued the agent's NEXT tool call
    behind them — a 5s image cap still produced a 108s turn."""
    import tools_and_config.tools as T

    assert T._IMAGE_POOL is not None
    assert "wa-image" in T._IMAGE_POOL._thread_name_prefix


def test_cold_media_is_not_fetched_inline(monkeypatch):
    """Cold downloads serialize behind the single MCP session lock, so they are
    deliberately never fetched during a summary."""
    import tools_and_config.tools as T

    monkeypatch.setattr(T, "_whatsapp_image_config", lambda: (True, 3, 5.0))
    monkeypatch.setattr(T, "_local_media_path", lambda mid, jid: None)

    def no_download(*a, **k):
        raise AssertionError("cold media must not be downloaded inline")

    monkeypatch.setattr(T, "download_media", no_download)
    rows = [{"id": "m1", "chat_jid": "g@g.us", "media_type": "image",
             "content": "", "timestamp": "2026-07-26T10:00:00"}]
    out = asyncio.new_event_loop().run_until_complete(
        T._describe_images_inline(rows))
    assert "read_whatsapp_image" in out[0]["content"] or \
        "still downloading" in out[0]["content"]


# --- message rows must be readable, and cheap ------------------------------
# Live failure, 2026-07-26: a whole-chat summary came back 308 characters long.
# The agent had asked for 100 messages and received 1500 characters of them —
# five rows — because each MCP row spends ~300 chars repeating chat_jid, sender
# and a 32-char id.

def test_message_rows_are_compacted_for_the_reader():
    from tools_and_config.tools import _compact_message_rows
    fat = [{"timestamp": "2026-07-23T20:46:45+05:30", "sender": "20000000000041",
            "content": "Banana le aana", "is_from_me": 0,
            "chat_jid": "20000000000041@lid", "id": "AC29D716EC8EAC506401931CCBAD9D6A",
            "chat_name": "Namita", "media_type": "", "sender_name": "Namita"}]
    slim = _compact_message_rows(fat)[0]
    assert slim == {"time": "07-23 20:46", "from": "Namita", "text": "Banana le aana"}
    assert len(json.dumps(slim)) < len(json.dumps(fat[0])) / 3


def test_media_rows_keep_what_read_whatsapp_image_needs():
    """id and chat_jid are dead weight on text rows but required on media."""
    from tools_and_config.tools import _compact_message_rows
    slim = _compact_message_rows([{
        "timestamp": "2026-07-20T16:59:00+05:30", "content": "[image - ...]",
        "chat_jid": "20000000000041@lid", "id": "ACDE94", "media_type": "image",
        "sender_name": "Namita"}])[0]
    assert slim["id"] == "ACDE94" and slim["chat_jid"] == "20000000000041@lid"
    assert slim["media"] == "image"


def test_a_numeric_chat_name_on_a_message_row_gets_a_real_name(monkeypatch):
    """Every row read "20000000000041", so the agent assumed it had the wrong
    chat and burned an entire extra turn re-fetching identical messages.

    `chat_name` is the message-row spelling of the field; only the chat-row
    spelling (`name`) was ever labelled.
    """
    from core.self_extend import whatsapp_contacts, whatsapp_mcp as wm
    monkeypatch.setattr(whatsapp_contacts, "display_name",
                        lambda ident: "Namita" if "2000000000004" in ident else None)
    labelled = wm._label_people([{
        "chat_jid": "20000000000041@lid", "chat_name": "20000000000041",
        "sender": "20000000000041", "content": "hi"}])[0]
    assert labelled["chat_name"] == "Namita"
    assert labelled["number"] == "20000000000041"   # identifier never lost


def test_a_group_chat_name_is_never_overwritten(monkeypatch):
    """Groups have real names already; the number lookup must not touch them."""
    from core.self_extend import whatsapp_contacts, whatsapp_mcp as wm
    monkeypatch.setattr(whatsapp_contacts, "display_name", lambda ident: "Wrong")
    row = wm._label_people([{"chat_jid": "120363000000000001@g.us",
                             "chat_name": "burgito time", "content": "hi"}])[0]
    assert row["chat_name"] == "burgito time"


# --- the agent must know what "him" and "it" refer to ----------------------
# The code router builds its tool call from the user's WORDS ONLY, so before
# this the agent received "summarize my chat with him" with no referent at all.

def test_snapshot_renders_turns_and_drops_only_the_persona():
    from core import llm
    llm._note_conversation([
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": "studio messaged me"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "The Matax one?"},
    ])
    snap = llm.conversation_snapshot()
    assert "SYSTEM PROMPT" not in snap      # index 0 is the persona
    assert snap == "Vaibhav: studio messaged me\nKiki: The Matax one?"


def test_snapshot_trims_from_the_front():
    """The newest turns are what a pronoun points at, so they must survive."""
    from core import llm
    llm._note_conversation([{"role": "system", "content": "PERSONA"},
                            {"role": "user", "content": "x" * 300},
                            {"role": "user", "content": "send it to studio"}])
    snap = llm.conversation_snapshot(max_chars=60)
    assert "send it to studio" in snap and len(snap) <= 60


def test_persona_excerpt_stops_on_a_sentence():
    from core import llm
    brief = llm.persona_brief(300)
    assert 0 < len(brief) <= 300
    assert brief.endswith(".") or len(brief) == 300


def test_context_never_enters_the_tool_call_arguments():
    """§4 cache contract: a tool call's arguments become assistant text that
    register_history writes into the warm speaking prefix. Putting the
    conversation there would re-prefill the box on every routed turn."""
    from core import llm
    llm._note_conversation([{"role": "user", "content": "studio messaged me"}])
    event = llm._auto_complex_query_tool_event(
        [{"role": "user", "content": "summarize my chat with him and reply"}],
        verify_prefill=True, use_fallback=False)
    if event is None:
        pytest.skip("complex_query not in the active main_tools config")
    raw, payload = event
    assert set(json.loads(payload["calls"][0]["arguments"])) == {"request"}
    assert "studio messaged me" not in raw


def test_background_carries_persona_and_history_into_the_prompt():
    from core import llm
    from core.brain import action_agent
    llm._note_conversation([{"role": "user", "content": "studio sent the spreadsheet"}])
    prompt = action_agent._prompt("summarize my chat with him")
    assert "Vaibhav: studio sent the spreadsheet" in prompt
    assert "Kiki" in prompt and "CONVERSATION SO FAR" in prompt


def test_a_missing_snapshot_does_not_break_the_agent():
    from core import llm
    from core.brain import action_agent
    llm._note_conversation([])
    prompt = action_agent._prompt("send a message to namita")
    assert "CONVERSATION SO FAR (most recent last):" not in prompt
    assert "REQUEST: send a message to namita" in prompt


# --- The stuck-nudge deadlock ----------------------------------------------
# Live failure 2026-08-28 23:35:35: "Did I drink water recently?" routed to the
# care agent, which already had the answer from an injected care event and so
# called no tool. `min_tool_calls` rejected the completion six times with one
# FIXED sentence that recommended `search_web` — a tool irrelevant to the task —
# so the model re-emitted identical JSON until the turn budget died. Kiki then
# spoke "That care action did not complete", discarding an answer it had.

def test_the_forced_tool_nudge_names_tools_the_task_actually_has(monkeypatch):
    """A nudge recommending search_web to a care agent is unusable advice."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=3)
    prompts = []

    def fake(prompt, provider=None, stop_event=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"status":"completed","summary":"Yes, I just saw you drink."}'
        return '{"tool_calls":[{"tool":"get_care_plan","args":{"section":"care_log"}}]}'

    monkeypatch.setattr(fast_cloud, "complete", fake)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a, care_mode=False: "[]")
    run(action_agent.run_complex_query("Did I drink water recently?"))

    nudge = prompts[1]
    assert "get_care_plan" in nudge, "the nudge never names a care tool"
    assert "search_web" not in nudge.rsplit("SYSTEM ERROR:", 1)[-1], \
        "a care agent was told to use search_web"


def test_a_repeated_answer_escalates_instead_of_looping(monkeypatch):
    """Appending the same nudge to the same answer never converges."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=4)
    prompts = []

    def stubborn(prompt, provider=None, stop_event=None):
        prompts.append(prompt)
        return '{"status":"completed","summary":"Yes, I just saw you drink."}'

    monkeypatch.setattr(fast_cloud, "complete", stubborn)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a, care_mode=False: "[]")
    run(action_agent.run_complex_query("Did I drink water recently?"))

    assert "SAME answer again" in prompts[2], \
        "a verbatim repeat was met with the identical nudge"


def test_a_care_question_keeps_its_answer_instead_of_a_canned_failure(monkeypatch):
    """Exhausting the turns must not replace a real answer with 'did not complete'."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=3)

    def stubborn(prompt, provider=None, stop_event=None):
        return '{"status":"completed","summary":"Yes, you drank water a moment ago."}'

    monkeypatch.setattr(fast_cloud, "complete", stubborn)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a, care_mode=False: "[]")
    out = run(action_agent.run_complex_query("Did I drink water recently?"))

    assert "CARE_ACTION_FAILED" not in out, out
    assert "did not complete" not in out, out
    assert "drank water" in out


# --- Live failure 2026-09-01 00:11:59 ---------------------------------------
# "respond back to Suyash about the work he said and also mention that this is
# Kiki and what all healthcare features you already have now?"
#
# The request reached the agent as "... list the healthcare features I
# currently have (AQI monitoring, heart rate monitoring, ...)". `heart rate`
# matched _CARE_STRONG_RE, so a WhatsApp send became a care action on the
# direct-to-TTS path. The agent then did exactly the right thing -- it found
# two contacts called Suyash and asked which one -- and that question was
# thrown away for "That care action did not complete."

def test_a_message_that_merely_mentions_a_care_topic_is_not_a_care_action():
    """The exact request from the log."""
    assert action_agent.is_care_request(
        "respond to Suyash about his work on the parametric wristwatch model, "
        "introduce myself as Kiki, and list the healthcare features I "
        "currently have (AQI monitoring, heart rate monitoring, and "
        "environmental data processing)") is False


@pytest.mark.parametrize("request_text", [
    "reply to Namita about my medicine",
    "send Yash a message about the walking routine",
    "email the doctor about my appointment",
    "forward that to Suyash about the heart rate sensor",
    "whatsapp my brother about dinner",
])
def test_outbound_messages_about_care_topics_are_messages(request_text):
    assert action_agent.is_care_request(request_text) is False


@pytest.mark.parametrize("request_text", [
    "schedule my medicine for 8pm and message my daughter",
    "add a hydration check-in and text Namita about it",
])
def test_a_care_write_that_also_sends_a_message_is_still_care(request_text):
    """Position decides: the care noun comes first, so that is the request."""
    assert action_agent.is_care_request(request_text) is True


@pytest.mark.parametrize("request_text", [
    "measure my heart rate",
    "remind me to take my medicine at 9",
    "add a walking routine to my day",
    "send me my heart rate trend",
])
def test_ordinary_care_requests_are_untouched(request_text):
    assert action_agent.is_care_request(request_text) is True


def test_an_explicit_delegation_still_wins():
    """CARE_PLAN_DELEGATION is set by the care path itself, not inferred."""
    assert action_agent.is_care_request(
        "send Namita the schedule", "CARE_PLAN_DELEGATION") is True


def test_a_care_turn_that_asks_a_question_speaks_the_question(monkeypatch):
    """The agent knew exactly what it needed. Saying "that did not complete"
    instead loses the only thing that could unblock the turn."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=3)
    turns = []

    def asks(prompt, provider=None, stop_event=None):
        turns.append(prompt)
        if len(turns) == 1:
            return ('{"tool_calls":[{"tool":"send_message","args":'
                    '{"recipient":"Suyash","message":"hi"}}]}')
        return ('{"status":"incomplete","summary":"There are two people called '
                'Suyash in your contacts, Saxena and Srivastava. Which one '
                'should I message?"}')

    monkeypatch.setattr(fast_cloud, "complete", asks)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a, care_mode=False: '{"error":"ambiguous"}')
    out = run(action_agent.run_complex_query("measure my heart rate"))

    assert "Which one should I message?" in out
    assert "did not complete" not in out


def test_a_vague_care_failure_still_gets_the_deterministic_line(monkeypatch):
    """No question, nothing to act on -- the canned wording is right here, and
    it is what stops a failed write being spoken as a success."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=2)

    def vague(prompt, provider=None, stop_event=None):
        return '{"status":"incomplete","summary":"..."}'

    monkeypatch.setattr(fast_cloud, "complete", vague)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a, care_mode=False: "OK")
    out = run(action_agent.run_complex_query("measure my heart rate"))

    assert "did not complete" in out


def test_a_question_that_claims_success_is_not_a_get_out(monkeypatch):
    """A question mark is the licence, but the summary still has to say
    something -- "?" alone must not smuggle an empty turn through."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=2)

    def barely(prompt, provider=None, stop_event=None):
        return '{"status":"incomplete","summary":"?"}'

    monkeypatch.setattr(fast_cloud, "complete", barely)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a, care_mode=False: "OK")
    out = run(action_agent.run_complex_query("measure my heart rate"))

    assert "did not complete" in out


def test_an_unverified_care_WRITE_still_fails_loudly(monkeypatch):
    """A schedule nobody created must never be spoken as done."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=3)

    def liar(prompt, provider=None, stop_event=None):
        return '{"status":"completed","summary":"Done! Medicine scheduled for 8am."}'

    monkeypatch.setattr(fast_cloud, "complete", liar)
    monkeypatch.setattr(action_agent, "_tool_executor",
                        lambda n, a, care_mode=False: "OK")
    out = run(action_agent.run_complex_query("Schedule my medicine at 8am"))

    assert out.startswith("CARE_ACTION_FAILED"), out
    assert "8am" not in out, "an unperformed schedule leaked into the reply"


def test_non_care_fabrication_is_still_refused(monkeypatch):
    """The escape hatch is care-question-only; the taco-night guard stands."""
    _override_cfg(monkeypatch, min_tool_calls=1, max_turns=3)

    def liar(prompt, provider=None, stop_event=None):
        return '{"status":"completed","summary":"They planned a taco night."}'

    monkeypatch.setattr(fast_cloud, "complete", liar)
    monkeypatch.setattr(action_agent, "_tool_executor", lambda n, a: "OK")
    out = run(action_agent.run_complex_query("what did they say about lunch?"))

    assert out.startswith("ACTION"), out
    assert "taco" not in out.split("Progress so far")[0]
