"""One object the gateway talks to, and the switch that turns it all on.

Every integration point in `session.py` and `inference.py` reaches exactly one
function here -- `get_care_runtime()` -- and every one of them is guarded by
`mode.care_active()`. In `default` mode this module allocates nothing, starts no
thread, opens no file and makes no network request. That is the whole safety
story for the demo: the care stack is not "disabled", it is *not running*.

What it owns while it IS running:

* the care plan store (a local JSON file on this machine),
* the care manager, which materialises routine events into the worker scheduler
  the gateway already runs,
* the environment provider (Open-Meteo, no key, no Raspberry Pi),
* the wiring that lets the care agent time a hold on the board's speaker and
  read the board's IMU and heart-rate sensor.

Nothing here requires the RPi to be switched on. Wearable telemetry from the
band is stored locally by the same code the RPi's health service uses; the Pi
remains a *destination* the existing `health_bridge` posts to when it is up, and
its absence costs nothing.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import cadence, imu
from .mode import care_active, companion_active, environment_active


LOG = logging.getLogger(__name__)

# Handed to the care agent when a hold finished and the routine is continuing
# without waiting for an answer. The person is mid-movement; the next turn is
# Kiki's, and it must be obvious to the model that nobody spoke.
NO_REPLY = "[NO REPLY - CONTINUE THE ROUTINE YOURSELF]"


class CareRuntime:
    """The live `health_sih` stack. One per process, created on first use."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active = False
        self._core = None
        self._manager = None
        self._pending_event_id: Optional[str] = None
        self._last_care_line = ""
        # Set while a board is connected: hands a due care event to the
        # foreground voice lifecycle. A worker must never speak on its own
        # thread, so this is the whole of its involvement.
        self._starter: Optional[Callable[[str], None]] = None
        # Mirrors the two switches on the watch. The BOARD enforces them; this
        # copy exists so the care agent can say "movement checks are off"
        # instead of reporting a sensor that failed to answer, which is the
        # same sentence a broken one would produce.
        self._fall_alerts = True
        self._movement_checks = True

    # -- lifecycle ---------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    def attach_core(self, core) -> None:
        """Remember the runtime core, so activation can find the scheduler."""
        self._core = core

    def sync_to_mode(self) -> bool:
        """Activate or deactivate to match the mode. Returns True if it moved.

        Called wherever a mode switch is already noticed. Idempotent, cheap,
        and safe to call from any thread -- which matters, because the mode can
        change from a spoken `switch_mode`, from the Web UI, or from config.
        """
        wanted = care_active()
        with self._lock:
            if wanted == self._active:
                return False
        if wanted:
            self.activate()
        else:
            self.deactivate()
        return True

    def activate(self) -> None:
        with self._lock:
            if self._active:
                return
            self._active = True
        LOG.info("health mode: activating the care stack")
        try:
            from .plan import get_care_plan_store

            plan = get_care_plan_store()
            # A session that survived a restart belongs to a process that no
            # longer exists. Clearing it here is what stops health mode opening
            # into a conversation nobody remembers starting.
            plan.end_orphaned_session()
        except Exception:
            # Stand back down rather than latching "on" with no plan behind it.
            # Otherwise `sync_to_mode` would see the stack as already active and
            # never try again -- so a care plan that was briefly unreadable
            # would cost health mode for the rest of the process.
            LOG.exception("care plan unavailable; health mode did not start")
            with self._lock:
                self._active = False
            return

        self._start_environment()
        self._start_scheduler(plan)
        self._seed_companion_routines(plan)

    def deactivate(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            manager, self._manager = self._manager, None
        LOG.info("health mode: standing the care stack down")
        if manager is not None:
            try:
                manager.deactivate()
            except Exception:
                LOG.exception("could not cancel care workers")
        try:
            from .environment import reset_environment_provider

            reset_environment_provider()
        except Exception:
            LOG.exception("could not stop the environment provider")
        imu.get_window_store().clear()

    def _start_environment(self) -> None:
        if not environment_active():
            return
        try:
            from .environment import get_environment_provider

            get_environment_provider().start()
        except Exception:
            LOG.exception("environment provider unavailable")

    def _start_scheduler(self, plan) -> None:
        worker_manager = getattr(self._core, "worker_manager", None)
        if worker_manager is None:
            LOG.warning("no worker scheduler yet; care routines will be "
                        "materialised when health mode is next entered")
            return
        try:
            from .scheduling import get_senior_care_manager, set_foreground_hook

            manager = get_senior_care_manager(worker_manager, plan)
            if manager is None:
                return
            set_foreground_hook(self._queue_session)
            count = manager.activate()
            self._manager = manager
            LOG.info("health mode: %d care worker(s) scheduled", count)
        except Exception:
            LOG.exception("could not materialise care routines")

    def _seed_companion_routines(self, plan) -> None:
        if not companion_active():
            return
        from .settings import flag

        if not flag("seed_companion_routines", False):
            return
        try:
            from .companion import ensure_companion_routines

            added = ensure_companion_routines(plan)
            if added:
                LOG.info("seeded %d companion routine(s)", len(added))
                if self._manager is not None:
                    self._manager.sync_workers()
        except Exception:
            LOG.exception("could not seed companion routines")

    # -- board wiring ------------------------------------------------------
    def attach_session(self, session) -> None:
        """Point the board-facing helpers at the session that is connected.

        Mirrors `LegacyKikiCore.attach`: the board comes and goes, the care
        stack does not. Everything registered here is cleared on detach so a
        hold cannot beep into a closed socket.
        """
        from .heart_rate import get_heart_rate_controller

        get_heart_rate_controller().set_sender(session.send_event_sync)
        cadence.set_audio_player(session.play_cadence_wav)
        cadence.set_motion_capture(session.request_motion_window)
        with self._lock:
            self._starter = session.start_due_care_session

    def detach_session(self) -> None:
        from .heart_rate import get_heart_rate_controller

        get_heart_rate_controller().set_sender(None)
        cadence.set_audio_player(None)
        cadence.set_motion_capture(None)
        with self._lock:
            self._starter = None

    # -- device events -----------------------------------------------------
    def ingest_telemetry(self, batch: Dict[str, Any]) -> Dict[str, Any]:
        """Store one wearable batch locally. The Pi is not consulted.

        This is the same call the RPi health service makes on `POST
        /api/health/v1/ingest`; running it here is what makes the laptop and
        the band a complete system.
        """
        from .plan import get_care_plan_store

        return get_care_plan_store().record_wearable_batch(batch)

    def note_imu_window(self, event: Dict[str, Any]) -> bool:
        window = imu.parse_window(event)
        if window is None:
            LOG.info("imu_window carried no usable samples")
            return False
        return imu.get_window_store().offer(window)

    def note_care_options(self, event: Dict[str, Any]) -> None:
        """The wearer flipped a health switch on the watch."""
        if isinstance(event.get("fall_alerts"), bool):
            self._fall_alerts = event["fall_alerts"]
        if isinstance(event.get("movement_checks"), bool):
            self._movement_checks = event["movement_checks"]
        LOG.info("care options from the watch: fall alerts %s, movement checks %s",
                 "on" if self._fall_alerts else "off",
                 "on" if self._movement_checks else "off")

    @property
    def movement_checks_enabled(self) -> bool:
        return self._movement_checks

    @property
    def fall_alerts_enabled(self) -> bool:
        return self._fall_alerts

    def note_measurement_status(self, event: Dict[str, Any]) -> None:
        from .heart_rate import get_heart_rate_controller

        get_heart_rate_controller().note_status(event)

    # -- the plan, as the watch sees it ------------------------------------
    #
    # The board is a view: it renders what it is sent and asks for an action.
    # Nothing here trusts it to hold state, and every action re-sends the whole
    # plan afterwards, so the screen can only ever show a change that actually
    # happened.

    MAX_PANEL_ITEMS = 16

    @staticmethod
    def _when_text(schedule: Dict[str, Any]) -> str:
        """A schedule as a person reads it on a watch face."""
        if not isinstance(schedule, dict):
            return "--"
        kind = str(schedule.get("kind") or "")
        value = schedule.get("value")
        if kind == "daily":
            return str(value or "--")[:5]
        if kind == "once":
            try:
                from datetime import datetime as _dt

                when = _dt.fromisoformat(str(value))
                return when.strftime("%b %-d, %H:%M")
            except (TypeError, ValueError):
                return "once"
        if kind == "recurring":
            try:
                seconds = int(float(value))
            except (TypeError, ValueError):
                return "repeating"
            if seconds % 3600 == 0:
                return f"every {seconds // 3600}h"
            if seconds % 60 == 0:
                return f"every {seconds // 60}m"
            return f"every {seconds}s"
        return "--"

    def plan_payload(self) -> Dict[str, Any]:
        """The whole care plan, flattened for the panel."""
        from .plan import get_care_plan_store

        rows: List[Dict[str, Any]] = []
        try:
            plan = get_care_plan_store()
            sections = (
                ("routine_events", "title", "session_brief"),
                ("exercises", "name", "steps"),
                ("reminders", "message", "message"),
            )
            for section, title_key, brief_key in sections:
                for item in plan.get_section(section) or []:
                    brief = item.get(brief_key)
                    if isinstance(brief, list):
                        brief = "; ".join(str(step) for step in brief)
                    rows.append({
                        "id": str(item.get("id", "")),
                        "title": str(item.get(title_key) or "Untitled")[:60],
                        "when": self._when_text(item.get("schedule")),
                        "category": str(item.get("category") or section[:-1])[:15],
                        "brief": str(brief or "")[:220],
                        "enabled": bool(item.get("enabled", True)),
                        "section": section,
                    })
        except Exception:
            LOG.exception("could not read the care plan for the panel")
        # Soonest first, and anything without a real time after it: a schedule
        # is read in the order it happens, not the order it was typed.
        rows.sort(key=lambda row: (row["when"] in {"--", "once", "repeating"},
                                   row["when"]))
        session = {"active": False, "title": ""}
        try:
            state = get_care_plan_store().care_session_state()
            if state.get("status") == "active":
                session = {"active": True,
                           "title": str(state.get("event_title") or "session")[:40]}
        except Exception:
            LOG.exception("could not read the care session for the panel")
        return {"items": rows[:self.MAX_PANEL_ITEMS], "total": len(rows),
                "session": session,
                "options": {"fall_alerts": self._fall_alerts,
                            "movement_checks": self._movement_checks}}

    def apply_panel_action(self, action: str, item_id: str) -> Dict[str, Any]:
        """Delete, enable, disable or start one routine from the watch.

        Returns `{"ok": bool, "detail": str}`. Nothing is reported as done that
        the store did not actually do -- the panel is not allowed to show a
        deletion that failed any more than Kiki is allowed to say one happened.
        """
        from .plan import get_care_plan_store

        action = str(action or "").strip().lower()
        item_id = str(item_id or "").strip()
        if action == "refresh":
            return {"ok": True, "detail": "refreshed"}
        if action == "end":
            # One tap, no confirmation: someone reaching for this wants it to
            # stop now. Nothing is lost -- the transcript is archived and the
            # routine can be started again.
            ended = self.end_session("ended from the watch")
            return {"ok": bool(ended),
                    "detail": "session ended" if ended else "no session was running"}
        if not item_id:
            return {"ok": False, "detail": "no routine was named"}

        plan = get_care_plan_store()
        row = next((r for r in self.plan_payload()["items"]
                    if r["id"] == item_id), None)
        section = (row or {}).get("section", "routine_events")
        try:
            if action == "delete":
                ok = self._remove(plan, section, item_id)
                detail = "deleted" if ok else "could not delete it"
            elif action in {"enable", "disable"}:
                ok = self._set_enabled(plan, section, item_id, action == "enable")
                detail = ("turned on" if action == "enable" else "turned off") \
                    if ok else "could not change it"
            elif action == "start":
                from .scheduling import start_care_session_now

                result = start_care_session_now(item_id)
                ok = not str(result).startswith("CARE_ACTION_FAILED")
                detail = "started" if ok else str(result)[:120]
            else:
                return {"ok": False, "detail": f"unknown action {action!r}"}
        except Exception as exc:
            LOG.exception("care panel action %s failed", action)
            return {"ok": False, "detail": str(exc)[:160]}

        if ok and action in {"delete", "enable", "disable"} and self._manager:
            # The schedule changed, so the workers have to agree with it before
            # the panel says it is done.
            try:
                self._manager.sync_workers()
            except Exception:
                LOG.exception("could not resync care workers")
        return {"ok": bool(ok), "detail": detail}

    @staticmethod
    def _remove(plan, section: str, item_id: str) -> bool:
        if section == "routine_events":
            return bool(plan.remove_routine_event(item_id))
        if section == "exercises":
            return bool(plan.remove_exercise(item_id))
        return bool(plan.remove_reminder(item_id))

    @staticmethod
    def _set_enabled(plan, section: str, item_id: str, enabled: bool) -> bool:
        if section == "routine_events":
            return bool(plan.edit_routine_event(item_id, enabled=enabled))
        if section == "exercises":
            return bool(plan.edit_exercise(item_id, enabled=enabled))
        return bool(plan.edit_reminder(item_id, enabled=enabled))

    # -- context -----------------------------------------------------------
    def care_line(self) -> str:
        """The compact `Care:` row for the gateway's live context anchor.

        Returns "" when nothing has changed since the last time it was
        injected, because the anchor is append-only: an unchanged row would
        grow the prompt every five minutes and buy nothing.
        """
        if not self._active:
            return ""
        try:
            from .care_now import build_care_now

            line = str(build_care_now() or "").strip()
        except Exception:
            LOG.exception("care snapshot unavailable")
            return ""
        if not line or line == self._last_care_line:
            return ""
        self._last_care_line = line
        return line

    # -- the care conversation --------------------------------------------
    def session_active(self) -> bool:
        """Is a care session holding the conversation right now?"""
        if not self._active:
            return False
        try:
            from .plan import get_care_plan_store

            return get_care_plan_store().care_session_state().get("status") == "active"
        except Exception:
            LOG.exception("could not read the care session state")
            return False

    def take_pending_session(self) -> Optional[str]:
        """The event id a worker queued for the foreground, once."""
        with self._lock:
            event_id, self._pending_event_id = self._pending_event_id, None
        return event_id

    def _queue_session(self, event_id: str) -> None:
        """Called from a worker thread when a routine falls due.

        The session state is already persisted by the worker; this only asks
        the foreground to start talking. If no board is connected the id is
        held, and `take_pending_session()` hands it over when one arrives --
        which is what stops a routine that fell due during a reconnect from
        being lost.
        """
        with self._lock:
            self._pending_event_id = str(event_id or "") or None
            starter = self._starter
        LOG.info("care event %s queued for the foreground", event_id)
        if starter is not None:
            try:
                starter(str(event_id or ""))
            except Exception:
                LOG.exception("could not hand care event %s to the foreground",
                              event_id)

    async def run_turn(self, user_text: str = "",
                       abort: Optional[threading.Event] = None,
                       recent_texts: Optional[List[str]] = None
                       ) -> Tuple[str, Dict[str, Any]]:
        """One care turn. Returns `(spoken_text, directive)`.

        The directive is what the session needs in order to do the right thing
        next: `hold_seconds` to beep out with the microphone muted, and
        `expect_reply` to decide whether to open it again afterwards.
        """
        from .agent import get_last_care_directive, run_care_voice_turn

        spoken = await run_care_voice_turn(user_text, stop_event=abort,
                                           recent_texts=recent_texts)
        return spoken, get_last_care_directive()

    async def hold(self, seconds: int, abort: Optional[threading.Event] = None) -> bool:
        """Beep out a hold and record the wrist across it. Off the event loop."""
        return await asyncio.to_thread(cadence.play_countdown, int(seconds), abort)

    async def cue(self, name: str) -> None:
        if name:
            await asyncio.to_thread(cadence.play_cue, name)

    def end_session(self, reason: str, status: str = "cancelled") -> bool:
        from .agent import end_active_care_session

        return end_active_care_session(reason, status)


_RUNTIME: Optional[CareRuntime] = None
_RUNTIME_LOCK = threading.Lock()


def get_care_runtime(create: bool = True) -> Optional[CareRuntime]:
    """The process's care runtime, or None when there is nothing to talk to.

    `create=False` is the read-only form used on hot paths: it answers "is
    there a care stack?" without building one, so a call from `default` mode
    costs a dictionary lookup.
    """
    global _RUNTIME
    if _RUNTIME is None and not create:
        return None
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = CareRuntime()
        return _RUNTIME


def care_runtime_if_live() -> Optional[CareRuntime]:
    """The runtime, only when health mode is actually on. The usual guard."""
    runtime = get_care_runtime(create=False)
    if runtime is None or not runtime.active:
        return None
    return runtime
