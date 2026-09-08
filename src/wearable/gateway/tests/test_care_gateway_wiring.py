"""Where health mode touches the gateway, and what it costs in default mode.

The integration points are deliberately few: the tool catalog, the system
prompt, the live context anchor, the turn router, and three device events. Each
one is tested here from both sides -- what it does in health mode, and that it
does nothing at all outside it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kiki_gateway import inference  # noqa: E402
from kiki_gateway.care import mode as care_mode  # noqa: E402
from kiki_gateway.care import runtime as care_runtime  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_runtime(monkeypatch):
    monkeypatch.setattr(care_runtime, "_RUNTIME", None)
    yield


# ------------------------------------------------------- the live anchor ----

def test_the_anchor_says_nothing_in_default_mode():
    """The row Kiki gets every five minutes must not grow by a byte."""
    assert inference._care_context_suffix() == ""


def test_the_anchor_carries_one_care_line_in_health_mode(monkeypatch):
    runtime = care_runtime.get_care_runtime()
    runtime._active = True
    monkeypatch.setattr(runtime, "care_line", lambda: "CARE NOW: AQI 301 very poor")

    suffix = inference._care_context_suffix()
    assert suffix == "\nCARE NOW: AQI 301 very poor"


def test_an_unchanged_snapshot_appends_nothing(monkeypatch):
    """Append-only history means a repeated row is pure prompt growth."""
    runtime = care_runtime.get_care_runtime()
    runtime._active = True
    monkeypatch.setattr(care_runtime, "care_runtime_if_live", lambda: runtime)
    monkeypatch.setattr(runtime, "_last_care_line", "")
    monkeypatch.setattr(
        "kiki_gateway.care.care_now.build_care_now",
        lambda *args, **kwargs: "CARE NOW: heart rate 74, steps 3400",
        raising=False)

    assert runtime.care_line() == "CARE NOW: heart rate 74, steps 3400"
    assert runtime.care_line() == "", "the same line twice is not new information"


def test_a_broken_care_snapshot_cannot_break_the_clock(monkeypatch):
    """Kiki losing her clock is worse than losing the care line."""
    runtime = care_runtime.get_care_runtime()
    runtime._active = True
    monkeypatch.setattr(care_runtime, "care_runtime_if_live", lambda: runtime)

    def explode():
        raise RuntimeError("care plan is on fire")

    monkeypatch.setattr(runtime, "care_line", explode)
    assert inference._care_context_suffix() == ""


# --------------------------------------------------------- the tool path ----

def test_a_care_tool_is_not_executed_in_default_mode(monkeypatch):
    monkeypatch.setattr(care_mode, "active_mode", lambda: "default")
    assert inference.LegacyKikiCore._execute_care_call(
        "update_care_plan", {"section": "routine_event"}) is None


def test_an_ordinary_tool_is_never_captured_by_the_care_path(monkeypatch):
    monkeypatch.setattr(care_mode, "active_mode", lambda: "health_sih")
    monkeypatch.setattr(care_mode, "_mode_config",
                        lambda name: {"capabilities": ["care"]})
    assert inference.LegacyKikiCore._execute_care_call("play_music", {}) is None


def test_a_care_tool_runs_in_health_mode(monkeypatch):
    monkeypatch.setattr(care_mode, "active_mode", lambda: "health_sih")
    monkeypatch.setattr(care_mode, "_mode_config",
                        lambda name: {"capabilities": ["care"]})
    monkeypatch.setattr("kiki_gateway.care.tools.execute_care_tool",
                        lambda name, args: f"ran {name}")
    assert inference.LegacyKikiCore._execute_care_call(
        "get_care_plan", {}) == "ran get_care_plan"


def test_a_failing_care_tool_reports_a_failure_rather_than_a_success(monkeypatch):
    monkeypatch.setattr(care_mode, "active_mode", lambda: "health_sih")
    monkeypatch.setattr(care_mode, "_mode_config",
                        lambda name: {"capabilities": ["care"]})

    def explode(name, args):
        raise RuntimeError("disk full")

    monkeypatch.setattr("kiki_gateway.care.tools.execute_care_tool", explode)
    result = inference.LegacyKikiCore._execute_care_call("update_care_plan", {})
    assert result.startswith("ERROR")
    assert "Nothing was changed" in result


# ------------------------------------------------------- the mode switch ----

def test_entering_and_leaving_health_mode_starts_and_stops_the_stack(monkeypatch):
    runtime = care_runtime.get_care_runtime()
    started, stopped = [], []

    # The doubles set `_active` because the real methods do; that flag is what
    # makes a second switch into the same mode a no-op instead of rebuilding
    # every care worker.
    def activate():
        runtime._active = True
        started.append(True)

    def deactivate():
        runtime._active = False
        stopped.append(True)

    monkeypatch.setattr(runtime, "activate", activate)
    monkeypatch.setattr(runtime, "deactivate", deactivate)

    monkeypatch.setattr(care_runtime, "care_active", lambda: True)
    assert runtime.sync_to_mode() is True
    assert started == [True]

    assert runtime.sync_to_mode() is False, "already on; nothing to do"

    monkeypatch.setattr(care_runtime, "care_active", lambda: False)
    assert runtime.sync_to_mode() is True
    assert stopped == [True]


# ----------------------------------------------------- the device events ----

def test_an_imu_window_from_the_board_reaches_the_store(monkeypatch):
    import base64
    import struct

    from kiki_gateway.care import imu

    runtime = care_runtime.get_care_runtime()
    runtime._active = True
    samples = [(0, 0, 1000, 0, 0, 0)] * 100
    payload = base64.b64encode(
        b"".join(struct.pack("<6h", *row) for row in samples)).decode()

    assert runtime.note_imu_window({
        "window_id": "w9", "hz": 50, "worn": True, "data": payload}) is True
    window, _reason = imu.get_window_store().take(timeout=0.0)
    assert window is not None and window.samples == 100
    imu.get_window_store().clear()


def test_a_window_with_no_samples_is_rejected_rather_than_stored():
    runtime = care_runtime.get_care_runtime()
    runtime._active = True
    assert runtime.note_imu_window({"window_id": "w0", "data": ""}) is False


def test_a_measurement_status_reaches_the_heart_rate_controller():
    from kiki_gateway.care.heart_rate import get_heart_rate_controller

    runtime = care_runtime.get_care_runtime()
    runtime._active = True
    runtime.note_measurement_status({"state": "measuring", "progress_percent": 40})
    assert get_heart_rate_controller().status()["state"] == "measuring"


def test_care_runtime_if_live_is_false_until_health_mode_is_entered():
    runtime = care_runtime.get_care_runtime()
    assert runtime.active is False
    assert care_runtime.care_runtime_if_live() is None
    runtime._active = True
    assert care_runtime.care_runtime_if_live() is runtime


# ------------------------------------------------------------ the prompt ----

def test_the_health_layer_names_the_rules_that_matter():
    text = care_mode.SYSTEM_PROMPT_ADDENDUM
    assert "NO camera" in text
    assert "Never diagnose" in text
    assert "only if a trusted tool" in text
    assert "still exactly the Kiki described above" in text, (
        "the layer must not replace her personality")


# ------------------------------------- a schedule must reach the care plan ---

@pytest.mark.parametrize("request_text", [
    "Schedule a care plan taking medicine session at three pm",   # the live one
    "remind me to take my tablet at 9am",
    "add a walk to my routine every evening",
    "set up a water reminder",
    "मुझे तीन बजे दवा याद दिलाना",
])
def test_a_care_schedule_sent_to_complex_query_is_refused(monkeypatch, request_text):
    """It would become a background worker: invisible, and never a care session."""
    monkeypatch.setattr(care_mode, "active_mode", lambda: "health_sih")
    monkeypatch.setattr(care_mode, "_mode_config",
                        lambda name: {"capabilities": ["care"]})

    result = inference.LegacyKikiCore._execute_care_call(
        "complex_query", {"request": request_text})
    assert result is not None, f"not caught: {request_text!r}"
    assert result.startswith("REDIRECTED")
    assert "update_care_plan" in result


@pytest.mark.parametrize("request_text", [
    "message Namita that I'll be late",
    "what's the weather in Delhi tomorrow",
    "search for the SIH submission deadline",
    "read my last three whatsapp messages",
    "play something calm",
])
def test_every_other_complex_query_is_untouched(monkeypatch, request_text):
    monkeypatch.setattr(care_mode, "active_mode", lambda: "health_sih")
    monkeypatch.setattr(care_mode, "_mode_config",
                        lambda name: {"capabilities": ["care"]})
    assert inference.LegacyKikiCore._execute_care_call(
        "complex_query", {"request": request_text}) is None


def test_complex_query_is_never_touched_outside_health_mode(monkeypatch):
    monkeypatch.setattr(care_mode, "active_mode", lambda: "default")
    assert inference.LegacyKikiCore._execute_care_call(
        "complex_query", {"request": "remind me to take my tablet at 9am"}) is None


@pytest.mark.parametrize("request_text", [
    "what's my heart rate today",
    "how many steps have I done",
    "what is in my care plan",
    "read my routine back to me",
    "मेरी दवा कब है",
])
def test_reading_this_persons_own_health_data_is_redirected(monkeypatch, request_text):
    """complex_query cannot see the plan or the wearable store on this body.

    Left alone it answers from the conversation or from general knowledge,
    which is a made-up number wearing the shape of a measurement.
    """
    monkeypatch.setattr(care_mode, "active_mode", lambda: "health_sih")
    monkeypatch.setattr(care_mode, "_mode_config",
                        lambda name: {"capabilities": ["care"]})
    result = inference.LegacyKikiCore._execute_care_call(
        "complex_query", {"request": request_text})
    assert result is not None, f"not caught: {request_text!r}"
    assert result.startswith("REDIRECTED")
    assert "get_care_plan" in result or "get_wearable_status" in result


@pytest.mark.parametrize("request_text", [
    "what is a normal resting heart rate for a 60 year old",
    "search for whether walking after dinner helps digestion",
    "what does spo2 actually measure",
    "look up the side effects of metformin",
])
def test_general_health_research_still_goes_to_the_complex_agent(monkeypatch,
                                                                 request_text):
    """The line is WHOSE data it is, not whether the topic is medical."""
    monkeypatch.setattr(care_mode, "active_mode", lambda: "health_sih")
    monkeypatch.setattr(care_mode, "_mode_config",
                        lambda name: {"capabilities": ["care"]})
    assert inference.LegacyKikiCore._execute_care_call(
        "complex_query", {"request": request_text}) is None


# ------------------------------------------------------ firmware updates ----

@pytest.mark.parametrize("url,allowed", [
    ("https://arms-caribbean.trycloudflare.com/kiki_esp32.bin", True),
    ("http://192.168.1.21:8137/kiki_esp32.bin", True),
    ("http://10.42.0.7/kiki_esp32.bin", True),
    ("http://172.16.5.9:8137/kiki_esp32.bin", True),
    ("http://127.0.0.1:8137/kiki_esp32.bin", True),
    # Plaintext from the open internet is an image anyone on the path can swap.
    ("http://8.8.8.8/kiki_esp32.bin", False),
    ("http://example.com/kiki_esp32.bin", False),
    # A hostname that merely looks private is still a hostname.
    ("http://192.168.1.5.evil.com/kiki_esp32.bin", False),
    ("ftp://192.168.1.5/kiki_esp32.bin", False),
    ("", False),
])
def test_only_https_or_a_literal_lan_address_may_flash_the_board(url, allowed):
    """The LAN exception exists because *.trycloudflare.com does not resolve here.

    Both the OTA tunnel and the gateway's own tunnel are unreachable by name on
    this network, so the HTTPS path cannot deliver an image at all and USB was
    the only way to update a board on the same desk.
    """
    from kiki_gateway.session import _acceptable_ota_url

    assert _acceptable_ota_url(url) is allowed, url
