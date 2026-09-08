"""Notice that something was discussed before, without being told.

The failure this fixes: ask Kiki an ordinary question about a topic you have
talked about for months, and she answers from nothing. The existing router
(`core/llm.py::_should_auto_recall_memory`) only fires on explicit cues --
"we discussed", "past conversation", "what do you remember about". A normal
question carries no such cue, so nothing searches, and the speaking model
cannot ask for what it does not know exists.

## Why this is not a relevance threshold

The obvious fix -- run `search_memory` every turn and inject when the score is
high enough -- was measured against the live 3437-record corpus and does not
work. Scores of questions that ARE in memory and questions that are not overlap
almost completely:

    "the openclaw release"        53.5   (real)
    "can you move forward"        43.0   (nothing to recall)
    "play some music"             39.0   (nothing to recall)
    "did we talk about NSUT"      19.7   (real)

Distinctiveness (top hit vs. the rest) and term rarity were both tested and
separate no better; "what is the capital of France" has the single rarest
matched term of any query tried. The cause is structural rather than a tuning
problem: TF-IDF over a large personal corpus finds lexical overlap for any
English sentence, so the score measures "these words occur somewhere", never
"we have discussed this".

## What actually separates them

The things a person asks about again are the things that got *stored as
something*: a person, a fact, a learning, a recorded experience, or a named
background-research topic. So the gate is entity anchoring against knowledge
keys and thinking-journal titles, not free-text scoring.
Measured on the same queries: 11 of 12 real recalls caught, 1 of 14 controls
fired -- and that one ("what's the weather" matching a stored `Delhi_Weather`)
is removed by the rarity gate below, because weather now has a live provider
and has no business being answered from a July memory.

A useful side effect: the anchor test is a dictionary scan over a few hundred
keys. A turn with no anchor never runs the 250 ms search at all, so the
overwhelming majority of turns pay nothing for this feature.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Words that appear in so many stored keys that matching one proves nothing.
_KEY_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "update", "status",
    "new", "get", "use", "are", "was", "his", "her", "its", "our", "about",
    "note", "notes", "info", "data", "general", "misc", "other",
}

# A single matched token only counts when it is genuinely rare in the corpus.
# Measured: moksha 6.44, openclaw 7.23, detection 5.79 -- all real topics;
# weather 4.69, music 3.55, turn 3.87 -- all things Kiki says constantly.
_DEFAULT_MIN_ANCHOR_IDF = 5.5

_MIN_KEY_TOKEN = 4
_MAX_INJECTION_CHARS = 340
_DEFAULT_TIMEOUT = 1.5


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


class EntityIndex:
    """The names durable memory actually stores, for anchor matching.

    Rebuilt when either the knowledge base or thinking journal changes, using
    the same size+mtime signature trick as `MemorySearcher`.  Research used to
    be searchable by the explicit recall tool but invisible to automatic recall
    because only knowledge-base keys were anchors; a normal question about a
    newly researched named topic therefore failed to search at all.
    """

    def __init__(self, knowledge_path: Optional[Path] = None,
                 journal_path: Optional[Path] = None):
        self._explicit_knowledge_path = knowledge_path is not None
        self._path = Path(knowledge_path) if knowledge_path else None
        self._journal_path = Path(journal_path) if journal_path else None
        self._lock = threading.RLock()
        self._entries: List[Tuple[str, Tuple[str, ...], str]] = []
        self._signature: tuple = ()

    def _resolve_path(self) -> Optional[Path]:
        if self._path is not None:
            return self._path
        try:
            from tools_and_config.config_loader import get_full_config
            root = Path(__file__).resolve().parents[2]
            configured = get_full_config().get("knowledge_base", {}).get("file_path")
            self._path = Path(configured) if configured else root / "state" / "knowledge_base.json"
        except Exception:
            self._path = None
        return self._path

    def _resolve_journal_path(self) -> Optional[Path]:
        if self._journal_path is not None:
            return self._journal_path
        # An explicit knowledge path denotes an isolated/test index.  Do not
        # silently mix the robot's live journal into it unless the caller also
        # supplied that journal path explicitly.
        if self._explicit_knowledge_path:
            return None
        try:
            from tools_and_config.config_loader import get_full_config
            root = Path(__file__).resolve().parents[2]
            configured = get_full_config().get("idle_mind", {}).get("journal_file")
            self._journal_path = (
                Path(configured) if configured else root / "state" / "thinking_journal.json")
        except Exception:
            self._journal_path = None
        return self._journal_path

    def _current_signature(self, path: Path) -> tuple:
        try:
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return ()

    def _rebuild_if_needed(self) -> None:
        path = self._resolve_path()
        journal_path = self._resolve_journal_path()
        if path is None and journal_path is None:
            return
        signature = (
            self._current_signature(path) if path is not None else (),
            self._current_signature(journal_path) if journal_path is not None else (),
        )
        with self._lock:
            if signature and signature == self._signature and self._entries:
                return
            self._signature = signature
        try:
            data = (
                json.loads(path.read_text(encoding="utf-8"))
                if path is not None else {})
        except Exception:
            data = {}

        try:
            journal = (
                json.loads(journal_path.read_text(encoding="utf-8"))
                if journal_path is not None else {})
        except Exception:
            journal = {}

        keys = set()
        for section in ("people", "facts", "learnings", "environments"):
            value = data.get(section)
            if isinstance(value, dict):
                keys.update(value.keys())
        for section in ("experiences", "experiences_archive"):
            value = data.get(section)
            if isinstance(value, list):
                for row in value:
                    if isinstance(row, dict) and row.get("event"):
                        keys.add(str(row["event"]))
        for row in journal.get("entries", []):
            if isinstance(row, dict) and row.get("topic"):
                keys.add(str(row["topic"]))

        entries = []
        for key in keys:
            normalized = _norm(key)
            # `guest_20260722_1838` is a face-recognition placeholder, not a
            # topic anyone asks about by name.
            if len(normalized) < 3 or normalized.startswith("guest "):
                continue
            tokens = tuple(
                token for token in normalized.split()
                if len(token) >= _MIN_KEY_TOKEN and token not in _KEY_STOP
            )
            entries.append((normalized, tokens, str(key)))
        with self._lock:
            self._entries = entries

    def size(self) -> int:
        self._rebuild_if_needed()
        with self._lock:
            return len(self._entries)

    def find_anchors(self, query: str, limit: int = 3,
                     min_idf: float = _DEFAULT_MIN_ANCHOR_IDF) -> List[str]:
        """Stored names this question actually mentions."""
        self._rebuild_if_needed()
        normalized = _norm(query)
        if not normalized:
            return []
        padded = f" {normalized} "
        query_tokens = set(normalized.split())
        with self._lock:
            entries = list(self._entries)

        matches: List[str] = []
        for key_text, tokens, original in entries:
            if f" {key_text} " in padded:
                matches.append(original)
                continue
            if not tokens:
                continue
            hit = [token for token in tokens if token in query_tokens]
            if len(hit) >= 2:
                matches.append(original)
            elif len(hit) == 1 and _anchor_token_ok(hit[0], min_idf):
                matches.append(original)
            if len(matches) >= limit:
                break
        return matches[:limit]


def _token_idf(token: str) -> Optional[float]:
    """Corpus rarity of one token, or None when rarity is not yet knowable.

    Returning None rather than a number matters. On an unbuilt search index
    `MemorySearcher._idf` computes log(1 + 1.5/0.5) = 1.386 for *every* term,
    because the corpus size and document frequencies are both zero. That is not
    a low rarity score, it is no score at all -- and treating it as one silently
    rejected every single-token anchor for the half second after boot before the
    index finished building. A caller that cannot get rarity needs to know that,
    so it can fall back rather than quietly mis-answer.
    """
    try:
        from core.brain.memory_search import get_memory_searcher, _stem
        searcher = get_memory_searcher()
        if not getattr(searcher, "_document_frequency", None):
            return None
        return float(searcher._idf(_stem(token)))
    except Exception:
        return None


def _anchor_token_ok(token: str, min_idf: float) -> bool:
    """Is this single token specific enough to stand alone as an anchor?

    Prefers corpus rarity, and falls back to length while the search index is
    still building. The length bar of 7 is the one measured before rarity was
    added: on the real corpus it caught 11 of 12 real recalls against 1 false
    fire, so a boot-time question degrades to slightly-worse rather than broken.
    """
    idf = _token_idf(token)
    if idf is None:
        return len(token) >= 7
    return idf >= min_idf


class AutoRecall:
    """Runs the search off the speaking path and hands back one compact line."""

    def __init__(self, config: Optional[dict] = None):
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", True))
        self.min_anchor_idf = float(cfg.get("min_anchor_idf", _DEFAULT_MIN_ANCHOR_IDF))
        self.max_chars = int(cfg.get("max_injection_chars", _MAX_INJECTION_CHARS))
        self.timeout = float(cfg.get("search_timeout_seconds", _DEFAULT_TIMEOUT))
        self.max_hits = int(cfg.get("max_hits", 2))
        self.index = EntityIndex()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._result: str = ""
        self._started_at: float = 0.0
        self._recent: List[str] = []

    # -- lifecycle --

    def warm(self) -> None:
        """Build both indexes at boot so the first question pays neither.

        The memory index alone takes 0.54 s cold; paying that inside someone's
        first sentence of the day is exactly the kind of stall this module is
        supposed to avoid creating.
        """
        try:
            self.index.size()
            from core.brain.memory_search import get_memory_searcher
            get_memory_searcher()._rebuild_if_needed()
        except Exception as exc:
            print(f"[AutoRecall] warm-up skipped: {exc}")

    def start(self, query: str) -> bool:
        """Begin a search if this question names something stored.

        Returns True when a search was started. Cheap and non-blocking: with no
        anchor it does a dictionary scan and returns, so an ordinary turn costs
        microseconds rather than the 250 ms search.
        """
        with self._lock:
            self._result = ""
            self._thread = None
        if not self.enabled:
            return False
        try:
            anchors = self.index.find_anchors(
                query, min_idf=self.min_anchor_idf)
        except Exception as exc:
            print(f"[AutoRecall] anchor check failed: {exc}")
            return False
        if not anchors:
            return False

        print(f"[AutoRecall] anchors: {', '.join(anchors)}")
        thread = threading.Thread(
            target=self._run, args=(query, anchors), daemon=True,
            name="AutoRecall")
        with self._lock:
            self._thread = thread
            self._started_at = time.time()
        thread.start()
        return True

    def _run(self, query: str, anchors: List[str]) -> None:
        try:
            from core.brain.memory_search import get_memory_searcher
            response = get_memory_searcher().search(query, limit=self.max_hits + 2)
            line = self._format(response, anchors)
        except Exception as exc:
            print(f"[AutoRecall] search failed: {exc}")
            line = ""
        with self._lock:
            self._result = line

    def _format(self, response, anchors: List[str]) -> str:
        """One short line of grounded context, or "".

        Deliberately framed as retrieved notes rather than as certainty: the
        excerpt may be months old and may have been superseded, and the model
        must be free to treat it as a reminder instead of a fact to assert.
        """
        if response.approximate or not response.hits:
            return ""
        pieces = []
        budget = self.max_chars - 90
        for hit in response.hits[:self.max_hits]:
            record = hit.record
            excerpt = response._best_excerpt(record.text, limit=max(60, budget))
            if not excerpt:
                continue
            when = f" ({record.date})" if record.date else ""
            title = str(record.title or "").strip()
            piece = f"{title}{when}: {excerpt}" if title else f"{excerpt}{when}"
            pieces.append(piece)
            budget -= len(piece)
            if budget <= 60:
                break
        if not pieces:
            return ""
        body = " | ".join(pieces)
        line = (f"[Memory] You have talked about {', '.join(anchors[:2])} before. "
                f"Relevant notes: {body}")
        return line[:self.max_chars]

    # -- reading --

    def collect(self, timeout: Optional[float] = None) -> str:
        """Wait briefly for the search, then return the line to inject or "".

        A search that has not finished in time is abandoned rather than waited
        on. Missing one recall is a small loss; adding a visible stall to every
        voice turn would undo the whole point of running it in the background.
        """
        with self._lock:
            thread = self._thread
        if thread is None:
            return ""
        limit = self.timeout if timeout is None else float(timeout)
        thread.join(timeout=max(0.0, limit))
        with self._lock:
            if thread.is_alive():
                elapsed = time.time() - self._started_at
                print(f"[AutoRecall] abandoned after {elapsed:.2f}s "
                      "(search still running)")
                self._thread = None
                return ""
            line = self._result
            self._result = ""
            self._thread = None
            if not line or line in self._recent:
                return ""
            self._recent.append(line)
            if len(self._recent) > 8:
                del self._recent[:-8]
        return line

    def maybe_inject(self, message_history: List[Dict[str, Any]],
                     timeout: Optional[float] = None) -> str:
        """Append the recalled context, unless it is already in the prompt.

        The dedupe against recent history matters more than it looks: the
        conversation summary and the knowledge-base startup block already carry
        a lot of this material, and re-stating a fact that is sitting a few rows
        above costs warm prefix on every later turn for no benefit.
        """
        try:
            line = self.collect(timeout)
            if not line:
                return ""
            excerpt = line[-120:]
            for row in (message_history or [])[-12:]:
                content = row.get("content")
                if isinstance(content, str) and excerpt and excerpt in content:
                    return ""
            message_history.append({"role": "system", "content": line})
            print(f"[AutoRecall] injected {len(line)} chars")
            return line
        except Exception as exc:
            print(f"[AutoRecall] injection skipped: {exc}")
            return ""


_recall: Optional[AutoRecall] = None
_recall_lock = threading.RLock()


def get_auto_recall(config: Optional[dict] = None) -> AutoRecall:
    global _recall
    with _recall_lock:
        if _recall is None:
            if config is None:
                try:
                    from tools_and_config.config_loader import get_full_config
                    config = get_full_config().get("auto_recall", {})
                except Exception:
                    config = {}
            _recall = AutoRecall(config)
        return _recall


def reset_auto_recall() -> None:
    global _recall
    with _recall_lock:
        _recall = None
