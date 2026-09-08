"""Persistent dated research for the Unified Idle Mind.

The cloud background agent writes full findings here while the speaking context
only carries one model-chosen next-turn note. Full details are retrieved through
the unified ``recall_memory`` tool.
"""

import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from typing import List, Optional

from tools_and_config.config_loader import get_full_config


_cfg = get_full_config().get("idle_mind", {})
JOURNAL_FILE = _cfg.get(
    "journal_file", "/home/vaibhav/KikiESP32/gateway/legacy_kiki/thinking_journal.json")
MAX_ENTRIES = _cfg.get("journal_max_entries", 300)
MAX_OPEN_QUESTIONS = _cfg.get("max_open_questions", 10)
_DETAILS_CAP = 2000


class ThinkingJournal:
    """Atomic journal storage for research and unresolved curiosity."""

    def __init__(self, file_path: str = JOURNAL_FILE):
        self.file_path = file_path
        self._lock = threading.Lock()
        self._data = {
            "schema_version": 2,
            "entries": [],
            "open_questions": [],
        }
        self._load()

    def _load(self):
        try:
            if not os.path.exists(self.file_path):
                return
            with open(self.file_path, "r") as file:
                data = json.load(file)
            if isinstance(data, dict):
                self._data["schema_version"] = data.get("schema_version", 1)
                self._data["entries"] = data.get("entries", [])
                self._data["open_questions"] = data.get("open_questions", [])
        except Exception as exc:
            print(f"[ThinkingJournal] Load failed ({exc}), starting fresh")

    def _save(self):
        try:
            tmp = self.file_path + ".tmp"
            with open(tmp, "w") as file:
                json.dump(self._data, file, indent=2)
            os.replace(tmp, self.file_path)
        except Exception as exc:
            print(f"[ThinkingJournal] Save failed: {exc}")

    def migrate_for_unified_idle_mind(self) -> Optional[str]:
        """Remove the retired multi-point surfacing state once, with a backup."""
        with self._lock:
            if self._data.get("schema_version") == 2:
                return None
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = f"{self.file_path}.legacy_{stamp}.bak"
            try:
                if os.path.exists(self.file_path):
                    shutil.copy2(self.file_path, backup)
            except Exception as exc:
                print(f"[ThinkingJournal] Migration backup failed: {exc}")
                return None
            for entry in self._data["entries"]:
                entry.pop("surfaced", None)
            self._data["schema_version"] = 2
            self._save()
            print(
                f"[ThinkingJournal] Migrated to schema v2; "
                f"legacy backup: {backup}")
            return backup

    def add_entry(
        self,
        focus: str,
        topic: str,
        summary: str,
        details: str = "",
        tools_used: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
    ) -> str:
        """Add a journal entry and return its opaque ID."""
        entry_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._data["entries"].append({
                "id": entry_id,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "epoch": time.time(),
                "focus": focus,
                "topic": (topic or "untitled")[:120],
                "summary": (summary or "")[:500],
                "details": (details or "")[:_DETAILS_CAP],
                "tools_used": tools_used or [],
                "sources": sources or [],
            })
            self._data["entries"] = self._data["entries"][-MAX_ENTRIES:]
            self._save()
        return entry_id

    def save_background_research(
        self,
        topic: str,
        summary: str,
        details: str = "",
        sources: Optional[List[str]] = None,
        tools_used: Optional[List[str]] = None,
        mode: str = "light_research",
    ) -> str:
        """Validated, deduplicated write used by the Unified Idle Mind."""
        topic = (topic or "").strip()
        summary = (summary or "").strip()
        if not topic or not summary:
            return "Error: topic and summary are required."
        if self.is_recent_duplicate(topic, summary, n=12, threshold=0.65):
            return f"Skipped duplicate background research: '{topic}'."
        focus = (
            mode if mode in ("light_research", "deep_research")
            else "background_research"
        )
        entry_id = self.add_entry(
            focus=focus,
            topic=topic,
            summary=summary,
            details=details,
            tools_used=tools_used,
            sources=sources,
        )
        return f"Saved background research '{topic}' as {entry_id}."

    def recent_summaries(self, n: int = 8) -> str:
        """Compact recent research for anti-repetition context."""
        with self._lock:
            entries = self._data["entries"][-n:]
        if not entries:
            return "Nothing yet."
        return "\n".join(
            f"- [{entry['timestamp']}] ({entry['focus']}) "
            f"{entry['topic']}: {entry['summary']}"
            for entry in entries
        )

    @staticmethod
    def _word_set(text: str) -> set:
        return {
            word
            for word in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(word) > 3
        }

    def is_recent_duplicate(
        self,
        topic: str,
        summary: str = "",
        n: int = 10,
        threshold: float = 0.5,
    ) -> bool:
        """Reject substantial overlap with recent saved research."""
        new_words = self._word_set(f"{topic} {summary}")
        if not new_words:
            return False
        with self._lock:
            recent = self._data["entries"][-n:]
        for entry in recent:
            old_words = self._word_set(
                f"{entry['topic']} {entry['summary']}")
            if not old_words:
                continue
            overlap = len(new_words & old_words) / min(
                len(new_words), len(old_words))
            if overlap >= threshold:
                return True
        return False

    def search(self, topic: str, max_results: int = 3) -> str:
        """Keyword search over full journal details, newest matches first."""
        words = [
            word for word in (topic or "").lower().split()
            if len(word) > 2
        ]
        if not words:
            return "No search terms given."
        with self._lock:
            entries = list(reversed(self._data["entries"]))
        scored = []
        for entry in entries:
            haystack = (
                f"{entry['topic']} {entry['summary']} "
                f"{entry['details']}"
            ).lower()
            score = sum(1 for word in words if word in haystack)
            if score:
                scored.append((score, entry))
        if not scored:
            return f"No thinking-journal entries found about '{topic}'."
        scored.sort(key=lambda item: -item[0])
        return "\n\n".join(
            f"[{entry['timestamp']}] {entry['topic']}\n"
            f"Summary: {entry['summary']}\n"
            f"Details: {entry['details'] or '(no details)'}"
            for _, entry in scored[:max_results]
        )

    def add_open_questions(
        self, questions: List[str], source_entry_id: str = ""
    ):
        """Add deduplicated curiosity threads for later background sessions."""
        with self._lock:
            existing = {
                question["text"].lower()
                for question in self._data["open_questions"]
            }
            for question in questions or []:
                question = (question or "").strip()[:200]
                if not question or question.lower() in existing:
                    continue
                unresolved = [
                    item for item in self._data["open_questions"]
                    if not item["resolved"]
                ]
                if len(unresolved) >= MAX_OPEN_QUESTIONS:
                    break
                self._data["open_questions"].append({
                    "text": question,
                    "created": datetime.now().isoformat(timespec="seconds"),
                    "source_entry_id": source_entry_id,
                    "resolved": False,
                })
                existing.add(question.lower())
            self._data["open_questions"] = self._data[
                "open_questions"][-MAX_OPEN_QUESTIONS * 5:]
            self._save()

    def pending_open_questions(self, n: int = 5) -> List[str]:
        """Return oldest-first unresolved curiosity questions."""
        with self._lock:
            return [
                question["text"]
                for question in self._data["open_questions"]
                if not question["resolved"]
            ][:n]

    def resolve_open_questions(self, answered: List[str]):
        """Resolve questions using word overlap rather than exact wording."""
        if not answered:
            return
        with self._lock:
            changed = False
            for answer in answered:
                answer_words = self._word_set(answer)
                if not answer_words:
                    continue
                for question in self._data["open_questions"]:
                    if question["resolved"]:
                        continue
                    question_words = self._word_set(question["text"])
                    if (
                        question_words
                        and len(answer_words & question_words)
                        / min(len(answer_words), len(question_words)) >= 0.5
                    ):
                        question["resolved"] = True
                        changed = True
            if changed:
                self._save()


_journal: Optional[ThinkingJournal] = None
_journal_lock = threading.Lock()


def get_journal() -> ThinkingJournal:
    global _journal
    if _journal is None:
        with _journal_lock:
            if _journal is None:
                _journal = ThinkingJournal()
    return _journal
