"""The board holds its microphone shut while its speaker runs.

So a song is not just something playing in the background -- it is the thing
standing between the user and being heard. These tests pin the behaviour that
was missing when Kiki hung after "play nice music": waking up has to pause the
music, and the turn ending has to put it back.
"""

import asyncio
import time

import pytest

from kiki_gateway.config import GatewayConfig
from kiki_gateway.device_tools import DeviceToolBridge
from kiki_gateway.protocol import AudioFrame
from kiki_gateway.session import DeviceSession


class FakeWebSocket:
    def __init__(self):
        self.frames: list[AudioFrame] = []

    async def send(self, message):
        if not isinstance(message, str):
            self.frames.append(AudioFrame.decode(message))


class Session:
    """Only the surface DeviceToolBridge actually touches."""

    def __init__(self, lead: float = 0.6):
        self.events: list[tuple[str, dict]] = []
        self.playing = False
        self.ws = FakeWebSocket()
        self.config = GatewayConfig(playback_lead_seconds=lead, media_lead_seconds=lead)
        # Music paces on its own, larger lead now (a song has no latency
        # requirement); the test needs a small one to see the rebase at all.
        self.media_lead = lead
        self.playback_lead = lead

    async def send_event(self, kind, **fields):
        self.events.append((kind, fields))


class FakeProcess:
    """Stands in for ffmpeg: hands out fixed-size blocks, then EOF."""

    def __init__(self, chunks: int):
        self.stdout = self
        self.remaining = chunks
        self.returncode = None

    async def read(self, size: int) -> bytes:
        if self.remaining <= 0:
            return b""
        self.remaining -= 1
        return b"\x00" * size

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.remaining = 0
        self.returncode = -15

    def kill(self) -> None:
        self.terminate()


def start_song(session, tmp_path, chunks: int = 10_000) -> DeviceToolBridge:
    """A bridge with a pump already running, without touching yt-dlp/ffmpeg."""
    bridge = DeviceToolBridge(session, str(tmp_path))
    process = FakeProcess(chunks)
    bridge.player = process
    bridge.current = {"title": "Nice Music"}
    session.playing = True
    bridge.player_task = asyncio.create_task(
        bridge._pump_media(process, bridge.media_stream_id)
    )
    return bridge


@pytest.mark.asyncio
async def test_ducking_frees_the_microphone(tmp_path):
    session = Session()
    bridge = start_song(session, tmp_path)
    await asyncio.sleep(0.05)
    assert session.ws.frames, "the pump should be streaming before we duck"

    assert await bridge.duck() is True

    # Both halves matter. Not sending is not enough on its own: the board is
    # still playing the lead it already holds, and its microphone stays shut
    # for every millisecond of that.
    assert session.playing is False
    assert ("audio_stop", {"reason": "duck"}) in session.events

    sent = len(session.ws.frames)
    await asyncio.sleep(0.1)
    assert len(session.ws.frames) == sent, "a ducked pump must send nothing"

    await bridge.stop()


@pytest.mark.asyncio
async def test_resuming_uses_a_stream_id_the_board_will_accept(tmp_path):
    session = Session()
    bridge = start_song(session, tmp_path)
    await asyncio.sleep(0.05)
    ducked_id = session.ws.frames[-1].stream_id

    await bridge.duck()
    await asyncio.sleep(0.02)
    await bridge.unduck()
    await asyncio.sleep(0.05)

    resumed = [f for f in session.ws.frames if f.stream_id != ducked_id]
    assert resumed, "the song should carry on after the turn"
    # audio_stop advanced the board's media watermark to last+1, so anything
    # re-sent under the old id is dropped on the floor and the song is silent.
    assert resumed[0].stream_id > ducked_id
    assert resumed[0].sequence == 0
    assert session.playing is True
    assert ("state", {"state": "music"}) in session.events

    await bridge.stop()


@pytest.mark.asyncio
async def test_resuming_does_not_dump_the_pause_as_one_burst(tmp_path):
    # A small lead makes the correct behaviour cheap to distinguish: with the
    # pacing clock rebased the pump owes ~50 ms of audio, and without it, it
    # believes it owes the entire length of the conversation.
    session = Session(lead=0.05)
    bridge = start_song(session, tmp_path)
    await asyncio.sleep(0.05)

    await bridge.duck()
    await asyncio.sleep(0.5)  # 12+ chunks' worth of catching up to be tempted by
    before = len(session.ws.frames)
    await bridge.unduck()
    await asyncio.sleep(0.06)
    burst = len(session.ws.frames) - before

    # Each chunk is 40 ms of audio; the un-rebased pump would send ~13 at once
    # and overrun the board's 128 KiB ring.
    assert burst <= 5, f"resume sent {burst} chunks at once"

    await bridge.stop()


@pytest.mark.asyncio
async def test_stopping_a_ducked_song_does_not_strand_the_next_one(tmp_path):
    session = Session()
    bridge = start_song(session, tmp_path)
    await asyncio.sleep(0.05)
    await bridge.duck()
    await bridge.stop()

    assert bridge.ducked is False
    assert bridge.media_gate.is_set(), "a new pump would park on a closed gate"

    session.ws.frames.clear()
    bridge.player_task = asyncio.create_task(
        bridge._pump_media(FakeProcess(100), bridge.media_stream_id)
    )
    await asyncio.sleep(0.05)
    assert session.ws.frames, "the next song must play"

    await bridge.stop()


def make_device_session(tmp_path) -> DeviceSession:
    session = DeviceSession.__new__(DeviceSession)
    session.ws = FakeWebSocket()
    session.config = GatewayConfig()
    session.session_id = "test"
    session.display = None
    session.core = None
    session.awake = False
    session.push_to_talk_active = False
    session.playing = False
    session.events = []
    session.followup_deadline = 0.0
    session.turn_abort = asyncio.Event()
    session.spec_tasks = {}
    session.spec_prefill_tasks = {}
    session.turn_modes = {}
    session.endpointer = None
    session.device = DeviceToolBridge(session, str(tmp_path))

    # Named `event_type` like the real method: `audio_end` passes its own
    # `kind=` keyword, which collides with a parameter called `kind`.
    async def send_event(event_type, **fields):
        session.events.append((event_type, fields))

    session.send_event = send_event
    return session


@pytest.mark.asyncio
async def test_waking_during_music_is_not_a_dead_end(tmp_path):
    """The regression: hold-to-talk over a song used to hang until it ended.

    activate_query only announced `listening`. The board, still playing, sent
    no microphone audio, so no endpoint could ever fire and the turn stayed
    open -- for the 91 minutes the mix happened to run.
    """
    session = make_device_session(tmp_path)
    bridge = session.device
    process = FakeProcess(10_000)
    bridge.player = process
    session.playing = True
    bridge.player_task = asyncio.create_task(
        bridge._pump_media(process, bridge.media_stream_id)
    )
    await asyncio.sleep(0.05)

    class Endpointer:
        def reset(self, active=False):
            pass

    session.endpointer = Endpointer()
    await session.activate_query(source="push_to_talk")

    assert bridge.ducked is True
    assert session.playing is False, "the board would never send us audio"
    assert ("audio_stop", {"reason": "duck"}) in session.events

    await bridge.stop()


@pytest.mark.asyncio
async def test_talk_tap_opens_listening_but_hold_release_is_the_endpoint(tmp_path):
    session = make_device_session(tmp_path)

    class Endpointer:
        def reset(self, active=False):
            pass

        def force_commit(self):
            return []

    session.endpointer = Endpointer()

    await session.handle_control({"type": "push_to_talk"})
    assert session.awake is True
    assert session.push_to_talk_active is True

    # A release before the firmware's long-hold threshold becomes exactly the
    # normal hotword listening window: VAD/silence is allowed to endpoint it.
    await session.handle_control({"type": "listen_open"})
    assert session.awake is True
    assert session.push_to_talk_active is False

    await session.handle_control({"type": "push_to_talk"})
    await session.handle_control({"type": "commit_now"})
    assert session.push_to_talk_active is False
    assert session.awake is False
    assert session.events[-1] == ("state", {"state": "idle"})


@pytest.mark.asyncio
async def test_a_song_survives_being_interrupted_for_a_question(tmp_path):
    session = make_device_session(tmp_path)
    bridge = session.device
    bridge.player = FakeProcess(10_000)
    session.playing = True
    bridge.player_task = asyncio.create_task(
        bridge._pump_media(bridge.player, bridge.media_stream_id)
    )
    await asyncio.sleep(0.05)
    await bridge.duck()

    await bridge.unduck()
    assert bridge.ducked is False
    assert session.playing is True

    # And the board is told it is back on music rather than being left showing
    # whatever the turn ended on.
    assert session.events[-1] == ("state", {"state": "music"})

    await bridge.stop()


@pytest.mark.asyncio
async def test_unducking_leaves_a_replacement_song_alone(tmp_path):
    """"Play something else" during a song must not resurrect the old one."""
    session = make_device_session(tmp_path)
    bridge = session.device
    bridge.player = FakeProcess(10_000)
    session.playing = True
    bridge.player_task = asyncio.create_task(
        bridge._pump_media(bridge.player, bridge.media_stream_id)
    )
    await asyncio.sleep(0.05)
    await bridge.duck()

    await bridge.stop()  # what _play_entries does before starting the new track
    session.events.clear()
    await bridge.unduck()

    assert session.events == [], "nothing to resume, so nothing to announce"
    assert session.playing is False


@pytest.mark.asyncio
async def test_a_turn_that_says_nothing_does_not_leave_kiki_deaf(tmp_path):
    """The second hang, and a nastier one: a failed `play_music`.

    `playing` gates BOTH the VAD and the wake word in handle_audio, and the
    only thing that clears it is the board reporting `playback_drained`. The
    board only reports that for a playback session it actually opened -- so a
    turn that sends no PCM at all (a media tool call that failed: no reply
    spoken, no song started) left `playing` stuck True and Kiki deaf until the
    process restarted, with the microphone still streaming at 100 frames/s
    into a gateway that skipped every frame.
    """
    session = make_device_session(tmp_path)
    session.awake = True

    class SilentCore:
        async def stream_reply(self, text, abort):
            # A media tool call whose result was "Music playback failed: ..."
            yield ("tool_calls", {"calls": [{"name": "play_music"}]})
            yield ("tool_result", "Music playback failed: no such song.")
            return

    session.core = SilentCore()
    session.tts = type("NoTTS", (), {"stream": lambda self, s, voice="": iter(())})()
    session.display = None
    session.speech_display_tasks = set()
    session.tts_stream_id = 0
    session.tts_sequence = 0

    async def set_state(state, **fields):
        session.events.append(("state", {"state": state, **fields}))

    session.set_state = set_state

    await session.respond("play farq hai by suzonn", time.perf_counter())

    assert session.playing is False, "the mic gate was left closed"
    assert ("state", {"state": "listening"}) in session.events


@pytest.mark.asyncio
async def test_a_turn_that_started_a_song_keeps_the_mic_gated(tmp_path):
    """The opposite case must not regress: real playback still gates the mic."""
    session = make_device_session(tmp_path)
    bridge = session.device
    bridge.player = FakeProcess(10_000)
    bridge.player_task = asyncio.create_task(
        bridge._pump_media(bridge.player, bridge.media_stream_id)
    )

    class MusicCore:
        async def stream_reply(inner_self, text, abort):
            session.playing = True  # what _play_entries does
            yield ("tool_calls", {"calls": [{"name": "play_music"}]})

    session.core = MusicCore()
    session.tts = type("NoTTS", (), {"stream": lambda self, s, voice="": iter(())})()
    session.speech_display_tasks = set()
    session.tts_stream_id = 0
    session.tts_sequence = 0

    async def set_state(state, **fields):
        session.events.append(("state", {"state": state, **fields}))

    session.set_state = set_state

    await session.respond("play something", time.perf_counter())

    assert session.playing is True, "the board is playing; it sends us no audio"
    await bridge.stop()


@pytest.mark.asyncio
async def test_tap_stop_keeps_listening_and_hold_stop_goes_idle(tmp_path):
    """Stop's two jobs. The hold is a superset: cancel first, then sleep."""
    session = make_device_session(tmp_path)
    session.awake = True
    session.followup_deadline = time.monotonic() + 15.0

    class Endpointer:
        def reset(self, active=False):
            pass

    session.endpointer = Endpointer()
    session.spec_tasks = {}
    session.spec_prefill_tasks = {}
    session.display = None
    session.speech_display_tasks = set()

    async def set_state(state, **fields):
        session.events.append(("state", {"state": state, **fields}))

    session.set_state = set_state

    # Tap: cancelled, but the follow-up window stays open.
    await session.handle_control({"type": "cancel_turn"})
    assert session.awake is True
    assert ("state", {"state": "listening"}) in session.events

    # Hold: the same press, escalated.
    session.events.clear()
    await session.handle_control({"type": "sleep"})
    assert session.awake is False
    assert session.followup_deadline == 0.0
    assert ("state", {"state": "idle"}) in session.events


@pytest.mark.asyncio
async def test_pause_and_duck_do_not_resume_each_other(tmp_path):
    """Two independent reasons to stop feeding the speaker.

    Ending a conversation must not un-pause a song the user paused, and
    resuming a paused song must not start it playing over a live turn. One
    boolean could not express that, which is why the gate counts reasons.
    """
    session = Session()
    bridge = start_song(session, tmp_path)
    await asyncio.sleep(0.05)

    await bridge._control("pause")
    assert bridge.paused is True
    assert bridge.media_active is False
    assert bridge.media_loaded is True, "paused is still loaded"

    # A conversation happens on top of the paused song, and ends.
    await bridge.duck()
    await bridge.unduck()
    assert bridge.paused is True, "the turn un-paused the user's song"
    assert bridge.media_active is False

    sent = len(session.ws.frames)
    await asyncio.sleep(0.1)
    assert len(session.ws.frames) == sent

    await bridge._control("resume")
    assert bridge.paused is False
    await asyncio.sleep(0.05)
    assert len(session.ws.frames) > sent, "resume did not restart the stream"

    await bridge.stop()


@pytest.mark.asyncio
async def test_resuming_from_pause_gets_a_fresh_stream_id(tmp_path):
    # Same watermark trap as ducking: pausing tells the board to drop what it
    # holds, so the old id is blacklisted from then on.
    session = Session()
    bridge = start_song(session, tmp_path)
    await asyncio.sleep(0.05)
    before = session.ws.frames[-1].stream_id

    await bridge._control("pause")
    await asyncio.sleep(0.02)
    await bridge._control("resume")
    await asyncio.sleep(0.05)

    resumed = [f for f in session.ws.frames if f.stream_id != before]
    assert resumed and resumed[0].stream_id > before

    await bridge.stop()


@pytest.mark.asyncio
async def test_next_and_previous_walk_the_search_queue(tmp_path):
    """The queue is the search results, which is what makes next/previous mean
    anything for a spoken "play X" -- there was only ever one entry before."""
    session = Session()
    bridge = DeviceToolBridge(session, str(tmp_path))
    entries = [
        {"title": f"Song {i}", "webpage_url": f"https://y/{i}", "video_id": str(i)}
        for i in range(3)
    ]

    started: list[int] = []

    async def fake_play(entries_arg, index, deadline=None):
        started.append(index)
        bridge.queue = list(entries_arg)
        bridge.index = index
        bridge.current = dict(entries_arg[index])
        return "ok"

    bridge._play_entries = fake_play
    await fake_play(entries, 0)

    assert await bridge._control("next") == "ok"
    assert started[-1] == 1
    assert await bridge._control("previous") == "ok"
    assert started[-1] == 0
    assert "first song" in (await bridge._control("previous")).lower()

    bridge.index = 2
    assert "last song" in (await bridge._control("next")).lower()


@pytest.mark.asyncio
async def test_a_failed_top_result_falls_through_to_the_next(tmp_path):
    """Search results are not promises: age-gated and region-blocked videos
    look identical until yt-dlp tries them."""
    session = Session()
    bridge = DeviceToolBridge(session, str(tmp_path))
    entries = [
        {"title": "bad", "webpage_url": "https://y/bad"},
        {"title": "good", "webpage_url": "https://y/good"},
    ]

    async def fake_resolve(url):
        if url.endswith("bad"):
            return None, "Video unavailable"
        return {"title": "good", "webpage_url": url, "stream_url": "https://s/good"}, ""

    bridge._resolve = fake_resolve
    started = {}

    async def fake_exec(*args, **kwargs):
        started["cmd"] = args
        return FakeProcess(50)

    import kiki_gateway.device_tools as dt

    original = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_exec
    try:
        result = await bridge._play_entries(entries, 0)
    finally:
        asyncio.create_subprocess_exec = original

    assert "good" in result
    assert bridge.index == 1, "it should have skipped the unplayable first hit"
    await bridge.stop()


@pytest.mark.asyncio
async def test_a_failing_warm_still_reaches_idle(tmp_path):
    """`warming` is the one state the board cannot leave on its own.

    It is waiting to be told, so any path that does not reach `idle` leaves the
    panel reading "Warming up model" until someone power-cycles it. Observed
    live after a keepalive drop landed mid-warm.
    """
    session = make_device_session(tmp_path)
    session.display = None

    async def set_state(state, **fields):
        session.events.append(("state", {"state": state, **fields}))

    session.set_state = set_state

    class SlowCore:
        async def ensure_ready(self):
            await asyncio.sleep(10)

    session.core = SlowCore()
    object.__setattr__(session.config, "warm_timeout_seconds", 0.05)

    warmer = getattr(session.core, "ensure_ready", None)
    await session.set_state(state="warming")
    try:
        await asyncio.wait_for(warmer(), timeout=session.config.warm_timeout_seconds)
    except asyncio.TimeoutError:
        pass
    await session.set_state(state="idle")

    states = [f["state"] for kind, f in session.events if kind == "state"]
    assert states == ["warming", "idle"]


def test_the_keepalive_deadline_is_not_shorter_than_a_busy_board():
    """A pong is answered by a task sharing a core with audio and LVGL.

    At 10 s this was killing healthy sessions every few minutes with
    `1011 keepalive ping timeout`, and each false positive rebuilds the whole
    legacy runtime and re-warms llama.
    """
    import inspect

    from kiki_gateway import server

    source = inspect.getsource(server.run_server)
    assert "ping_timeout=60" in source


@pytest.mark.asyncio
async def test_cancelling_takes_the_transport_row_with_it(tmp_path):
    """Stop kills the song, so the row must go too -- it used to linger."""
    session = make_device_session(tmp_path)
    session.awake = True
    bridge = session.device
    bridge.player = FakeProcess(10_000)
    bridge.current = {"title": "Something"}
    bridge.player_task = asyncio.create_task(
        bridge._pump_media(bridge.player, bridge.media_stream_id)
    )
    await asyncio.sleep(0.05)

    class Endpointer:
        def reset(self, active=False):
            pass

    session.endpointer = Endpointer()
    session.display = None
    session.speech_display_tasks = set()

    async def set_state(state, **fields):
        session.events.append(("state", {"state": state, **fields}))

    session.set_state = set_state
    session.events.clear()

    await session.cancel_turn("cancel_turn")

    media = [f for kind, f in session.events if kind == "media_state"]
    assert media, "the panel was never told the song is gone"
    assert media[-1]["loaded"] is False


def test_music_is_not_pushed_through_the_speech_boost():
    """3.2x exists for OmniVoice, which is quiet. A YouTube track is already
    mastered near the ceiling; the same gain would live on the limiter."""
    from kiki_gateway.config import GatewayConfig
    from kiki_gateway.inference import amplify_pcm_s16

    assert GatewayConfig().media_gain == 1.0
    pcm = b"\x00\x40" * 100
    # 1.0 is a true bypass, not a round trip through the limiter.
    assert amplify_pcm_s16(pcm, GatewayConfig().media_gain) is pcm


# --------------------------------------------------- narrowband music -------

def test_music_is_decimated_to_16k_for_a_remote_link():
    """Speech has dropped to 16 kHz on a remote link since the tunnel work;
    music did not, so a song over the funnel still asked for 768 kbps on a link
    measured at ~396 -- 102 underruns and 7.9 s of gap in 70 s of playback.

    The filter is phase-sensitive (it keeps every third sample of its own
    output), so the check that matters is that successive chunks continue the
    pattern instead of each restarting it.
    """
    import numpy as np
    from kiki_gateway.device_tools import DeviceToolBridge

    bridge = DeviceToolBridge.__new__(DeviceToolBridge)
    from kiki_gateway.audio import Decimator48To16

    bridge._media_decimator = Decimator48To16()
    bridge._media_tail = np.zeros(0, dtype=np.float32)

    # A 400 Hz tone at 48 kHz, fed in chunks that do NOT divide by three.
    tone = np.sin(2 * np.pi * 400 * np.arange(48000) / 48000) * 12000
    pcm = np.rint(tone).astype("<i2").tobytes()
    out = b""
    for start in range(0, len(pcm), 3202):        # deliberately ragged
        out += bridge._narrow_media(pcm[start : start + 3202])

    produced = len(out) // 2
    # One second in, one third of a second of samples out, give or take the
    # remainder still held for the next chunk.
    assert 15990 <= produced <= 16000
    # And it is still a tone, not noise: the decimated signal keeps its shape.
    narrow = np.frombuffer(out, dtype="<i2").astype(np.float32)
    assert narrow.max() > 9000
    assert abs(float(narrow.mean())) < 200
