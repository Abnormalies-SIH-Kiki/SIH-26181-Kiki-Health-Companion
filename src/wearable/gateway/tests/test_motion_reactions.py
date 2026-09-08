import pytest

from kiki_gateway.motion_reactions import motion_question_cooldown
from kiki_gateway.session import DeviceSession


MOTION_STATES = {
    "picked_up", "upright_alert", "face_down", "upside_down", "sideways",
    "gentle_wiggle", "shake", "repeated_shake", "dizzy_after_shake",
    "rocking", "spin", "carried", "bump", "freefall", "hard_landing",
    "set_down", "bored", "very_bored", "dozing", "charging_rest",
    "music_dance", "low_battery_tired", "wake_from_sleep",
}


def test_every_semantic_motion_state_has_a_speech_cooldown():
    assert all(motion_question_cooldown(state) for state in MOTION_STATES)
    assert motion_question_cooldown("not_a_real_state") is None


def fake_session(questions):
    session = DeviceSession.__new__(DeviceSession)
    session.motion_question_last = {}
    session.motion_question_last_any = 0.0
    session.motion_question_task = None
    session.device = None
    session.playing = False
    session.awake = False
    session.push_to_talk_active = False
    session.followup_deadline = 0.0
    session.spoken = []
    session.states = []
    session.cancelled = []
    session.posture = None
    session.do_not_disturb = False

    class Core:
        last_motion = None
        retained = []

        def update_motion_event(self, name, posture):
            self.last_motion = (name, posture)

        def choose_motion_question(self, name):
            values = questions.get(name, [])
            return values.pop(0) if values else None

        def record_motion_question(self, name, question):
            self.retained.append((name, question))

    session.core = Core()

    async def speak(text):
        session.spoken.append(text)
        return True

    async def set_state(state, **_fields):
        session.states.append(state)

    # A face-down event now also flips do-not-disturb, which stops the turn.
    async def cancel_turn(reason):
        session.cancelled.append(reason)

    async def show_do_not_disturb():
        session.states.append("do_not_disturb")

    session.speak_background = speak
    session.set_state = set_state
    session.cancel_turn = cancel_turn
    session._show_do_not_disturb = show_do_not_disturb
    return session


@pytest.mark.asyncio
async def test_session_asks_reserved_question_and_opens_followup_once():
    session = fake_session({
        "repeated_shake": ["What has you this worked up?", "Another question?"]
    })
    event = {
        "event": "repeated_shake",
        "posture": "upright",
        "intensity": 0.9,
        "shown": True,
    }

    await session._handle_motion_event(event)
    assert session.motion_question_task is not None
    await session.motion_question_task
    assert session.spoken == ["What has you this worked up?"]
    assert session.core.last_motion == ("repeated_shake", "upright")
    assert session.core.retained == [
        ("repeated_shake", "What has you this worked up?")
    ]
    assert session.awake is True
    assert session.followup_deadline > 0

    # The event cooldown prevents the next bank entry being consumed.
    await session._handle_motion_event(event)
    assert session.spoken == ["What has you this worked up?"]


@pytest.mark.asyncio
async def test_motion_question_never_interrupts_busy_or_hidden_reaction():
    session = fake_session({"face_down": ["Could I get a better view?"]})
    event = {
        "event": "face_down",
        "posture": "face_down",
        "intensity": 0.8,
        "shown": True,
    }

    session.playing = True
    await session._handle_motion_event(event)
    assert session.motion_question_task is None

    session.playing = False
    await session._handle_motion_event({**event, "shown": False})
    assert session.motion_question_task is None
    assert session.spoken == []
