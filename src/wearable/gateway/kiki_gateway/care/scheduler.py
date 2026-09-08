"""The care schedule, timed by this package rather than by the worker engine.

WHY THIS EXISTS, in one paragraph, because it cost a morning.

`scheduling.SeniorCareManager` materialised each routine as a generic worker
via `WorkerManager.create_worker(task_description=...)`. On the RPi that is
safe: its worker engine recognises a `senior:` worker and hands it to
`execute_scheduled_routine`, which only opens session state and queues the voice
loop. The engine in *this* gateway is an older snapshot with no such dispatch
and no `set_care_session_callback`, so a care routine was simply an LLM task.
It ran the cloud brain, the hourly cloud cap was already spent, the call
returned empty, the worker was marked FAILED -- and this engine also predates
the fix for retrying failed workers, so it retried forever. Measured on
2026-09-07: **3,351 firings of one routine**, each injecting its failure into
chat history, which grew to 1,134 messages / 42,709 tokens against an 8,192
token window. Every request the local model saw was then rejected, so every
single thing Kiki said became "I'm having trouble answering right now."

So care timing is owned here. The properties that matter:

* **It never calls a model.** Firing means handing an event id to the foreground
  voice loop. That is the whole action.
* **It never retries.** A routine that could not be spoken is skipped to its
  next occurrence. There is no failure state that can accumulate, which is what
  makes a storm structurally impossible rather than merely unlikely.
* **It fires an occurrence at most once**, keyed by the occurrence rather than
  by a timer, and that key is persisted -- so a restart does not re-fire this
  morning's routine, and a routine missed while the process was down is
  recorded as missed rather than shouted late.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


LOG = logging.getLogger(__name__)

# How often the table is re-examined. Care times are minutes, not seconds, so
# this is deliberately lazy: it costs one dict comparison per routine.
TICK_SECONDS = 20.0

# A routine due more than this long ago is recorded as missed instead of fired.
# Somebody who missed their 09:00 stretch does not want it at 14:00, and a
# process that was down all morning must not wake up and run five of them.
DEFAULT_GRACE_SECONDS = 600


def _state_path() -> Path:
    from .plan import get_care_plan_path

    return get_care_plan_path().with_name("schedule_state.json")


def _parse_hhmm(value: str) -> Optional[tuple]:
    try:
        hour, minute = str(value).strip().split(":")[:2]
        hour, minute = int(hour), int(minute)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def occurrence(item: Dict[str, Any], now: datetime) -> Optional[tuple]:
    """`(key, due_at)` for the occurrence of `item` that is current at `now`.

    The key identifies the OCCURRENCE, not the timer: "2026-09-07 09:00" rather
    than "the third tick". That is what makes firing idempotent across restarts
    and across a re-synced schedule.
    """
    schedule = item.get("schedule") or {}
    kind = str(schedule.get("kind") or "")
    value = schedule.get("value")
    item_id = str(item.get("id") or "")

    if kind == "daily":
        parsed = _parse_hhmm(value)
        if parsed is None:
            return None
        hour, minute = parsed
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due > now:
            due -= timedelta(days=1)  # the occurrence we are currently inside
        return f"{item_id}@{due:%Y-%m-%d %H:%M}", due

    if kind == "once":
        try:
            due = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return f"{item_id}@once:{due.isoformat()}", due

    if kind == "recurring":
        try:
            seconds = int(float(value))
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None
        # Anchored to the epoch so the key is stable across restarts.
        index = int(now.timestamp()) // seconds
        due = datetime.fromtimestamp(index * seconds)
        return f"{item_id}@every{seconds}:{index}", due

    return None


class CareScheduler:
    """Times the care plan. One thread, no model, no retries."""

    def __init__(self, plan, starter: Optional[Callable[[str], None]] = None,
                 grace_seconds: int = DEFAULT_GRACE_SECONDS,
                 busy: Optional[Callable[[], bool]] = None) -> None:
        self.plan = plan
        self.starter = starter
        self.grace_seconds = int(grace_seconds)
        self.busy = busy
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._fired: Dict[str, str] = self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> Dict[str, str]:
        try:
            return json.loads(_state_path().read_text())
        except Exception:
            return {}

    def _save(self) -> None:
        try:
            path = _state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            # Keep it small: only the most recent occurrences matter, and an
            # unbounded file is a slow leak nobody would ever look at.
            if len(self._fired) > 200:
                newest = sorted(self._fired.items(), key=lambda kv: kv[1])[-100:]
                self._fired = dict(newest)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._fired, indent=2))
            os.replace(tmp, path)
        except Exception:
            LOG.exception("could not persist the care schedule state")

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> int:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.count()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="kiki-care-schedule",
                                            daemon=True)
            self._thread.start()
        due = self.count()
        LOG.info("care schedule armed: %d routine(s)", due)
        return due

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def count(self) -> int:
        try:
            return len(self.plan.all_active_schedules())
        except Exception:
            LOG.exception("could not read the care schedule")
            return 0

    # -- the tick ----------------------------------------------------------
    def _run(self) -> None:
        # Everything a routine could have been due for while the process was
        # down is marked seen before the first tick, so a restart cannot fire a
        # backlog. `check()` still fires anything genuinely due right now.
        self.check(catch_up=True)
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.check()
            except Exception:
                LOG.exception("care schedule tick failed")

    def check(self, now: Optional[datetime] = None, catch_up: bool = False) -> List[str]:
        """Fire every routine whose current occurrence is due. Returns what fired."""
        now = now or datetime.now()
        fired: List[str] = []
        try:
            items = self.plan.all_active_schedules()
        except Exception:
            LOG.exception("could not read the care schedule")
            return fired

        for item in items:
            slot = occurrence(item, now)
            if slot is None:
                continue
            key, due = slot
            if due > now:
                continue
            with self._lock:
                if key in self._fired:
                    continue
            late = (now - due).total_seconds()

            if catch_up or late > self.grace_seconds:
                # Too late to be useful. Record it so it cannot fire later, and
                # say so once -- a missed routine is worth a line in the log and
                # nothing else. This is also the ONLY thing that happens to a
                # routine that could not run: there is no retry, ever.
                with self._lock:
                    self._fired[key] = now.isoformat()
                self._save()
                if not catch_up:
                    LOG.info("care routine %r missed by %.0f min; skipped to its "
                             "next occurrence", item.get("title") or item.get("id"),
                             late / 60)
                continue

            if self.busy is not None:
                try:
                    if self.busy():
                        # A session is already talking to them. Leave the
                        # occurrence unfired and look again next tick -- this is
                        # a deferral, not a failure, so nothing accumulates.
                        continue
                except Exception:
                    LOG.exception("could not check whether care is busy")

            with self._lock:
                self._fired[key] = now.isoformat()
            self._save()
            fired.append(key)
            self._fire(item)
        return fired

    def _fire(self, item: Dict[str, Any]) -> None:
        event_id = str(item.get("id") or "")
        title = item.get("title") or item.get("name") or item.get("message") or event_id
        if self.starter is None:
            LOG.warning("care routine %r is due but no voice loop is connected", title)
            return
        LOG.info("care routine due: %r (%s)", title, event_id)
        try:
            self.starter(event_id)
        except Exception:
            # Swallowed on purpose. A failure to speak must not become a
            # retried, accumulating error -- that is the exact shape of the
            # storm this module replaced.
            LOG.exception("could not hand care routine %r to the foreground", title)

    # -- what the tools ask ------------------------------------------------
    def receipt(self, item_id: str) -> Dict[str, Any]:
        """The verified next trigger for one routine, for `get_care_schedule_status`."""
        item_id = str(item_id or "").strip()
        now = datetime.now()
        try:
            items = self.plan.all_active_schedules()
        except Exception:
            items = []
        for item in items:
            if str(item.get("id")) != item_id:
                continue
            slot = occurrence(item, now)
            if slot is None:
                return {"status": "error", "item_id": item_id, "scheduled": False,
                        "reason": "the schedule could not be read"}
            key, due = slot
            nxt = due if due > now else self._next_after(item, now)
            return {
                "status": "ok", "item_id": item_id, "scheduled": True,
                "title": item.get("title") or item.get("name") or item.get("message"),
                "schedule": item.get("schedule"),
                "next_trigger": nxt.isoformat() if nxt else None,
                "already_fired_this_occurrence": key in self._fired,
                "timed_by": "care scheduler",
            }
        return {"status": "not_scheduled", "item_id": item_id, "scheduled": False,
                "reason": "no enabled care item with that id"}

    @staticmethod
    def _next_after(item: Dict[str, Any], now: datetime) -> Optional[datetime]:
        schedule = item.get("schedule") or {}
        kind = str(schedule.get("kind") or "")
        if kind == "daily":
            parsed = _parse_hhmm(schedule.get("value"))
            if parsed is None:
                return None
            hour, minute = parsed
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return due if due > now else due + timedelta(days=1)
        if kind == "once":
            try:
                due = datetime.fromisoformat(str(schedule.get("value")))
            except (TypeError, ValueError):
                return None
            return due if due > now else None
        if kind == "recurring":
            try:
                seconds = int(float(schedule.get("value")))
            except (TypeError, ValueError):
                return None
            if seconds <= 0:
                return None
            index = int(now.timestamp()) // seconds + 1
            return datetime.fromtimestamp(index * seconds)
        return None

    def is_scheduled(self, item_id: str) -> bool:
        return bool(self.receipt(item_id).get("scheduled"))
