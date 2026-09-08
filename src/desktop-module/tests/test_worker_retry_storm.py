"""Regression cover for the care-worker retry storm and the dropped tool call.

All four behaviours here come from one evening's logs (2026-08-29):

* two care workers reached retry_count 459 against max_retries 3, re-firing
  every 5 s for hours because FAILED counted as triggerable;
* each of those re-fires injected a failure line into the conversation until
  the local model's 8192-token context overflowed;
* a 16:00 one-off was still trying to fire at 18:46;
* and a care turn emitted `update_care_plan` together with
  `status: "completed"`, spoke the summary, and never ran the tool — which is
  why the neck exercise the person asked for at 18:40 never existed.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from core.workers.worker_engine import (
    Worker, WorkerTrigger, WorkerStatus, TriggerType, WorkerDeferred,
)


# --------------------------------------------------------------------------
# FAILED is terminal
# --------------------------------------------------------------------------

def _worker(**kw):
    return Worker(
        name=kw.pop("name", "senior:routine_event:abc123"),
        task_description="do the thing",
        trigger=WorkerTrigger(
            trigger_type=TriggerType.SCHEDULED_TIME.value,
            scheduled_time=kw.pop("scheduled_time", "2026-08-29T16:00:00"),
        ),
        **kw,
    )


def test_a_worker_that_gave_up_is_not_triggerable_again():
    w = _worker(max_retries=3)
    for _ in range(3):
        w.mark_failed("another care session is already active")

    assert w.status == WorkerStatus.FAILED.value
    assert w.retry_count == 3
    # The storm: FAILED used to report itself as active, so every 5 s tick
    # re-ran a worker that had already given up.
    assert not w.is_active()


def test_retries_before_the_limit_stay_pending_and_active():
    """The retry path must keep working — it runs through PENDING, not FAILED."""
    w = _worker(max_retries=3)
    w.mark_failed("transient")

    assert w.status == WorkerStatus.PENDING.value
    assert w.is_active()


# --------------------------------------------------------------------------
# Overdue one-offs are skipped, not delivered late
# --------------------------------------------------------------------------

class _StubManager:
    """Just enough WorkerManager to exercise _check_scheduled_workers."""

    def __init__(self, workers, grace=900):
        import threading
        from core.workers.worker_manager import WorkerManager
        self._workers = workers
        self._lock = threading.Lock()
        self._running_tasks = {}
        self._overdue_grace_seconds = grace
        self._deferred_until = {}
        self._defer_retry_seconds = 30
        self.fired = []
        self._saves = 0
        self._check = WorkerManager._check_scheduled_workers.__get__(self)
        self._is_deferred = WorkerManager._is_deferred.__get__(self)

    def _save(self):
        self._saves += 1

    def _execute_worker_background(self, worker):
        self.fired.append(worker.name)


def test_a_long_overdue_one_off_is_marked_missed_instead_of_firing():
    stale = _worker(
        name="senior:routine_event:9bc010ed",
        scheduled_time=(datetime.now() - timedelta(hours=2)).isoformat())
    mgr = _StubManager([stale], grace=900)

    mgr._check()

    assert mgr.fired == []
    assert stale.status == WorkerStatus.COMPLETED.value
    assert "MISSED" in stale.last_result
    # And it must not re-qualify on the next tick — that loop was the storm.
    mgr._check()
    assert mgr.fired == []


def test_a_just_due_one_off_still_fires():
    fresh = _worker(
        name="senior:routine_event:fresh",
        scheduled_time=(datetime.now() - timedelta(seconds=30)).isoformat())
    mgr = _StubManager([fresh], grace=900)

    mgr._check()

    assert mgr.fired == ["senior:routine_event:fresh"]


def test_the_grace_check_can_be_disabled():
    stale = _worker(
        name="senior:routine_event:catchup",
        scheduled_time=(datetime.now() - timedelta(hours=5)).isoformat())
    mgr = _StubManager([stale], grace=0)

    mgr._check()

    assert mgr.fired == ["senior:routine_event:catchup"]


# --------------------------------------------------------------------------
# A busy care session defers rather than fails
# --------------------------------------------------------------------------

def test_a_busy_care_session_raises_deferred_not_failure(monkeypatch):
    from core.senior import senior_care_manager as scm

    class _Plan:
        def start_care_session(self, event_id):
            raise ValueError(
                "another care session is already active; finish or cancel it first")

    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store",
                        lambda: _Plan())

    with pytest.raises(WorkerDeferred):
        asyncio.run(scm.execute_scheduled_routine(
            _worker(name="senior:routine_event:d4884ee8")))


def test_a_real_error_is_still_a_failure(monkeypatch):
    """Deferral must not swallow genuine breakage."""
    from core.senior import senior_care_manager as scm

    class _Plan:
        def start_care_session(self, event_id):
            raise ValueError("no scheduled care item with that id")

    monkeypatch.setattr("core.senior.care_plan.get_care_plan_store",
                        lambda: _Plan())

    ok, result, speak = asyncio.run(scm.execute_scheduled_routine(
        _worker(name="senior:routine_event:gone")))

    assert ok is False
    assert "no scheduled care item" in result


def test_a_deferred_worker_keeps_its_retry_budget_and_says_nothing():
    """The deferral path must not burn a retry or reach the conversation."""
    from core.workers.worker_manager import WorkerManager

    history = []
    w = _worker(name="senior:routine_event:busy")
    w.status = WorkerStatus.PENDING.value

    loop = asyncio.new_event_loop()
    try:
        mgr = WorkerManager.__new__(WorkerManager)
        import threading
        mgr._workers = [w]
        mgr._lock = threading.Lock()
        mgr._loop = loop
        mgr._running_tasks = {}
        mgr._message_history = history
        mgr._care_session_callback = None
        mgr._enabled = True
        mgr._persistence_file = "/dev/null"

        async def _boom(worker):
            raise WorkerDeferred("busy")

        import core.senior.senior_care_manager as scm
        original = scm.execute_scheduled_routine
        scm.execute_scheduled_routine = _boom
        try:
            mgr._execute_worker_background(w)
            # Drain the coroutine that _execute_worker_background scheduled.
            pending = asyncio.all_tasks(loop)
            loop.run_until_complete(asyncio.sleep(0.05))
            for _ in range(20):
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                if not pending:
                    break
                loop.run_until_complete(asyncio.sleep(0.02))
        finally:
            scm.execute_scheduled_routine = original
    finally:
        loop.close()

    assert w.retry_count == 0, "a deferral must not spend a retry"
    assert w.status == WorkerStatus.PENDING.value
    assert history == [], "a deferral must not be reported to the person"
