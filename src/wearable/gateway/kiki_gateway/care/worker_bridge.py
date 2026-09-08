"""Adapt the deployed worker engine to foreground care, without editing it.

The legacy engine lacks prefix removal/custom executors and submits a second
coroutine when its first submission takes over one second. Care triggers must
never run the general cloud worker agent or inject its failures into dialogue.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

LOG = logging.getLogger(__name__)


def install(manager):
    if getattr(manager, "_kiki_care_bridge", False):
        return
    original = manager._execute_worker_background

    def remove(prefix):
        with manager._lock:
            removed = [w for w in manager._workers if w.name.startswith(prefix)]
            manager._workers[:] = [w for w in manager._workers if not w.name.startswith(prefix)]
            tasks = [manager._running_tasks.get(w.id) for w in removed]
            for w in removed:
                w.mark_cancelled()
            manager._save()
        for task in tasks:
            if task is not None:
                manager._loop.call_soon_threadsafe(task.cancel)
        LOG.info("removed %d superseded care workers; care plan retained", len(removed))
        return len(removed)

    async def run(worker):
        from . import scheduling
        from .mode import care_active
        try:
            if not care_active():
                worker.mark_cancelled()
                return
            success, result, _ = await scheduling.execute_scheduled_routine(worker)
            payload = json.loads(result) if success else {}
            now = datetime.now()
            if payload.get("status") == "deferred":
                worker.status = "pending"
                delay = now + timedelta(seconds=30)
                if worker.trigger.trigger_type == "recurring":
                    worker.trigger.last_fired_at = (delay - timedelta(
                        seconds=worker.trigger.interval_seconds)).isoformat()
                else:
                    worker.trigger.scheduled_time = delay.isoformat()
            elif success:
                worker.status = "pending" if worker.trigger.trigger_type in {"recurring", "event"} else "completed"
                worker.retry_count = 0
                if payload.get("status") == "care_session_ready" and scheduling._foreground_hook:
                    scheduling._foreground_hook(payload["event_id"])
            else:
                worker.mark_failed(result)
                # Old Worker.is_active includes FAILED even after max_retries.
                if worker.retry_count >= worker.max_retries:
                    worker.mark_cancelled()
                elif worker.trigger.trigger_type == "scheduled_time":
                    worker.trigger.scheduled_time = (now + timedelta(seconds=30)).isoformat()
            worker.last_result = result
            LOG.info("care trigger %s -> %s", worker.name, worker.status)
        except asyncio.CancelledError:
            worker.mark_cancelled()
            raise
        except Exception as exc:
            worker.mark_failed(str(exc))
            worker.mark_cancelled()  # never retry a broken adapter every scheduler tick
            LOG.exception("care trigger failed: %s", worker.name)
        finally:
            with manager._lock:
                manager._running_tasks.pop(worker.id, None)
                manager._save()

    def dispatch(worker):
        if not worker.name.startswith("senior:") or worker.name == "senior:daily_summary":
            return original(worker)
        # Mark the attempt before submitting. A scheduler tick or a duplicate
        # trigger cannot submit again, and failed recurring attempts are paced.
        with manager._lock:
            if not manager._enabled or worker not in manager._workers or not worker.is_active() \
                    or worker.id in manager._running_tasks:
                return
            worker.mark_running()
            worker.trigger.last_fired_at = datetime.now().isoformat()
            manager._save()
            task = asyncio.run_coroutine_threadsafe(run(worker), manager._loop)
            manager._running_tasks[worker.id] = task

    if not hasattr(manager, "remove_workers_by_prefix"):
        manager.remove_workers_by_prefix = remove
    manager._execute_worker_background = dispatch
    manager._kiki_care_bridge = True
