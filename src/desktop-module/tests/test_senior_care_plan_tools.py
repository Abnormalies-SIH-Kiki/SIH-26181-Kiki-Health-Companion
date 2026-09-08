"""Regression tests for senior-mode care-plan voice edits."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from core.senior.care_plan import CarePlan
from core.tts_sync import romanize_hindi_for_lcd
from main import deterministic_care_plan_failure_reply, tool_result_note
from tools_and_config.tools import update_care_plan, validate_tool_arguments


class _InactiveManager:
    def is_active(self):
        return False


@pytest.fixture
def isolated_plan(tmp_path, monkeypatch):
    plan = CarePlan(tmp_path / "care_plan.json")
    monkeypatch.setattr(
        "core.senior.care_plan.get_care_plan_store", lambda: plan)
    monkeypatch.setattr(
        "core.senior.senior_care_manager.get_senior_care_manager",
        lambda: _InactiveManager())
    return plan


def _run_update(**kwargs):
    return asyncio.run(update_care_plan(**kwargs))


def test_exact_live_hindi_call_is_coerced_then_asks_for_time(isolated_plan):
    args = {
        "section": "reminders",
        "action": "add",
        "data": "सुबह जल्दी उठना",
    }
    valid, reason = validate_tool_arguments("update_care_plan", args)
    assert valid, reason
    assert args["data"] == {"message": "सुबह जल्दी उठना"}

    result = _run_update(**args)
    assert result.startswith("NEEDS_CLARIFICATION:")
    assert "No reminder was saved" in result
    assert isolated_plan.get_section("reminders") == []


def test_complete_hindi_reminder_saves_and_preserves_original_text(isolated_plan):
    result = _run_update(
        section="reminders",
        action="create",
        data={
            "category": "other",
            "message": "सुबह जल्दी उठना",
            "schedule": {"kind": "daily", "value": "8:00"},
        },
    )

    assert result.startswith("SUCCESS:")
    reminders = isolated_plan.get_section("reminders")
    assert len(reminders) == 1
    assert reminders[0]["message"] == "सुबह जल्दी उठना"
    assert reminders[0]["schedule"] == {"kind": "daily", "value": "08:00"}

    raw = isolated_plan.file_path.read_text(encoding="utf-8")
    assert "सुबह जल्दी उठना" in raw
    assert romanize_hindi_for_lcd(reminders[0]["message"]) == "subah jaldi uthanaa"


def test_exact_live_bare_time_shape_becomes_a_daily_schedule(isolated_plan):
    result = _run_update(
        section="reminders",
        action="add",
        data={
            "message": "सुबह आठ बजे उठने का समय हो गया है",
            "schedule": "08:00",
        },
    )
    assert result.startswith("SUCCESS:")
    reminder = isolated_plan.get_section("reminders")[0]
    assert reminder["schedule"] == {"kind": "daily", "value": "08:00"}


def test_live_exercise_reminder_shape_does_not_create_an_inert_routine(isolated_plan):
    result = _run_update(
        section="exercises",
        action="add",
        data={
            "message": "पीठ के लिए व्यायाम करने का समय हो गया है",
            "schedule": "19:20",
        },
    )
    assert result.startswith("SUCCESS:")
    assert isolated_plan.get_section("exercises") == []
    reminder = isolated_plan.get_section("reminders")[0]
    assert reminder["category"] == "exercise"
    assert reminder["schedule"] == {"kind": "daily", "value": "19:20"}


def test_live_exercise_shape_materializes_an_active_daily_worker(
        tmp_path, monkeypatch):
    from core.senior.senior_care_manager import SeniorCareManager

    class FakeWorker:
        def __init__(self, name, trigger_type, trigger_value):
            self.id = name
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
            self.workers.append(worker)
            return worker

        def _save(self):
            pass

    plan = CarePlan(tmp_path / "care_plan.json")
    workers = FakeWorkerManager()
    manager = SeniorCareManager(workers, plan)
    manager.activate()
    monkeypatch.setattr(
        "core.senior.care_plan.get_care_plan_store", lambda: plan)
    monkeypatch.setattr(
        "core.senior.senior_care_manager.get_senior_care_manager",
        lambda: manager)

    result = _run_update(
        section="exercises",
        action="add",
        data={"message": "पीठ का व्यायाम", "schedule": "19:20"},
    )

    assert result.startswith("SUCCESS:")
    active = [worker for worker in workers.workers if worker.is_active()]
    assert len(active) == 1
    assert active[0].name.startswith("senior:reminder:")
    assert active[0].trigger.interval_seconds == 86400
    assert active[0].trigger.last_fired_at.endswith("19:20:00")


def test_empty_guided_exercise_is_rejected_by_the_store(tmp_path):
    plan = CarePlan(tmp_path / "care_plan.json")
    with pytest.raises(ValueError, match="exercise name"):
        plan.add_exercise("", [], {"kind": "daily", "value": "19:20"})
    assert plan.get_section("exercises") == []


@pytest.mark.parametrize("schedule", [
    {},
    {"kind": "daily", "value": "25:00"},
    {"kind": "recurring", "value": 0},
    {"kind": "recurring", "value": "not-a-number"},
    {"kind": "once", "value": "someday"},
])
def test_invalid_schedule_never_creates_an_inert_reminder(isolated_plan, schedule):
    result = _run_update(
        section="reminder",
        action="add",
        data={"message": "पानी पीना", "schedule": schedule},
    )
    assert not result.startswith("SUCCESS:")
    assert isolated_plan.get_section("reminders") == []


def test_stringified_object_from_old_prompt_is_accepted():
    args = {
        "section": "reminder",
        "action": "add",
        "data": json.dumps({
            "message": "Take medicine",
            "schedule": {"kind": "daily", "value": "09:00"},
        }),
    }
    valid, reason = validate_tool_arguments("update_care_plan", args)
    assert valid, reason
    assert isinstance(args["data"], dict)


def test_care_result_note_forbids_false_success():
    note = tool_result_note(
        [{"name": "update_care_plan"}],
        "NEEDS_CLARIFICATION: No reminder was saved. Ask what time it should run.",
    )
    assert "saved ONLY" in note
    assert "it was not saved yet" in note
    assert "PARTIAL means it was saved but will not reliably trigger" in note


@pytest.mark.parametrize("result", [
    "- update_care_plan: ERROR: No change was saved. invalid schedule",
    "- update_care_plan: PARTIAL: saved but worker was not scheduled",
    "- update_care_plan: NEEDS_CLARIFICATION: No reminder was saved. Ask what time",
])
def test_failed_care_write_gets_a_deterministic_truthful_reply(result):
    reply = deterministic_care_plan_failure_reply(
        [{"name": "update_care_plan"}], result)
    assert reply
    assert "सेव" in reply or "चालू नहीं" in reply
    assert "सेट कर दिया" not in reply


def test_tool_schema_exposes_data_as_an_object():
    from tools_and_config.tools import _TOOL_SCHEMAS_BY_NAME

    schema = _TOOL_SCHEMAS_BY_NAME["update_care_plan"]
    assert schema["parameters"]["properties"]["data"]["type"] == "object"


# --- The routine-action key-name loop --------------------------------------
# Live failure 2026-08-29 00:41:20: the agent sent a complete, sensible "Back
# Exercise" routine five times as {"action": ..., "data": ...} instead of
# {"type": ..., "instruction": ...}. Every action was dropped, the error said
# only "requires at least one valid action", so the agent reworded the prose it
# had RIGHT instead of the keys it had WRONG until the turn budget died and
# Kiki spoke "That care action did not complete."

LIVE_LOOPING_PAYLOAD = {
    "title": "Back Exercise",
    "schedule": "2026-08-29T12:39:00",
    "continuous_vision": True,
    "actions": [
        {"action": "speak",
         "data": "Hey Vaibhav, it is time for your midday back stretch."},
        {"action": "check_in",
         "data": "Are you feeling any pain or stiffness in your back right now?",
         "needs_response": True},
        {"action": "guided_step",
         "data": "Let's start with a gentle Cat-Cow stretch. Move with your breath.",
         "needs_response": True},
        {"action": "log", "data": "Completed midday back exercise routine."},
    ],
}


def test_the_exact_looping_routine_payload_is_now_saved(isolated_plan):
    """A routine keyed with action/data is semantically right — accept it."""
    result = _run_update(section="routine_event", action="add",
                         data=dict(LIVE_LOOPING_PAYLOAD))
    assert "ERROR" not in result, result

    events = isolated_plan.get_section("routine_events")
    assert len(events) == 1, events
    actions = events[0]["actions"]
    assert [a["type"] for a in actions] == [
        "speak", "check_in", "guided_step", "log"]
    assert actions[0]["instruction"].startswith("Hey Vaibhav")
    assert actions[1]["needs_response"] is True


def test_an_unfixable_action_says_which_keys_it_got(isolated_plan):
    """The rejection must name the keys, or the agent reworders and loops."""
    result = _run_update(section="routine_event", action="add", data={
        "title": "Broken", "schedule": "2026-08-29T12:39:00",
        "actions": [{"type": "speak", "wrong_key": "hello"}]})

    assert "ERROR" in result
    assert "wrong_key" in result, result          # what it actually sent
    assert '"instruction"' in result, result      # what it should have sent
    assert "do not reword" in result.lower(), result


# Live failures 2026-08-29 00:08:26 and 00:37:43: two more schedule shapes the
# agent produced, each rejected four times with a message that listed the
# accepted forms but never said what it had received.
@pytest.mark.parametrize("schedule,kind,value", [
    ({"start_time": "12:39", "trigger_type": "scheduled_time"}, "daily", "12:39"),
    ("daily 00:00", "daily", "00:00"),
    ("2026-08-29T12:39:00", "once", "2026-08-29T12:39:00"),
    ({"kind": "daily", "value": "08:00"}, "daily", "08:00"),
])
def test_the_schedule_shapes_the_agent_actually_sends(
        isolated_plan, schedule, kind, value):
    result = _run_update(section="routine_event", action="add", data={
        "title": "Back Exercise", "schedule": schedule,
        "actions": [{"type": "guided_step", "instruction": "Cat-Cow stretch."}]})

    assert "ERROR" not in result, result
    saved = isolated_plan.get_section("routine_events")[0]["schedule"]
    assert saved == {"kind": kind, "value": value}


def test_an_ambiguous_schedule_is_still_refused_and_quotes_what_it_got(isolated_plan):
    """Coercion must not become guessing — and the refusal must be actionable."""
    result = _run_update(section="routine_event", action="add", data={
        "title": "Vague", "schedule": {"kind": "weekly", "value": "monday"},
        "actions": [{"type": "speak", "instruction": "hello"}]})

    assert "ERROR" in result
    assert "weekly" in result and "monday" in result, result
    assert isolated_plan.get_section("routine_events") == []
