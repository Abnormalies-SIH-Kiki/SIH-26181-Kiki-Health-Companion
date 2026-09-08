"""Care timing, and the storm it must never reproduce.

On 2026-09-07 one care routine fired 3,351 times. It was materialised as a
generic worker, this gateway's engine has no dispatch for `senior:` workers so
it ran the cloud brain, the hourly cap was spent, the call returned empty, the
worker was marked FAILED -- and this engine predates the failed-worker retry
fix. Each failure injected into chat history, which reached 1,134 messages /
42,709 tokens against an 8,192 window, after which the local model rejected
every request and every reply became "I'm having trouble answering right now."

The properties below are what make that shape impossible, not unlikely.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kiki_gateway.care import plan as care_plan  # noqa: E402
from kiki_gateway.care.scheduler import CareScheduler, occurrence  # noqa: E402


@pytest.fixture
def plan(tmp_path, monkeypatch):
    store = care_plan.CarePlan(tmp_path / "care_plan.json")
    monkeypatch.setattr(care_plan, "get_care_plan_store", lambda: store)
    monkeypatch.setattr(care_plan, "get_care_plan_path",
                        lambda: tmp_path / "care_plan.json")
    monkeypatch.setattr("kiki_gateway.care.scheduler.get_care_plan_path",
                        lambda: tmp_path / "care_plan.json", raising=False)
    return store


def daily(plan, title, hhmm):
    return plan.add_routine_event(
        title=title, category="exercise",
        schedule={"kind": "daily", "value": hhmm},
        session_brief=f"{title}: a short seated routine.")


# ----------------------------------------------------------- occurrences ---

def test_an_occurrence_is_keyed_by_the_time_not_by_the_tick():
    """Idempotence across restarts depends on this and nothing else."""
    item = {"id": "abc", "schedule": {"kind": "daily", "value": "09:00"}}
    morning = datetime(2026, 9, 7, 9, 30)
    later = datetime(2026, 9, 7, 11, 45)
    assert occurrence(item, morning)[0] == occurrence(item, later)[0]
    assert occurrence(item, morning)[0] == "abc@2026-09-07 09:00"
    # A different day is a different occurrence.
    assert occurrence(item, datetime(2026, 9, 8, 9, 30))[0] != occurrence(item, morning)[0]


def test_before_todays_time_the_current_occurrence_is_yesterdays():
    item = {"id": "abc", "schedule": {"kind": "daily", "value": "09:00"}}
    key, due = occurrence(item, datetime(2026, 9, 7, 8, 0))
    assert due == datetime(2026, 9, 6, 9, 0)
    assert key == "abc@2026-09-06 09:00"


def test_a_recurring_schedule_is_anchored_to_the_epoch():
    """Buckets are N seconds long and stable across restarts.

    Anchored to the epoch rather than to local midnight, so the boundary can
    fall mid-hour in a half-hour timezone like IST. That is fine -- the
    guarantee is "once per interval, same key after a restart", not "on the
    hour" -- so the test computes the bucket rather than assuming where it
    starts.
    """
    item = {"id": "r", "schedule": {"kind": "recurring", "value": 3600}}
    base = datetime(2026, 9, 7, 10, 5)
    key, due = occurrence(item, base)

    # Anywhere inside the same bucket gives the same key...
    assert occurrence(item, due + timedelta(minutes=1))[0] == key
    assert occurrence(item, due + timedelta(minutes=59))[0] == key
    # ...and the next bucket is a different occurrence.
    assert occurrence(item, due + timedelta(minutes=61))[0] != key


@pytest.mark.parametrize("schedule", [
    {"kind": "daily", "value": "not a time"},
    {"kind": "daily", "value": "99:99"},
    {"kind": "once", "value": "whenever"},
    {"kind": "recurring", "value": 0},
    {"kind": "recurring", "value": "soon"},
    {"kind": "nonsense", "value": "09:00"},
    {},
])
def test_a_schedule_that_cannot_be_read_never_fires(schedule):
    assert occurrence({"id": "x", "schedule": schedule}, datetime.now()) is None


# ------------------------------------------------------------- the storm ---

def test_a_routine_fires_once_per_occurrence(plan):
    daily(plan, "Stretch", "09:00")
    fired = []
    sched = CareScheduler(plan, starter=fired.append)
    now = datetime(2026, 9, 7, 9, 1)

    assert len(sched.check(now=now)) == 1
    assert len(fired) == 1
    # Ten more ticks inside the same occurrence.
    for minute in range(2, 12):
        sched.check(now=datetime(2026, 9, 7, 9, minute))
    assert len(fired) == 1, f"fired {len(fired)} times in one occurrence"


def test_it_fires_again_the_next_day(plan):
    daily(plan, "Stretch", "09:00")
    fired = []
    sched = CareScheduler(plan, starter=fired.append)
    sched.check(now=datetime(2026, 9, 7, 9, 1))
    sched.check(now=datetime(2026, 9, 8, 9, 1))
    assert len(fired) == 2


def test_a_starter_that_raises_does_not_retry(plan):
    """THE storm property. A failure to speak must not accumulate."""
    daily(plan, "Stretch", "09:00")
    calls = []

    def explode(event_id):
        calls.append(event_id)
        raise RuntimeError("the cloud budget is spent")

    sched = CareScheduler(plan, starter=explode)
    for minute in range(1, 40):
        sched.check(now=datetime(2026, 9, 7, 9, minute))
    assert len(calls) == 1, f"a failing routine ran {len(calls)} times"


def test_no_voice_loop_connected_is_not_a_retry_either(plan):
    daily(plan, "Stretch", "09:00")
    sched = CareScheduler(plan, starter=None)
    for minute in range(1, 20):
        sched.check(now=datetime(2026, 9, 7, 9, minute))
    # Nothing to assert but the absence of an exception and of accumulation:
    # the occurrence is consumed exactly once.
    assert len(sched._fired) == 1


def test_the_schedule_never_reaches_a_model(plan, monkeypatch):
    """It hands over an event id. That is the entire action."""
    daily(plan, "Stretch", "09:00")
    seen = []
    sched = CareScheduler(plan, starter=seen.append)
    sched.check(now=datetime(2026, 9, 7, 9, 1))
    event = plan.get_section("routine_events")[0]
    assert seen == [event["id"]]


# ------------------------------------------------------------ lateness -----

def test_a_routine_missed_by_hours_is_skipped_not_shouted_late(plan):
    daily(plan, "Morning stretch", "09:00")
    fired = []
    sched = CareScheduler(plan, starter=fired.append, grace_seconds=600)
    sched.check(now=datetime(2026, 9, 7, 14, 0))
    assert fired == [], "a 09:00 routine must not run at 14:00"
    # And it is consumed, so it cannot fire later in the day either.
    sched.check(now=datetime(2026, 9, 7, 14, 5))
    assert fired == []


def test_a_routine_a_minute_late_still_runs(plan):
    daily(plan, "Stretch", "09:00")
    fired = []
    sched = CareScheduler(plan, starter=fired.append, grace_seconds=600)
    sched.check(now=datetime(2026, 9, 7, 9, 1))
    assert len(fired) == 1


def test_a_restart_does_not_re_fire_this_mornings_routine(plan):
    daily(plan, "Stretch", "09:00")
    first, second = [], []
    CareScheduler(plan, starter=first.append).check(now=datetime(2026, 9, 7, 9, 1))
    assert len(first) == 1
    # A brand new scheduler, as after a restart: the state is on disk.
    CareScheduler(plan, starter=second.append).check(now=datetime(2026, 9, 7, 9, 5))
    assert second == []


def test_a_backlog_is_marked_seen_rather_than_run(plan):
    """`catch_up` is what the thread does before its first real tick."""
    daily(plan, "Stretch", "09:00")
    fired = []
    sched = CareScheduler(plan, starter=fired.append)
    sched.check(now=datetime(2026, 9, 7, 9, 1), catch_up=True)
    assert fired == []
    sched.check(now=datetime(2026, 9, 7, 9, 2))
    assert fired == [], "catch-up consumed the occurrence"


# --------------------------------------------------------------- busy ------

def test_a_live_session_defers_rather_than_consuming_the_occurrence(plan):
    daily(plan, "Stretch", "09:00")
    fired = []
    busy = {"value": True}
    sched = CareScheduler(plan, starter=fired.append,
                          busy=lambda: busy["value"])
    sched.check(now=datetime(2026, 9, 7, 9, 1))
    assert fired == [], "it must not interrupt a session in progress"

    busy["value"] = False
    sched.check(now=datetime(2026, 9, 7, 9, 2))
    assert len(fired) == 1, "and it must still run once the session ends"


# ------------------------------------------------------------ receipts -----

def test_a_receipt_names_the_real_next_trigger(plan):
    event = daily(plan, "Stretch", "09:00")
    sched = CareScheduler(plan, starter=lambda _e: None)
    receipt = sched.receipt(event["id"])
    assert receipt["scheduled"] is True
    assert receipt["next_trigger"]
    assert receipt["title"] == "Stretch"
    assert sched.is_scheduled(event["id"]) is True


def test_an_unknown_item_is_reported_as_not_scheduled(plan):
    sched = CareScheduler(plan, starter=lambda _e: None)
    receipt = sched.receipt("nope")
    assert receipt["scheduled"] is False
    assert sched.is_scheduled("nope") is False


def test_a_disabled_routine_is_not_timed(plan):
    event = daily(plan, "Stretch", "09:00")
    plan.edit_routine_event(event["id"], enabled=False)
    fired = []
    sched = CareScheduler(plan, starter=fired.append)
    sched.check(now=datetime(2026, 9, 7, 9, 1))
    assert fired == []
    assert sched.is_scheduled(event["id"]) is False
