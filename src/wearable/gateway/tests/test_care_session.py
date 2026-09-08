"""The care plan, the tools, and the promise that no Raspberry Pi is needed.

`health_sih` has to be complete on the laptop plus the band. That means the plan
lives here, the wearable telemetry is stored here, and a heart rate exists only
when the band actually reported one.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kiki_gateway.care import heart_rate as care_hr  # noqa: E402
from kiki_gateway.care import plan as care_plan  # noqa: E402
from kiki_gateway.care import tools as care_tools  # noqa: E402


@pytest.fixture
def plan(tmp_path, monkeypatch):
    store = care_plan.CarePlan(tmp_path / "care_plan.json")
    monkeypatch.setattr(care_plan, "_store", store, raising=False)
    monkeypatch.setattr(care_plan, "get_care_plan_store", lambda: store)
    monkeypatch.setattr(care_tools, "care_active", lambda: True)
    return store


def run(name, **arguments) -> str:
    return care_tools.execute_care_tool(name, arguments)


# ------------------------------------------------------------- the plan -----

def test_the_plan_is_gateway_owned_not_the_senior_modes_file(monkeypatch):
    """Sharing `senior`'s file would migrate a mode we promised not to touch."""
    monkeypatch.delenv("KIKI_GATEWAY_CARE_PLAN_FILE", raising=False)
    monkeypatch.delenv("KIKI_GATEWAY_CARE_DIR", raising=False)
    path = care_plan.get_care_plan_path()
    assert path.name == "care_plan.json"
    assert path.parent.name == "care-state"
    assert "legacy_kiki" not in str(path) and "kiki_runtime" not in str(path)


def test_an_explicit_path_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("KIKI_GATEWAY_CARE_PLAN_FILE", str(tmp_path / "mine.json"))
    assert care_plan.get_care_plan_path() == tmp_path / "mine.json"


def test_a_routine_needs_a_real_schedule_and_a_real_brief(plan):
    with pytest.raises(ValueError):
        plan.add_routine_event(title="Stretch", category="exercise",
                               schedule={"kind": "whenever", "value": "soon"},
                               session_brief="Arms.")
    with pytest.raises(ValueError):
        plan.add_routine_event(title="Stretch", category="exercise",
                               schedule={"kind": "daily", "value": "08:00"},
                               session_brief="")


def test_motion_tracking_replaces_the_camera_flag_and_accepts_its_old_name(plan):
    event = plan.add_routine_event(
        title="Arm stretch", category="exercise",
        schedule={"kind": "daily", "value": "08:00"},
        session_brief="Gentle seated arm movements for a stiff shoulder.",
        continuous_vision=True)
    assert event["motion_tracking"] is True
    assert "continuous_vision" not in event, (
        "a camera flag on a camera-less body misleads every future reader")


def test_a_session_can_be_started_recorded_and_closed(plan):
    event = plan.add_routine_event(
        title="Arm stretch", category="exercise",
        schedule={"kind": "daily", "value": "08:00"},
        session_brief="Gentle seated arm movements.", motion_tracking=True)
    state = plan.start_care_session(event["id"])
    assert state["status"] == "active"
    assert state["motion_tracking"] is True

    plan.record_care_turn(user_text="ready", assistant_text="Raise your arm.",
                          motion_observation="still [instruction_followed: no]")
    assert plan.care_session_state()["transcript"][-1]["motion_observation"]

    plan.finish_care_session("completed", reason="done")
    # The finished record deliberately stays in `active_session` -- "what
    # happened last session" is a real question -- and everything that asks
    # "is a session running" tests for `active` rather than for presence.
    closed = plan.care_session_state()
    assert closed["status"] == "completed"
    assert closed["status"] != "active"
    assert plan.get_section("session_history"), "a finished session is archived"
    assert "care_context" not in closed, (
        "the frozen plan copy is dropped; it was 18 kB of a 43 kB live file")


def test_a_second_session_cannot_start_on_top_of_a_live_one(plan):
    first = plan.add_routine_event(
        title="Arms", category="exercise",
        schedule={"kind": "daily", "value": "08:00"}, session_brief="Arms.")
    second = plan.add_routine_event(
        title="Water", category="hydration",
        schedule={"kind": "daily", "value": "09:00"}, session_brief="Water.")
    plan.start_care_session(first["id"])
    with pytest.raises(Exception) as excinfo:
        plan.start_care_session(second["id"])
    assert "already active" in str(excinfo.value)


# ------------------------------------------------------------- the tools ----

def test_reading_the_plan_returns_json_not_prose(plan):
    payload = json.loads(run("get_care_plan"))
    assert "routine_events" in payload and "active_session" in payload


def test_update_care_plan_refuses_to_start_a_routine_now(plan):
    """"Start my exercise" is a different tool, and saying so beats failing.

    A model asked to begin a routine reaches for `update_care_plan(...,
    "start")`; unrecognised actions just failed, and it retried until it ran
    out of turns and its raw JSON was read out loud.
    """
    result = run("update_care_plan", section="routine_event", action="start")
    assert "ERROR" in result and "start_care_session" in result


def test_a_saved_routine_reports_success_only_once_it_is_saved(plan):
    result = run("update_care_plan", section="routine_event", action="add",
                 data={"title": "Evening walk", "category": "exercise",
                       "schedule": {"kind": "daily", "value": "18:30"},
                       "session_brief": "A short walk while the air is better."})
    assert result.startswith("SUCCESS"), result
    assert plan.get_section("routine_events"), "the tool claimed a save it did not make"


def test_a_malformed_data_argument_saves_nothing(plan):
    """The invariant is "nothing was saved", not any particular wording.

    A bare string used to be rejected outright. It is now read as the person's
    words and comes back as NEEDS_CLARIFICATION naming what is still missing,
    which is the more useful of the two -- but the half that matters is that
    the plan is untouched either way.
    """
    result = run("update_care_plan", section="routine_event", action="add",
                 data="not an object")
    assert result.startswith(("ERROR", "NEEDS_CLARIFICATION")), result
    assert "SUCCESS" not in result
    assert not plan.get_section("routine_events")


# ------------------------------------------------- the band, with no Pi -----

def test_wearable_telemetry_is_stored_locally(plan):
    batch = {
        "batch_id": "b1",
        "device_id": "kiki-band",
        "captured_at": datetime.now().astimezone().isoformat(),
        "steps_delta": 120, "steps_total": 3400, "worn": True,
        "activity": "walking",
        "readings": {"heart_rate": {"value": 74, "quality": "GOOD",
                                    "peak_bpm": 74, "acf_bpm": 75,
                                    "estimator_version": 2}},
    }
    plan.record_wearable_batch(batch)
    assert plan.get_section("wearable_events"), "no Pi, so this store is the record"
    summary = json.loads(run("get_wearable_status"))
    assert summary


def test_the_same_batch_twice_is_stored_once(plan):
    batch = {"batch_id": "b1", "captured_at": datetime.now().astimezone().isoformat(),
             "steps_delta": 10, "steps_total": 10, "worn": True}
    plan.record_wearable_batch(batch)
    plan.record_wearable_batch(batch)
    assert len(plan.get_section("wearable_events")) == 1


def test_a_poor_quality_rate_never_becomes_a_trusted_measurement(plan):
    plan.record_wearable_batch({
        "batch_id": "b2", "captured_at": datetime.now().astimezone().isoformat(),
        "worn": True,
        "readings": {"heart_rate": {"value": 180, "quality": "POOR"}},
    })
    assert not plan.get_section("health_measurements")


# ------------------------------------------------------- the heart rate -----

def test_no_band_connected_is_reported_not_guessed():
    controller = care_hr.BoardHeartRate()
    controller.set_sender(None)
    result = controller.measure(timeout=5)
    assert result["status"] == "unavailable"
    assert "bpm" not in result


def test_a_failed_capture_produces_no_number():
    controller = care_hr.BoardHeartRate()
    sent = []

    def sender(event_type, fields):
        sent.append((event_type, fields))
        controller.note_status({"state": "failed", "worn": False,
                                "reason": "no skin contact"})
        return True

    controller.set_sender(sender)
    result = controller.measure(timeout=5)
    assert sent[0][0] == "wearable_measure"
    assert result["status"] == "no_contact"
    assert result.get("bpm") is None


def test_a_reading_the_firmware_will_not_stand_behind_is_not_a_reading():
    """The band reported a number AND said the signal was bad. It is not a rate."""
    controller = care_hr.BoardHeartRate()

    def sender(_type, _fields):
        controller.note_status({"state": "complete", "heart_rate": 143,
                               "quality": "POOR", "worn": True})
        return True

    controller.set_sender(sender)
    result = controller.measure(timeout=5)
    assert result["status"] == "poor_quality"
    assert result["bpm"] is None


def test_a_good_reading_comes_back_with_its_quality_and_experimental_spo2():
    controller = care_hr.BoardHeartRate()

    def sender(_type, _fields):
        controller.note_status({"state": "complete", "heart_rate": 76.4,
                               "quality": "GOOD", "worn": True,
                               "spo2_experimental": 96.5})
        return True

    controller.set_sender(sender)
    result = controller.measure(timeout=5)
    assert result["status"] == "trusted_reading"
    assert result["bpm"] == 76.4
    assert result["quality"] == "GOOD"
    assert "uncalibrated" in result["spo2_note"].lower()


def test_a_silent_board_times_out_rather_than_inventing_a_rate():
    controller = care_hr.BoardHeartRate()
    controller.set_sender(lambda _type, _fields: True)
    result = controller.measure(timeout=5)
    assert result["status"] == "timeout"
    assert "bpm" not in result


def test_a_trusted_reading_is_recorded_and_a_failed_one_is_only_an_attempt(plan,
                                                                          monkeypatch):
    controller = care_hr.BoardHeartRate()
    monkeypatch.setattr(care_hr, "_CONTROLLER", controller)

    def good(_type, _fields):
        controller.note_status({"state": "complete", "heart_rate": 72,
                               "quality": "FAIR", "worn": True})
        return True

    controller.set_sender(good)
    result = json.loads(run("measure_heart_rate", action="capture"))
    assert result["status"] == "trusted_reading"
    assert len(plan.get_section("health_measurements")) == 1

    controller.set_sender(None)
    failed = json.loads(run("measure_heart_rate", action="capture"))
    assert failed["status"] == "unavailable"
    assert "Do NOT state a heart rate" in failed["speak"]
    assert len(plan.get_section("health_measurements")) == 1, (
        "a failed attempt must never reach trusted history")


# ------------------------------------- what the model actually calls ---------

def test_the_shape_the_model_really_sent_saves_a_routine(plan):
    """Reproduced from the live 2026-09-06 23:48 failure, verbatim.

    Health mode, empty plan, first attempt to add anything:
    `key=`/`value=` instead of `data=`, and a plural section name. Every part
    a reasonable reading of "add a thing to a plan"; nothing was saved.
    """
    result = run("update_care_plan", section="exercises", action="add",
                 key="Hand Exercises (Up/Down)",
                 value="Perform 10 repetitions of wrist flexion and extension.",
                 data={"schedule": {"kind": "daily", "value": "09:00"}})
    assert result.startswith("SUCCESS"), result
    saved = plan.get_section("exercises")
    assert saved and saved[0]["name"] == "Hand Exercises (Up/Down)"
    assert "wrist flexion" in " ".join(saved[0]["steps"])


def test_a_routine_event_accepts_title_and_description_synonyms(plan):
    result = run("update_care_plan", section="routine", action="add",
                 data={"name": "Evening shoulder rolls", "category": "exercise",
                       "description": "Ten slow shoulder rolls while seated.",
                       "time": "18:30"})
    assert result.startswith("SUCCESS"), result
    event = plan.get_section("routine_events")[0]
    assert event["title"] == "Evening shoulder rolls"
    assert "shoulder rolls" in event["session_brief"]


def test_a_missing_time_asks_rather_than_inventing_one(plan):
    result = run("update_care_plan", section="routine_event", action="add",
                 key="Morning stretch", value="Gentle stretching.")
    assert "NEEDS_CLARIFICATION" in result or "ERROR" in result
    assert not plan.get_section("routine_events"), "no time means no schedule"


def test_a_wrong_call_comes_back_with_the_right_shape(plan, monkeypatch):
    """A model that only learns "TypeError" retries the same way until it dies."""
    monkeypatch.setattr(care_tools, "care_active", lambda: True)
    result = care_tools.execute_care_tool("measure_heart_rate",
                                          {"nonsense": 1, "also": 2})
    # measure_heart_rate has no `data` param, so junk is dropped and it runs.
    assert not result.startswith("ERROR"), result


def test_a_json_string_data_argument_is_parsed(plan):
    result = run("update_care_plan", section="routine_event", action="add",
                 data='{"title":"Water","category":"hydration",'
                      '"schedule":{"kind":"daily","value":"11:00"},'
                      '"session_brief":"A glass of water mid-morning."}')
    assert result.startswith("SUCCESS"), result
    assert plan.get_section("routine_events")[0]["title"] == "Water"


# --------------------------------------------- starting without a plan -------

def test_a_session_can_start_when_the_plan_is_empty(plan):
    """"Take me through a stretch" must work on day one, with nothing saved."""
    state = plan.start_adhoc_care_session(
        title="Shoulder stretch",
        session_brief="A short seated shoulder stretch, asked for by voice.")
    assert state["status"] == "active"
    assert state["event_title"] == "Shoulder stretch"
    assert state["motion_tracking"] is True
    assert state["event"]["source"] == "ad_hoc"


def test_an_adhoc_session_is_not_silently_added_to_the_schedule(plan):
    plan.start_adhoc_care_session(title="Shoulder stretch",
                                  session_brief="A short seated stretch.")
    assert not plan.get_section("routine_events"), (
        "a one-off request must not become a thing that speaks every day")
    assert not plan.all_active_schedules()


def test_an_adhoc_session_survives_being_read_back(plan):
    """Its event is inline, so nothing can look it up in routine_events."""
    plan.start_adhoc_care_session(title="Neck turns",
                                  session_brief="Slow seated neck turns.")
    state = plan.care_session_state()
    assert state["event"]["title"] == "Neck turns"
    assert state["motion_tracking"] is True


def test_an_adhoc_session_still_blocks_a_second_one(plan):
    plan.start_adhoc_care_session(title="A", session_brief="first")
    with pytest.raises(Exception) as excinfo:
        plan.start_adhoc_care_session(title="B", session_brief="second")
    assert "already active" in str(excinfo.value)


def test_asking_for_a_session_with_an_empty_plan_starts_one(plan, monkeypatch):
    """The live 23:48 failure: "there are no enabled routines in the care plan"."""
    from kiki_gateway.care import scheduling

    handed = []
    monkeypatch.setattr(scheduling, "_foreground_hook", handed.append)
    monkeypatch.setattr(scheduling, "get_care_plan_store", lambda: plan,
                        raising=False)

    result = scheduling.start_care_session_now("a gentle shoulder stretch")
    assert result.startswith("CARE_SESSION_STARTED"), result
    assert handed, "the foreground was never asked to speak it"
    state = plan.care_session_state()
    assert state["status"] == "active"
    assert "shoulder stretch" in state["event"]["session_brief"]


def test_a_session_that_cannot_be_voiced_is_closed_again(plan, monkeypatch):
    """An orphan session blocks every other routine behind it."""
    from kiki_gateway.care import scheduling

    monkeypatch.setattr(scheduling, "_foreground_hook", None)
    result = scheduling.start_care_session_now("neck turns")
    assert result.startswith("CARE_ACTION_FAILED"), result
    assert plan.care_session_state().get("status") != "active"


# ------------------------------------------------ the plan, on the watch -----

@pytest.fixture
def panel(plan, monkeypatch):
    from kiki_gateway.care import runtime as care_runtime

    rt = care_runtime.CareRuntime()
    rt._active = True
    monkeypatch.setattr(care_runtime, "care_active", lambda: True)
    return rt


def seed(plan, title, when, category="exercise"):
    return plan.add_routine_event(
        title=title, category=category,
        schedule={"kind": "daily", "value": when},
        session_brief=f"{title}: a short seated routine.")


def test_the_watch_is_sent_the_whole_plan_soonest_first(panel, plan):
    seed(plan, "Evening walk", "18:30")
    seed(plan, "Morning stretch", "07:15")
    seed(plan, "Midday water", "12:00", category="hydration")

    payload = panel.plan_payload()
    assert payload["total"] == 3
    assert [row["when"] for row in payload["items"]] == ["07:15", "12:00", "18:30"]
    assert payload["items"][0]["title"] == "Morning stretch"
    assert payload["items"][2]["category"] == "exercise"
    assert "seated routine" in payload["items"][0]["brief"]


def test_every_schedule_kind_reads_as_a_time(panel):
    assert panel._when_text({"kind": "daily", "value": "09:00"}) == "09:00"
    assert panel._when_text({"kind": "recurring", "value": 1800}) == "every 30m"
    assert panel._when_text({"kind": "recurring", "value": 7200}) == "every 2h"
    assert panel._when_text({"kind": "once", "value": "2026-09-07T18:30:00"}).startswith("Sep")
    assert panel._when_text(None) == "--"


def test_deleting_from_the_watch_actually_deletes(panel, plan):
    event = seed(plan, "Evening walk", "18:30")
    result = panel.apply_panel_action("delete", event["id"])
    assert result["ok"] is True
    assert not plan.get_section("routine_events")
    assert panel.plan_payload()["total"] == 0


def test_turning_a_routine_off_keeps_it_but_unschedules_it(panel, plan):
    event = seed(plan, "Evening walk", "18:30")
    assert panel.apply_panel_action("disable", event["id"])["ok"] is True

    row = panel.plan_payload()["items"][0]
    assert row["enabled"] is False, "off, not gone"
    assert not plan.all_active_schedules(), "a routine that is off does not fire"

    assert panel.apply_panel_action("enable", event["id"])["ok"] is True
    assert plan.all_active_schedules()


def test_the_watch_cannot_delete_something_that_is_not_there(panel, plan):
    seed(plan, "Evening walk", "18:30")
    result = panel.apply_panel_action("delete", "nonexistent")
    assert result["ok"] is False
    assert plan.get_section("routine_events"), "nothing else was touched"


def test_an_unknown_action_changes_nothing(panel, plan):
    event = seed(plan, "Evening walk", "18:30")
    assert panel.apply_panel_action("wipe_everything", event["id"])["ok"] is False
    assert plan.get_section("routine_events")


def test_a_long_plan_is_capped_but_says_so(panel, plan):
    for index in range(20):
        seed(plan, f"Routine {index}", f"{index % 24:02d}:00")
    payload = panel.plan_payload()
    assert len(payload["items"]) == panel.MAX_PANEL_ITEMS
    assert payload["total"] == 20


def test_a_running_session_shows_on_the_watch_and_can_be_ended(panel, plan):
    event = seed(plan, "Evening walk", "18:30")
    plan.start_care_session(event["id"])

    payload = panel.plan_payload()
    assert payload["session"] == {"active": True, "title": "Evening walk"}

    result = panel.apply_panel_action("end", "")
    assert result["ok"] is True
    assert plan.care_session_state().get("status") != "active"
    assert panel.plan_payload()["session"]["active"] is False


def test_ending_nothing_says_so_rather_than_claiming_success(panel, plan):
    result = panel.apply_panel_action("end", "")
    assert result["ok"] is False
    assert "no session" in result["detail"]


def test_the_watch_switches_reach_the_payload(panel):
    assert panel.plan_payload()["options"] == {"fall_alerts": True,
                                               "movement_checks": True}
    panel.note_care_options({"fall_alerts": False, "movement_checks": False})
    assert panel.movement_checks_enabled is False
    assert panel.fall_alerts_enabled is False
    assert panel.plan_payload()["options"]["fall_alerts"] is False


def test_a_partial_options_event_only_changes_what_it_names(panel):
    panel.note_care_options({"movement_checks": False})
    assert panel.movement_checks_enabled is False
    assert panel.fall_alerts_enabled is True, "an absent field is not a False"
