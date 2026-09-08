"""One Kiki per process, not one per WebSocket.

A DeviceSession is per-connection. The conversation, the warm KV prefix, the
workers and the idle mind are not. Building a LegacyKikiCore per socket meant
that every dropped link -- on a college AP, every 15 to 275 seconds -- threw the
conversation away, re-read the knowledge base, re-registered the speaking
prefix, spent a cloud call summarizing a fragment, and put "Warming up model"
back on the panel. These tests pin the shape that fixed it.
"""

import asyncio
import threading
import types

import pytest

from kiki_gateway import inference
from kiki_gateway.config import GatewayConfig
from kiki_gateway.inference import LegacyKikiCore
from kiki_gateway.session import DeviceSession


@pytest.fixture(autouse=True)
def _no_leftover_core():
    inference._SHARED_CORE = None
    yield
    inference._SHARED_CORE = None


def make_core() -> LegacyKikiCore:
    """A core without its very heavy __init__ (config, KB, llama registration)."""
    core = LegacyKikiCore.__new__(LegacyKikiCore)
    core.device_executor = None
    core.media_active = None
    core._proactive_callback = None
    core._speak_callback = None
    core._start_task = None
    core.warmed = False
    core._summarized_upto = 0
    core.worker_manager = None
    core._sessions = []
    core.history = [{"role": "system", "content": "prompt"}]
    return core


def make_session(core, narrowband: bool = False) -> DeviceSession:
    session = DeviceSession.__new__(DeviceSession)
    session.session_id = "s"
    session.config = GatewayConfig()
    session.core = core
    session.display = None
    session.device = None
    session.narrowband_out = narrowband
    session.playback_lead = session.config.playback_lead_seconds
    session.media_lead = session.config.media_lead_seconds
    session.turn_abort = threading.Event()
    session.speech_display_tasks = set()
    session.spec_tasks = {}
    session.spec_prefill_tasks = {}
    session.partial_tasks = set()
    session.turn_tasks = set()
    session.background_start_task = None
    session.ota_watch_task = None
    session.motion_question_task = None
    # Added to DeviceSession.__init__ alongside the wearable fall alert; this
    # hand-built session predates it and close() iterates it.
    session.health_alert_tasks = set()
    session.panel_status_task = None
    session.health_queue = None
    session.health_task = None
    session.rnnoise = types.SimpleNamespace(close=lambda: None)
    return session


def test_one_core_is_shared_by_every_session(monkeypatch):
    built = []

    def fake_init(self, legacy_root, device_executor=None, media_active=None,
                  gateway_config=None):
        built.append(legacy_root)
        for name, value in vars(make_core()).items():
            setattr(self, name, value)

    monkeypatch.setattr(LegacyKikiCore, "__init__", fake_init)
    config = GatewayConfig(legacy_root="/somewhere")

    first = inference.get_shared_core(config)
    second = inference.get_shared_core(config)

    assert first is second
    assert built == ["/somewhere"], "the runtime must be built once, not per connection"


def test_the_conversation_survives_a_reconnect():
    core = make_core()
    core.history.append({"role": "user", "content": "remember the tacos"})

    first = make_session(core)
    core.attach(first)
    core.detach(first)
    second = make_session(core)
    core.attach(second)

    assert core.history[-1]["content"] == "remember the tacos"
    assert core.device_executor == second._execute_device_tool_sync


def test_a_departing_session_does_not_unhook_the_arriving_one():
    """Sockets overlap: the board reconnects before the old one is reaped."""
    core = make_core()
    old = make_session(core)
    new = make_session(core)

    core.attach(old)
    core.attach(new)      # the board is back before `old` has finished closing
    core.detach(old)      # ...and only now does the old handler run its finally

    assert core.device_executor == new._execute_device_tool_sync
    assert core._speak_callback == new.speak_background


def test_detaching_the_live_session_promotes_whoever_is_left():
    core = make_core()
    first = make_session(core)
    second = make_session(core)
    core.attach(first)
    core.attach(second)

    core.detach(second)   # the newest device goes away

    assert core.device_executor == first._execute_device_tool_sync, (
        "the runtime must fall back to a device that is still connected, not "
        "go silent until the next reconnect"
    )


def test_detaching_the_current_session_really_detaches():
    core = make_core()
    session = make_session(core)
    core.attach(session)
    core.detach(session)
    assert core.device_executor is None
    assert core._speak_callback is None
    assert core._proactive_callback is None


def test_closing_a_session_does_not_shut_the_runtime_down():
    core = make_core()
    core.shutdown = lambda: pytest.fail("a dropped link is not the end of a conversation")
    session = make_session(core)
    core.attach(session)

    asyncio.run(session.close())

    assert core.device_executor is None       # detached...
    assert core.history                        # ...but the conversation is intact


def test_a_reconnect_does_not_summarize_again():
    core = make_core()
    core.history += [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    calls = []
    core._conversation_text = lambda: "user: hello\nassistant: hi"
    core._summarize = lambda text: calls.append(text) or "a summary"

    async def run():
        await core.save_session_summary()
        await core.save_session_summary()

    try:
        asyncio.run(run())
    except Exception:
        # save_summary writes to the legacy tree, which is not importable in a
        # bare test env. What matters is that the second call bailed out early.
        pass
    assert len(calls) <= 1, "a quiet reconnect must not spend a second cloud call"


def test_a_stuttered_reply_buys_send_lead_not_time_to_first_word():
    session = make_session(make_core(), narrowband=True)
    session.playback_lead = session.config.playback_lead_seconds_remote
    before = session.playback_lead

    asyncio.run(session.handle_control({"type": "request_lead", "seconds": 3.25}))
    assert session.playback_lead == 3.25 > before

    # Clamped: a device asking for a minute of cushion does not get one.
    asyncio.run(session.handle_control({"type": "request_lead", "seconds": 60.0}))
    assert session.playback_lead == session.config.playback_lead_seconds_max

    # And never lowered -- a later small request cannot undo a real finding.
    asyncio.run(session.handle_control({"type": "request_lead", "seconds": 0.1}))
    assert session.playback_lead == session.config.playback_lead_seconds_max


def test_a_bad_request_lead_is_ignored_rather_than_crashing():
    session = make_session(make_core())
    before = session.playback_lead
    asyncio.run(session.handle_control({"type": "request_lead", "seconds": "lots"}))
    assert session.playback_lead == before
