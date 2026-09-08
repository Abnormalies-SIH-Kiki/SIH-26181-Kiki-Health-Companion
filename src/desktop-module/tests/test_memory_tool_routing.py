import json

import pytest

import core.llm as llm


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    """These assert DEFAULT-mode routing.

    The code routers read the ACTIVE mode's tool list (a roleplay character
    must not have recall_memory fired at it in code, since Kiki's memories are
    not theirs). Without pinning, this file would pass or fail depending on
    `assistant_modes.active_on_startup`.
    """
    monkeypatch.setattr("core.runtime_controls.get_active_mode", lambda: "default")


@pytest.mark.parametrize("query", [
    "Search your memory for discrete structures.",
    "What did we discuss about discrete structures till date?",
    "Find some funny memories.",
    "Do you remember what I told you about my exam?",
    "How did my discrete structures test go?",
])
def test_clear_personal_memory_queries_are_auto_routed(query):
    assert llm._should_auto_recall_memory(query)


@pytest.mark.parametrize("query", [
    "How does computer memory work?",
    "Do you remember the capital of France?",
    "Search the web for today's news.",
    "Explain discrete structures to me.",
    "Play some music.",
])
def test_non_autobiographical_queries_still_go_to_the_model(query):
    assert not llm._should_auto_recall_memory(query)


def test_auto_route_emits_normal_tool_protocol_without_calling_model(monkeypatch):
    monkeypatch.setattr(llm, "_SEND_TOOLS", True)
    monkeypatch.setattr(llm, "_MAIN_TOOL_NAMES", ["recall_memory"])

    def model_must_not_run(*args, **kwargs):
        raise AssertionError("clear memory request should route before generation")

    monkeypatch.setattr(llm, "_stream_local", model_must_not_run)
    events = list(llm.stream_response([
        {"role": "system", "content": "test"},
        {"role": "user", "content": "What did we discuss about discrete structures?"},
    ]))

    assert [kind for kind, _ in events] == ["tool_calls", "done"]
    call = events[0][1]["calls"][0]
    assert call["name"] == "recall_memory"
    assert json.loads(call["arguments"])["query"].startswith("What did we discuss")
    assert "<tool_call>" in events[1][1]


def test_followup_turn_is_not_auto_routed_again(monkeypatch):
    monkeypatch.setattr(llm, "_SEND_TOOLS", True)
    monkeypatch.setattr(llm, "_MAIN_TOOL_NAMES", ["recall_memory"])
    assert llm._auto_memory_tool_event(
        [{"role": "user", "content": "Search your memory for exams"}],
        verify_prefill=False,
        use_fallback=False,
    ) is None


def test_local_tool_prompt_is_short_and_decisive():
    instruction = llm._get_tools_instruction()

    assert "MANDATORY" in instruction
    assert "Never guess" in instruction
    assert "recall_memory(query)" in instruction
    assert "Parameters schema" not in instruction
    assert len(instruction) < 2200
