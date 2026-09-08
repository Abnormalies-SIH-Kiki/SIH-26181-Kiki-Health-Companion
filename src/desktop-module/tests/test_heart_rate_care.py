"""MAX30102 care tool, trend persistence, scheduling, and direct speech contracts."""

import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest

import max30102_read
from core.brain import action_agent
from core.senior.care_plan import CarePlan
from core.senior.heart_rate import HeartRateController
from core.senior.senior_care_manager import execute_scheduled_routine
from tools_and_config.tools import heart_rate_measurement


def test_signal_analysis_finds_a_stable_synthetic_pulse():
    fs, seconds, hz = 100, 35, 1.25
    t = np.arange(fs * seconds) / fs
    pulse = np.sin(2 * np.pi * hz * t) + 0.18 * np.sin(4 * np.pi * hz * t)
    ir = 120_000 + 3_500 * pulse
    red = 100_000 + 2_000 * pulse

    result = max30102_read.analyse(red, ir, fs)

    assert result["bpm"] == pytest.approx(75, abs=2)
    assert result["n_peaks"] >= 12


def test_controller_enforces_prepare_then_capture():
    prepared = {"status": "ready_for_contact", "site": "finger",
                "contact_gate": 20_000}
    trusted = {"status": "trusted_reading", "bpm": 72, "quality": "GOOD",
               "site": "finger", "signal": {"pi": 1.1}}
    controller = HeartRateController(
        prepare_fn=lambda **_kwargs: prepared,
        capture_fn=lambda _prep, **_kwargs: trusted,
        progress_fn=lambda _event: None)

    assert controller.capture()["status"] == "not_prepared"
    assert controller.prepare()["status"] == "ready_for_contact"
    assert controller.state()["status"] == "ready_for_contact"
    assert controller.capture()["bpm"] == 72
    assert controller.state()["status"] == "idle"


def test_controller_rejects_an_expired_ambient_baseline(monkeypatch):
    clock = iter((100.0, 281.0, 281.0))
    monkeypatch.setattr("core.senior.heart_rate.time.monotonic", lambda: next(clock))
    controller = HeartRateController(
        prepare_fn=lambda **_kwargs: {
            "status": "ready_for_contact", "site": "finger",
            "ambient_baseline": 200, "contact_gate": 15_000,
        },
        capture_fn=lambda _prep, **_kwargs: pytest.fail(
            "expired preparation must not reach the sensor capture"),
        progress_fn=lambda _event: None)

    assert controller.prepare()["status"] == "ready_for_contact"
    assert controller.capture() == {
        "status": "not_prepared", "reason": "run_prepare_first"}
    assert controller.state()["status"] == "idle"


def test_trusted_tool_reading_is_recorded_and_trended(tmp_path, monkeypatch):
    plan = CarePlan(tmp_path / "care.json")

    class Controller:
        def capture(self, _seconds=None):
            return {"status": "trusted_reading", "measurement": "heart_rate",
                    "bpm": 74, "unit": "bpm", "quality": "GOOD",
                    "site": "finger", "signal": {"pi": 1.4, "n_peaks": 28}}

    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store", lambda: plan)
    monkeypatch.setattr(
        "core.senior.heart_rate.get_heart_rate_controller", lambda: Controller())

    result = json.loads(asyncio.run(heart_rate_measurement(
        "capture", context="seated and resting")))

    assert result["status"] == "trusted_reading"
    assert result["bpm"] == 74
    assert result["record_id"]
    assert result["trend"]["median"] == 74
    stored = plan.get_section("health_measurements")
    assert len(stored) == 1
    assert stored[0]["context"] == "seated and resting"


def test_poor_signal_is_logged_but_never_added_to_numeric_trend(
        tmp_path, monkeypatch):
    plan = CarePlan(tmp_path / "care.json")

    class Controller:
        def capture(self, _seconds=None):
            return {"status": "retryable_poor_signal", "quality": "POOR",
                    "reasons": ["too few clean beats"], "signal": {"n_peaks": 2}}

    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store", lambda: plan)
    monkeypatch.setattr(
        "core.senior.heart_rate.get_heart_rate_controller", lambda: Controller())

    result = json.loads(asyncio.run(heart_rate_measurement("capture")))

    assert result["status"] == "retryable_poor_signal"
    assert "bpm" not in result
    assert plan.health_trend()["count"] == 0
    assert plan.get_section("care_log")[-1]["kind"] == "heart_rate_attempt"


def test_measure_vital_is_an_adaptive_care_action(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    event = plan.add_routine_event(
        title="Morning heart-rate check", objective="Record a trusted resting BPM",
        category="vitals", schedule={"kind": "daily", "value": "09:00"},
        actions=[
            {"type": "check_in", "instruction": "Confirm readiness",
             "needs_response": True},
            {"type": "measure_vital", "vital_type": "heart_rate",
             "instruction": "Conduct a quality-gated MAX30102 measurement"},
            {"type": "log", "instruction": "Record the outcome"},
        ])

    assert event["category"] == "vitals"
    assert event["actions"][1]["type"] == "measure_vital"
    assert event["actions"][1]["vital_type"] == "heart_rate"
    assert event["actions"][1]["needs_response"] is True


def test_scheduled_routine_opens_foreground_session_without_worker_speech(
        tmp_path, monkeypatch):
    plan = CarePlan(tmp_path / "care.json")
    event = plan.add_routine_event(
        title="Morning heart-rate check", category="vitals",
        schedule={"kind": "daily", "value": "09:00"},
        session_brief="Conduct a resting MAX30102 heart-rate session.")
    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store", lambda: plan)
    worker = SimpleNamespace(
        name=f"senior:routine_event:{event['id']}", task_description="timing only")

    ok, result, speak_text = asyncio.run(execute_scheduled_routine(worker))

    assert ok is True
    assert json.loads(result)["status"] == "care_session_ready"
    assert speak_text is None
    assert plan.care_session_state()["status"] == "active"


def test_care_complex_result_is_marked_for_direct_tts():
    from main import (direct_complex_reply, is_direct_care_complex_call,
                      queue_voice_ready_text)

    calls = [{"name": "complex_query", "arguments": json.dumps({
        "request": "मेरी हार्ट रेट मापो"}, ensure_ascii=False)}]
    assert is_direct_care_complex_call(calls, "") is True
    assert direct_complex_reply(
        calls, "- complex_query: [gentle] उंगली सेंसर से हटा दीजिए।") == (
            "[gentle] उंगली सेंसर से हटा दीजिए।")
    queued = []
    fake_tts = SimpleNamespace(add_sentence=queued.append)
    assert queue_voice_ready_text(fake_tts, "पहला कदम। अब उंगली रखिए।") == 2
    assert queued == ["पहला कदम।", "अब उंगली रखिए।"]


def test_care_agent_catalog_and_prompt_own_measurement_dialogue():
    prompt = action_agent._prompt("मेरी हार्ट रेट मापो")
    assert "heart_rate_measurement" in action_agent._catalog()
    assert "Sensor tools return data and never speak" in prompt
    assert "Care summaries go directly to TTS" in prompt
    assert action_agent._cfg()["health_measurement_deadline_seconds"] > 60
