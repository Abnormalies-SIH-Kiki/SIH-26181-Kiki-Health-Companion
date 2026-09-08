"""The panel must admit when it is actually warming.

Suppressing "Warming up model" on reconnect was right: Kiki used to be rebuilt
per WebSocket, so it appeared after every dropped link. But suppressing it
unconditionally made the panel claim ready while the first question silently
paid a 45 s prefill -- "it says nothing and then works after a while" is the
same confusion in the other direction.

A resident prefix is hash-deduped and returns in milliseconds. Only a genuine
repair is slow, and only that is worth showing.
"""

import asyncio

import pytest

from kiki_gateway.config import GatewayConfig
from kiki_gateway.session import DeviceSession


class Session(DeviceSession):
    WARM_NOTICE_AFTER_SECONDS = 0.05

    def __init__(self):
        self.config = GatewayConfig()
        self.states: list[str] = []
        self.playing = False
        self.awake = False
        self.display = None

    async def set_state(self, state: str, **fields) -> None:
        self.states.append(state)


@pytest.mark.asyncio
async def test_a_deduped_warm_says_nothing():
    """The common case: the prefix survived, so the board stays idle."""
    session = Session()

    async def instant():
        return None

    await session._reverify_warm(instant)
    assert session.states == []


@pytest.mark.asyncio
async def test_a_real_prefill_shows_warming_then_hands_the_panel_back():
    session = Session()

    async def slow():
        await asyncio.sleep(0.2)

    await session._reverify_warm(slow)
    assert session.states == ["warming", "idle"]


@pytest.mark.asyncio
async def test_a_question_mid_warm_keeps_the_panel():
    """Someone who starts talking owns the state; warming must not steal it."""
    session = Session()

    async def slow():
        await asyncio.sleep(0.2)
        session.awake = True   # a question arrived while the prefill ran

    await session._reverify_warm(slow)
    assert session.states == ["warming"], "must not force idle over a live turn"


@pytest.mark.asyncio
async def test_a_queued_but_warm_rewarm_stays_silent():
    """A plain disconnect must never show "Warming up model".

    The runtime survives the socket, so the prefix is still resident and
    nothing needs rebuilding -- that is the whole point of Kiki outliving the
    connection. But the local box has one slot, so even a no-op rewarm can wait
    behind idle-mind or worker output. Waiting is not warming, and the panel
    must not claim it is.
    """
    session = Session()
    session.WARM_NOTICE_AFTER_SECONDS = 0.3

    async def queued_then_deduped():
        await asyncio.sleep(0.1)   # behind other work on the single slot

    await session._reverify_warm(queued_then_deduped)
    assert session.states == [], "a queued rewarm is not a cold start"
