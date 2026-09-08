"""Stop must abandon the answer, not skip to the next one.

Every committed turn runs as its own asyncio task, and nothing bounded how many
could be alive at once. Ask three questions in a row and three replies generate
concurrently, serialised only by the single-slot local model. Cancelling the
audio then simply let the next one start speaking -- so Stop behaved like "next
answer please", and pressing it three times played all three in order.

`turn_abort` could never have fixed this by itself: respond() installs a fresh
Event per turn, so setting it reaches only the newest turn and orphans the
older ones, which keep their own captured copy and speak anyway.
"""

import asyncio

import pytest

from kiki_gateway.session import DeviceSession


class Session(DeviceSession):
    """A DeviceSession with only the turn bookkeeping cancel_turn touches."""

    STOP_GRACE_SECONDS = 0.05

    def __init__(self):
        self.turn_tasks: set[asyncio.Task] = set()
        self.query_turns: set[asyncio.Task] = set()
        self.spoke: list[str] = []

    def spawn_turn(self, name: str, hold: float = 30.0, query: bool = True,
                   cooperative: bool = True):
        """A turn that would eventually speak if nobody stopped it.

        `cooperative` mirrors a real turn, which checks its abort flag between
        sentences. A non-cooperative one stands in for a turn wedged somewhere
        that never looks -- the case the grace period exists for.
        """
        async def answer():
            # Registered from inside, exactly as a real turn does: the flag
            # belongs to the task that will be asked to stop.
            flag = self._new_turn_abort()
            for _ in range(int(hold * 100)):
                if cooperative and flag.is_set():
                    return
                await asyncio.sleep(0.01)
            self.spoke.append(name)

        task = asyncio.create_task(answer())
        self.turn_tasks.add(task)
        if query:
            self.query_turns.add(task)
        task.add_done_callback(self.turn_tasks.discard)
        task.add_done_callback(self.query_turns.discard)
        return task


@pytest.mark.asyncio
async def test_stop_cancels_every_reply_still_in_flight():
    session = Session()
    for name in ("first", "second", "third"):
        session.spawn_turn(name)
    await asyncio.sleep(0)
    assert len(session.turn_tasks) == 3

    cancelled = session._cancel_turn_tasks("stop")
    assert cancelled == "3 turn(s)"

    # Give the loop a chance to actually deliver the cancellations.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Nothing spoke, and nothing is waiting to.
    assert session.spoke == []
    # Asked, not ripped out: the turns stop themselves, which is what lets their
    # teardown run -- including the context compaction a skipped one loses.
    await asyncio.sleep(0.02)
    assert all(flag.is_set() for flag in session.turn_aborts.values())


@pytest.mark.asyncio
async def test_stop_reports_nothing_when_no_turn_is_running():
    session = Session()
    assert session._cancel_turn_tasks("stop") is None


@pytest.mark.asyncio
async def test_a_turn_does_not_cancel_itself():
    """The caller is excluded, or a turn that stops itself never finishes."""
    session = Session()
    survived = []

    async def turn_that_stops_itself():
        # This is the shape of a barge-in: the turn is running when the cancel
        # goes out. Cancelling itself here would kill the coroutine mid-cleanup.
        assert session._cancel_turn_tasks("self") is None
        survived.append(True)

    task = asyncio.create_task(turn_that_stops_itself())
    session.turn_tasks.add(task)
    task.add_done_callback(session.turn_tasks.discard)
    await task
    assert survived == [True]


@pytest.mark.asyncio
async def test_ambient_speech_does_not_kill_the_answer_you_are_waiting_for():
    """The regression that made her listen and then say nothing.

    Every committed utterance reaches the same COMMIT path, ambient ones
    included -- background speech, a TV, the tail of your own sentence, all
    picked up while she is answering. Superseding on those threw the answer
    away and dropped the panel back to listening in silence.
    """
    session = Session()
    answer = session.spawn_turn("the answer", query=True)
    ambient = session.spawn_turn("room noise", query=False)
    await asyncio.sleep(0)

    # A question supersedes only other questions. The ambient turn -- the one
    # that used to do the killing -- is not even a candidate.
    assert session._cancel_turn_tasks("a new question", queries_only=True) == "1 turn(s)"
    await asyncio.sleep(0.03)
    assert answer.done(), "the previous question is displaced"
    assert not ambient.done(), "ambient transcription must survive"
    ambient.cancel()


@pytest.mark.asyncio
async def test_a_new_question_still_displaces_the_previous_one():
    session = Session()
    first = session.spawn_turn("first question", query=True)
    await asyncio.sleep(0)
    assert session._cancel_turn_tasks("superseded", queries_only=True) == "1 turn(s)"
    await asyncio.sleep(0.03)
    assert first.done() and not first.cancelled(), "it should stop itself, cleanly"


@pytest.mark.asyncio
async def test_stop_still_cancels_ambient_turns_too():
    """Stop is not narrowed: it means everything."""
    session = Session()
    session.spawn_turn("answer", query=True)
    session.spawn_turn("room noise", query=False)
    await asyncio.sleep(0)
    assert session._cancel_turn_tasks("stop") == "2 turn(s)"


@pytest.mark.asyncio
async def test_cancelling_never_leaves_playback_latched():
    """The failure that needed a laptop restart.

    A cancelled task cannot be trusted to run its own cleanup -- the reply
    path's finally block opens with an await, which re-raises immediately in a
    task already being cancelled, skipping everything after it. If `playing` is
    left set the gateway discards every microphone frame from then on, so Kiki
    listens, hears nothing and returns to idle for every question until the
    process is restarted.
    """
    session = Session()
    session.playing = True
    session.speech_playback_started_at = 123.0
    session.spawn_turn("mid-reply", query=True)
    await asyncio.sleep(0)

    session._cancel_turn_tasks("stop")
    assert session.playing is False
    assert session.speech_playback_started_at == 0.0


@pytest.mark.asyncio
async def test_a_wedged_turn_is_cancelled_after_the_grace_period():
    """Asking is not enough if the turn never looks at the flag."""
    session = Session()
    wedged = session.spawn_turn("wedged", cooperative=False)
    await asyncio.sleep(0)

    session._cancel_turn_tasks("stop")
    await asyncio.sleep(session.STOP_GRACE_SECONDS + 0.05)
    assert wedged.cancelled(), "a turn that ignores the flag must still be stopped"
