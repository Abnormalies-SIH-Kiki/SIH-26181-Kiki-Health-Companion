"""A care session must not stay active forever.

On 2026-08-29 a "Hydration Reminder" session opened at 15:57 and was still
active at 18:39 — two hours forty minutes, 23 turns. Sessions ended only when
the model chose to emit ``session: "complete"``, so one that never did caused
two separate failures at once:

* every other due care routine raised "another care session is already
  active", failed, and retried every 5 seconds for hours;
* main.py routes *all* speech to the care agent while a session is active
  (``guided_care_turn = status == "active"``), so unrelated conversation was
  swallowed by the hydration reminder for the whole afternoon.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta

import pytest

from core.senior import care_plan as care_plan_module


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A CarePlanStore backed by a throwaway file with one schedulable event."""
    path = tmp_path / "care_plan.json"
    path.write_text(json.dumps({
        "senior": {"name": "Test", "language": "en"},
        "family_contacts": [], "reminders": [], "exercises": [],
        "approved_music": [], "approved_topics": [], "care_log": [],
        "metadata": {}, "health_measurements": [],
        "routine_events": [{
            "id": "evt00001",
            "title": "Hydration Reminder",
            "objective": "drink water",
            "category": "other",
            "schedule": {"kind": "once", "value": "2026-08-29T01:05:00"},
            "actions": [], "continuous_vision": False, "enabled": True,
            "source": "user", "adaptation": {},
        }],
        "active_session": None,
    }))
    monkeypatch.setattr(
        care_plan_module, "get_full_config",
        lambda: {"senior_mode": {"care_agent": {
            "session_idle_timeout_minutes": 20}}},
        raising=False)
    return care_plan_module.CarePlan(Path(str(path)))


def _age_session(store, minutes):
    """Backdate the active session's last-touched stamp."""
    stamp = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    store.data["active_session"]["updated_at"] = stamp
    store.save()


def test_a_session_idle_past_the_timeout_is_abandoned(store):
    store.start_care_session("evt00001")
    _age_session(store, 180)          # the real one ran 2h40m

    assert store.expire_stale_care_session() is True
    assert store.care_session_state()["status"] == "abandoned"


def test_an_abandoned_session_stops_blocking_other_routines(store):
    """The storm's actual mechanism: a stuck session made everything else fail."""
    store.start_care_session("evt00001")
    _age_session(store, 180)

    # Reading the state is enough to clear it — no sweeper required.
    store.care_session_state()

    # A different event can now open its own session instead of raising
    # "another care session is already active".
    store.data["routine_events"].append({
        "id": "evt00002", "title": "Neck Exercise", "objective": "neck",
        "category": "exercise",
        "schedule": {"kind": "daily", "value": "18:40"},
        "actions": [], "continuous_vision": True, "enabled": True,
        "source": "user", "adaptation": {},
    })
    state = store.start_care_session("evt00002")
    assert state["status"] == "active"
    assert state["event_id"] == "evt00002"


def test_a_due_routine_clears_a_stale_session_rather_than_deferring_behind_it(store):
    """Expiry cannot be read-triggered only.

    An idle house never calls care_session_state(), so without a sweep here a
    stale session blocks every due routine forever — they just defer instead of
    failing now, which is quieter but no less stuck.
    """
    store.start_care_session("evt00001")
    _age_session(store, 180)
    store.data["routine_events"].append({
        "id": "evt00003", "title": "Neck Exercise", "objective": "neck",
        "category": "exercise",
        "schedule": {"kind": "daily", "value": "18:40"},
        "actions": [], "continuous_vision": True, "enabled": True,
        "source": "user", "adaptation": {},
    })

    state = store.start_care_session("evt00003")

    assert state["status"] == "active"
    assert state["event_id"] == "evt00003"


def test_a_live_session_is_left_alone(store):
    """The timeout must never cut off a person mid-routine."""
    store.start_care_session("evt00001")
    _age_session(store, 3)

    assert store.expire_stale_care_session() is False
    assert store.care_session_state()["status"] == "active"


def test_a_recorded_turn_keeps_the_session_alive(store):
    """Ongoing conversation refreshes the clock rather than racing it."""
    store.start_care_session("evt00001")
    _age_session(store, 19)
    store.record_care_turn(user_text="still here", assistant_text="good")

    assert store.expire_stale_care_session() is False
    assert store.care_session_state()["status"] == "active"


def test_the_timeout_can_be_disabled(store, monkeypatch):
    """Both caps off means a session really does run forever.

    `max_session_minutes` has to be disabled too: since 2026-08-31 there is a
    second, independent wall-clock limit, because the idle timeout can only see
    a SILENT session and the observed failure was one that kept capturing
    unrelated speech, refreshing its own idle clock with every stolen turn.
    """
    monkeypatch.setattr(
        care_plan_module, "get_full_config",
        lambda: {"senior_mode": {"care_agent": {
            "session_idle_timeout_minutes": 0, "max_session_minutes": 0}}},
        raising=False)
    store.start_care_session("evt00001")
    _age_session(store, 600)

    assert store.expire_stale_care_session() is False
    assert store.care_session_state()["status"] == "active"


def test_abandonment_is_written_to_the_care_log(store):
    store.start_care_session("evt00001")
    _age_session(store, 180)
    store.expire_stale_care_session()

    entries = [e for e in store.data["care_log"]
               if e.get("kind") == "care_session"]
    assert entries, "an abandoned session must leave a trace"
    assert "abandoned" in entries[-1]["text"]
