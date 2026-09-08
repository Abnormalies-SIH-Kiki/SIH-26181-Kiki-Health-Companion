"""Exercise visuals must agree with agent intent and cannot break voice/Stop."""
import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from kiki_gateway.care import agent, instructor
from kiki_gateway.care.plan import CarePlan
from kiki_gateway.session import DeviceSession


DEMO = {"move": "arm_raise", "side": "left", "pattern": "repeat", "period_seconds": 6}


@pytest.mark.parametrize("value", [None, [], "raise arms", {}, {"move": []},
    {**DEMO, "move": "neck_circle"}, {**DEMO, "side": []}, {**DEMO, "pattern": {}},
    {**DEMO, "period_seconds": float("nan")}, {**DEMO, "period_seconds": float("inf")},
    {**DEMO, "period_seconds": True}, {**DEMO, "period_seconds": "6"},
    {**DEMO, "period_seconds": 0}, {**DEMO, "period_seconds": 20},
    {**DEMO, "move": "shoulder_roll", "pattern": "hold"},
    {**DEMO, "move": "torso_twist", "side": "both", "pattern": "hold"}])
def test_bad_agent_commands_fail_closed(value):
    assert instructor.validate(value) is None


@pytest.fixture
def directive_state(monkeypatch):
    monkeypatch.setattr(agent, "_LAST_DIRECTIVE", {})
    monkeypatch.setattr(agent, "_exercise_cfg", lambda: {})


@pytest.mark.parametrize("fields,physical,ok", [
    ({"reply_reason": "safety"}, True, True),
    ({"reply_reason": "choice"}, True, True),
    ({"session": "complete"}, True, True),
    ({"hold_seconds": 0}, True, True),
    ({}, False, True), ({}, True, False),
])
def test_no_demo_when_listening_ending_or_failed(directive_state, fields, physical, ok):
    session = {"event": {"category": "exercise" if physical else "wellbeing"},
               "transcript": [{"assistant": "Ready?"}]}
    final = {"session": "continue", "reply_reason": "none", "hold_seconds": 6,
             "instructor": DEMO, **fields}
    agent._set_last_directive(final, ok, "Should we stop?", session)
    assert agent.get_last_care_directive()["instructor"] is None


def test_explicit_command_survives_and_is_cleared_on_next_turn(directive_state):
    session = {"event": {"category": "exercise"}, "transcript": []}
    final = {"hold_seconds": 6, "instructor": DEMO}
    agent._set_last_directive(final, True, "Raise your left arm.", session)
    assert agent.get_last_care_directive()["instructor"] == DEMO
    agent._set_last_directive({"hold_seconds": 6}, True, "Rest your arms.", session)
    assert agent.get_last_care_directive()["instructor"] is None


def test_form_retry_replays_the_previous_command_not_the_proposed_next_step(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    routine = plan.add_routine_event(title="Arms", category="exercise",
        schedule={"kind": "daily", "value": "08:00"}, session_brief="Gentle seated arms")
    plan.start_care_session(routine["id"])
    plan.record_care_turn(assistant_text="Raise your left arm.", instructor=DEMO)
    session = plan.care_session_state()
    corrected, _ = agent._enforce_motion_reply_contract(
        {"instruction_followed": "no", "reply_reason": "none", "hold_seconds": 6,
         "instructor": {**DEMO, "move": "seated_march"}},
        "Now march.", "Wrist was still.", has_motion_evidence=True,
        motion_expected=True, session=session)
    assert corrected["instructor"] == DEMO


def rig(demo=DEMO, fail_at=""):
    s = DeviceSession.__new__(DeviceSession)
    s.session_id = "demo-test"
    s.tts_stream_id = 0
    s.recent_ambient = deque()
    s.core = SimpleNamespace()
    s.endpointer = SimpleNamespace(reset=lambda **kwargs: None)
    s._reset_barge_in = lambda: None
    events = []
    state = {"active": True, "worker_done": False}

    async def send(kind, **fields):
        events.append((kind, fields))
        if kind == "exercise_instructor" and fail_at == "visual_send":
            raise OSError("visual channel unavailable")

    async def set_state(**fields):
        events.append(("state", fields))

    async def tts(queue, _endpoint):
        try:
            while (sentence := await queue.get()) is not None:
                events.append(("speech", sentence))
        finally:
            state["worker_done"] = True

    async def drained(_timeout):
        events.append(("drained", {}))
        s.playing = fail_at == "drain_timeout"
        if fail_at == "abort_drain":
            s.turn_abort.set()

    async def run(*_args, **_kwargs):
        if fail_at == "abort_model":
            s.turn_abort.set()
        return "Raise your left arm slowly.", {
            "instructor": demo, "hold_seconds": 6, "expect_reply": False, "cue": "start"}

    async def cue(_cue):
        if fail_at == "abort_cue":
            s.turn_abort.set()

    async def hold(_seconds, abort):
        events.append(("hold", {}))
        if fail_at == "cancel_hold":
            raise asyncio.CancelledError()
        if fail_at == "error_hold":
            raise RuntimeError("hold failed")
        state["active"] = False

    s.send_event = send
    s.set_state = set_state
    s._tts_worker = tts
    s.await_playback_drained = drained
    runtime = SimpleNamespace(attach_session=lambda _s: None, run_turn=run,
        session_active=lambda: state["active"], hold=hold, cue=cue)
    return s, runtime, events, state


async def test_demo_starts_only_after_speech_drains_and_stops_after_hold():
    s, runtime, events, state = rig()
    await s._run_care_turn(runtime, "ready", 0)
    sequence = [f.get("action") if k == "exercise_instructor" else k for k, f in events]
    assert sequence.index("prepare") < sequence.index("speech")
    assert sequence.index("speech") < sequence.index("drained") < sequence.index("start")
    assert sequence.index("start") < sequence.index("hold") < sequence.index("stop")
    demos = [f for k, f in events if k == "exercise_instructor" and f["action"] != "stop"]
    assert demos[0]["demo_id"] == demos[1]["demo_id"]
    assert state["worker_done"] and not s.playing


@pytest.mark.parametrize("fail_at", ["abort_model", "abort_drain", "abort_cue", "drain_timeout", "visual_send"])
async def test_abort_timeout_or_visual_failure_cannot_start_movement(fail_at):
    s, runtime, events, state = rig(fail_at=fail_at)
    await s._run_care_turn(runtime, "ready", 0)
    assert not any(k == "exercise_instructor" and f["action"] == "start" for k, f in events)
    assert state["worker_done"] and not s.playing


@pytest.mark.parametrize("fail_at,error", [("cancel_hold", asyncio.CancelledError), ("error_hold", RuntimeError)])
async def test_hold_cancellation_and_failure_always_stop_the_visual(fail_at, error):
    s, runtime, events, state = rig(fail_at=fail_at)
    with pytest.raises(error):
        await s._run_care_turn(runtime, "ready", 0)
    commands = [f["action"] for k, f in events if k == "exercise_instructor"]
    assert commands[-1] == "stop" and "start" in commands
    assert state["worker_done"] and not s.playing


@pytest.mark.parametrize("demo", [None, {"move": "unknown"}])
async def test_old_or_unsupported_directives_preserve_speech_and_hold(demo):
    s, runtime, events, _ = rig(demo=demo)
    await s._run_care_turn(runtime, "ready", 0)
    assert any(k == "speech" for k, _ in events)
    assert any(k == "hold" for k, _ in events)
    assert not any(k == "exercise_instructor" and f["action"] != "stop" for k, f in events)
