"""On-demand MAX30102 measurement, driven over the WebSocket.

The RPi owns its sensor: `max30102_read.py` talks to an I2C device on the same
machine, and the care tool calls it directly. Here the sensor is on the band,
forty milliseconds and one Wi-Fi hop away, and the firmware already knows how
to run a capture -- `wearable_request_measurement()` in `kiki_wearable.cpp`
drives contact detection, stillness gating, the ten-second window and the
quality verdict, and the Settings screen has been using it for weeks.

So this module does not measure anything. It asks the board to, follows the
`measurement_status` events it sends back, and turns the result into the same
structured shape the care agent already knows how to talk about.

Three rules it exists to enforce, all of them the same rule:

* **A BPM exists only if the board reported one.** A timeout, a disconnect, a
  board too old to understand the command -- each returns an explicit failure.
  On 2026-08-29 the RPi said "78 BPM" when no trusted reading existed at all;
  the shape of that bug is a number produced by anything other than a sensor.
* **Only GOOD/FAIR is a reading.** Everything else is an attempt, logged as an
  attempt, and never promoted into trusted history or a trend.
* **SpO2 stays experimental.** The MAX30102 here is uncalibrated. It may be
  shown as a personal trend and must never drive an urgent claim.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional


# States the firmware reports, mirroring `WearableMeasurementState`.
IDLE = "idle"
WAITING_FOR_CONTACT = "waiting_for_contact"
WAITING_FOR_STILLNESS = "waiting_for_stillness"
MEASURING = "measuring"
COMPLETE = "complete"
FAILED = "failed"

TERMINAL = {COMPLETE, FAILED}

# Contact + stillness + a ten-second window, with room for a retry inside the
# firmware. Past this the person has been holding still for long enough that
# asking them again is kinder than waiting.
DEFAULT_TIMEOUT_SECONDS = 75.0

TRUSTED_QUALITIES = {"GOOD", "FAIR"}


class BoardHeartRate:
    """Drives one measurement at a time on the connected board."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sender: Optional[Callable[[str, Dict[str, Any]], bool]] = None
        self._status: Dict[str, Any] = {"state": IDLE}
        self._updated = threading.Event()
        self._active = False

    # -- wiring ------------------------------------------------------------
    def set_sender(self, sender: Optional[Callable[[str, Dict[str, Any]], bool]]) -> None:
        """Register `sender(event_type, fields) -> accepted`.

        The gateway session registers its own event sender at connect and
        clears it at disconnect, so "no board" is a state this module can see
        rather than a message into a closed socket.
        """
        with self._lock:
            self._sender = sender

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._sender is not None

    # -- events from the board ---------------------------------------------
    def note_status(self, event: Dict[str, Any]) -> None:
        """Consume one `measurement_status` device event."""
        state = str(event.get("state") or "").strip().lower()
        if not state:
            return
        with self._lock:
            self._status = {
                "state": state,
                "progress_percent": event.get("progress_percent"),
                "worn": event.get("worn"),
                "sensor_available": event.get("sensor_available"),
                "bpm": event.get("heart_rate") or event.get("bpm"),
                "spo2_experimental": event.get("spo2_experimental"),
                "quality": str(event.get("quality") or "NONE").upper(),
                "reason": str(event.get("reason") or "")[:200],
                "at": time.time(),
            }
        self._updated.set()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    # -- the measurement ---------------------------------------------------
    def measure(self, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                progress: Optional[Callable[[Dict[str, Any]], None]] = None
                ) -> Dict[str, Any]:
        """Ask the board for one reading. Blocking; call it off the event loop.

        Returns a structured result. `status` is one of `trusted_reading`,
        `poor_quality`, `no_contact`, `unavailable`, `timeout`, `busy`.
        """
        with self._lock:
            if self._active:
                return {"status": "busy",
                        "reason": "a measurement is already running"}
            sender = self._sender
            self._active = True
            self._status = {"state": WAITING_FOR_CONTACT}
        self._updated.clear()
        try:
            if sender is None:
                return {"status": "unavailable",
                        "reason": "no band is connected right now"}
            try:
                accepted = bool(sender("wearable_measure", {"action": "start"}))
            except Exception as exc:
                return {"status": "unavailable",
                        "reason": f"could not reach the band: {str(exc)[:120]}"}
            if not accepted:
                return {"status": "unavailable",
                        "reason": "the band did not accept the measurement request"}

            deadline = time.monotonic() + max(5.0, timeout)
            last_state = ""
            while time.monotonic() < deadline:
                self._updated.wait(0.5)
                self._updated.clear()
                current = self.status()
                state = str(current.get("state") or "")
                if state and state != last_state:
                    last_state = state
                    if progress is not None:
                        try:
                            progress(current)
                        except Exception:
                            pass
                if state in TERMINAL:
                    return self._result(current)
            return {"status": "timeout",
                    "reason": "the band did not finish the measurement in time",
                    "last_state": last_state or "unknown"}
        finally:
            with self._lock:
                self._active = False

    def cancel(self) -> Dict[str, Any]:
        with self._lock:
            sender = self._sender
        if sender is None:
            return {"status": "unavailable", "reason": "no band is connected"}
        try:
            sender("wearable_measure", {"action": "cancel"})
        except Exception as exc:
            return {"status": "error", "reason": str(exc)[:160]}
        return {"status": "cancelled"}

    @staticmethod
    def _result(status: Dict[str, Any]) -> Dict[str, Any]:
        quality = str(status.get("quality") or "NONE").upper()
        try:
            bpm = float(status.get("bpm") or 0)
        except (TypeError, ValueError):
            bpm = 0.0
        if status.get("state") == FAILED or bpm <= 0:
            if status.get("worn") is False:
                return {"status": "no_contact", "quality": quality,
                        "reason": status.get("reason")
                        or "the band was not in contact with the skin"}
            return {"status": "poor_quality", "quality": quality,
                    "reason": status.get("reason")
                    or "the signal was not good enough to report a rate"}
        if quality not in TRUSTED_QUALITIES:
            # A number the firmware itself would not stand behind. Reporting it
            # anyway is the exact failure this whole module is shaped around.
            return {"status": "poor_quality", "quality": quality, "bpm": None,
                    "reason": "the band reported the signal quality as "
                              f"{quality or 'NONE'}, so this is not a reading"}
        result: Dict[str, Any] = {
            "status": "trusted_reading",
            "bpm": round(bpm, 1),
            "quality": quality,
            "site": "wrist",
            "signal": {"source": "max30102", "estimator_version": 2},
        }
        try:
            spo2 = float(status.get("spo2_experimental") or 0)
        except (TypeError, ValueError):
            spo2 = 0.0
        if spo2 > 0:
            result["spo2_experimental"] = round(spo2, 1)
            result["spo2_note"] = (
                "Uncalibrated and experimental. Personal trend only; never "
                "present it as a clinical oxygen level or act on it alone.")
        return result


_CONTROLLER = BoardHeartRate()


def get_heart_rate_controller() -> BoardHeartRate:
    return _CONTROLLER
