"""Every way a dance can end, and the one state it must never get stuck in.

A dance takes over the whole panel and silences every other route into it, so
the interesting tests are not "does it start" -- they are "does it always
stop". There are six ways out (a tap, being turned upside down, the song
ending, a cancelled turn, going face down, and talking to her) and all six have
to leave exactly one clean idle behind them.
"""

import asyncio
from collections import deque
import threading
import types

import pytest

from kiki_gateway import dance
from kiki_gateway.beatgrid import BeatGrid
from kiki_gateway.config import GatewayConfig
from kiki_gateway.session import DeviceSession


class FakeDevice:
    def __init__(self):
        self.media_active = False
        self.media_loaded = False
        self.media_stream_id = 0
        self.stopped = 0
        self.ducked = 0
        self.executed = []

    async def stop(self):
        self.stopped += 1
        self.media_active = False

    async def notify_media(self):
        pass

    async def duck(self):
        self.ducked += 1
        return True

    async def unduck(self):
        pass

    async def execute(self, name, arguments):
        self.executed.append((name, arguments))
        return "Dancing to Test Song."


def make_session(device: FakeDevice | None = None) -> DeviceSession:
    session = DeviceSession.__new__(DeviceSession)
    session.session_id = "test"
    session.config = GatewayConfig()
    session.display = None
    session.device = device
    session.core = types.SimpleNamespace()
    session.turn_abort = threading.Event()
    session.speech_display_tasks = set()
    session.spec_tasks = {}
    session.spec_prefill_tasks = {}
    session.turn_modes = {}
    session.turn_lock = asyncio.Lock()
    session.endpointer = types.SimpleNamespace(
        generation=1, reset=lambda active=False: None
    )
    session.recent_ambient = deque(maxlen=8)
    session.motion_question_last = {}
    session.motion_question_last_any = 0.0
    session.motion_question_task = None
    session.awake = False
    session.playing = False
    session.push_to_talk_active = False
    session.followup_deadline = 0.0
    session.speech_playback_started_at = 0.0
    session.tts_stream_id = 0
    session.posture = None
    session.do_not_disturb = False
    session.dance_active = False
    session.dance_started_at = 0.0
    session.dance_title = ""
    session._logged_first_stats = True
    session._reported_underruns = 0
    session._battery_source = None
    session._battery_bucket = None
    session.events = []

    async def send_event(kind, **fields):
        session.events.append((kind, fields))

    session.send_event = send_event
    return session


def routine_for(session=None):
    grid = BeatGrid(bpm=120.0, beat0=0.05, confidence=0.9,
                    energy=tuple(range(16)) * 8, analysed_seconds=45.0)
    return dance.build_routine(grid, 120.0, None, "Test Song"), grid


async def start_dance(session, device):
    routine, grid = routine_for()
    await session.begin_dance(routine, grid)
    device.media_active = True
    device.media_loaded = True
    session.playing = True
    session.events.clear()
    return routine


def kinds(session):
    return [kind for kind, _ in session.events]


def states(session):
    return [f.get("state") for kind, f in session.events if kind == "state"]


# ------------------------------------------------------------- starting ----

@pytest.mark.asyncio
async def test_the_routine_reaches_the_board_before_any_music():
    session = make_session(FakeDevice())
    routine, grid = routine_for()

    await session.begin_dance(routine, grid)

    assert session.dance_active is True
    kind, fields = session.events[-1]
    assert kind == "dance_start"
    assert fields["bpm"] == pytest.approx(120.0)
    assert fields["beat0_ms"] == 50
    assert fields["routine"]
    assert fields["energy"]
    assert fields["measured"] is True


@pytest.mark.asyncio
async def test_a_hinted_tempo_is_reported_as_unmeasured():
    session = make_session(FakeDevice())
    routine, _ = routine_for()
    hinted = BeatGrid(bpm=112.0, beat0=0.0, confidence=0.0, energy=(),
                      analysed_seconds=0.0)

    await session.begin_dance(routine, hinted)

    assert session.events[-1][1]["measured"] is False


# --------------------------------------------------------------- stopping --

@pytest.mark.asyncio
async def test_a_tap_on_the_stage_stops_the_song_too():
    device = FakeDevice()
    session = make_session(device)
    await start_dance(session, device)

    await session.handle_control({"type": "dance_stop", "reason": "tap"})

    assert session.dance_active is False
    assert device.stopped == 1
    assert session.playing is False
    # The board stopped itself; telling it again would be a loop.
    assert "dance_stop" not in kinds(session)
    assert states(session)[-1] == "idle"


@pytest.mark.asyncio
async def test_turning_her_upside_down_ends_the_performance():
    device = FakeDevice()
    session = make_session(device)
    await start_dance(session, device)

    await session.note_posture("upside_down")

    assert session.dance_active is False
    assert device.stopped == 1
    # This one the gateway initiated, so the board is told.
    assert "dance_stop" in kinds(session)
    # And it is not do-not-disturb: turn her back over and she is just idle.
    assert session.do_not_disturb is False


@pytest.mark.asyncio
async def test_face_down_stops_a_dance_and_keeps_her_quiet():
    device = FakeDevice()
    session = make_session(device)
    await start_dance(session, device)

    await session.note_posture("face_down")

    assert session.dance_active is False
    assert session.do_not_disturb is True
    assert session.playing is False


@pytest.mark.asyncio
async def test_cancelling_the_turn_cancels_the_dance():
    device = FakeDevice()
    session = make_session(device)
    await start_dance(session, device)

    await session.cancel_turn("open_palm")

    assert session.dance_active is False
    assert "dance_stop" in kinds(session)
    assert states(session)[-1] == "idle"


@pytest.mark.asyncio
async def test_talking_to_her_ends_the_dance_rather_than_pausing_it():
    # Ordinary music ducks and resumes; a performance does not. Coming back
    # halfway through a routine the listener has lost the thread of is worse
    # than ending it.
    from kiki_gateway.device_tools import DeviceToolBridge

    session = make_session(None)
    bridge = DeviceToolBridge.__new__(DeviceToolBridge)
    bridge.session = session
    session.device = bridge
    session.dance_active = True
    ended = []

    async def end_dance(reason, notify_device=True):
        ended.append(reason)

    session.end_dance = end_dance

    result = await DeviceToolBridge.duck(bridge)

    assert result is False
    assert ended == ["interrupted"]


@pytest.mark.asyncio
async def test_a_song_that_produces_no_audio_is_not_a_dance():
    # `media_loaded` only means the pump task exists. When the URL is dead
    # (googlevideo 403, dead extractor, no ffmpeg) the pump reads nothing and
    # ends, and the dance is over ~60 ms after it began -- the panel blinks
    # black and back while Kiki has just said she is about to dance. Success
    # has to mean frames were actually sent.
    from kiki_gateway.device_tools import DeviceToolBridge

    bridge = DeviceToolBridge.__new__(DeviceToolBridge)
    bridge.player_task = None            # nothing playing: media_loaded False
    bridge.media_sequence = 0
    assert await DeviceToolBridge._dance_music_started(bridge, timeout=1.0) is False

    class Running:
        done = staticmethod(lambda: False)

    bridge.player_task = Running()
    bridge.media_sequence = 3            # frames really went out
    assert await DeviceToolBridge._dance_music_started(bridge, timeout=1.0) is True


@pytest.mark.asyncio
async def test_stopping_twice_is_harmless():
    device = FakeDevice()
    session = make_session(device)
    await start_dance(session, device)

    await session.end_dance("tap")
    session.events.clear()
    await session.end_dance("tap")

    assert session.events == []


@pytest.mark.asyncio
async def test_a_dance_that_never_started_cannot_be_stopped_into_a_bad_state():
    session = make_session(FakeDevice())
    await session.end_dance("song_ended")
    assert session.events == []
    assert session.dance_active is False


# ----------------------------------------------------------- the fast path --

@pytest.mark.asyncio
async def test_saying_dance_goes_straight_to_the_tool():
    device = FakeDevice()
    session = make_session(device)
    recorded = []
    session.core = types.SimpleNamespace(
        record_exchange=lambda user, assistant: recorded.append((user, assistant)),
        stream_reply=None,
    )

    async def fake_execute(name, arguments):
        device.executed.append((name, arguments))
        session.dance_active = True
        session.dance_title = "Test Song"
        return "Dancing to Test Song."

    device.execute = fake_execute

    await session.respond("kiki dance", 0.0)

    assert device.executed == [("dance", {"request": "kiki dance"})]
    assert recorded and recorded[0][0] == "kiki dance"
    assert "Test Song" in recorded[0][1]


@pytest.mark.asyncio
async def test_a_dance_that_could_not_start_says_so_out_loud():
    device = FakeDevice()
    session = make_session(device)
    spoken = []

    async def fake_execute(name, arguments):
        return "Could not find anything playable."

    async def fake_speak(text):
        spoken.append(text)
        return True

    device.execute = fake_execute
    session.speak_background = fake_speak
    session.core = types.SimpleNamespace(record_exchange=lambda *a: None)

    await session.respond("dance", 0.0)

    assert spoken and "could not find" in spoken[0].lower()


@pytest.mark.asyncio
async def test_a_normal_sentence_is_not_hijacked_by_the_fast_path():
    device = FakeDevice()
    session = make_session(device)
    calls = []
    session.core = types.SimpleNamespace(
        stream_reply=lambda text, abort: calls.append(text) or _empty()
    )

    def _empty():
        async def generator():
            if False:
                yield ("sentence", "")
        return generator()

    session.set_state = lambda **kwargs: asyncio.sleep(0)
    session._tts_worker = lambda queue, at: asyncio.sleep(0, result=False)

    await session.respond("what did we talk about yesterday", 0.0)

    assert device.executed == []
    assert calls == ["what did we talk about yesterday"]
