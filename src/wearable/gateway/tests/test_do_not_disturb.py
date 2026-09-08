"""Face down is the do-not-disturb switch.

It is the one gesture that needs no screen, no button and no sentence, so it
has to be absolute: whatever Kiki is saying or playing stops immediately, and
for as long as she is on her face she starts nothing of her own -- no proactive
question, no movement question, no background line. The panel says why.
"""

import asyncio
import types
from collections import deque

import pytest

from kiki_gateway.config import GatewayConfig
from kiki_gateway.session import DeviceSession


class FakeDevice:
    def __init__(self):
        self.media_active = False
        self.media_loaded = False
        self.media_stream_id = 0
        self.stopped = 0

    async def stop(self):
        self.stopped += 1
        self.media_active = False

    async def notify_media(self):
        pass

    async def duck(self):
        return True

    async def unduck(self):
        pass


def make_session(device: FakeDevice | None = None) -> DeviceSession:
    session = DeviceSession.__new__(DeviceSession)
    session.session_id = "test"
    session.config = GatewayConfig()
    session.display = None
    session.device = device
    session.core = types.SimpleNamespace()
    session.turn_abort = __import__("threading").Event()
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
    # device_stats bookkeeping, so the telemetry route can be driven whole.
    session._logged_first_stats = True
    session._reported_underruns = 0
    session._battery_source = None
    session._battery_bucket = None

    session.events = []

    async def send_event(kind, **fields):
        session.events.append((kind, fields))

    session.send_event = send_event
    return session


def panel_rows(session) -> list[tuple[str, str]]:
    return [
        (f.get("line1", ""), f.get("line2", ""))
        for kind, f in session.events
        if kind == "lcd"
    ]


def states(session) -> list[str]:
    return [f.get("state") for kind, f in session.events if kind == "state"]


@pytest.mark.asyncio
async def test_turning_her_face_down_stops_the_speaking_and_the_music():
    device = FakeDevice()
    device.media_active = True
    session = make_session(device)
    session.playing = True
    session.awake = True

    await session.note_posture("face_down")

    assert session.do_not_disturb is True
    assert device.stopped == 1
    assert session.turn_abort.is_set()
    assert session.playing is False
    assert any(kind == "audio_stop" for kind, _ in session.events)
    # Not left listening: the window closes with everything else.
    assert session.awake is False
    assert states(session)[-1] == "idle"


@pytest.mark.asyncio
async def test_the_panel_says_why_she_went_quiet():
    session = make_session()

    await session.note_posture("face_down")

    assert ("Do Not Disturb", "Face down") in panel_rows(session)


@pytest.mark.asyncio
async def test_returning_to_rest_re_asserts_the_notice():
    # A direct question answered while face down ends on the ordinary idle
    # rows, which would otherwise invite the next one.
    session = make_session()
    await session.note_posture("face_down")
    session.events.clear()

    await session.set_state(state="idle")

    assert panel_rows(session) == [("Do Not Disturb", "Face down")]


@pytest.mark.asyncio
async def test_no_proactive_question_while_she_is_face_down():
    session = make_session()
    session.core = types.SimpleNamespace(stream_proactive=lambda *a, **k: None)

    await session.note_posture("face_down")

    assert await session.ask_proactive_question("say something") is False


@pytest.mark.asyncio
async def test_no_background_speech_while_she_is_face_down():
    session = make_session()

    await session.note_posture("face_down")

    assert await session.speak_background("by the way...") is False


@pytest.mark.asyncio
async def test_the_face_down_event_does_not_also_ask_a_movement_question():
    # face_down has a journal question bank of its own, so without the
    # do-not-disturb check the gesture that means "be quiet" would speak.
    session = make_session()
    asked = []
    session.core = types.SimpleNamespace(
        update_motion_event=lambda name, posture: None,
        choose_motion_question=lambda name: asked.append(name) or "Why the floor?",
        record_motion_question=lambda name, question: None,
    )

    await session._handle_motion_event(
        {"event": "face_down", "posture": "face_down", "intensity": 0.8, "shown": True}
    )

    assert asked == []
    assert session.motion_question_task is None
    assert session.do_not_disturb is True


@pytest.mark.asyncio
async def test_turning_her_back_over_ends_it():
    session = make_session()
    await session.note_posture("face_down")
    session.events.clear()

    await session.note_posture("upright")

    assert session.do_not_disturb is False
    assert states(session) == ["idle"]
    assert panel_rows(session) == []


@pytest.mark.asyncio
async def test_an_unreadable_posture_does_not_cancel_do_not_disturb():
    # "unknown" is what the classifier reports when it cannot tell. Treating
    # that as "turned back over" would let one bad sample end the mode.
    session = make_session()
    await session.note_posture("face_down")

    await session.note_posture("unknown")
    await session.note_posture("")

    assert session.do_not_disturb is True


@pytest.mark.asyncio
async def test_five_second_telemetry_does_not_re_enter_it_over_and_over():
    device = FakeDevice()
    session = make_session(device)

    await session.handle_control({"type": "device_stats", "imu_posture": "face_down"})
    settled = len(panel_rows(session))

    for _ in range(3):
        await session.handle_control({"type": "device_stats", "imu_posture": "face_down"})

    # Only the transition acts. Re-cancelling every five seconds would keep
    # tearing down a turn the user may have started on purpose.
    assert device.stopped == 1
    assert len(panel_rows(session)) == settled


@pytest.mark.asyncio
async def test_telemetry_alone_can_end_it_after_a_reconnect():
    # A motion_event announces the flip, but a session that started while she
    # was already face down only ever learns the posture from device_stats.
    session = make_session()

    await session.handle_control({"type": "device_stats", "imu_posture": "face_down"})
    assert session.do_not_disturb is True

    await session.handle_control({"type": "device_stats", "imu_posture": "face_up"})
    assert session.do_not_disturb is False
