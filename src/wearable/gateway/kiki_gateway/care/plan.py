"""Care plan store for `health_sih` mode -- the laptop owns it.

Vendored from the RPi runtime (`core/senior/care_plan.py`) rather than imported,
for two reasons that both matter here:

* `gateway/legacy_kiki` is a *copy* of KikiFast that is re-synced wholesale, so
  anything this package needed from there would have to survive a sync it does
  not control. The snapshot this gateway runs predates the whole health stack.
* The RPi must not have to be switched on. This file, its lock and its JSON live
  beside the gateway on the laptop, so `health_sih` is complete with nothing but
  the laptop and the board.

Everything else -- the versioned structure, the cross-process lock, the session
lifecycle, the trusted/untrusted measurement split -- is the proven RPi
behaviour and is deliberately left alone.

A small JSON-backed store (mirrors ``core/brain/knowledge_base.py``) holding the
caregiver-defined care plan: the senior's profile, family contacts, scheduled
reminders (medicine / hydration / meal / appointment / exercise), guided
exercise routines, family-approved music/topics, and a rolling ``care_log`` of
what actually happened during the day (used for the daily caregiver summary).

Voice-first: Kiki sends caregiver/senior requests to the complex care agent,
which owns ``get_care_plan``/``update_care_plan`` and verifies scheduling. The
store remains plain JSON that a caregiver Web UI can render and edit through a
future API.

Saves are atomic (tmp + ``os.replace``) so a crash mid-write never corrupts it.
"""

import json
import os
import statistics
import threading
import uuid
import fcntl
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from .settings import care_settings
from .signal_quality import wearable_heart_is_coherent


def get_full_config() -> Dict[str, Any]:
    """The live legacy config, or an empty one off-runtime.

    Unit tests import this module without the legacy tree on `sys.path`, and a
    care plan that cannot be constructed without a full Kiki runtime is a care
    plan that cannot be tested.
    """
    try:
        from tools_and_config.config_loader import get_full_config as _live

        return _live() or {}
    except Exception:
        return {}


# ============================================================================
# Configuration / paths
# ============================================================================

VALID_REMINDER_CATEGORIES = {
    "medicine", "hydration", "meal", "appointment", "exercise", "other",
}

# A schedule is one of:
#   {"kind": "recurring", "value": <seconds:int>}   -> fires every N seconds
#   {"kind": "daily",     "value": "HH:MM"}         -> fires each day at HH:MM
#   {"kind": "once",      "value": "<ISO datetime>"}-> fires once at that time
VALID_SCHEDULE_KINDS = {"recurring", "daily", "once"}
VALID_ROUTINE_CATEGORIES = {
    "morning", "medicine", "hydration", "meal", "exercise", "sleep",
    "appointment", "wellbeing", "memory", "social", "safety", "vitals", "other",
}
VALID_ROUTINE_ACTION_TYPES = {
    "speak", "check_in", "guided_step", "memory_activity", "play_music",
    "observe", "measure_vital", "log", "notify_caregiver",
}


def _default_care_plan_path() -> Path:
    """`<gateway>/care-state/care_plan.json` on whichever machine runs this.

    Deliberately NOT `senior_mode.care_plan_file`. That key points at the old
    `senior` mode's plan, written by a different (older) schema through the
    legacy tools, and the demo running tomorrow depends on it being untouched.
    A separate file is the difference between adding a mode and migrating one.

    `care-state/` sits next to `context-archives/`, outside `legacy_kiki`, so a
    re-sync of the shared runtime cannot delete a person's care plan.
    """
    override = os.environ.get("KIKI_GATEWAY_CARE_PLAN_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    directory = os.environ.get("KIKI_GATEWAY_CARE_DIR", "").strip()
    if directory:
        return Path(directory).expanduser() / "care_plan.json"
    # gateway/kiki_gateway/care/plan.py -> gateway/care-state/care_plan.json
    return Path(__file__).resolve().parents[2] / "care-state" / "care_plan.json"


def get_care_plan_path() -> Path:
    return _default_care_plan_path()


DEFAULT_STRUCTURE: Dict[str, Any] = {
    "senior": {"name": "", "language": "hi", "notes": "", "health_conditions": []},
    "family_contacts": [],      # {name, relationship, email, notify_on:[alert,daily_summary]}
    "reminders": [],            # {id, category, message, schedule, enabled}
    "exercises": [],            # {id, name, steps:[...], schedule, prescribed_by, enabled}
    # A person's day.  session_brief is a rich hand-off to the conversational
    # care model; it is deliberately not an executable script.
    "routine_events": [],       # {id,title,category,schedule,session_brief,...}
    "active_session": None,     # persisted care-agent conversation
    "approved_music": [],       # ["song / query", ...]
    "approved_topics": [],      # ["cricket", "old bollywood", ...]
    "care_log": [],             # {ts, kind, text}
    "session_history": [],      # compact finished-session records (evening reflection)
    "health_measurements": [], # trusted numeric readings for trend analysis
    # Wearable telemetry is deliberately separate from trusted clinical-style
    # measurements.  MAX30102 SpO2 is useful as an experimental personal trend,
    # but is never promoted into the trusted measurement list or used alone for
    # an urgent alert.
    "wearable_health": {},      # latest compact wearable snapshot
    "wearable_events": [],      # deduplicated rolling raw/derived batches
    "health_advisories": [],    # deterministic, non-diagnostic observations
    "health_alerts": [],        # fall notification attempts/outcomes
    "metadata": {"created": "", "last_updated": ""},
}

_MAX_CARE_LOG = 500
_MAX_SESSION_HISTORY = 60

# A finished session's `care_context` is a frozen copy of the WHOLE care plan,
# kept only so a stateless API can be re-sent an identical prefix mid-session.
# Once the session ends it is 15 kB of dead weight that outlives its purpose --
# measured on the live plan, one finished session was 18 kB of a 43 kB file, and
# nothing ever removed it. It is dropped at completion; the transcript, which is
# the part with lasting value, is kept.
_FINISHED_SESSION_DROP = ("care_context",)

# Hard ceiling on real turns in one session. The 19:07 neck session ran eight
# turns and kept going because the model is the ONLY thing that ever emitted
# `session: "complete"` -- and a model told "usually it is continue" continues.
# The 20-minute idle timeout was the sole backstop, which does not help at all
# while the person is still talking.
_DEFAULT_MAX_SESSION_TURNS = 40

# Hard wall-clock lifetime for one session, independent of idleness.
#
# The idle timeout can only kill a SILENT session, and on 2026-08-31 that turned
# out to be the wrong half of the problem: an engagement session opened at 19:35,
# survived a process restart, and then captured every utterance for four minutes
# -- a maths question, the time, the weather -- because `guided_care_turn` is
# simply "is a session active". Each captured turn refreshed `updated_at`, so the
# idle clock reset continuously and could never fire. A session eating unrelated
# conversation is exactly the case the idle timeout cannot see.
_DEFAULT_MAX_SESSION_MINUTES = 45

# When this module was imported, i.e. roughly when this process started. A
# session whose last turn predates it belongs to a process that no longer
# exists, and a foreground voice conversation cannot outlive its own process.
_PROCESS_STARTED_AT = datetime.now()


class CarePlan:
    """JSON-backed care plan with atomic saves."""

    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or get_care_plan_path()
        self._lock = threading.RLock()
        self.data = self._load()
        self._persisted_data = json.loads(json.dumps(self.data))
        self._disk_signature = self._stat_signature()

    # ------------------------------------------------------------------ io
    def _load(self) -> Dict[str, Any]:
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[CarePlan] Loaded from {self.file_path}")
                return self._migrate(data)
            except (json.JSONDecodeError, IOError) as e:
                print(f"[CarePlan] Error loading ({e}); starting fresh.")
        data = json.loads(json.dumps(DEFAULT_STRUCTURE))
        data["metadata"]["created"] = datetime.now().isoformat()
        return data

    def _migrate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        for key, default in DEFAULT_STRUCTURE.items():
            if key not in data:
                data[key] = json.loads(json.dumps(default))
        for event in data.get("routine_events", []):
            if not str(event.get("session_brief") or "").strip():
                legacy = event.get("actions") or []
                event["session_brief"] = (
                    json.dumps(legacy, ensure_ascii=False)
                    if legacy else str(event.get("objective") or event.get("title") or ""))
        old_session = data.get("active_session")
        if isinstance(old_session, dict) and "transcript" not in old_session:
            old_session["transcript"] = [
                {
                    "at": row.get("at", ""), "user": row.get("response", ""),
                    "assistant": "", "motion_observation": "",
                    "note": "Migrated from legacy scripted session.",
                    "tools_used": [],
                }
                for row in (old_session.get("responses") or [])
            ]
            old_session["turn_count"] = len(old_session["transcript"])
        # Self-heal a plan written before sessions were archived: a session that
        # is already over has no business still carrying the frozen plan copy.
        if (isinstance(old_session, dict)
                and old_session.get("status") not in (None, "active")
                and any(key in old_session for key in _FINISHED_SESSION_DROP)):
            for key in _FINISHED_SESSION_DROP:
                old_session.pop(key, None)
        return data

    def _stat_signature(self):
        try:
            stat = self.file_path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    @property
    def lock_path(self) -> Path:
        return self.file_path.with_suffix(self.file_path.suffix + ".lock")

    @contextmanager
    def _file_lock(self):
        """Cross-process lock shared by Kiki and the health service."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_disk(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            data = json.loads(json.dumps(DEFAULT_STRUCTURE))
            data["metadata"]["created"] = datetime.now().isoformat()
            return data
        with open(self.file_path, "r", encoding="utf-8") as handle:
            return self._migrate(json.load(handle))

    @staticmethod
    def _merge_changed_section(base: Any, local: Any, latest: Any) -> Any:
        """Preserve concurrent appends while still allowing normal replacement."""
        if isinstance(base, list) and isinstance(local, list) and isinstance(latest, list):
            append_only = len(local) >= len(base) and local[:len(base)] == base
            if append_only:
                merged = json.loads(json.dumps(latest))
                known_ids = {
                    item.get("id") for item in merged
                    if isinstance(item, dict) and item.get("id")
                }
                for item in local[len(base):]:
                    item_id = item.get("id") if isinstance(item, dict) else None
                    if item_id and item_id in known_ids:
                        continue
                    if item not in merged:
                        merged.append(json.loads(json.dumps(item)))
                    if item_id:
                        known_ids.add(item_id)
                return merged
        return json.loads(json.dumps(local))

    @staticmethod
    def _merge_wearable_snapshot(base: Any, local: Any, latest: Any) -> Dict[str, Any]:
        """Merge the latest fields while adding concurrent daily step deltas."""
        base = base if isinstance(base, dict) else {}
        local = local if isinstance(local, dict) else {}
        latest = latest if isinstance(latest, dict) else {}
        local_time = str(local.get("captured_at") or "")
        latest_time = str(latest.get("captured_at") or "")
        merged = json.loads(json.dumps(
            latest if latest_time > local_time else local
        ))
        day = str(local.get("step_date") or "")
        if day:
            local_before = int(base.get("steps_today", 0) or 0) \
                if base.get("step_date") == day else 0
            local_after = int(local.get("steps_today", 0) or 0)
            delta = max(0, local_after - local_before)
            latest_total = int(latest.get("steps_today", 0) or 0) \
                if latest.get("step_date") == day else 0
            merged["step_date"] = day
            merged["steps_today"] = latest_total + delta
        return merged

    def _refresh_from_disk_if_clean(self) -> None:
        signature = self._stat_signature()
        if signature == self._disk_signature or self.data != self._persisted_data:
            return
        try:
            fresh = self._read_disk()
        except (OSError, json.JSONDecodeError):
            return
        self.data = fresh
        self._persisted_data = json.loads(json.dumps(fresh))
        self._disk_signature = signature

    def save(self) -> bool:
        with self._lock:
            try:
                local = json.loads(json.dumps(self.data))
                base = self._persisted_data
                changed = {
                    key for key in set(base) | set(local)
                    if key != "metadata" and base.get(key) != local.get(key)
                }
                with self._file_lock():
                    latest = self._read_disk()
                    for key in changed:
                        if key == "wearable_health":
                            latest[key] = self._merge_wearable_snapshot(
                                base.get(key), local.get(key), latest.get(key))
                        else:
                            latest[key] = self._merge_changed_section(
                                base.get(key), local.get(key), latest.get(key))
                    metadata = latest.setdefault("metadata", {})
                    metadata["last_updated"] = datetime.now().astimezone().isoformat()
                    metadata["revision"] = int(metadata.get("revision", 0) or 0) + 1
                    tmp = self.file_path.with_name(
                        f"{self.file_path.name}.tmp.{os.getpid()}.{threading.get_ident()}"
                    )
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(latest, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, self.file_path)
                self.data = latest
                self._persisted_data = json.loads(json.dumps(latest))
                self._disk_signature = self._stat_signature()
                return True
            except Exception as e:
                print(f"[CarePlan] Error saving: {e}")
                return False

    # -------------------------------------------------------------- getters
    def get_section(self, name: str) -> Any:
        with self._lock:
            self._refresh_from_disk_if_clean()
            return json.loads(json.dumps(self.data.get(name)))

    def all_active_schedules(self) -> List[Dict[str, Any]]:
        """Reminders + exercises that are enabled and have a valid schedule.

        Returns a flat list of dicts each carrying ``_kind`` (``reminder`` or
        ``exercise``) so the care manager can materialize one worker per item.
        """
        with self._lock:
            items: List[Dict[str, Any]] = []
            for r in self.data.get("reminders", []):
                if r.get("enabled", True) and _valid_schedule(r.get("schedule")):
                    items.append({**r, "_kind": "reminder"})
            for ex in self.data.get("exercises", []):
                if ex.get("enabled", True) and _valid_schedule(ex.get("schedule")):
                    items.append({**ex, "_kind": "exercise"})
            for event in self.data.get("routine_events", []):
                if event.get("enabled", True) and _valid_schedule(event.get("schedule")):
                    items.append({**event, "_kind": "routine_event"})
            return items

    def contacts_for(self, purpose: str) -> List[Dict[str, Any]]:
        """Family contacts whose ``notify_on`` includes ``purpose`` and have an email."""
        with self._lock:
            out = []
            for c in self.data.get("family_contacts", []):
                if c.get("email") and purpose in (c.get("notify_on") or []):
                    out.append(json.loads(json.dumps(c)))
            return out

    def language(self) -> str:
        with self._lock:
            return str(self.data.get("senior", {}).get("language") or "hi")

    def daily_routine(self) -> List[Dict[str, Any]]:
        """Enabled routine events in the order Kiki will encounter each day."""
        with self._lock:
            events = [json.loads(json.dumps(event))
                      for event in self.data.get("routine_events", [])
                      if event.get("enabled", True)]

        def sort_key(event):
            schedule = event.get("schedule") or {}
            kind = schedule.get("kind")
            value = str(schedule.get("value", ""))
            if kind == "daily":
                return (0, value, event.get("title", ""))
            if kind == "once":
                return (1, value, event.get("title", ""))
            return (2, value, event.get("title", ""))

        return sorted(events, key=sort_key)

    # -------------------------------------------------------------- mutators
    def set_senior_profile(self, **fields) -> bool:
        with self._lock:
            prof = self.data.setdefault("senior", {})
            for k, v in fields.items():
                if v is not None:
                    prof[k] = v
        return self.save()

    def add_reminder(self, category: str, message: str, schedule: Dict[str, Any],
                     enabled: bool = True) -> Dict[str, Any]:
        category = (category or "other").strip().lower()
        if category not in VALID_REMINDER_CATEGORIES:
            category = "other"
        message = str(message or "").strip()
        if not message:
            raise ValueError("reminder message is required")
        schedule = _normalize_schedule(schedule)
        if not _valid_schedule(schedule):
            raise ValueError(
                "a valid reminder schedule is required: daily HH:MM, "
                "recurring positive seconds, or once ISO datetime")
        item = {
            "id": uuid.uuid4().hex[:8],
            "category": category,
            "message": message,
            "schedule": schedule,
            "enabled": bool(enabled),
        }
        with self._lock:
            self.data.setdefault("reminders", []).append(item)
        if not self.save():
            with self._lock:
                self.data["reminders"] = [
                    existing for existing in self.data.get("reminders", [])
                    if existing.get("id") != item["id"]
                ]
            raise IOError("could not persist reminder")
        return item

    def edit_reminder(self, reminder_id: str, **fields) -> bool:
        if "message" in fields:
            fields["message"] = str(fields["message"] or "").strip()
            if not fields["message"]:
                raise ValueError("reminder message cannot be empty")
        if "schedule" in fields:
            fields["schedule"] = _normalize_schedule(fields["schedule"])
            if not _valid_schedule(fields["schedule"]):
                raise ValueError(
                    "a valid reminder schedule is required: daily HH:MM, "
                    "recurring positive seconds, or once ISO datetime")
        return self._edit_item("reminders", reminder_id, fields)

    def remove_reminder(self, reminder_id: str) -> bool:
        return self._remove_item("reminders", reminder_id)

    def add_exercise(self, name: str, steps: List[str], schedule: Optional[Dict[str, Any]] = None,
                     prescribed_by: str = "", enabled: bool = True) -> Dict[str, Any]:
        name = str(name or "").strip()
        steps = [str(s).strip() for s in (steps or []) if str(s).strip()]
        if not name:
            raise ValueError("exercise name is required")
        if not steps:
            raise ValueError("a guided exercise requires at least one step")
        normalized_schedule = _normalize_schedule(schedule) if schedule else {}
        if schedule and not _valid_schedule(normalized_schedule):
            raise ValueError(
                "a valid exercise schedule must be daily HH:MM, recurring "
                "positive seconds, or once ISO datetime")
        item = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "steps": steps,
            "schedule": normalized_schedule,
            "prescribed_by": str(prescribed_by or "").strip(),
            "enabled": bool(enabled),
        }
        with self._lock:
            self.data.setdefault("exercises", []).append(item)
        if not self.save():
            with self._lock:
                self.data["exercises"] = [
                    existing for existing in self.data.get("exercises", [])
                    if existing.get("id") != item["id"]
                ]
            raise IOError("could not persist exercise")
        return item

    def edit_exercise(self, exercise_id: str, **fields) -> bool:
        return self._edit_item("exercises", exercise_id, fields)

    def remove_exercise(self, exercise_id: str) -> bool:
        return self._remove_item("exercises", exercise_id)

    def add_routine_event(self, title: str, category: str,
                          schedule: Dict[str, Any],
                          actions: Optional[List[Dict[str, Any]]] = None,
                          enabled: bool = True, source: str = "user",
                          evidence: str = "", adaptation: Optional[Dict[str, Any]] = None,
                          motion_tracking: bool = False, objective: str = "",
                          session_brief: str = "", companion_key: str = "",
                          continuous_vision: Optional[bool] = None
                          ) -> Dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ValueError("routine event title is required")
        category = str(category or "other").strip().lower()
        if category not in VALID_ROUTINE_CATEGORIES:
            category = "other"
        raw_schedule = schedule
        schedule = _normalize_schedule(schedule)
        if not _valid_schedule(schedule):
            raise ValueError(
                "a valid routine schedule is required: "
                '{"kind":"daily","value":"HH:MM"} or {"kind":"once","value":'
                '"<ISO datetime>"} or {"kind":"recurring","value":<seconds>}. '
                f"Received: {json.dumps(raw_schedule, ensure_ascii=False, default=str)[:200]}")
        # ``actions`` is retained only as migration input for plans created by
        # older builds.  Runtime care never iterates it or turns it into user
        # replies.  New plans use one rich natural-language hand-off so the
        # care model can reason and converse instead of executing a mini DSL.
        raw_actions = actions or []
        action_problems: List[str] = []
        actions = _normalize_routine_actions(raw_actions, action_problems)
        session_brief = str(session_brief or "").strip()
        if raw_actions and not actions and not session_brief:
            raise ValueError(_no_valid_actions_error(action_problems))
        if not session_brief and actions:
            session_brief = json.dumps(actions, ensure_ascii=False)
        if not session_brief:
            raise ValueError(
                "routine event requires a substantive session_brief containing "
                "the person's context, intended outcome, and any caregiver-provided "
                "guidance; no executable dialogue script is required")
        source = str(source or "user").strip().lower()
        evidence = str(evidence or "").strip()
        # `continuous_vision` is the RPi's name for the same flag, kept as an
        # accepted alias because the care agent's tool arguments are written by
        # a model that has read a lot of KikiFast. On a body with no camera the
        # evidence is wrist motion, so the stored name says so.
        if continuous_vision is not None:
            if not isinstance(continuous_vision, bool):
                raise ValueError("motion_tracking must be true or false")
            motion_tracking = motion_tracking or continuous_vision
        if not isinstance(motion_tracking, bool):
            raise ValueError("motion_tracking must be true or false")
        if source == "idle_mind" and len(evidence) < 20:
            raise ValueError(
                "idle-mind routine changes require concrete repeated-routine evidence")
        adaptation = adaptation if isinstance(adaptation, dict) else {}
        try:
            review_after = max(
                1, int(adaptation.get("review_after_occurrences", 3) or 3))
        except (TypeError, ValueError):
            review_after = 3
        # Agent retries and network timeouts must be idempotent. If the exact
        # event is already present, return it rather than creating a duplicate.
        with self._lock:
            duplicate = next((existing for existing in self.data.get("routine_events", [])
                              if existing.get("enabled", True)
                              and str(existing.get("title", "")).casefold() == title.casefold()
                              and existing.get("category") == category
                              and existing.get("schedule") == schedule), None)
            if duplicate is not None:
                result = json.loads(json.dumps(duplicate))
                result["_existing"] = True
                return result
        now = datetime.now().isoformat()
        item = {
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "objective": str(objective or title).strip(),
            "category": category,
            "schedule": schedule,
            "session_brief": session_brief,
            # Kept for old WebUI data only. The live care agent never advances
            # an action index or treats these records as things already done.
            "actions": actions,
            # Raw wrist-IMU windows are captured during each timed hold and
            # handed to the care agent as measured evidence before it judges
            # whether the movement it asked for actually happened.
            "motion_tracking": bool(motion_tracking),
            "enabled": bool(enabled),
            "source": source,
            "evidence": evidence,
            # Stable identity for a seeded companion routine, so re-seeding
            # recognises it even after the person renames or retimes it. Empty
            # for everything the person or the care agent created themselves.
            "companion_key": str(companion_key or "").strip(),
            "adaptation": {
                "allowed": bool(adaptation.get("allowed", True)),
                "strategy": str(adaptation.get("strategy", "adapt_to_response")).strip(),
                "review_after_occurrences": review_after,
            },
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self.data.setdefault("routine_events", []).append(item)
        if not self.save():
            with self._lock:
                self.data["routine_events"] = [
                    existing for existing in self.data.get("routine_events", [])
                    if existing.get("id") != item["id"]
                ]
            raise IOError("could not persist routine event")
        return item

    def edit_routine_event(self, event_id: str, **fields) -> bool:
        fields = dict(fields)
        if "title" in fields and not str(fields["title"] or "").strip():
            raise ValueError("routine event title cannot be empty")
        if "objective" in fields:
            fields["objective"] = str(fields["objective"] or "").strip()
        if "session_brief" in fields:
            fields["session_brief"] = str(fields["session_brief"] or "").strip()
            if not fields["session_brief"]:
                raise ValueError("routine event session_brief cannot be empty")
        if "schedule" in fields:
            fields["schedule"] = _normalize_schedule(fields["schedule"])
            if not _valid_schedule(fields["schedule"]):
                raise ValueError("routine event schedule is invalid")
        if "actions" in fields:
            action_problems: List[str] = []
            fields["actions"] = _normalize_routine_actions(
                fields["actions"], action_problems)
            if not fields["actions"]:
                raise ValueError(_no_valid_actions_error(action_problems))
        if "continuous_vision" in fields:
            fields["motion_tracking"] = fields.pop("continuous_vision")
        if "motion_tracking" in fields:
            if not isinstance(fields["motion_tracking"], bool):
                raise ValueError("motion_tracking must be true or false")
        fields["updated_at"] = datetime.now().isoformat()
        return self._edit_item("routine_events", event_id, fields)

    def remove_routine_event(self, event_id: str) -> bool:
        with self._lock:
            active = self.data.get("active_session") or {}
            if active.get("event_id") == event_id and active.get("status") == "active":
                self.data["active_session"] = None
        return self._remove_item("routine_events", event_id)

    def _session_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Return a conversational event for any scheduled care-plan item.

        ``routine_events`` are canonical.  Reminders and exercises predate that
        model, but a due legacy item must still enter the same foreground care
        conversation instead of reviving the old worker-authored dialogue path.
        The adapter carries stored facts across verbatim; it does not manufacture
        an execution sequence or claim any step occurred.
        """
        event = next((item for item in self.data.get("routine_events", [])
                      if item.get("id") == event_id), None)
        if event is not None:
            return json.loads(json.dumps(event))

        reminder = next((item for item in self.data.get("reminders", [])
                         if item.get("id") == event_id), None)
        if reminder is not None:
            return {
                **json.loads(json.dumps(reminder)),
                "title": reminder.get("message") or "Care reminder",
                "objective": reminder.get("message") or "Care reminder",
                "session_brief": (
                    "Legacy care-plan reminder record (stored user/caregiver "
                    "context): " + json.dumps(reminder, ensure_ascii=False)),
                "motion_tracking": bool(reminder.get("motion_tracking",
                                                    reminder.get("continuous_vision", False))),
                "_legacy_kind": "reminder",
            }

        exercise = next((item for item in self.data.get("exercises", [])
                         if item.get("id") == event_id), None)
        if exercise is not None:
            return {
                **json.loads(json.dumps(exercise)),
                "title": exercise.get("name") or "Care exercise",
                "objective": exercise.get("name") or "Care exercise",
                "session_brief": (
                    "Legacy care-plan exercise record (stored user/caregiver "
                    "context): " + json.dumps(exercise, ensure_ascii=False)),
                "motion_tracking": bool(exercise.get("motion_tracking",
                                                    exercise.get("continuous_vision", False))),
                "_legacy_kind": "exercise",
            }
        return None

    def start_care_session(self, event_id: str) -> Dict[str, Any]:
        # Clear a session nobody has spoken to before deciding this one is
        # blocked. Without this the expiry is purely read-triggered: an idle
        # house never calls care_session_state(), so a stale session lingers
        # and every due routine is deferred behind it indefinitely.
        self.expire_stale_care_session()
        with self._lock:
            event = self._session_event(event_id)
        if event is None:
            raise ValueError("no scheduled care item with that id")
        return self._open_session(event_id, event)

    def start_adhoc_care_session(self, title: str, session_brief: str,
                                 category: str = "exercise",
                                 motion_tracking: bool = True) -> Dict[str, Any]:
        """Open a session for something that was never in the care plan.

        "Kiki, take me through a shoulder stretch" must work on the day the
        care plan is still empty. Requiring a saved routine first turned the
        headline feature into a configuration exercise: the plan starts empty, so the
        answer was always "there are no enabled routines in the care plan",
        which is true and useless.

        The event is INLINE and is never written into `routine_events`, so a
        one-off request cannot quietly become a thing that speaks every day at
        this time. Anything the person wants repeated goes through
        `update_care_plan`, which is a deliberate act with a schedule attached.
        """
        title = str(title or "").strip() or "Guided session"
        session_brief = str(session_brief or "").strip()
        if not session_brief:
            raise ValueError("an ad-hoc session still needs a brief")
        category = str(category or "exercise").strip().lower()
        if category not in VALID_ROUTINE_CATEGORIES:
            category = "other"
        event = {
            "id": "adhoc-" + uuid.uuid4().hex[:6],
            "title": title,
            "objective": title,
            "category": category,
            "schedule": {"kind": "once", "value": datetime.now().isoformat()},
            "session_brief": session_brief,
            "motion_tracking": bool(motion_tracking),
            "enabled": False,
            "source": "ad_hoc",
            "actions": [],
            "adaptation": {"allowed": True, "strategy": "adapt_to_response",
                           "review_after_occurrences": 0},
        }
        return self._open_session(event["id"], event)

    def _open_session(self, event_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """The shared body of start_care_session and its ad-hoc sibling."""
        with self._lock:
            active = self.data.get("active_session")
            if isinstance(active, dict) and active.get("status") == "active":
                if active.get("event_id") == event_id:
                    return self.care_session_state()
                raise ValueError(
                    "another care session is already active; finish or cancel it first")
            session = {
                "id": uuid.uuid4().hex[:8],
                "event_id": event_id,
                "event_title": event.get("title", ""),
                "status": "active",
                "started_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "turn_count": 0,
                "transcript": [],
                # Freeze the complete care context at the start. Cerebras is a
                # stateless API, so this same snapshot is resent on later turns;
                # its prompt cache makes that stable prefix inexpensive.
                "care_context": json.loads(json.dumps({
                    **self.data,
                    "active_session": None,
                })),
                "motion_override": None,
                # Carried for an ad-hoc event, which by design is in no list
                # this session could look it up from later.
                "inline_event": json.loads(json.dumps(event)),
            }
            self.data["active_session"] = session
        if not self.save():
            raise IOError("could not persist active care session")
        return self.care_session_state()

    def advance_care_session(self, response: str = "") -> Dict[str, Any]:
        """Legacy alias: record what the person said without advancing a script."""
        return self.record_care_turn(user_text=response)

    def adapt_care_session(self, response: str, remaining_actions: List[Dict[str, Any]],
                           reason: str = "") -> Dict[str, Any]:
        """Legacy alias: record a deviation for conversational context only."""
        note = str(reason or "").strip()
        if remaining_actions:
            note += (" | User-requested direction: "
                     + json.dumps(remaining_actions, ensure_ascii=False))
        return self.record_care_turn(user_text=response, note=note)

    def record_care_turn(self, user_text: str = "", assistant_text: str = "",
                         motion_observation: str = "", note: str = "",
                         tools_used: Optional[List[str]] = None,
                         instructor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Persist one real microphone→care-model exchange.

        There is intentionally no action counter. Only speech actually heard,
        speech actually delivered, tool use, and grounded sensor evidence are
        recorded; the conversational care model decides what to do next.

        `motion_observation` is this body's version of the RPi's
        `visual_observation`: what the wrist IMU measured during the movement
        that was just asked for. Same role in the transcript, same reason for
        existing -- the next turn must be able to read what was actually
        judged, not a run of interchangeable confirmations.
        """
        with self._lock:
            session = self.data.get("active_session")
            if not isinstance(session, dict) or session.get("status") != "active":
                raise ValueError("there is no active care session")
            turn = {
                "at": datetime.now().isoformat(),
                "user": str(user_text or "").strip()[:2000],
                "assistant": str(assistant_text or "").strip()[:4000],
                "motion_observation": str(motion_observation or "").strip()[:3000],
                "note": str(note or "").strip()[:1000],
                "tools_used": [str(name) for name in (tools_used or [])][:20],
            }
            if instructor is not None:
                from .instructor import validate
                turn["instructor"] = validate(instructor)
            if any(value for key, value in turn.items() if key != "at"):
                transcript = session.setdefault("transcript", [])
                transcript.append(turn)
                if len(transcript) > 100:
                    del transcript[:len(transcript) - 100]
                session["turn_count"] = int(session.get("turn_count", 0)) + 1
            session["updated_at"] = datetime.now().isoformat()
        if not self.save():
            raise IOError("could not persist care-session turn")
        return self.care_session_state()

    def set_care_session_motion(self, enabled: bool) -> Dict[str, Any]:
        """Turn per-turn motion evidence on or off for this live session only.

        "Stop watching my movement" is a thing a person is entitled to say
        mid-routine, and it must not require editing the saved routine.
        """
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        with self._lock:
            session = self.data.get("active_session")
            if not isinstance(session, dict) or session.get("status") != "active":
                raise ValueError("there is no active care session")
            session["motion_override"] = enabled
            session["updated_at"] = datetime.now().isoformat()
        if not self.save():
            raise IOError("could not persist care-session motion setting")
        return self.care_session_state()

    def max_session_turns(self) -> int:
        """Hard ceiling on real turns in one session. 0 disables it."""
        try:
            return max(0, int(care_settings().get(
                "max_session_turns", _DEFAULT_MAX_SESSION_TURNS)))
        except Exception:
            return _DEFAULT_MAX_SESSION_TURNS

    def _close_session(self, session: Dict[str, Any], status: str,
                       reason: str = "") -> None:
        """Mark a session over, strip its dead weight, and archive it.

        Caller must hold ``self._lock`` and must save afterwards. `active_session`
        deliberately keeps holding the finished record rather than being set to
        None: "what happened in the last session" is a real question the WebUI,
        the tools, and the evening reflection all ask, and `start_care_session`
        already treats any non-active status as unblocked.
        """
        now = datetime.now().isoformat()
        session["status"] = status
        session["updated_at"] = now
        session["completed_at"] = now
        if reason:
            session["end_reason"] = reason
        for key in _FINISHED_SESSION_DROP:
            session.pop(key, None)

        transcript = session.get("transcript") or []
        history = self.data.setdefault("session_history", [])
        history.append({
            "id": session.get("id", ""),
            "event_id": session.get("event_id", ""),
            "event_title": session.get("event_title", ""),
            "status": status,
            "end_reason": reason,
            "started_at": session.get("started_at", ""),
            "completed_at": now,
            "turn_count": int(session.get("turn_count", 0) or 0),
            # The last exchange is what an evening reflection actually needs:
            # how the session ended, in the person's own words and Kiki's.
            "last_user": str((transcript[-1] or {}).get("user", ""))[:400] if transcript else "",
            "last_assistant": str((transcript[-1] or {}).get("assistant", ""))[:400] if transcript else "",
        })
        if len(history) > _MAX_SESSION_HISTORY:
            del history[:len(history) - _MAX_SESSION_HISTORY]

    def finish_care_session(self, status: str = "completed", response: str = "",
                            reason: str = "") -> Dict[str, Any]:
        status = str(status or "completed").strip().lower()
        if status not in {"completed", "cancelled", "declined"}:
            raise ValueError("session status must be completed, cancelled, or declined")
        with self._lock:
            session = self.data.get("active_session")
            if not isinstance(session, dict):
                raise ValueError("there is no care session to finish")
            if str(response or "").strip():
                session.setdefault("transcript", []).append({
                    "at": datetime.now().isoformat(),
                    "user": str(response).strip()[:2000],
                    "assistant": "", "motion_observation": "",
                    "note": f"Session {status} by conversational agent/user.",
                    "tools_used": [],
                })
            self._close_session(session, status, reason)
        if not self.save():
            raise IOError("could not persist care-session completion")
        return self.care_session_state()

    def _session_idle_timeout_seconds(self) -> float:
        """How long an active session may go untouched before it is abandoned."""
        try:
            return float(care_settings().get(
                "session_idle_timeout_minutes", 20)) * 60.0
        except Exception:
            return 20 * 60.0

    def expire_stale_care_session(self) -> bool:
        """End an active session that has no business still being active.

        Two independent conditions, because each covers a failure the other
        structurally cannot see:

          lifetime   it has simply run too long, however busy it has been.
          idle       nobody has spoken to it.

        A third condition -- the session belonging to a dead process -- is
        handled by :meth:`end_orphaned_session`, which runs once at boot rather
        than on every read. It is a startup fact, not a threshold that can
        become true mid-conversation.

        The original version had only the idle check, which protects against a
        session going SILENT -- and the observed failure was the opposite: a
        session that kept capturing unrelated speech, refreshing `updated_at`
        with every stolen turn so the idle clock could never expire.

        A session only ended when the model chose to emit
        ``session: "complete"``. If it never did — the person walked away
        mid-routine, or the turn failed — the session stayed active forever.
        One "Hydration Reminder" session ran from 15:57 to 18:39 and did two
        things it should not have: it made every other due care routine fail
        with "another care session is already active" (retrying every 5 s for
        hours), and because main.py routes *all* speech to the care agent
        while a session is active, it swallowed unrelated conversation for
        nearly three hours.

        Returns True when a session was expired.
        """
        timeout = self._session_idle_timeout_seconds()
        lifetime = self._session_max_lifetime_seconds()
        now = datetime.now()
        with self._lock:
            session = self.data.get("active_session")
            if not isinstance(session, dict) or session.get("status") != "active":
                return False

            def _age(field: str):
                stamp = session.get(field)
                try:
                    return (now - datetime.fromisoformat(str(stamp))).total_seconds()
                except (TypeError, ValueError):
                    return None

            idle = _age("updated_at")
            if idle is None:
                idle = _age("started_at")
            alive = _age("started_at")
            reason = ""
            # 1. Ran too long overall, however busy it was.
            if (lifetime > 0 and alive is not None
                    and alive >= lifetime):
                reason = (f"ran for {alive / 60:.0f} min "
                          f"(limit {lifetime / 60:.0f} min)")
            # 2. Nobody has spoken to it.
            if (not reason and timeout > 0 and idle is not None
                    and idle >= timeout):
                reason = (f"no activity for {idle / 60:.0f} min "
                          f"(timeout {timeout / 60:.0f} min)")
            if not reason:
                return False

            title = session.get("event_title", "Care session")
            session["abandoned_reason"] = reason
            self._close_session(session, "abandoned", reason)
        self.add_care_log("care_session", f"{title} ended as abandoned: {reason}.")
        self.save()
        print(f"[CarePlan] Expired stale care session '{title}' ({reason})")
        return True

    def end_orphaned_session(self) -> bool:
        """End a session left behind by a process that no longer exists.

        Call this ONCE at startup, before anything can route a turn. A
        foreground voice conversation cannot outlive the process that was
        holding the microphone, so a session whose last turn predates this one
        is over whatever its status says.

        This is the fix for the 2026-08-31 hijack: an engagement session opened
        at 19:35, was still `active` when Kiki restarted at 19:53, and captured
        every following utterance -- a maths question answered by the care agent
        -- because routing is simply "is a session active". The idle timeout
        could not save it either, since each stolen turn refreshed `updated_at`.

        Deliberately NOT part of `expire_stale_care_session`: that runs on every
        read, and "older than process start" is a startup fact rather than
        something that becomes true mid-conversation.
        """
        with self._lock:
            session = self.data.get("active_session")
            if not isinstance(session, dict) or session.get("status") != "active":
                return False
            stamp = session.get("updated_at") or session.get("started_at")
            try:
                if datetime.fromisoformat(str(stamp)) >= _PROCESS_STARTED_AT:
                    return False
            except (TypeError, ValueError):
                return False
            title = session.get("event_title", "Care session")
            reason = "the process that owned it restarted"
            session["abandoned_reason"] = reason
            self._close_session(session, "abandoned", reason)
        self.add_care_log("care_session", f"{title} ended as abandoned: {reason}.")
        self.save()
        print(f"[CarePlan] Ended orphaned care session '{title}' ({reason})")
        return True

    def _session_max_lifetime_seconds(self) -> float:
        """Total wall-clock a session may run, busy or not. 0 disables it."""
        try:
            return max(0.0, float(care_settings().get(
                "max_session_minutes", _DEFAULT_MAX_SESSION_MINUTES)) * 60.0)
        except Exception:
            return _DEFAULT_MAX_SESSION_MINUTES * 60.0

    def care_session_state(self) -> Dict[str, Any]:
        # Checked on read so a stuck session heals itself wherever it is
        # observed — the scheduler, the voice router, or the Web UI — instead
        # of needing something to remember to sweep it.
        self.expire_stale_care_session()
        with self._lock:
            session = json.loads(json.dumps(self.data.get("active_session")))
            if not isinstance(session, dict):
                return {"status": "none"}
            event = (session.get("inline_event")
                     or self._session_event(session.get("event_id")))
            if event:
                session["event"] = event
                override = session.get("motion_override")
                session["motion_tracking"] = (
                    override if isinstance(override, bool)
                    else bool(event.get("motion_tracking", False)))
            limit = self.max_session_turns()
            turns = int(session.get("turn_count", 0) or 0)
            session["turn_limit"] = limit
            session["turns_remaining"] = max(0, limit - turns) if limit else None
            session["turn_limit_reached"] = bool(limit and turns >= limit)
            return session

    def session_history_since(self, iso_ts: str) -> List[Dict[str, Any]]:
        """Finished sessions completed at or after ``iso_ts``.

        What an evening reflection reads to talk about the day using CONFIRMED
        outcomes: each record says how a session actually ended, not what the
        care plan hoped would happen.
        """
        with self._lock:
            history = json.loads(json.dumps(self.data.get("session_history") or []))
        return [row for row in history
                if str(row.get("completed_at", "")) >= str(iso_ts)]

    def add_family_contact(self, name: str, email: str, relationship: str = "",
                           notify_on: Optional[List[str]] = None) -> Dict[str, Any]:
        item = {
            "name": str(name or "").strip(),
            "relationship": str(relationship or "").strip(),
            "email": str(email or "").strip(),
            "notify_on": notify_on or ["alert", "daily_summary"],
        }
        with self._lock:
            self.data.setdefault("family_contacts", []).append(item)
        self.save()
        return item

    def remove_family_contact(self, name: str) -> bool:
        name = (name or "").strip().lower()
        with self._lock:
            before = len(self.data.get("family_contacts", []))
            self.data["family_contacts"] = [
                c for c in self.data.get("family_contacts", [])
                if c.get("name", "").strip().lower() != name
            ]
            changed = len(self.data["family_contacts"]) != before
        return self.save() and changed

    def add_to_list(self, section: str, value: str) -> bool:
        """Append to ``approved_music`` / ``approved_topics`` (dedup, case-insensitive)."""
        if section not in ("approved_music", "approved_topics"):
            return False
        value = str(value or "").strip()
        if not value:
            return False
        with self._lock:
            lst = self.data.setdefault(section, [])
            if value.lower() not in [x.lower() for x in lst]:
                lst.append(value)
        return self.save()

    def remove_from_list(self, section: str, value: str) -> bool:
        if section not in ("approved_music", "approved_topics"):
            return False
        value = (value or "").strip().lower()
        with self._lock:
            lst = self.data.get(section, [])
            self.data[section] = [x for x in lst if x.strip().lower() != value]
        return self.save()

    def add_care_log(self, kind: str, text: str) -> None:
        entry = {"ts": datetime.now().isoformat(), "kind": str(kind or "note"),
                 "text": str(text or "").strip()}
        with self._lock:
            log = self.data.setdefault("care_log", [])
            log.append(entry)
            if len(log) > _MAX_CARE_LOG:
                del log[: len(log) - _MAX_CARE_LOG]
        self.save()

    def care_log_since(self, iso_ts: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [json.loads(json.dumps(e)) for e in self.data.get("care_log", [])
                    if e.get("ts", "") >= iso_ts]

    def activity_counts(self, since_iso: str = "") -> Dict[str, int]:
        """How many times each observed activity happened, newest window first.

        The CLIP care cascade has been writing `observation` rows since it was
        built -- "drinking: a person raises a glass...", "exercising: ..." --
        and nothing has ever read them back. That is the difference between
        Kiki noticing something and Kiki *knowing* it, and it is what lets her
        answer "how many times have I had water today" at all.

        Counts rather than the rows themselves, deliberately: a list of eight
        near-identical descriptions is both useless to a person and expensive in
        a prompt, while "water 3, exercise 1" is the whole answer.
        """
        if not since_iso:
            since_iso = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0).isoformat()
        counts: Dict[str, int] = {}
        for entry in self.care_log_since(since_iso):
            if entry.get("kind") != "observation":
                continue
            text = str(entry.get("text") or "")
            activity = text.split(":", 1)[0].strip().lower()
            if activity and len(activity) < 40:
                counts[activity] = counts.get(activity, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def add_health_measurement(self, measurement: str, value: float, unit: str,
                               quality: str, site: str = "", context: str = "",
                               signal: Optional[Dict[str, Any]] = None,
                               routine_event_id: str = "", session_id: str = ""
                               ) -> Dict[str, Any]:
        """Persist one trusted numeric vital reading for personal trends."""
        measurement = str(measurement or "").strip().lower()
        quality = str(quality or "").strip().upper()
        if measurement != "heart_rate":
            raise ValueError("unsupported health measurement")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError("health measurement value must be numeric")
        if not 20 <= value <= 250:
            raise ValueError("heart-rate value is outside the sensor's supported range")
        if quality not in {"GOOD", "FAIR"}:
            raise ValueError("only trusted GOOD/FAIR readings may be trended")
        entry = {
            "id": uuid.uuid4().hex[:8],
            "measured_at": datetime.now().astimezone().isoformat(),
            "measurement": measurement,
            "value": round(value, 1),
            "unit": str(unit or "bpm").strip().lower(),
            "quality": quality,
            "site": str(site or "").strip().lower(),
            "context": str(context or "").strip()[:500],
            "signal": json.loads(json.dumps(signal or {})),
            "routine_event_id": str(routine_event_id or "").strip(),
            "session_id": str(session_id or "").strip(),
        }
        with self._lock:
            values = self.data.setdefault("health_measurements", [])
            values.append(entry)
            if len(values) > 2000:
                del values[:len(values) - 2000]
        if not self.save():
            raise IOError("could not persist health measurement")
        return json.loads(json.dumps(entry))

    def record_wearable_batch(self, batch: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically persist one deduplicated wearable telemetry batch.

        The gateway may retry after losing an acknowledgement, so ``batch_id``
        is the idempotency key.  Only quality-gated heart rate is copied into
        ``health_measurements``; experimental SpO2 remains visibly labelled in
        the wearable stream and can never become a trusted/urgent vital.
        """
        if not isinstance(batch, dict):
            raise ValueError("wearable batch must be an object")
        batch_id = str(batch.get("batch_id") or "").strip()[:80]
        if not batch_id:
            raise ValueError("batch_id is required")
        device_id = str(batch.get("device_id") or "wearable-kiki").strip()[:80]
        captured_at = str(batch.get("captured_at") or "").strip()
        try:
            captured = datetime.fromisoformat(captured_at) if captured_at else \
                datetime.now().astimezone()
            if captured.tzinfo is None:
                captured = captured.astimezone()
        except ValueError:
            raise ValueError("captured_at must be an ISO timestamp")

        readings = batch.get("readings") if isinstance(batch.get("readings"), dict) else {}
        heart = readings.get("heart_rate") if isinstance(readings.get("heart_rate"), dict) else {}
        spo2 = readings.get("spo2") if isinstance(readings.get("spo2"), dict) else {}

        def number(value, low, high):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            return parsed if low <= parsed <= high else None

        heart_value = number(heart.get("value"), 20, 250)
        heart_quality = str(heart.get("quality") or "").upper()
        heart_accepted = (
            heart_value is not None
            and heart_quality in {"GOOD", "FAIR"}
            and wearable_heart_is_coherent({**heart, "value": heart_value})
        )
        stored_heart_quality = heart_quality if heart_accepted else "POOR"
        spo2_value = number(spo2.get("value"), 50, 100)
        try:
            steps_delta = max(0, min(100000, int(batch.get("steps_delta", 0) or 0)))
            steps_total = max(0, int(batch.get("steps_total", 0) or 0))
        except (TypeError, ValueError):
            raise ValueError("steps must be integers")

        event = {
            "id": batch_id,
            "batch_id": batch_id,
            "device_id": device_id,
            "captured_at": captured.isoformat(),
            "received_at": datetime.now().astimezone().isoformat(),
            "sequence": int(batch.get("sequence", 0) or 0),
            "steps_delta": steps_delta,
            "steps_total": steps_total,
            "activity": str(batch.get("activity") or "unknown")[:40],
            "worn": bool(batch.get("worn", False)),
            "battery_percent": number(batch.get("battery_percent"), 0, 100),
            "readings": {
                "heart_rate": ({
                    "value": round(heart_value, 1),
                    "unit": "bpm",
                    "quality": stored_heart_quality,
                    "signal": json.loads(json.dumps(heart.get("signal") or {})),
                } if heart_value is not None else {}),
                "spo2": ({
                    "value": round(spo2_value, 1),
                    "unit": "%",
                    "quality": str(spo2.get("quality") or "EXPERIMENTAL").upper(),
                    "calibrated": False,
                    "signal": json.loads(json.dumps(spo2.get("signal") or {})),
                } if spo2_value is not None else {}),
            },
            "environment": json.loads(json.dumps(
                batch.get("environment") if isinstance(batch.get("environment"), dict) else {}
            )),
            "fall": json.loads(json.dumps(batch.get("fall") or {})),
        }

        with self._lock:
            events = self.data.setdefault("wearable_events", [])
            duplicate = next((row for row in events if row.get("batch_id") == batch_id), None)
            if duplicate:
                return {"accepted": True, "duplicate": True,
                        "batch_id": batch_id, "snapshot": self.get_section("wearable_health")}
            events.append(event)
            if len(events) > 5000:
                del events[:len(events) - 5000]

            snapshot = self.data.setdefault("wearable_health", {})
            today = captured.astimezone().date().isoformat()
            if snapshot.get("step_date") != today:
                snapshot["steps_today"] = 0
                snapshot["step_date"] = today
            snapshot["steps_today"] = int(snapshot.get("steps_today", 0) or 0) + steps_delta
            snapshot.update({
                "device_id": device_id,
                "last_batch_id": batch_id,
                "last_seen": event["received_at"],
                "captured_at": event["captured_at"],
                "activity": event["activity"],
                "worn": event["worn"],
                "battery_percent": event["battery_percent"],
                "steps_device_total": steps_total,
                "environment": event["environment"],
            })
            if heart_accepted:
                snapshot["heart_rate"] = event["readings"]["heart_rate"]
                snapshot["heart_rate"]["measured_at"] = event["captured_at"]
            # SpO2 is derived from the same red/IR waveform. It remains
            # experimental even after a clean window, and a POOR HR-quality
            # window must not leak an optical estimate into user-facing state.
            if spo2_value is not None and heart_accepted:
                snapshot["spo2_experimental"] = event["readings"]["spo2"]
                snapshot["spo2_experimental"]["measured_at"] = event["captured_at"]
            if event["fall"]:
                snapshot["fall"] = {**event["fall"], "captured_at": event["captured_at"]}

            if heart_accepted:
                measurements = self.data.setdefault("health_measurements", [])
                measurements.append({
                    "id": f"wear-{batch_id}"[:80],
                    "measured_at": event["captured_at"],
                    "measurement": "heart_rate",
                    "value": round(heart_value, 1),
                    "unit": "bpm",
                    "quality": heart_quality,
                    "site": "wrist",
                    "context": event["activity"],
                    "signal": event["readings"]["heart_rate"].get("signal", {}),
                    "source": "wearable_kiki",
                    "batch_id": batch_id,
                    "routine_event_id": "",
                    "session_id": "",
                })
                if len(measurements) > 2000:
                    del measurements[:len(measurements) - 2000]
        if not self.save():
            raise IOError("could not persist wearable telemetry")
        return {"accepted": True, "duplicate": False, "batch_id": batch_id,
                "heart_rate_accepted": heart_accepted,
                "snapshot": self.get_section("wearable_health")}

    def replace_health_advisories(self, advisories: List[Dict[str, Any]]) -> None:
        with self._lock:
            replacement = json.loads(json.dumps(advisories[-50:]))
            if self.data.get("health_advisories", []) == replacement:
                return
            self.data["health_advisories"] = replacement
        if not self.save():
            raise IOError("could not persist health advisories")

    def record_health_alert(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        row = json.loads(json.dumps(alert))
        row.setdefault("id", uuid.uuid4().hex[:12])
        row.setdefault("created_at", datetime.now().astimezone().isoformat())
        with self._lock:
            values = self.data.setdefault("health_alerts", [])
            values.append(row)
            if len(values) > 500:
                del values[:len(values) - 500]
        if not self.save():
            raise IOError("could not persist health alert")
        return json.loads(json.dumps(row))

    def health_trend(self, measurement: str = "heart_rate", days: int = 7,
                     limit: int = 30) -> Dict[str, Any]:
        measurement = str(measurement or "heart_rate").strip().lower()
        days = max(1, min(365, int(days or 7)))
        limit = max(1, min(200, int(limit or 30)))
        cutoff = datetime.now().astimezone().timestamp() - days * 86400
        with self._lock:
            self._refresh_from_disk_if_clean()
            rows = json.loads(json.dumps(self.data.get("health_measurements", [])))
        trusted = []
        for row in rows:
            if row.get("measurement") != measurement:
                continue
            try:
                if datetime.fromisoformat(row.get("measured_at", "")).timestamp() < cutoff:
                    continue
                trusted.append(row)
            except (TypeError, ValueError):
                continue
        trusted.sort(key=lambda row: row.get("measured_at", ""))
        values = [float(row["value"]) for row in trusted]
        result: Dict[str, Any] = {
            "measurement": measurement,
            "period_days": days,
            "count": len(values),
            "recent": trusted[-limit:],
        }
        if values:
            result.update({
                "latest": values[-1],
                "median": round(statistics.median(values), 1),
                "minimum": round(min(values), 1),
                "maximum": round(max(values), 1),
                "change_from_median": round(values[-1] - statistics.median(values), 1),
                "unit": trusted[-1].get("unit", "bpm"),
            })
        return result

    # -------------------------------------------------------------- internals
    def _edit_item(self, section: str, item_id: str, fields: Dict[str, Any]) -> bool:
        with self._lock:
            for it in self.data.get(section, []):
                if it.get("id") == item_id:
                    for k, v in fields.items():
                        if v is None:
                            continue
                        if k == "schedule":
                            v = _normalize_schedule(v)
                        it[k] = v
                    break
            else:
                return False
        return self.save()

    def _remove_item(self, section: str, item_id: str) -> bool:
        with self._lock:
            before = len(self.data.get(section, []))
            self.data[section] = [it for it in self.data.get(section, [])
                                  if it.get("id") != item_id]
            changed = len(self.data[section]) != before
        return self.save() and changed


# ============================================================================
# Schedule helpers
# ============================================================================

def _valid_schedule(schedule: Any) -> bool:
    if not isinstance(schedule, dict):
        return False
    kind = schedule.get("kind")
    value = schedule.get("value")
    if kind == "recurring":
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if kind == "daily":
        try:
            datetime.strptime(str(value), "%H:%M")
            return True
        except (TypeError, ValueError):
            return False
    if kind == "once":
        try:
            datetime.fromisoformat(str(value))
            return True
        except (TypeError, ValueError):
            return False
    return False


# The agent invents plausible key names for a schedule the same way it does for
# actions. Live failures 2026-08-29: {"start_time":"12:39","trigger_type":
# "scheduled_time"} was rejected four times at 00:37, and "daily 00:00" four
# times at 00:08 — both unambiguous. Coerce the shape; `_valid_schedule` is
# still the gate, so a genuinely ambiguous schedule is refused, not guessed.
_SCHEDULE_KIND_KEYS = ("kind", "type", "trigger_type", "frequency",
                       "recurrence", "mode")
_SCHEDULE_VALUE_KEYS = ("value", "time", "start_time", "at", "when",
                        "datetime", "date_time", "iso", "seconds",
                        "interval_seconds", "interval")
_SCHEDULE_KIND_ALIASES = {
    "daily": "daily", "every_day": "daily", "everyday": "daily", "day": "daily",
    "scheduled_time": "daily", "time_of_day": "daily", "time": "daily",
    "once": "once", "one_time": "once", "onetime": "once", "single": "once",
    "specific_date_time": "once", "date": "once", "datetime": "once",
    "recurring": "recurring", "interval": "recurring", "repeat": "recurring",
    "every": "recurring", "periodic": "recurring",
}


def _coerce_schedule_value(value: Any) -> tuple:
    """Infer (kind, canonical_value) from the value alone, or (None, None)."""
    if isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        seconds = int(value)
        return ("recurring", seconds) if seconds > 0 else (None, None)
    text = str(value or "").strip()
    if not text:
        return None, None
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I %p"):
        try:
            return "daily", datetime.strptime(text.upper(), fmt).strftime("%H:%M")
        except ValueError:
            pass
    try:
        datetime.fromisoformat(text)
        return "once", text
    except ValueError:
        pass
    if text.isdigit() and int(text) > 0:
        return "recurring", int(text)
    return None, None


def _normalize_schedule(schedule: Any) -> Dict[str, Any]:
    """Best-effort coercion of a spoken/loose schedule into the canonical shape."""
    kind_hint, raw_value = "", None
    if isinstance(schedule, dict):
        kind_hint = _first_str(schedule, _SCHEDULE_KIND_KEYS).lower()
        for key in _SCHEDULE_VALUE_KEYS:
            if schedule.get(key) not in (None, ""):
                raw_value = schedule[key]
                break
    elif isinstance(schedule, bool) or schedule is None:
        return {}
    elif isinstance(schedule, (str, int, float)):
        # "daily 08:00" / "once 2026-08-29T12:39:00" / a bare time or interval.
        head, _, tail = str(schedule).strip().partition(" ")
        if tail.strip() and head.lower() in _SCHEDULE_KIND_ALIASES:
            kind_hint, raw_value = head.lower(), tail.strip()
        else:
            raw_value = str(schedule).strip()
    else:
        return {}

    kind = _SCHEDULE_KIND_ALIASES.get(kind_hint, "")
    inferred, value = _coerce_schedule_value(raw_value)
    if value is None:
        return {}
    # A stated kind only wins when the value agrees with it; otherwise the value
    # is the more reliable signal (models mislabel "once" far more often than
    # they mistype an ISO timestamp).
    if kind and kind == inferred:
        return {"kind": kind, "value": value}
    return {"kind": inferred, "value": value}


# The agent writes these actions from a natural-language request, so it reaches
# for reasonable synonyms of the canonical keys. Live failure 2026-08-29
# 00:41:20: a perfectly good "Back Exercise" routine was sent five times as
# {"action": "guided_step", "data": "..."} — semantically exact, keyed wrong.
# Every action was dropped, the error said only "requires at least one valid
# action", and the agent reworded the prose it had right instead of the keys it
# had wrong until the turn budget died. Accept the synonyms; a rename is not a
# reason to lose a routine.
_ACTION_TYPE_KEYS = ("type", "action", "action_type", "kind")
_ACTION_TEXT_KEYS = ("instruction", "text", "question", "data", "content",
                     "message", "prompt", "say", "value", "description")


def _first_str(raw: Dict[str, Any], keys) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _no_valid_actions_error(problems: List[str]) -> str:
    """Build a rejection the agent can actually fix on its next turn."""
    detail = "; ".join(problems[:6]) if problems else "no actions were supplied"
    return (
        "a routine event requires at least one valid action — " + detail +
        '. Each action must be {"type": "speak|check_in|guided_step|'
        'memory_activity|play_music|observe|measure_vital|log|notify_caregiver"'
        ', "instruction": "<the words Kiki should say or do>", "needs_response":'
        " true|false}. Fix the KEY NAMES; do not reword the instruction text.")


def _normalize_routine_actions(
        actions: Any, problems: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Canonicalize routine actions, recording WHY any were rejected.

    `problems` (when given) collects one human-readable reason per dropped
    action. A validation error the model cannot act on is not validation — it
    is a retry loop, so callers surface these back to the agent verbatim.
    """
    if problems is None:
        problems = []
    if not isinstance(actions, list):
        problems.append(
            f"'actions' must be a JSON array, got {type(actions).__name__}")
        return []
    normalized = []
    for index, raw in enumerate(actions[:20]):
        if not isinstance(raw, dict):
            problems.append(f"action {index}: must be an object, got "
                            f"{type(raw).__name__}")
            continue
        action_type = (_first_str(raw, _ACTION_TYPE_KEYS) or "speak").lower()
        if action_type not in VALID_ROUTINE_ACTION_TYPES:
            problems.append(
                f"action {index}: type {action_type!r} is not one of "
                + "|".join(sorted(VALID_ROUTINE_ACTION_TYPES)))
            continue
        instruction = _first_str(raw, _ACTION_TEXT_KEYS)
        steps = [str(step).strip() for step in (raw.get("steps") or [])
                 if str(step).strip()]
        if action_type == "guided_step" and not instruction and steps:
            instruction = steps[0]
        if not instruction:
            problems.append(
                f"action {index} ({action_type}): no instruction text. Put the "
                f"words in \"instruction\". Keys received: "
                + (", ".join(sorted(raw)) or "none"))
            continue
        normalized.append({
            "type": action_type,
            "instruction": instruction,
            "needs_response": bool(raw.get(
                "needs_response",
                action_type in {"check_in", "guided_step", "measure_vital"})),
            "success_signal": str(raw.get("success_signal", "")).strip(),
            "on_concern": str(raw.get("on_concern", "")).strip(),
        })
        if action_type == "measure_vital":
            normalized[-1]["vital_type"] = str(
                raw.get("vital_type") or "heart_rate").strip().lower()
    return normalized


def _routine_turn_actions(actions: List[Dict[str, Any]], start: int) -> List[Dict[str, Any]]:
    """Return the ordered actions Kiki should conduct before waiting again."""
    batch: List[Dict[str, Any]] = []
    for action in actions[max(0, int(start)):]:
        batch.append(json.loads(json.dumps(action)))
        if action.get("needs_response"):
            break
    return batch


# ============================================================================
# Singleton
# ============================================================================

_care_plan_instance: Optional[CarePlan] = None
_singleton_lock = threading.Lock()


def get_care_plan_store() -> CarePlan:
    global _care_plan_instance
    with _singleton_lock:
        if _care_plan_instance is None:
            _care_plan_instance = CarePlan()
        return _care_plan_instance


def reload_care_plan() -> CarePlan:
    global _care_plan_instance
    with _singleton_lock:
        _care_plan_instance = CarePlan()
        return _care_plan_instance
