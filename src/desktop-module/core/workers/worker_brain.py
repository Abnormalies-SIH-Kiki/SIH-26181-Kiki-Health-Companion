"""
Worker Brain — LLM Execution Engine for Workers
=================================================

Each worker gets its own mini "brain" that:
1. Checks pre-conditions (face seen, time range, etc.)
2. Builds a task-specific prompt with full tool access
3. Runs a multi-turn LLM loop (generate → tool calls → feed back → repeat)
4. Retries on failure with adjusted prompts
5. Reports results back to the WorkerManager

Uses the same multi-provider router as Unified Idle Mind.
Non-streaming since workers are background tasks (no TTS needed).
"""

from pathlib import Path
import asyncio
import time
import subprocess
from datetime import datetime
from typing import Optional, List, Dict, Any
import os
from core.workers.worker_engine import Worker, WorkerCondition
from core.observability import observe


# The agent-loop engine now lives in core/agent_loop.py. Re-exported here so
# Existing worker imports keep working unchanged.
from core.agent_loop import run_agent_loop

# Ordinary scheduled workers must not control live interaction/identity,
# physical tracking, or write into the Unified Idle Mind's private state.
_WORKER_BLOCKED_TOOLS = {
    "look_at_scene",
    "track_person",
    "switch_voice",
    "switch_mode",
    "set_followups",
    "adjust_volume",
    "remember_me",
    "set_person_real_name",
    "save_background_research",
    "set_next_turn_note",
    "add_open_question",
    "resolve_open_question",
}


def _worker_tool_guard(name, _args, _parsed, _total, _used):
    if str(name or "").lower() in _WORKER_BLOCKED_TOOLS:
        return False, "tool is unavailable to ordinary scheduled workers"
    return True, "ok"

# ============================================================================
# Face History Buffer (shared, populated by face_handler + main.py)
# ============================================================================

class FaceHistoryBuffer:
    """
    Thread-safe rolling buffer of face detection events.
    Populated by the face event listener, read by worker condition checks.
    """
    def __init__(self, max_entries: int = 200):
        self._events: List[Dict[str, Any]] = []
        self._max = max_entries
        import threading
        self._lock = threading.Lock()

    def record_face(self, person_name: str, event_type: str = "detected"):
        with self._lock:
            self._events.append({
                "person": person_name,
                "type": event_type,
                "timestamp": datetime.now().isoformat(),
                "epoch": time.time(),
            })
            if len(self._events) > self._max:
                self._events = self._events[-self._max:]

    def person_seen_within(self, person_name: str, within_minutes: int) -> bool:
        """Check if a person was seen within the last N minutes."""
        cutoff = time.time() - (within_minutes * 60)
        with self._lock:
            for event in reversed(self._events):
                if event["epoch"] < cutoff:
                    break
                if (event["person"].lower() == person_name.lower() and
                        event["type"] == "detected"):
                    return True
        return False

    def get_recent(self, minutes: int = 60) -> List[Dict[str, Any]]:
        """Get face events from the last N minutes."""
        cutoff = time.time() - (minutes * 60)
        with self._lock:
            return [e for e in self._events if e["epoch"] >= cutoff]


# Global singleton
_face_history: Optional[FaceHistoryBuffer] = None


def get_face_history() -> FaceHistoryBuffer:
    global _face_history
    if _face_history is None:
        _face_history = FaceHistoryBuffer()
    return _face_history


# ============================================================================
# Vision Context History (shared, populated by vision_handler)
# ============================================================================

class VisionContextHistory:
    """
    Rolling buffer of vision analysis results.
    Populated by VisionHandler after each vision update.
    """
    def __init__(self, max_entries: int = 50):
        self._history: List[Dict[str, Any]] = []
        self._max = max_entries
        import threading
        self._lock = threading.Lock()

    def record_vision(self, context: str):
        with self._lock:
            self._history.append({
                "context": context,
                "timestamp": datetime.now().isoformat(),
                "epoch": time.time(),
            })
            if len(self._history) > self._max:
                self._history = self._history[-self._max:]

    def get_recent(self, minutes: int = 60) -> List[Dict[str, Any]]:
        """Get vision contexts from the last N minutes."""
        cutoff = time.time() - (minutes * 60)
        with self._lock:
            return [v for v in self._history if v["epoch"] >= cutoff]

    def get_latest(self) -> Optional[str]:
        with self._lock:
            return self._history[-1]["context"] if self._history else None


_vision_history: Optional[VisionContextHistory] = None


def get_vision_history() -> VisionContextHistory:
    global _vision_history
    if _vision_history is None:
        _vision_history = VisionContextHistory()
    return _vision_history


# ============================================================================
# Condition Checker
# ============================================================================

def check_conditions(conditions: List[WorkerCondition]) -> tuple[bool, str]:
    """
    Check if all worker conditions are met.
    Returns (all_met: bool, reason: str).
    """
    if not conditions:
        return True, "No conditions to check"

    face_history = get_face_history()
    failed = []

    for cond in conditions:
        if cond.condition_type == "person_seen":
            person = cond.params.get("person", "")
            within = cond.params.get("within_minutes", 60)
            if not face_history.person_seen_within(person, within):
                failed.append(f"'{person}' not seen in the last {within} minutes")

        elif cond.condition_type == "time_range":
            start_hour = cond.params.get("start_hour", 0)
            end_hour = cond.params.get("end_hour", 24)
            current_hour = datetime.now().hour
            if not (start_hour <= current_hour < end_hour):
                failed.append(f"Current hour {current_hour} not in range [{start_hour}, {end_hour})")

        elif cond.condition_type == "custom":
            # Custom conditions are evaluated by the LLM itself
            pass

        else:
            print(f"[WorkerBrain] Unknown condition type: {cond.condition_type}")

    if failed:
        return False, "; ".join(failed)
    return True, "All conditions met"


# ============================================================================
# Worker Brain — LLM Execution
# ============================================================================



@observe("worker")
async def execute_worker(worker: Worker) -> tuple[bool, str, str | None]:
    """
    Execute a worker's task using the LLM with full tool access.
    
    This is the main entry point. It:
    1. Checks conditions
    2. Builds the LLM prompt with context
    3. Runs a multi-turn tool loop
    4. Returns (success, result_text, speak_text_or_None)
       - speak_text is the text to speak aloud via TTS (None = silent worker)
    """
    from tools_and_config.tools import get_detailed_tool_descriptions

    print(f"\n{'=' * 50}")
    print(f"[WorkerBrain] Executing: {worker}")
    print(f"{'=' * 50}")

    # 1. Check pre-conditions
    conditions_met, condition_reason = check_conditions(worker.conditions)
    if not conditions_met:
        msg = f"Conditions not met: {condition_reason}"
        print(f"[WorkerBrain] {msg}")
        return False, msg, None

    print(f"[WorkerBrain] Conditions satisfied: {condition_reason}")

    # 2. Build context
    vision_history = get_vision_history()
    face_history = get_face_history()

    recent_vision = vision_history.get_recent(minutes=60)
    recent_faces = face_history.get_recent(minutes=60)

    vision_context = ""
    if recent_vision:
        vision_context = "\n".join([
            f"  [{v['timestamp']}] {v['context'][:200]}"
            for v in recent_vision[-5:]  # Last 5 vision snapshots
        ])

    face_context = ""
    if recent_faces:
        face_context = "\n".join([
            f"  [{f['timestamp']}] {f['person']} ({f['type']})"
            for f in recent_faces[-10:]  # Last 10 face events
        ])

    # Build tool descriptions for the prompt using the detailed formatter
    tool_desc = get_detailed_tool_descriptions(
        excluded_names=_WORKER_BLOCKED_TOOLS)

    # Full long-term context (workers run on cloud → no ctx limit, so hand the
    # worker everything Kiki knows about the people/environment it serves).
    try:
        from core.brain.knowledge_base import get_knowledge_summary
        kb_summary = get_knowledge_summary(max_lines=250) or "Nothing yet."
    except Exception:
        kb_summary = "Nothing yet."

    current_time = datetime.now().strftime("%I:%M %p on %A, %B %d, %Y")

    worker_prompt = f"""You are Kiki's Worker Brain — an autonomous agent executing a specific task.
You have been scheduled to do this job and you MUST complete it.
You ARE Kiki — the witty, caring robot companion.

## YOUR TASK
{worker.task_description}

## CURRENT TIME
{current_time}

## AVAILABLE TOOLS
You can call ANY of these tools to complete your task. Return tool calls as JSON.
{tool_desc}

## WHAT KIKI KNOWS (long-term memory)
{kb_summary}

## RECENT VISION CONTEXT (What Kiki has seen recently)
{vision_context if vision_context else "No recent vision data available."}

## RECENT FACE DETECTIONS
{face_context if face_context else "No recent face detections."}

## INSTRUCTIONS
1. Analyze your task and decide what tools to call
2. If you need to call tools, respond with a JSON object:
   {{"tool_calls": [{{"tool": "tool_name", "args": {{"arg1": "value1"}}}}]}}
3. After receiving tool results, continue until your task is complete
4. When done, respond with a JSON object:
   {{"status": "completed", "summary": "What you accomplished", "speak": true/false, "speak_text": "What Kiki should say aloud (conversational, friendly, in Kiki's voice)"}}
   - Set "speak": true if the result is something Kiki should announce or say to the people around him
   - Set "speak_text" to what Kiki should SAY — make it sound like Kiki talking naturally, NOT a status report
   - Example speak_text: "Hey Vaibhav! I found some chill lo-fi beats based on your vibe. Playing it now!"
5. If you cannot complete the task, respond with:
   {{"status": "failed", "reason": "Why it failed", "speak": true/false, "speak_text": "optional message"}}

{f'## RETRY NOTE: This is retry #{worker.retry_count}. Previous attempt failed: {worker.last_result}. Try a different approach.' if worker.retry_count > 0 else ''}

Complete your task now."""

    # 3. Multi-turn LLM loop (shared engine) — observed as a grouped session so
    # the Web UI shows the worker's whole run (prompt, LLM turns, tool calls).
    from core.observability import get_recorder
    _sid = get_recorder().start_session(
        "worker", name=worker.name, task=worker.task_description, prompt=worker_prompt)

    # Drive the OLED "workers" animation + live narration while this runs.
    _oled_progress = None
    try:
        from core.oled_display import oled_manager, make_progress_fn
        _wname = (worker.name or "background task")[:18]
        oled_manager.set_progress("workers", f"starting {_wname}", _wname)
        _oled_progress = make_progress_fn("workers", prefix=_wname)
    except Exception:
        oled_manager = None

    # Workers run on cloud (no ctx limit) → don't strip the prompt or tool
    # results; a hard tool-call ceiling still bounds runaway loops.
    try:
        from tools_and_config.config_loader import get_full_config
        _wcfg = get_full_config().get("workers", {})
    except Exception:
        _wcfg = {}
    success, result, speak_text, _final_json, tools_used = await run_agent_loop(
        worker_prompt, session_id=_sid, progress_fn=_oled_progress,
        max_tool_calls=_wcfg.get("max_tool_calls", 12),
        max_calls_per_turn=_wcfg.get("max_calls_per_turn", 4),
        max_prompt_chars=10_000_000, max_tool_result_chars=1_000_000,
        tool_guard_fn=_worker_tool_guard)
    get_recorder().end_session(
        _sid, status="done" if success else "failed",
        result=str(result)[:800], tools_used=tools_used)

    # Final narration, then hand the screen back to idle.
    if oled_manager is not None:
        try:
            tail = " ".join(str(result).split())[:120]
            oled_manager.push_status(
                f"{worker.name}: {'done' if success else 'failed'}"
                + (f" — {tail}" if tail else ""))
            # Persist the worker outcome in the background-activity feed so the
            # resting OLED can surface it for a while.
            oled_manager.log_activity(
                f"WORKER · {worker.name}"[:16],
                f"{'done' if success else 'failed'}: {tail}" if tail
                else ("done" if success else "failed"))
            # Only hand the screen back if we're still showing the worker view
            # (a conversation may have grabbed the display while we ran).
            if getattr(oled_manager, "_state", None) == "workers":
                oled_manager.set_state("idle")
        except Exception:
            pass
    return success, result, speak_text


# ============================================================================
# Python Code Execution Tool
# ============================================================================

async def _execute_python_code(code: str, timeout: int = 30) -> str:
    """Execute Python code in a subprocess and return output."""
    if not code.strip():
        return "Error: No code provided"

    try:
        print(f"[WorkerBrain] Executing Python code ({len(code)} chars)...")
        loop = asyncio.get_running_loop()
        import tempfile
        import uuid
        
        # Write code to a temp file to avoid any command line quote escaping issues
        temp_dir = tempfile.gettempdir()
        filename = f"worker_script_{uuid.uuid4().hex[:8]}.py"
        file_path = os.path.join(temp_dir, filename)
        
        with open(file_path, "w") as f:
            f.write(code)

        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["python3", file_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(Path(__file__).resolve().parents[2])
                )
            )
            output = ""
            if result.stdout:
                output += result.stdout.strip()
            if result.stderr:
                output += f"\nSTDERR: {result.stderr.strip()}"
            return output if output else "Code executed with no output."
        except subprocess.TimeoutExpired:
            return f"Error: Code execution timed out after {timeout}s"
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
            
    except Exception as e:
        return f"Error executing code: {e}"
