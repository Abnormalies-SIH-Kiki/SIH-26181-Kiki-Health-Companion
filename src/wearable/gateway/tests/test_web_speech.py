"""The WebUI box says what you typed, or says why it didn't.

Live on 2026-09-07 the box was a one-turn *instruction* to the model: the text
went to Gemini, the model chose the wording, and four attempts in a row logged
`proactive question stayed silent` about two seconds apart -- "Ask vaibhav if he
is tired or not", "Hi", and two rephrasings. Nothing was spoken and nothing said
why. The context was at 7,784 of 8,192 tokens with a 1,200-token reply reserve,
so the most likely answer is that there was no room left to reply in.

A typed line is the one case with nothing for a model to decide, so the model is
now out of the path entirely. These tests pin that: the words spoken are the
words typed, and every way of not speaking is loud about it.
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from kiki_gateway.session import DeviceSession


def fake_session() -> DeviceSession:
    session = DeviceSession.__new__(DeviceSession)

    # No streamer of any kind is attached. If the verbatim path ever reaches for
    # a model again, these tests fail with AttributeError rather than quietly
    # going back to the behaviour they exist to prevent.
    session.core = SimpleNamespace(history=[{"role": "system", "content": "You are Kiki."}])
    session.core.register_history = lambda history, **kwargs: session.registered.append(
        kwargs)

    session.registered = []
    session.device = None
    session.session_id = "test"
    session.playing = False
    session.awake = False
    session.do_not_disturb = False
    session.dance_active = False
    session.push_to_talk_active = False
    session.turn_lock = asyncio.Lock()
    session.turn_abort = threading.Event()
    session.tts_stream_id = 0
    session.tts_sequence = 0
    session.followup_deadline = 0.0
    session.web_followup_pending = False
    session.speech_playback_started_at = 0.0
    session.events = []
    session.spoken = []
    session.cancelled = []
    session.audio_available = True

    async def send_event(kind, **fields):
        session.events.append((kind, fields))

    async def set_state(state, **fields):
        session.events.append(("state", {"state": state, **fields}))

    async def tts_worker(queue, started):
        while True:
            item = await queue.get()
            if item is None:
                break
            session.spoken.append(item)
        return bool(session.spoken) and session.audio_available

    async def cancel_turn(reason):
        session.cancelled.append(reason)

    session.send_event = send_event
    session.set_state = set_state
    session._tts_worker = tts_worker
    session.cancel_turn = cancel_turn
    session._new_turn_abort = lambda: threading.Event()
    session._reset_barge_in = lambda: None
    session._forget_ambient = lambda: None
    return session


def said(session) -> str:
    """Everything that reached TTS, rejoined."""
    return " ".join(text for text, _expression in session.spoken)


# ------------------------------------------------------------- the words ----

@pytest.mark.asyncio
async def test_it_speaks_exactly_what_was_typed():
    session = fake_session()
    assert await session.speak_verbatim("Vaibhav, are you tired?") is True
    assert said(session) == "Vaibhav, are you tired?"


@pytest.mark.asyncio
async def test_a_paragraph_is_chunked_but_not_altered():
    session = fake_session()
    typed = "Good morning. Time for your walk! Shall we go?"
    assert await session.speak_verbatim(typed) is True
    # Chunking exists so the first words start before the rest is synthesised.
    assert len(session.spoken) == 3
    assert said(session) == typed, "splitting must not change a character"


@pytest.mark.asyncio
async def test_hindi_sentence_endings_split_too():
    session = fake_session()
    typed = "नमस्ते। दवा का समय हो गया।"
    assert await session.speak_verbatim(typed) is True
    assert len(session.spoken) == 2
    assert said(session) == typed


@pytest.mark.asyncio
async def test_text_with_no_sentence_ending_is_still_spoken_whole():
    session = fake_session()
    assert await session.speak_verbatim("come here") is True
    assert said(session) == "come here"


@pytest.mark.asyncio
async def test_the_webui_sees_the_same_words_it_will_hear():
    session = fake_session()
    await session.speak_verbatim("Ready when you are.")
    sentences = [f["text"] for kind, f in session.events if kind == "response_sentence"]
    assert sentences == ["Ready when you are."]


# -------------------------------------------------------------- the tags ----

@pytest.mark.asyncio
async def test_a_display_tag_moves_her_face_instead_of_being_read_aloud():
    session = fake_session()
    assert await session.speak_verbatim("<oled:happy> Well done!") is True
    assert said(session) == "Well done!"
    assert session.spoken[-1][1] == "happy"


@pytest.mark.asyncio
async def test_the_expression_lands_with_the_last_chunk():
    session = fake_session()
    await session.speak_verbatim("<oled:curious> One. Two. Three.")
    expressions = [expression for _text, expression in session.spoken]
    assert expressions == [None, None, "curious"], (
        "an expression applied to the first chunk would clear before she finishes")


@pytest.mark.asyncio
async def test_nothing_but_a_tag_is_not_speech():
    session = fake_session()
    assert await session.speak_verbatim("<oled:happy>") is False
    assert session.spoken == []


@pytest.mark.asyncio
async def test_empty_input_is_refused():
    session = fake_session()
    assert await session.speak_verbatim("   ") is False


# ------------------------------------------------------------- the state ----

@pytest.mark.asyncio
async def test_speaking_opens_the_reply_window_so_you_can_answer_her():
    session = fake_session()
    assert await session.speak_verbatim("Are you ready?") is True
    assert session.awake is True
    assert session.playing is True
    assert session.web_followup_pending is True
    assert session.followup_deadline > time.monotonic()


@pytest.mark.asyncio
async def test_she_remembers_having_said_it():
    """Without this the next turn can contradict words she just spoke."""
    session = fake_session()
    await session.speak_verbatim("Your medicine is at three.")
    assert session.core.history[-1] == {
        "role": "assistant", "content": "Your medicine is at three."}
    assert session.registered == [{"after_speaking": True}]


@pytest.mark.asyncio
async def test_the_state_says_speaking_not_thinking():
    """There is nothing to think about; showing "thinking" would be a lie."""
    session = fake_session()
    await session.speak_verbatim("Hello.")
    assert session.events[0][0] == "state"
    assert session.events[0][1]["state"] == "speaking"


@pytest.mark.asyncio
async def test_silence_is_reported_and_resets_the_turn():
    """The old failure mode, now impossible to hide: no audio means False."""
    session = fake_session()
    session.audio_available = False
    assert await session.speak_verbatim("Say this.") is False
    assert session.playing is False
    assert session.awake is False
    assert session.web_followup_pending is False
    assert ("state", {"state": "idle"}) in session.events


@pytest.mark.asyncio
async def test_a_history_failure_does_not_swallow_the_speech():
    """She already said it; failing to write it down must not report otherwise."""
    session = fake_session()

    def explode(history, **kwargs):
        raise RuntimeError("disk full")

    session.core.register_history = explode
    assert await session.speak_verbatim("Already spoken.") is True


# ------------------------------------------------------------ the routing ---

@pytest.mark.asyncio
async def test_the_box_routes_to_verbatim_speech():
    session = fake_session()
    await session._handle_web_query("Exactly these words.")
    assert said(session) == "Exactly these words."


@pytest.mark.asyncio
async def test_an_active_turn_is_cancelled_first_so_the_line_is_not_queued_behind_it():
    session = fake_session()
    session.playing = True
    await session._handle_web_query("Stop and say this.")
    assert session.cancelled == ["webui_instruction"]
    assert said(session) == "Stop and say this."


@pytest.mark.asyncio
async def test_do_not_disturb_refuses_rather_than_speaking():
    session = fake_session()
    session.do_not_disturb = True
    await session._handle_web_query("Should stay quiet.")
    assert session.spoken == []


@pytest.mark.asyncio
async def test_dancing_refuses_too():
    session = fake_session()
    session.dance_active = True
    await session._handle_web_query("Not mid-dance.")
    assert session.spoken == []
