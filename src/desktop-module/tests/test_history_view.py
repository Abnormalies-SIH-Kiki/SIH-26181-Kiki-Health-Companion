"""Kiki's history, rendered for readers that are not the local box.

Every case here is one of the failure classes we traced from a single
question — "if I asked a while ago to play music, when will 'send the music
link' work?" The answer used to be: never. The URL is returned by play_music
into a `system` message, and both readers of the history (the action agent and
the background summariser) dropped that role.
"""

import pytest

from core.brain import history_view as hv
from main import tool_result_note


MUSIC_RESULT = ("Now playing Blinding Lights - "
                "https://www.youtube.com/watch?v=4NRXx6U8ABQ")


@pytest.fixture
def music_turn():
    """The four rows main.py really writes for one tool turn."""
    return [
        {"role": "system", "content": "PERSONA"},
        {"role": "user", "content": "play some music"},
        {"role": "assistant",
         "content": '<tool_call>{"name": "play_music", "arguments": '
                    '{"song": "something upbeat"}}</tool_call>'},
        {"role": "system", "content": tool_result_note(
            [{"name": "play_music"}], MUSIC_RESULT)},
        {"role": "assistant", "content": "Playing Blinding Lights for you."},
    ]


def test_the_tool_note_marker_still_matches_main():
    """If main.tool_result_note is reworded, the payload split silently turns
    into noise. Fail loudly here instead."""
    for calls in ([{"name": "search_web"}], [{"name": "complex_query"}]):
        note = tool_result_note(calls, "PAYLOAD")
        assert hv.is_tool_result(note), f"marker drifted for {calls}"
        assert hv.tool_result_payload(note) == "PAYLOAD"


def test_a_tool_result_survives_into_the_rendered_history(music_turn):
    """Failure class 1: the value only ever existed in a tool result."""
    text = hv.as_text(music_turn, 4000)
    assert "https://www.youtube.com/watch?v=4NRXx6U8ABQ" in text
    # ...and without the instruction wrapper wrapped around it.
    assert "do not think out loud" not in text


def test_a_tool_call_is_prose_not_xml(music_turn):
    """The raw tag read as speech: `Kiki: <tool_call>{"name":"play_music"...`."""
    text = hv.as_text(music_turn, 4000)
    assert '[Kiki used play_music(song="something upbeat")]' in text
    assert "<tool_call>" not in text


def test_the_persona_is_skipped_but_other_system_rows_are_not(music_turn):
    text = hv.as_text(music_turn, 4000)
    assert "PERSONA" not in text
    assert "[result]" in text


def test_compacted_memory_is_carried_not_dropped():
    """Failure class 2: everything older than the last compaction. The
    summariser writes it back under `system`, which used to be filtered out —
    so dropping that role dropped Kiki's entire long-term memory."""
    records = hv.render([
        {"role": "system", "content": "PERSONA"},
        {"role": "system", "content": "Your memories from past conversations:\n"
                                      "Vaibhav was stressed about the Delhi trip."},
        {"role": "user", "content": "remind me about that"},
    ])
    kinds = [r["kind"] for r in records]
    assert "memory" in kinds
    assert "Delhi trip" in hv.as_text([
        {"role": "system", "content": "PERSONA"},
        {"role": "system", "content": "Your memories from past conversations:\n"
                                      "Vaibhav was stressed about the Delhi trip."},
    ], 4000)


def test_camera_and_room_context_survives():
    """Failure class 4: "who's here? ... send them the photo"."""
    text = hv.as_text([
        {"role": "system", "content": "PERSONA"},
        {"role": "system", "content": "Nikhil and Bharat are in the room."},
    ], 4000)
    assert "Nikhil and Bharat" in text


def test_trimming_keeps_the_newest_records():
    """Failure class 2 again: a pronoun points at the END of the history."""
    rows = [{"role": "system", "content": "PERSONA"}]
    rows += [{"role": "user", "content": f"old message {i}"} for i in range(50)]
    rows.append({"role": "user", "content": "send it to namita"})
    text = hv.as_text(rows, 200)
    assert "send it to namita" in text
    assert "old message 0" not in text
    assert len(text) <= 200


def test_a_long_record_is_clipped_but_still_present():
    """Failure class 3: past 400 chars into a long turn, the link was gone."""
    text = hv.as_text([{"role": "system", "content": "PERSONA"},
                       {"role": "user", "content": "y" * 5000}],
                      max_chars=4000, per_record_chars=300)
    assert 0 < len(text) <= 320


# --- artifacts: the durable half ------------------------------------------

def test_artifacts_harvest_the_link_with_enough_context(music_turn):
    found = hv.harvest_artifacts(music_turn)
    assert any("youtube.com/watch?v=4NRXx6U8ABQ" in a for a in found)
    assert any("Blinding Lights" in a for a in found), \
        "a bare URL is not identifiable as 'the music link'"


def test_artifacts_survive_a_clipped_result(music_turn):
    """The whole point: the body gets truncated, the link does not."""
    long_note = tool_result_note([{"name": "search_web"}],
                                 "z" * 4000 + " https://example.com/article")
    rows = music_turn + [{"role": "system", "content": long_note}]
    assert "https://example.com/article" not in hv.as_text(rows, 600)
    assert any("example.com/article" in a for a in hv.harvest_artifacts(rows))


def test_artifacts_include_paths_and_jids():
    rows = [{"role": "system", "content": "PERSONA"},
            {"role": "system", "content": tool_result_note(
                [{"name": "record_voice_note"}],
                "Saved /tmp/kiki/note.wav for 919900000011@s.whatsapp.net")}]
    found = " ".join(hv.harvest_artifacts(rows))
    assert "/tmp/kiki/note.wav" in found
    assert "919900000011@s.whatsapp.net" in found


def test_artifacts_are_newest_first():
    def note(url):
        return {"role": "system",
                "content": tool_result_note([{"name": "search_web"}], f"see {url}")}
    found = hv.harvest_artifacts([{"role": "system", "content": "PERSONA"},
                                  note("https://a.example"),
                                  note("https://b.example")])
    assert found[0].startswith("https://b.example")
    assert found[1].startswith("https://a.example")


def test_a_relisted_link_is_deduplicated_to_its_newest_mention():
    """Mentioned again later = more salient, so it ranks by the LAST sighting."""
    def note(url):
        return {"role": "system",
                "content": tool_result_note([{"name": "search_web"}], f"see {url}")}
    found = hv.harvest_artifacts([{"role": "system", "content": "PERSONA"},
                                  note("https://a.example"),
                                  note("https://b.example"),
                                  note("https://a.example")])
    assert sum("a.example" in f for f in found) == 1
    assert found[0].startswith("https://a.example")


def test_render_never_raises_on_odd_shapes():
    assert hv.render(None) == []
    assert hv.render([{"role": "assistant", "content": "<tool_call>{bad</tool_call>"}])
    assert hv.render([{"role": "system", "content": [{"type": "text", "text": "hi"}]}],
                     skip_system_prompt=False)[0]["text"] == "hi"


# --- what the summariser is asked to remember ------------------------------
# Kiki's long-term memory is written from this text. Before the fix it kept
# user/assistant rows and the [TIME] anchors and skipped everything else, so
# nothing Kiki learned by CALLING a tool was ever remembered.

def test_summary_input_remembers_tool_results(music_turn):
    from main import build_summary_input
    text = build_summary_input(music_turn)
    assert "https://www.youtube.com/watch?v=4NRXx6U8ABQ" in text
    assert "[TOOL RESULT]" in text


def test_summary_input_carries_the_previous_memory_forward():
    """Otherwise each summary covers only what happened since the last one and
    memory resets at every compaction instead of accumulating."""
    from main import build_summary_input
    text = build_summary_input([
        {"role": "system", "content": "PERSONA"},
        {"role": "system", "content": "Your memories from past conversations:\n"
                                      "Vaibhav's mum visited in June."},
        {"role": "user", "content": "what did we talk about"},
    ])
    assert "[EARLIER MEMORY]" in text and "mum visited in June" in text


def test_summary_input_is_prose_not_xml(music_turn):
    from main import build_summary_input
    text = build_summary_input(music_turn)
    assert "<tool_call>" not in text
    assert '[KIKI USED] play_music(song="something upbeat")' in text


def test_summary_input_keeps_time_anchors_and_skips_the_persona():
    from main import build_summary_input
    text = build_summary_input([
        {"role": "system", "content": "PERSONA TEXT"},
        {"role": "system", "content": "Vaibhav, right now it's Monday evening."},
    ])
    assert text.startswith("[TIME] ") and "PERSONA TEXT" not in text
