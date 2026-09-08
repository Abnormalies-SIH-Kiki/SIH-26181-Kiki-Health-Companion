"""A completion claim must never outrank unexecuted tool calls.

From the 2026-08-29 logs, the care agent answered a request to add a neck
exercise with a single object containing BOTH an `update_care_plan` call and
`status: "completed"`:

    18:38:59.812 [CareVoice] LLM response: {"tool_calls":[{"tool":"update_care_plan",
        ...,"schedule":{"kind":"daily","value":"18:40"},...}],
        "status":"completed","summary":"ठीक है वैभव, मैंने आपके केय...
    18:38:59.813 [CareVoice] Task completed: ...गर्दन की कसरत का समय जोड़ दिया है।

One millisecond apart: the status branch was evaluated first, returned, and
the tool never ran. Kiki said the exercise was scheduled, the care plan was
never written, and nothing happened at 18:40.
"""

import asyncio

from core.agent_loop import run_agent_loop


def _run(**kw):
    return asyncio.run(run_agent_loop(**kw))


def test_tool_calls_run_even_when_the_same_response_claims_completion():
    executed = []
    responses = iter([
        # The exact shape from the log: do the thing AND declare it done.
        '{"tool_calls":[{"tool":"update_care_plan","args":{"action":"create"}}],'
        '"status":"completed","summary":"I added the neck exercise."}',
        # Only after seeing a real result may it finish.
        '{"status":"completed","summary":"Neck exercise added for 18:40."}',
    ])

    def llm_fn(_prompt):
        return next(responses)

    def executor(name, args):
        executed.append((name, args))
        return '{"ok": true, "id": "neck01"}'

    ok, result, _speak, _final, tools_used = _run(
        prompt="add a neck exercise", llm_fn=llm_fn, label="Test",
        tool_executor=executor, max_turns=4, min_tool_calls=0)

    assert executed == [("update_care_plan", {"action": "create"})], \
        "the tool call was dropped in favour of the completion claim"
    assert tools_used == ["update_care_plan"]
    assert ok
    assert "Neck exercise added" in result


def test_a_failure_claim_also_does_not_cancel_pending_tool_calls():
    executed = []
    responses = iter([
        '{"tool_calls":[{"tool":"alert_family","args":{"msg":"x"}}],'
        '"status":"failed","reason":"could not reach anyone"}',
        '{"status":"completed","summary":"Family alerted."}',
    ])

    def llm_fn(_prompt):
        return next(responses)

    def executor(name, args):
        executed.append(name)
        return "sent"

    ok, result, _speak, _final, _tools = _run(
        prompt="alert the family", llm_fn=llm_fn, label="Test",
        tool_executor=executor, max_turns=4)

    assert executed == ["alert_family"]
    assert ok
    assert "Family alerted" in result


def test_a_plain_completion_with_no_tool_calls_still_returns_immediately():
    """The fix must not force a pointless extra turn on normal completions."""
    turns = []

    def llm_fn(_prompt):
        turns.append(1)
        return '{"status":"completed","summary":"Nothing to do."}'

    ok, result, _speak, _final, tools_used = _run(
        prompt="say hello", llm_fn=llm_fn, label="Test",
        tool_executor=lambda n, a: "", max_turns=4)

    assert len(turns) == 1
    assert ok
    assert result == "Nothing to do."
    assert tools_used == []


def test_an_empty_tool_calls_list_is_not_treated_as_pending_work():
    """`tool_calls: []` alongside completion is a finished turn, not a claim."""
    turns = []

    def llm_fn(_prompt):
        turns.append(1)
        return '{"tool_calls":[],"status":"completed","summary":"All done."}'

    ok, result, _speak, _final, _tools = _run(
        prompt="do nothing", llm_fn=llm_fn, label="Test",
        tool_executor=lambda n, a: "", max_turns=4)

    assert len(turns) == 1
    assert ok
    assert result == "All done."
