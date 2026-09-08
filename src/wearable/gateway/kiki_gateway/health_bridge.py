"""Background bridge between wearable events, the Pi store, and desktop context."""

from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Callable

import requests


LOG = logging.getLogger(__name__)


class WearableHealthBridge:
    def __init__(self, base_url: str, token: str = "", poll_seconds: float = 30.0,
                 on_update: Callable[[dict], None] | None = None,
                 context_cooldown_seconds: float = 900.0):
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "")
        self.poll_seconds = max(5.0, float(poll_seconds))
        self.on_update = on_update
        self.context_cooldown_seconds = max(0.0, float(context_cooldown_seconds))
        self._lock = threading.RLock()
        self._summary: dict = {}
        self._environment: dict = {}
        self._environment_received = 0.0
        self._accepted: dict = {}
        self._last_notified_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="wearable-health", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # First poll immediately: a gateway restart must not wait half a minute
        # before desktop Kiki knows the person's latest reading.
        while not self._stop.is_set():
            try:
                response = self._session.get(
                    f"{self.base_url}/api/health/v1/summary",
                    params={"days": 7}, headers=self._headers(), timeout=5,
                )
                response.raise_for_status()
                self._accept_summary(response.json())
                self.heartbeat()
            except Exception as exc:
                LOG.debug("wearable summary poll unavailable: %s", exc)
            try:
                response = self._session.get(
                    f"{self.base_url}/api/health/v1/environment",
                    headers=self._headers(), timeout=(2, 3),
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    with self._lock:
                        self._environment = payload
                        self._environment_received = time.monotonic()
            except Exception as exc:
                LOG.debug("panel environment unavailable: %s", exc)
            self._stop.wait(self.poll_seconds)

    @staticmethod
    def _hr_band(value: float | None) -> str:
        if value is None:
            return "none"
        if value < 50:
            return "low"
        if value <= 100:
            return "usual"
        if value <= 120:
            return "raised"
        return "high"

    def _material(self, summary: dict) -> bool:
        latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
        heart = latest.get("heart_rate") if isinstance(latest.get("heart_rate"), dict) else {}
        try:
            hr = float(heart["value"]) if heart.get("value") is not None else None
        except (TypeError, ValueError):
            hr = None
        try:
            steps = max(0, int(latest.get("steps_today", 0) or 0))
        except (TypeError, ValueError):
            steps = 0
        spo2_row = latest.get("spo2_experimental") \
            if isinstance(latest.get("spo2_experimental"), dict) else {}
        try:
            spo2 = float(spo2_row["value"]) if spo2_row.get("value") is not None else None
        except (TypeError, ValueError):
            spo2 = None
        advisory_ids = tuple(str(row.get("id")) for row in summary.get("advisories", [])
                             if isinstance(row, dict))
        fall = latest.get("fall") if isinstance(latest.get("fall"), dict) else {}
        candidate = {
            "hr": hr, "hr_band": self._hr_band(hr), "spo2": spo2,
            "step_bucket": steps // 500,
            "worn": latest.get("worn"), "activity": latest.get("activity"),
            "fresh": latest.get("fresh"),
            "fall": (fall.get("status"), fall.get("event_id")),
            "advisories": advisory_ids,
        }
        old = self._accepted
        changed = not old
        fall_changed = False
        if old:
            old_hr = old.get("hr")
            old_spo2 = old.get("spo2")
            fall_changed = candidate["fall"] != old.get("fall")
            changed = (
                (hr is None) != (old_hr is None)
                or (hr is not None and old_hr is not None and abs(hr - old_hr) >= 5)
                or candidate["hr_band"] != old.get("hr_band")
                or (spo2 is None) != (old_spo2 is None)
                or (spo2 is not None and old_spo2 is not None
                    and abs(spo2 - old_spo2) >= 2)
                or candidate["step_bucket"] != old.get("step_bucket")
                or candidate["worn"] != old.get("worn")
                or candidate["activity"] != old.get("activity")
                or candidate["fresh"] != old.get("fresh")
                or fall_changed
                or candidate["advisories"] != old.get("advisories")
            )
        if changed:
            now = time.monotonic()
            if (old and not fall_changed and
                    now - self._last_notified_at < self.context_cooldown_seconds):
                return False
            self._accepted = candidate
            self._last_notified_at = now
        return changed

    def _accept_summary(self, summary: object) -> None:
        if not isinstance(summary, dict):
            return
        callback = None
        with self._lock:
            self._summary = copy.deepcopy(summary)
            if self._material(summary):
                callback = self.on_update
        if callback:
            callback(copy.deepcopy(summary))

    def summary(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._summary)

    def environment(self) -> dict:
        with self._lock:
            # Link loss must never leave the panel claiming cached values are current.
            if time.monotonic() - self._environment_received > 120:
                return {"available": False, "state": "unavailable"}
            return copy.deepcopy(self._environment)

    def ingest(self, batch: dict) -> dict:
        if not self.enabled:
            raise RuntimeError("health service URL is not configured")
        # A cancelled websocket session's HTTP call may still be finishing
        # when its replacement starts. Give each ingest its own connection
        # rather than sharing the poller's mutable requests.Session.
        response = requests.post(
            f"{self.base_url}/api/health/v1/ingest", json=batch,
            # A confirmed fall may synchronously submit up to a few caregiver
            # emails. The firmware's durable queue is the retry boundary; do
            # not time out a Pi request that already claimed the alert id.
            headers=self._headers(), timeout=(3, 45),
        )
        response.raise_for_status()
        payload = response.json()
        summary = payload.get("summary")
        if isinstance(summary, dict):
            self._accept_summary(summary)
        return payload

    def record_alert_outcome(self, outcome: dict) -> None:
        if not self.enabled:
            return
        try:
            requests.post(
                f"{self.base_url}/api/health/v1/alert-outcomes", json=outcome,
                headers=self._headers(), timeout=5,
            ).raise_for_status()
        except Exception as exc:
            LOG.warning("could not record wearable alert outcome: %s", exc)

    def heartbeat(self, source: str = "desktop_kiki") -> None:
        if not self.enabled:
            return
        try:
            self._session.post(
                f"{self.base_url}/api/health/v1/heartbeats",
                json={"source": source, "at": time.time()}, headers=self._headers(), timeout=5,
            ).raise_for_status()
        except Exception:
            LOG.debug("health heartbeat unavailable", exc_info=True)
