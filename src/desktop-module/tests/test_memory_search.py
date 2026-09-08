import json
from pathlib import Path

from core.brain.memory_search import MemorySearcher


def _build_searcher(tmp_path: Path) -> MemorySearcher:
    knowledge = {
        "people": {
            "Vaibhav": {
                "last_seen": "2026-07-20T10:00:00",
                "routine": [
                    "Avoided Discrete Math study by doing chores at midnight",
                    "Listens to music after class",
                ],
                "notes_list": ["Enjoys playful late-night banter with Kiki"],
            }
        },
        "environments": {},
        "learnings": {
            "math_humor": [
                "Two random variables walked into a bar and tried to be discrete."
            ]
        },
        "experiences": [
            {
                "date": "2026-02-06T23:33:00",
                "event": "Discrete Structures Test",
                "outcome": "The class test was scheduled for February 12.",
            },
            {
                "date": "2026-03-13T14:05:00",
                "event": "discrete_structures_result",
                "outcome": "Vaibhav scored 16 out of 20 in Discrete Structures.",
            },
        ],
        "experiences_archive": [],
        "facts": {"favorite_snack": "Momos after a long coding session"},
        "personality": {"developed_traits": ["witty"], "interaction_preferences": {}},
    }
    knowledge_path = tmp_path / "knowledge_base.json"
    knowledge_path.write_text(json.dumps(knowledge), encoding="utf-8")

    journal = {
        "entries": [
            {
                "id": "j1",
                "timestamp": "2026-07-18T12:00:00",
                "focus": "free thought",
                "topic": "Orbital mango experiment",
                "summary": "Wondered whether fruit could survive a tiny satellite trip.",
                "details": "A deliberately strange but memorable research thread.",
            }
        ]
    }
    journal_path = tmp_path / "thinking_journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    conversations = tmp_path / "conversations"
    conversations.mkdir()
    (conversations / "2026-07-20_16-23-40.txt").write_text(
        "Conversation Summary\nDate: 2026-07-20\nTime: 16:23:40\n"
        "==================================================\n\n"
        "Vaibhav wanted our funniest highlight reel. We laughed about his Amitabh "
        "Bachchan impression, the Tiki speech-to-text incident, and our playful banter.\n",
        encoding="utf-8",
    )
    return MemorySearcher(
        knowledge_path=knowledge_path,
        conversations_path=conversations,
        journal_path=journal_path,
    )


def test_discrete_structures_returns_the_result_not_a_whole_person_blob(tmp_path):
    response = _build_searcher(tmp_path).search("discrete structures")
    rendered = response.format()

    assert not response.approximate
    assert "16 out of 20" in rendered
    assert "February 12" in rendered
    assert "{'routine'" not in rendered


def test_emotional_vague_cue_recalls_a_funny_conversation(tmp_path):
    response = _build_searcher(tmp_path).search("find some funny memories")
    rendered = response.format()

    assert not response.approximate
    assert "Past conversation" in rendered
    assert "Bachchan" in rendered or "Tiki" in rendered


def test_broad_math_cue_uses_conceptual_association(tmp_path):
    response = _build_searcher(tmp_path).search("that maths thing we talked about")
    rendered = response.format()

    assert not response.approximate
    assert "Discrete" in rendered


def test_search_includes_thinking_journal(tmp_path):
    response = _build_searcher(tmp_path).search("orbital fruit research")

    assert any(hit.record.source == "journal" for hit in response.hits)
    assert "Orbital mango" in response.format()


def test_no_match_returns_a_diverse_labelled_assortment(tmp_path):
    response = _build_searcher(tmp_path).search("purple submarine xylophone")
    rendered = response.format()

    assert response.approximate
    assert response.hits
    assert len({hit.record.source for hit in response.hits}) >= 2
    assert "diverse set" in rendered
    assert "Nothing in memory" not in rendered
    assert len(rendered) <= 1420


def test_index_refreshes_when_a_memory_file_changes(tmp_path):
    searcher = _build_searcher(tmp_path)
    assert searcher.search("volcanic penguin").approximate

    path = tmp_path / "knowledge_base.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["facts"]["volcanic_penguin"] = "A newly remembered absurd mascot"
    path.write_text(json.dumps(data), encoding="utf-8")

    refreshed = searcher.search("volcanic penguin")
    assert not refreshed.approximate
    assert "newly remembered" in refreshed.format()
