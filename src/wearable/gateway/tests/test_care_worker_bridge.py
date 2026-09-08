import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from kiki_gateway.care import scheduling, worker_bridge


def worker(name="senior:exercise:one", trigger="recurring"):
    w = SimpleNamespace(id=name, name=name, status="pending", retry_count=0,
        max_retries=3, last_result=None, trigger=SimpleNamespace(
            trigger_type=trigger, interval_seconds=86400, last_fired_at=None,
            scheduled_time=None))
    w.is_active = lambda: w.status in {"pending", "failed"}
    w.mark_running = lambda: setattr(w, "status", "running")
    w.mark_cancelled = lambda: setattr(w, "status", "cancelled")
    def failed(message):
        w.retry_count += 1
        w.status = "failed"
    w.mark_failed = failed
    return w


def manager(*workers):
    ordinary = []
    m = SimpleNamespace(_workers=list(workers), _running_tasks={}, _enabled=True,
        _lock=threading.Lock(), _loop=asyncio.get_running_loop(), _save=lambda: None,
        _execute_worker_background=lambda w: ordinary.append(w))
    worker_bridge.install(m)
    return m, ordinary


async def completed(m):
    await asyncio.gather(*(asyncio.wrap_future(f) for f in list(m._running_tasks.values())))


async def test_one_submission_and_foreground_handoff_without_general_agent(monkeypatch):
    w = worker()
    m, ordinary = manager(w)
    calls, handoffs = [], []
    monkeypatch.setattr("kiki_gateway.care.mode.care_active", lambda: True)
    async def execute(w):
        calls.append(w)
        await asyncio.sleep(.01)
        return True, '{"status":"care_session_ready","event_id":"one"}', None
    monkeypatch.setattr(scheduling, "execute_scheduled_routine", execute)
    monkeypatch.setattr(scheduling, "_foreground_hook", handoffs.append)
    m._execute_worker_background(w)
    m._execute_worker_background(w)
    await completed(m)
    assert calls == [w] and handoffs == ["one"] and not ordinary
    assert w.status == "pending" and w.trigger.last_fired_at
    assert not m._running_tasks


async def test_rematerialization_removes_duplicates_but_preserves_unrelated_work():
    a, b, c = worker(), worker(), worker("personal:timer")
    m, _ = manager(a, b, c)
    assert m.remove_workers_by_prefix("senior:") == 2
    assert m._workers == [c] and a.status == b.status == "cancelled"


async def test_noncare_workers_keep_original_executor():
    w = worker("ordinary")
    m, ordinary = manager(w)
    m._execute_worker_background(w)
    assert ordinary == [w]


async def test_stale_care_worker_is_cancelled_outside_health_mode(monkeypatch):
    monkeypatch.setattr("kiki_gateway.care.mode.care_active", lambda: False)
    w = worker()
    m, ordinary = manager(w)
    m._execute_worker_background(w)
    await completed(m)
    assert w.status == "cancelled" and not ordinary


async def test_failed_once_worker_exhausts_retries_instead_of_firing_forever(monkeypatch):
    monkeypatch.setattr("kiki_gateway.care.mode.care_active", lambda: True)
    async def execute(_):
        return False, "missing routine", None
    monkeypatch.setattr(scheduling, "execute_scheduled_routine", execute)
    w = worker(trigger="scheduled_time")
    m, _ = manager(w)
    for _ in range(3):
        m._execute_worker_background(w)
        await completed(m)
    assert w.status == "cancelled" and w.retry_count == 3
    assert datetime.fromisoformat(w.trigger.scheduled_time) > datetime.now()


async def test_busy_session_defers_without_spending_retry_budget(monkeypatch):
    monkeypatch.setattr("kiki_gateway.care.mode.care_active", lambda: True)
    async def execute(_):
        return True, '{"status":"deferred"}', None
    monkeypatch.setattr(scheduling, "execute_scheduled_routine", execute)
    w = worker()
    m, _ = manager(w)
    m._execute_worker_background(w)
    await completed(m)
    assert w.status == "pending" and w.retry_count == 0
    elapsed = (datetime.now() - datetime.fromisoformat(w.trigger.last_fired_at)).total_seconds()
    assert 86360 < elapsed < 86380
