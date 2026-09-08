"""Raw wrist motion as the care agent's evidence, in place of a camera frame.

The RPi conducts a guided exercise by attaching fresh camera frames to every
care turn and asking Gemma what the person's body is doing. This body has no
camera and does have a 50 Hz six-axis IMU strapped to the wrist, so the same
question is answered from motion instead: the board buffers the raw samples
taken *during the hold it was told to time*, ships the window when the hold
ends, and this module turns it into a short block of measured facts.

Why measured facts and not the raw array: handing 500 samples of accelerometer
data to a language model asks it to do signal processing in prose, and it will
happily oblige with numbers it did not compute. Everything the model is told
here -- rep count, cadence, swept angle, stillness, wear -- comes out of the
arithmetic below, so a claim in the transcript can be traced to a sample.

The failure this design inherits is the one the vision path was repaired for on
2026-09-02: a model that writes the praise first and the observation afterwards
will confirm six asanas in a row while nobody moves. The structural answer is
the same and lives in `agent.py` -- observation before verdict, a verdict that
is checked in code -- and the answer here is that **absent evidence is stated,
never inferred**. No window means "I could not feel any movement", and an
identical window twice running means the sensor is stuck, not that the person
held perfectly still.
"""

from __future__ import annotations

import base64
import hashlib
import math
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# The board samples at 50 Hz (`kiki_motion.cpp`, kSamplePeriodMs = 20). Both
# sides agree on this constant; a window that reports something else is trusted
# over the default, because the firmware knows what it actually did.
DEFAULT_HZ = 50.0

# Wire scaling. Accelerometer in milli-g, gyro in centi-degrees/s, both int16 —
# +-32 g and +-327 deg/s of headroom, which covers the 8 g / 1024 dps ranges the
# QMI8658 is configured for at the resolution any of this needs.
ACCEL_SCALE_G = 0.001
GYRO_SCALE_DPS = 0.01

# Below this the wrist is doing nothing. Chosen against the accelerometer noise
# floor with the board resting on a table (~0.01 g RMS) with margin, so "still"
# means still rather than "quiet room".
STILL_ACCEL_RMS_G = 0.035
STILL_GYRO_RMS_DPS = 8.0

# A rep is a peak in the smoothed acceleration magnitude that clears this much
# above the local baseline. Small enough for a slow neck turn, large enough
# that a hand resting on a table does not count breathing as exercise.
REP_PROMINENCE_G = 0.12
# Two peaks closer together than this are one movement seen twice. 0.35 s is
# ~170 reps/min, past anything a guided routine asks for.
REP_MIN_SEPARATION_S = 0.35

# |a| beyond this is either a real impact or a clipped sample; either way the
# window is not a clean record of an exercise.
SATURATION_G = 7.5


def _decode_i16x6(payload: str) -> List[Tuple[int, int, int, int, int, int]]:
    raw = base64.b64decode(payload, validate=False)
    count = len(raw) // 12
    if count <= 0:
        return []
    values = struct.unpack_from("<" + "h" * (count * 6), raw, 0)
    return [tuple(values[i * 6:i * 6 + 6]) for i in range(count)]  # type: ignore[misc]


@dataclass(frozen=True)
class ImuWindow:
    """One decoded capture window. Immutable: it is evidence."""

    window_id: str
    hz: float
    worn: bool
    accel_g: List[Tuple[float, float, float]]
    gyro_dps: List[Tuple[float, float, float]]
    received_at: float = field(default_factory=time.time)
    note: str = ""

    @property
    def samples(self) -> int:
        return len(self.accel_g)

    @property
    def seconds(self) -> float:
        return self.samples / self.hz if self.hz > 0 else 0.0

    @property
    def digest(self) -> str:
        """Identity of the DATA, not of the message.

        A board that re-sends its previous buffer produces a new `window_id`
        and identical samples. The camera path learned this the hard way: a
        wedged frame server kept answering 200 with its last JPEG and the care
        agent read it as the person holding a position beautifully.
        """
        hasher = hashlib.sha256()
        for (ax, ay, az), (gx, gy, gz) in zip(self.accel_g, self.gyro_dps):
            hasher.update(struct.pack("<6f", ax, ay, az, gx, gy, gz))
        return hasher.hexdigest()


def parse_window(event: Dict[str, Any]) -> Optional[ImuWindow]:
    """Decode an `imu_window` device event. None when it carries no samples."""
    try:
        hz = float(event.get("hz") or DEFAULT_HZ)
    except (TypeError, ValueError):
        hz = DEFAULT_HZ
    if hz <= 0:
        hz = DEFAULT_HZ
    scale = event.get("scale") if isinstance(event.get("scale"), dict) else {}
    try:
        accel_scale = float(scale.get("accel_g", ACCEL_SCALE_G))
        gyro_scale = float(scale.get("gyro_dps", GYRO_SCALE_DPS))
    except (TypeError, ValueError):
        accel_scale, gyro_scale = ACCEL_SCALE_G, GYRO_SCALE_DPS
    payload = str(event.get("data") or "")
    if not payload:
        return None
    try:
        rows = _decode_i16x6(payload)
    except Exception:
        return None
    if not rows:
        return None
    accel = [(ax * accel_scale, ay * accel_scale, az * accel_scale)
             for ax, ay, az, _gx, _gy, _gz in rows]
    gyro = [(gx * gyro_scale, gy * gyro_scale, gz * gyro_scale)
            for _ax, _ay, _az, gx, gy, gz in rows]
    return ImuWindow(
        window_id=str(event.get("window_id") or ""),
        hz=hz,
        worn=bool(event.get("worn", True)),
        accel_g=accel,
        gyro_dps=gyro,
        note=str(event.get("note") or "")[:200],
    )


def _magnitude(rows: List[Tuple[float, float, float]]) -> List[float]:
    return [math.sqrt(x * x + y * y + z * z) for x, y, z in rows]


def _rms(values: List[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _smooth(values: List[float], width: int) -> List[float]:
    """Centred moving average. Rep counting on raw 50 Hz data finds noise."""
    if width <= 1 or len(values) < width:
        return list(values)
    out: List[float] = []
    half = width // 2
    for index in range(len(values)):
        low = max(0, index - half)
        high = min(len(values), index + half + 1)
        window = values[low:high]
        out.append(sum(window) / len(window))
    return out


def count_reps(magnitude: List[float], hz: float) -> Tuple[int, List[float]]:
    """Peaks in the smoothed |a| that clear REP_PROMINENCE_G above baseline.

    Returns the count and the peak times, so a caller can report cadence and a
    test can point at which movements were counted.
    """
    if hz <= 0 or len(magnitude) < 5:
        return 0, []
    smoothed = _smooth(magnitude, max(3, int(round(hz * 0.3))))
    baseline = _mean(smoothed)
    min_gap = max(1, int(round(REP_MIN_SEPARATION_S * hz)))
    peaks: List[int] = []
    for index in range(1, len(smoothed) - 1):
        value = smoothed[index]
        if value < baseline + REP_PROMINENCE_G:
            continue
        if value < smoothed[index - 1] or value < smoothed[index + 1]:
            continue
        if peaks and index - peaks[-1] < min_gap:
            # Keep the taller of two movements too close together to be
            # separate ones.
            if value > smoothed[peaks[-1]]:
                peaks[-1] = index
            continue
        peaks.append(index)
    return len(peaks), [round(index / hz, 2) for index in peaks]


def _tilt_degrees(vector: Tuple[float, float, float]) -> Tuple[float, float]:
    """Pitch and roll of the gravity direction, in degrees.

    Only meaningful while the wrist is roughly still -- during a movement the
    accelerometer reads gravity plus whatever the arm is doing -- which is
    exactly when it is used: the start and end of a hold.
    """
    x, y, z = vector
    pitch = math.degrees(math.atan2(-x, math.sqrt(y * y + z * z) or 1e-9))
    roll = math.degrees(math.atan2(y, z if abs(z) > 1e-9 else 1e-9))
    return round(pitch, 1), round(roll, 1)


def analyse(window: ImuWindow) -> Dict[str, Any]:
    """Everything the care agent is allowed to be told about this window.

    Pure arithmetic over the samples. Nothing here guesses at intent, names an
    exercise, or decides whether an instruction was followed -- that judgement
    belongs to the model, and it has to be made against these numbers rather
    than against the instruction it just gave.
    """
    accel_mag = _magnitude(window.accel_g)
    gyro_mag = _magnitude(window.gyro_dps)
    seconds = window.seconds
    dynamic = [value - 1.0 for value in accel_mag]  # gravity removed, crudely
    accel_rms = _rms([value - _mean(accel_mag) for value in accel_mag])
    gyro_rms = _rms(gyro_mag)
    reps, peak_times = count_reps(accel_mag, window.hz)

    # Total angular path: the integral of |gyro| over the window. This is the
    # honest version of "range of motion" from a single wrist sensor -- it says
    # how much rotation happened, not what shape it made.
    swept_degrees = sum(gyro_mag) / window.hz if window.hz > 0 else 0.0

    settle = max(1, int(round(window.hz * 0.5)))
    start_tilt = _tilt_degrees(tuple(
        _mean([row[axis] for row in window.accel_g[:settle]]) for axis in range(3)
    ))  # type: ignore[arg-type]
    end_tilt = _tilt_degrees(tuple(
        _mean([row[axis] for row in window.accel_g[-settle:]]) for axis in range(3)
    ))  # type: ignore[arg-type]

    peak_g = max(accel_mag) if accel_mag else 0.0
    saturated = peak_g >= SATURATION_G
    still = accel_rms <= STILL_ACCEL_RMS_G and gyro_rms <= STILL_GYRO_RMS_DPS

    if still:
        intensity = "still"
    elif accel_rms < 0.12 and gyro_rms < 45:
        intensity = "gentle"
    elif accel_rms < 0.35 and gyro_rms < 160:
        intensity = "moderate"
    else:
        intensity = "vigorous"

    return {
        "seconds": round(seconds, 2),
        "samples": window.samples,
        "hz": window.hz,
        "worn": bool(window.worn),
        "reps": reps,
        "peak_times_s": peak_times,
        "cadence_per_min": round(reps / seconds * 60.0, 1) if seconds > 0 else 0.0,
        "swept_degrees": round(swept_degrees, 1),
        "accel_rms_g": round(accel_rms, 3),
        "gyro_rms_dps": round(gyro_rms, 1),
        "peak_accel_g": round(peak_g, 2),
        "dynamic_peak_g": round(max((abs(v) for v in dynamic), default=0.0), 2),
        "start_tilt_deg": {"pitch": start_tilt[0], "roll": start_tilt[1]},
        "end_tilt_deg": {"pitch": end_tilt[0], "roll": end_tilt[1]},
        "tilt_change_deg": round(
            math.sqrt((end_tilt[0] - start_tilt[0]) ** 2
                      + (end_tilt[1] - start_tilt[1]) ** 2), 1),
        "still": still,
        "intensity": intensity,
        "saturated": saturated,
    }


# The block that goes into the prompt. Written as measurements with units, in
# the order a person reading it would want them, and it always says which kind
# of evidence it is: RAW when the board sent samples, and never anything else
# dressed up to look like it.
def evidence_text(features: Optional[Dict[str, Any]], note: str = "") -> str:
    if not features:
        return (
            "NO motion data is attached to this request. You cannot tell whether "
            "the person moved, held a position, or did nothing at all. Say so "
            "plainly and hand the turn back to them. Do NOT judge the previous "
            "instruction, do not praise, and do not start the next movement."
            + (f" ({note})" if note else "")
        )
    if not features.get("worn", True):
        return (
            "The motion window arrived but the band reports it is NOT being "
            "worn, so these samples are of a device lying somewhere, not of a "
            "person exercising. Ask them to put it back on the wrist before "
            "continuing. Judge nothing from this window."
        )
    tilt_start = features.get("start_tilt_deg", {})
    tilt_end = features.get("end_tilt_deg", {})
    lines = [
        f"RAW wrist motion, measured on the band over the last "
        f"{features.get('seconds')} s at {int(features.get('hz', 0))} Hz "
        f"({features.get('samples')} samples). These are computed values, not "
        f"impressions:",
        f"- distinct movements counted: {features.get('reps')}"
        + (f" at {features.get('cadence_per_min')} per minute"
           if features.get("reps") else ""),
        f"- total rotation swept: {features.get('swept_degrees')} degrees",
        f"- movement intensity: {features.get('intensity')} "
        f"(acceleration {features.get('accel_rms_g')} g RMS, rotation "
        f"{features.get('gyro_rms_dps')} deg/s RMS)",
        f"- wrist angle at the start: pitch {tilt_start.get('pitch')}, roll "
        f"{tilt_start.get('roll')}; at the end: pitch {tilt_end.get('pitch')}, "
        f"roll {tilt_end.get('roll')} (moved {features.get('tilt_change_deg')} "
        f"degrees overall)",
    ]
    if features.get("still"):
        lines.append(
            "- THE WRIST WAS STILL for this whole window. If you asked for a "
            "movement, it did not happen: this is `instruction_followed: \"no\"`, "
            "not encouragement. If you asked them to HOLD a position, this is "
            "what holding it looks like."
        )
    if features.get("saturated"):
        lines.append(
            f"- a {features.get('peak_accel_g')} g spike is in this window, which "
            f"is an impact or a knock rather than exercise. Check they are all "
            f"right before continuing."
        )
    if note:
        lines.append(f"- {note}")
    return "\n".join(lines)


class ImuWindowStore:
    """Where the board's windows wait for the next care turn.

    One slot, plus the digest of the one before it. There is exactly one care
    session at a time and each turn consumes at most one window, so a queue
    would only ever hold staleness. `take()` is destructive for the same reason
    the camera path clears its frames: a window read twice is a window claimed
    as evidence for a movement it never saw.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._window: Optional[ImuWindow] = None
        self._last_digest = ""
        self._waiters: List[threading.Event] = []

    def offer(self, window: Optional[ImuWindow]) -> bool:
        if window is None:
            return False
        with self._lock:
            self._window = window
            waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            waiter.set()
        return True

    def take(self, timeout: float = 0.0) -> Tuple[Optional[ImuWindow], str]:
        """The pending window, or (None, why-not) after waiting `timeout`.

        The second element is the reason, and it is written to be read by the
        model: every path out of here that has no data says what happened
        instead of returning an empty string that the prompt would render as
        silence.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._lock:
                window = self._window
                if window is not None:
                    self._window = None
                    digest = window.digest
                    repeat = digest == self._last_digest
                    self._last_digest = digest
                    if repeat:
                        return None, (
                            "The band sent the SAME motion samples as the previous "
                            "window, byte for byte. A sensor that repeats itself is "
                            "stuck, not a person holding perfectly still -- treat "
                            "this as no evidence at all."
                        )
                    return window, ""
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, (
                        "The band did not send a motion window in time."
                    )
                waiter = threading.Event()
                self._waiters.append(waiter)
            waiter.wait(min(remaining, 0.25))

    def clear(self) -> None:
        """Between sessions. The next session starts with no inherited history."""
        with self._lock:
            self._window = None
            self._last_digest = ""
            waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            waiter.set()


_STORE = ImuWindowStore()


def get_window_store() -> ImuWindowStore:
    return _STORE
