import asyncio
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from kiki_gateway.config import GatewayConfig
from kiki_gateway.inference import LegacyKikiCore
from kiki_gateway.session import DeviceSession


def bare_core() -> LegacyKikiCore:
    core = LegacyKikiCore.__new__(LegacyKikiCore)
    core.history = [{"role": "system", "content": "You are Kiki."}]
    core.idle_manager = None
    core.max_followup_tool_rounds = 1
    core._execute_calls = lambda calls: ""
    # The runtime is shared across sessions now, so its device-facing callbacks
    # are rebound on every attach rather than captured once.
    core._proactive_callback = None
    core._speak_callback = None
    core._sessions = []
    return core


def test_proactive_defaults_are_the_requested_twenty_to_thirty_minutes():
    config = GatewayConfig()
    assert config.proactive_questions_enabled is True
    assert config.proactive_question_min_seconds == 20 * 60
    assert config.proactive_question_max_seconds == 30 * 60


def test_environment_config_defaults_to_the_bundled_rpi_core(monkeypatch):
    monkeypatch.delenv("KIKI_GATEWAY_LEGACY_ROOT", raising=False)

    config = GatewayConfig.from_env()

    assert Path(config.legacy_root).name == "legacy_kiki"
    assert Path(config.legacy_root).is_dir()


@pytest.mark.asyncio
async def test_proactive_scheduler_uses_the_rpi_idle_mind_selector_when_due():
    core = bare_core()
    core.proactive_question_min_seconds = 0.01
    core.proactive_question_max_seconds = 0.01
    core.proactive_question_busy_retry_seconds = 0.01
    seen_scenes = []
    core.idle_manager = SimpleNamespace(
        is_thinking=False,
        get_proactive_injection=lambda scene: (
            seen_scenes.append(scene) or "RPi-selected grounded prompt"
        ),
    )
    fired = asyncio.Event()

    async def callback(prompt):
        assert prompt == "RPi-selected grounded prompt"
        fired.set()
        return True

    # Late-bound on purpose: this loop outlives every WebSocket session, so it
    # reads the callback of whichever device is attached now rather than one
    # captured at startup on a socket that has since closed.
    core._proactive_callback = callback
    task = asyncio.create_task(core._proactive_question_loop())
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert seen_scenes == [""]


@pytest.mark.asyncio
async def test_stream_proactive_matches_rpi_system_turn_and_forces_cloud():
    core = bare_core()
    core._maybe_inject_time = lambda: None
    registered = []
    core.register_history = lambda history, **kwargs: registered.append(list(history))
    core.idle_manager = SimpleNamespace(mark_next_turn_note_used=lambda text: None)

    def stream(messages, **kwargs):
        assert messages[-1] == {"role": "system", "content": "RPi proactive prompt"}
        assert not any(row["role"] == "user" for row in messages)
        assert kwargs["use_fallback"] is True
        assert kwargs["verify_prefill"] is True
        yield "sentence", "[question-en] How is Monday's demo feeling now?"
        yield "done", "[question-en] How is Monday's demo feeling now?"

    core.stream_response = stream
    events = [
        event
        async for event in core.stream_proactive(
            "RPi proactive prompt", threading.Event()
        )
    ]

    assert events[0][0] == "sentence"
    assert core.history[-1]["role"] == "assistant"
    assert "Monday's demo" in core.history[-1]["content"]
    assert core.history[-2] == {"role": "system", "content": "RPi proactive prompt"}
    assert registered


def fake_session(stream_events) -> DeviceSession:
    session = DeviceSession.__new__(DeviceSession)
    session.core = SimpleNamespace()

    async def stream_proactive(prompt, abort):
        session.prompt_seen = prompt
        for event in stream_events:
            yield event

    session.core.stream_proactive = stream_proactive
    session.device = None
    session.playing = False
    session.awake = False
    session.push_to_talk_active = False
    session.turn_lock = asyncio.Lock()
    session.turn_abort = threading.Event()
    session.tts_stream_id = 0
    session.tts_sequence = 0
    session.followup_deadline = 0.0
    session.events = []
    session.spoken = []

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
        return bool(session.spoken)

    session.send_event = send_event
    session.set_state = set_state
    session._tts_worker = tts_worker
    return session


@pytest.mark.asyncio
async def test_proactive_question_opens_a_reply_window():
    session = fake_session(
        [
            ("sentence", "<oled:curious> Still nervous about Monday?"),
            ("done", "<oled:curious> Still nervous about Monday?"),
        ]
    )

    assert await session.ask_proactive_question("grounded prompt") is True
    assert session.prompt_seen == "grounded prompt"
    assert session.awake is True
    assert session.playing is True
    assert session.followup_deadline > time.monotonic()
    assert session.spoken == [("Still nervous about Monday?", "curious")]
    assert session.events[0] == (
        "state",
        {"state": "thinking", "detail": "I was thinking..."},
    )


@pytest.mark.asyncio
async def test_proactive_question_never_interrupts_an_active_turn():
    session = fake_session([("sentence", "Should not be spoken")])
    session.awake = True

    assert await session.ask_proactive_question("grounded prompt") is False
    assert not hasattr(session, "prompt_seen")
    assert session.spoken == []


@pytest.mark.asyncio
async def test_proactive_silence_returns_to_idle_without_a_reply_window():
    session = fake_session([("done", "")])

    assert await session.ask_proactive_question("weak prompt") is False
    assert session.awake is False
    assert session.playing is False
    assert session.followup_deadline == 0.0
    assert session.events[-1] == ("state", {"state": "idle"})
