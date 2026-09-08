from __future__ import annotations

import asyncio
from collections import deque
import ipaddress
import contextlib
import json
import logging
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import urlparse

import numpy as np

from .audio import Decimator48To16, RNNoise48k
from .barge_in import BargeInDecision, BargeInDetector
from .config import GatewayConfig
from .config_store import ConfigStore
from . import dance
from .device_tools import DeviceToolBridge
from .display_bridge import DisplayBridge, panel_text
from .endpointer import ActionKind, StreamingEndpointer
from .expressions import last_expression_tag
from .hotword_text import HotwordMatcher, tokenize
from .inference import DirectLlamaCore, TTSClient, WhisperClient, get_shared_core
from .motion_reactions import motion_question_cooldown
from .panel_status import environment_text, panel_details, recent_whatsapp
from .protocol import AudioFlag, AudioFrame, BinaryKind, decode_event, encode_event
from .opus_codec import OpusUnavailable, TtsOpusEncoder
from .silero import SileroVAD
from .wakeword import WakeWordDetector


LOG = logging.getLogger(__name__)
SILENT_DISPLAY_TAGS = re.compile(r"<(?:neck|oled):[^>]+>", re.IGNORECASE)
# Verbatim speech is chunked on sentence ends purely so playback can start
# before the whole paragraph is synthesised. The delimiter is kept with the
# sentence, so joining the parts reproduces the input exactly.
_VERBATIM_SENTENCES = re.compile(r"(?<=[.!?\u0964])\s+")
# whisper.cpp's own narration of what it heard instead of words.
ASR_ANNOTATION = re.compile(r"[\(\[\*][^\)\]\*]{0,40}[\)\]\*]")
FALL_HELP = re.compile(
    r"\b(?:help|not\s+okay|not\s+ok|hurt|injured|i\s+fell|call\s+(?:them|family))\b|"
    r"(?:मदद|चोट|गिर\s*गया|गिर\s*गयी)", re.IGNORECASE)
FALL_OK = re.compile(
    r"\b(?:i(?:'m|\s+am)?\s+(?:okay|ok|fine|safe)|all\s+good|cancel(?:\s+it)?)\b|"
    r"(?:मैं\s+ठीक|सब\s+ठीक|ठीक\s+हूँ|हाँ\s+ठीक)", re.IGNORECASE)


# Handed to the care agent when a hold has just finished and the routine is
# continuing without an answer. Mirrors `care.runtime.NO_REPLY`; defined here so
# the session can recognise its own synthetic turn and keep it out of history.
CARE_NO_REPLY = "[NO REPLY - CONTINUE THE ROUTINE YOURSELF]"

# The care agent returns one finished spoken paragraph rather than a stream, so
# it is split here for the same reason the streaming path emits sentences: TTS
# starts on the first one instead of the whole reply.
_CARE_SENTENCE_RE = re.compile(r"[^.!?।\n]+[.!?।]*\s*")


def _care_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _CARE_SENTENCE_RE.findall(str(text or ""))]
    return [part for part in parts if part] or (
        [str(text).strip()] if str(text or "").strip() else [])


def _acceptable_ota_url(url: str) -> bool:
    """Which firmware URLs the board may be pointed at.

    HTTPS anywhere, and plain HTTP **only** to a literal private address.

    A firmware image fetched over plaintext from the open internet is an image
    anyone on the path can replace, so that stays refused. The LAN exception
    exists because the HTTPS path needs a public hostname and on this network
    freshly-allocated `*.trycloudflare.com` names do not resolve at all -- both
    the OTA tunnel and the gateway's own tunnel are unreachable by name -- which
    left USB as the only way to update a board sitting on the same desk.

    A literal IP is required rather than a hostname: "it resolved to something
    private when I checked" is not a property that holds for the board a second
    later, and the board is the one doing the fetching.
    """
    url = str(url or "").strip()
    if url.startswith("https://"):
        return True
    if not url.startswith("http://"):
        return False
    try:
        host = urlparse(url).hostname or ""
        return ipaddress.ip_address(host).is_private
    except ValueError:
        # Not a literal address -- a hostname, or nothing at all.
        return False


class DeviceSession:
    panel_status_task = None
    # Class-level defaults, not documentation: several narrow test fixtures --
    # and the error paths that run before setup finishes -- build a session
    # without __init__, and do-not-disturb must read as "off" there rather
    # than raising out of a status update.
    posture: str | None = None
    do_not_disturb: bool = False
    # Dance mode. Declared at class level for the same reason as the two above:
    # several code paths ask "is she dancing?" and a fixture-built session must
    # answer "no" rather than raise.
    dance_active: bool = False
    # How far ahead of real time the board may be buffered. Same reason again:
    # _tts_worker reads these on every chunk, and a fixture-built session must
    # get the conservative default rather than an AttributeError mid-reply.
    playback_lead: float = GatewayConfig.playback_lead_seconds
    media_lead: float = GatewayConfig.media_lead_seconds
    # Same reason: request_lead reads this and fixture-built sessions never run
    # __init__.
    downlink_saturated: bool = False
    # How long a background prefix re-verify may run before the panel admits it
    # is warming.
    #
    # Deliberately far above "slow": a plain disconnect leaves the prefix
    # resident, so nothing needs rebuilding and the panel must stay quiet --
    # that is the whole point of the runtime surviving the socket. But the local
    # box has ONE slot, so even a no-op rewarm can sit behind idle-mind or
    # worker output for several seconds. Timing alone cannot tell "queued but
    # warm" from "actually rebuilding" at one or two seconds.
    #
    # A cold prefill is ~45 s. Eight seconds is past anything a queue explains
    # and far short of a real rebuild, so the notice appears only when the first
    # question would otherwise have paid for it invisibly.
    WARM_NOTICE_AFTER_SECONDS: float = 8.0
    # How long a turn gets to notice it was asked to stop before it is
    # cancelled outright. Long enough for the normal unwind -- which is what
    # runs the teardown -- short enough that Stop still feels immediate, and the
    # audio has already been silenced by then in any case.
    STOP_GRACE_SECONDS: float = 2.0
    # And the same again for the cancel_turn log line, which reports how much of
    # the reply had already gone out when the Stop button was pressed.
    tts_stream_id: int = 0
    tts_sequence: int = 0
    # Speech codec, decided from the device's hello. See the hello handler.
    opus_out: bool = False
    _opus: "TtsOpusEncoder | None" = None

    def __init__(self, websocket, config, core=None):
        self.ws = websocket
        self.config = config
        self.session_id = uuid.uuid4().hex
        self.whisper = WhisperClient(config.whisper_url)
        self.tts = TTSClient(config.tts_url, config.tts_output_rate, config.tts_gain)
        self.speaker_volume = config.speaker_volume
        self.device = DeviceToolBridge(self, config.legacy_root) if config.legacy_root else None
        self.display: DisplayBridge | None = None
        self.config_store = ConfigStore(config.legacy_root) if config.legacy_root else None
        self.loop = asyncio.get_running_loop()
        # One runtime per process, not per connection. See get_shared_core:
        # the socket is per-connection state, the conversation is not.
        self.core = core or (
            get_shared_core(config)
            if config.legacy_root
            else DirectLlamaCore(config.llama_url)
        )
        self.rnnoise = RNNoise48k()
        self.decimator = Decimator48To16()
        # A second decimator, for Kiki's voice on the way out. Separate from the
        # microphone's because both are stateful filters and interleaving them
        # would smear each into the other.
        self.tts_decimator = Decimator48To16()
        self._tts_tail = np.zeros(0, dtype=np.float32)
        # Set from the board's hello. It knows which URI it reached us on, and
        # that is the only thing that reliably distinguishes "same house" from
        # "across the internet" -- the peer address does not, because tunnelled
        # traffic and a local test client both arrive from 127.0.0.1.
        self.narrowband_out = False
        # Set from the previous reply's downlink accounting: True once a reply
        # took longer to send than it takes to play. See _tts_worker.
        self.downlink_saturated = False
        # How far ahead of real time the board is allowed to be buffered. Set
        # from the link in hello, and raised on request when a reply stutters.
        self.playback_lead = config.playback_lead_seconds
        self.media_lead = config.media_lead_seconds
        self.silero = SileroVAD(config.model_path)
        self.endpointer = StreamingEndpointer(self.silero, config)
        # Barge-in must not share Silero's recurrent state with ordinary
        # endpointing. During playback it sees AEC output while the endpointer
        # is intentionally idle; sharing would contaminate the next turn.
        self.barge_in = BargeInDetector(
            SileroVAD(config.model_path),
            probability_threshold=config.barge_in_vad_threshold,
            min_dbfs=config.barge_in_min_dbfs,
            noise_margin_db=config.barge_in_noise_margin_db,
            window_frames=config.barge_in_window_frames,
            min_votes=config.barge_in_min_votes,
            min_consecutive=config.barge_in_min_consecutive,
        )
        # The board owns the user-facing switch and sends its persisted value
        # after every connection. Keep a session-local fallback so an older
        # firmware still follows the gateway default.
        self.barge_in_enabled = config.barge_in_enabled
        # The acoustic detector is now the fallback, not the wake path: Kiki is
        # woken by her name in Whisper's text (see _finalize_turn). Loading the
        # model at all is opt-in, because openWakeWord costs memory and a
        # per-frame inference for a job the transcript already does.
        self.wakeword = None
        if getattr(config, "wakeword_enabled", False):
            model_dir = Path(config.model_path).resolve().parent
            self.wakeword = WakeWordDetector(model_dir / "kiki.onnx")
        # Rebuilt whenever the active mode changes the answer.
        self._matcher: HotwordMatcher | None = None
        self._matcher_words: tuple[str, ...] = ()
        # Recent idle speech, kept only long enough to be carried into a query
        # whose hotword arrived in a later utterance.
        self.recent_ambient: deque[tuple[float, str]] = deque(maxlen=8)
        # The generation woken on a speculative transcript, so a hotword the
        # final transcript does not confirm can be taken back.
        self._early_wake_generation: int | None = None
        # Turning Kiki face down is the do-not-disturb switch (§5.4).
        self.posture: str | None = None
        self.do_not_disturb = False
        self.pcm16 = np.zeros(0, dtype=np.float32)
        self.pcm48 = np.zeros(0, dtype=np.int16)
        self.last_mic_sequence: int | None = None
        self.awake = False
        # True only between a physical Hold-to-talk press and its release. A
        # quick tap converts this to ordinary open listening; a real hold keeps
        # the VAD from committing early during a pause.
        self.push_to_talk_active = False
        self.followup_deadline = 0.0
        # Set only by the dashboard Ask action. Ordinary replies still return
        # directly to idle; this grants exactly one explicit spoken follow-up.
        self.web_followup_pending = False
        self.playing = False
        # Zero while generating/thinking. Voice barge-in is armed only once
        # TTS PCM really starts, never merely because `playing` is true.
        self.speech_playback_started_at = 0.0
        self.turn_abort = self._new_turn_abort()
        self.spec_tasks: dict[int, asyncio.Task] = {}
        self.spec_prefill_tasks: dict[int, asyncio.Task] = {}
        self.partial_tasks: set[asyncio.Task] = set()
        self.partial_busy = False
        self.last_asr_ms = 0.0
        self.turn_modes: dict[int, bool] = {}
        self.turn_tasks: set[asyncio.Task] = set()
        # The subset of turn_tasks that are answering a question rather than
        # transcribing the room. See _cancel_turn_tasks.
        self.query_turns: set[asyncio.Task] = set()
        self.turn_lock = asyncio.Lock()
        self.tts_sequence = 0
        self.tts_stream_id = 0
        self.background_start_task: asyncio.Task | None = None
        # Set once hello succeeds, but declared here so close() can cancel it
        # even for a session that was rejected before it ever started.
        self.ota_watch_task: asyncio.Task | None = None
        self.mic_frames_received = 0
        self.mic_samples_received = 0
        self.mic_stats_started = time.monotonic()
        self.mic_stats_last_log = self.mic_stats_started
        self._reported_underruns = 0
        self._logged_first_stats = False
        self._battery_source: str | None = None
        self._battery_bucket: int | None = None
        self.speech_display_tasks: set[asyncio.Task] = set()
        self.motion_question_last: dict[str, float] = {}
        self.motion_question_last_any = 0.0
        self.motion_question_task: asyncio.Task | None = None
        self.fall_check_pending = False
        # Confirmed alerts may be replayed from the watch's durable queue after
        # a reconnect. Keep announcement ids on the process-owned core so that
        # a new websocket session does not speak the same emergency twice.
        if not hasattr(self.core, "_announced_fall_ids"):
            self.core._announced_fall_ids = set()
        self.health_alert_tasks: set[asyncio.Task] = set()
        # Health HTTP requests must never hold up the single websocket reader.
        # Bound and deduplicate retries; the board retains unacknowledged data.
        self.health_queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        self.health_batch_ids: set[str] = set()
        self.health_task: asyncio.Task | None = None
        # Dance mode: the whole panel becomes a stage and the *board* owns the
        # animation clock, driven by the media samples it has actually played.
        # This side only starts it, stops it, and refuses to start anything
        # else while it runs. See dance.py.
        self.dance_active = False
        self.dance_started_at = 0.0
        self.dance_title = ""

    async def send_event(self, event_type: str, **fields) -> None:
        if event_type == "audio_stop":
            # A stop must name what it invalidates. Deriving the cutoff from
            # the last frame the board happened to parse leaves a race where a
            # delayed websocket frame can arrive after the stop and be treated
            # as current audio. Explicit watermarks make cancellation correct
            # even when control and PCM sends were concurrent.
            fields.setdefault("tts_stream_id", int(getattr(self, "tts_stream_id", 0)))
            device = getattr(self, "device", None)
            fields.setdefault("media_stream_id", int(getattr(device, "media_stream_id", 0)))
        await self.ws.send(encode_event(event_type, session_id=self.session_id, **fields))

    def _device_media_active(self) -> bool:
        """Called from the legacy stream thread; a plain attribute read."""
        return bool(self.device and self.device.media_active)

    def _execute_device_tool_sync(self, name: str, arguments: dict) -> str | None:
        if not self.device or name not in self.device.HANDLED:
            return None
        future = asyncio.run_coroutine_threadsafe(self.device.execute(name, arguments), self.loop)
        return future.result(timeout=30)

    def enqueue_web_query(self, text: str) -> dict:
        """Accept a typed dashboard turn without blocking Flask's thread."""
        query = " ".join(str(text or "").split()).strip()
        if not query:
            return {"accepted": False, "error": "Enter an instruction."}
        if self.do_not_disturb:
            return {"accepted": False, "error": "Kiki is in Do Not Disturb."}
        if self.dance_active:
            return {"accepted": False, "error": "Stop the dance before asking."}

        def start() -> None:
            task = asyncio.create_task(self._handle_web_query(query[:1000]))
            self.turn_tasks.add(task)
            self.query_turns.add(task)
            task.add_done_callback(self.turn_tasks.discard)
            task.add_done_callback(self.query_turns.discard)

        self.loop.call_soon_threadsafe(start)
        return {"accepted": True, "instruction": query[:1000]}

    async def _handle_web_query(self, text: str) -> None:
        """Speak typed text verbatim.

        This used to hand the text to the model as a one-turn instruction and
        let it phrase the result. That is a nice idea and it failed constantly:
        measured on 2026-09-07, four consecutive attempts ("Ask vaibhav if he is
        tired or not", "Hi", and two others) each reached Gemini and each logged
        `proactive question stayed silent` about two seconds later. The model
        returned nothing speakable, most likely because the context was at
        7,784 of 8,192 tokens against a 1,200-token reply reserve -- so the box
        appeared to work, said nothing, and gave no reason.

        A typed instruction is the one case where there is nothing for a model
        to decide. The operator has already written the words. Speaking them
        directly removes the model, the cloud round trip, the context pressure
        and the possibility of a refusal or a paraphrase from the path, which is
        why this cannot "stay silent" any more: if TTS produced audio, she said
        exactly what was typed.
        """
        self.awake = False
        self.followup_deadline = 0.0
        self.web_followup_pending = False
        if self.playing or self.turn_lock.locked():
            await self.cancel_turn("webui_instruction")
        if self.do_not_disturb or self.dance_active:
            # Deliberate, and now said out loud. Silently dropping the request
            # is indistinguishable from the failure above.
            LOG.info("WebUI speech refused: dnd=%s dancing=%s", self.do_not_disturb,
                     self.dance_active)
            return
        self._forget_ambient()
        LOG.info("WebUI speak session=%s text=%r", self.session_id, text[:160])
        await self.speak_verbatim(text)

    async def speak_verbatim(self, text: str) -> bool:
        """Say `text` exactly as written. No model call anywhere in this path."""
        # Display tags are markup, not speech: a typed `<oled:happy>` should move
        # her face, not be read out as an angle bracket. Nothing else is touched,
        # so the spoken words are the typed words.
        expression = last_expression_tag(text)
        spoken_text = SILENT_DISPLAY_TAGS.sub("", text).strip()
        if not spoken_text:
            LOG.info("WebUI speech was empty after stripping display tags")
            return False

        async with self.turn_lock:
            self.turn_abort = self._new_turn_abort()
            self.playing = True
            self.awake = True
            self.web_followup_pending = True
            self.tts_stream_id += 1
            self.tts_sequence = 0
            await self.set_state(state="speaking", detail=spoken_text[:80])

            sentence_queue: asyncio.Queue[
                str | tuple[str, str | None] | None
            ] = asyncio.Queue()
            tts_task = asyncio.create_task(
                self._tts_worker(sentence_queue, time.perf_counter()))
            # Split only so the TTS engine gets natural units and the first
            # words start sooner; splitting never changes a character.
            parts = [part for part in _VERBATIM_SENTENCES.split(spoken_text) if part.strip()]
            if not parts:
                parts = [spoken_text]
            try:
                for index, part in enumerate(parts):
                    sentence = part.strip()
                    await self.send_event("response_sentence", text=sentence)
                    # The expression rides on the last part, so a tag typed
                    # anywhere in the box lands with the end of the speech.
                    await sentence_queue.put(
                        (sentence, expression if index == len(parts) - 1 else None))
            finally:
                await sentence_queue.put(None)
                sent_audio = await tts_task

            if not sent_audio:
                self.playing = False
                self.speech_playback_started_at = 0.0
                self._reset_barge_in()
                self.awake = False
                self.web_followup_pending = False
                self.followup_deadline = 0.0
                await self.set_state(state="idle")
                LOG.warning("WebUI speech produced no audio: %r", spoken_text[:120])
                return False

            # She said it, so she has to know she said it: without this the next
            # turn has no idea where the words came from and can contradict them.
            try:
                self.core.history.append({"role": "assistant", "content": spoken_text})
                await asyncio.to_thread(self.core.register_history, self.core.history,
                                        after_speaking=True)
            except Exception:
                LOG.exception("could not record the spoken line in history")

            self.followup_deadline = time.monotonic() + 15.0
            LOG.info("WebUI spoke verbatim: %r", spoken_text[:200])
            return True

    async def run(self) -> None:
        try:
            first = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
        except asyncio.TimeoutError:
            await self.ws.close(code=1008, reason="hello timeout")
            return
        if not isinstance(first, str):
            await self.ws.close(code=1008, reason="hello required")
            return
        hello = decode_event(first)
        if hello.get("type") != "hello":
            await self.ws.close(code=1008, reason="hello required")
            return
        if self.config.auth_token and hello.get("token") != self.config.auth_token:
            await self.ws.close(code=1008, reason="unauthorized")
            return
        # Log which image is talking to us. Answering "did the flash actually
        # take?" used to need a serial cable and a reboot, which is exactly the
        # moment you cannot get one -- the board is across the house, or across
        # the country, and the only thing you can see is that it reconnected.
        # An allowlist, not "anything that is not lan": firmware older than the
        # link field sends nothing, and defaulting that to remote would push
        # 16 kHz audio at a board expecting 48 kHz. "remote" is the name the
        # first version of this sent; "cloud"/"backup" name the actual slot.
        # Health mode's board-facing helpers follow the same rule as the
        # runtime's: the board comes and goes, the care stack does not.
        care = self._care_runtime()
        if care is not None:
            try:
                care.attach_session(self)
            except Exception:
                LOG.exception("could not attach the care stack to this session")
        attach = getattr(self.core, "attach", None)
        if attach:
            # After the token check, deliberately: attaching points Kiki's
            # workers, idle mind and device tools at this socket.
            attach(self)
        link = str(hello.get("link", "lan")).lower()
        self.narrowband_out = link in ("remote", "cloud", "backup")
        # Negotiated, never assumed. Firmware older than the codec list sends
        # nothing here and keeps getting PCM, which is why this is an opt-in
        # allowlist rather than "anything remote gets Opus".
        codecs = hello.get("codecs")
        wants_opus = isinstance(codecs, list) and "opus" in [
            str(name).lower() for name in codecs
        ]
        self.opus_out = False
        if self.narrowband_out and wants_opus and self.config.opus_enabled:
            try:
                self._opus = TtsOpusEncoder(self.config.opus_bitrate)
                self.opus_out = True
                LOG.info(
                    "speech codec: opus %d kbps (16 kHz PCM would have been 256)",
                    self.config.opus_bitrate // 1000,
                )
            except OpusUnavailable as exc:
                # Never fatal: PCM still works, it just needs a better link.
                LOG.warning("opus unavailable, staying on PCM (%s)", exc)
        elif self.narrowband_out:
            LOG.info(
                "speech codec: 16 kHz PCM (%s)",
                "opus disabled in config" if wants_opus else "device did not offer opus",
            )
        if self.narrowband_out:
            self.playback_lead = self.config.playback_lead_seconds_remote
            self.media_lead = self.config.media_lead_seconds_remote
        LOG.info(
            "device firmware build %s (%s) link=%s",
            hello.get("firmware", "unknown"),
            hello.get("device", "unknown"),
            hello.get("link", "unknown"),
        )
        # The board's black box. A disconnect kills the log that would have
        # explained it, so the board carries the evidence across and reports it
        # on the way back in: how strong the signal is, how many times Wi-Fi
        # itself dropped (and why -- reason 8 is the AP disassociating you,
        # 2/4/15 are auth/timeout), and how many websocket sessions it has
        # lost. A board reconnecting every few seconds looks identical from
        # this side whether the cause is a weak AP, a saturated uplink or a
        # tunnel having a bad day; these four numbers separate them.
        if "rssi" in hello or "ws_drops" in hello:
            try:
                rssi = int(hello.get("rssi", 0))
            except (TypeError, ValueError):
                rssi = 0
            LOG.info(
                "device link health: ssid %s, rssi %s dBm (%s), wifi drops %s "
                "(last reason %s), websocket drops %s",
                # Which network it actually joined. A board that quietly took
                # the built-in fallback reads identically from here otherwise,
                # and that difference is the whole experiment when the point is
                # to be on the weak AP.
                hello.get("ssid") or "?",
                rssi,
                "weak" if rssi and rssi <= -70 else "ok" if rssi else "unknown",
                hello.get("wifi_drops"), hello.get("wifi_reason"),
                hello.get("ws_drops"),
            )
        await self.send_event(
            "hello_ack",
            fallback_uri=self._public_uri(),
            sample_rate_in=48000,
            sample_rate_out=48000,
            frame_samples=480,
            endpoint_ms=self.config.endpoint_silence_ms,
            speculative_ms=self.config.speculative_silence_ms,
        )
        await self._push_pending_ota()
        # ...and keep looking. Handing the URL over only at hello meant a queued
        # update sat untouched for as long as the board held its connection --
        # which, now that the link is stable, can be hours. An update you have
        # already asked for should not be waiting on a disconnection.
        self.ota_watch_task = asyncio.create_task(self._watch_for_pending_ota())
        await self.send_event("volume", percent=self.speaker_volume)
        await self.send_event("gain", value=round(self.tts.gain, 2))
        # State the board cannot know on its own. It keeps whatever the last
        # session left on screen, so without this a transport row from a song
        # that finished long ago stays up over an idle Kiki -- and the board
        # reconnects by itself, so "last session" is often minutes ago.
        if self.device:
            await self.device.notify_media()
        if self.config_store:
            # The Pi asks these before any service starts; here the answers live
            # on the laptop, so the board can only be offered them once it is
            # talking to us.
            await self.send_event(
                "config_options",
                options=self.config_store.options(
                    self.speaker_volume, self.tts.gain, self.config.display_sync_offset_ms
                ),
            )
        # Attach before warming so the board shows the legacy runtime's own
        # "Warming up model" rows rather than a bare state name.
        if self.config.legacy_root:
            self.display = DisplayBridge(self)
            self.display.attach()
        warmer = getattr(self.core, "ensure_ready", None)
        if warmer and getattr(self.core, "warmed", False):
            # The runtime survived the reconnect, so the speaking prefix is
            # already resident and rewarm() is hash-deduped to a no-op. Verify
            # it off the critical path and let the board go straight to idle --
            # "Warming up model" after every dropped link was the single most
            # visible symptom of rebuilding Kiki per connection.
            asyncio.create_task(self._reverify_warm(warmer))
        elif warmer:
            await self.set_state(state="warming")
            try:
                # Bounded, and never fatal. `warming` is the one state the board
                # cannot leave on its own -- it is waiting to be told -- so any
                # path that fails to reach `idle` strands the panel on "Warming
                # up model" until someone power-cycles it. A cold prefill is
                # ~45 s, so this only trips on something genuinely wrong.
                await asyncio.wait_for(warmer(), timeout=self.config.warm_timeout_seconds)
            except asyncio.TimeoutError:
                LOG.warning(
                    "prefix warm did not finish in %.0fs; going idle anyway. The "
                    "first turn will pay the prefill.",
                    self.config.warm_timeout_seconds,
                )
            except Exception:
                LOG.exception("prefix warm failed; going idle anyway")
            # Set even after a timeout: the first turn pays the prefill either
            # way, and making every later reconnect wait again helps nobody.
            with contextlib.suppress(Exception):
                self.core.warmed = True
        await self.set_state(state="idle")
        self.panel_status_task = asyncio.create_task(self._panel_status_loop())
        if self.config.startup_speech:
            asyncio.create_task(self.speak_background(self.config.startup_speech))
        if self.config.autoplay_query and self.device:
            await self.device.execute("adjust_volume", {"action": "set", "amount": 100})
            self.device.repeat_queue = self.config.autoplay_repeat
            result = await self.device.execute(
                "play_music", {"song": self.config.autoplay_query}
            )
            LOG.info("autoplay: %s", result)
            if self.config.autoplay_seconds > 0:
                async def stop_autoplay() -> None:
                    await asyncio.sleep(self.config.autoplay_seconds)
                    await self.device.execute("control_music", {"action": "stop"})

                asyncio.create_task(stop_autoplay())
        starter = getattr(self.core, "start_background", None)
        if starter and getattr(self.core, "_start_task", None) is None:
            async def start_when_boot_is_idle() -> None:
                await asyncio.sleep(30)
                await starter(
                    self.speak_background,
                    self.config.webui_port,
                    self.ask_proactive_question,
                )

            # Owned by the core, not by this session: a board that drops inside
            # the settle window used to restart the countdown every time, so on
            # a bad link the workers and the idle mind never started at all.
            task = asyncio.create_task(start_when_boot_is_idle())
            with contextlib.suppress(Exception):
                self.core._start_task = task
        async for message in self.ws:
            try:
                if isinstance(message, str):
                    await self.handle_control(decode_event(message))
                else:
                    await self.handle_audio(AudioFrame.decode(message))
            except Exception as exc:
                LOG.exception("session message failed")
                await self.send_event("error", code="bad_message", message=str(exc))

    async def handle_control(self, event: dict) -> None:
        kind = event["type"]
        if kind == "hello":
            return
        elif kind in ("wake", "push_to_talk"):
            # Logged because "hold to talk does nothing" has three completely
            # different causes -- the press never arriving, the press arriving
            # while `playing` is stuck so the audio is discarded unheard, or the
            # audio arriving but never committing -- and they are uniformly
            # invisible. `playing` is the one worth printing: while it is set,
            # handle_audio drops every frame on the floor.
            LOG.info("%s (awake=%s playing=%s)", kind, self.awake, self.playing)
            self.push_to_talk_active = kind == "push_to_talk"
            await self.activate_query(source=kind)
        elif kind == "request_lead":
            # The board stuttered and is asking to be buffered further ahead.
            # It asks instead of deepening its own prebuffer because its
            # prebuffer is time-to-first-word and this is not: pacing only ever
            # delays later frames, so a deeper lead is free to the listener.
            try:
                want = float(event.get("seconds", 0.0))
            except (TypeError, ValueError):
                want = 0.0
            ceiling = self.config.playback_lead_seconds_max
            # A deeper cushion only helps when the stutter was jitter. If the
            # last reply could not be pushed out faster than it plays, the link
            # itself is the bottleneck and more lead is actively harmful: the
            # extra audio does not reach the speaker any sooner, it just sits in
            # the TCP send buffer ahead of everything else on the socket --
            # including the keepalive PING, which is then answered too late and
            # takes the whole session down. That is the loop this device was
            # stuck in: stutter, ask for more lead, get it, stall harder.
            if self.downlink_saturated:
                LOG.info(
                    "device asked for %.1fs of cushion; refused, the link is "
                    "already saturated (more lead would only deepen the stall)",
                    want,
                )
                return
            new_lead = max(self.playback_lead, min(want, ceiling))
            if new_lead > self.playback_lead:
                LOG.info(
                    "device asked for more cushion: send lead %.1fs -> %.1fs",
                    self.playback_lead, new_lead,
                )
                self.playback_lead = new_lead
                self.media_lead = max(self.media_lead, new_lead)
        elif kind == "listen_open":
            # A quick button tap began provisionally as push-to-talk so the
            # first syllable could never be clipped. Release turns it into the
            # same open, silence-ended listening window as a hotword trigger.
            self.push_to_talk_active = False
            LOG.info("short talk tap -> open listening")
        elif kind in ("cancel_turn", "mute"):
            # Logged for the same reason push_to_talk and commit_now are: "Stop
            # does nothing" has several causes that are invisible from either
            # end -- the press never arriving, the press arriving and the
            # gateway ignoring it, or the gateway stopping and the board playing
            # on out of its own buffer. Only the first is visible here, and its
            # absence is the finding.
            LOG.info(
                "%s from device (playing=%s awake=%s tts_stream=%s sent=%s frames)",
                kind, self.playing, self.awake, self.tts_stream_id, self.tts_sequence,
            )
            await self.cancel_turn(kind)
        elif kind == "set_barge_in":
            enabled = event.get("enabled", True)
            if isinstance(enabled, str):
                enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
            self.barge_in_enabled = bool(enabled)
            self._reset_barge_in()
            LOG.info("voice barge-in %s from device settings",
                     "enabled" if self.barge_in_enabled else "disabled")
        elif kind == "dance_stop":
            # The board stopped dancing on its own -- a tap on the stage, or
            # being turned upside down. It has already silenced itself and put
            # the face back; this tears down the song behind it. `notify_device`
            # is False because telling it what it just told us would be a loop.
            LOG.info("device ended the dance (%s)", str(event.get("reason", ""))[:32])
            await self.end_dance(str(event.get("reason", "device"))[:32],
                                 notify_device=False)
        elif kind == "device_log":
            # The board's own ESP_LOG, mirrored over the socket so it can be
            # debugged without a USB cable -- which is what today cost hours.
            LOG.info("[device] %s", str(event.get("line", ""))[:300])
        elif kind == "motion_event":
            await self._handle_motion_event(event)
        elif kind == "care_action":
            await self._handle_care_action(event)
        elif kind == "care_options":
            runtime = self._care_runtime()
            if runtime is not None:
                runtime.note_care_options(event)
        elif kind in {"imu_window", "measurement_status"}:
            # Health-mode sensor events. Silently ignored in every other mode:
            # a board on newer firmware must be able to talk to a gateway that
            # is not in health mode without anything going wrong.
            self._handle_care_device_event(kind, event)
        elif kind in {"health_telemetry", "health_fall"}:
            self._queue_health_telemetry(event)
        elif kind == "firmware_update":
            # Relayed from the Web UI or a console; the device does the work.
            LOG.warning("sending firmware update to the device: %s", event.get("url"))
            await self.send_event("firmware_update", url=str(event.get("url", "")))
        elif kind == "shutdown":
            # The panel's Shut down item. Worth its own line: every other way
            # the socket dies looks identical in the log, and hunting phantom
            # reconnects is exactly the debugging this avoids repeating.
            LOG.info("device is powering off deliberately (session=%s)", self.session_id)
        elif kind == "media_control":
            # The panel's transport row. Routed through the same tool the voice
            # commands use, so "next song" and the button cannot drift apart.
            if self.device:
                action = str(event.get("action", "")).strip().lower()
                result = await self.device.execute("control_music", {"action": action})
                LOG.info("media_control %s -> %s", action, result)
                await self.device.notify_media()
        elif kind == "sleep":
            # Holding Stop. A tap cancels and leaves the follow-up window open
            # so you can just keep talking; a hold means "and stop listening" --
            # back to the wake word. Sent after the tap's own cancel_turn, so
            # this only has to close the window.
            self.awake = False
            self.push_to_talk_active = False
            self.followup_deadline = 0.0
            self.endpointer.reset(active=False)
            await self.set_state(state="idle")
        elif kind == "commit_now":
            # The user released a hold-to-talk press on the panel.
            self.push_to_talk_active = False
            actions = self.endpointer.force_commit()
            LOG.info(
                "commit_now (playing=%s) -> %d action(s)",
                self.playing,
                len(actions) if actions else 0,
            )
            if actions:
                # Release is the endpoint. Reflect that immediately instead of
                # leaving the panel on Listening throughout final Whisper.
                await self.set_state(state="thinking")
            else:
                # A silent hold has nothing to process and must not leave an
                # apparently released push-to-talk session listening forever.
                self.awake = False
                self.followup_deadline = 0.0
                await self.set_state(state="idle")
            await self.handle_endpoint_actions(actions)
        elif kind == "playback_drained":
            # A reply finishing on top of a song is not the end of the song.
            # Reporting idle here would both hand the panel the wrong state and
            # un-gate the wake word while the speaker is still running.
            media_live = bool(self.device and self.device.media_active)
            self.playing = media_live
            self.speech_playback_started_at = 0.0
            self._reset_barge_in()
            self._reset_wakeword()
            if media_live:
                await self.set_state(state="music")
            else:
                if self.web_followup_pending:
                    # Clicking Ask is an explicit activation, like pressing
                    # the talk control. Open one spoken follow-up window after
                    # the typed query's answer finishes playing.
                    self.web_followup_pending = False
                    self.awake = True
                    self.followup_deadline = time.monotonic() + 15.0
                    self.endpointer.reset(active=False)
                    await self.set_state(state="listening")
                elif self.awake and self._followups_enabled():
                    # She was addressed, she answered, and the speaker has
                    # actually finished. Answering is a turn in a conversation,
                    # not the end of one -- so the window reopens here and you
                    # can just talk back without saying her name again.
                    #
                    # THE WINDOW IS OPENED HERE AND NOWHERE EARLIER, which is
                    # the whole point of doing it on `playback_drained`: the
                    # clock has to start when the speaker goes quiet. Started
                    # when generation finished, a long reply sitting in the
                    # board's jitter buffer would eat the entire 15 seconds
                    # before the person had heard the question they were being
                    # asked to answer.
                    #
                    # `awake` is the gate, and it means "this turn was
                    # addressed to Kiki". Ambient speech never sets it, so
                    # overhearing the room still cannot open a microphone
                    # window on its own.
                    self.followup_deadline = time.monotonic() + 15.0
                    self.endpointer.reset(active=False)
                    await self.set_state(state="followup")
                else:
                    self.awake = False
                    self.followup_deadline = 0.0
                    self.endpointer.reset(active=False)
                    await self.set_state(state="idle")
        elif kind == "set_volume":
            percent = max(0, min(100, int(event.get("percent", 70))))
            self.speaker_volume = percent
            # Route through the tool bridge so a change made on the panel and one
            # made by voice end up in exactly the same place.
            if self.device:
                await self.device.execute("adjust_volume", {"action": "set", "amount": percent})
            else:
                await self.send_event("volume", percent=percent)
        elif kind == "config_set":
            if self.config_store:
                key = str(event.get("key", ""))
                value = event.get("value")
                restart = self.config_store.stage(key, value)
                # Apply the live ones immediately so the wizard is not a
                # promise: the user should hear volume and gain change as they
                # turn them, exactly as on the Pi.
                if key == "volume":
                    await self.handle_control({"type": "set_volume", "percent": value})
                elif key == "gain":
                    await self.handle_control({"type": "set_gain", "value": value})
                elif key == "sync":
                    from dataclasses import replace

                    self.config = replace(self.config, display_sync_offset_ms=int(value))
                elif key == "mode":
                    await self._switch_mode(str(value))
                await self.send_event("config_ack", key=key, restart_required=restart)
        elif kind == "config_commit":
            if self.config_store:
                result = self.config_store.commit()
                # The file is not the source of truth for a running process:
                # config_loader caches config.json at import, so without this
                # reload the wizard writes a setting nobody ever reads. This is
                # why switching *to* Hindi appeared to work (the gateway had
                # been restarted since) and switching back did not.
                reload = getattr(self.core, "reload_config", None)
                if reload:
                    await asyncio.to_thread(reload)
                # Mode and language live in the system prompt, so they only take
                # effect once it is rebuilt.
                await self._refresh_prompt()
                await self.send_event("config_saved", **result)
        elif kind == "set_gain":
            # Digital gain sits ahead of the codec, so it is the gateway's to
            # apply -- the panel only asks. Bounded to the range the calibration
            # docs call usable; beyond it the limiter is doing all the work.
            gain = max(1.0, min(5.0, float(event.get("value", 3.2))))
            self.tts.gain = gain
            await self.send_event("gain", value=round(gain, 2))
        elif kind == "device_stats":
            # First one of each session, in full and at INFO. It is the only
            # place the board's memory figures and its field set are visible,
            # and the field set identifies which firmware is running.
            if not self._logged_first_stats:
                self._logged_first_stats = True
                LOG.info("device_stats(first) %s", json.dumps(event, sort_keys=True))
            underruns = int(event.get("underruns", 0))
            dropped = int(event.get("dropped_bytes", 0))
            forced = int(event.get("forced_ends", 0))
            report = (
                "device session=%s buffered_ms=%s underruns=%s underrun_ms=%s "
                "dropped_bytes=%s forced_ends=%s heap_free=%s "
                # Only device_stats(first) is logged in full, and at that point
                # the board has not decoded anything yet -- so the codec
                # counters read zero in the one line that shows them. They
                # belong here, on the line that fires when playback breaks.
                "opus_frames=%s opus_us_max=%s opus_pcm_per_frame=%s "
                "rx_stack_free=%s telemetry_stack_free=%s"
            )
            fields = (
                self.session_id,
                event.get("buffered_ms"),
                underruns,
                event.get("underrun_ms"),
                dropped,
                forced,
                event.get("heap_free"),
                event.get("opus_frames"),
                event.get("opus_us_max"),
                event.get("opus_pcm_per_frame"),
                event.get("rx_stack_free"),
                event.get("telemetry_stack_free"),
            )
            # Only shout when playback actually broke; otherwise this is a
            # once-per-5s heartbeat and must not bury the log.
            if underruns > self._reported_underruns or dropped or forced:
                self._reported_underruns = underruns
                LOG.warning(report, *fields)
            else:
                LOG.debug(report, *fields)
            if event.get("dancing"):
                # The board's own log is deliberately silent while audio plays
                # (kiki_log gives the uplink to the music), so this is the only
                # channel a dance's frame rate can arrive on -- and "the moves
                # are not on the beat" has two very different causes, a clock
                # that is off and a frame rate too low to land a pose on a
                # 376 ms beat. Only while dancing, so it cannot become noise.
                LOG.info("dancing: %s fps, %s us drawing, buffered %s ms, "
                         "prebuffer %s ms",
                         event.get("dance_fps"), event.get("dance_draw_us"),
                         event.get("buffered_ms"), event.get("prebuffer_ms"))
            self._report_battery(event)
            update_motion = getattr(
                getattr(self, "core", None), "update_motion_telemetry", None
            )
            if update_motion is not None:
                update_motion(event)
            # The durable half of do-not-disturb. A motion_event announces the
            # flip, but this is what keeps the answer true afterwards -- and it
            # is the only source that survives a reconnect.
            await self.note_posture(event.get("imu_posture"))
        elif kind == "ping":
            await self.send_event("pong", client_timestamp_us=event.get("timestamp_us"), server_timestamp_us=time.time_ns() // 1000)

    def _queue_health_telemetry(self, event: dict) -> None:
        """Observe the local fall state immediately; deliver HTTP independently."""
        fall = event.get("fall") if isinstance(event.get("fall"), dict) else {}
        status = str(fall.get("status") or "").lower()
        if status == "pending":
            self.fall_check_pending = True
        elif status in {"cancelled", "confirmed", "timeout"}:
            self.fall_check_pending = False
        if status in {"confirmed", "timeout"}:
            event_id = str(fall.get("event_id") or event.get("batch_id") or "")[:80]
            announced = self.core._announced_fall_ids
            if event_id and event_id not in announced:
                announced.add(event_id)
                task = asyncio.create_task(self._announce_confirmed_fall(event_id))
                self.health_alert_tasks.add(task)
                task.add_done_callback(self.health_alert_tasks.discard)
        batch_id = str(event.get("batch_id") or "")[:80]
        if batch_id in self.health_batch_ids:
            return
        try:
            self.health_queue.put_nowait(event)
        except asyncio.QueueFull:
            # No acknowledgement: the durable device queue retries later.
            LOG.warning("wearable delivery queue full; retaining batch %s on device", batch_id)
            return
        self.health_batch_ids.add(batch_id)
        if self.health_task is None or self.health_task.done():
            self.health_task = asyncio.create_task(self._deliver_health_telemetry())

    async def _announce_confirmed_fall(self, event_id: str) -> None:
        """Interrupt ordinary activity and speak the nearby-person warning."""
        LOG.warning("confirmed wearable fall %s: sounding spoken alert", event_id)
        self.awake = False
        self.followup_deadline = 0.0
        await self.cancel_turn("confirmed_fall")
        await self.speak_background(
            "A person has fallen. Please check on them immediately.",
            critical=True,
        )

    async def _deliver_health_telemetry(self) -> None:
        while not self.health_queue.empty():
            event = self.health_queue.get_nowait()
            batch_id = str(event.get("batch_id") or "")[:80]
            try:
                await self._handle_health_telemetry(event)
            except Exception:
                # Failed HTTP/ack/alert delivery cannot tear down voice.
                LOG.exception("wearable delivery failed for batch %s", batch_id)
            finally:
                self.health_batch_ids.discard(batch_id)
                self.health_queue.task_done()

    async def _stop_health_delivery(self) -> None:
        task = getattr(self, "health_task", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.health_task = None
        queue = getattr(self, "health_queue", None)
        if queue is not None:
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
            self.health_batch_ids.clear()

    async def _handle_health_telemetry(self, event: dict) -> None:
        """Deliver one batch from the background worker, never the WS reader."""
        batch_id = str(event.get("batch_id") or "")[:80]
        if not batch_id:
            await self.send_event("health_telemetry_ack", batch_id="", accepted=False,
                                  retry=False, error="missing_batch_id")
            return
        batch = {key: value for key, value in event.items()
                 if key not in {"type", "session_id"}}
        # In health mode the laptop stores the batch itself, using the same
        # code the RPi health service runs. This is what lets the band and this
        # machine be a complete system with the Pi switched off -- and it is
        # deliberately done BEFORE the Pi is contacted, so a durable local
        # record does not depend on a network hop that may not answer.
        stored_locally = await self._store_health_batch_locally(batch)
        bridge = getattr(getattr(self, "core", None), "health_bridge", None)
        if bridge is None:
            await self.send_event("health_telemetry_ack", batch_id=batch_id,
                                  accepted=stored_locally,
                                  retry=not stored_locally,
                                  error="" if stored_locally
                                        else "health_bridge_unavailable")
            return
        try:
            result = await asyncio.to_thread(bridge.ingest, batch)
        except Exception as exc:
            LOG.warning("wearable batch %s could not reach Pi: %s", batch_id, exc)
            # Only unacknowledged batches are retried by the board's durable
            # queue. If this gateway already stored it, acknowledging is the
            # truthful answer -- and without it a person running health mode
            # with no Pi would have the same three batches retried forever.
            await self.send_event("health_telemetry_ack", batch_id=batch_id,
                                  accepted=stored_locally,
                                  retry=not stored_locally,
                                  error="" if stored_locally else str(exc)[:160])
            return

        await self.send_event(
            "health_telemetry_ack", batch_id=batch_id,
            accepted=bool(result.get("accepted")) or stored_locally,
            duplicate=bool(result.get("duplicate")), retry=False,
        )
        alert = result.get("alert_requested")
        dispatch = getattr(self.core, "dispatch_wearable_whatsapp_alert", None)
        if isinstance(alert, dict) and dispatch:
            outcomes = await asyncio.to_thread(dispatch, alert)
            for outcome in outcomes:
                await asyncio.to_thread(bridge.record_alert_outcome, {
                    **outcome, "batch_id": batch_id,
                    # Provider acceptance is not a delivery receipt. Preserve
                    # that distinction explicitly in the durable log.
                    "delivery_confirmed": False,
                })

    async def _handle_motion_event(self, event: dict) -> None:
        """Observe physical reactions and occasionally ask a fresh question."""

        name = str(event.get("event", "")).strip().lower()[:48]
        posture = str(event.get("posture", "unknown"))[:32]
        try:
            intensity = max(0.0, min(1.0, float(event.get("intensity", 0.0))))
        except (TypeError, ValueError):
            intensity = 0.0
        LOG.info(
            "motion_event event=%s posture=%s intensity=%.2f shown=%s",
            name,
            posture,
            intensity,
            bool(event.get("shown", False)),
        )
        if not name:
            return
        # Before anything else: this is the fast edge of do-not-disturb, and
        # the face-down event is itself one of the events that would otherwise
        # make her speak.
        await self.note_posture(posture)
        update_motion = getattr(getattr(self, "core", None), "update_motion_event", None)
        if update_motion is not None:
            update_motion(name, posture)
        cooldown = motion_question_cooldown(name)
        if cooldown is None or not bool(event.get("shown", False)):
            return

        media_live = bool(self.device and self.device.media_active)
        if (
            self.do_not_disturb
            or self.playing
            or self.awake
            or self.push_to_talk_active
            or media_live
            or (self.motion_question_task is not None
                and not self.motion_question_task.done())
        ):
            return
        now = time.monotonic()
        if (
            self.motion_question_last_any
            and now - self.motion_question_last_any < 60.0
        ):
            return
        last_for_event = self.motion_question_last.get(name)
        if last_for_event is not None and now - last_for_event < cooldown:
            return

        picker = getattr(
            getattr(self, "core", None), "choose_motion_question", None
        )
        if picker is None:
            return
        try:
            # The journal reserves and atomically persists the random choice.
            # Keep that small disk write off the websocket receive loop.
            question = await asyncio.to_thread(picker, name)
        except Exception:
            LOG.exception("could not choose journal movement question for %s", name)
            return
        if not question:
            LOG.info("motion question bank exhausted for %s", name)
            return

        self.motion_question_last[name] = now
        self.motion_question_last_any = now
        # Set awake before yielding so a second event cannot race in. Kiki is
        # about to ASK something, and the ordinary drained path then opens the
        # window so it can be answered without saying her name first. A question
        # nobody is allowed to answer is not a question.
        self.awake = True
        self.motion_question_task = asyncio.create_task(
            self._ask_motion_question(name, str(question))
        )

    async def _ask_motion_question(self, event: str, question: str) -> None:
        """Speak one reserved question and make the user's answer coherent."""

        spoken = await self.speak_background(question)
        if not spoken:
            self.awake = False
            self.followup_deadline = 0.0
            if not self.playing:
                await self.set_state(state="idle")
            LOG.info("reserved motion question produced no audio: %s", event)
            return

        recorder = getattr(
            getattr(self, "core", None), "record_motion_question", None
        )
        if recorder is not None:
            try:
                await asyncio.to_thread(recorder, event, question)
            except Exception:
                LOG.exception("could not retain spoken motion question in context")
        self.followup_deadline = time.monotonic() + 15.0
        LOG.info("motion question spoken event=%s text=%s", event, question[:220])

    async def _watch_for_pending_ota(self) -> None:
        """Deliver a firmware URL that appears while the board is connected."""
        try:
            while True:
                await asyncio.sleep(10)
                await self._push_pending_ota()
        except asyncio.CancelledError:
            pass

    async def _push_pending_ota(self) -> None:
        """Hand the device a firmware URL if one is waiting, right after hello.

        Pushed on connect rather than on demand because a congested board only
        holds the socket for ~20 s at a time, and the one moment it is
        reliably listening is immediately after the handshake. The file is
        cleared once sent so a reconnect loop cannot restart the download over
        and over.
        """
        path = self.config.pending_ota_file
        if not path:
            return
        try:
            with open(path) as handle:
                url = handle.read().strip()
        except OSError:
            return
        if not _acceptable_ota_url(url):
            LOG.warning("refusing to push a firmware URL that is not https:// "
                        "or a literal LAN address: %r", url[:120])
            return
        LOG.warning("pushing firmware update to the device: %s", url)
        await self.send_event("firmware_update", url=url)
        try:
            Path(path).unlink()
        except OSError:
            LOG.warning("could not clear %s; the update may be pushed again", path)

    def _public_uri(self) -> str:
        """This gateway's address from outside, handed to the device on hello.

        Read fresh on every connection rather than cached at startup: a
        Cloudflare quick tunnel gets a new hostname each time it restarts, and
        the device is only ever told during a successful LAN connection, so
        this is the one moment the value is both current and deliverable.
        """
        path = self.config.public_uri_file
        if not path:
            return ""
        try:
            with open(path) as handle:
                uri = handle.read().strip()
        except OSError:
            return ""
        # Only a wss:// URL is useful: the device validates the certificate
        # against the public CA bundle, and a plain ws:// public endpoint would
        # be sending a live microphone across the internet in clear text.
        return uri if uri.startswith("wss://") else ""

    def _report_battery(self, event: dict) -> None:
        """Log the power source, and warn once per 10% as a cell runs down.

        On battery there is no USB serial, so this log is the only place the
        charge level can be read from while the board is actually untethered --
        which is the only time it matters.
        """
        percent = event.get("battery_percent")
        update_context = getattr(self.core, "update_battery_context", None)
        if update_context is not None:
            update_context(
                percent,
                on_usb=event.get("on_usb"),
                charging=event.get("charging"),
            )
        if percent is None:
            return
        percent = int(percent)
        on_usb = bool(event.get("on_usb"))
        source = "usb" if on_usb else "battery"
        if source != self._battery_source:
            self._battery_source = source
            LOG.info(
                "power: now on %s (%s%%, %smV, charger=%s)",
                source,
                percent,
                event.get("battery_mv"),
                event.get("charger"),
            )
            self._battery_bucket = None
        if percent < 0 or on_usb:
            return
        bucket = percent // 10
        if self._battery_bucket is None or bucket < self._battery_bucket:
            self._battery_bucket = bucket
            log = LOG.warning if percent <= 20 else LOG.info
            log("power: battery at %s%% (%smV)", percent, event.get("battery_mv"))

    async def _switch_mode(self, name: str) -> str | None:
        """Apply a mode now, through the legacy runtime's own switch."""
        try:
            from core.runtime_controls import switch_mode

            result = await asyncio.to_thread(switch_mode, name)
            # A mode owns both personality and voice.  Drop a prior manual
            # switch_voice override so the selected mode can be heard.
            if self.device:
                self.device.voice = None
            await self._refresh_prompt()
            LOG.info("startup mode switched to %s", name)
            return result
        except Exception:
            LOG.exception("could not switch mode to %s live; it will apply on restart", name)

    @staticmethod
    def _followups_enabled() -> bool:
        """Whether answering should reopen the microphone. Fails OPEN.

        A spoken "stop following up" turns this off through the legacy runtime
        control, and it resets on every launch. Unlike the care capability
        gate, the safe default here is ON: the cost of being wrong is one idle
        listening window, and the cost of the other default is a Kiki who
        cannot be talked back to -- which is exactly the regression this
        predicate was added to fix.
        """
        try:
            from core.runtime_controls import followups_enabled

            return bool(followups_enabled())
        except Exception:
            return True

    def _current_tts_voice(self) -> str:
        """Resolve manual voice override, then the active mode's voice."""
        if self.device and self.device.voice is not None:
            return self.device.voice
        voice_getter = getattr(self.core, "current_voice", None)
        return voice_getter() if voice_getter else ""

    async def _reverify_warm(self, warmer) -> None:
        """Confirm the speaking prefix off the critical path.

        rewarm() is hash-deduped, so when the prefix really is still resident
        this costs nothing. When background work has evicted it, this repairs it
        while the board is already idle and usable -- which is strictly better
        than making a person watch "Warming up model" to find out.
        """
        task = asyncio.ensure_future(
            asyncio.wait_for(warmer(), timeout=self.config.warm_timeout_seconds)
        )
        # Say so if it turns out to be real work. Suppressing "Warming up model"
        # on reconnect was right -- it used to appear after every dropped link
        # because Kiki was rebuilt per connection -- but suppressing it
        # unconditionally made the panel claim ready while the first question
        # silently paid a 45 s prefill. "It says nothing and then works after a
        # while" is the same confusion the other way round.
        #
        # The delay is what separates the two: a still-resident prefix is
        # hash-deduped and returns in milliseconds, so only a genuine repair is
        # slow enough to be worth showing.
        showed = False
        try:
            await asyncio.wait([task], timeout=self.WARM_NOTICE_AFTER_SECONDS)
            if not task.done() and not self.playing and not self.awake:
                showed = True
                await self.set_state(state="warming")
            await task
        except Exception:
            LOG.warning("background prefix re-verify failed", exc_info=True)
        finally:
            # Only hand the panel back if we were the one who took it, and only
            # if nothing has started since -- a question that arrived mid-warm
            # owns the state now.
            if showed and not self.playing and not self.awake:
                await self.set_state(state="idle")

    async def _refresh_prompt(self) -> None:
        """Rebuild the system prefix so a mode/language change takes hold.

        Replacing the first message invalidates the whole KV-cached prefix, so
        this re-warms; it is deliberately only called when a setting changed,
        never per turn.
        """
        refresh = getattr(self.core, "refresh_system_prompt", None)
        if not refresh:
            return
        try:
            if await asyncio.to_thread(refresh):
                await self.set_state(state="warming")
                warmer = getattr(self.core, "ensure_ready", None)
                if warmer:
                    await warmer()
                await self.set_state(state="idle")
        except Exception:
            LOG.exception("could not rebuild the system prompt")

    async def set_state(self, state: str, **fields) -> None:
        """Announce a state to the board and give it the matching LCD rows.

        These are one action, not two: a state without its rows is what left the
        panel showing a stale line while the face had already moved on.
        """
        await self.send_event("state", state=state, **fields)
        if self.display:
            self.display.status_for_state(state, str(fields.get("detail", "")))
        # Every route back to rest re-asserts the notice, because the rows it
        # was written into are the same ones a turn overwrites. Without this,
        # answering one direct question while face down would quietly leave
        # "Say 'kiki'" on a device that is still refusing to start anything.
        if self.do_not_disturb and state in ("idle", "followup"):
            await self._show_do_not_disturb()

    # Gravity, not a menu. Face down is the one gesture that needs no screen,
    # no button and no sentence, so it is what stops everything.
    DO_NOT_DISTURB_POSTURE = "face_down"
    # Turning her upside down stops a dance. Deliberately a different gesture
    # from face down: this one ends the performance, it does not silence her.
    DANCE_STOP_POSTURE = "upside_down"
    # Only a posture the classifier is sure of ends it. "unknown" is what the
    # IMU reports when it cannot tell -- treating that as "turned back over"
    # would let a bad sample cancel do-not-disturb.
    KNOWN_POSTURES = frozenset(
        {"face_up", "face_down", "upright", "upside_down", "side_left", "side_right"}
    )

    async def note_posture(self, posture: object) -> None:
        """Enter or leave do-not-disturb when the board's orientation changes.

        Fed from both `device_stats` (every 5 s, the authority on the state
        Kiki is *in*) and `motion_event` (the edge, which arrives as soon as
        she is flipped). Both call this and only a real change acts, so the
        fast path and the durable one cannot fight.
        """
        name = str(posture or "").strip().lower()
        if not name or name not in self.KNOWN_POSTURES or name == self.posture:
            return
        self.posture = name
        if name == self.DO_NOT_DISTURB_POSTURE:
            await self._enter_do_not_disturb()
            return
        if name == self.DANCE_STOP_POSTURE and self.dance_active:
            # Holding her upside down is the second way to stop a dance, and
            # the board acts on it locally too -- this is the half that tears
            # down the song. Unlike face down it is not do-not-disturb: turn
            # her back over and she is simply idle again.
            await self.end_dance("upside_down")
        if self.do_not_disturb:
            await self._leave_do_not_disturb()

    async def _enter_do_not_disturb(self) -> None:
        """Stop everything, and refuse to start anything until she is upright."""
        self.do_not_disturb = True
        LOG.info("face down: do not disturb (stopping speech and media)")
        # Cleared before the cancel, so cancel_turn's own closing state is
        # `idle` rather than an open listening window nobody asked for.
        self.awake = False
        self.push_to_talk_active = False
        self.followup_deadline = 0.0
        await self.cancel_turn("do_not_disturb")
        await self._show_do_not_disturb()

    async def _leave_do_not_disturb(self) -> None:
        self.do_not_disturb = False
        LOG.info("turned back over: do not disturb off")
        await self.set_state(state="idle")

    async def _show_do_not_disturb(self) -> None:
        """Say so on the panel.

        Sent as the ordinary status rows rather than a new `state` name: the
        state machine that would have to learn one lives in firmware, and a
        message explaining why Kiki is quiet must not itself require an OTA to
        appear. These are the same two rows that otherwise read "Kiki is Idle"
        / "Say 'kiki'", so the notice lands exactly where the invitation was.
        """
        await self.send_event("lcd", line1="Do Not Disturb", line2="Face down")

    # ------------------------------------------------------------- dancing --

    async def await_playback_drained(self, timeout: float) -> None:
        """Wait until the board reports the current audio finished.

        `playing` is cleared only by `playback_drained` from the device, which
        is the one signal that means the speaker is actually free. Bounded,
        because a reply that never drains must not be able to hold a dance --
        or anything else -- forever.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while self.playing and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    async def begin_dance(self, routine, grid) -> None:
        """Hand the board the routine, then let the music start.

        Order matters and is the reason this is a separate step from starting
        playback: the board arms its dance clock on the first media sample it
        plays, so the grid has to already be there when that sample arrives.
        Events and audio share one ordered websocket, so sending this first is
        sufficient -- there is no handshake to wait for.
        """
        self.dance_active = True
        self.dance_started_at = time.monotonic()
        self.dance_title = routine.song
        await self.send_event(
            "dance_start",
            bpm=round(float(grid.bpm), 3),
            # Where the first beat sits inside the song, in milliseconds. The
            # board adds this to its own media clock; it is not a delay.
            beat0_ms=int(round(grid.beat0 * 1000.0)),
            mood=routine.mood,
            title=routine.song[:64],
            beats=(routine.steps[-1].beat + routine.steps[-1].beats) if routine.steps else 0,
            measured=bool(grid.confidence > 0.0),
            routine=routine.encode(),
            energy=dance.encode_energy(grid.energy),
        )
        LOG.info("dance started: %r at %.1f BPM (%s grid)", routine.song, grid.bpm,
                 "measured" if grid.confidence > 0.0 else "hinted")

    async def end_dance(self, reason: str, notify_device: bool = True) -> None:
        """The single exit. Every stop path -- tap, flip, voice, song end,
        cancel, do-not-disturb, disconnect -- comes through here.

        Idempotent on purpose: the board stops locally the instant it is
        tapped and *also* tells us, and the gateway may already have stopped
        for its own reasons. Both orders have to be safe.
        """
        if not self.dance_active:
            return
        self.dance_active = False
        LOG.info("dance ended after %.0fs (%s)",
                 time.monotonic() - self.dance_started_at, reason)
        if notify_device:
            await self.send_event("dance_stop", reason=reason)
        if self.device:
            await self.device.stop()
            await self.device.notify_media()
        await self.send_event("audio_stop", reason=f"dance_{reason}"[:32])
        self.playing = False
        self.speech_playback_started_at = 0.0
        self._reset_barge_in()
        await self.set_state(state="listening" if self.awake else "idle")

    async def activate_query(
        self,
        source: str,
        score: float | None = None,
        reset_endpointer: bool = True,
        early: bool = False,
    ) -> None:
        """Open the query window.

        ``reset_endpointer`` is False for every text-hotword wake. A button
        press and an acoustic wake word both happen *before* the request is
        spoken, so throwing away the audio collected so far is correct there.
        A hotword found in a transcript arrives during or after the sentence it
        belongs to, and resetting would delete the utterance still in progress.
        """
        self.awake = True
        self.followup_deadline = time.monotonic() + 15.0
        if not early:
            # Any other wake supersedes a provisional one, so the speculative
            # transcript can no longer take this window back. Idle speech from
            # before this moment is dropped with it: being addressed directly
            # is the point at which older chatter stops being the subject.
            self._early_wake_generation = None
            self._forget_ambient()
        # Waking up while a song plays is otherwise a dead end: the board holds
        # its microphone shut for as long as its speaker is running, so nothing
        # would ever arrive to end the turn. Pause the music and the mic returns
        # within a buffer's worth of audio; unduck() puts the song back.
        if self.device:
            await self.device.duck()
        if reset_endpointer:
            self.endpointer.reset(active=False)
        await self.send_event("wake", source=source, score=score)
        await self.set_state(state="listening")

    def _reset_wakeword(self) -> None:
        detector = getattr(self, "wakeword", None)
        if detector is not None:
            detector.reset()

    def _hotword_matcher(self) -> HotwordMatcher | None:
        """The matcher for the mode that is active right now.

        Rebuilt only when the word list actually changes, so a mode switch is
        picked up on the next utterance without constructing a matcher per
        frame.
        """
        if not getattr(self.config, "hotword_enabled", True):
            return None
        words: tuple[str, ...] = ()
        getter = getattr(self.core, "active_hotwords", None)
        if getter is not None:
            try:
                words = tuple(getter())
            except Exception:
                LOG.debug("could not read the mode's hotwords", exc_info=True)
        if not words:
            words = self.config.default_hotwords()
        if not words:
            return None
        if self._matcher is None or words != self._matcher_words:
            self._matcher = HotwordMatcher(
                words,
                threshold=self.config.hotword_similarity,
                min_fuzzy_length=self.config.hotword_min_fuzzy_length,
            )
            self._matcher_words = words
            LOG.info("hotwords: %s", ", ".join(words))
        return self._matcher

    def _match_hotword(self, text: str):
        matcher = self._hotword_matcher()
        return matcher.find(text) if matcher is not None else None

    def _remember_ambient(self, text: str) -> None:
        """Keep idle speech that could still turn out to be a question.

        whisper.cpp narrates silence and noise as ``[BLANK_AUDIO]``,
        ``(music)`` and friends. Those are the most common thing said in a
        quiet room, so without this filter the text carried into a query is
        usually a stage direction.
        """
        cleaned = ASR_ANNOTATION.sub(" ", text).strip()
        if len(tokenize(cleaned)) < 2:
            return
        self.recent_ambient.append((time.monotonic(), cleaned))

    def _forget_ambient(self) -> None:
        # Tolerant of the narrow test fixtures that build a DeviceSession
        # without __init__, the same way _reset_barge_in is.
        buffer = getattr(self, "recent_ambient", None)
        if buffer is not None:
            buffer.clear()

    def _carry_back_text(self, match) -> str:
        """Recent idle speech that belongs to a hotword arriving after it.

        "what's the capital of france" ... "what do you think kiki" is two
        utterances to the endpointer, and answering only the second one is
        answering nothing. The first is carried in -- but only when the
        hotword's own utterance looks like an address rather than a question:
        few words before it, and essentially nothing after it. That is what
        separates "what do you think kiki" (carried) from "kiki what's the
        weather" (self-contained, and prepending old chatter would corrupt it).
        """
        config = self.config
        if match.words_before > config.hotword_carry_back_max_lead_words:
            return ""
        if match.words_after > config.hotword_carry_back_max_trail_words:
            return ""
        now = time.monotonic()
        collected: list[str] = []
        for spoken_at, text in reversed(self.recent_ambient):
            if now - spoken_at > config.hotword_carry_back_seconds:
                break
            if not text:
                continue
            collected.append(text)
            if len(collected) >= config.hotword_carry_back_max_utterances:
                break
        return " ".join(reversed(collected))

    async def _early_wake(self, generation: int, match) -> None:
        """Wake on a speculative transcript, ~360 ms before the commit does."""
        self._early_wake_generation = generation
        await self.activate_query(
            source="hotword_speculative",
            score=match.score,
            reset_endpointer=False,
            early=True,
        )

    async def _revert_early_wake(self) -> None:
        """Take back a window opened on a hotword the final transcript denies."""
        LOG.info("speculative hotword not confirmed; closing the window again")
        self.awake = False
        self.followup_deadline = 0.0
        if self.device:
            await self.device.unduck()
        if not self.playing:
            await self.set_state(state="idle")

    async def _compact_quietly(self, compact) -> None:
        """Compact the context off the critical path, never fatally."""
        try:
            if await compact():
                LOG.info("context compacted after an abandoned turn")
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.warning("post-abort compaction failed", exc_info=True)

    def _new_turn_abort(self) -> threading.Event:
        """A fresh cancellation flag for one turn, registered so it can be
        reached from outside that turn.

        respond() installs a NEW Event per turn and the worker captures it, so
        `self.turn_abort` only ever names the newest turn -- setting it orphans
        every older one, which keeps its own copy and speaks anyway. Keeping
        them all is what makes a cooperative stop possible at all.
        """
        abort = threading.Event()
        if not hasattr(self, "turn_aborts"):
            self.turn_aborts = {}
        # Keyed by the task that owns it, so a stop can be aimed. Setting every
        # flag would abort ambient transcription along with the question, which
        # is the bug this file already fixed once at the task level.
        try:
            owner = asyncio.current_task()
        except RuntimeError:
            # __init__ builds one of these, and a DeviceSession can be
            # constructed with no loop running. current_task() raises there
            # rather than returning None, which would have turned "make a
            # session" into a crash on some paths.
            owner = None
        # Bounded: a finished task has nothing left to abort.
        self.turn_aborts = {
            task: flag for task, flag in self.turn_aborts.items()
            if task is not None and not task.done()
        }
        self.turn_aborts[owner] = abort
        return abort

    def _cancel_turn_tasks(self, reason: str, queries_only: bool = False) -> str | None:
        """Cancel in-flight turns, never the one asking.

        `queries_only` narrows it to turns that are answering a question. Stop
        means stop everything; a new question only displaces the previous
        question, and must leave ambient transcription alone -- that is what
        keeps the room's context and the hotword working.

        Returns a short description when something was actually cancelled, so
        callers can say so; a turn abandoned mid-answer is not something to
        discover only from the silence.
        """
        current = asyncio.current_task()
        pool = getattr(self, "query_turns", ()) if queries_only else None
        # getattr, not self.turn_tasks: a few narrow fixtures build
        # DeviceSession without __init__, and cancel_turn is on the
        # do-not-disturb and shutdown paths where raising would be worse than
        # having nothing to cancel. A mutable class-level default would be the
        # other way to do this, and would quietly share one set between every
        # session in the process.
        stale = [task for task in getattr(self, "turn_tasks", ())
                 if task is not current and not task.done()
                 and (pool is None or task in pool)]
        # Ask every turn to stop, do not rip it out. task.cancel() abandons
        # the HTTP stream to llama.cpp mid-flight, and that box has ONE slot:
        # the coordinator never learns the request ended, so the slot is never
        # released and the NEXT question waits on it forever. That is why a
        # Stop press was followed by a turn that transcribed correctly, printed
        # "turn endpoint", and then produced nothing at all -- the reply had
        # nowhere to run. Setting the flag lets each turn unwind through its
        # own path and hand the slot back.
        flags = getattr(self, "turn_aborts", {})
        for task in stale:
            flag = flags.get(task)
            if flag is not None:
                flag.set()
        # The current turn's flag is set too, and that is correct: whoever is
        # asking wants everything stopped. It is only the *task* we must not
        # cancel, because cancelling the caller kills it mid-cleanup.
        #
        # Asking is not enough on its own: a turn blocked somewhere that never
        # looks at the flag would linger forever. So give them a moment to
        # unwind properly -- which is what runs their cleanup, including the
        # context compaction that a skipped teardown used to lose -- and cancel
        # only what is still running after that.
        async def enforce() -> None:
            await asyncio.sleep(self.STOP_GRACE_SECONDS)
            for task in stale:
                if not task.done():
                    LOG.warning("turn ignored the stop flag; cancelling it")
                    task.cancel()

        enforcer = asyncio.ensure_future(enforce())
        # Held only so it is not garbage collected mid-wait, and only if this
        # session has the set at all -- cancel_turn runs on the do-not-disturb
        # and shutdown paths, where narrow fixtures build DeviceSession without
        # __init__ and raising would be worse than not tracking one task.
        tracked = getattr(self, "turn_tasks", None)
        if tracked is not None:
            tracked.add(enforcer)
            enforcer.add_done_callback(tracked.discard)
        if not stale:
            return None
        # A cancelled turn cannot be relied on to run its own cleanup: the
        # finally block in the reply path starts with an await, and awaiting
        # inside a task that is already being cancelled re-raises immediately,
        # so everything after it is skipped. `playing` is the one flag that
        # must not be left set -- while it is true the gateway discards every
        # microphone frame, so Kiki listens, hears nothing, and returns to idle
        # for every question from then on. That is a dead session until the
        # process restarts, which is far worse than the queue this cancelling
        # was added to fix.
        self.playing = False
        self.speech_playback_started_at = 0.0
        LOG.info("%s: cancelled %d turn(s) still in flight", reason, len(stale))
        return f"{len(stale)} turn(s)"

    async def cancel_turn(self, reason: str) -> None:
        # A dance is cancelled by every one of the things that cancel a turn --
        # the Stop button, an open palm, going face down, a barge-in -- so it
        # is torn down here rather than in each of them. end_dance() is
        # idempotent and the work below repeats harmlessly if it already ran.
        if self.dance_active:
            await self.end_dance(reason)
        self.turn_abort.set()
        # Stop means stop *everything*, not just the reply currently making
        # sound. Every committed turn runs as its own task and nothing bounded
        # how many could be alive: ask three questions in a row and three
        # replies generate concurrently, serialised only by the single-slot
        # local model. Cancelling the audio then just let the next one start
        # speaking -- so Stop behaved like "skip to the next answer", and
        # pressing it three times played all three answers in order.
        #
        # turn_abort cannot do this on its own: respond() installs a *fresh*
        # Event per turn, so setting it reaches only the newest turn and
        # orphans the older ones, which hold their own captured copy and go on
        # to speak regardless.
        self._cancel_turn_tasks(reason)
        self.push_to_talk_active = False
        self._cancel_speech_text()
        self.playing = False
        self.speech_playback_started_at = 0.0
        self._reset_barge_in()
        for task in self.spec_tasks.values():
            task.cancel()
        for task in self.spec_prefill_tasks.values():
            task.cancel()
        self.spec_tasks.clear()
        self.spec_prefill_tasks.clear()
        self.turn_modes.clear()
        self.endpointer.reset(active=False)
        if self.device:
            await self.device.stop()
            # stop() kills the song; without this the transport row stays on
            # screen with nothing playing behind it.
            await self.device.notify_media()
        await self.send_event("audio_stop", reason=reason)
        await self.set_state(state="listening" if self.awake else "idle")

    def _narrow_tts(self, pcm: bytes) -> bytes:
        """48 kHz speech down to 16 kHz for a link that cannot carry 768 kbps.

        Only the *rate* changes; the reply is the same length of audio. The
        decimator is phase-sensitive -- it keeps every third sample of its own
        filtered output -- so whole groups of three have to be handed to it or
        successive chunks would each restart the pattern and the voice would
        develop a periodic warble. Whatever does not divide evenly is carried
        into the next chunk.
        """
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if self._tts_tail.size:
            samples = np.concatenate((self._tts_tail, samples))
        usable = samples.size - (samples.size % 3)
        self._tts_tail = samples[usable:].copy()
        if usable == 0:
            return b""
        narrow = self.tts_decimator.process(samples[:usable])
        return np.clip(np.rint(narrow), -32768, 32767).astype("<i2").tobytes()

    def _reset_barge_in(self) -> None:
        # A few narrow unit-test fixtures construct DeviceSession without
        # __init__; keeping reset tolerant also makes shutdown/error paths safe
        # if model construction ever fails before session setup completes.
        detector = getattr(self, "barge_in", None)
        if detector is not None:
            detector.reset()

    async def handle_audio(self, frame: AudioFrame) -> None:
        if frame.kind not in (
            BinaryKind.MIC_PCM_S16_48K_MONO,
            BinaryKind.MIC_PCM_S16_16K_MONO,
        ):
            raise ValueError("device may only send microphone frames")
        narrowband = frame.kind == BinaryKind.MIC_PCM_S16_16K_MONO
        self.mic_frames_received += 1
        self.mic_samples_received += len(frame.payload) // 2
        now = time.monotonic()
        if self.mic_frames_received == 1:
            self.mic_stats_started = now
            self.mic_stats_last_log = now
        if now - self.mic_stats_last_log >= 2.0:
            elapsed = now - self.mic_stats_started
            # Whatever rate the board is sending at, or audio_rate reads 3x
            # slow the moment the link goes remote and looks like packet loss.
            audio_seconds = self.mic_samples_received / (16000.0 if narrowband else 48000.0)
            LOG.info(
                "mic stream session=%s frames=%d rate=%.1f/s audio_rate=%.2fx sequence=%d",
                self.session_id,
                self.mic_frames_received,
                self.mic_frames_received / elapsed,
                audio_seconds / elapsed,
                frame.sequence,
            )
            self.mic_stats_last_log = now
        if self.last_mic_sequence is not None and frame.sequence != self.last_mic_sequence + 1:
            await self.send_event(
                "audio_gap", expected=self.last_mic_sequence + 1, received=frame.sequence
            )
        self.last_mic_sequence = frame.sequence
        # The board sends 16 kHz once it is on the remote link, having already
        # done the low-pass and decimation this branch would do. RNNoise is the
        # one thing lost: it needs 48 kHz, and denoising is worth less than
        # audio that arrives at all.
        if narrowband:
            chunks = [np.frombuffer(frame.payload, dtype="<i2").astype(np.float32) / 32768.0]
        else:
            chunks = []
            self.pcm48 = np.concatenate(
                (self.pcm48, np.frombuffer(frame.payload, dtype="<i2"))
            )
            while self.pcm48.size >= 480:
                raw, self.pcm48 = self.pcm48[:480], self.pcm48[480:]
                chunks.append(self.decimator.process(self.rnnoise.process(raw)))

        for audio16 in chunks:
            # Full-duplex listening is deliberately fail-closed: playback can
            # only be interrupted by one complete 32 ms frame which the new
            # firmware identifies as ESP-SR AEC output. Raw/old firmware,
            # malformed chunking, and music playback keep the former behavior
            # and cannot trigger on Kiki's own speaker.
            if self.playing:
                continue
            # Room audio is endpointed and transcribed whether Kiki is awake or
            # not. That is not an optimisation to skip: THE HOTWORD LIVES IN THE
            # TRANSCRIPT (§5.3). There is no acoustic wake model running by
            # default, so if idle audio never reaches the endpointer there is no
            # utterance, no Whisper text, and nothing for the hotword matcher to
            # find -- saying "kiki" does nothing and the physical talk control
            # becomes the only way in. Idle speech is also what feeds ambient
            # context and the carry-back that answers "what do you think kiki".
            if self.wakeword is not None:
                score = self.wakeword.feed(audio16)
                if score is not None and not self.awake:
                    await self.activate_query("openwakeword", score)
            self.pcm16 = np.concatenate((self.pcm16, audio16))
            while self.pcm16.size >= 512:
                vad_frame, self.pcm16 = self.pcm16[:512], self.pcm16[512:]
                if self.awake and time.monotonic() > self.followup_deadline:
                    self.awake = False
                    await self.set_state(state="idle")
                    # Woken, then nothing said. Put a ducked song back rather
                    # than leaving it paused for good -- after the idle state,
                    # so the resumed music is what the panel ends up showing.
                    if self.device:
                        await self.device.unduck()
                    continue
                actions = self.endpointer.feed(
                    vad_frame, defer_commit=self.push_to_talk_active
                )
                await self.handle_endpoint_actions(actions)

    async def _handle_barge_in_frame(self, audio16: np.ndarray, flags: int) -> None:
        """Confirm near-end speech during TTS, then hand it to endpointing."""

        media_live = bool(self.device and self.device.media_active)
        if (
            not getattr(self, "barge_in_enabled", self.config.barge_in_enabled)
            or media_live
            or self.speech_playback_started_at <= 0.0
            or (time.monotonic() - self.speech_playback_started_at) * 1000.0
            < self.config.barge_in_guard_ms
        ):
            return
        device_speech = bool(flags & int(AudioFlag.DEVICE_VAD_SPEECH))
        decision = self.barge_in.feed(
            audio16,
            device_vad_speech=device_speech,
        )
        if decision is not None:
            await self._start_voice_barge_in(decision)

    async def _start_voice_barge_in(self, decision: BargeInDecision) -> None:
        """Stop only Kiki's TTS and seed a normal user turn with its preroll."""

        # The media check is repeated at the destructive edge in case a tool
        # started a track between detection and this coroutine being scheduled.
        if not self.playing or (self.device and self.device.media_active):
            self._reset_barge_in()
            return
        LOG.info(
            "voice barge-in confirmed probability=%.3f level=%.1fdBFS floor=%.1fdBFS preroll_ms=%d",
            decision.probability,
            decision.level_dbfs,
            decision.noise_floor_dbfs,
            round(decision.audio.size / 16.0),
        )
        self.turn_abort.set()
        self._cancel_speech_text()
        self.playing = False
        self.speech_playback_started_at = 0.0
        self.awake = True
        self.push_to_talk_active = False
        self.followup_deadline = time.monotonic() + 15.0
        self._reset_wakeword()
        self.endpointer.reset(active=False)
        self._reset_barge_in()
        await self.send_event(
            "audio_stop",
            reason="voice_barge_in",
            probability=round(decision.probability, 3),
            level_dbfs=round(decision.level_dbfs, 1),
        )
        await self.set_state(state="listening")
        # Preserve the speech that accumulated while voting. Feeding the exact
        # AEC-clean preroll into the ordinary endpointer prevents the first
        # word from being cut off and keeps ASR/commit behavior identical to a
        # button or wake-word turn.
        for offset in range(0, decision.audio.size, 512):
            chunk = decision.audio[offset : offset + 512]
            if chunk.size != 512:
                break
            actions = self.endpointer.feed(chunk)
            await self.handle_endpoint_actions(actions)

    async def handle_endpoint_actions(self, actions) -> None:
        for action in actions:
            if action.kind == ActionKind.SPEECH_START:
                self.turn_modes[action.generation] = self.awake
                self.last_asr_ms = 0.0
                # Ambient too: the panel showing that she is HEARING you is
                # what makes saying her name feel like it did something, and it
                # is the only feedback there is before the transcript lands.
                await self.send_event("speech_start", generation=action.generation,
                                      mode="query" if self.awake else "ambient")
            elif action.kind == ActionKind.PARTIAL:
                if not self.turn_modes.get(action.generation, False):
                    continue
                if self.partial_busy or self.last_asr_ms > self.config.partial_asr_interval_ms * 0.4:
                    continue
                self.partial_busy = True
                task = asyncio.create_task(self._run_partial(action.generation, action.audio))
                self.partial_tasks.add(task)
                task.add_done_callback(self.partial_tasks.discard)
            elif action.kind == ActionKind.SPECULATIVE:
                query_mode = self.turn_modes.get(action.generation, False)
                if not query_mode:
                    continue
                old = self.spec_tasks.pop(action.generation, None)
                if old:
                    old.cancel()
                asr_task = asyncio.create_task(self.whisper.transcribe(action.audio))
                self.spec_tasks[action.generation] = asr_task
                self.spec_prefill_tasks[action.generation] = asyncio.create_task(
                    self._prefill_speculative(action.generation, asr_task, query_mode)
                )
                await self.send_event("stt_speculative", generation=action.generation)
            elif action.kind == ActionKind.INVALIDATE:
                task = self.spec_tasks.pop(action.generation, None)
                if task:
                    task.cancel()
                prefill = self.spec_prefill_tasks.pop(action.generation, None)
                if prefill:
                    prefill.cancel()
                await self.send_event("stt_speculative_invalid", generation=action.generation)
            elif action.kind == ActionKind.COMMIT:
                task = self.spec_tasks.pop(action.generation, None)
                self.spec_prefill_tasks.pop(action.generation, None)
                query_mode = self.turn_modes.pop(action.generation, self.awake)
                if not query_mode:
                    if task:
                        task.cancel()
                    continue
                # A new *question* replaces one still being answered. She
                # cannot answer two at once, and letting both run is what built
                # the reply queue.
                #
                # Only a question, and only against other questions. Every
                # committed utterance arrives here, including ambient ones the
                # endpointer picks up while she is talking -- background speech,
                # a TV, the tail of your own sentence. Cancelling on those threw
                # away the answer you were waiting for and dropped the panel
                # straight back to listening with nothing said, which is exactly
                # what "it listens and then just goes back" looked like.
                if query_mode:
                    self._cancel_turn_tasks("superseded by a new question",
                                            queries_only=True)
                turn_task = asyncio.create_task(
                    self._finalize_turn(action.generation, action.audio, task, query_mode)
                )
                self.turn_tasks.add(turn_task)
                if query_mode:
                    self.query_turns.add(turn_task)
                turn_task.add_done_callback(self.turn_tasks.discard)
                turn_task.add_done_callback(self.query_turns.discard)

    async def _prefill_speculative(
        self, generation: int, asr_task: asyncio.Task, query_mode: bool = True
    ) -> None:
        try:
            text, _ = await asyncio.shield(asr_task)
            if not text or generation != self.endpointer.generation:
                return
            if not query_mode:
                # Idle speech: nothing may touch the speaking model until the
                # hotword says this was meant for Kiki.
                if self.awake:
                    return
                match = self._match_hotword(text)
                if match is None:
                    return
                LOG.info(
                    "hotword %r heard early in %r (score %.2f)",
                    match.hotword,
                    text[:120],
                    match.score,
                )
                await self._early_wake(generation, match)
            await self.core.prefill_partial(text)
        except asyncio.CancelledError:
            return
        except Exception:
            LOG.debug("speculative LLM prefill failed", exc_info=True)

    async def _run_partial(self, generation: int, audio: np.ndarray) -> None:
        try:
            text, elapsed = await self.whisper.transcribe(audio)
            self.last_asr_ms = elapsed * 1000
            if text and generation == self.endpointer.generation and self.awake:
                await self.send_event(
                    "transcript_partial", text=panel_text(text), stt_ms=round(elapsed * 1000)
                )
                await self.core.prefill_partial(text)
        except asyncio.CancelledError:
            return
        except Exception:
            LOG.debug("partial ASR failed", exc_info=True)
        finally:
            self.partial_busy = False

    async def _finalize_turn(
        self, generation: int, audio: np.ndarray, speculative_task, query_mode: bool
    ) -> None:
        async with self.turn_lock:
            endpoint_at = time.perf_counter()
            try:
                if speculative_task is None:
                    text, asr_seconds = await self.whisper.transcribe(audio)
                else:
                    text, asr_seconds = await speculative_task
            except asyncio.CancelledError:
                return
            except Exception as exc:
                await self.send_event("error", code="stt_failed", message=str(exc))
                return
            # THE WAKE DECISION. An utterance spoken while the window was
            # already open is a query whatever it says; otherwise Kiki was
            # addressed only if her name is in this transcript.
            match = None if query_mode else self._match_hotword(text)
            woke_early = self._early_wake_generation == generation
            if woke_early:
                self._early_wake_generation = None
                if match is None and not query_mode:
                    await self._revert_early_wake()
                    woke_early = False
            is_query = query_mode or match is not None
            await self.send_event(
                "transcript_final",
                text=panel_text(text),
                mode="query" if is_query else "ambient",
                stt_ms=round(asr_seconds * 1000),
                endpoint_wait_ms=round((time.perf_counter() - endpoint_at) * 1000),
            )
            LOG.info(
                "turn endpoint session=%s mode=%s stt_ms=%d endpoint_wait_ms=%d",
                self.session_id,
                "query" if is_query else "ambient",
                round(asr_seconds * 1000),
                round((time.perf_counter() - endpoint_at) * 1000),
            )
            if not text:
                return
            if await self._handle_fall_voice_response(text):
                return
            if not is_query:
                self._remember_ambient(text)
                await self.send_event("ambient", text=panel_text(text))
                recorder = getattr(self.core, "record_ambient", None)
                if recorder:
                    await recorder(text)
                return
            query = text
            if match is not None:
                carried = self._carry_back_text(match)
                LOG.info(
                    "hotword %r matched %r score=%.2f before=%d after=%d carried=%d chars",
                    match.hotword,
                    match.matched,
                    match.score,
                    match.words_before,
                    match.words_after,
                    len(carried),
                )
                if not woke_early:
                    await self.activate_query(
                        source="hotword", score=match.score, reset_endpointer=False
                    )
                if carried:
                    query = carried + " " + text
                elif match.is_bare:
                    # Her name and nothing else, with no recent speech to
                    # attach it to: the old wake word's exact behaviour --
                    # start listening, say nothing.
                    self._forget_ambient()
                    await self.set_state(state="listening")
                    return
            # Anything carried in has now been asked; leaving it buffered would
            # let it be prepended to the NEXT question too.
            self._forget_ambient()
            await self.respond(query, endpoint_at)

    async def _handle_fall_voice_response(self, text: str) -> bool:
        """Resolve the wearable's local question before ordinary hotword routing."""
        if not getattr(self, "fall_check_pending", False):
            return False
        if FALL_HELP.search(text):
            self.fall_check_pending = False
            await self.send_event("fall_check_confirm")
            LOG.warning("wearer asked for help during fall check")
            return True
        if FALL_OK.search(text):
            self.fall_check_pending = False
            await self.send_event("fall_check_cancel")
            LOG.info("wearer verbally cancelled fall check")
            return True
        return False

    # ----------------------------------------------------------- health mode
    #
    # Everything below is reached only from a mode that declares the `care`
    # capability. `_care_session_owner()` is the gate, it returns None in every
    # other mode, and the cost of it in `default` is one attribute lookup.

    def _care_runtime(self):
        """The live care runtime, or None. Never builds one."""
        try:
            from .care.runtime import care_runtime_if_live

            return care_runtime_if_live()
        except Exception:
            LOG.exception("care runtime unavailable")
            return None

    def _care_session_owner(self):
        """The runtime, only while a care session is actually holding the mic."""
        runtime = self._care_runtime()
        if runtime is None:
            return None
        try:
            return runtime if runtime.session_active() else None
        except Exception:
            LOG.exception("could not read the care session state")
            return None

    def _handle_care_device_event(self, kind: str, event: dict) -> None:
        """Route one sensor event from the board into the care stack."""
        runtime = self._care_runtime()
        if runtime is None:
            return
        try:
            if kind == "imu_window":
                accepted = runtime.note_imu_window(event)
                LOG.info("imu_window %s samples=%s accepted=%s",
                         str(event.get("window_id"))[:24], event.get("samples"),
                         accepted)
            else:
                runtime.note_measurement_status(event)
        except Exception:
            LOG.exception("could not handle the %s event", kind)

    async def _store_health_batch_locally(self, batch: dict) -> bool:
        """Persist one wearable batch on this machine. False when not in health mode."""
        runtime = self._care_runtime()
        if runtime is None:
            return False
        try:
            await asyncio.to_thread(runtime.ingest_telemetry, batch)
            return True
        except Exception:
            LOG.exception("could not store the wearable batch locally")
            return False

    async def _handle_care_action(self, event: dict) -> None:
        """The watch asked to view or change the care plan."""
        runtime = self._care_runtime()
        action = str(event.get("action") or "refresh")
        if runtime is None:
            # Not in health mode, so there is no plan to show. Say so on the
            # panel rather than leaving the screen sitting on an empty list it
            # cannot explain.
            await self.send_event("care_plan", items=[], total=0,
                                  detail="Health mode is off")
            return
        if action != "refresh":
            result = await asyncio.to_thread(
                runtime.apply_panel_action, action, str(event.get("id") or ""))
            LOG.info("care panel %s id=%s -> %s", action,
                     str(event.get("id"))[:12], result)
            if action == "end":
                await self.cancel_turn("care_end")
        await self._send_care_plan()

    async def _send_care_plan(self) -> None:
        """Push the whole plan to the board. The panel holds no state of its own."""
        runtime = self._care_runtime()
        if runtime is None:
            return
        try:
            payload = await asyncio.to_thread(runtime.plan_payload)
        except Exception:
            LOG.exception("could not build the care plan payload")
            return
        await self.send_event("care_plan", **payload)

    def send_event_sync(self, event_type: str, fields: dict) -> bool:
        """Send one device event from a worker thread. For the care stack."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.send_event(event_type, **(fields or {})), self.loop)
            future.result(timeout=5)
            return True
        except Exception:
            LOG.exception("could not send %s to the board", event_type)
            return False

    def request_motion_window(self, seconds: float) -> bool:
        """Ask the board to record the wrist for `seconds` and send it back."""
        return self.send_event_sync(
            "imu_window_start", {"seconds": round(float(seconds), 2)})

    def play_cadence_wav(self, path: str, blocking: bool) -> bool:
        """Play a rendered cadence wav on the board's speaker.

        The hold beeps go down the ordinary TTS PCM path rather than the media
        path, because they are Kiki timing something and must duck and cancel
        exactly like her voice does. `blocking` is honoured by the caller
        (`cadence.play_countdown` times the hold itself), so this only has to
        get the audio moving.
        """
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_wav(path), self.loop)
            if blocking:
                future.result(timeout=180)
            return True
        except Exception:
            LOG.exception("could not play the cadence track")
            return False

    async def _send_wav(self, path: str) -> None:
        """Stream a 24 kHz mono wav to the board as ordinary speech PCM."""
        import wave

        def read() -> tuple[bytes, int]:
            with wave.open(path, "rb") as handle:
                return handle.readframes(handle.getnframes()), handle.getframerate()

        pcm, rate = await asyncio.to_thread(read)
        if not pcm:
            return
        audio = np.frombuffer(pcm, dtype=np.int16)
        # The wav is 24 kHz (cadence.SAMPLE_RATE); the wire is 48 kHz. Doubling
        # each sample is enough for beeps -- these are pure tones with nothing
        # above 1.5 kHz, so there is no image worth filtering out.
        audio = np.repeat(audio, 2)
        self.tts_stream_id += 1
        stream_id = self.tts_stream_id
        chunk = 960  # 20 ms at 48 kHz
        sequence = 0
        for start in range(0, len(audio), chunk):
            if self.turn_abort.is_set():
                break
            payload = audio[start:start + chunk].tobytes()
            frame = AudioFrame(BinaryKind.TTS_PCM_S16_48K_MONO, 0, stream_id,
                               sequence, time.time_ns() // 1000, payload)
            await self.ws.send(frame.encode())
            sequence += 1
        await self.send_event("audio_end", kind="tts", stream_id=stream_id,
                              sequence=sequence)

    def start_due_care_session(self, event_id: str) -> None:
        """A care routine has fallen due. Speak it on the foreground loop.

        Called from the worker scheduler's thread, so it only schedules; the
        worker itself never speaks, never generates a participant reply, and
        never touches the microphone. That separation is the reason a care
        routine cannot talk over a conversation already in progress.
        """
        def start() -> None:
            task = asyncio.create_task(self._open_care_session(event_id))
            self.turn_tasks.add(task)
            task.add_done_callback(self.turn_tasks.discard)

        try:
            self.loop.call_soon_threadsafe(start)
        except Exception:
            LOG.exception("could not schedule care event %s", event_id)

    async def _open_care_session(self, event_id: str) -> None:
        """Give a due care session its first spoken turn."""
        runtime = self._care_runtime()
        if runtime is None:
            return
        if self.do_not_disturb:
            # Face down is absolute, and it outranks a schedule. The session
            # stays open in the plan; it simply is not spoken now.
            LOG.info("care event %s deferred: do not disturb", event_id)
            return
        if self.dance_active:
            LOG.info("care event %s deferred: a dance is running", event_id)
            return
        runtime.take_pending_session()
        async with self.turn_lock:
            if not runtime.session_active():
                LOG.info("care event %s is no longer active; nothing spoken",
                         event_id)
                return
            LOG.info("care event %s opening in the foreground", event_id)
            self._forget_ambient()
            await self._run_care_turn(runtime, "", time.perf_counter())

    async def _send_instructor(self, **fields) -> bool:
        """Visual delivery is best effort and cannot fail a spoken care turn."""
        try:
            await self.send_event("exercise_instructor", **fields)
            return True
        except Exception:
            LOG.warning("could not deliver instructor %s", fields.get("action"), exc_info=True)
            return False

    async def _run_care_turn(self, runtime, text: str, endpoint_at: float) -> None:
        """Conduct care turns until the session hands the microphone back.

        The loop exists because a guided routine is not turn-by-turn: Kiki
        gives an instruction, times a hold with the microphone MUTED, reads the
        motion recorded during that hold, and speaks again -- all without the
        person having to say anything. `expect_reply` is what ends the loop and
        opens the microphone, and the care agent is only allowed to set it
        after actually asking a question.
        """
        # Re-attach before the first turn rather than trusting connect-time
        # ordering. A board that connected BEFORE health mode was switched on
        # never got the hold player, the motion capture or the heart-rate
        # sender wired to it -- and the symptom is a silent hold that records
        # nothing, which looks exactly like a broken sensor.
        try:
            runtime.attach_session(self)
        except Exception:
            LOG.exception("could not attach the care stack to this session")
        self.turn_abort = self._new_turn_abort()
        turns = 0
        spoken_text = text
        tts_task = None
        last_demo_id = None
        try:
            while not self.turn_abort.is_set():
                turns += 1
                await self.set_state(state="thinking")
                self.playing = True
                self.tts_stream_id += 1
                self.tts_sequence = 0
                queue: asyncio.Queue = asyncio.Queue()
                tts_task = asyncio.create_task(
                    self._tts_worker(queue, endpoint_at))
                said: list[str] = []
                try:
                    reply, directive = await runtime.run_turn(
                        spoken_text, abort=self.turn_abort,
                        recent_texts=[text for _at, text in self.recent_ambient])
                except Exception:
                    LOG.exception("care turn failed")
                    reply, directive = (
                        "Something went wrong in the middle of that. "
                        "Shall we carry on?",
                        {"hold_seconds": 0, "expect_reply": True, "cue": ""})
                if self.turn_abort.is_set():
                    await queue.put(None)
                    await tts_task
                    break
                from .care.instructor import validate as validate_instructor
                demo = validate_instructor(directive.get("instructor"))
                hold = int(directive.get("hold_seconds") or 0)
                demo = demo if (hold > 0 and not directive.get("expect_reply", True)
                                and runtime.session_active()) else None
                if demo:
                    demo["demo_id"] = f"{self.session_id}:{self.tts_stream_id}"
                    last_demo_id = demo["demo_id"]
                    # Show the starting posture during speech; only start the
                    # movement when the board confirms the instruction drained.
                    if not await self._send_instructor(action="prepare", **demo):
                        demo = None
                for sentence in _care_sentences(reply):
                    said.append(sentence)
                    await self.send_event("response_sentence", text=sentence)
                    await queue.put((sentence, None))
                await queue.put(None)
                await tts_task
                await self.await_playback_drained(20.0)

                if self.turn_abort.is_set() or not runtime.session_active():
                    break
                if demo and self.playing:
                    # A timed-out wait is not confirmation that speech finished.
                    await self._send_instructor(action="stop", demo_id=last_demo_id)
                    demo = None

                record = getattr(self.core, "record_exchange", None)
                if record is not None and said:
                    try:
                        await asyncio.to_thread(
                            record,
                            "" if spoken_text == CARE_NO_REPLY else spoken_text,
                            " ".join(said))
                    except Exception:
                        LOG.exception("could not record the care turn")

                cue = str(directive.get("cue") or "")
                if cue:
                    await runtime.cue(cue)
                hold = int(directive.get("hold_seconds") or 0)
                if hold > 0:
                    # The microphone stays shut for the whole hold. This is
                    # what stops Kiki hearing her own beeps and answering them,
                    # and it is also the window the wrist is recorded across.
                    await self.set_state(state="thinking", detail=f"hold {hold}s")
                    try:
                        if demo and not self.turn_abort.is_set():
                            await self._send_instructor(action="start", seconds=min(hold, 120), **demo)
                        await runtime.hold(hold, self.turn_abort)
                    finally:
                        if demo:
                            await self._send_instructor(action="stop", demo_id=demo["demo_id"])

                if directive.get("expect_reply", True):
                    break
                if not runtime.session_active():
                    break
                # Nobody spoke; the routine continues under its own steam.
                spoken_text = CARE_NO_REPLY
                endpoint_at = time.perf_counter()
        finally:
            if tts_task is not None and not tts_task.done():
                tts_task.cancel()
                await asyncio.gather(tts_task, return_exceptions=True)
            if last_demo_id is not None:
                await self._send_instructor(action="stop", demo_id=last_demo_id)
            self.playing = False
            self.speech_playback_started_at = 0.0
            self._reset_barge_in()
            if runtime.session_active() and not self.turn_abort.is_set():
                # A session that is still going gets the microphone back with a
                # real window, because the person has just been asked something.
                self.awake = True
                self.followup_deadline = time.monotonic() + 15.0
                self.endpointer.reset(active=False)
                await self.set_state(state="listening")
            else:
                self.awake = False
                self.followup_deadline = 0.0
                self.endpointer.reset(active=False)
                await self.set_state(state="idle")
            LOG.info("care exchange finished session=%s turns=%d active=%s",
                     self.session_id, turns, runtime.session_active())

    async def _run_dance_fast_path(self, text: str) -> None:
        """Answer "kiki, dance" by dancing, without a model round trip.

        The dance tool speaks its own intro line and then the music is the
        reply, so there is nothing for a generation to add here -- and the
        seconds it would cost are seconds of silence before the song. The turn
        is still written into history afterwards, so the next thing the user
        says has the context that Kiki just started dancing.
        """
        LOG.info("dance fast path: %r", text[:80])
        result = await self.device.execute("dance", {"request": text})
        LOG.info("dance tool: %s", str(result)[:160])
        spoken = f"Okay -- dancing to {self.dance_title}." if self.dance_active else ""
        if not self.dance_active:
            spoken = ("I could not find a good track to dance to. "
                      "Name a song and I will try again.")
            await self.speak_background(spoken)
        record = getattr(self.core, "record_exchange", None)
        if record is not None:
            try:
                await asyncio.to_thread(record, text, spoken)
            except Exception:
                LOG.exception("could not record the dance turn in history")
        self.awake = False
        self.followup_deadline = 0.0

    async def respond(self, text: str, endpoint_at: float) -> None:
        # A live care session owns the conversation until it closes. This is
        # first because a guided routine has to be able to hear "stop", "it
        # hurts" and "play some music" without the ordinary router deciding
        # what they mean -- the care agent handles all three, and closing the
        # session is what hands the microphone back.
        care = self._care_session_owner()
        if care is not None:
            await self._run_care_turn(care, text, endpoint_at)
            return

        # Two triggers, one path (dance.py): this catches the plain spoken
        # request, and the `dance` tool in the model's catalog catches the rest.
        if (
            self.device is not None
            and getattr(self.config, "dance_enabled", True)
            and not self.dance_active
            and not self.do_not_disturb
            and dance.is_dance_request(text)
        ):
            await self._run_dance_fast_path(text)
            return

        # Never clear a previous turn's cancellation token. A tap on Stop used
        # to set it, then activate_query() cleared the same object while the
        # old TTS coroutine was still unwinding. That coroutine resumed and
        # emitted the tail of the old answer immediately before the new one.
        self.turn_abort = self._new_turn_abort()
        self.playing = True
        self.tts_stream_id += 1
        self.tts_sequence = 0
        observer = getattr(self.core, "observability", None)
        observed_turn = observer.start_turn(text) if observer is not None else None
        observed_started = time.monotonic()
        observed_abort = self.turn_abort
        await self.set_state(state="thinking")
        sentence_queue: asyncio.Queue[str | tuple[str, str | None] | None] = asyncio.Queue()
        spoken: list[str] = []
        tts_task = asyncio.create_task(self._tts_worker(sentence_queue, endpoint_at))
        try:
            async for event, data in self.core.stream_reply(text, self.turn_abort):
                if event == "sentence":
                    raw_sentence = str(data)
                    expression = last_expression_tag(raw_sentence)
                    sentence = SILENT_DISPLAY_TAGS.sub("", raw_sentence).strip()
                    if sentence:
                        spoken.append(sentence)
                        await self.send_event("response_sentence", text=sentence)
                        # The panel text is NOT shown here. A sentence is
                        # generated well before it is spoken, so displaying it
                        # now runs the caption ahead of the voice; _tts_worker
                        # schedules it for when its audio actually becomes
                        # audible.
                        # Keep the mood beside its sentence until TTS has PCM.
                        # Sending it here races ahead while the board is still
                        # showing `thinking`, where expression overrides are
                        # intentionally rejected.
                        await sentence_queue.put((sentence, expression))
                elif event == "tool_calls":
                    # The tool name picks the face's scene: a search gets the
                    # magnifying-glass pose, everything else "types".
                    calls = (data or {}).get("calls") if isinstance(data, dict) else None
                    name = calls[0].get("name", "") if calls else ""
                    await self.set_state(state="tool", detail=str(name)[:48])
                elif event == "tool_result":
                    await self.send_event("tool_result", result=str(data)[:4000])
                elif event == "error":
                    raise data
        except Exception as exc:
            LOG.exception("response failed")
            await self.send_event("error", code="response_failed", message=str(exc))
            if not spoken and not self.turn_abort.is_set():
                # A failed preflight/model request must not look like a mic
                # failure. Stop remains silent; actual failures get a response.
                notice = "I'm having trouble answering right now. Please try asking me again."
                spoken.append(notice)
                await sentence_queue.put(notice)
        finally:
            await sentence_queue.put(None)
            spoke = await tts_task
            if observer is not None:
                observer.finish_turn(observed_turn, " ".join(spoken),
                    int((time.monotonic() - observed_started) * 1000),
                    cancelled=observed_abort.is_set())
            after_response = getattr(self.core, "after_response", None)
            if after_response and not self.turn_abort.is_set():
                await after_response(text, " ".join(spoken))
            else:
                # An abandoned turn still has to do the housekeeping. Context
                # compaction lives inside after_response, so skipping it here
                # left the history over llama's window -- and the NEXT question
                # was then unanswerable: the prompt no longer fit, the model
                # returned nothing, and the panel dropped back to listening
                # having said nothing at all.
                #
                # Measured: Stop at 00:43:28 skipped this, the context stayed
                # at 7302 tokens against a 6300 limit, and the question at
                # 00:46:50 transcribed correctly and produced no reply.
                #
                # Not awaited: compaction is a cloud call that took twelve
                # seconds, and nothing about tearing this turn down should wait
                # for it.
                compact = getattr(self.core, "maybe_summarize", None)
                if compact:
                    task = asyncio.create_task(self._compact_quietly(compact))
                    self.turn_tasks.add(task)
                    task.add_done_callback(self.turn_tasks.discard)
            # A no-op unless this turn interrupted a song. If the turn itself
            # started one, stop() already cleared the duck and this leaves the
            # new track alone.
            if self.device:
                await self.device.unduck()
            if not spoke and not (self.device and self.device.media_active):
                # Say so. A question that transcribes correctly and then
                # produces no reply at all is the hardest failure here to see:
                # the log shows `turn endpoint`, then nothing, and the panel
                # just returns to listening. It took reading four minutes of
                # raw log to find it once. If it happens again this line is the
                # first thing a grep will land on.
                if not self.turn_abort.is_set():
                    LOG.warning(
                        "turn produced no reply session=%s text=%r -- check the "
                        "context size just above; a history over llama's window "
                        "cannot be answered",
                        self.session_id, text[:120],
                    )
                # A turn that said nothing and started nothing sends no PCM, so
                # the board never opens a playback session and never reports
                # `playback_drained` -- and that event is the ONLY thing that
                # clears `playing`. While it is set, handle_audio skips both the
                # VAD and the wake word, so Kiki goes deaf until the process
                # restarts. A failed `play_music` is exactly this shape: the
                # media tool speaks no reply, and then the request it failed at
                # cost you the microphone too.
                self.playing = False
                self.speech_playback_started_at = 0.0
                self._reset_barge_in()
                # A turn that produced no speech sends no PCM, so the board
                # never reports `playback_drained` -- which is the ONLY thing
                # that reopens the window below. Without this branch doing it
                # itself, a failed turn left Kiki deaf until the next tap: you
                # asked something, got nothing, and could not even ask again.
                if self.awake and self._followups_enabled():
                    # No endpointer reset here, unlike the drained path: this
                    # turn consumed no audio, so there is nothing buffered that
                    # would leak into the next utterance.
                    self.followup_deadline = time.monotonic() + 15.0
                    await self.set_state(state="listening")
                else:
                    self.awake = False
                    self.followup_deadline = 0.0
                    await self.set_state(state="idle")
            elif not self.awake:
                # A speaking turn keeps its window; `playback_drained` opens it
                # when the speaker actually goes quiet. Zeroing it here would
                # start the clock while the reply was still being played out,
                # so a long answer would spend the whole window before the
                # person had finished hearing the question in it.
                self.followup_deadline = 0.0

    async def ask_proactive_question(self, prompt: str, from_webui: bool = False) -> bool:
        """Generate and speak one autonomous question when the device is idle.

        This is deliberately a real conversational turn rather than background
        TTS: Kiki's persona/model phrases it, the question is retained in model
        context, and playback opens the ordinary follow-up listening window so
        Vaibhav can answer without saying the hotword.
        """

        streamer_name = "stream_web_instruction" if from_webui else "stream_proactive"
        streamer = getattr(self.core, streamer_name, None)
        media_live = bool(self.device and self.device.media_active)
        if (
            streamer is None
            or not prompt.strip()
            or self.do_not_disturb
            or self.playing
            or self.awake
            or self.push_to_talk_active
            or self.turn_lock.locked()
            or media_live
        ):
            LOG.info(
                "proactive question busy: dnd=%s playing=%s awake=%s ptt=%s turn=%s media=%s",
                self.do_not_disturb,
                self.playing,
                self.awake,
                self.push_to_talk_active,
                self.turn_lock.locked(),
                media_live,
            )
            return False

        async with self.turn_lock:
            media_live = bool(self.device and self.device.media_active)
            if (
                self.do_not_disturb
                or self.playing
                or self.awake
                or self.push_to_talk_active
                or media_live
            ):
                return False

            started = time.perf_counter()
            self.turn_abort = self._new_turn_abort()
            self.playing = True
            self.awake = True
            self.web_followup_pending = from_webui
            self.tts_stream_id += 1
            self.tts_sequence = 0
            await self.set_state(state="thinking", detail="I was thinking...")
            sentence_queue: asyncio.Queue[
                str | tuple[str, str | None] | None
            ] = asyncio.Queue()
            spoken: list[str] = []
            tts_task = asyncio.create_task(self._tts_worker(sentence_queue, started))
            try:
                async for event, data in streamer(prompt, self.turn_abort):
                    if event == "sentence":
                        raw_sentence = str(data)
                        expression = last_expression_tag(raw_sentence)
                        sentence = SILENT_DISPLAY_TAGS.sub("", raw_sentence).strip()
                        if sentence:
                            spoken.append(sentence)
                            await self.send_event("response_sentence", text=sentence)
                            await sentence_queue.put((sentence, expression))
                    elif event == "error":
                        raise data
            except Exception as exc:
                LOG.exception("proactive question failed")
                await self.send_event(
                    "error", code="proactive_question_failed", message=str(exc)
                )
            finally:
                await sentence_queue.put(None)
                sent_audio = await tts_task

            if not sent_audio:
                self.playing = False
                self.speech_playback_started_at = 0.0
                self._reset_barge_in()
                self.awake = False
                self.web_followup_pending = False
                self.followup_deadline = 0.0
                await self.set_state(state="idle")
                LOG.info("proactive question stayed silent")
                return False

            self.followup_deadline = time.monotonic() + 15.0
            LOG.info("proactive question spoken: %s", " ".join(spoken)[:300])
            return True

    async def speak_background(self, text: str, critical: bool = False) -> bool:
        # Every autonomous voice route ends here -- startup lines, worker and
        # idle-mind speech, the journal's movement questions -- so this one
        # check is what makes do-not-disturb mean it. Answering a question the
        # user actually asked still goes through respond().
        if not text.strip() or self.playing or (self.do_not_disturb and not critical):
            return False
        self.playing = True
        self.tts_stream_id += 1
        self.tts_sequence = 0
        self.turn_abort = self._new_turn_abort()
        await self.set_state(state="speaking")
        queue: asyncio.Queue[str | tuple[str, str | None] | None] = asyncio.Queue()
        expression = last_expression_tag(text)
        sentence = SILENT_DISPLAY_TAGS.sub("", text).strip()
        if sentence:
            await queue.put((sentence, expression))
        await queue.put(None)
        try:
            return await self._tts_worker(queue, time.perf_counter())
        except Exception as exc:
            # Background speech has no enclosing conversational turn to clean
            # it up. A truncated TTS HTTP stream used to leave `playing=True`
            # forever, making the gateway discard every microphone frame even
            # though the board had never received PCM.
            LOG.exception("background TTS failed")
            self.turn_abort.set()
            self._cancel_speech_text()
            self.playing = False
            self.speech_playback_started_at = 0.0
            self._reset_barge_in()
            await self.send_event("audio_stop", reason="background_tts_failed")
            await self.set_state(state="followup" if self.awake else "idle")
            await self.send_event(
                "error", code="background_tts_failed", message=str(exc)
            )
            return False

    def _schedule_speech_text(
        self, sentence: str, delay: float, expression: str | None = None
    ) -> None:
        """Apply the face mood and caption when this audio reaches the speaker."""

        abort = self.turn_abort

        async def show() -> None:
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                if abort.is_set():
                    return
                if expression:
                    # Expression first, caption second, in one coroutine. This
                    # preserves the Pi contract at sentence boundaries and
                    # prevents two zero-delay tasks from reordering on the loop.
                    await self.send_event("expression", name=expression)
                # The whole sentence, not a 16x2 slice: the panel has room for
                # it and the board picks a font that makes it fit. Converted for
                # display only -- TTS keeps the Devanagari and the tags.
                await self.send_event("speech", text=panel_text(sentence))
            except asyncio.CancelledError:
                return
            except Exception:
                LOG.debug("could not show speech text", exc_info=True)

        task = asyncio.create_task(show())
        self.speech_display_tasks.add(task)
        task.add_done_callback(self.speech_display_tasks.discard)

    def _cancel_speech_text(self) -> None:
        for task in list(self.speech_display_tasks):
            task.cancel()
        self.speech_display_tasks.clear()

    async def _tts_worker(self, sentences: asyncio.Queue, endpoint_at: float) -> bool:
        """Speak every queued sentence. Returns whether any PCM was sent."""
        # Both values belong to this worker for its entire lifetime. Reading
        # mutable session fields for every chunk lets an old worker inherit a
        # new turn's cleared abort flag or stream id after cancellation.
        abort = self.turn_abort
        stream_id = self.tts_stream_id
        sequence = 0
        first_pcm = True
        playback_started: float | None = None
        playback_seconds_sent = 0.0
        # Downlink accounting. The board can report that it underran, but only
        # this side can say whether the bytes ever had room to arrive: if the
        # wall clock spent sending a reply exceeds the reply's own duration, the
        # link is narrower than the audio and no amount of jitter buffer or send
        # lead can fix it. Without this number "she stutters" and "the link is
        # too slow" are indistinguishable from the log.
        bytes_sent = 0
        blocked_in_send = 0.0
        if self._opus is not None:
            # A new reply must not start from the previous one's carried
            # remainder; the board resets its decoder on the stream id change
            # to match.
            self._opus.reset()
        while True:
            item = await sentences.get()
            if item is None or abort.is_set():
                break
            if isinstance(item, tuple):
                sentence, expression = item
            else:
                # Kept for simple callers and older tests; all production LLM
                # sentences use the metadata tuple above.
                sentence, expression = str(item), None
            voice = self._current_tts_voice()
            sentence_scheduled = False
            pcm_stream = self.tts.stream(sentence, voice=voice)
            async for pcm in pcm_stream:
                if abort.is_set():
                    break
                if self.opus_out and self._opus is not None:
                    # 48k -> 16k -> Opus. The decimator and the encoder each
                    # carry their own remainder, so one PCM chunk can produce
                    # no packets at all or several.
                    narrow = self._narrow_tts(pcm)
                    payloads = self._opus.encode(narrow) if narrow else []
                    kind = BinaryKind.TTS_OPUS_16K_MONO
                elif self.narrowband_out:
                    narrow = self._narrow_tts(pcm)
                    payloads = [narrow] if narrow else []
                    kind = BinaryKind.TTS_PCM_S16_16K_MONO
                else:
                    payloads = [pcm]
                    kind = BinaryKind.TTS_PCM_S16_48K_MONO
                if not payloads:
                    # Nothing completed a frame yet, but the duration accounting
                    # still has to count these samples or the pacing runs fast.
                    playback_seconds_sent += len(pcm) / (2 * 48000)
                    continue
                schedule_now = not sentence_scheduled
                if schedule_now:
                    sentence_scheduled = True
                    # When this sentence's first chunk goes out, the audio
                    # already queued ahead of it is exactly the current lead, so
                    # it becomes audible that far in the future plus the board's
                    # own output latency. Showing either words or mood when the
                    # sentence is *generated* runs the panel ahead of Kiki.
                    if playback_started is None:
                        ahead = 0.0
                    else:
                        ahead = max(
                            0.0,
                            playback_seconds_sent - (time.perf_counter() - playback_started),
                        )
                if first_pcm:
                    first_pcm = False
                    self.speech_playback_started_at = time.monotonic()
                    self._reset_barge_in()
                    ttfw_ms = round((time.perf_counter() - endpoint_at) * 1000)
                    LOG.info(
                        "first PCM session=%s ttfw_ms=%d",
                        self.session_id,
                        ttfw_ms,
                    )
                    await self.send_event(
                        "first_pcm",
                        ttfw_ms=ttfw_ms,
                        sample_rate=16000 if self.narrowband_out else 48000,
                    )
                    # The board only permits moods to refine speaking/tool.
                    # Establish that base before the zero-delay display task
                    # can possibly send an expression event.
                    await self.set_state(state="speaking")
                if schedule_now:
                    self._schedule_speech_text(
                        sentence,
                        ahead + self.config.display_sync_offset_ms / 1000.0,
                        expression,
                    )
                if playback_started is None:
                    playback_started = time.perf_counter()
                playback_seconds_sent += len(pcm) / (2 * 48000)
                lead = playback_seconds_sent - (time.perf_counter() - playback_started)
                # Read live, not captured: a board that reports a stutter mid
                # reply gets the deeper cushion for the rest of that same reply.
                if lead > self.playback_lead:
                    await asyncio.sleep(lead - self.playback_lead)
                # Cancellation commonly happens during the pacing sleep. The
                # old check was before it, allowing one delayed PCM chunk to be
                # sent after audio_stop.
                if abort.is_set():
                    break
                for payload in payloads:
                    frame = AudioFrame(
                        kind,
                        0,
                        stream_id,
                        sequence,
                        time.time_ns() // 1000,
                        payload,
                    )
                    encoded = frame.encode()
                    send_started = time.perf_counter()
                    await self.ws.send(encoded)
                    # Time spent *inside* send() is time the socket refused to
                    # take more, i.e. genuine backpressure from a full send
                    # buffer. It is the cleanest signal we have that the wire,
                    # not the gateway, is the bottleneck.
                    blocked_in_send += time.perf_counter() - send_started
                    bytes_sent += len(encoded)
                    sequence += 1
                    if stream_id == self.tts_stream_id:
                        self.tts_sequence = sequence
            if abort.is_set():
                # `break` does not synchronously finalize an async generator.
                # Close it explicitly so TTSClient tears down the cancelled
                # HTTP stream before the synthesizer accepts the next reply.
                await pcm_stream.aclose()
        if self.opus_out and self._opus is not None and not abort.is_set():
            # The final partial frame. Without it the last few milliseconds of
            # every reply are dropped -- inaudible on its own, but the carry
            # would otherwise prefix the next reply, which is not.
            for payload in self._opus.flush():
                frame = AudioFrame(
                    BinaryKind.TTS_OPUS_16K_MONO,
                    0,
                    stream_id,
                    sequence,
                    time.time_ns() // 1000,
                    payload,
                )
                encoded = frame.encode()
                try:
                    await self.ws.send(encoded)
                except Exception:
                    LOG.debug("could not flush the opus tail", exc_info=True)
                    break
                bytes_sent += len(encoded)
                sequence += 1
        if bytes_sent and playback_started is not None:
            wall = time.perf_counter() - playback_started
            LOG.info(
                "reply downlink session=%s codec=%s audio=%.1fs wall=%.1fs "
                "bytes=%d goodput=%.0fkbps blocked_in_send=%.1fs%s",
                self.session_id,
                "opus" if self.opus_out else ("pcm16k" if self.narrowband_out else "pcm48k"),
                playback_seconds_sent,
                wall,
                bytes_sent,
                (bytes_sent * 8 / wall / 1000) if wall > 0 else 0.0,
                blocked_in_send,
                # The verdict, spelled out. Sending must finish faster than the
                # audio plays or the speaker has nothing to play from.
                "" if wall <= playback_seconds_sent else "  LINK TOO SLOW",
            )
            self.downlink_saturated = wall > playback_seconds_sent
        await self.send_event(
            "audio_end", kind="tts", stream_id=stream_id, sequence=sequence
        )
        return not first_pcm

    async def _panel_status_loop(self) -> None:
        # Reads only cached data. Does not call weather APIs or mutate history.
        try:
            while True:
                bridge = getattr(self.core, "health_bridge", None)
                snapshot = bridge.environment() if bridge else {}
                try:
                    messages = await asyncio.to_thread(recent_whatsapp)
                except Exception:
                    messages = None
                details = panel_details(snapshot, bridge.summary() if bridge else {}, messages)
                await self.send_event("dashboard", environment=environment_text(snapshot), **details)
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.debug("panel status loop ended", exc_info=True)

    async def close(self) -> None:
        if self.panel_status_task:
            self.panel_status_task.cancel()
            await asyncio.gather(self.panel_status_task, return_exceptions=True)
        self.turn_abort.set()
        await self._stop_health_delivery()
        self._cancel_speech_text()
        if self.display:
            # Restore the legacy hooks first: leaving a tap pointing at a dead
            # session would raise on every later status line.
            self.display.detach()
            self.display = None
        if self.background_start_task:
            self.background_start_task.cancel()
        if self.ota_watch_task:
            self.ota_watch_task.cancel()
        if self.motion_question_task:
            self.motion_question_task.cancel()
        for task in self.health_alert_tasks:
            task.cancel()
        if self.health_alert_tasks:
            await asyncio.gather(*self.health_alert_tasks, return_exceptions=True)
        if self.device:
            await self.device.close()
        # Detach, do not close. The runtime belongs to the process; this socket
        # does not. Tearing it down here is what lost the conversation, spent a
        # cloud summarization call and re-warmed llama on every dropped link.
        care = self._care_runtime()
        if care is not None:
            try:
                care.detach_session()
            except Exception:
                LOG.exception("could not detach the care stack")
        detach = getattr(self.core, "detach", None)
        if detach:
            detach(self)
        self.rnnoise.close()
        for task in list(self.spec_tasks.values()) + list(self.partial_tasks):
            task.cancel()
        for task in self.spec_prefill_tasks.values():
            task.cancel()
        for task in self.turn_tasks:
            task.cancel()
        if self.turn_tasks:
            await asyncio.gather(*self.turn_tasks, return_exceptions=True)
