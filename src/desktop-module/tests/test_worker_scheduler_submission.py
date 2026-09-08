"""A due worker must be submitted exactly once."""

from types import SimpleNamespace

from core.workers.worker_manager import WorkerManager


def test_background_worker_is_not_resubmitted_after_one_second(monkeypatch):
    manager = WorkerManager.__new__(WorkerManager)
    manager._enabled = True
    manager._loop = object()
    manager._running_tasks = {}

    submitted = []
    future = SimpleNamespace(done=lambda: False)

    def fake_submit(coro, loop):
        submitted.append((coro, loop))
        coro.close()
        return future

    monkeypatch.setattr(
        "core.workers.worker_manager.asyncio.run_coroutine_threadsafe",
        fake_submit)

    manager._execute_worker_background(SimpleNamespace(id="care-worker-1"))

    assert len(submitted) == 1
    assert manager._running_tasks["care-worker-1"] is future
