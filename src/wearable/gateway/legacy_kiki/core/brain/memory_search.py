"""Human-like retrieval across Kiki's persistent memory stores.

The knowledge base is deliberately structured, while conversation summaries and
the thinking journal are prose.  ``MemorySearcher`` turns all three into small,
individually rankable records and applies a lightweight lexical/semantic ranker.
It has no model or network dependency, so ``recall_memory`` remains fast enough
for the speaking path.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
import threading
from typing import Any, Iterable, Optional


_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")

# Query scaffolding carries no memory meaning.  Keeping it would make a request
# such as "find some funny memories" rank every summary containing "memory".
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "could", "date", "did", "do", "does", "for", "from", "had", "has", "have",
    "he", "her", "here", "him", "his", "i", "in", "into", "is", "it", "its",
    "kiki", "me", "memory", "memories", "my", "of", "on", "or", "our", "please",
    "recall", "remember", "search", "show", "some", "something", "tell", "that",
    "the", "their", "them", "there", "these", "they", "thing", "things", "this",
    "till", "to", "up", "us", "vaibhav", "was", "we", "were", "what", "when",
    "where", "which", "who", "with", "would", "you", "your", "about", "find",
    "discussion", "discussions", "discussed", "conversation", "conversations",
    "any", "anything", "cannot", "couldnt", "cue", "exist", "kind", "possibly",
}

# Small concept clusters are intentional rather than an enormous brittle
# thesaurus.  They cover the ways people naturally cue autobiographical memory:
# an emotion/tone, a broad life area, or a near-synonym for the remembered topic.
_CONCEPT_GROUPS = (
    {"funny", "humor", "humorous", "joke", "jokes", "laugh", "laughter", "hilarious",
     "amusing", "banter", "tease", "teasing", "playful", "silly", "ridiculous",
     "comedy", "chaotic", "mischief"},
    {"sad", "upset", "unhappy", "hurt", "cry", "crying", "emotional", "somber"},
    {"happy", "joy", "joyful", "excited", "celebrate", "celebration", "proud"},
    {"study", "studying", "academic", "college", "course", "class", "exam", "test",
     "quiz", "semester", "notes"},
    {"math", "mathematics", "mathematical", "discrete", "structures", "probability",
     "theorem", "logic", "algebra"},
    {"coding", "code", "programming", "software", "engineering", "technical", "debug",
     "bug", "project"},
    {"sleep", "sleeping", "bed", "bedtime", "tired", "late", "midnight", "night"},
    {"music", "song", "songs", "playlist", "lofi", "instrumental", "band"},
    {"food", "eat", "eating", "drink", "momos", "snack", "dinner", "lunch"},
)

_SOURCE_LABELS = {
    "knowledge": "Knowledge base",
    "conversation": "Past conversation",
    "journal": "Thinking journal",
    "current_summary": "Current conversation summary",
}


def _stem(word: str) -> str:
    """Tiny conservative stemmer suited to short conversational queries."""
    word = word.lower().strip("'")
    if len(word) > 5 and word.endswith("iest"):
        return word[:-4] + "y"
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    for suffix in ("ingly", "edly", "ing", "ed"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[:-len(suffix)]
            break
    if len(word) > 4 and word.endswith("es"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


def _tokens(text: str, *, remove_stop_words: bool = False) -> list[str]:
    words = [_stem(w) for w in _WORD_RE.findall(text or "")]
    if remove_stop_words:
        return [w for w in words if len(w) > 1 and w not in _STOP_WORDS]
    return [w for w in words if len(w) > 1]


def _concept_map() -> dict[str, frozenset[str]]:
    result: dict[str, frozenset[str]] = {}
    for group in _CONCEPT_GROUPS:
        stems = frozenset(_stem(word) for word in group)
        for word in stems:
            result[word] = stems
    return result


_CONCEPTS = _concept_map()


def _clean(text: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False, default=str)
    return _SPACE_RE.sub(" ", str(text)).strip()


def _display_date(value: Optional[str]) -> str:
    if not value:
        return "unknown date"
    return value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", value) else value


def _parse_epoch(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class MemoryRecord:
    source: str
    category: str
    title: str
    text: str
    date: Optional[str] = None
    locator: str = ""

    @property
    def searchable_text(self) -> str:
        return f"{self.title} {self.text}"


@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    score: float


@dataclass(frozen=True)
class MemorySearchResponse:
    query: str
    hits: tuple[MemoryHit, ...]
    approximate: bool
    total_records: int

    def _best_excerpt(self, text: str, limit: int = 245) -> str:
        """Show the passage that caused a vague match, not a chunk's preamble."""
        body = _clean(text)
        if len(body) <= limit:
            return body
        query_terms = _tokens(self.query, remove_stop_words=True)
        cues: set[str] = set(query_terms)
        for term in query_terms:
            cues.update(_CONCEPTS.get(term, ()))
        positions = []
        lowered = body.lower()
        for cue in cues:
            match = re.search(rf"\b{re.escape(cue)}\w*\b", lowered)
            if match:
                positions.append(match.start())
        center = min(positions) if positions else 0
        start = max(0, center - limit // 3)
        if start:
            next_space = body.find(" ", start)
            start = next_space + 1 if next_space >= 0 else start
        excerpt = body[start:start + limit]
        if start + limit < len(body):
            last_space = excerpt.rfind(" ")
            if last_space > limit // 2:
                excerpt = excerpt[:last_space]
        return ("..." if start else "") + excerpt.rstrip() + ("..." if start + len(excerpt) < len(body) else "")

    def format(self, max_chars: int = 1420) -> str:
        if self.approximate:
            header = (
                f"No strong literal match for '{self.query}'. Here is a diverse set of "
                "possibly related memories from across Kiki's memory (approximate recall):"
            )
        else:
            header = (
                f"Best memory matches for '{self.query}' (knowledge base + past "
                "conversations + thinking journal):"
            )

        footer = (
            "Use these dated memories as evidence and synthesize the answer."
            if not self.approximate else
            "Synthesize cautiously: these are associative leads, not proof that the exact requested event occurred."
        )
        lines = [header]
        for hit in self.hits:
            record = hit.record
            source = _SOURCE_LABELS.get(record.source, record.source.title())
            body = self._best_excerpt(record.text)
            title = _clean(record.title)
            display_date = _display_date(record.date)
            if record.source == "journal" and record.category == "ambient_listening":
                entry = (
                    f"- This was an ambient observation captured on {display_date}: "
                    f"{title}"
                )
            elif record.source == "journal":
                entry = (
                    f"- This was background research you performed on {display_date}: "
                    f"{title}"
                )
            else:
                entry = f"- [{display_date}] {source}/{record.category}: {title}"
            if body and body.lower() != title.lower():
                entry += f" — {body}"
            if len("\n".join(lines + [entry, footer])) > max_chars:
                remaining = max_chars - len("\n".join(lines + [footer])) - 2
                if remaining >= 100:
                    lines.append(entry[:remaining - 3].rstrip() + "...")
                break
            lines.append(entry)

        lines.append(footer)
        output = "\n".join(lines)
        return output if len(output) <= max_chars else output[:max_chars - 3].rstrip() + "..."


class MemorySearcher:
    """Build and search a compact index over every persistent memory source."""

    def __init__(
        self,
        *,
        knowledge_path: Path,
        conversations_path: Path,
        journal_path: Path,
        current_summary_path: Optional[Path] = None,
    ) -> None:
        self.knowledge_path = Path(knowledge_path)
        self.conversations_path = Path(conversations_path)
        self.journal_path = Path(journal_path)
        self.current_summary_path = Path(current_summary_path) if current_summary_path else None
        self._lock = threading.RLock()
        self._signature: tuple = ()
        self._records: list[MemoryRecord] = []
        self._token_counts: list[Counter[str]] = []
        self._title_tokens: list[set[str]] = []
        self._document_frequency: Counter[str] = Counter()

    @staticmethod
    def _file_signature(path: Optional[Path]) -> tuple:
        if path is None:
            return ()
        try:
            stat = path.stat()
            return (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(path), 0, 0)

    def _source_signature(self) -> tuple:
        conversation_files = []
        try:
            conversation_files = sorted(
                p for p in self.conversations_path.glob("*.txt")
                if p.name != "cached_past_summary.txt"
            )
        except OSError:
            pass
        return (
            self._file_signature(self.knowledge_path),
            self._file_signature(self.journal_path),
            self._file_signature(self.current_summary_path),
            tuple(self._file_signature(path) for path in conversation_files),
        )

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _append(
        records: list[MemoryRecord], source: str, category: str, title: Any,
        text: Any, date: Optional[str] = None, locator: str = "",
    ) -> None:
        title_text, body = _clean(title), _clean(text)
        if not title_text and not body:
            return
        records.append(MemoryRecord(source, category, title_text, body, date, locator))

    def _knowledge_records(self) -> list[MemoryRecord]:
        data = self._load_json(self.knowledge_path)
        records: list[MemoryRecord] = []

        for name, info in data.get("people", {}).items():
            if not isinstance(info, dict):
                self._append(records, "knowledge", "person", name, info)
                continue
            default_date = info.get("last_seen")
            for field, value in info.items():
                if field in {"first_seen", "last_seen", "current_ongoing_updated"} or not value:
                    continue
                item_date = info.get("current_ongoing_updated") if field == "current_ongoing" else default_date
                values = value if isinstance(value, list) else [value]
                for item in values:
                    self._append(
                        records, "knowledge", "person", f"{name} — {field.replace('_', ' ')}",
                        item, item_date, f"people.{name}.{field}",
                    )

        for name, info in data.get("environments", {}).items():
            if isinstance(info, dict):
                for field, value in info.items():
                    values = value if isinstance(value, list) else [value]
                    for item in values:
                        self._append(records, "knowledge", "environment", f"{name} — {field}", item)
            else:
                self._append(records, "knowledge", "environment", name, info)

        for category, items in data.get("learnings", {}).items():
            values = items if isinstance(items, list) else [items]
            for item in values:
                self._append(records, "knowledge", "learning", category, item)

        for bucket in ("experiences", "experiences_archive"):
            for experience in data.get(bucket, []):
                if not isinstance(experience, dict):
                    continue
                details = ". ".join(
                    part for part in (_clean(experience.get("outcome")), _clean(experience.get("details")))
                    if part
                )
                category = "archived experience" if bucket.endswith("archive") else "experience"
                self._append(
                    records, "knowledge", category, experience.get("event", "Experience"),
                    details, experience.get("date"), bucket,
                )

        for key, value in data.get("facts", {}).items():
            self._append(records, "knowledge", "fact", key, value)

        personality = data.get("personality", {})
        if isinstance(personality, dict):
            for field, value in personality.items():
                if isinstance(value, dict):
                    for key, item in value.items():
                        self._append(records, "knowledge", "personality", f"{field} — {key}", item)
                else:
                    values = value if isinstance(value, list) else [value]
                    for item in values:
                        self._append(records, "knowledge", "personality", field, item)
        return records

    def _journal_records(self) -> list[MemoryRecord]:
        data = self._load_json(self.journal_path)
        records: list[MemoryRecord] = []
        for entry in data.get("entries", []):
            if not isinstance(entry, dict):
                continue
            body = ". ".join(
                part for part in (_clean(entry.get("summary")), _clean(entry.get("details"))) if part
            )
            self._append(
                records, "journal", entry.get("focus", "entry"), entry.get("topic", "Thought"),
                body, entry.get("timestamp"), str(entry.get("id", "")),
            )
        return records

    @staticmethod
    def _conversation_date(path: Path, text: str) -> Optional[str]:
        match = re.search(r"(?m)^Date:\s*(\d{4}-\d{2}-\d{2})", text)
        if match:
            return match.group(1)
        match = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
        return match.group(1) if match else None

    @staticmethod
    def _strip_conversation_header(text: str) -> str:
        marker = re.search(r"(?m)^={10,}\s*$", text)
        return text[marker.end():].strip() if marker else text.strip()

    @staticmethod
    def _split_long_chunk(text: str, limit: int = 1200) -> Iterable[str]:
        text = text.strip()
        while len(text) > limit:
            cut = text.rfind(". ", 0, limit)
            if cut < limit // 2:
                cut = text.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            yield text[:cut + 1].strip()
            text = text[cut + 1:].strip()
        if text:
            yield text

    def _conversation_chunks(self, body: str) -> list[str]:
        # Raw shutdown saves contain role-labelled turns. Pair adjacent turns so
        # a user's cue and Kiki's response can be recalled together.
        role_matches = list(re.finditer(r"(?m)^(?:USER|ASSISTANT):\s*", body))
        if role_matches:
            turns = []
            for index, match in enumerate(role_matches):
                end = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(body)
                turns.append(body[match.start():end].strip())
            return ["\n".join(turns[index:index + 2]) for index in range(0, len(turns), 2)]

        chunks: list[str] = []
        for paragraph in re.split(r"\n\s*\n", body):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            # Markdown bullet lists hold distinct memories; split their items.
            bullets = [item.strip(" *\t") for item in re.split(r"(?m)^\s*[*-]\s+", paragraph) if item.strip()]
            pieces = bullets if len(bullets) > 1 else [paragraph]
            for piece in pieces:
                chunks.extend(self._split_long_chunk(piece))
        return chunks

    def _conversation_records(self, path: Path, source: str = "conversation") -> list[MemoryRecord]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        date = self._conversation_date(path, raw)
        body = self._strip_conversation_header(raw)
        records: list[MemoryRecord] = []
        for index, chunk in enumerate(self._conversation_chunks(body), 1):
            self._append(
                records, source, "session", f"Session {date or path.stem}", chunk,
                date, f"{path.name}#{index}",
            )
        return records

    def _rebuild_if_needed(self) -> None:
        signature = self._source_signature()
        with self._lock:
            if signature == self._signature and self._records:
                return
            records = self._knowledge_records() + self._journal_records()
            try:
                conversation_files = sorted(
                    p for p in self.conversations_path.glob("*.txt")
                    if p.name != "cached_past_summary.txt"
                )
            except OSError:
                conversation_files = []
            for path in conversation_files:
                records.extend(self._conversation_records(path))
            if self.current_summary_path and self.current_summary_path.exists():
                records.extend(self._conversation_records(self.current_summary_path, "current_summary"))

            counts = [Counter(_tokens(record.searchable_text)) for record in records]
            title_tokens = [set(_tokens(record.title)) for record in records]
            document_frequency: Counter[str] = Counter()
            for count in counts:
                document_frequency.update(count.keys())

            self._records = records
            self._token_counts = counts
            self._title_tokens = title_tokens
            self._document_frequency = document_frequency
            self._signature = signature

    def _idf(self, term: str) -> float:
        total = max(1, len(self._records))
        frequency = self._document_frequency.get(term, 0)
        return math.log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))

    def _score(
        self, index: int, query_terms: list[str], normalized_query: str,
        fuzzy_terms: Optional[dict[str, str]] = None,
    ) -> float:
        record = self._records[index]
        counts = self._token_counts[index]
        title_terms = self._title_tokens[index]
        searchable = " ".join(_tokens(record.searchable_text))
        score = 0.0
        matched = 0

        if normalized_query and normalized_query in searchable:
            score += 9.0 + min(4.0, len(query_terms))

        for query_term in query_terms:
            if query_term in counts:
                contribution = 2.2 * self._idf(query_term) * (1.0 + min(counts[query_term], 3) * 0.12)
                score += contribution
                matched += 1
                if query_term in title_terms:
                    score += 1.8
                # Concrete emotional memories tend to contain several related
                # signals (laugh + teasing + playful), while meta statements
                # such as "tag this as funny" contain only the label itself.
                related = _CONCEPTS.get(query_term, frozenset()).intersection(counts)
                score += min(1.8, max(0, len(related) - 1) * 0.45)
                continue

            concept_terms = _CONCEPTS.get(query_term, frozenset())
            concept_matches = concept_terms.intersection(counts)
            if concept_matches:
                best = max(concept_matches, key=self._idf)
                score += 1.15 * self._idf(best)
                matched += 1
                if best in title_terms:
                    score += 0.7
                continue

            fuzzy_term = (fuzzy_terms or {}).get(query_term)
            if fuzzy_term and fuzzy_term in counts:
                score += SequenceMatcher(None, query_term, fuzzy_term).ratio() * 1.4
                matched += 1

        if query_terms:
            score += 3.0 * matched / len(query_terms)
            if matched == len(query_terms) and len(query_terms) > 1:
                score += 2.0

        source_bonus = {"knowledge": 0.35, "conversation": 0.45, "journal": 0.4,
                        "current_summary": 0.3}.get(record.source, 0.0)
        score += source_bonus
        epoch = _parse_epoch(record.date)
        if epoch:
            age_days = max(0.0, (datetime.now(timezone.utc).timestamp() - epoch) / 86400.0)
            score += 0.7 / (1.0 + age_days / 120.0)
        return score if matched else 0.0

    @staticmethod
    def _similar(left: MemoryRecord, right: MemoryRecord) -> bool:
        left_words, right_words = set(_tokens(left.searchable_text)), set(_tokens(right.searchable_text))
        if not left_words or not right_words:
            return False
        overlap = len(left_words & right_words) / min(len(left_words), len(right_words))
        return overlap >= 0.66

    def _diverse_hits(self, candidates: list[MemoryHit], limit: int) -> tuple[MemoryHit, ...]:
        selected: list[MemoryHit] = []
        source_counts: Counter[str] = Counter()
        remaining = candidates[:40]
        while remaining and len(selected) < limit:
            choices = []
            for hit in remaining:
                if any(self._similar(hit.record, chosen.record) for chosen in selected):
                    continue
                diversity_penalty = max(0, source_counts[hit.record.source] - 1) * 0.8
                choices.append((hit.score - diversity_penalty, hit))
            if not choices:
                break
            _, best = max(choices, key=lambda item: item[0])
            selected.append(best)
            source_counts[best.record.source] += 1
            remaining.remove(best)
        return tuple(selected)

    def _fallback_hits(self, query: str, limit: int) -> tuple[MemoryHit, ...]:
        emotional = {"funny", "laugh", "playful", "happy", "sad", "proud", "excited", "love"}
        candidates: list[MemoryHit] = []
        query_offset = int(hashlib.sha1(query.encode("utf-8")).hexdigest()[:6], 16) % 17
        now = datetime.now(timezone.utc).timestamp()
        for index, record in enumerate(self._records):
            words = set(self._token_counts[index])
            richness = min(len(record.text) / 350.0, 1.4)
            personal = 0.8 if words.intersection(emotional) else 0.0
            category = 1.0 if "experience" in record.category else 0.0
            source = {"conversation": 0.8, "journal": 0.65, "knowledge": 0.45,
                      "current_summary": 0.5}.get(record.source, 0.0)
            epoch = _parse_epoch(record.date)
            recency = 0.0
            if epoch:
                age_days = max(0.0, (now - epoch) / 86400.0)
                recency = 1.5 / (1.0 + age_days / 180.0)
            # A tiny deterministic rotation avoids returning the exact same
            # handful for every totally unrelated cue while remaining testable.
            rotation = ((index + query_offset) % 17) / 100.0
            candidates.append(MemoryHit(record, richness + personal + category + source + recency + rotation))
        candidates.sort(key=lambda hit: hit.score, reverse=True)

        # A no-match recall should feel associative, not like six adjacent
        # paragraphs from the newest session. Seed one result per memory store,
        # then fill remaining slots while capping any one store at two.
        selected: list[MemoryHit] = []
        for source in ("conversation", "knowledge", "journal", "current_summary"):
            candidate = next(
                (hit for hit in candidates
                 if hit.record.source == source
                 and not any(self._similar(hit.record, old.record) for old in selected)),
                None,
            )
            if candidate:
                selected.append(candidate)
            if len(selected) >= limit:
                return tuple(selected)

        source_counts = Counter(hit.record.source for hit in selected)
        for hit in candidates:
            if len(selected) >= limit:
                break
            if source_counts[hit.record.source] >= 2:
                continue
            if any(self._similar(hit.record, old.record) for old in selected):
                continue
            selected.append(hit)
            source_counts[hit.record.source] += 1
        return tuple(selected)

    def search(self, query: str, limit: int = 6) -> MemorySearchResponse:
        self._rebuild_if_needed()
        clean_query = _clean(query)
        query_terms = list(dict.fromkeys(_tokens(clean_query, remove_stop_words=True)))
        normalized_query = " ".join(query_terms)

        with self._lock:
            # Resolve typos once per query against the index vocabulary instead
            # of running SequenceMatcher inside every document score.
            fuzzy_terms: dict[str, str] = {}
            vocabulary = self._document_frequency.keys()
            for query_term in query_terms:
                if query_term in self._document_frequency:
                    continue
                candidates = (
                    term for term in vocabulary
                    if term[:1] == query_term[:1] and abs(len(term) - len(query_term)) <= 2
                )
                best_term, best_ratio = "", 0.0
                for term in candidates:
                    ratio = SequenceMatcher(None, query_term, term).ratio()
                    if ratio > best_ratio:
                        best_term, best_ratio = term, ratio
                if best_ratio >= 0.82:
                    fuzzy_terms[query_term] = best_term

            scored = [
                (MemoryHit(record, self._score(index, query_terms, normalized_query, fuzzy_terms)),
                 sum(1 for term in query_terms if term in self._token_counts[index]), index)
                for index, record in enumerate(self._records)
            ]
            scored = [(hit, direct, index) for hit, direct, index in scored if hit.score > 0.0]
            # When several records contain every multi-word cue literally, they
            # are much safer than documents connected only through a broad
            # concept (e.g. "logic" for "discrete structures").
            if len(query_terms) > 1:
                full_direct = [(hit, direct, index) for hit, direct, index in scored
                               if direct == len(query_terms)]
                if len(full_direct) >= 2:
                    # Keep associative variants that contain the rarest anchor
                    # too: "discrete math" belongs with "discrete structures",
                    # but a generic document containing only "structure" does not.
                    # Natural-language topic phrases usually put the narrowing
                    # modifier first ("discrete structures", "funny college").
                    # Corpus rarity is unreliable here: an unrelated technical
                    # note can make "structures" rarer than "discrete".
                    anchor = query_terms[0]
                    scored = [
                        (hit, direct, index) for hit, direct, index in scored
                        if anchor in self._token_counts[index]
                    ]
            candidates = [hit for hit, _, _ in scored]
            candidates.sort(key=lambda hit: hit.score, reverse=True)
            approximate = not candidates
            hits = self._fallback_hits(clean_query, limit) if approximate else self._diverse_hits(candidates, limit)
            return MemorySearchResponse(clean_query, hits, approximate, len(self._records))


_SEARCHER: Optional[MemorySearcher] = None
_SEARCHER_LOCK = threading.Lock()


def get_memory_searcher() -> MemorySearcher:
    """Return the process-wide searcher using paths from Kiki's config."""
    global _SEARCHER
    with _SEARCHER_LOCK:
        if _SEARCHER is None:
            from tools_and_config.config_loader import get_full_config
            from core.brain.summary_manager import (
                get_conversations_folder_path,
                get_summary_file_path,
            )

            config = get_full_config()
            root = Path(__file__).resolve().parents[2]
            knowledge = Path(config.get("knowledge_base", {}).get("file_path", root / "knowledge_base.json"))
            journal = Path(
                config.get("idle_mind", {}).get(
                    "journal_file", root / "thinking_journal.json"))
            if not journal.is_absolute():
                journal = root / journal
            _SEARCHER = MemorySearcher(
                knowledge_path=knowledge,
                conversations_path=get_conversations_folder_path(),
                journal_path=journal,
                current_summary_path=get_summary_file_path(),
            )
        return _SEARCHER


def search_memory(query: str, limit: int = 6, max_chars: int = 1420) -> str:
    """Convenience entry point used by the tool layer."""
    return get_memory_searcher().search(query, limit=limit).format(max_chars=max_chars)
