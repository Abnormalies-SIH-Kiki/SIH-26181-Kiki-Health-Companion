"""End-to-end contracts for the agent-owned daily care routine."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from core.brain import action_agent
from core.senior.care_plan import CarePlan
from core.senior.senior_care_manager import SeniorCareManager
from tools_and_config.tools import (
    get_care_schedule_status,
    update_care_plan,
)


class FakeWorker:
    def __init__(self, name, trigger_type, trigger_value):
        self.id = f"worker-{len(name)}-{name[-4:]}"
        self.name = name
        self.status = "pending"
        self.trigger = SimpleNamespace(
            trigger_type=trigger_type,
            interval_seconds=(int(trigger_value)
                              if trigger_type == "recurring" else None),
            scheduled_time=(trigger_value
                            if trigger_type == "scheduled_time" else None),
            last_fired_at=None,
        )

    def is_active(self):
        return self.status == "pending"


class FakeWorkerManager:
    def __init__(self):
        self.workers = []

    def list_workers(self, include_completed=True):
        return self.workers

    def cancel_worker(self, worker_id):
        for worker in self.workers:
            if worker.id == worker_id:
                worker.status = "cancelled"
                return True
        return False

    def create_worker(self, name, task_description, trigger_type,
                      trigger_value, created_by):
        worker = FakeWorker(name, trigger_type, trigger_value)
        worker.task_description = task_description
        self.workers.append(worker)
        return worker

    def _save(self):
        pass


def routine_data(**overrides):
    data = {
        "title": "शाम की पीठ की एक्सरसाइज़",
        "category": "exercise",
        "schedule": {"kind": "daily", "value": "19:20"},
        "session_brief": (
            "Conduct a complete evening back-mobility session using the "
            "person's care context, live responses, and caregiver guidance."),
        "actions": [
            {"type": "speak", "instruction": "चलिये धीरे से शुरू करते हैं।"},
            {"type": "check_in", "instruction": "पीठ में दर्द तो नहीं है?",
             "needs_response": True, "on_concern": "सत्र रोकें और परिवार को बताने की पेशकश करें"},
            {"type": "guided_step", "instruction": "कुर्सी पर सीधा बैठिए।",
             "needs_response": True, "success_signal": "तैयार"},
            {"type": "guided_step", "instruction": "कंधे धीरे पीछे घुमाइए।",
             "needs_response": True},
            {"type": "log", "instruction": "सत्र का परिणाम केयर लॉग में लिखें।"},
        ],
        "source": "user",
        "evidence": "User explicitly requested this daily routine.",
        "adaptation": {"allowed": True, "review_after_occurrences": 3},
        "continuous_vision": True,
    }
    data.update(overrides)
    return data


@pytest.fixture
def active_care(tmp_path, monkeypatch):
    plan = CarePlan(tmp_path / "care.json")
    workers = FakeWorkerManager()
    manager = SeniorCareManager(workers, plan)
    manager.activate()
    monkeypatch.setattr(
        "core.senior.care_plan.get_care_plan_store", lambda: plan)
    monkeypatch.setattr(
        "core.senior.senior_care_manager.get_senior_care_manager",
        lambda: manager)
    return plan, workers, manager


def test_routine_event_materializes_and_returns_verified_receipt(active_care):
    plan, workers, manager = active_care
    result = asyncio.run(update_care_plan(
        section="routine_event", action="add", data=routine_data()))
    assert result.startswith("SUCCESS:")
    event = plan.get_section("routine_events")[0]
    receipt_metadata = json.loads(result.split("RECEIPT_JSON:", 1)[1])
    assert receipt_metadata == {
        "section": "routine_event", "item_id": event["id"]}
    assert len(event["actions"]) == 5
    assert event["actions"][1]["needs_response"] is True

    receipt = asyncio.run(get_care_schedule_status(event["id"]))
    assert '"scheduled": true' in receipt
    assert '"next_trigger_at":' in receipt
    active = [worker for worker in workers.workers if worker.is_active()]
    assert len(active) == 1
    assert active[0].name == f"senior:routine_event:{event['id']}"
    assert "foreground care-session trigger" in active[0].task_description
    assert "must not generate dialogue" in active[0].task_description

    session_result = asyncio.run(update_care_plan(
        section="care_session", action="start", data={"event_id": event["id"]}))
    assert session_result.startswith("SUCCESS:")
    assert plan.care_session_state()["status"] == "active"
    assert manager.continuous_vision_required() is True
    asyncio.run(update_care_plan(
        section="care_session", action="set_vision", data={"enabled": False}))
    assert manager.continuous_vision_required() is False
    asyncio.run(update_care_plan(
        section="care_session", action="set_vision", data={"enabled": True}))
    assert manager.continuous_vision_required() is True
    # Starting the session must not cancel/rebuild the worker that invoked it.
    assert len([worker for worker in workers.workers if worker.is_active()]) == 1

    asyncio.run(update_care_plan(
        section="care_session", action="complete", data={"response": "done"}))
    assert manager.continuous_vision_required() is False


def test_interactive_routine_persists_real_conversation_across_turns(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    event = plan.add_routine_event(**{
        key: value for key, value in routine_data().items()
        if key in {"title", "category", "schedule", "session_brief", "actions", "source",
                   "evidence", "adaptation", "continuous_vision"}
    })
    state = plan.start_care_session(event["id"])
    assert state["status"] == "active"
    assert "action_index" not in state
    assert "turn_actions" not in state
    assert state["transcript"] == []
    assert state["continuous_vision"] is True

    resumed = plan.start_care_session(event["id"])
    assert resumed["id"] == state["id"]
    assert resumed["turn_count"] == 0

    state = plan.record_care_turn(
        "हाँ, शुरू करें", "ठीक है, आज आराम से शुरू करते हैं।",
        "Person is seated beside a chair.")
    assert state["turn_count"] == 1
    assert state["transcript"][0]["user"] == "हाँ, शुरू करें"

    state = plan.adapt_care_session(
        "आज पीठ नहीं, हाथों की हल्की एक्सरसाइज़ करें",
        [
            {"type": "check_in", "instruction": "हाथों में दर्द तो नहीं है?",
             "needs_response": True},
            {"type": "guided_step", "instruction": "उंगलियाँ धीरे खोलें और बंद करें।",
             "needs_response": True},
            {"type": "log", "instruction": "आज के बदलाव को केयर लॉग में लिखें।"},
        ],
        "User changed today's activity.")
    assert state["turn_count"] == 2
    assert "User-requested direction" in state["transcript"][-1]["note"]
    # The recurring plan remains caregiver-approved; only this live session changed.
    stored = plan.get_section("routine_events")[0]
    assert stored["actions"][2]["instruction"] == "कुर्सी पर सीधा बैठिए।"

    state = plan.finish_care_session("declined", "आज दर्द है")
    assert state["status"] == "declined"
    reloaded = CarePlan(plan.file_path).care_session_state()
    assert reloaded["status"] == "declined"
    assert reloaded["continuous_vision"] is True


def test_exact_agent_retry_is_idempotent(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    kwargs = {key: value for key, value in routine_data().items()
              if key in {"title", "category", "schedule", "session_brief", "actions", "source",
                         "evidence", "adaptation", "continuous_vision"}}
    first = plan.add_routine_event(**kwargs)
    second = plan.add_routine_event(**kwargs)
    assert first["id"] == second["id"]
    assert second["_existing"] is True
    assert len(plan.get_section("routine_events")) == 1


def test_idle_inference_requires_evidence(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    kwargs = {key: value for key, value in routine_data(
        source="idle_mind", evidence="once").items()
        if key in {"title", "category", "schedule", "session_brief", "actions", "source",
                   "evidence", "adaptation", "continuous_vision"}}
    with pytest.raises(ValueError, match="concrete repeated-routine evidence"):
        plan.add_routine_event(**kwargs)


def test_complex_agent_has_care_tools_and_care_specific_protocol():
    catalog = action_agent._catalog()
    for name in ("get_care_plan", "update_care_plan", "get_care_schedule_status"):
        assert f"- {name}(" in catalog
    prompt = action_agent._prompt(
        "रोज़ शाम सात बीस पर पीठ का व्यायाम करवाना",
        "User means 7:20 PM.")
    assert "session_brief" in prompt
    assert "not dialogue, an action DSL, or a tiny checklist" in prompt
    assert "get_care_schedule_status" in prompt
    assert "NEVER use schedule_worker" in prompt
    assert "fresh camera frame" in prompt
    assert "do not run a fixed questionnaire" in prompt.lower()
    assert "exact natural question Kiki should speak" in prompt
    assert "Never simulate the person's" in prompt


def test_care_session_phrasing_cannot_fall_into_the_general_agent():
    assert action_agent.is_care_request(
        "Create a daily guided mobility care session at 7:20 PM for me")
    prompt = action_agent._prompt(
        "Create a daily guided mobility care session at 7:20 PM for me")
    assert "read the COMPLETE plan" in prompt


def test_care_agent_cannot_bypass_plan_with_generic_worker():
    result = action_agent._tool_executor(
        "schedule_worker", {
            "name": "care", "task_description": "care",
            "trigger_type": "recurring", "trigger_value": "86400",
        }, care_mode=True)
    assert result.startswith("BLOCKED FOR CARE")


def test_complex_query_runs_care_write_and_runtime_verification(
        active_care, monkeypatch):
    from core.brain import fast_cloud

    plan, _workers, manager = active_care
    payload = routine_data()
    replies = [
        '{"tool_calls":[{"tool":"get_care_plan","args":{"section":""}}]}',
        '{"tool_calls":[{"tool":"update_care_plan","args":'
        + '{"section":"routine_event","action":"add","data":'
        + json.dumps(payload, ensure_ascii=False)
        + '}}]}',
        ('{"status":"completed","summary":"मैंने आपकी शाम की पूरी एक्सरसाइज़ '
         'दिनचर्या तैयार करके सक्रिय कर दी है।"}'),
    ]

    monkeypatch.setattr(
        fast_cloud, "complete",
        lambda _prompt, stop_event=None: replies.pop(0))
    monkeypatch.setattr(fast_cloud, "active_model", lambda: "test-care-model")

    result = asyncio.run(action_agent.run_complex_query(
        "रोज़ शाम सात बीस पर पीठ की पूरी एक्सरसाइज़ करवाना",
        "7:20 means PM."))

    assert "दिनचर्या" in result
    assert "अगला सत्र" in result
    assert "07:20 PM" in result
    event = plan.get_section("routine_events")[0]
    assert manager.schedule_receipt(event["id"])["scheduled"] is True


def test_senior_speaking_routes_care_to_complex_only(monkeypatch):
    import core.llm as llm
    from tools_and_config.config_loader import get_full_config

    monkeypatch.setattr("core.runtime_controls.get_active_mode", lambda: "senior")
    assert llm._should_route_complex_query(
        "मुझे रोज़ शाम सात बीस पर व्यायाम करवाना", [])
    senior_tools = set(
        get_full_config()["assistant_modes"]["modes"]["senior"]["main_tools"])
    assert "complex_query" in senior_tools
    assert "update_care_plan" not in senior_tools
    assert "get_care_plan" not in senior_tools
    instruction = llm._get_tools_instruction()
    assert "ONLY `complex_query` may read, create, edit, or conduct" in instruction


def test_active_session_routes_even_a_short_reply(monkeypatch):
    import core.llm as llm

    monkeypatch.setattr(llm, "_active_care_session", lambda: {"status": "active"})
    assert llm._should_route_complex_query("हाँ", [])


def test_senior_care_plan_reads_route_to_complex(monkeypatch):
    import core.llm as llm

    monkeypatch.setattr("core.runtime_controls.get_active_mode", lambda: "senior")
    assert llm._should_route_complex_query("आज मेरी कौनसी दवाई है?", [])
    assert llm._should_route_complex_query("What exercises are in my routine?", [])
