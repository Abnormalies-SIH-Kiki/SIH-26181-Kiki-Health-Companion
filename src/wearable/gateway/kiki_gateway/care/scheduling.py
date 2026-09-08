"""Bridges the care plan onto the gateway's existing worker scheduler.

Vendored from the RPi's `core/senior/senior_care_manager.py`. The scheduler it
drives is the one this gateway already runs (`LegacyKikiCore.start_background`
constructs a `WorkerManager` at startup), so care events become ordinary
background workers rather than a second timing system nobody else can see.

The one behavioural difference from the RPi is `WorkerDeferred`: that exception
was added to KikiFast's worker engine after these gateway runtime snapshots
were taken, so it is resolved at call time and falls back to a plain retry-free
failure when the loaded engine does not have it. A missing exception class must
not turn a scheduling collision into a crash.

When a mode with the ``care`` capability is entered, every enabled routine event,
legacy reminder, and guided exercise in ``care_plan.json`` is materialized into
a real background *worker* (§5.13), plus a daily caregiver-summary worker.
Leaving the mode cancels them. Schedule edits re-sync immediately; progress
inside an active multi-turn session deliberately does not rebuild its worker.

Nothing here re-implements scheduling or speaking. It drives
``WorkerManager.create_worker``; when any care item becomes due, the worker only
opens persisted session state and queues ``main.py``. The foreground microphone
lifecycle and conversational care agent own every spoken turn.

Schedule mapping:
  recurring {value=<sec>}  -> recurring worker, first fire <sec> from now
  daily     {value=HH:MM}  -> recurring worker (interval 24h), back-dated
                             last_fired_at so the first fire lands at HH:MM
  once      {value=ISO}    -> one-shot scheduled_time worker
"""

import json
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional

try:  # The worker engine lives in whichever Kiki runtime is loaded.
    from core.workers.worker_engine import TriggerType
except Exception:  # pragma: no cover - import-time fallback for unit tests
    class TriggerType(str, Enum):  # type: ignore[no-redef]
        SCHEDULED = "scheduled"
        RECURRING = "recurring"
        EVENT = "event"

_DAY_SECONDS = 86400
_WORKER_PREFIX = "senior:"


async def execute_scheduled_routine(worker) -> tuple[bool, str, Optional[str]]:
    """Open a due session; main.py owns its first real voice turn."""
    from .plan import get_care_plan_store

    try:
        from core.workers.worker_engine import WorkerDeferred
    except Exception:
        WorkerDeferred = None  # type: ignore[assignment]
    event_id = str(worker.name or "").rsplit(":", 1)[-1]
    try:
        state = get_care_plan_store().start_care_session(event_id)
        result = json.dumps({
            "status": "care_session_ready", "event_id": event_id,
            "session_id": state.get("id"),
        }, ensure_ascii=False, separators=(",", ":"))
        # A worker never speaks or creates a fake participant response.
        return True, result, None
    except Exception as exc:
        # Kiki is already mid-conversation with the person about another
        # routine. That is a scheduling collision, not a broken worker: wait
        # and try again rather than spending a retry and reporting a failure
        # the person never needed to hear about.
        if "another care session is already active" in str(exc):
            message = (f"care session {event_id} is waiting for the active "
                       f"session to finish")
            if WorkerDeferred is not None:
                raise WorkerDeferred(message) from exc
            # Older engine: no deferral type exists, so report it as a
            # non-failure. Returning True keeps the collision out of the retry
            # budget, which is the whole point of the deferral -- a routine
            # that collided must not be retried into a storm (459 retries of a
            # FAILED worker is a real observed number here).
            return True, json.dumps({"status": "deferred", "reason": message}), None
        return False, f"Could not start care session {event_id}: {exc}", None


# Set by main.py at wiring time: hands an event id to the foreground voice
# lifecycle so a care session opens and speaks on the NEXT turn. Same route the
# scheduler uses — a session must never be spoken by a worker thread.
_foreground_hook = None


def set_foreground_hook(callback) -> None:
    global _foreground_hook
    _foreground_hook = callback


def _start_adhoc(plan, request: str) -> str:
    """Open a one-off session for something that is not in the care plan.

    Deliberately thin. The brief carries the person's own words and nothing
    else, because inventing a routine here would put words in the care agent's
    mouth about what they asked for -- and the agent is the part that is
    supposed to decide how to conduct it.
    """
    asked = str(request or "").strip()
    title = (asked[:60] or "Guided session").strip()
    brief = (
        f"The person asked for this out loud, just now, in their own words: "
        f"\"{asked}\". " if asked else
        "The person asked to start a session without naming one. ")
    brief += (
        "It is not a saved routine, so there is no prior history for it and "
        "nothing has been agreed in advance. Ask what they want from it if that "
        "is genuinely unclear, then lead it. Keep it short, conservative and "
        "well within a comfortable range."
    )
    try:
        state = plan.start_adhoc_care_session(
            title=title, session_brief=brief,
            category="exercise", motion_tracking=True)
    except Exception as exc:
        return f"CARE_ACTION_FAILED: could not start the session: {exc}"
    return _hand_to_foreground(plan, state, title)


def _hand_to_foreground(plan, state, title: str) -> str:
    """Ask the voice lifecycle to speak an already-open session.

    A session that cannot be voiced is closed again rather than left open: an
    orphan blocks every other care routine behind "another care session is
    already active", which is how one stuck session once swallowed four minutes
    of unrelated conversation.
    """
    if _foreground_hook is None:
        plan.finish_care_session("cancelled")
        return ("CARE_ACTION_FAILED: the session could not be started because "
                "no voice loop is connected to conduct it.")
    try:
        _foreground_hook(str(state.get("event_id", "")))
    except Exception as exc:
        plan.finish_care_session("cancelled")
        return f"CARE_ACTION_FAILED: could not hand the session over: {exc}"
    return (f"CARE_SESSION_STARTED: {title}. Kiki is taking the microphone for "
            f"it now; say nothing else in this reply.")


def start_care_session_now(event_ref: str = "") -> str:
    """Begin a care routine immediately instead of waiting for its schedule.

    ``event_ref`` is a routine-event id, or part of its title ("neck", "waist").
    With nothing at all, the single enabled routine is used when there is only
    one, because "start my exercise" is unambiguous with one routine and
    dangerous to guess at with five.
    """
    from .plan import get_care_plan_store

    plan = get_care_plan_store()
    events = [e for e in (plan.data.get("routine_events") or [])
              if e.get("enabled", True)]
    if not events:
        # The plan starts empty, and "take me through a shoulder stretch" has to
        # work on day one. Refusing until something has been scheduled made the
        # headline feature depend on a configuration step nobody had done yet:
        # observed live on 2026-09-06, the first request in health mode came
        # back as "there are no enabled routines in the care plan".
        return _start_adhoc(plan, event_ref)

    ref = str(event_ref or "").strip().lower()
    if not ref:
        if len(events) != 1:
            titles = ", ".join(str(e.get("title", "")) for e in events)
            return (f"Which routine should I start? The care plan has: {titles}.")
        match = events[0]
    else:
        match = next((e for e in events if str(e.get("id", "")).lower() == ref), None)
        if match is None:
            hits = [e for e in events
                    if ref in str(e.get("title", "")).lower()
                    or ref in str(e.get("objective", "")).lower()]
            if not hits:
                # Asked for something that is not in the plan. Run it as a
                # one-off rather than reading the catalogue back at them: a
                # person asking for a neck stretch wants a neck stretch, not a
                # list of the routines they did not ask for.
                return _start_adhoc(plan, event_ref)
            if len(hits) > 1:
                titles = ", ".join(str(e.get("title", "")) for e in hits)
                return f"Which one did you mean: {titles}?"
            match = hits[0]

    event_id = str(match.get("id", ""))
    try:
        state = plan.start_care_session(event_id)
    except Exception as exc:
        return f"CARE_ACTION_FAILED: could not start {match.get('title')}: {exc}"

    if _foreground_hook is None:
        # The session is open but nothing can voice it; say so rather than
        # letting the person wait for a routine that will never speak.
        plan.finish_care_session("cancelled")
        return ("CARE_ACTION_FAILED: the foreground care route is not wired, so "
                "the session was not started.")
    try:
        _foreground_hook(event_id)
    except Exception as exc:
        plan.finish_care_session("cancelled")
        return f"CARE_ACTION_FAILED: could not open the care turn: {exc}"

    print(f"[SeniorCare] Immediate session requested for "
          f"{match.get('title')!r} ({event_id})")
    return json.dumps({
        "status": "care_session_starting",
        "event_id": event_id,
        "title": match.get("title", ""),
        "session_id": state.get("id"),
        "note": ("The session is open and Kiki will begin guiding it on this "
                 "turn. Do not also describe the routine yourself."),
    }, ensure_ascii=False)


class SeniorCareManager:
    def __init__(self, worker_manager, care_plan, config: Optional[dict] = None):
        self.wm = worker_manager
        self.plan = care_plan
        self.config = config or {}
        self._active = False
        self._scheduler = None

    # ---------------------------------------------------------------- public
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> int:
        """Arm the care schedule. Returns how many routines are timed.

        This used to materialise one generic worker per routine through
        `WorkerManager.create_worker`. It does not any more, and must not: the
        worker engine in this gateway is an older snapshot with no dispatch for
        `senior:` workers, so each routine became an ordinary LLM task. With the
        cloud budget spent it returned empty, the worker was marked FAILED, and
        this engine also predates the failed-worker retry fix -- 3,351 firings
        of one routine on 2026-09-07, a chat history of 1,134 messages, and a
        local model that rejected every request from then on.

        `CareScheduler` calls no model and never retries, so that shape of
        failure has nowhere to accumulate.
        """
        self._clear_existing()
        from .scheduler import CareScheduler

        if self._scheduler is None:
            self._scheduler = CareScheduler(
                self.plan,
                starter=lambda event_id: (_foreground_hook or (lambda _e: None))(event_id),
                busy=self._session_busy,
            )
        count = self._scheduler.start()
        self._active = True
        print(f"[SeniorCare] Activated — {count} care routine(s) timed.")
        return count

    def _session_busy(self) -> bool:
        """True while a care session already owns the conversation."""
        try:
            return self.plan.care_session_state().get("status") == "active"
        except Exception:
            return False

    def deactivate(self) -> None:
        self._clear_existing()
        if self._scheduler is not None:
            self._scheduler.stop()
        self._active = False
        print("[SeniorCare] Deactivated — care schedule stopped.")

    def sync_workers(self) -> int:
        """Pick up a care-plan edit. The scheduler reads the plan every tick,
        so this only has to report the count -- there is nothing to rebuild."""
        if not self._active or self._scheduler is None:
            return 0
        return self._scheduler.count()

    def is_item_scheduled(self, item_id: str) -> bool:
        """Whether this care item is actually timed right now."""
        if self._scheduler is None:
            return False
        return self._scheduler.is_scheduled(item_id)

    def schedule_receipt(self, item_id: str = "") -> dict:
        """A verified receipt with the exact next trigger time.

        This is what stands between "Kiki said it is scheduled" and "it is
        scheduled": `update_care_plan` refuses to report success until this
        confirms the item is timed.
        """
        if self._scheduler is None:
            return {"status": "inactive", "item_id": item_id, "scheduled": False,
                    "reason": "the care schedule is not running"}
        if item_id:
            return self._scheduler.receipt(item_id)
        try:
            items = self.plan.all_active_schedules()
        except Exception as exc:
            return {"status": "error", "scheduled": False, "reason": str(exc)}
        receipts = [self._scheduler.receipt(str(item.get("id"))) for item in items]
        return {"status": "ok", "scheduled": bool(receipts),
                "count": len(receipts), "items": receipts}


# The one care manager for this process.
_manager = None


def get_senior_care_manager(worker_manager=None, care_plan=None, config=None) -> Optional[SeniorCareManager]:
    """Get/create the singleton. Requires worker_manager + care_plan on first call."""
    global _manager
    if _manager is None:
        if worker_manager is None or care_plan is None:
            return None
        _manager = SeniorCareManager(worker_manager, care_plan, config)
    return _manager
