#!/usr/bin/env python3
"""The board, without the board.

Speaks the real wire protocol (``kiki_gateway.protocol``) so the gateway cannot
tell it apart from the panel, and reports the numbers a hardware run cannot give
you quickly: time-to-first-word split into its parts, playback underruns from a
faithful port of the firmware's jitter buffer, control-event round trip, bytes
per second in each direction, and session drops.

    python3 fake_device.py --url ws://127.0.0.1:8766 --token $KIKI_GATEWAY_TOKEN \
        --link cloud --turns 10 --say "kiki what is the capital of france"

Point ``--url`` at ``slowlink.py`` to run it over a bad link.

**Three TTFW numbers, because they answer different questions.** The gateway
reports its own ``ttfw_ms``, measured from the endpoint it detected to the first
PCM chunk it handed to the socket -- that number is blind to the network and to
the board's prebuffer gate, so it can look perfect while the user waits. This
tool additionally measures from the last speech sample it *sent* to the first
byte that arrived (transport), and from that same instant to the moment its
playback model actually starts producing sound (**perceived** -- the one a human
experiences). Optimising the first at the expense of the third is the failure
this harness exists to catch.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import statistics
import struct
import sys
import time
import wave
from pathlib import Path

import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kiki_gateway.protocol import (  # noqa: E402
    AudioFlag,
    AudioFrame,
    BinaryKind,
    decode_event,
    encode_event,
)

MIC_RATE = 16000
MIC_FRAME = 512                     # samples; the ESP-SR AEC output size (32 ms)
FRAME_SECONDS = MIC_FRAME / MIC_RATE
PLAYBACK_RATE = 48000               # the codec runs at 48 kHz for everything


# --- the firmware's jitter buffer, ported ----------------------------------
# Mirrors audio_pipeline.cpp so an underrun count here means the same thing an
# underrun count on the board means. Keep these in step with the constants
# there; --legacy-buffer restores the pre-fix values so a before/after run can
# be done without reflashing the board.
PREBUFFER_FLOOR_MS = 100
RING_BYTES = 768 * 1024
LEAD_STEP_SECONDS = 0.75
LEAD_CEILING_SECONDS = 5.0
STREAM_STALL_TIMEOUT = 3.0
LEGACY_PREBUFFER_CEILING_MS = 340
LEGACY_PREBUFFER_STEP_MS = 60
LEGACY_PREBUFFER_RECOVER_MS = 20
LEGACY_RING_BYTES = 128 * 1024


class PlaybackModel:
    """What the speaker would actually have done with the audio that arrived."""

    def __init__(self, legacy: bool = False):
        self.legacy = legacy
        self.ring_bytes = LEGACY_RING_BYTES if legacy else RING_BYTES
        self.prebuffer_ms = PREBUFFER_FLOOR_MS
        self.requested_lead = 0.0
        self.pending_lead = 0.0
        self.reset_session()
        self.underruns = 0
        self.underrun_ms = 0.0
        self.dropped_bytes = 0
        self.forced_ends = 0
        self.started_at: float | None = None   # when sound first came out

    def reset_session(self):
        self.active = False
        self.started = False
        self.buffered = 0.0                    # seconds of audio held
        self.session_started = 0.0
        self.end_requested = False
        self.empty_since: float | None = None
        self.session_underruns_at_start = 0

    def queue(self, seconds: float, now: float) -> None:
        if self.buffered * PLAYBACK_RATE * 2 > self.ring_bytes:
            self.dropped_bytes += int(seconds * PLAYBACK_RATE * 2)
            return
        self.buffered += seconds
        if not self.active:
            self.active = True
            self.started = False
            self.end_requested = False
            self.session_started = now
            self.session_underruns_at_start = self.underruns
            self.started_at = None

    def mark_end(self) -> None:
        self.end_requested = True

    def abort(self) -> None:
        self.buffered = 0.0
        self.reset_session()

    def tick(self, dt: float, now: float) -> bool:
        """Advance by dt. Returns True when a stream has just drained."""
        if not self.active:
            return False
        if not self.started:
            target = self.prebuffer_ms / 1000.0
            waited = now - self.session_started
            if self.buffered >= target or self.end_requested or waited >= target:
                self.started = True
                self.started_at = now
            else:
                return False
        if self.buffered >= dt:
            self.buffered -= dt
            self.empty_since = None
            return False
        # Nothing left: either the reply finished or the network fell behind.
        self.buffered = 0.0
        if self.end_requested:
            self.pending_lead = self._retune()
            self.reset_session()
            return True
        if self.empty_since is None:
            self.empty_since = now
            self.underruns += 1
        elif now - self.empty_since >= STREAM_STALL_TIMEOUT:
            self.forced_ends += 1
            self.pending_lead = self._retune()
            self.reset_session()
            return True
        self.underrun_ms += dt * 1000.0
        return False

    def _retune(self) -> float:
        """Returns the send lead to ask the gateway for, or 0 to ask for nothing.

        The board no longer deepens its own start gate after a stutter -- that
        gate is time-to-first-word. It asks the gateway to send further ahead
        instead, which buys the same cushion for free.
        """
        stuttered = self.underruns - self.session_underruns_at_start
        if self.legacy:
            if stuttered:
                self.prebuffer_ms = min(self.prebuffer_ms + LEGACY_PREBUFFER_STEP_MS,
                                        LEGACY_PREBUFFER_CEILING_MS)
            elif self.prebuffer_ms > PREBUFFER_FLOOR_MS:
                self.prebuffer_ms = max(self.prebuffer_ms - LEGACY_PREBUFFER_RECOVER_MS,
                                        PREBUFFER_FLOOR_MS)
            return 0.0
        if not stuttered or self.requested_lead >= LEAD_CEILING_SECONDS:
            return 0.0
        self.requested_lead = min(self.requested_lead + LEAD_STEP_SECONDS,
                                  LEAD_CEILING_SECONDS)
        return self.requested_lead


# --- stimulus ---------------------------------------------------------------

def _resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return samples
    try:
        from scipy.signal import resample_poly
        g = math.gcd(src, dst)
        return resample_poly(samples, dst // g, src // g)
    except Exception:
        n = int(round(len(samples) * dst / src))
        return np.interp(np.linspace(0, len(samples) - 1, n),
                         np.arange(len(samples)), samples)


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: only 16-bit wav is supported")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return _resample(data, rate, MIC_RATE).astype(np.float32)


def synthesize(text: str, tts_url: str, cache: Path) -> np.ndarray:
    """Make the stimulus with the laptop's own TTS server.

    A harness that can generate its own known utterance is reproducible; a
    checked-in clip of someone saying something once is not, and nobody can tell
    from the file whether Whisper got it right.
    """
    if cache.exists():
        return load_wav(str(cache))
    import requests
    resp = requests.post(tts_url, json={"input": text, "response_format": "pcm"},
                         timeout=(3, 60))
    resp.raise_for_status()
    # OmniVoice answers with raw 24 kHz s16 mono; the gateway's own client
    # upsamples 2x for the codec, which is not what a microphone would hear.
    samples = np.frombuffer(resp.content, dtype="<i2").astype(np.float32)
    samples = _resample(samples, 24000, MIC_RATE)
    peak = float(np.max(np.abs(samples))) or 1.0
    samples = samples * (0.6 * 32767.0 / peak)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(cache), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(MIC_RATE)
        w.writeframes(np.clip(samples, -32768, 32767).astype("<i2").tobytes())
    return samples


# --- the device -------------------------------------------------------------

class Turn:
    __slots__ = ("index", "speech_end", "first_byte", "first_audible",
                 "gateway_ttfw_ms", "control_rtt_ms", "underruns_before",
                 "reply_done", "reply_seconds", "replay", "first_pcm_event")

    def __init__(self, index: int):
        self.index = index
        self.replay = {}
        # When the gateway's own "I am sending audio now" event landed. The
        # binary frames follow it immediately on the same ordered socket, so a
        # gap between the two separates "the gateway sent late" from "the
        # network delivered late" -- two findings with nothing in common.
        self.first_pcm_event = None
        self.speech_end = None
        self.first_byte = None
        self.first_audible = None
        self.gateway_ttfw_ms = None
        self.control_rtt_ms = None
        self.underruns_before = 0
        self.reply_done = None
        self.reply_seconds = 0.0


def replay(arrivals: list[tuple[float, float]], ends: list[float],
           prebuffer_ms: int, ring_bytes: int = RING_BYTES) -> dict:
    """What the speaker would have done, computed from arrival times alone.

    Walks the arrival log in order, holding the same prebuffer gate the firmware
    holds, and reports when sound would have started, how much silence the
    listener would have heard, and how much audio the 128 KiB ring would have
    had to throw away. Independent of this process's own scheduling.
    """
    started_at = None
    playhead = None        # wall time up to which audio has been produced
    buffered = 0.0
    session_start = None
    underruns = 0
    silence = 0.0
    dropped = 0.0
    ends = sorted(ends)
    ring_seconds = ring_bytes / (PLAYBACK_RATE * 2)

    for at, seconds in arrivals:
        if session_start is None:
            session_start = at
        if started_at is not None:
            # Drain whatever should have played between the last arrival and
            # this one, counting any shortfall as audible silence.
            played = min(buffered, at - playhead)
            gap = (at - playhead) - played
            buffered -= played
            playhead = at
            if gap > 1e-6:
                underruns += 1
                silence += gap
        buffered += seconds
        if buffered > ring_seconds:
            dropped += (buffered - ring_seconds) * PLAYBACK_RATE * 2
            buffered = ring_seconds
        if started_at is None:
            target = prebuffer_ms / 1000.0
            if buffered >= target or (at - session_start) >= target:
                started_at = at
                playhead = at
    drain_at = (playhead + buffered) if playhead is not None else None
    return {
        "started_at": started_at,
        "underruns": underruns,
        "silence_ms": silence * 1000.0,
        "dropped_bytes": int(dropped),
        "drained_at": drain_at,
        "audio_seconds": sum(s for _, s in arrivals),
    }


class FakeDevice:
    def __init__(self, args, stimulus: np.ndarray):
        self.args = args
        self.stimulus = stimulus
        self.playback = PlaybackModel(legacy=args.legacy_buffer)
        self.turns: list[Turn] = []
        self.turn: Turn | None = None
        self.sequence = 0
        self.ws_drops = 0
        self.bytes_up = 0
        self.bytes_down = 0
        self.state = "?"
        self.connected = asyncio.Event()
        self.ws = None
        self.started_at = time.monotonic()
        self.reply_finished = asyncio.Event()
        self.control_sent_at: float | None = None
        # The board's stream watermarks, which exist so audio already in flight
        # when a turn is cancelled cannot be played over the next one.
        self.min_tts = self.last_tts = 0
        self.min_media = self.last_media = 0
        self.sessions = 0
        self.stall_events: list[float] = []
        self.watermarked = 0
        self.audio_stops = 0
        # Arrival log, replayed analytically at the end of each turn. A live
        # ticker on a Raspberry Pi cannot tell its own scheduling jitter from a
        # real underrun, and "the harness stuttered" would be indistinguishable
        # from the bug we are here to measure. Exact arithmetic over timestamps
        # can: it depends only on when bytes arrived, which is what the network
        # decided, not on when this process happened to be scheduled.
        self.last_audio_at = 0.0
        self.wedged = 0
        # Stop-button test: when set, everything after this instant is audio the
        # gateway sent AFTER being told to stop.
        self.cancel_sent_at = None
        self.bytes_after_cancel = 0
        self.last_audio_after_cancel = 0.0
        self.arrivals: list[tuple[float, float]] = []
        self.stream_ends: list[float] = []
        self.tick_worst = 0.0

    # -- sending ------------------------------------------------------------
    async def send_event(self, kind: str, **fields) -> None:
        if self.ws is None:
            return
        message = encode_event(kind, **fields)
        with contextlib.suppress(Exception):
            await self.ws.send(message)
            self.bytes_up += len(message)

    async def send_hello(self) -> None:
        await self.send_event(
            "hello",
            token=self.args.token,
            device="waveshare-amoled-1.75",
            firmware="fakedev0",
            link=self.args.link,
            rssi=self.args.rssi,
            wifi_drops=0,
            wifi_reason=0,
            ws_drops=self.ws_drops,
        )

    async def send_mic(self, samples: np.ndarray, speech: bool) -> None:
        flags = int(AudioFlag.AEC_PROCESSED)
        if speech:
            flags |= int(AudioFlag.DEVICE_VAD_SPEECH)
        frame = AudioFrame(
            BinaryKind.MIC_PCM_S16_16K_MONO, flags, 0, self.sequence,
            time.time_ns() // 1000,
            np.clip(samples, -32768, 32767).astype("<i2").tobytes(),
        )
        self.sequence += 1
        raw = frame.encode()
        send_started = time.monotonic()
        with contextlib.suppress(Exception):
            await self.ws.send(raw)
            self.bytes_up += len(raw)
        took = time.monotonic() - send_started
        # The firmware's own early warning: a send that took far longer than the
        # 32 ms of audio it carries is a session about to be lost.
        if took > 0.25:
            self.stall_events.append(took)

    # -- receiving ----------------------------------------------------------
    def _handle_binary(self, raw: bytes, now: float) -> None:
        frame = AudioFrame.decode(raw)
        kind = frame.kind
        if kind in (BinaryKind.TTS_PCM_S16_48K_MONO, BinaryKind.TTS_PCM_S16_16K_MONO):
            if frame.stream_id < self.min_tts:
                # The board discards these silently, which is what made the
                # 2026-08-17 "she talks, the caption appears, no sound comes
                # out" bug so hard to see. Count them: an unexplained delay to
                # first audio is either this or the network, and they need
                # different fixes.
                self.watermarked += 1
                return
            self.last_tts = frame.stream_id
            rate = 48000 if kind is BinaryKind.TTS_PCM_S16_48K_MONO else 16000
            # Only count audio that arrived after the user stopped talking. On
            # a slow link the previous reply is often still draining when the
            # next turn begins, and attributing those bytes to this turn reads
            # as a negative time-to-first-word.
            if (self.turn is not None and self.turn.first_byte is None
                    and self.turn.speech_end is not None
                    and now >= self.turn.speech_end):
                self.turn.first_byte = now
        elif kind in (BinaryKind.MEDIA_PCM_S16_48K_MONO, BinaryKind.MEDIA_PCM_S16_16K_MONO):
            if frame.stream_id < self.min_media:
                self.watermarked += 1
                return
            self.last_media = frame.stream_id
            rate = 48000 if kind is BinaryKind.MEDIA_PCM_S16_48K_MONO else 16000
        else:
            return
        seconds = len(frame.payload) / 2 / rate
        if self.turn is not None:
            self.turn.reply_seconds += seconds
        self.arrivals.append((now, seconds))
        self.last_audio_at = now
        if self.cancel_sent_at is not None and now > self.cancel_sent_at:
            self.bytes_after_cancel += len(frame.payload)
            self.last_audio_after_cancel = now
        self.playback.queue(seconds, now)

    async def _handle_json(self, message: str, now: float) -> None:
        event = decode_event(message)
        kind = event.get("type")
        if kind == "state":
            self.state = event.get("state", "?")
            if self.control_sent_at is not None and self.turn is not None:
                self.turn.control_rtt_ms = (now - self.control_sent_at) * 1000.0
                self.control_sent_at = None
            if self.args.verbose:
                print(f"    state={self.state} {event.get('detail','')}", flush=True)
        elif kind == "first_pcm":
            if self.turn is not None:
                self.turn.gateway_ttfw_ms = event.get("ttfw_ms")
                if self.turn.first_pcm_event is None:
                    self.turn.first_pcm_event = now
        elif kind == "audio_stop":
            for name, attr in (("tts_stream_id", "tts"), ("media_stream_id", "media")):
                value = event.get(name)
                if isinstance(value, (int, float)) and value >= 0:
                    last = getattr(self, f"last_{attr}")
                    cutoff = max(last, int(value))
                    if cutoff >= getattr(self, f"min_{attr}"):
                        setattr(self, f"min_{attr}", cutoff + 1)
            self.audio_stops += 1
            self.playback.abort()
        elif kind == "audio_end":
            stale = False
            if isinstance(event.get("stream_id"), (int, float)) and event.get("kind"):
                floor = self.min_media if event["kind"] == "media" else self.min_tts
                stale = int(event["stream_id"]) < floor
            if not stale:
                self.stream_ends.append(now)
                self.playback.mark_end()
        elif kind == "hello_ack":
            self.connected.set()
        elif kind == "speech" and self.args.verbose:
            print(f"    speech: {event.get('text','')[:90]}", flush=True)
        elif kind == "error":
            print(f"    !! gateway error: {event}", flush=True)

    async def reader(self) -> None:
        async for message in self.ws:
            now = time.monotonic()
            if isinstance(message, str):
                self.bytes_down += len(message)
                await self._handle_json(message, now)
            else:
                self.bytes_down += len(message)
                self._handle_binary(message, now)

    # -- the speaker's clock ------------------------------------------------
    async def speaker(self) -> None:
        tick = 0.01
        last = time.monotonic()
        was_started = False
        while True:
            await asyncio.sleep(tick)
            now = time.monotonic()
            dt, last = now - last, now
            self.tick_worst = max(self.tick_worst, dt)
            drained = self.playback.tick(dt, now)
            if self.playback.started and not was_started:
                was_started = True
                if self.turn is not None and self.turn.first_audible is None:
                    self.turn.first_audible = self.playback.started_at
            if drained:
                was_started = False
                await self.send_event("playback_drained")
                if self.playback.pending_lead:
                    await self.send_event("request_lead",
                                          seconds=round(self.playback.pending_lead, 2))
                    self.playback.pending_lead = 0.0
                if self.turn is not None and self.turn.reply_done is None:
                    self.turn.reply_done = now
                    self.reply_finished.set()

    async def telemetry(self) -> None:
        while True:
            await asyncio.sleep(5)
            p = self.playback
            await self.send_event(
                "device_stats",
                buffered_ms=int(p.buffered * 1000),
                underruns=p.underruns,
                underrun_ms=int(p.underrun_ms),
                dropped_bytes=p.dropped_bytes,
                forced_ends=p.forced_ends,
                heap_free=175000, internal_free=175000, dma_largest=126976,
                prebuffer_ms=p.prebuffer_ms,
                aec_enabled=True, aec_frames=0, aec_vad_frames=0,
                aec_feed_failures=0, aec_max_process_us=23000, aec_rearms=0,
                battery_percent=88, on_usb=True, charging=False,
                rssi=self.args.rssi, wifi_drops=0,
                imu_posture="upright", motion_event="",
            )

    # -- the turn loop ------------------------------------------------------
    async def stream_silence(self, seconds: float) -> None:
        frames = int(seconds / FRAME_SECONDS)
        deadline = time.monotonic()
        quiet = np.random.normal(0, 12, MIC_FRAME)   # a real room is not digital zero
        for _ in range(frames):
            deadline += FRAME_SECONDS
            await self.send_mic(quiet, speech=False)
            sleep = deadline - time.monotonic()
            if sleep > 0:
                await asyncio.sleep(sleep)

    async def run_turn(self, index: int) -> Turn:
        turn = Turn(index)
        turn.underruns_before = self.playback.underruns
        self.turn = turn
        self.reply_finished.clear()

        if self.args.trigger == "hold":
            self.control_sent_at = time.monotonic()
            await self.send_event("push_to_talk")

        deadline = time.monotonic()
        total = len(self.stimulus)
        for start in range(0, total, MIC_FRAME):
            chunk = self.stimulus[start:start + MIC_FRAME]
            if len(chunk) < MIC_FRAME:
                chunk = np.pad(chunk, (0, MIC_FRAME - len(chunk)))
            deadline += FRAME_SECONDS
            await self.send_mic(chunk, speech=True)
            sleep = deadline - time.monotonic()
            if sleep > 0:
                await asyncio.sleep(sleep)
        # The instant the user stopped talking. Everything the human perceives
        # as "how long did she take" is measured from here.
        turn.speech_end = time.monotonic()
        # Anything logged before this instant belongs to the previous reply.
        self.arrivals = [(at, s) for at, s in self.arrivals if at >= turn.speech_end]
        self.stream_ends = [at for at in self.stream_ends if at >= turn.speech_end]
        if self.args.trigger == "hold":
            await self.send_event("commit_now")

        # The Stop test has to happen DURING her reply, not after it: pressing
        # Stop once she has finished tests nothing at all.
        stopper = (asyncio.create_task(self.press_stop())
                   if self.args.stop_after > 0 else None)
        # Keep the microphone running, exactly as the board does -- a device that
        # stops streaming while it waits is not the device under test.
        waiter = asyncio.create_task(self.reply_finished.wait())
        silence = asyncio.create_task(self.stream_silence(self.args.reply_timeout))
        wait_for = {waiter, silence}
        if stopper is not None:
            # Do not tear the turn down before the Stop measurement finishes.
            wait_for.add(stopper)
        done, _ = await asyncio.wait(wait_for,
                                     return_when=(asyncio.ALL_COMPLETED if stopper
                                                  else asyncio.FIRST_COMPLETED),
                                     timeout=self.args.reply_timeout + 2)
        for task in (waiter, silence):
            task.cancel()
        if stopper is not None and not stopper.done():
            stopper.cancel()
        await asyncio.gather(waiter, silence, *( [stopper] if stopper else [] ),
                             return_exceptions=True)
        turn.replay = replay(self.arrivals, self.stream_ends,
                             self.playback.prebuffer_ms, self.playback.ring_bytes)
        if turn.replay["started_at"]:
            turn.first_audible = turn.replay["started_at"]
        self.arrivals.clear()
        self.stream_ends.clear()
        self.turn = None
        return turn

    async def run_turns(self) -> None:
        # Resume where the last session left off. A reconnect must not restart
        # the count, or an outage run does twice the turns.
        for index in range(len(self.turns) + 1, self.args.turns + 1):
            await self.stream_silence(self.args.gap)
            # Three conditions, and the third is the one that was missing. The
            # gateway's `playing` flag is cleared only by `playback_drained`,
            # and it can be set again at any moment by speech this side did not
            # ask for -- a worker line, an idle-mind question, a tool follow-up.
            # While it is set, handle_audio discards every microphone frame, so
            # a turn started then is not endpointed until she finishes: it
            # showed up here as a 6-9 s "network" delay on one turn in six, with
            # the gateway's own audio arriving 0 ms after its first_pcm event.
            # A second of no inbound audio is the cheapest honest proxy for
            # "she has actually stopped".
            #
            # The real board never had this problem: talk_event_cb sends
            # cancel_turn instead of push_to_talk while she is busy.
            quiet_for = self.args.quiet_before_turn
            for _ in range(int(self.args.settle_timeout / 0.25)):
                if (not self.playback.active
                        and self.state in ("idle", "listening", "followup")
                        and time.monotonic() - self.last_audio_at > quiet_for):
                    break
                await asyncio.sleep(0.25)
            else:
                # Do not sit here for the rest of the run. A session that never
                # reports itself idle again is exactly what a board would answer
                # by reconnecting, so do that -- and say so, because it is a
                # finding either way.
                print(f"    (turn {index}: no quiet after "
                      f"{self.args.settle_timeout:.0f}s; state={self.state} "
                      f"playing={self.playback.active} -- reconnecting)",
                      flush=True)
                self.wedged += 1
                return
            self.arrivals.clear()
            self.stream_ends.clear()
            turn = await self.run_turn(index)
            self.turns.append(turn)
            self._print_turn(turn)

    async def press_stop(self) -> None:
        """Tap Stop while she is speaking, exactly as the panel does.

        `talk_event_cb` sends `cancel_turn` on PRESSED whenever kiki_is_busy(),
        so this is the same event the button produces -- and the question it
        answers is the one that cannot be answered by reading code: how much
        speech still arrives after the gateway has been told to stop.
        """
        # Wait for her to actually be speaking; stopping a turn that has not
        # started tests nothing.
        for _ in range(int(self.args.reply_timeout / 0.05)):
            if self.playback.active and self.state == "speaking":
                break
            await asyncio.sleep(0.05)
        else:
            print("    [stop] she never started speaking; nothing to stop", flush=True)
            return
        await asyncio.sleep(self.args.stop_after)

        buffered_before = self.playback.buffered
        self.bytes_after_cancel = 0
        self.cancel_sent_at = time.monotonic()
        await self.send_event("cancel_turn")
        print(f"    [stop] cancel_turn sent (state={self.state}, "
              f"{buffered_before:.2f}s already buffered on the board)", flush=True)

        # Watch for three seconds. A correct stop shows a state change within a
        # round trip and no further audio at all.
        state_at = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if state_at is None and self.state in ("listening", "idle", "followup"):
                state_at = time.monotonic()
            await asyncio.sleep(0.02)

        sent_after = self.bytes_after_cancel
        tail = ((self.last_audio_after_cancel - self.cancel_sent_at) * 1000
                if self.last_audio_after_cancel else 0.0)
        print(f"    [stop] state -> {self.state}"
              f" after {((state_at - self.cancel_sent_at) * 1000) if state_at else -1:.0f} ms"
              f" | audio sent AFTER cancel: {sent_after} B"
              f" ({sent_after / (2 * 16000) * 1000:.0f} ms of speech,"
              f" last arrived +{tail:.0f} ms)"
              f" | board buffer now {self.playback.buffered:.2f}s"
              f" | audio_stop events {self.audio_stops}", flush=True)
        self.cancel_sent_at = None

    async def session(self) -> None:
        self.sessions += 1
        async with websockets.connect(
            self.args.url, max_size=4 * 1024 * 1024, ping_interval=None,
            open_timeout=self.args.reply_timeout,
        ) as ws:
            self.ws = ws
            self.connected.clear()
            # A new session restarts the gateway's stream numbering, so the
            # watermarks have to restart with it or her speech is discarded.
            self.min_tts = self.last_tts = 0
            self.min_media = self.last_media = 0
            await self.send_hello()
            reader = asyncio.create_task(self.reader())
            tasks = [reader,
                     asyncio.create_task(self.speaker()),
                     asyncio.create_task(self.telemetry())]
            try:
                await asyncio.wait_for(self.connected.wait(), timeout=30)
                print(f"[dev] session {self.sessions} up (link={self.args.link}); "
                      f"waiting for idle", flush=True)
                for _ in range(int(self.args.warm_timeout / 0.25)):
                    if self.state in ("idle", "listening", "followup"):
                        break
                    await asyncio.sleep(0.25)
                print(f"[dev] state={self.state}; starting turns", flush=True)
                turns = asyncio.create_task(self.run_turns())
                # Race the turn loop against the reader. When the link is
                # severed the reader raises and dies -- and used to die
                # silently, because gather(return_exceptions=True) swallowed it
                # while the turn loop carried on talking to a socket that was
                # gone. An outage then looked like a slow turn instead of a
                # disconnect, which is the opposite of what this row measures.
                done, _ = await asyncio.wait({turns, reader},
                                             return_when=asyncio.FIRST_COMPLETED)
                if turns not in done:
                    turns.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await turns
                    print("[dev] link went away mid-run", flush=True)
                for task in done:
                    if task is reader and not task.cancelled() and task.exception():
                        raise task.exception()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.ws = None

    def _print_turn(self, t: Turn) -> None:
        def ms(a, b):
            if not (a and b):
                return "      -"
            value = (b - a) * 1000
            return f"{value:7.0f}" if value >= 0 else "  BOGUS"
        print(
            f"  turn {t.index:>2}: perceived {ms(t.speech_end, t.first_audible)} ms |"
            f" transport {ms(t.speech_end, t.first_byte)} ms |"
            f" gateway {str(t.gateway_ttfw_ms or '-'):>5} ms |"
            f" ctrl {('%.0f' % t.control_rtt_ms) if t.control_rtt_ms else '-':>5} ms |"
            f" evt->pcm {ms(t.first_pcm_event, t.first_byte)} ms |"
            f" gaps {t.replay.get('underruns', 0)}"
            f"/{t.replay.get('silence_ms', 0):.0f}ms"
            f" | prebuf {self.playback.prebuffer_ms} ms"
            f" | dropped {t.replay.get('dropped_bytes', 0)}B"
            f" | reply {t.reply_seconds:.1f}s",
            flush=True,
        )

    def report(self) -> None:
        elapsed = time.monotonic() - self.started_at
        print("\n" + "=" * 78)
        print(f"link={self.args.link}  url={self.args.url}  {elapsed:.0f}s  "
              f"sessions={self.sessions}  ws_drops={self.ws_drops}")

        def stats(name, values, unit="ms"):
            # A negative interval means the harness attributed the previous
            # reply's audio to this turn. Report it, never average it in.
            bogus = len([v for v in values if v < 0])
            values = [v for v in values if v >= 0]
            if bogus:
                print(f"  {name:<26} {bogus} unusable sample(s) discarded")
            if not values:
                print(f"  {name:<26} (none)")
                return
            values = sorted(values)
            p95 = values[min(len(values) - 1, int(len(values) * 0.95))]
            print(f"  {name:<26} p50 {statistics.median(values):7.0f} {unit}"
                  f"   p95 {p95:7.0f} {unit}   n={len(values)}")

        done = [t for t in self.turns if t.speech_end]
        stats("TTFW perceived", [(t.first_audible - t.speech_end) * 1000
                                 for t in done if t.first_audible])
        stats("TTFW transport", [(t.first_byte - t.speech_end) * 1000
                                 for t in done if t.first_byte])
        stats("TTFW gateway-reported", [t.gateway_ttfw_ms for t in done
                                        if t.gateway_ttfw_ms])
        # The part this project actually controls. Whisper and llama vary by
        # seconds run to run and that variance swamps the network on a good
        # link; subtracting the gateway's own number leaves the transport, the
        # jitter buffer and the start gate -- which is what a shaping change is
        # supposed to move.
        stats("TTFW network + buffer", [
            (t.first_audible - t.speech_end) * 1000 - t.gateway_ttfw_ms
            for t in done if t.first_audible and t.gateway_ttfw_ms
        ])
        stats("control round trip", [t.control_rtt_ms for t in done if t.control_rtt_ms])
        p = self.playback
        gaps = sum(t.replay.get("underruns", 0) for t in done)
        silence = sum(t.replay.get("silence_ms", 0.0) for t in done)
        dropped = sum(t.replay.get("dropped_bytes", 0) for t in done)
        audio = sum(t.replay.get("audio_seconds", 0.0) for t in done)
        print(f"  audible gaps               {gaps} ({silence:.0f} ms of silence"
              f" in {audio:.0f} s of speech = {silence/max(audio,1e-9)/10:.2f}%)")
        print(f"  audio dropped on arrival   {dropped} B"
              f" ({dropped/(PLAYBACK_RATE*2)*1000:.0f} ms of words lost)")
        print(f"  harness tick worst case    {self.tick_worst*1000:.0f} ms"
              " (metrics are replayed from arrival times, so this is FYI only)")
        print(f"  final start gate           {p.prebuffer_ms} ms "
              f"(floor {PREBUFFER_FLOOR_MS}) <- must stay at the floor"
              f"   send lead asked for {p.requested_lead:.2f}s")
        print(f"  frames discarded (stale)   {self.watermarked}"
              f"   audio_stop events {self.audio_stops}")
        print(f"  slow sends (>250 ms)       {len(self.stall_events)}"
              + (f"   worst {max(self.stall_events)*1000:.0f} ms" if self.stall_events else ""))
        print(f"  uplink                     {self.bytes_up*8/1000/elapsed:.0f} kbps"
              f"   downlink {self.bytes_down*8/1000/elapsed:.0f} kbps")
        answered = len([t for t in done if t.first_byte])
        print(f"  turns answered             {answered}/{len(self.turns)}")
        if self.wedged:
            print(f"  sessions abandoned         {self.wedged}"
                  " (never reported idle again -- see the gateway log)")
        print("=" * 78, flush=True)


async def run(args) -> None:
    if args.wav:
        stimulus = load_wav(args.wav)
    else:
        cache = Path(args.cache_dir) / (
            "".join(c if c.isalnum() else "_" for c in args.say)[:60] + ".wav")
        print(f"[dev] stimulus: {cache}", flush=True)
        stimulus = synthesize(args.say, args.tts_url, cache)
    print(f"[dev] stimulus {len(stimulus)/MIC_RATE:.2f}s at {MIC_RATE} Hz", flush=True)

    device = FakeDevice(args, stimulus)
    try:
        while len(device.turns) < args.turns:
            try:
                await device.session()
            except Exception as exc:
                device.ws_drops += 1
                print(f"[dev] session ended: {type(exc).__name__}: {exc}", flush=True)
                if len(device.turns) >= args.turns:
                    break
                await asyncio.sleep(args.reconnect_delay)
    except KeyboardInterrupt:
        pass
    finally:
        device.report()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="ws://127.0.0.1:8765")
    p.add_argument("--token", default="")
    p.add_argument("--link", default="cloud", choices=["lan", "cloud", "backup"],
                   help="what the board would report; cloud/backup select narrowband")
    p.add_argument("--turns", type=int, default=10)
    p.add_argument("--gap", type=float, default=2.0, help="silence before each turn")
    p.add_argument("--say", default="kiki, what is the capital of france")
    p.add_argument("--wav", default="", help="use this 16-bit wav instead of --say")
    p.add_argument("--tts-url", default="http://127.0.0.1:8082/v1/audio/speech")
    p.add_argument("--cache-dir", default="/tmp/kiki_stimulus")
    p.add_argument("--trigger", default="hold", choices=["hold", "hotword"],
                   help="hold = deterministic push_to_talk/commit_now; "
                        "hotword = let the VAD and transcript decide")
    p.add_argument("--reply-timeout", type=float, default=45.0)
    p.add_argument("--quiet-before-turn", type=float, default=1.0,
                   help="inbound audio must have stopped for this long first")
    p.add_argument("--settle-timeout", type=float, default=75.0,
                   help="max wait for the previous reply to finish playing")
    p.add_argument("--warm-timeout", type=float, default=150.0)
    p.add_argument("--reconnect-delay", type=float, default=1.0)
    p.add_argument("--rssi", type=int, default=-55)
    p.add_argument("--legacy-buffer", action="store_true",
                   help="model the OLD firmware jitter buffer (128 KiB ring, "
                        "start gate that ratchets 100->340 ms) for before/after runs")
    p.add_argument("--stop-after", type=float, default=0.0,
                   help="seconds into her reply to tap Stop (0 = never). "
                        "Reports how much speech the gateway sends afterwards.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
