import asyncio
import json
import threading
import time

import pytest

from kiki_gateway.config import GatewayConfig
from kiki_gateway.protocol import AudioFrame, BinaryKind
from kiki_gateway.session import DeviceSession


class FakeTTS:
    """Yields `chunks` slices of 20 ms PCM with no network delay."""

    def __init__(self, chunks: int):
        self.chunks = chunks

    async def stream(self, sentence, voice=""):
        for _ in range(self.chunks):
            yield b"\x00\x00" * 960  # 20 ms at 48 kHz mono s16


class FakeWebSocket:
    def __init__(self):
        self.binary: list[AudioFrame] = []
        self.text: list[str] = []

    async def send(self, message):
        if isinstance(message, str):
            self.text.append(message)
        else:
            self.binary.append(AudioFrame.decode(message))


def make_session(**overrides) -> DeviceSession:
    chunks = overrides.pop("chunks", 40)
    session = DeviceSession.__new__(DeviceSession)
    session.ws = FakeWebSocket()
    session.config = GatewayConfig(**overrides)
    # The lead is per-session now, not per-config: it is chosen from the link in
    # hello and can be raised at runtime when the board reports a stutter.
    session.playback_lead = session.config.playback_lead_seconds
    session.media_lead = session.config.media_lead_seconds
    session.session_id = "test"
    session.tts = FakeTTS(chunks)
    session.device = None
    session.display = None
    session.core = None
    session.tts_stream_id = 7
    session.tts_sequence = 0
    session.narrowband_out = False
    session.turn_abort = asyncio.Event()
    session.speech_display_tasks = set()
    return session


@pytest.mark.asyncio
async def test_lead_is_configurable_and_bounds_how_far_ahead_the_device_runs():
    session = make_session(playback_lead_seconds=0.2)
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("hello")
    await queue.put(None)

    started = time.perf_counter()
    await session._tts_worker(queue, started)
    elapsed = time.perf_counter() - started

    # 40 chunks x 20 ms = 800 ms of audio. With a 200 ms lead the sender must
    # have paced itself to roughly 600 ms of real time rather than dumping it.
    assert 0.45 < elapsed < 0.9
    assert len(session.ws.binary) == 40


@pytest.mark.asyncio
async def test_a_larger_lead_buffers_more_audio_without_delaying_the_first_frame():
    """The whole point of raising the lead: jitter tolerance is free at the front."""
    slow = make_session(playback_lead_seconds=0.2)
    fast = make_session(playback_lead_seconds=0.6)

    for session in (slow, fast):
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put("hello")
        await queue.put(None)
        session._first_frame_at = None
        started = time.perf_counter()
        await session._tts_worker(queue, started)
        session._elapsed = time.perf_counter() - started

    # More lead means the sender finishes sooner (it waits less), i.e. the
    # device is holding more audio ahead of the speaker at any moment.
    assert fast._elapsed < slow._elapsed


@pytest.mark.asyncio
async def test_audio_end_names_the_stream_kind_so_a_stale_marker_can_be_dropped():
    session = make_session()
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(None)
    await session._tts_worker(queue, time.perf_counter())

    assert session.ws.text, "the worker must always close the stream"
    import json

    end = json.loads(session.ws.text[-1])
    assert end["type"] == "audio_end"
    assert end["kind"] == "tts"
    assert end["stream_id"] == 7


@pytest.mark.asyncio
async def test_frames_carry_the_stream_id_the_device_gates_on():
    session = make_session(chunks=3)
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("hi")
    await queue.put(None)
    await session._tts_worker(queue, time.perf_counter())

    assert [frame.stream_id for frame in session.ws.binary] == [7, 7, 7]
    assert all(frame.kind is BinaryKind.TTS_PCM_S16_48K_MONO for frame in session.ws.binary)


@pytest.mark.asyncio
async def test_worker_keeps_its_original_stream_id_if_session_advances():
    session = make_session(chunks=3)
    original_send = session.ws.send

    async def advance_after_first_frame(message):
        await original_send(message)
        if not isinstance(message, str) and len(session.ws.binary) == 1:
            session.tts_stream_id = 8

    session.ws.send = advance_after_first_frame
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("old response")
    await queue.put(None)
    await session._tts_worker(queue, time.perf_counter())

    assert [frame.stream_id for frame in session.ws.binary] == [7, 7, 7]
    assert [frame.sequence for frame in session.ws.binary] == [0, 1, 2]


@pytest.mark.asyncio
async def test_cancel_during_pacing_cannot_send_one_last_pcm_chunk():
    session = make_session(chunks=1, playback_lead_seconds=0.0)
    token = session.turn_abort
    session._schedule_speech_text = lambda *_args, **_kwargs: token.set()
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("cancel me")
    await queue.put(None)

    await session._tts_worker(queue, time.perf_counter())

    assert session.ws.binary == []


@pytest.mark.asyncio
async def test_cancel_closes_the_underlying_tts_stream_immediately():
    class ClosingTTS:
        def __init__(self):
            self.closed = False

        async def stream(self, sentence, voice=""):
            try:
                while True:
                    yield b"\x00\x00" * 960
            finally:
                self.closed = True

    session = make_session(chunks=0, playback_lead_seconds=0.0)
    tts = ClosingTTS()
    session.tts = tts
    token = session.turn_abort
    session._schedule_speech_text = lambda *_args, **_kwargs: token.set()
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("cancel me")
    await queue.put(None)

    await session._tts_worker(queue, time.perf_counter())

    assert tts.closed
    assert session.ws.binary == []


@pytest.mark.asyncio
async def test_audio_stop_carries_explicit_stream_watermarks():
    session = make_session(chunks=0)

    await session.send_event("audio_stop", reason="test")

    event = json.loads(session.ws.text[-1])
    assert event["tts_stream_id"] == 7
    assert event["media_stream_id"] == 0


@pytest.mark.asyncio
async def test_opening_listening_does_not_revive_cancelled_response():
    session = DeviceSession.__new__(DeviceSession)
    token = threading.Event()
    token.set()
    session.turn_abort = token
    session.awake = False
    session.followup_deadline = 0.0
    session.device = None
    session.endpointer = type("Endpoint", (), {"reset": lambda self, active: None})()
    session.events = []

    async def send_event(kind, **fields):
        session.events.append((kind, fields))

    async def set_state(state, **fields):
        session.events.append(("state", {"state": state, **fields}))

    session.send_event = send_event
    session.set_state = set_state

    await session.activate_query("push_to_talk")

    assert session.turn_abort is token
    assert token.is_set()


@pytest.mark.asyncio
async def test_the_caption_is_delayed_to_when_its_audio_is_audible():
    """A sentence is generated well before it is spoken. The caption has to wait
    for the audio already queued ahead of it, plus the board's output latency."""
    session = make_session(chunks=10, display_sync_offset_ms=100)
    shown: list[tuple[float, str]] = []
    start = time.perf_counter()

    async def capture(event_type, **fields):
        if event_type == "speech":
            shown.append((time.perf_counter() - start, fields["text"]))

    session.send_event = capture

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("first sentence")
    await queue.put("second sentence")
    await queue.put(None)
    await session._tts_worker(queue, start)
    await asyncio.gather(*session.speech_display_tasks, return_exceptions=True)

    assert [text for _, text in shown] == ["first sentence", "second sentence"]
    # 10 chunks x 20 ms = 200 ms of audio per sentence, so the second sentence
    # cannot be captioned until roughly that much later than the first.
    assert shown[1][0] - shown[0][0] > 0.12
    # And the first waits out the configured output latency rather than
    # appearing the instant the sentence was generated.
    assert shown[0][0] >= 0.09


@pytest.mark.asyncio
async def test_a_cancelled_turn_drops_captions_that_have_not_shown_yet():
    session = make_session(chunks=4, display_sync_offset_ms=400)
    shown = []

    async def capture(event_type, **fields):
        if event_type == "speech":
            shown.append(fields["text"])

    session.send_event = capture
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put("never spoken")
    await queue.put(None)
    await session._tts_worker(queue, time.perf_counter())

    session._cancel_speech_text()
    await asyncio.sleep(0.05)
    assert shown == []


@pytest.mark.asyncio
async def test_expression_waits_for_speaking_and_its_sentence_audio():
    """The old gateway emitted the tag during generation while the board was
    still in `thinking`, so its priority guard discarded the expression."""
    session = make_session(chunks=1, display_sync_offset_ms=0)
    events: list[tuple[str, dict]] = []

    async def capture(event_type, **fields):
        events.append((event_type, fields))

    session.send_event = capture
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(("that is lovely", "love"))
    await queue.put(None)
    await session._tts_worker(queue, time.perf_counter())
    await asyncio.gather(*session.speech_display_tasks, return_exceptions=True)

    kinds = [kind for kind, _ in events]
    speaking = next(
        i for i, (kind, fields) in enumerate(events)
        if kind == "state" and fields.get("state") == "speaking"
    )
    assert speaking < kinds.index("expression") < kinds.index("speech")
    assert events[kinds.index("expression")][1] == {"name": "love"}


def test_only_pi_compatible_expression_names_are_selected():
    from kiki_gateway.expressions import last_expression_tag

    assert last_expression_tag("oh <oled:LOVE> wow <oled:shy>") == "shy"
    assert last_expression_tag("nope <oled:banana>") is None


@pytest.mark.asyncio
async def test_failed_background_tts_cannot_leave_microphone_gated():
    session = make_session(chunks=0)
    session.playing = False
    session.awake = False

    class FailingTTS:
        async def stream(self, sentence, voice=""):
            if False:
                yield b""
            raise RuntimeError("response ended prematurely")

    session.tts = FailingTTS()
    await session.speak_background("test phrase")

    assert session.playing is False
    import json

    events = [json.loads(raw) for raw in session.ws.text]
    assert any(
        event.get("type") == "audio_stop"
        and event.get("reason") == "background_tts_failed"
        for event in events
    )
    assert any(
        event.get("type") == "state" and event.get("state") == "idle"
        for event in events
    )
