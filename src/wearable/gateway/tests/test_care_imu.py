"""Wrist motion as evidence: the measurements, and the absences.

The camera version of this routine failed in a specific way on 2026-09-02: it
confirmed six asanas in a row while the person sat still, because every
"observation" was assembled from the instruction rather than from the frames.
The structural defence is in `agent.py`; the defence HERE is that the numbers
handed to the model are computed, and that every way of having no evidence
produces an explicit statement of that rather than a plausible blank.
"""

from __future__ import annotations

import base64
import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kiki_gateway.care import imu  # noqa: E402


def encode(samples):
    """Samples as the firmware sends them: int16 milli-g and centi-dps."""
    payload = b"".join(struct.pack("<6h", *row) for row in samples)
    return base64.b64encode(payload).decode()


def still_window(seconds=4.0, hz=50, worn=True):
    """A wrist doing nothing: 1 g down, no rotation, a little sensor noise."""
    count = int(seconds * hz)
    samples = []
    for index in range(count):
        wobble = 3 if index % 7 == 0 else -2
        samples.append((wobble, wobble, 1000 + wobble, 1, -1, 0))
    return {"window_id": "w1", "hz": hz, "worn": worn, "samples": count,
            "data": encode(samples)}


def moving_window(reps=6, seconds=6.0, hz=50, amplitude=450, worn=True):
    """A repeated arm movement: `reps` cycles of real acceleration and rotation."""
    count = int(seconds * hz)
    samples = []
    for index in range(count):
        phase = 2 * math.pi * reps * index / count
        swing = math.sin(phase)
        samples.append((
            int(amplitude * swing),
            int(amplitude * 0.4 * math.cos(phase)),
            int(1000 + amplitude * 0.3 * swing),
            int(9000 * math.cos(phase)),      # 90 deg/s peak
            int(3000 * swing),
            0,
        ))
    return {"window_id": "w2", "hz": hz, "worn": worn, "samples": count,
            "data": encode(samples)}


# --------------------------------------------------------------- decoding ---

def test_a_window_decodes_to_the_units_the_maths_expects():
    window = imu.parse_window(still_window(seconds=1.0))
    assert window is not None
    assert window.samples == 50
    assert window.seconds == pytest.approx(1.0)
    ax, ay, az = window.accel_g[0]
    assert az == pytest.approx(1.003, abs=0.01), "milli-g must arrive as g"


def test_a_window_with_no_samples_is_not_a_window():
    assert imu.parse_window({"window_id": "w", "data": ""}) is None
    assert imu.parse_window({"window_id": "w", "data": "!!!not base64"}) is None


# --------------------------------------------------------------- analysis ---

def test_a_still_wrist_reads_as_still():
    features = imu.analyse(imu.parse_window(still_window()))
    assert features["still"] is True
    assert features["intensity"] == "still"
    assert features["reps"] == 0


def test_a_repeated_movement_is_counted_and_timed():
    features = imu.analyse(imu.parse_window(moving_window(reps=6, seconds=6.0)))
    assert features["reps"] == 6, features
    assert features["cadence_per_min"] == pytest.approx(60.0, abs=5)
    assert features["still"] is False
    assert features["swept_degrees"] > 200, "a real movement sweeps real degrees"


def test_intensity_separates_a_gentle_movement_from_a_vigorous_one():
    gentle = imu.analyse(imu.parse_window(moving_window(amplitude=120)))
    vigorous = imu.analyse(imu.parse_window(moving_window(amplitude=2500)))
    assert gentle["intensity"] in {"gentle", "moderate"}
    assert vigorous["intensity"] == "vigorous"


def test_an_impact_is_flagged_rather_than_counted_as_exercise():
    window = still_window()
    samples = [(0, 0, 1000, 0, 0, 0)] * 200
    samples[100] = (9000, 3000, 2000, 0, 0, 0)  # ~9.7 g
    window["data"] = encode(samples)
    features = imu.analyse(imu.parse_window(window))
    assert features["saturated"] is True


# --------------------------------------------------------------- evidence ---

def test_a_still_window_tells_the_model_it_is_a_no_not_encouragement():
    text = imu.evidence_text(imu.analyse(imu.parse_window(still_window())))
    assert "THE WRIST WAS STILL" in text
    assert 'instruction_followed: \\"no\\"' in text or "instruction_followed" in text


def test_no_window_at_all_forbids_judging_the_instruction():
    text = imu.evidence_text(None, note="the band sent nothing")
    assert "NO motion data" in text
    assert "do not praise" in text.lower()
    assert "hand the turn back" in text.lower()


def test_an_unworn_band_is_not_a_person_exercising():
    text = imu.evidence_text(imu.analyse(imu.parse_window(still_window(worn=False))))
    assert "NOT being worn" in text
    assert "Judge nothing" in text


def test_the_evidence_reports_measurements_not_impressions():
    features = imu.analyse(imu.parse_window(moving_window(reps=4, seconds=4.0)))
    text = imu.evidence_text(features)
    assert "distinct movements counted: 4" in text
    assert "degrees" in text and "RMS" in text
    assert "computed values, not" in text


# ------------------------------------------------------------------ store ---

def test_a_window_waits_for_the_turn_that_asks_for_it():
    store = imu.ImuWindowStore()
    window = imu.parse_window(moving_window())
    assert store.offer(window) is True
    taken, reason = store.take(timeout=0.0)
    assert taken is window and reason == ""


def test_taking_it_twice_yields_nothing_the_second_time():
    store = imu.ImuWindowStore()
    store.offer(imu.parse_window(moving_window()))
    store.take(timeout=0.0)
    taken, reason = store.take(timeout=0.05)
    assert taken is None
    assert "did not send" in reason


def test_identical_samples_twice_running_are_a_stuck_sensor():
    """The IMU shape of the frozen-camera bug, refused for the same reason."""
    store = imu.ImuWindowStore()
    first = moving_window()
    store.offer(imu.parse_window(first))
    assert store.take(timeout=0.0)[0] is not None

    repeat = dict(first, window_id="w-different-id")  # new id, same bytes
    store.offer(imu.parse_window(repeat))
    taken, reason = store.take(timeout=0.0)
    assert taken is None
    assert "stuck" in reason
    assert "holding perfectly still" in reason


def test_a_genuinely_new_window_is_accepted_after_a_repeat():
    store = imu.ImuWindowStore()
    store.offer(imu.parse_window(moving_window()))
    store.take(timeout=0.0)
    store.offer(imu.parse_window(moving_window()))
    store.take(timeout=0.0)  # refused as a repeat
    store.offer(imu.parse_window(still_window()))
    taken, reason = store.take(timeout=0.0)
    assert taken is not None and reason == ""


def test_clearing_the_store_forgets_the_previous_session():
    store = imu.ImuWindowStore()
    store.offer(imu.parse_window(moving_window()))
    store.take(timeout=0.0)
    store.clear()
    # The same samples are now evidence again: a new session did not see them.
    store.offer(imu.parse_window(moving_window()))
    taken, _reason = store.take(timeout=0.0)
    assert taken is not None


# ------------------------------------------------------- the wire contract ---

def test_the_firmware_wire_vector_decodes_to_the_right_physical_units():
    """The other half of `firmware/tests/test_imu_wire.cpp`.

    Both sides assert this exact base64 string. A disagreement about scale or
    byte order would not fail loudly -- it would produce plausible motion
    evidence that is wrong, and a care model would then judge somebody's
    exercise against it. So the contract is pinned as a literal on both sides
    rather than as a round trip inside one language.

    Two samples: (1, -1, 1000, 0, 0, 0) and (258, 0, 0, 0, 0, -2), in wire
    units of milli-g and centi-deg/s.
    """
    window = imu.parse_window({
        "window_id": "wire", "hz": 50, "worn": True,
        "data": "AQD//+gDAAAAAAAAAgEAAAAAAAAAAP7/",
    })
    assert window is not None
    assert window.samples == 2

    first_accel = window.accel_g[0]
    assert first_accel[0] == pytest.approx(0.001)
    assert first_accel[1] == pytest.approx(-0.001)
    assert first_accel[2] == pytest.approx(1.0), "1000 milli-g is one g"

    assert window.accel_g[1][0] == pytest.approx(0.258)
    assert window.gyro_dps[1][2] == pytest.approx(-0.02), "centi-deg/s"


def test_the_declared_scale_is_the_one_the_firmware_sends():
    """The board states its scale in every window; the defaults must match it."""
    assert imu.ACCEL_SCALE_G == 0.001
    assert imu.GYRO_SCALE_DPS == 0.01
    assert imu.DEFAULT_HZ == 50.0


def test_a_board_that_declares_a_different_scale_is_believed():
    """The firmware knows what it actually did; the default is only a default."""
    window = imu.parse_window({
        "window_id": "w", "hz": 100, "worn": True,
        "scale": {"accel_g": 0.002, "gyro_dps": 0.05},
        "data": "AQD//+gDAAAAAAAAAgEAAAAAAAAAAP7/",
    })
    assert window.hz == 100
    assert window.accel_g[0][2] == pytest.approx(2.0)


# ------------------------------------------------- the hold, out loud --------

def test_a_hold_beeps_and_records_and_takes_real_time():
    """The "beep boop" is the point: a hold the person cannot hear is a pause.

    All three have to happen together -- the countdown plays, the wrist is
    recorded across it, and it occupies the real number of seconds -- because
    each one alone is a different broken routine: silent timing, an unrecorded
    hold, or an instant "hold" nobody could follow.
    """
    import time as _time

    from kiki_gateway.care import cadence

    played, captured = [], []
    cadence.set_audio_player(lambda path, blocking: played.append((path, blocking)))
    cadence.set_motion_capture(captured.append)
    try:
        started = _time.monotonic()
        assert cadence.play_countdown(2) is True
        elapsed = _time.monotonic() - started
    finally:
        cadence.set_audio_player(None)
        cadence.set_motion_capture(None)

    assert played and played[0][0].endswith(".wav")
    assert played[0][1] is True, "the hold blocks; the microphone stays shut"
    assert captured and captured[0] >= 2.0, "the window covers the whole hold"
    assert 1.9 <= elapsed <= 3.5, f"a 2 s hold took {elapsed:.2f}s"


def test_a_hold_with_no_speaker_still_takes_the_time():
    """Degrading to a silent hold is safe; degrading to no hold is not."""
    import time as _time

    from kiki_gateway.care import cadence

    cadence.set_audio_player(None)
    cadence.set_motion_capture(None)
    started = _time.monotonic()
    assert cadence.play_countdown(1) is True
    assert _time.monotonic() - started >= 0.9


def test_the_countdown_track_is_as_long_as_the_hold():
    import wave

    from kiki_gateway.care import cadence

    path = cadence.countdown_track(4)
    with wave.open(path, "rb") as handle:
        seconds = handle.getnframes() / handle.getframerate()
    assert 3.9 <= seconds <= 4.6, f"a 4 s countdown rendered {seconds:.2f}s"


def test_every_named_cue_renders():
    from kiki_gateway.care import cadence

    for name in cadence.CUE_NAMES:
        assert cadence.cue_track(name), f"{name} produced no audio"
    assert cadence.cue_track("not-a-cue") is None
