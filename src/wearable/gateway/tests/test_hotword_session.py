"""What the gateway does with a hotword it found in a transcript.

The acoustic wake word could only ever be heard before the request, so the
session never had to ask *which* words were meant for Kiki. Text hotwording
does, and these are the four answers it has to get right:

1. an utterance carrying the name is a query, and the whole utterance is it;
2. an utterance without it is ambient, and must not reach the speaking model;
3. a name arriving at the END of a short utterance is addressing something
   already said, so recent idle speech is carried into the query;
4. a name and nothing else just opens the window, exactly as the old wake word
   did.
"""

import asyncio
import sys
import time
import types
from collections import deque

import numpy as np
import pytest

from kiki_gateway.config import GatewayConfig
from kiki_gateway.inference import LegacyKikiCore
from kiki_gateway.session import DeviceSession


class FakeWhisper:
    """Hands back scripted transcripts in order."""

    def __init__(self, *transcripts: str):
        self.queue = list(transcripts)

    async def transcribe(self, audio):
        return (self.queue.pop(0) if self.queue else ""), 0.05


class Harness:
    """A DeviceSession with only the surface the wake decision touches."""

    def __init__(self, *transcripts: str, hotwords=(), config=None):
        session = DeviceSession.__new__(DeviceSession)
        session.session_id = "test"
        session.config = config or GatewayConfig()
        session.whisper = FakeWhisper(*transcripts)
        session.turn_lock = asyncio.Lock()
        session.display = None
        session.device = None
        session.wakeword = None
        session.awake = False
        session.playing = False
        session.followup_deadline = 0.0
        session.recent_ambient = deque(maxlen=8)
        session._matcher = None
        session._matcher_words = ()
        session._early_wake_generation = None
        session.endpointer = types.SimpleNamespace(generation=1)

        self.events: list[tuple[str, dict]] = []
        self.ambient: list[str] = []
        self.answered: list[str] = []
        self.prefilled: list[str] = []

        async def send_event(kind, **fields):
            self.events.append((kind, fields))

        async def record_ambient(text):
            self.ambient.append(text)

        async def respond(text, endpoint_at):
            self.answered.append(text)

        async def prefill_partial(text):
            self.prefilled.append(text)

        session.send_event = send_event
        session.respond = respond
        session.core = types.SimpleNamespace(
            record_ambient=record_ambient,
            prefill_partial=prefill_partial,
            active_hotwords=lambda: tuple(hotwords),
        )
        self.session = session

    async def hear(self, query_mode: bool = False, generation: int = 1) -> None:
        """One committed utterance, transcribed and classified."""
        await self.session._finalize_turn(
            generation, np.zeros(512, dtype=np.float32), None, query_mode
        )

    def states(self) -> list[str]:
        return [f.get("state") for kind, f in self.events if kind == "state"]

    def transcript_modes(self) -> list[str]:
        return [f.get("mode") for kind, f in self.events if kind == "transcript_final"]


@pytest.mark.asyncio
async def test_the_hotword_anywhere_in_the_utterance_answers_it():
    harness = Harness("Kiki, can you hear me?")

    await harness.hear()

    assert harness.answered == ["Kiki, can you hear me?"]
    assert harness.session.awake is True
    assert harness.transcript_modes() == ["query"]


@pytest.mark.asyncio
async def test_a_misheard_name_still_wakes_her():
    # The whole point of matching text rather than audio: openWakeWord scored
    # this as silence.
    harness = Harness("hey tiki what is your name")

    await harness.hear()

    assert harness.answered == ["hey tiki what is your name"]


@pytest.mark.asyncio
async def test_idle_speech_without_the_name_never_reaches_the_model():
    harness = Harness("i think we should leave around seven")

    await harness.hear()

    assert harness.answered == []
    assert harness.ambient == ["i think we should leave around seven"]
    assert harness.transcript_modes() == ["ambient"]
    assert harness.session.awake is False


@pytest.mark.asyncio
async def test_a_trailing_hotword_carries_back_what_was_asked_before_it():
    # The request and the address were two utterances to the endpointer. The
    # second one alone is not a question, and answering it alone is answering
    # nothing.
    harness = Harness("what is the capital of france", "so what do you think kiki")

    await harness.hear()
    await harness.hear()

    assert harness.answered == [
        "what is the capital of france so what do you think kiki"
    ]


@pytest.mark.asyncio
async def test_a_leading_hotword_does_not_drag_old_chatter_into_the_question():
    harness = Harness("i told him it was fine", "kiki what is the weather today")

    await harness.hear()
    await harness.hear()

    assert harness.answered == ["kiki what is the weather today"]


@pytest.mark.asyncio
async def test_carried_speech_is_used_once_and_not_again():
    harness = Harness("the meeting is at four", "what do you think kiki", "kiki")

    await harness.hear()
    await harness.hear()
    await harness.hear()

    assert harness.answered == ["the meeting is at four what do you think kiki"]
    # The carried sentence has been asked. Leaving it buffered would prepend it
    # to the next question too.
    assert harness.states()[-1] == "listening"


@pytest.mark.asyncio
async def test_speech_older_than_the_window_is_not_carried_back():
    # A question from four minutes ago is not what "what do you think" means.
    harness = Harness("that was ages ago", "what do you think kiki")

    await harness.hear()
    harness.session.recent_ambient[0] = (
        time.monotonic() - harness.session.config.hotword_carry_back_seconds - 5,
        harness.session.recent_ambient[0][1],
    )
    await harness.hear()

    assert harness.answered == ["what do you think kiki"]


@pytest.mark.asyncio
async def test_whisper_narrating_silence_is_never_carried_into_a_question():
    harness = Harness("[BLANK_AUDIO]", "what do you think kiki")

    await harness.hear()
    await harness.hear()

    assert harness.answered == ["what do you think kiki"]


@pytest.mark.asyncio
async def test_her_name_alone_opens_the_window_and_says_nothing():
    harness = Harness("hey kiki")

    await harness.hear()

    assert harness.answered == []
    assert harness.session.awake is True
    assert harness.states()[-1] == "listening"
    assert harness.session.followup_deadline > time.monotonic()


@pytest.mark.asyncio
async def test_an_open_window_answers_without_the_name():
    harness = Harness("and what about tomorrow")

    await harness.hear(query_mode=True)

    assert harness.answered == ["and what about tomorrow"]
    assert harness.ambient == []


@pytest.mark.asyncio
async def test_a_mode_answers_to_its_own_hotword_and_not_to_kiki():
    harness = Harness("kiki are you there", "rohan are you there", hotwords=("rohan",))

    await harness.hear()
    assert harness.answered == []

    await harness.hear()
    assert harness.answered == ["rohan are you there"]


@pytest.mark.asyncio
async def test_the_speculative_transcript_wakes_her_before_the_commit_does():
    harness = Harness("kiki what time is it")
    task = asyncio.create_task(asyncio.sleep(0, ("kiki what time is it", 0.04)))

    await harness.session._prefill_speculative(1, task, query_mode=False)

    assert harness.session.awake is True
    assert harness.session._early_wake_generation == 1
    assert harness.prefilled == ["kiki what time is it"]


@pytest.mark.asyncio
async def test_an_early_wake_the_final_transcript_denies_is_taken_back():
    harness = Harness("i took a quick look at it")
    spec = asyncio.create_task(asyncio.sleep(0, ("i took a kiki look at it", 0.04)))

    await harness.session._prefill_speculative(1, spec, query_mode=False)
    assert harness.session.awake is True

    await harness.hear()

    assert harness.session.awake is False
    assert harness.answered == []
    assert harness.states()[-1] == "idle"


@pytest.mark.asyncio
async def test_idle_speech_never_prefills_the_speaking_model():
    harness = Harness("we should paint the wall")
    spec = asyncio.create_task(asyncio.sleep(0, ("we should paint the wall", 0.04)))

    await harness.session._prefill_speculative(1, spec, query_mode=False)

    assert harness.prefilled == []
    assert harness.session.awake is False


def fake_runtime_controls(monkeypatch, mode: str) -> None:
    package = types.ModuleType("core")
    module = types.ModuleType("core.runtime_controls")
    module.get_active_mode = lambda: mode
    package.runtime_controls = module
    monkeypatch.setitem(sys.modules, "core", package)
    monkeypatch.setitem(sys.modules, "core.runtime_controls", module)


def core_with_modes(modes: dict, **settings) -> LegacyKikiCore:
    core = LegacyKikiCore.__new__(LegacyKikiCore)
    core.full_config = {"assistant_modes": dict(settings, modes=modes)}
    return core


def test_a_mode_with_its_own_voice_answers_to_that_name(monkeypatch):
    # The convention that makes this work on an unedited config.json: a mode
    # that owns a voice is a character with its own name.
    fake_runtime_controls(monkeypatch, "rohan")
    core = core_with_modes({"rohan": {"voice": "rohan"}, "default": {"voice": ""}})

    assert core.active_hotwords() == ("rohan",)


def test_a_mode_that_is_still_kiki_falls_through_to_the_gateway_default(monkeypatch):
    fake_runtime_controls(monkeypatch, "default")
    core = core_with_modes({"default": {"voice": ""}})

    assert core.active_hotwords() == ()


def test_an_explicit_mode_hotword_list_wins(monkeypatch):
    fake_runtime_controls(monkeypatch, "jarvis")
    core = core_with_modes(
        {"jarvis": {"voice": "jarvis", "hotwords": ["jarvis", "sir jarvis"]}},
        hotwords=["kiki"],
    )

    assert core.active_hotwords() == ("jarvis", "sir jarvis")


def test_a_global_hotword_list_beats_the_voice_convention(monkeypatch):
    fake_runtime_controls(monkeypatch, "rohan")
    core = core_with_modes({"rohan": {"voice": "rohan"}}, hotwords="kiki, rohan")

    assert core.active_hotwords() == ("kiki", "rohan")


@pytest.mark.asyncio
async def test_idle_audio_still_reaches_the_endpointer():
    """The hotword lives in the TRANSCRIPT, so idle audio must be transcribed.

    Regression for the change that added `if not self.awake: continue` to
    `handle_audio`. There is no acoustic wake model running by default, so
    dropping idle audio means no utterance, no Whisper text, and nothing for
    the matcher to find: saying "kiki" did nothing and the physical talk
    control became the only way in. Every turn in the 2026-09-07 log arrived
    via push_to_talk for exactly this reason.
    """
    import inspect

    from kiki_gateway.session import DeviceSession

    source = inspect.getsource(DeviceSession.handle_audio)
    body = source[source.index("if self.playing:"):]
    gate = "if not self.awake:\n                continue"
    assert gate not in body, (
        "idle audio is being dropped before the endpointer; the text hotword "
        "cannot work without it")
    assert "self.pcm16 = np.concatenate" in body
