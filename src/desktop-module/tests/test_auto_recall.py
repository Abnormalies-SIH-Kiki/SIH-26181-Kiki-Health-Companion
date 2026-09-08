"""Noticing that something was discussed before, without being told.

The measurement that shaped this module, run against the live 3437-record
corpus: relevance SCORE does not separate questions that are in memory from
questions that are not. "can you move forward" scored 43.0 while "did we talk
about NSUT" scored 19.7. Distinctiveness and term rarity were tested too and
separate no better -- "what is the capital of France" had the single rarest
matched term of anything tried.

So these tests pin the thing that does work: anchoring on names the knowledge
base actually stores, with a corpus-rarity gate on single-token matches. The
negative cases matter more than the positive ones -- a wrong injection derails
an ordinary conversation, while a miss just means answering without context.
"""

import json

import pytest

from core.brain import auto_recall as ar
from core.brain.auto_recall import AutoRecall, EntityIndex, get_auto_recall, reset_auto_recall


KB = {
    "people": {
        "Namita": {"relationship": "mother"},
        "Yash Gupta": {"note": "Larc AI"},
        "guest_20260722_1838": {"note": "unknown face"},
    },
    "facts": {
        "Moksha 2026": {"text": "NSUT cultural fest"},
        "Tata Elxsi": {"text": "stock"},
        "Delhi_Weather": {"text": "hot"},
    },
    "learnings": {"turn_detection_update": {"text": "endpointing work"}},
    "experiences": [{"event": "discrete_structures_result", "date": "2026-03-13"}],
}


@pytest.fixture
def index(tmp_path, monkeypatch):
    path = tmp_path / "knowledge_base.json"
    path.write_text(json.dumps(KB))
    # Rarity is the gate for single-token matches; stub it so the tests do not
    # depend on whatever happens to be in the real corpus today.
    rare = {"moksha": 6.4, "openclaw": 7.2, "detection": 5.8, "structures": 5.7}
    monkeypatch.setattr(ar, "_token_idf", lambda token: rare.get(token, 3.5))
    return EntityIndex(path)


# --- anchors: the things that SHOULD fire ---

@pytest.mark.parametrize("question,expected", [
    ("how is Namita doing", "Namita"),
    ("tell me about Yash Gupta", "Yash Gupta"),
    ("what is happening with Tata Elxsi", "Tata Elxsi"),
    ("what about Moksha", "Moksha 2026"),
    ("how is my discrete structures going", "discrete_structures_result"),
    ("my turn detection work", "turn_detection_update"),
])
def test_a_stored_topic_is_recognised(index, question, expected):
    assert expected in index.find_anchors(question)


# --- anchors: the things that MUST NOT fire ---

@pytest.mark.parametrize("question", [
    "what is the capital of France",
    "how do I boil an egg",
    "tell me a joke",
    "what time is it",
    "play some music",
    "turn the light on",
    "how tall is Everest",
    "can you move forward",
    "7 times 8",
    "define photosynthesis",
    "remind me to take my tablet",
    "how are you feeling today",
])
def test_an_ordinary_question_finds_no_anchor(index, question):
    assert index.find_anchors(question) == []


def test_a_common_word_in_a_stored_key_is_not_an_anchor(index):
    """The measured false positive this gate removes.

    "what's the weather" matched a stored `Delhi_Weather` on the single token
    "weather". Weather is now a live provider reading; answering it from a
    months-old memory is worse than not answering it from memory at all, and
    the rarity gate is what tells the two cases apart (weather 4.69 in the real
    corpus, against moksha 6.44).
    """
    assert index.find_anchors("whats the weather") == []


def test_a_face_placeholder_is_never_a_topic(index):
    """`guest_20260722_1838` is a recognition artefact, not something anyone
    asks about by name."""
    assert index.find_anchors("guest 20260722 1838") == []
    assert all("guest" not in a.lower() for a in index.find_anchors("who is the guest"))


def test_one_common_token_alone_is_not_enough(index):
    """`turn_detection_update` must not fire on "turn the light on"."""
    assert index.find_anchors("turn the light on") == []


def test_two_matching_tokens_are_enough_without_rarity(index):
    assert "turn_detection_update" in index.find_anchors("the turn detection thing")


def test_anchors_are_capped(index):
    assert len(index.find_anchors("Namita Yash Gupta Moksha Tata Elxsi", limit=2)) <= 2


def test_an_empty_question_finds_nothing(index):
    assert index.find_anchors("") == []
    assert index.find_anchors("   ") == []


def test_the_index_notices_a_changed_knowledge_base(index, tmp_path):
    assert index.find_anchors("tell me about Rohit") == []
    path = tmp_path / "knowledge_base.json"
    updated = json.loads(path.read_text())
    updated["people"]["Rohit Sharma"] = {"note": "new"}
    path.write_text(json.dumps(updated))
    assert "Rohit Sharma" in index.find_anchors("tell me about Rohit Sharma")


def test_a_saved_research_topic_is_an_automatic_recall_anchor(
        tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge.json"
    journal = tmp_path / "journal.json"
    knowledge.write_text(json.dumps({}))
    journal.write_text(json.dumps({
        "entries": [{
            "topic": "Splash Canvas",
            "summary": "A Google Arts and Culture AI painting experiment.",
        }]
    }))
    monkeypatch.setattr(ar, "_token_idf", lambda _token: 7.0)
    research_index = EntityIndex(knowledge, journal)

    assert "Splash Canvas" in research_index.find_anchors(
        "is Splash Canvas actually interesting?")


def test_a_missing_knowledge_base_is_not_an_error(tmp_path):
    missing = EntityIndex(tmp_path / "nope.json")
    assert missing.find_anchors("anything") == []
    assert missing.size() == 0


# --- the search only runs when there is a reason ---

class Rec:
    """A stand-in MemoryRecord: the searcher is mocked, only these fields matter."""

    def __init__(self, title, text, date=None):
        self.title, self.text, self.date = title, text, date


def _recall(index, monkeypatch, hits=None, approximate=False):
    recall = AutoRecall({"search_timeout_seconds": 3.0})
    recall.index = index
    calls = []

    class Hit:
        def __init__(self, record):
            self.record, self.score = record, 40.0

    class Resp:
        def __init__(self):
            self.hits = tuple(Hit(r) for r in (hits or []))
            self.approximate = approximate
            self.query = "q"

        def _best_excerpt(self, text, limit=245):
            return text[:limit]

    class Searcher:
        def search(self, query, limit=6):
            calls.append(query)
            return Resp()

    monkeypatch.setattr(
        "core.brain.memory_search.get_memory_searcher", lambda: Searcher())
    return recall, calls


def test_no_anchor_means_no_search_at_all(index, monkeypatch):
    """The latency contract.

    Searching costs ~250ms on the real corpus. Anchoring is a dictionary scan,
    so an ordinary turn pays microseconds -- which is the only reason this can
    run on the speaking path at all.
    """
    recall, calls = _recall(index, monkeypatch)
    assert recall.start("play some music") is False
    assert calls == []
    assert recall.collect() == ""


def test_an_anchor_starts_a_search_and_injects(index, monkeypatch):
    recall, calls = _recall(
        index, monkeypatch,
        hits=[Rec("Namita — traits", "She called on Sunday about the trip.", "2026-08-01")])
    history = []
    assert recall.start("how is Namita doing") is True
    line = recall.maybe_inject(history)
    assert calls == ["how is Namita doing"]
    assert len(history) == 1
    assert "Namita" in line
    assert "called on Sunday" in line


def test_a_weak_result_is_not_injected(index, monkeypatch):
    """An anchor says the topic exists; it does not promise the retriever found
    anything worth saying."""
    recall, _ = _recall(index, monkeypatch, hits=[], approximate=True)
    history = []
    recall.start("how is Namita doing")
    assert recall.maybe_inject(history) == ""
    assert history == []


def test_the_injection_is_capped(index, monkeypatch):
    recall, _ = _recall(
        index, monkeypatch, hits=[Rec("Namita", "x" * 5000, "2026-08-01")])
    recall.max_chars = 340
    history = []
    recall.start("how is Namita doing")
    line = recall.maybe_inject(history)
    assert 0 < len(line) <= 340


def test_the_same_memory_is_not_injected_twice(index, monkeypatch):
    recall, _ = _recall(
        index, monkeypatch, hits=[Rec("Namita", "She called on Sunday.", "2026-08-01")])
    history = []
    recall.start("how is Namita doing")
    assert recall.maybe_inject(history)
    recall.start("how is Namita doing")
    assert recall.maybe_inject(history) == ""
    assert len(history) == 1


def test_a_memory_already_in_the_prompt_is_not_repeated(index, monkeypatch):
    """The conversation summary and the startup knowledge block already carry a
    lot of this; restating it costs warm prefix on every later turn."""
    recall, _ = _recall(
        index, monkeypatch, hits=[Rec("Namita", "She called on Sunday.", "2026-08-01")])
    recall.start("how is Namita doing")
    line = recall.collect()
    recall._recent.clear()

    history = [{"role": "system", "content": "earlier context ... " + line}]
    recall.start("how is Namita doing")
    assert recall.maybe_inject(history) == ""
    assert len(history) == 1


def test_a_slow_search_is_abandoned_not_awaited(index, monkeypatch):
    """A stalled search must never become a stalled voice turn."""
    import threading

    release = threading.Event()

    class Slow:
        def search(self, query, limit=6):
            release.wait(5)
            raise RuntimeError("too late")

    monkeypatch.setattr(
        "core.brain.memory_search.get_memory_searcher", lambda: Slow())
    recall = AutoRecall({"search_timeout_seconds": 0.05})
    recall.index = index
    history = []
    recall.start("how is Namita doing")
    assert recall.maybe_inject(history) == ""
    assert history == []
    release.set()


def test_a_failing_search_never_raises(index, monkeypatch):
    class Boom:
        def search(self, query, limit=6):
            raise RuntimeError("index corrupt")

    monkeypatch.setattr(
        "core.brain.memory_search.get_memory_searcher", lambda: Boom())
    recall = AutoRecall({"search_timeout_seconds": 2.0})
    recall.index = index
    history = []
    recall.start("how is Namita doing")
    assert recall.maybe_inject(history) == ""
    assert history == []


def test_it_can_be_switched_off(index, monkeypatch):
    recall, calls = _recall(index, monkeypatch)
    recall.enabled = False
    assert recall.start("how is Namita doing") is False
    assert calls == []


def test_a_new_question_discards_the_previous_result(index, monkeypatch):
    recall, _ = _recall(
        index, monkeypatch, hits=[Rec("Namita", "She called.", "2026-08-01")])
    recall.start("how is Namita doing")
    recall.start("play some music")          # no anchor: must clear, not carry over
    assert recall.collect() == ""


def test_the_singleton_can_be_reset():
    reset_auto_recall()
    first = get_auto_recall({"enabled": False})
    assert get_auto_recall() is first
    reset_auto_recall()
    assert get_auto_recall({"enabled": False}) is not first
    reset_auto_recall()


def test_the_shipped_config_is_wired():
    from tools_and_config.config_loader import get_full_config

    cfg = get_full_config().get("auto_recall", {})
    assert cfg.get("enabled") is True
    assert cfg.get("min_anchor_idf", 0) >= 5.0
    assert cfg.get("max_injection_chars", 0) <= 500


# --- rarity is not always knowable ---

def test_rarity_falls_back_to_length_while_the_index_is_cold(index, monkeypatch):
    """The bug this guards, found during calibration.

    On an unbuilt search index `_idf` returns log(4) = 1.386 for EVERY term,
    because corpus size and document frequency are both zero. Read as a rarity
    score that silently rejected every single-token anchor for the half second
    after boot -- "what about Moksha" recalled nothing, with no error anywhere.
    Unknown rarity must degrade to the length heuristic, not to a false low.
    """
    monkeypatch.setattr(ar, "_token_idf", lambda token: None)
    assert "Moksha 2026" in index.find_anchors("what about Moksha 2026")
    assert "discrete_structures_result" in index.find_anchors("my discrete structures")
    # ...and the fallback still refuses a short common token.
    assert index.find_anchors("turn the light on") == []


def test_a_known_low_rarity_token_is_still_rejected(index, monkeypatch):
    monkeypatch.setattr(ar, "_token_idf", lambda token: 2.0)
    assert index.find_anchors("whats the weather") == []


def test_token_rarity_reports_unknown_rather_than_a_false_low(monkeypatch):
    class Cold:
        _document_frequency = {}

    monkeypatch.setattr(
        "core.brain.memory_search.get_memory_searcher", lambda: Cold())
    assert ar._token_idf("moksha") is None
