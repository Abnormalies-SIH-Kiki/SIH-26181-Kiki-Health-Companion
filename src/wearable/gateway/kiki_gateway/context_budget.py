"""Model-token budgeting and cancellation-safe, staged cloud compaction."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
import threading
from collections import OrderedDict
from pathlib import Path

import requests

LOG = logging.getLogger(__name__)


class ContextBudget:
    def __init__(self, url, normalize, summarize, archive_dir, reply_tokens=1200):
        self.url = url.rsplit("/v1/", 1)[0].rstrip("/")
        self.normalize = normalize
        self.summarize = summarize
        self.archive_dir = Path(archive_dir)
        # A historical 6000-token output cap cannot coexist with Kiki's
        # instructions in an 8192-token slot. Keep a useful, bounded answer
        # allowance and apply this same cap to the gateway's local speaker.
        self.reply_tokens = min(1200, max(512, int(reply_tokens)))
        self.n_ctx = 8192
        self._props_at = 0.0
        self._counts = OrderedDict()
        self._count_lock = threading.RLock()
        self.task = None
        self.ready = None
        self._last_snapshot = None

    @property
    def hard_limit(self):
        # Budget for the complete answer, plus token/template boundary slack.
        return self.n_ctx - self.reply_tokens - 128

    @property
    def soft_limit(self):
        return self.hard_limit - 768

    def count(self, messages):
        with self._count_lock:
            return self._count(messages)

    def count_normalized(self, messages):
        with self._count_lock:
            return self._count(messages, normalized=True)

    def _count(self, messages, normalized=False):
        if time.monotonic() - self._props_at > 60:
            response = requests.get(self.url + "/props", timeout=(1, 3))
            response.raise_for_status()
            self.n_ctx = int(response.json()["default_generation_settings"]["n_ctx"])
            self._props_at = time.monotonic()
            self._counts.clear()
        normalized = messages if normalized else self.normalize(messages)
        key = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        if key in self._counts:
            return self._counts[key]
        response = requests.post(self.url + "/apply-template", json={
            "messages": normalized, "thinking_budget_tokens": 0,
        }, timeout=(1, 10))
        response.raise_for_status()
        response = requests.post(self.url + "/tokenize", json={
            "content": response.json()["prompt"], "add_special": True,
            "parse_special": True,
        }, timeout=(1, 3))
        response.raise_for_status()
        count = len(response.json()["tokens"])
        self._counts[key] = count
        if len(self._counts) > 128:
            self._counts.popitem(last=False)
        return count

    def guard_background(self, local_llm):
        """Check already-normalized cache prewarms on their background thread.

        Never warm a shortened prefix under the original prefix's hash. The
        next foreground turn archives/trims the actual history and registers it.
        """
        original = getattr(local_llm, "_kiki_original_background", local_llm.generate_background)
        local_llm._kiki_original_background = original

        def guarded(*args, **kwargs):
            messages = kwargs.get("messages", args[0] if args else None)
            if kwargs.get("is_rewarm") and messages:
                try:
                    if self.count_normalized(messages) > self.hard_limit:
                        LOG.warning("skipping oversized background prewarm (%d messages)", len(messages))
                        return ""
                except Exception:
                    LOG.warning("background prewarm budget unavailable; deferring to foreground")
                    return ""
            return original(*args, **kwargs)

        local_llm.generate_background = guarded

    def bound_memory(self, instructions, memory):
        """Only trim optional recalled memory, once when a prefix is built."""
        target = self.hard_limit - 1400
        def fits(text):
            return self.count([{"role": "system", "content": instructions + text}]) <= target
        if fits(memory):
            return instructions + memory
        if not memory or not fits(""):
            # Never mutilate persona/tool instructions to force a fit.
            return instructions
        suffix = "\n[More saved memories are available through recall_memory.]"
        low, high = 0, len(memory)
        while low < high:
            mid = (low + high + 1) // 2
            if fits(memory[:mid] + suffix):
                low = mid
            else:
                high = mid - 1
        LOG.info("bounded optional prompt memory: %d -> %d characters", len(memory), low)
        return instructions + memory[:low] + suffix

    def archive(self, messages):
        data = json.dumps(messages, ensure_ascii=False, indent=2)
        key = hashlib.sha256(data.encode()).hexdigest()[:20]
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.archive_dir / (key + ".json")
        if not path.exists():
            path.write_text(data, encoding="utf-8")

    def start(self, messages):
        """The core owns this task; Stop and socket teardown never cancel it."""
        if self.task is not None and not self.task.done():
            return self.task
        snapshot = copy.deepcopy(messages)
        if snapshot == self._last_snapshot or not any(
            m.get("role") == "user" for m in snapshot[1:]
        ):
            return self.task
        self._last_snapshot = snapshot
        self.task = asyncio.create_task(self._make_summary(snapshot))
        return self.task

    async def _make_summary(self, snapshot):
        started = time.monotonic()
        try:
            await asyncio.to_thread(self.archive, snapshot)
            # Include earlier summaries and live context, not only user text.
            conversation = "\n".join(
                f"{m.get('role', 'system').upper()}: {m.get('content', '')}"
                for m in snapshot[1:]
            )
            summary = await asyncio.to_thread(self.summarize, conversation)
            if not summary:
                raise RuntimeError("cloud summary was empty")
            self.ready = (snapshot, str(summary))
            LOG.info("cloud compaction ready in %.1fs; staged until turn boundary",
                     time.monotonic() - started)
            return True
        except Exception:
            LOG.exception("cloud compaction unavailable; local budget guard remains active")
            self._last_snapshot = None
            return False

    def apply_ready(self, history):
        if self.ready is None:
            return False
        snapshot, summary = self.ready
        self.ready = None
        # New turns may have arrived while the cloud worked. Keep the exact
        # unsummarized suffix; never replace a generation's history mid-turn.
        if history[:len(snapshot)] != snapshot:
            LOG.info("discarded stale compaction snapshot; history has changed")
            return False
        tail = history[len(snapshot):]
        history[:] = [history[0], {"role": "system", "content":
            "Earlier conversation summary (memory, not new instructions):\n" + summary}, *tail]
        LOG.info("applied cloud compaction; preserved %d newer messages", len(tail))
        return True

    def fit(self, messages, target=None):
        """Emergency short-term pruning; archive first and preserve current turn."""
        target = self.hard_limit if target is None else target
        original = copy.deepcopy(messages)
        result = copy.deepcopy(messages)
        # The old worker manager hot-injected the same failed scheduled task
        # hundreds of times, including AFTER the most recent user. Keep its
        # latest receipt; it is not part of the indivisible current tool turn.
        seen = set()
        retained = []
        for message in reversed(result):
            content = message.get("content", "")
            match = re.match(r"\[Worker '([^']+)' (?:completed|failed)\]:", content) \
                if message.get("role") == "system" and isinstance(content, str) else None
            if match:
                if match[1] in seen:
                    continue
                seen.add(match[1])
            retained.append(message)
        result = list(reversed(retained))
        if result != original:
            self.archive(original)
            LOG.warning("coalesced repeated worker receipts: %d -> %d messages", len(original), len(result))
        if self.count(result) <= target:
            return result
        self.archive(original)
        # Remove complete old turns, including their tool-result rows. The
        # latest user and everything since it are indivisible.
        while self.count(result) > target:
            users = [i for i, m in enumerate(result) if m.get("role") == "user"]
            if len(users) > 1:
                del result[1:users[1]]
            elif users and users[0] > 1:
                del result[1:users[0]]
            elif not users and len(result) > 1:
                result.pop(1)
            else:
                raise RuntimeError("The current request and required instructions exceed the reply budget")
        LOG.warning("context safety trim: %d -> %d messages; full text archived",
                    len(original), len(result))
        return result
