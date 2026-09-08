"""
Worker Manager — Scheduler, Persistence & Lifecycle
=====================================================

The central controller for all workers. Handles:
- CRUD operations (create, cancel, list, get workers)
- Persistence to/from workers.json
- Background scheduler thread (checks time-based triggers)
- Lifecycle event hooks (startup, shutdown, sleep, wake, after_response, face_detected)
- Async worker execution via WorkerBrain

Never blocks the main voice pipeline — all worker executions are background tasks.
"""

import asyncio
import json
import os
import time
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

from core.workers.worker_engine import (
    Worker, WorkerTrigger, WorkerCondition,
    WorkerStatus, TriggerType, VALID_EVENTS
)
from core.workers.worker_brain import execute_worker, get_face_history, get_vision_history
from tools_and_config.config_loader import get_full_config, register_reload_listener


# ============================================================================
# Worker Manager
# ============================================================================

class WorkerManager:
    """
    Central manager for all workers.
    Thread-safe. Runs a background scheduler.
    All worker executions happen as background asyncio tasks.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, message_history: list = None):
        self._workers: List[Worker] = []
        self._lock = threading.Lock()
        self._loop = loop
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_running = False
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._message_history = message_history  # Shared chat history reference

        # Config
        config = get_full_config()
        workers_config = config.get("workers", {})
        self._enabled = workers_config.get("enabled", True)
        self._persistence_file = workers_config.get(
            "persistence_file",
            "/home/vaibhav/KikiESP32/gateway/legacy_kiki/workers.json"
        )
        self._scheduler_interval = workers_config.get("scheduler_interval_seconds", 30)
        self._max_active = workers_config.get("max_active_workers", 20)

        # Load persisted workers
        self._load()
        # Apply Web UI config edits live (enable/disable + scheduler cadence).
        register_reload_listener(self._reload_tunables)
        print(f"[WorkerManager] Initialized: {len(self._workers)} workers loaded, enabled={self._enabled}")

    def _reload_tunables(self):
        """Re-pull the workers toggle/cadence from live config (fired by the Web
        UI on a config change). Disabling stops new work taking effect on the
        NEXT scheduler tick; re-enabling starts the scheduler if it wasn't
        running — no restart needed."""
        try:
            wc = get_full_config().get("workers", {})
            was_enabled = self._enabled
            self._enabled = wc.get("enabled", self._enabled)
            self._scheduler_interval = wc.get("scheduler_interval_seconds", self._scheduler_interval)
            self._max_active = wc.get("max_active_workers", self._max_active)
            if self._enabled and not was_enabled and not self._scheduler_running:
                self.start_scheduler()
            print(f"[WorkerManager] tunables reloaded (enabled={self._enabled})")
        except Exception as e:
            print(f"[WorkerManager] reload_tunables failed: {e}")

    # ========================================================================
    # Persistence
    # ========================================================================

    def _load(self):
        """Load workers from disk."""
        try:
            if os.path.exists(self._persistence_file):
                with open(self._persistence_file, "r") as f:
                    data = json.load(f)
                workers_data = data.get("workers", [])
                self._workers = [Worker.from_dict(w) for w in workers_data]
                print(f"[WorkerManager] Loaded {len(self._workers)} workers from {self._persistence_file}")
            else:
                self._workers = []
        except Exception as e:
            print(f"[WorkerManager] Error loading workers: {e}")
            self._workers = []

    def _save(self):
        """Persist workers to disk."""
        try:
            data = {
                "workers": [w.to_dict() for w in self._workers],
                "last_saved": datetime.now().isoformat()
            }
            with open(self._persistence_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[WorkerManager] Error saving workers: {e}")

    # ========================================================================
    # CRUD Operations
    # ========================================================================

    def create_worker(
        self,
        name: str,
        task_description: str,
        trigger_type: str,
        trigger_value: str = "",
        conditions: Optional[List[Dict]] = None,
        max_retries: int = 3,
        created_by: str = "kiki"
    ) -> Worker:
        """Create and register a new worker."""
        with self._lock:
            # Check limits
            active_count = sum(1 for w in self._workers if w.is_active())
            if active_count >= self._max_active:
                raise ValueError(f"Max active workers ({self._max_active}) reached")

            # Build trigger
            trigger = WorkerTrigger(trigger_type=trigger_type)

            if trigger_type == TriggerType.SCHEDULED_TIME.value:
                trigger.scheduled_time = trigger_value  # ISO datetime string

            elif trigger_type == TriggerType.EVENT.value:
                if trigger_value not in VALID_EVENTS:
                    print(f"[WorkerManager] Warning: event '{trigger_value}' is non-standard, adding anyway")
                trigger.event_name = trigger_value

            elif trigger_type == TriggerType.RECURRING.value:
                try:
                    trigger.interval_seconds = int(trigger_value)
                except ValueError:
                    trigger.interval_seconds = 300  # Default 5 min

            # Build conditions
            worker_conditions = []
            if conditions:
                for c in conditions:
                    if isinstance(c, dict):
                        worker_conditions.append(WorkerCondition.from_dict(c))

            worker = Worker(
                name=name,
                task_description=task_description,
                trigger=trigger,
                conditions=worker_conditions,
                max_retries=max_retries,
                created_by=created_by,
            )

            self._workers.append(worker)
            self._save()

            print(f"[WorkerManager] Created: {worker}")
            return worker

    def cancel_worker(self, worker_id: str) -> bool:
        """Cancel a worker by ID or name."""
        with self._lock:
            for w in self._workers:
                if w.id == worker_id or w.name.lower() == worker_id.lower():
                    if w.is_active():
                        w.mark_cancelled()
                        # Cancel running task if any
                        task = self._running_tasks.get(w.id)
                        if task and not task.done():
                            task.cancel()
                        self._save()
                        print(f"[WorkerManager] Cancelled: {w}")
                        return True
            return False

    def list_workers(self, include_completed: bool = False) -> List[Worker]:
        """List all workers (optionally including completed ones)."""
        with self._lock:
            if include_completed:
                return list(self._workers)
            return [w for w in self._workers if w.status not in
                    (WorkerStatus.COMPLETED.value, WorkerStatus.CANCELLED.value)]

    def get_worker(self, worker_id: str) -> Optional[Worker]:
        """Get a worker by ID."""
        with self._lock:
            for w in self._workers:
                if w.id == worker_id:
                    return w
        return None

    def cleanup_old_workers(self, max_age_hours: int = 24):
        """Remove completed/cancelled workers older than max_age_hours."""
        with self._lock:
            cutoff = time.time() - (max_age_hours * 3600)
            before = len(self._workers)
            self._workers = [
                w for w in self._workers
                if w.is_active() or
                (w.created_at and datetime.fromisoformat(w.created_at).timestamp() > cutoff)
            ]
            removed = before - len(self._workers)
            if removed > 0:
                self._save()
                print(f"[WorkerManager] Cleaned up {removed} old workers")

    # ========================================================================
    # Worker Execution
    # ========================================================================

    def _execute_worker_background(self, worker: Worker):
        """Launch a worker execution as a background asyncio task."""
        if not self._enabled:
            return

        async def _run():
            worker.mark_running()
            with self._lock:
                self._save()

            speak_text = None
            try:
                success, result, speak_text = await execute_worker(worker)
                if success:
                    # For recurring and event workers, reset to pending instead of completed
                    if worker.trigger.trigger_type in (TriggerType.RECURRING.value, TriggerType.EVENT.value):
                        worker.status = WorkerStatus.PENDING.value
                        worker.last_result = result
                        worker.trigger.last_fired_at = datetime.now().isoformat()
                        print(f"[WorkerManager] {worker.trigger.trigger_type} worker reset to pending: {worker.name}")
                    else:
                        worker.mark_completed(result)
                    print(f"[WorkerManager] Worker completed: {worker.name} — {result[:200]}")
                else:
                    worker.mark_failed(result)
                    print(f"[WorkerManager] Worker failed: {worker.name} — {result[:200]}")
            except asyncio.CancelledError:
                worker.mark_cancelled()
                print(f"[WorkerManager] Worker cancelled: {worker.name}")
            except Exception as e:
                worker.mark_failed(str(e))
                print(f"[WorkerManager] Worker exception: {worker.name} — {e}")
            finally:
                with self._lock:
                    self._save()
                    self._running_tasks.pop(worker.id, None)

                # --- Inject result into chat history ---
                if self._message_history is not None and worker.last_result:
                    status_label = "completed" if worker.status == WorkerStatus.COMPLETED.value else "failed"
                    # Append + hot-load so the worker result is prompt-cached
                    # on the box before the user's next turn.
                    from core.llm import hot_inject
                    hot_inject(self._message_history, {
                        "role": "system",
                        "content": f"[Worker '{worker.name}' {status_label}]: {worker.last_result[:500]}"
                    })
                    print(f"[WorkerManager] Injected worker result into chat history")

                # --- Speak the result via TTS if requested ---
                if speak_text:
                    try:
                        await self._speak_text(speak_text)
                    except Exception as e:
                        print(f"[WorkerManager] Error speaking worker result: {e}")

        # Schedule on the main event loop
        try:
            task = asyncio.run_coroutine_threadsafe(_run(), self._loop).result(timeout=1)
        except Exception:
            # If we can't get the future quickly, just submit it
            future = asyncio.run_coroutine_threadsafe(_run(), self._loop)
            # Store future reference to prevent GC
            self._running_tasks[worker.id] = future

    async def _speak_text(self, text: str):
        """Speak text aloud using TTS. Non-blocking background playback."""
        try:
            from core.tts import TTSStreamer
            loop = asyncio.get_running_loop()

            tts = TTSStreamer()
            tts.start()

            # Strip neck-gesture tags if any. Expression tags are left in —
            # add_sentence applies and strips them, so a worker's spoken result
            # still gets the right face.
            from robot.neck import strip_neck_tags
            clean_text = strip_neck_tags(text)

            if clean_text:
                tts.add_sentence(clean_text)

            await loop.run_in_executor(None, tts.finish)
            print(f"[WorkerManager] TTS playback complete for worker speech")

            # Also inject spoken text as assistant message into chat + hot-load.
            if self._message_history is not None:
                from core.llm import hot_inject
                hot_inject(self._message_history, {
                    "role": "assistant",
                    "content": clean_text
                })

        except Exception as e:
            print(f"[WorkerManager] TTS error: {e}")

    # ========================================================================
    # Scheduler Thread
    # ========================================================================

    def start_scheduler(self):
        """Start the background scheduler thread.

        The thread starts even when workers are disabled — the loop itself skips
        all work while self._enabled is False. This is what lets the Web UI
        ENABLE workers live (flip the flag and the already-running loop picks up
        on its next tick) instead of needing a restart."""
        if self._scheduler_running:
            return
        self._scheduler_running = True
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="WorkerScheduler"
        )
        self._scheduler_thread.start()
        print(f"[WorkerManager] Scheduler started (interval={self._scheduler_interval}s, enabled={self._enabled})")

    def stop_scheduler(self):
        """Stop the scheduler thread."""
        self._scheduler_running = False
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)
            print("[WorkerManager] Scheduler stopped")

    def _scheduler_loop(self):
        """Background loop that checks scheduled workers."""
        while self._scheduler_running:
            try:
                # Honour the live enable flag every tick — disabling in the Web
                # UI stops scheduled/recurring work immediately (no restart).
                if self._enabled:
                    self._check_scheduled_workers()
                    self._check_recurring_workers()
            except Exception as e:
                print(f"[WorkerScheduler] Error: {e}")

            # Sleep in small increments so we can stop quickly
            for _ in range(self._scheduler_interval * 2):
                if not self._scheduler_running:
                    return
                time.sleep(0.5)

    def _check_scheduled_workers(self):
        """Check if any time-scheduled workers should fire."""
        now = datetime.now()
        with self._lock:
            candidates = [
                w for w in self._workers
                if (w.is_active() and
                    w.trigger.trigger_type == TriggerType.SCHEDULED_TIME.value and
                    w.trigger.scheduled_time and
                    w.status != WorkerStatus.RUNNING.value and
                    w.id not in self._running_tasks)
            ]

        for worker in candidates:
            try:
                scheduled = datetime.fromisoformat(worker.trigger.scheduled_time)
                if now >= scheduled:
                    print(f"[WorkerScheduler] Time trigger fired: {worker}")
                    self._execute_worker_background(worker)
            except (ValueError, TypeError) as e:
                print(f"[WorkerScheduler] Invalid scheduled_time for {worker.id}: {e}")

    def _check_recurring_workers(self):
        """Check if any recurring workers should fire."""
        now = time.time()
        with self._lock:
            candidates = [
                w for w in self._workers
                if (w.is_active() and
                    w.trigger.trigger_type == TriggerType.RECURRING.value and
                    w.trigger.interval_seconds and
                    w.status != WorkerStatus.RUNNING.value and
                    w.id not in self._running_tasks)
            ]

        for worker in candidates:
            last_fired = 0
            if worker.trigger.last_fired_at:
                try:
                    last_fired = datetime.fromisoformat(worker.trigger.last_fired_at).timestamp()
                except (ValueError, TypeError):
                    pass

            elapsed = now - last_fired
            if elapsed >= worker.trigger.interval_seconds:
                print(f"[WorkerScheduler] Recurring trigger fired: {worker}")
                self._execute_worker_background(worker)

    # ========================================================================
    # Lifecycle Event Hooks
    # ========================================================================

    async def fire_event(self, event_name: str, **kwargs):
        """
        Fire a lifecycle event. All workers triggered by this event will execute.
        
        Supported events: startup, shutdown, sleep, wake, after_response, face_detected
        """
        if not self._enabled:
            return

        with self._lock:
            candidates = [
                w for w in self._workers
                if (w.is_active() and
                    w.trigger.trigger_type == TriggerType.EVENT.value and
                    w.trigger.event_name == event_name and
                    w.status != WorkerStatus.RUNNING.value and
                    w.id not in self._running_tasks)
            ]

        if not candidates:
            return

        print(f"[WorkerManager] Event '{event_name}' → {len(candidates)} worker(s) to execute")

        for worker in candidates:
            # For face_detected, optionally filter by person
            if event_name == "face_detected":
                person = kwargs.get("person", "")
                if person:
                    # Check if any condition references this person
                    relevant = False
                    if not worker.conditions:
                        relevant = True  # No conditions = always relevant
                    else:
                        for cond in worker.conditions:
                            if cond.params.get("person", "").lower() == person.lower():
                                relevant = True
                                break
                    if not relevant:
                        continue

            # Execute in background
            self._execute_worker_background(worker)

    # ========================================================================
    # Utility
    # ========================================================================

    def get_status_summary(self) -> str:
        """Get a human-readable summary of all workers."""
        with self._lock:
            if not self._workers:
                return "No workers scheduled."

            lines = []
            for w in self._workers:
                status_icon = {
                    "pending": "⏳",
                    "running": "🔄",
                    "completed": "✅",
                    "failed": "❌",
                    "cancelled": "🚫"
                }.get(w.status, "❓")
                lines.append(f"{status_icon} {w}")
            return "\n".join(lines)

    def get_workers_context_summary(self) -> str:
        """
        Get a summary of active workers for injection into Kiki's system prompt.
        This keeps Kiki aware of his scheduled tasks.
        """
        with self._lock:
            active = [w for w in self._workers if w.is_active()]
            if not active:
                return ""

            lines = ["## YOUR SCHEDULED WORKERS (Background Tasks)"]
            for w in active:
                trigger_info = ""
                if w.trigger.trigger_type == TriggerType.SCHEDULED_TIME.value and w.trigger.scheduled_time:
                    trigger_info = f"at {w.trigger.scheduled_time}"
                elif w.trigger.trigger_type == TriggerType.EVENT.value and w.trigger.event_name:
                    trigger_info = f"on '{w.trigger.event_name}' event"
                elif w.trigger.trigger_type == TriggerType.RECURRING.value and w.trigger.interval_seconds:
                    trigger_info = f"every {w.trigger.interval_seconds}s"

                conditions_info = ""
                if w.conditions:
                    cond_parts = []
                    for c in w.conditions:
                        if c.condition_type == "person_seen":
                            cond_parts.append(f"{c.params.get('person', '?')} must be present")
                        else:
                            cond_parts.append(str(c.params))
                    conditions_info = f" (conditions: {', '.join(cond_parts)})"

                lines.append(f"- [{w.status}] '{w.name}': {w.task_description[:100]} — triggers {trigger_info}{conditions_info}")

            return "\n".join(lines)


# ============================================================================
# Module-Level Singleton
# ============================================================================

_worker_manager: Optional[WorkerManager] = None


def get_worker_manager(loop: Optional[asyncio.AbstractEventLoop] = None, message_history: list = None) -> WorkerManager:
    """Get or create the global WorkerManager singleton."""
    global _worker_manager
    if _worker_manager is None:
        if loop is None:
            loop = asyncio.get_running_loop()
        _worker_manager = WorkerManager(loop, message_history=message_history)
    elif message_history is not None and _worker_manager._message_history is None:
        _worker_manager._message_history = message_history
    return _worker_manager
