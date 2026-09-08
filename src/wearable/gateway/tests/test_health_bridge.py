import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from kiki_gateway.health_bridge import WearableHealthBridge
from kiki_gateway.session import DeviceSession


def summary(hr=72, steps=100, worn=True, activity="still", advisory=None):
    return {
        "latest": {
            "heart_rate": {"value": hr, "quality": "GOOD"},
            "steps_today": steps, "worn": worn, "activity": activity,
        },
        "advisories": ([{"id": advisory}] if advisory else []),
        "context": f"Wearable: HR {hr} bpm; {steps} steps today; worn.",
    }


def test_bridge_only_notifies_for_material_health_changes():
    updates = []
    bridge = WearableHealthBridge("", on_update=updates.append,
                                  context_cooldown_seconds=0)
    bridge._accept_summary(summary())
    bridge._accept_summary(summary(hr=75, steps=499))
    bridge._accept_summary(summary(hr=77, steps=500))
    bridge._accept_summary(summary(hr=83, steps=501))

    assert len(updates) == 3  # initial, 500-step milestone, >=5 bpm from accepted
    assert updates[-1]["latest"]["heart_rate"]["value"] == 83


def test_session_acks_only_after_pi_accepts_and_records_whatsapp_outcome():
    sent = []
    outcomes = []

    class Bridge:
        def ingest(self, payload):
            assert payload["batch_id"] == "b1"
            return {
                "accepted": True, "duplicate": False,
                "alert_requested": {"id": "a1", "message": "check in",
                                    "whatsapp_recipients": ["Family"]},
            }

        def record_alert_outcome(self, outcome):
            outcomes.append(outcome)

    session = DeviceSession.__new__(DeviceSession)
    session.core = SimpleNamespace(
        health_bridge=Bridge(),
        dispatch_wearable_whatsapp_alert=lambda _alert: [
            {"channel": "whatsapp", "recipient": "Family", "accepted": True}
        ],
    )

    async def send_event(kind, **fields):
        sent.append((kind, fields))

    session.send_event = send_event
    asyncio.run(session._handle_health_telemetry({
        "type": "health_telemetry", "batch_id": "b1", "steps_delta": 10,
    }))

    assert sent == [("health_telemetry_ack", {
        "batch_id": "b1", "accepted": True, "duplicate": False, "retry": False,
    })]
    assert outcomes[0]["accepted"] is True
    assert outcomes[0]["delivery_confirmed"] is False


def test_session_requests_retry_when_pi_is_unavailable():
    sent = []

    class Bridge:
        def ingest(self, _payload):
            raise OSError("offline")

    session = DeviceSession.__new__(DeviceSession)
    session.core = SimpleNamespace(health_bridge=Bridge())

    async def send_event(kind, **fields):
        sent.append((kind, fields))

    session.send_event = send_event
    asyncio.run(session._handle_health_telemetry({"batch_id": "b1"}))

    assert sent[0][0] == "health_telemetry_ack"
    assert sent[0][1]["accepted"] is False
    assert sent[0][1]["retry"] is True


def test_fall_voice_answer_cancels_or_confirms_before_normal_routing():
    async def scenario(text):
        sent = []
        session = DeviceSession.__new__(DeviceSession)
        session.fall_check_pending = True

        async def send_event(kind, **fields):
            sent.append((kind, fields))

        session.send_event = send_event
        handled = await session._handle_fall_voice_response(text)
        return handled, sent

    handled, sent = asyncio.run(scenario("I'm okay, cancel it"))
    assert handled is True and sent[0][0] == "fall_check_cancel"
    handled, sent = asyncio.run(scenario("I'm not okay, help me"))
    assert handled is True and sent[0][0] == "fall_check_confirm"


def delivery_session(bridge):
    session = DeviceSession.__new__(DeviceSession)
    session.core = SimpleNamespace(health_bridge=bridge)
    session.health_queue = asyncio.Queue(maxsize=4)
    session.health_batch_ids = set()
    session.health_task = None
    session.fall_check_pending = False
    session.events = []
    session.playing = True
    session.awake = True
    session.tts_stream_id = 1
    session.tts_sequence = 10
    session.device = None
    session.display = None
    session.followup_deadline = time.monotonic() - 20
    session.web_followup_pending = False
    session.endpointer = SimpleNamespace(reset=lambda **_kwargs: None)
    session._reset_barge_in = lambda: None
    session._reset_wakeword = lambda: None

    async def send_event(kind, **fields):
        session.events.append((kind, fields))

    async def cancel_turn(reason):
        session.events.append(("cancelled", {"reason": reason}))

    session.send_event = send_event
    session.cancel_turn = cancel_turn
    return session


@pytest.mark.asyncio
async def test_slow_health_request_does_not_block_stop_ping_or_playback_followup():
    started = threading.Event()
    release = threading.Event()

    class Bridge:
        def ingest(self, _batch):
            started.set()
            assert release.wait(3), "test did not release HTTP request"
            return {"accepted": True}

    session = delivery_session(Bridge())
    try:
        await asyncio.wait_for(session.handle_control({
            "type": "health_telemetry", "batch_id": "slow",
        }), timeout=0.2)
        assert await asyncio.to_thread(started.wait, 1)
        assert session.events == []  # no early acknowledgement
        # Same sequential dispatch as the websocket reader: health must return
        # so the next button, keepalive and speaker-complete messages can run.
        await asyncio.wait_for(session.handle_control({"type": "cancel_turn"}), 0.2)
        await asyncio.wait_for(session.handle_control({"type": "ping"}), 0.2)
        await asyncio.wait_for(session.handle_control({"type": "playback_drained"}), 0.2)
        assert not session.playing
        assert session.followup_deadline >= time.monotonic() + 14
        assert [kind for kind, _ in session.events] == ["cancelled", "pong", "state"]
        assert session.events[-1][1]["state"] == "followup"
        release.set()
        await asyncio.wait_for(session.health_queue.join(), 1)
        assert session.events[-1][0] == "health_telemetry_ack"
        assert session.events[-1][1]["accepted"] is True
    finally:
        release.set()
        await session._stop_health_delivery()


@pytest.mark.asyncio
async def test_health_retries_are_bounded_and_fall_state_does_not_wait_for_pi():
    session = delivery_session(None)
    waiting = asyncio.Event()

    async def blocked_delivery(_event):
        await waiting.wait()

    session._handle_health_telemetry = blocked_delivery
    try:
        for _ in range(100):
            await session.handle_control({
                "type": "health_fall", "batch_id": "pending",
                "fall": {"status": "pending"},
            })
        assert session.health_queue.qsize() == 1
        assert session.fall_check_pending
        await asyncio.sleep(0)  # worker now owns the pending request
        for i in range(10):
            await session.handle_control({"type": "health_telemetry", "batch_id": str(i)})
        assert session.health_queue.qsize() == 4
        assert len(session.health_batch_ids) == 5  # active plus queued
        await session.handle_control({
            "type": "health_fall", "batch_id": "cancelled",
            "fall": {"status": "cancelled"},
        })
        assert not session.fall_check_pending  # even when delivery queue is full
        await asyncio.wait_for(session._stop_health_delivery(), 0.2)
        assert session.health_queue.empty()
        assert not session.health_batch_ids
        assert session.events == []  # nothing lost was acknowledged
    finally:
        waiting.set()
        await session._stop_health_delivery()


@pytest.mark.asyncio
async def test_speaker_completion_does_not_wake_a_sleeping_user_or_interrupt_music():
    session = delivery_session(None)
    session.awake = False
    session.followup_deadline = 0
    await session.handle_control({"type": "playback_drained"})
    assert session.events[-1][1]["state"] == "idle"
    assert session.followup_deadline == 0
    session.awake = True
    session.device = SimpleNamespace(media_active=True)
    await session.handle_control({"type": "playback_drained"})
    assert session.playing
    assert session.events[-1][1]["state"] == "music"
    assert session.followup_deadline == 0
