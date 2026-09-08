"""Starting a routine on request, and not re-asking a deferred one every tick."""

import json
from pathlib import Path

import pytest

from core.senior import care_plan as care_plan_module
from core.senior import senior_care_manager as scm


def _plan_file(tmp_path, events):
    path = tmp_path / "care_plan.json"
    path.write_text(json.dumps({
        "senior": {"name": "Vaibhav", "language": "hi"},
        "family_contacts": [], "reminders": [], "exercises": [],
        "approved_music": [], "approved_topics": [], "care_log": [],
        "metadata": {}, "health_measurements": [],
        "routine_events": events,
        "active_session": None,
    }))
    return path


def _event(eid, title, objective="", enabled=True):
    return {
        "id": eid, "title": title, "objective": objective or title,
        "category": "exercise", "schedule": {"kind": "daily", "value": "18:40"},
        "actions": [], "continuous_vision": True, "enabled": enabled,
        "source": "user", "adaptation": {},
    }


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A care plan with two routines and a captured foreground hook."""
    path = _plan_file(tmp_path, [
        _event("neck0001", "Neck Exercise"),
        _event("waist001", "Waist Exercise"),
    ])
    store = care_plan_module.CarePlan(Path(str(path)))
    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store",
                        lambda: store)
    queued = []
    scm.set_foreground_hook(queued.append)
    yield store, queued
    scm.set_foreground_hook(None)


def test_starting_by_name_opens_the_session_and_queues_the_voice_turn(wired):
    store, queued = wired

    result = scm.start_care_session_now("neck")

    assert json.loads(result)["status"] == "care_session_starting"
    assert store.care_session_state()["event_id"] == "neck0001"
    # The tool never speaks; the foreground lifecycle owns the turn.
    assert queued == ["neck0001"]


def test_starting_by_id_works_too(wired):
    store, _ = wired
    scm.start_care_session_now("waist001")
    assert store.care_session_state()["event_id"] == "waist001"


def test_an_ambiguous_name_asks_instead_of_guessing(wired):
    store, queued = wired

    result = scm.start_care_session_now("exercise")     # matches both

    assert "Which one" in result
    assert queued == []
    assert store.care_session_state()["status"] == "none"


def test_an_unknown_routine_lists_what_exists(wired):
    store, queued = wired

    result = scm.start_care_session_now("swimming")

    assert result.startswith("CARE_ACTION_FAILED")
    assert "Neck Exercise" in result and "Waist Exercise" in result
    assert queued == []


def test_no_argument_is_unambiguous_with_a_single_routine(tmp_path, monkeypatch):
    path = _plan_file(tmp_path, [_event("only0001", "Neck Exercise")])
    store = care_plan_module.CarePlan(Path(str(path)))
    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store",
                        lambda: store)
    queued = []
    scm.set_foreground_hook(queued.append)
    try:
        scm.start_care_session_now("")
        assert queued == ["only0001"]
    finally:
        scm.set_foreground_hook(None)


def test_no_argument_asks_when_several_routines_exist(wired):
    _, queued = wired
    result = scm.start_care_session_now("")
    assert "Which routine" in result
    assert queued == []


def test_an_unwired_foreground_route_does_not_strand_an_open_session(wired):
    """A session nothing can voice must be rolled back, not left hanging."""
    store, _ = wired
    scm.set_foreground_hook(None)

    result = scm.start_care_session_now("neck")

    assert result.startswith("CARE_ACTION_FAILED")
    assert store.care_session_state()["status"] != "active"


def test_disabled_routines_are_not_startable(tmp_path, monkeypatch):
    path = _plan_file(tmp_path, [_event("off00001", "Neck Exercise",
                                        enabled=False)])
    store = care_plan_module.CarePlan(Path(str(path)))
    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store",
                        lambda: store)
    result = scm.start_care_session_now("neck")
    assert result.startswith("CARE_ACTION_FAILED")


def test_the_tool_is_registered_and_offered_in_senior_mode():
    from tools_and_config.tools import TOOLS, _ASYNC_TOOL_HANDLERS
    from tools_and_config.config_loader import get_full_config

    assert "start_care_session" in _ASYNC_TOOL_HANDLERS
    schema = next(t for t in TOOLS
                  if t["function"]["name"] == "start_care_session")
    # The description has to steer away from update_care_plan, which only
    # schedules for later — that confusion is the whole reason for this tool.
    assert "update_care_plan" in schema["function"]["description"]

    senior = get_full_config()["assistant_modes"]["modes"]["senior"]
    assert "start_care_session" in senior["main_tools"]


# --------------------------------------------------------------------------
# Deferral backoff
# --------------------------------------------------------------------------

def test_a_deferred_worker_is_not_retried_on_the_very_next_tick():
    """It re-ran and logged every 5 s for as long as a session was open."""
    import threading
    from core.workers.worker_manager import WorkerManager
    from core.workers.worker_engine import (
        Worker, WorkerTrigger, TriggerType, WorkerStatus)

    mgr = WorkerManager.__new__(WorkerManager)
    mgr._deferred_until = {}
    mgr._defer_retry_seconds = 30
    mgr._lock = threading.Lock()

    w = Worker(name="senior:routine_event:busy", task_description="x",
               trigger=WorkerTrigger(
                   trigger_type=TriggerType.SCHEDULED_TIME.value,
                   scheduled_time="2026-08-29T19:45:00"))
    w.status = WorkerStatus.PENDING.value

    assert mgr._is_deferred(w) is False          # nothing recorded yet
    mgr._deferred_until[w.id] = __import__("time").monotonic() + 30
    assert mgr._is_deferred(w) is True           # inside the backoff

    # Once the window passes it becomes eligible again, and the entry is
    # cleaned up rather than growing forever.
    mgr._deferred_until[w.id] = __import__("time").monotonic() - 0.01
    assert mgr._is_deferred(w) is False
    assert w.id not in mgr._deferred_until


# --------------------------------------------------------------------------
# Machine output must never reach the speaker
# --------------------------------------------------------------------------

@pytest.mark.parametrize("blob", [
    '{"tool_calls":[{"tool":"update_care_plan","args":{"action":"start_session"}}]}',
    '[{"tool":"get_care_plan"}]',
    '{"status":"completed","summary":"done"}',
    '<tool_call>start_care_session</tool_call>',
    'Sure! {"tool_calls":[{"tool":"x"}]}',
    '',
    '   ',
])
def test_agent_protocol_is_never_spoken(blob):
    """Kiki read a JSON object aloud for thirty seconds on 2026-08-29 20:04."""
    from main import is_speakable_reply
    assert is_speakable_reply(blob) is False


@pytest.mark.parametrize("line", [
    "चलिए शुरू करते हैं। अपनी गर्दन धीरे-धीरे दाईं ओर झुकाइए।",
    "Alright, let's begin. Tilt your head slowly to the right.",
    "I added the neck exercise for 6:40 PM.",
    # Expression tags legitimately open a reply — rejecting every leading
    # bracket silenced real care answers.
    "[gentle] उंगली को सेंसर से हटा दीजिए।",
    "[cheerful] Great work! Now the other side.",
])
def test_real_speech_is_still_spoken(line):
    from main import is_speakable_reply
    assert is_speakable_reply(line) is True


def test_a_raw_agent_turn_falls_back_instead_of_being_recited():
    from main import direct_complex_reply
    calls = [{"name": "complex_query"}]
    raw = '- complex_query: {"tool_calls":[{"tool":"update_care_plan"}]}'

    # "" hands the turn back to normal generation, which speaks the result
    # properly rather than reciting the protocol.
    assert direct_complex_reply(calls, raw) == ""


def test_a_genuine_care_answer_still_goes_straight_to_tts():
    from main import direct_complex_reply
    calls = [{"name": "complex_query"}]
    reply = "- complex_query: ठीक है वैभव, मैंने वह जोड़ दिया है।"

    assert direct_complex_reply(calls, reply) == "ठीक है वैभव, मैंने वह जोड़ दिया है।"


def test_a_successful_start_result_is_a_handoff_not_a_local_followup():
    """The local model spoke step one even though CareVoice was already queued."""
    from main import is_successful_care_session_handoff

    calls = [{"name": "start_care_session"}]
    result = ('- start_care_session: {"status":"care_session_starting",'
              '"event_id":"198d109b","session_id":"65af6121"}')

    assert is_successful_care_session_handoff(calls, result) is True


@pytest.mark.parametrize("result", [
    "CARE_ACTION_FAILED: no care routine matches 'x'",
    '- start_care_session: CARE_ACTION_FAILED: session already active',
    '- start_care_session: {"status":"failed","event_id":"198d109b"}',
])
def test_a_failed_start_still_gets_a_truthful_local_followup(result):
    from main import is_successful_care_session_handoff

    assert is_successful_care_session_handoff(
        [{"name": "start_care_session"}], result) is False


def test_stale_input_cleanup_preserves_the_queued_care_handoff():
    """The post-TTS queue drain deleted the foreground event in the live log."""
    import asyncio
    from main import drain_stale_input_events

    queue = asyncio.Queue()
    queue.put_nowait(("interim", "late echo"))
    queue.put_nowait(("endpoint", None))
    queue.put_nowait(("care_session_start", "198d109b"))
    queue.put_nowait(("final", "also stale"))

    assert drain_stale_input_events(queue) == 1
    assert queue.get_nowait() == ("care_session_start", "198d109b")
    assert queue.empty()


def test_optional_prompt_marker_is_recovered_as_the_real_argument_name():
    """Gemma emitted `routine?` twice, copying the old compact signature."""
    from tools_and_config.tools import validate_tool_arguments

    args = {"routine?": "Surya Namaskar"}
    valid, reason = validate_tool_arguments("start_care_session", args)

    assert valid, reason
    assert args == {"routine": "Surya Namaskar"}


def test_optional_marker_recovery_does_not_hide_genuinely_unknown_arguments():
    from tools_and_config.tools import validate_tool_arguments

    args = {"made_up?": "Surya Namaskar"}
    valid, reason = validate_tool_arguments("start_care_session", args)

    assert not valid
    assert "unsupported argument" in reason


@pytest.mark.parametrize("bogus", ["start_session", "start", "begin"])
def test_starting_a_routine_on_the_wrong_section_names_the_right_tool(bogus):
    """The agent looped on update_care_plan(action="start_session")."""
    import asyncio
    from tools_and_config.tools import update_care_plan

    result = asyncio.run(update_care_plan(
        section="routine_event", action=bogus, data={}))

    assert "start_care_session" in result
    assert "ERROR" in result


def test_the_real_care_session_start_action_still_works():
    """care_session/start is a genuine combination and must not be blocked."""
    import asyncio, inspect
    from tools_and_config import tools as tools_mod

    src = inspect.getsource(tools_mod.update_care_plan)
    # start_session/begin are aliased onto it rather than rejected.
    assert '"start_session": "start"' in src
    assert 'action == "start" and section != "care_session"' in src
