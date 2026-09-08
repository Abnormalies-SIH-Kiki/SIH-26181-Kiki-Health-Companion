from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import time

from websockets.exceptions import ConnectionClosed
from websockets.asyncio.server import serve

from .audio_control import AudioControlState
from .config import GatewayConfig
from .inference import shutdown_shared_core
from .session import DeviceSession


LOG = logging.getLogger(__name__)


class _DropHealthProbeNoise(logging.Filter):
    """Silence the traceback a bare TCP health probe produces.

    The laptop's service manager checks the gateway is alive by opening a socket
    to 8765 and closing it. websockets sees a connection that never sent an HTTP
    request line and logs a full "opening handshake failed" traceback for every
    poll, which buries anything real. Only that exact shape is dropped.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "websockets.server" or not record.exc_info:
            return True
        # The record's own exception is InvalidMessage; the EOFError that
        # identifies a probe is further down the __cause__ chain.
        exc = record.exc_info[1]
        seen = 0
        while exc is not None and seen < 8:
            if isinstance(exc, EOFError):
                return False
            exc = exc.__cause__ or exc.__context__
            seen += 1
        return True


async def _watch_event_loop_lag(threshold: float = 1.0) -> None:
    """Report when this process stops reading its sockets.

    The gateway shares one event loop with the legacy Kiki runtime, which does
    genuinely blocking work -- model warm-ups, synchronous HTTP to Gemini,
    knowledge-base loads. While the loop is blocked nothing is read off the
    device socket, the board's TCP send buffer fills, and its websocket client
    gives up on a write it cannot complete and drops the connection. From the
    board that looks like a dead link; from here it looked like the device
    disconnected for no reason.

    Sleeping 0.5 s and measuring how long it actually took is the cheapest
    possible way to see that, and it turns "the connection dropped again" into a
    number with a cause attached.
    """
    interval = 0.5
    while True:
        before = time.monotonic()
        await asyncio.sleep(interval)
        lag = time.monotonic() - before - interval
        if lag >= threshold:
            LOG.warning(
                "event loop blocked for %.1fs -- nothing was read from the "
                "device socket during that time",
                lag,
            )


async def _summarize_when_the_conversation_is_over(state: dict, config: GatewayConfig) -> None:
    """Write the session summary once the device has really gone away.

    This used to happen on every WebSocket close, which on a bad link meant a
    cloud summarization call every couple of minutes for a "session" that was a
    dropped packet in the middle of a sentence. A reconnect is not the end of a
    conversation; a long silence is.
    """
    interval = max(60.0, config.idle_summary_seconds)
    while True:
        await asyncio.sleep(interval / 4)
        if state["devices"] or state["last_seen"] is None:
            continue
        if time.monotonic() - state["last_seen"] < interval:
            continue
        state["last_seen"] = None
        core = state.get("core")
        saver = getattr(core, "save_session_summary", None)
        if saver is None:
            continue
        LOG.info("no device for %.0f min; saving the session summary", interval / 60)
        try:
            await saver()
        except Exception:
            LOG.exception("idle session summary failed")


async def _close_quietly(websocket) -> None:
    """Close a socket we have already written off, without raising."""
    try:
        await websocket.close(code=1001, reason="superseded")
    except Exception:
        # It is being closed *because* it is unreachable; a failure here is the
        # expected case, not news.
        LOG.debug("superseded socket did not close cleanly", exc_info=True)


async def run_server(config: GatewayConfig) -> None:
    audio_control = AudioControlState(
        config.tts_gain, config.speaker_volume, config.display_sync_offset_ms
    )
    state = {"devices": 0, "last_seen": None, "core": None, "live": None}

    # Kept in a local so the task is not garbage collected mid-run.
    lag_watch = asyncio.create_task(_watch_event_loop_lag())
    idle_watch = asyncio.create_task(
        _summarize_when_the_conversation_is_over(state, config)
    )

    async def handler(websocket) -> None:
        session = DeviceSession(websocket, config)
        peer = websocket.remote_address

        # One board, one session. The board gives up on a stalled socket long
        # before our keepalive does -- it reconnects in seconds, while
        # ping_interval + ping_timeout keeps the corpse here for another 80 s
        # (observed: 80, 82, 100, 140, 191 and 402 s). For all of that time two
        # live DeviceSessions were serving one device: both attached to the
        # shared core, both able to speak, and the older one still streaming a
        # reply into a socket nobody was reading. Reap the old one the moment
        # its replacement arrives -- a second connection *is* the proof that the
        # first is dead, and it is far better proof than a ping timeout.
        previous = state.get("live")
        state["live"] = websocket
        if previous is not None and previous is not websocket:
            LOG.info("device reconnected as %s; closing the superseded socket", peer)
            # Not awaited: closing a socket that is already unreachable waits
            # out close_timeout, and the new session must not be held up by the
            # old one's funeral.
            asyncio.create_task(_close_quietly(previous))

        audio_control.attach(session)
        state["devices"] += 1
        state["core"] = session.core
        opened_at = time.monotonic()
        LOG.info("device connected: %s session=%s", peer, session.session_id)
        try:
            await session.run()
        except ConnectionClosed as exc:
            # A board that is reset, power-cycled or loses Wi-Fi goes away
            # without a close frame. That is ordinary here, and letting it
            # propagate made websockets log a full "connection handler failed"
            # traceback every time -- which matters because the deployment
            # checklist says to read gateway.log for exceptions, and this would
            # bury the real ones.
            #
            # But say *how* it went away, not just that it did. The class name
            # alone reads identically whether the board sent a close frame, the
            # TCP connection was reset, or our own keepalive gave up -- three
            # different faults with three different fixes. `rcvd`/`sent` carry
            # the close frames if there were any; both None means neither side
            # got to say goodbye. The uptime matters too: a session that always
            # dies at about the same age is a timeout somewhere, not bad luck.
            LOG.info(
                "device %s dropped the connection (%s: rcvd=%s sent=%s uptime=%.1fs)",
                peer,
                exc.__class__.__name__,
                getattr(exc, "rcvd", None),
                getattr(exc, "sent", None),
                time.monotonic() - opened_at,
            )
        finally:
            audio_control.detach(session)
            await session.close()
            # Only if it is still ours: a newer connection may already have
            # claimed the slot, and clearing it then would leave the next
            # reconnect with nothing to evict.
            if state.get("live") is websocket:
                state["live"] = None
            state["devices"] = max(0, state["devices"] - 1)
            state["last_seen"] = time.monotonic()
            LOG.info("device disconnected: %s", peer)

    control_server = await asyncio.start_server(
        audio_control.handle_client,
        config.audio_control_host,
        config.audio_control_port,
    )
    async with control_server, serve(
        handler,
        config.bind_host,
        config.bind_port,
        compression=None,
        max_size=2 * 1024 * 1024,
        ping_interval=20,
        # Was 10 s, and it was killing healthy sessions every few minutes with
        # `1011 keepalive ping timeout`. A pong is answered by the board's
        # websocket task, which shares a core with audio and LVGL and can be
        # busy for most of a second at a time; a 10 s deadline turns an ordinary
        # burst into a dropped connection. The cost of a false positive is high
        # -- the whole legacy runtime is rebuilt and llama re-warmed on the next
        # connect, and one of those drops landed mid-warm and left the panel
        # reading "Warming up model" indefinitely. Real liveness is already
        # visible in far better signals: 100 mic frames a second and
        # device_stats every 5 s.
        ping_timeout=60,
        close_timeout=2,
    ):
        LOG.info("gateway listening on ws://%s:%d", config.bind_host, config.bind_port)
        LOG.info(
            "audio calibration control listening on %s:%d",
            config.audio_control_host,
            config.audio_control_port,
        )
        # The runtime now outlives every connection, so the process exiting is
        # the only remaining "conversation is over" event -- and a plain
        # asyncio.Future() never sees SIGTERM, which is how the service manager
        # stops us. Without this the last conversation would never be saved.
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, stopping.set)
        try:
            await stopping.wait()
        finally:
            LOG.info("shutting down; saving the conversation")
            idle_watch.cancel()
            lag_watch.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(shutdown_shared_core(), timeout=60)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config = GatewayConfig.from_env()
    if args.host or args.port:
        config = GatewayConfig(**{
            **config.__dict__,
            "bind_host": args.host or config.bind_host,
            "bind_port": args.port or config.bind_port,
        })
    logging.getLogger("websockets.server").addFilter(_DropHealthProbeNoise())
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_server(config))


if __name__ == "__main__":
    main()
