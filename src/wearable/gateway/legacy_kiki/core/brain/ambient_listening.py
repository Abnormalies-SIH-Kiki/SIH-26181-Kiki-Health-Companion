"""Crash-safe ambient transcript capture for the Unified Idle Mind.

This module deliberately performs no cloud interpretation. ``main.py`` routes
passive STT transcripts here, and ``UnifiedIdleMindManager`` snapshots and
consumes them as part of its next background session.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path


DEFAULT_BUFFER_PATH = "/home/vaibhav/KikiESP32/gateway/legacy_kiki/ambient_listen_buffer.json"


class AmbientListeningManager:
    """Collect finalized ambient transcripts without starting another agent."""

    def __init__(self, full_config: dict):
        raw_flag = full_config.get("always_listen", False)
        embedded_cfg = raw_flag if isinstance(raw_flag, dict) else {}
        cfg = dict(full_config.get("always_listen_config", {}) or {})
        cfg.update(embedded_cfg)

        self.enabled = (
            bool(embedded_cfg.get("enabled", False))
            if embedded_cfg else bool(raw_flag)
        )
        self.min_batch_words = max(1, int(cfg.get("min_batch_words", 3)))
        self.max_buffer_sentences = max(
            10, int(cfg.get("max_buffer_sentences", 500)))
        self.max_sentence_chars = max(
            80, int(cfg.get("max_sentence_chars", 600)))
        self.buffer_path = Path(
            cfg.get("buffer_file", DEFAULT_BUFFER_PATH))
        self._buffer: list[dict] = []
        self._buffer_lock = threading.Lock()

        if self.enabled:
            self._load_buffer()

    def _load_buffer(self):
        try:
            if not self.buffer_path.exists():
                return
            data = json.loads(self.buffer_path.read_text())
            if not isinstance(data, list):
                return
            restored = []
            for item in data:
                if not isinstance(item, dict) or not item.get("text"):
                    continue
                normalized = dict(item)
                normalized.setdefault("id", uuid.uuid4().hex[:12])
                normalized.setdefault(
                    "timestamp", datetime.now().isoformat(timespec="seconds"))
                normalized.setdefault("epoch", time.time())
                restored.append(normalized)
            self._buffer = restored[-self.max_buffer_sentences:]
            if self._buffer:
                print(
                    f"[AlwaysListen] Restored {len(self._buffer)} "
                    "buffered sentence(s)")
        except Exception as exc:
            print(f"[AlwaysListen] Could not restore transcript buffer: {exc}")

    def _save_buffer_locked(self):
        try:
            self.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.buffer_path.with_suffix(self.buffer_path.suffix + ".tmp")
            tmp.write_text(json.dumps(
                self._buffer, indent=2, ensure_ascii=False))
            os.replace(tmp, self.buffer_path)
        except Exception as exc:
            print(f"[AlwaysListen] Could not save transcript buffer: {exc}")

    def add_sentence(self, text: str) -> bool:
        """Buffer one finalized ambient transcript."""
        if not self.enabled:
            return False
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return False
        cleaned = cleaned[:self.max_sentence_chars]
        item = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "epoch": time.time(),
            "text": cleaned,
        }
        with self._buffer_lock:
            self._buffer.append(item)
            self._buffer = self._buffer[-self.max_buffer_sentences:]
            self._save_buffer_locked()
            count = len(self._buffer)
        print(
            f"[AlwaysListen] Buffered ambient sentence "
            f"({count} pending): {cleaned[:120]}")
        return True

    @property
    def pending_count(self) -> int:
        with self._buffer_lock:
            return len(self._buffer)

    def start(self, loop=None):
        if self.enabled:
            print(
                "[AlwaysListen] Enabled — capture-only; "
                "Unified Idle Mind consumes buffered speech")

    async def stop(self):
        return

    def snapshot(self, limit: int = 24) -> list[dict]:
        """Return a stable copy of the newest pending ambient snippets."""
        with self._buffer_lock:
            return [
                dict(item)
                for item in self._buffer[-max(1, int(limit)):]
            ]

    def consume(self, item_ids) -> int:
        """Remove a successfully processed snapshot by opaque item ID."""
        ids = {str(item_id) for item_id in (item_ids or [])}
        if not ids:
            return 0
        with self._buffer_lock:
            before = len(self._buffer)
            self._buffer = [
                item for item in self._buffer
                if str(item.get("id")) not in ids
            ]
            removed = before - len(self._buffer)
            if removed:
                self._save_buffer_locked()
            return removed
