"""Starting an engagement session goes through the TOOL path, not around it.

The bug this pins: the first version started the session directly from main.py
on a keyword match. That skipped `is_successful_care_session_handoff`, which
exists to suppress the follow-up local generation on `start_care_session` --
so the speaking model produced an ordinary sympathetic reply and started
conducting the session, while the care agent was opening the very same session
underneath it.

Routing it as a synthetic tool call reuses the guard instead of racing it, in
exactly the shape `_auto_complex_query_tool_event` already uses.
"""

import json

import pytest

import core.llm as llm
from core.runtime_controls import switch_mode


@pytest.fixture(autouse=True)
def companion_mode():
    """conftest pins every test to `default`; these need the companion mode."""
    switch_mode("health_sih")
    yield


@pytest.fixture
def no_session(monkeypatch):
    class Plan:
        def care_session_state(self):
            return {"status": "none"}

    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store", lambda: Plan())


def _route(text, verify_prefill=True, use_fallback=False):
    return llm._auto_engagement_tool_event(
        [{"role": "user", "content": text}], verify_prefill, use_fallback)


@pytest.mark.parametrize("said", [
    "I'm bored", "im so bored", "nothing to do",
    "entertain me", "lets do something", "मन नहीं लग रहा", "कुछ सुनाओ",
])
def test_a_request_for_company_is_routed(no_session, said):
    event = _route(said)
    assert event is not None
    _, payload = event
    call = payload["calls"][0]
    assert call["name"] == "start_care_session"
    assert json.loads(call["arguments"])["routine"] == "Something to think about"
    assert payload["auto_routed"] is True


@pytest.mark.parametrize("said", [
    "play some music", "what time is it", "I'm bored of this schedule",
    "how are you", "remind me to take my tablet",
])
def test_ordinary_speech_is_not_routed(no_session, said):
    assert _route(said) is None


def test_a_running_session_is_never_interrupted(monkeypatch):
    class Busy:
        def care_session_state(self):
            return {"status": "active", "event_title": "Neck Routine"}

    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store", lambda: Busy())
    assert _route("I'm bored") is None


def test_a_mode_without_the_companion_capability_never_routes(no_session):
    """`senior` has the care stack but not companionship; `default` has neither."""
    for mode in ("senior", "default"):
        switch_mode(mode)
        assert _route("I'm bored") is None
    switch_mode("health_sih")


def test_the_tool_result_follow_up_turn_is_not_re_routed(no_session):
    """verify_prefill=False is the turn that speaks the tool's result. Routing
    again there would start a second session from the same sentence."""
    assert _route("I'm bored", verify_prefill=False) is None


def test_the_cloud_fallback_turn_is_not_routed(no_session):
    assert _route("I'm bored", use_fallback=True) is None


def test_the_model_is_told_the_tool_covers_boredom():
    """The PRIMARY route is the model choosing the tool itself; the router above
    is only the backstop. That only works if the hint says so."""
    instruction = llm._get_tools_instruction()
    assert "start_care_session" in instruction
    assert "bored" in instruction.lower()


def test_the_handoff_guard_still_recognises_the_routed_call():
    """The whole reason for routing as a tool: main.py must suppress the second
    local generation, or the model talks over the session it just opened."""
    from main import is_successful_care_session_handoff

    calls = [{"name": "start_care_session"}]
    result = ('- start_care_session: '
              + json.dumps({"status": "care_session_starting", "event_id": "e1"}))
    assert is_successful_care_session_handoff(calls, result) is True
