"""
Worker Engine — Data Models
============================

Defines the Worker, WorkerTrigger, and related enums/dataclasses.
Workers are Kiki's autonomous background tasks: LLM-powered agents
that execute at specific times, on events, or on recurring schedules.
"""

import uuid
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class WorkerStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TriggerType(str, Enum):
    SCHEDULED_TIME = "scheduled_time"   # Run at a specific datetime
    EVENT = "event"                      # Run on a lifecycle event
    RECURRING = "recurring"              # Run every N seconds


# Valid event names for event-triggered workers
VALID_EVENTS = {
    "startup",        # When KikiFast starts
    "shutdown",       # When KikiFast shuts down
    "sleep",          # When Kiki enters sleep mode
    "wake",           # When Kiki wakes from sleep
    "after_response", # After each LLM response cycle
    "face_detected",  # When a specific face is detected
}


@dataclass
class WorkerTrigger:
    """Defines when a worker should fire."""
    trigger_type: str  # TriggerType value

    # For scheduled_time
    scheduled_time: Optional[str] = None  # ISO datetime e.g. "2026-03-04T17:00:00"

    # For event-based
    event_name: Optional[str] = None  # e.g. "startup", "shutdown", "sleep", etc.

    # For recurring
    interval_seconds: Optional[int] = None  # Run every N seconds
    last_fired_at: Optional[str] = None     # Track last fire time for recurring

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerTrigger":
        return cls(
            trigger_type=data.get("trigger_type", "scheduled_time"),
            scheduled_time=data.get("scheduled_time"),
            event_name=data.get("event_name"),
            interval_seconds=data.get("interval_seconds"),
            last_fired_at=data.get("last_fired_at"),
        )


@dataclass
class WorkerCondition:
    """A pre-condition that must be satisfied before a worker executes."""
    condition_type: str    # "person_seen" | "time_range" | "custom"
    params: Dict[str, Any] = field(default_factory=dict)
    # Example: {"type": "person_seen", "params": {"person": "Vaibhav", "within_minutes": 60}}

    def to_dict(self) -> dict:
        return {"condition_type": self.condition_type, "params": self.params}

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerCondition":
        return cls(
            condition_type=data.get("condition_type", "custom"),
            params=data.get("params", data)  # Backwards-compat: if flat dict, use as params
        )


@dataclass
class Worker:
    """A single autonomous worker task."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    task_description: str = ""
    trigger: WorkerTrigger = field(default_factory=lambda: WorkerTrigger(trigger_type="scheduled_time"))
    conditions: List[WorkerCondition] = field(default_factory=list)
    status: str = WorkerStatus.PENDING.value
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_run_at: Optional[str] = None
    last_result: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    created_by: str = "kiki"  # "kiki" or "user"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "task_description": self.task_description,
            "trigger": self.trigger.to_dict(),
            "conditions": [c.to_dict() for c in self.conditions],
            "status": self.status,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "last_result": self.last_result,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Worker":
        trigger_data = data.get("trigger", {})
        conditions_data = data.get("conditions", [])
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            task_description=data.get("task_description", ""),
            trigger=WorkerTrigger.from_dict(trigger_data),
            conditions=[WorkerCondition.from_dict(c) for c in conditions_data],
            status=data.get("status", WorkerStatus.PENDING.value),
            created_at=data.get("created_at", datetime.now().isoformat()),
            last_run_at=data.get("last_run_at"),
            last_result=data.get("last_result"),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            created_by=data.get("created_by", "kiki"),
        )

    def is_active(self) -> bool:
        """Check if worker can still be triggered."""
        return self.status in (WorkerStatus.PENDING.value, WorkerStatus.FAILED.value)

    def mark_running(self):
        self.status = WorkerStatus.RUNNING.value
        self.last_run_at = datetime.now().isoformat()

    def mark_completed(self, result: str = ""):
        self.status = WorkerStatus.COMPLETED.value
        self.last_result = result

    def mark_failed(self, error: str = ""):
        self.retry_count += 1
        if self.retry_count >= self.max_retries:
            self.status = WorkerStatus.FAILED.value
        else:
            self.status = WorkerStatus.PENDING.value  # Keep pending for retry
        self.last_result = f"FAILED: {error}"

    def mark_cancelled(self):
        self.status = WorkerStatus.CANCELLED.value

    def __str__(self) -> str:
        trigger_info = self.trigger.trigger_type
        if self.trigger.scheduled_time:
            trigger_info += f" @ {self.trigger.scheduled_time}"
        elif self.trigger.event_name:
            trigger_info += f" on '{self.trigger.event_name}'"
        elif self.trigger.interval_seconds:
            trigger_info += f" every {self.trigger.interval_seconds}s"
        return f"Worker[{self.id}] '{self.name}' ({trigger_info}) [{self.status}]"
