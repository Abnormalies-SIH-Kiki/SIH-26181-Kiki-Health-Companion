from datetime import datetime, timezone
import threading

from core.health.care_snapshot import build_care_now
from core.health.service import create_app
from core.health.wearable import build_summary
from core.senior.care_plan import CarePlan


def batch(batch_id="b1", *, steps=520, hr=72, quality="GOOD", spo2=97,
          fall=None):
    return {
        "batch_id": batch_id,
        "device_id": "wearable-kiki",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "sequence": 1,
        "steps_delta": steps,
        "steps_total": steps,
        "activity": "walking",
        "worn": True,
        "battery_percent": 83,
        "readings": {
            "heart_rate": {"value": hr, "quality": quality,
                           "signal": {"estimator_version": 2,
                                      "pi": 1.2, "rr_cv": 0.08,
                                      "n_peaks": 12, "acf": 0.78,
                                      "acf_hr": hr, "peak_hr": hr,
                                      "channel_corr": 0.9,
                                      "i2c_glitches": 0}},
            "spo2": {"value": spo2, "quality": "EXPERIMENTAL",
                     "calibrated": False},
        },
        "fall": fall or {},
    }


def test_ingest_is_idempotent_and_builds_desktop_context(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    client = create_app(plan).test_client()

    first = client.post("/api/health/v1/ingest", json=batch()).get_json()
    again = client.post("/api/health/v1/ingest", json=batch()).get_json()

    assert first["accepted"] is True and first["duplicate"] is False
    assert again["accepted"] is True and again["duplicate"] is True
    assert len(plan.get_section("wearable_events")) == 1
    assert len(plan.get_section("health_measurements")) == 1
    assert first["summary"]["latest"]["steps_today"] == 520
    assert first["summary"]["context"].startswith("Wearable: HR 72 bpm")


def test_environment_endpoint_reads_cached_provider(tmp_path):
    class Provider:
        def snapshot(self):
            return {"available": True, "state": "fresh", "temperature_c": 27, "aqi": 61}

    plan = CarePlan(tmp_path / "environment.json")
    client = create_app(plan, Provider()).test_client()
    result = client.get("/api/health/v1/environment")
    assert result.status_code == 200
    assert result.get_json()["aqi"] == 61
    missing = create_app(plan).test_client().get("/api/health/v1/environment").get_json()
    assert missing == {"available": False, "state": "unavailable"}


def test_poor_optical_window_never_enters_user_facing_state(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    payload = batch(hr=72, quality="POOR", spo2=91)
    plan.record_wearable_batch(payload)

    assert plan.get_section("health_measurements") == []
    summary = build_summary(plan)
    assert "heart_rate" not in summary["latest"]
    assert "spo2_experimental" not in summary["latest"]
    assert summary["spo2_experimental"] == []
    assert "HR " not in summary["context"]
    assert "SpO2" not in summary["context"]


def test_legacy_poor_snapshot_is_sanitized_on_read(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    plan.data["wearable_health"] = {
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "steps_today": 12,
        "heart_rate": {"value": 138, "quality": "POOR"},
        "spo2_experimental": {"value": 95, "calibrated": False},
    }

    summary = build_summary(plan)

    assert "heart_rate" not in summary["latest"]
    assert "spo2_experimental" not in summary["latest"]
    assert "138" not in summary["context"]


def test_mislabeled_fair_window_never_reaches_desktop_context(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    low_confidence = batch("false-40", hr=40, quality="FAIR", spo2=85)
    low_confidence["readings"]["heart_rate"]["signal"].update({
        "pi": 0.017, "rr_cv": 0.201, "n_peaks": 12, "acf": 0.184,
        "acf_hr": 40.0, "peak_hr": 51.3, "channel_corr": 0.915,
    })
    bus_corruption = batch("false-50", hr=49.8, quality="FAIR", spo2=70)
    bus_corruption["readings"]["heart_rate"]["signal"].update({
        "pi": 35.112, "rr_cv": 0.27, "n_peaks": 9, "acf": 0.364,
        "acf_hr": 49.8, "peak_hr": 75.9, "channel_corr": 0.931,
        "i2c_glitches": 30,
    })

    first = plan.record_wearable_batch(low_confidence)
    second = plan.record_wearable_batch(bus_corruption)
    summary = build_summary(plan)

    assert first["heart_rate_accepted"] is False
    assert second["heart_rate_accepted"] is False
    assert plan.get_section("health_measurements") == []
    assert "heart_rate" not in summary["latest"]
    assert "spo2_experimental" not in summary["latest"]
    assert "HR " not in summary["context"]


def test_explicit_paired_peak_window_can_pass_without_weak_acf(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    paired = batch("paired-76", hr=75.9, quality="FAIR", spo2=96)
    paired["readings"]["heart_rate"]["signal"].update({
        "pi": 0.100, "rr_cv": 0.274, "n_peaks": 11, "acf": 0.099,
        "acf_hr": 40.0, "peak_hr": 75.9, "channel_corr": 0.984,
        "paired_peaks": True,
    })

    result = plan.record_wearable_batch(paired)
    summary = build_summary(plan)

    assert result["heart_rate_accepted"] is True
    assert summary["latest"]["heart_rate"]["value"] == 75.9
    assert "HR 75.9 bpm" in summary["context"]


def test_too_few_peaks_fail_even_when_firmware_claims_pairing(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    sparse = batch("paired-sparse", hr=43.5, quality="FAIR", spo2=95)
    sparse["readings"]["heart_rate"]["signal"].update({
        "pi": 0.600, "rr_cv": 0.197, "n_peaks": 5, "acf": 0.383,
        "acf_hr": 46.2, "peak_hr": 43.5, "channel_corr": 0.854,
        "paired_peaks": True,
    })

    result = plan.record_wearable_batch(sparse)
    summary = build_summary(plan)

    assert result["heart_rate_accepted"] is False
    assert plan.get_section("health_measurements") == []
    assert "heart_rate" not in summary["latest"]


def test_care_now_reuses_the_wearable_summary(tmp_path):
    plan = CarePlan(tmp_path / "care.json")
    plan.record_wearable_batch(batch())

    line = build_care_now(plan=plan)

    assert line.startswith("CARE NOW: Wearable:")
    assert "520 steps today" in line


def test_two_process_style_writers_merge_append_only_sections(tmp_path):
    path = tmp_path / "care.json"
    seed = CarePlan(path)
    assert seed.save()
    left, right = CarePlan(path), CarePlan(path)
    gate = threading.Barrier(2)

    def write(plan, payload):
        gate.wait()
        plan.record_wearable_batch(payload)

    threads = [
        threading.Thread(target=write, args=(left, batch("left", steps=10))),
        threading.Thread(target=write, args=(right, batch("right", steps=20))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    reloaded = CarePlan(path)
    assert {row["batch_id"] for row in reloaded.get_section("wearable_events")} == {
        "left", "right"
    }
    assert {row["batch_id"] for row in reloaded.get_section("health_measurements")} == {
        "left", "right"
    }
    assert reloaded.get_section("wearable_health")["steps_today"] == 30


def test_confirmed_fall_requests_alerts_without_claiming_delivery(tmp_path, monkeypatch):
    plan = CarePlan(tmp_path / "care.json")
    plan.data["family_contacts"] = [{
        "name": "Family", "email": "family@example.com",
        "whatsapp": "+911234567890", "notify_on": ["alert"],
    }]
    assert plan.save()
    email_calls = []

    def notify(_plan, alert):
        email_calls.append(alert["batch_id"])
        return [{"channel": "email", "accepted": True,
                 "delivery_confirmed": False}]

    monkeypatch.setattr("core.health.service._notify_email", notify)
    client = create_app(plan).test_client()
    payload = batch("fall-1", fall={"status": "confirmed", "event_id": "f1"})

    response = client.post("/api/health/v1/ingest", json=payload).get_json()

    alert = response["alert_requested"]
    assert alert["kind"] == "possible_fall"
    assert alert["whatsapp_recipients"] == ["+911234567890"]
    assert alert["email_outcomes"][0]["accepted"] is True
    assert alert["email_outcomes"][0]["delivery_confirmed"] is False
    client.post("/api/health/v1/ingest", json=payload)
    assert email_calls == ["fall-1"]


def test_dashboard_and_health_probe_are_available(tmp_path):
    client = create_app(CarePlan(tmp_path / "care.json")).test_client()
    assert client.get("/healthz").get_json()["ok"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert b"Wearable Kiki" in page.data


def test_advisory_claim_prevents_two_kikis_speaking_the_same_notice(tmp_path):
    client = create_app(CarePlan(tmp_path / "care.json")).test_client()
    first = client.post("/api/health/v1/advisories/a1/claim",
                        json={"speaker": "desktop"})
    second = client.post("/api/health/v1/advisories/a1/claim",
                         json={"speaker": "wearable"})
    ack = client.post("/api/health/v1/advisories/a1/ack",
                      json={"speaker": "desktop"})
    assert first.status_code == 200
    assert second.status_code == 409
    assert ack.get_json()["acked"] is True
