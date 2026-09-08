"""
STT module for KikiFast voice assistant.
Low-latency local whisper.cpp transcription with CLIENT-SIDE Silero VAD.
Microphone PCM is enhanced continuously by RNNoise before VAD and Whisper.

This is the optimized pipeline ported from the standalone `whisper_tts.py`
benchmark client. It replaces the old "re-transcribe a growing rolling buffer
every 0.4s + RMS-silence endpoint" design (≈2s end→text) with a streaming
per-frame VAD endpointer and speculative finalization (≈200–300ms end→text).

How it cuts the latency:
  1. Capture       : a mic-reader thread pushes fixed 32ms (512-sample @16k)
                     frames — the exact window Silero requires.
  2. Streaming VAD : the Silero model is scored per-frame (O(1)) by a small
                     state machine that tracks utterance onset and trailing
                     silence, with a pre-roll ring buffer so the first phoneme
                     isn't clipped. ONE whisper request is sent per utterance
                     (vad:false on the server — the client already did VAD),
                     instead of re-transcribing the whole buffer every step.
  3. Speculative   : the instant trailing silence begins (spec_silence_ms) the
                     final ASR is fired *in the background* while we keep
                     listening. When silence crosses endpoint_ms we commit using
                     the already-in-flight result. Critical path becomes
                     max(endpoint_ms, asr_time) instead of silence_timeout + ASR.

The public API is unchanged (drop-in for main.py / vision / face handlers):
  - mute() / unmute() / is_muted — instant flag flips, no reconnect
  - stream() — generator yielding (event_type, data) tuples:
        ("interim", text)   — live "still talking" signal (keeps the listen
                              window alive + drives the LCD); text is "…" until
                              the final transcript lands
        ("final", text)     — the committed utterance transcript (never None)
        ("endpoint", None)  — end of utterance
  - stop() — release mic + threads

Server expected at config stt.whisper_url (default http://100.64.0.10:5555/inference).
"""

import threading
import queue
import io
import os
import sys
import tempfile
import time
import wave
import numpy as np
import pyaudio
import requests
from concurrent.futures import ThreadPoolExecutor

from core.near_field_gate import NearFieldGate
from core.noise_suppression import (
    RNNOISE_FRAME,
    RNNOISE_SAMPLE_RATE,
    RNNoiseSuppressor,
    RNNoiseUnavailable,
    StreamingDecimator3,
)
from tools_and_config.config_loader import get_stt_config

try:
    import torch
    from silero_vad import load_silero_vad
    torch.set_num_threads(1)  # the tiny VAD model must not contend for CPU threads
    _SILERO_OK = True
except ImportError as e:  # pragma: no cover - environment guard
    _SILERO_OK = False
    _SILERO_IMPORT_ERR = e

SAMPLE_RATE = 16000
FRAME = 512                          # Silero's required window @16k == 32ms
FRAME_MS = 1000.0 * FRAME / SAMPLE_RATE


def _audio_to_wav_bytes(audio_f32: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convert float32 PCM array to WAV bytes for the whisper server."""
    pcm = (np.clip(audio_f32, -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _compute_audio_ctx(n_samples: int) -> int:
    """Size the encoder context to the clip. The core asserts
    n_mel_frames <= audio_ctx*2, i.e. audio_ctx >= duration_s*50. Add 30%
    margin, round to 64, clamp [256, 1500]. Sizing it to the (short) utterance
    is a big part of why each request is fast."""
    duration_s = n_samples / SAMPLE_RATE
    ctx = int(np.ceil(duration_s * 50 * 1.3 / 64.0)) * 64
    return max(256, min(1500, ctx))


class _AsrClient:
    """Fires transcription requests on a small thread pool over a keep-alive Session."""

    def __init__(self, url, language, max_tokens, request_timeout, max_request_timeout, workers=2):
        self.url = url
        self.language = language
        self.max_tokens = max_tokens
        self.request_timeout = request_timeout
        self.max_request_timeout = max_request_timeout
        self.session = requests.Session()
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def _do(self, wav_bytes, n_samples):
        payload = {
            "response_format": "verbose_json",
            "temperature": "0",
            "language": "en",
            "vad": "false",            # client already did VAD; skip the server's loop
            "no_timestamps": "true",   # also disables token_timestamps server-side
            "single_segment": "true",
            # "best_of": "1",
            "max_tokens": str(self.max_tokens),
            # "audio_ctx": str(audio_ctx),
        }
        files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
        # Scale the read timeout with clip length — a 20s utterance legitimately
        # takes longer to transcribe than a 1s one. Connect timeout stays short.
        clip_s = n_samples / SAMPLE_RATE
        read_timeout = max(self.request_timeout, min(self.max_request_timeout, clip_s * 1.5 + 2.0))
        t0 = time.time()
        try:
            r = self.session.post(self.url, data=payload, files=files,
                                  timeout=(3.0, read_timeout))
            if r.status_code == 200:
                return r.json().get("text", "").strip(), time.time() - t0
            print(f"[STT] ⚠ Whisper server HTTP {r.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"[STT] ⚠ Whisper request error: {e}")
        return None, time.time() - t0

    def submit(self, audio_f32):
        """Fire a transcription in the background; returns a Future of (text, asr_seconds)."""
        wav_bytes = _audio_to_wav_bytes(audio_f32)
        n_samples = len(audio_f32)
        return self.pool.submit(self._do, wav_bytes, n_samples)

    def close(self):
        try:
            self.pool.shutdown(wait=False)
        except Exception:
            pass
        try:
            self.session.close()
        except Exception:
            pass


class _Endpointer:
    """Per-frame Silero VAD state machine with pre-roll + speculative finalization.

    Calls on_interim(text) for the "still talking" heartbeat and on_commit(text)
    once an utterance ends. All callbacks run on the VAD worker thread.
    """

    def __init__(self, model, cfg, asr, on_commit, on_interim,
                 on_speculative=None, on_spec_invalid=None, on_partial=None,
                 hold_event=None, context_provider=None):
        self.model = model
        self.cfg = cfg
        self.asr = asr
        self.on_commit = on_commit
        self.on_interim = on_interim
        # Push-to-talk (IR hand-hover): while set, silence NEVER commits the
        # utterance — the user may pause mid-thought for as long as the hand
        # stays on the sensor. Speculative ASR still fires during held silence
        # so the reply pipeline warms up; force_commit() ends the turn on
        # hand release. (The max_utterance safety valve still commits.)
        self.hold_event = hold_event if hold_event is not None else threading.Event()
        self.context_provider = context_provider
        # Speculative-turn hooks (all optional, run on ASR pool threads):
        #   on_speculative(text) — the speculative final ASR resolved while we
        #     are still inside the silence window; the turn pipeline may start
        #     early (audio is held until commit confirms the same text).
        #   on_spec_invalid()    — speech resumed after a speculative fire; any
        #     speculative turn must be aborted.
        #   on_partial(text)     — rolling mid-speech ASR of the utterance so
        #     far (drives incremental LLM prefill; never spoken/final).
        self.on_speculative = on_speculative
        self.on_spec_invalid = on_spec_invalid
        self.on_partial = on_partial

        # Crowd gate: Silero cannot tell the user apart from the room, because
        # background babble is genuinely speech. This adds the level test that
        # does distinguish them, and stays inert in a quiet room.
        self.gate = NearFieldGate(cfg.get("near_field_gate", {}), frame_ms=FRAME_MS)
        self._gate_logged = False

        self.preroll_frames = max(1, int(cfg["preroll_ms"] / FRAME_MS))
        self.interim_interval_s = 1.0      # heartbeat cadence while speaking

        self.preroll = []                  # ring buffer of recent frames while idle
        self.in_speech = False
        self.utt = []                      # frames for the active utterance
        self.speech_ms = 0.0
        self.silence_ms = 0.0
        self.spec_future = None            # in-flight speculative ASR future
        self.last_interim_at = 0.0
        self.spec_gen = 0                  # bumped on every fire/discard/reset;
                                           # guards stale ASR-pool callbacks
        self.spec_announced = False        # an on_speculative went out for the
                                           # current spec_future
        self.last_partial_ms = 0.0         # speech_ms at the last partial ASR
        self.partial_busy = False
        self.last_asr_ms = 0.0             # measured cost of the last ASR call;
                                           # gates partials so a slow whisper
                                           # never queues the FINAL spec ASR
                                           # behind a mid-speech partial
        self.utterance_context = None

    def _fire_speculative(self):
        audio = np.concatenate(self.utt) if self.utt else np.zeros(0, np.float32)
        self.spec_gen += 1
        self.spec_announced = False
        gen = self.spec_gen
        fut = self.asr.submit(audio)
        self.spec_future = fut
        if self.on_speculative is not None:
            fired_at = time.time()

            def _announce(f, gen=gen):
                # Only announce if this future is still the live speculation
                # (speech has not resumed, no reset happened) — checked via the
                # generation stamp. _commit's blocking result() may race this
                # callback; that's fine, main ignores speculative events that
                # arrive after the endpoint.
                if gen != self.spec_gen:
                    return
                try:
                    text, _asr_s = f.result()
                except Exception:
                    return
                self.last_asr_ms = _asr_s * 1000
                print(f"[T] stt_spec_asr {_asr_s*1000:.0f}ms "
                      f"(ready +{(time.time()-fired_at)*1000:.0f}ms after fire)")
                if text:
                    self.spec_announced = True
                    self.on_speculative(text)
            fut.add_done_callback(_announce)

    def _fire_partial(self):
        """Rolling mid-speech ASR of the utterance so far (for LLM prefill)."""
        if self.partial_busy or self.on_partial is None:
            return
        audio = np.concatenate(self.utt) if self.utt else None
        if audio is None:
            return
        self.partial_busy = True
        gen_utt = self.spec_gen  # reset bumps this too, so it doubles as an utterance stamp
        fut = self.asr.submit(audio)
        def _deliver(f):
            self.partial_busy = False
            try:
                text, _asr_s = f.result()
            except Exception:
                return
            self.last_asr_ms = _asr_s * 1000
            if not self.in_speech or gen_utt != self.spec_gen:
                return
            if text:
                self.on_partial(text)
        fut.add_done_callback(_deliver)

    def _commit(self):
        """Returns True if a final transcript was emitted (on_commit fired)."""
        # Too little speech → almost certainly noise. Drop silently (no endpoint).
        if self.speech_ms < self.cfg["min_speech_ms"]:
            self._reset()
            return False
        if self.spec_future is None:           # no speculative fired yet; fire now
            self._fire_speculative()
        _t_wait = time.time()
        text, _asr_s = self.spec_future.result()
        _wait_ms = (time.time() - _t_wait) * 1000
        if _wait_ms > 5:
            # >5ms means the endpoint was gated on ASR, not the silence window —
            # if this is frequently large, the spec ASR queued behind a partial
            # on the serialized whisper server (or ASR is just slow).
            print(f"[T] stt_commit_blocked_on_asr {_wait_ms:.0f}ms")
        utterance_context = self.utterance_context
        self._reset()
        # Only surface a real transcript. An empty/failed result resets silently
        # so main.py never gets a None "final" or a stranded bare "endpoint".
        if text:
            if self.context_provider is None:
                self.on_commit(text)
            else:
                self.on_commit(text, utterance_context)
            return True
        return False

    def force_commit(self):
        """Push-to-talk hand release: end the utterance NOW instead of waiting
        out the silence window. Returns True if a final was emitted."""
        if not self.in_speech:
            self._reset()
            return False
        return self._commit()

    def _reset(self):
        self.model.reset_states()
        # Close the gate but keep the learned floor: the room is still the same
        # room, and re-learning it would leave the gate open for the first
        # seconds of the next utterance — exactly when the crowd needs rejecting.
        self.gate.reset(keep_floor=True)
        self.in_speech = False
        self.utt = []
        self.preroll = []
        self.speech_ms = 0.0
        self.silence_ms = 0.0
        self.spec_future = None
        self.last_interim_at = 0.0
        self.spec_gen += 1              # invalidate in-flight ASR callbacks
        self.spec_announced = False
        self.last_partial_ms = 0.0
        self.utterance_context = None

    def reset(self):
        """Public reset (used on mute/unmute flush)."""
        self._reset()

    def process(self, frame, now):
        """Feed one 512-sample float32 frame captured at wall-clock `now`."""
        prob = self.model(torch.from_numpy(frame), SAMPLE_RATE).item()
        is_speech = prob >= self.cfg["vad_threshold"]

        # In a crowd every frame is "speech", so silence_ms would never grow and
        # the utterance would never end. Require near-field level too — but only
        # once the gate has decided the room is actually noisy.
        #
        # The floor keeps tracking during push-to-talk, but the gate's verdict is
        # ignored: a hand deliberately held on the IR sensor is unambiguous
        # intent, and gating a quiet user out there would capture nothing at all
        # and make hold-to-talk useless in exactly the rooms it exists for.
        near_field = self.gate.update(frame, is_speech)
        voiced = is_speech and (near_field or self.hold_event.is_set())
        if self.gate.engaged and not self._gate_logged:
            self._gate_logged = True
            print(f"[STT] 🔊 Noisy room — near-field gate engaged ({self.gate.describe()})")
        elif self._gate_logged and not self.gate.engaged:
            self._gate_logged = False
            print("[STT] 🔈 Room quiet again — near-field gate inert")

        if not self.in_speech:
            self.preroll.append(frame)
            if len(self.preroll) > self.preroll_frames:
                self.preroll.pop(0)
            if voiced:
                self.in_speech = True
                if self.context_provider is not None:
                    self.utterance_context = self.context_provider()
                self.utt = list(self.preroll) + [frame]
                self.preroll = []
                self.speech_ms = FRAME_MS
                self.silence_ms = 0.0
                # Onset heartbeat: wakes the LCD + resets main.py's 15s mute timer.
                self.on_interim("…")
                self.last_interim_at = now
            return

        # in_speech
        self.utt.append(frame)
        if voiced:
            self.speech_ms += FRAME_MS
            self.silence_ms = 0.0
            if self.spec_future is not None:   # speech resumed → discard stale speculation
                self.spec_future = None
                self.spec_gen += 1             # stale ASR result must not announce
                if self.spec_announced and self.on_spec_invalid is not None:
                    self.spec_announced = False
                    self.on_spec_invalid()
            # Rolling partial ASR while the user is still talking, so the LLM
            # prefill of their words can overlap the speech itself. Gated on
            # the MEASURED ASR cost: whisper is serialized, so a partial that
            # is still running when the user stops would queue the final spec
            # ASR behind it and delay the endpoint commit. Only fire partials
            # when ASR is fast enough that a collision costs less than it saves.
            if (self.cfg.get("partial_asr_interval_ms", 0) > 0 and
                    self.speech_ms - self.last_partial_ms >= self.cfg["partial_asr_interval_ms"] and
                    self.last_asr_ms <= self.cfg["partial_asr_interval_ms"] * 0.4):
                self.last_partial_ms = self.speech_ms
                self._fire_partial()
            # Periodic heartbeat so a long continuous monologue keeps the listen
            # window alive (main.py mutes after 15s with no interim/final).
            if now - self.last_interim_at >= self.interim_interval_s:
                self.on_interim("…")
                self.last_interim_at = now
        else:
            self.silence_ms += FRAME_MS
            # min_speech guard: sub-min utterances commit silently as noise —
            # never announce a speculative turn for them.
            if (self.spec_future is None and self.silence_ms >= self.cfg["spec_silence_ms"]
                    and self.speech_ms >= self.cfg["min_speech_ms"]):
                self._fire_speculative()
            if (self.silence_ms >= self.cfg["endpoint_ms"]
                    and not self.hold_event.is_set()):
                self._commit()
                return

        # Hard safety: a pause-less monologue must still end so main.py can reply.
        if (self.speech_ms + self.silence_ms) / 1000.0 >= self.cfg["max_utterance_seconds"]:
            self._commit()


# The one live engine in this process. main.py builds it; tools that need
# microphone audio (record_voice_note) look it up here rather than opening a
# second PyAudio stream on a device this engine already owns.
_ACTIVE_ENGINE = None


def get_active_engine():
    """Return the live STTEngine, or None before main.py has built one."""
    return _ACTIVE_ENGINE


class STTEngine:
    """
    Streaming speech-to-text engine: local Silero VAD endpointing + whisper.cpp.
    Supports mute/unmute to prevent speaker feedback during TTS playback.
    ``set_capture_mode()`` labels finalized events as either explicit query speech
    or passive ambient speech so the orchestrator can keep one capture pipeline
    open without accidentally answering room conversation.

    Threads (all started by stream()):
      - mic_reader   : reads raw 512-sample PCM frames continuously → frame queue.
                       Keeps draining ALSA even while muted (zero-latency unmute).
      - vad_worker   : pulls frames, runs the Silero endpointer, fires speculative
                       ASR, and pushes ("interim"/"final"/"endpoint") events.
                       A slow/dead server never blocks audio capture.
      - stream()     : drains the event queue and yields events.
    """

    def __init__(self):
        cfg = get_stt_config()
        self.device_index = cfg.get("device_index", 2)
        self.channels = cfg.get("channels", 1)
        # Silero requires 16kHz mono; the 512-frame window is fixed (== 32ms).
        self.sample_rate = SAMPLE_RATE

        # Whisper server settings
        self.whisper_url = cfg.get("whisper_url", "http://100.64.0.10:5555/inference")
        self.language = cfg.get("language", "en")
        self.max_tokens = int(cfg.get("max_tokens", 128))
        self.request_timeout = float(cfg.get("request_timeout", 3.0))
        self.max_request_timeout = float(cfg.get("max_request_timeout", 15.0))

        # Endpointer tuning (new client-side VAD knobs; sensible low-latency defaults).
        self._ep_cfg = {
            "vad_threshold": float(cfg.get("vad_threshold", 0.5)),
            "endpoint_ms": int(cfg.get("endpoint_ms", 200)),
            # Fire the final ASR early in the silence window: a discarded result
            # only costs idle GPU time, and every ms it resolves before the
            # endpoint is a ms the speculative turn pipeline starts sooner.
            "spec_silence_ms": int(cfg.get("spec_silence_ms", 48)),
            # Reuse the existing min-speech knob; default tuned for short clips.
            "min_speech_ms": int(cfg.get("vad_min_speech_ms", 150)),
            "preroll_ms": int(cfg.get("preroll_ms", 250)),
            "max_utterance_seconds": float(cfg.get("max_utterance_seconds", 20.0)),
            # Rolling mid-speech ASR cadence (0 disables) for incremental prefill.
            "partial_asr_interval_ms": int(cfg.get("partial_asr_interval_ms", 1000)),
            # Crowded-room gate (see core/near_field_gate.py). Inert at home.
            "near_field_gate": cfg.get("near_field_gate", {}),
        }
        self._query_partial_interval_ms = self._ep_cfg["partial_asr_interval_ms"]

        # Neural noise suppression runs continuously in native 10 ms RNNoise
        # frames while speech is being captured.  It never adds a whole-clip
        # denoising job after endpoint, preserving time-to-first-word.
        ns_cfg = cfg.get("noise_suppression", {})
        self._ns_enabled = bool(ns_cfg.get("enabled", True))
        self._ns_max_process_ms = float(ns_cfg.get("max_process_ms", 5.0))
        self._ns_slow_frame_limit = int(ns_cfg.get("slow_frame_limit", 3))
        self._noise_suppressor = None
        self._noise_decimator = None

        self.debug = cfg.get("debug", False)

        self._muted = threading.Event()             # When set, audio is NOT processed
        self._stop = threading.Event()
        self._flush_requested = threading.Event()   # Set on unmute to discard stale audio
        self._capture_flush_requested = threading.Event()
        self._reset_requested = threading.Event()   # Reset VAD without draining new frames
        self._hold = threading.Event()              # push-to-talk: block endpoint commits
        self._force_commit = threading.Event()      # commit NOW (hand released)
        self._evt_queue = queue.Queue()
        self._frame_queue = queue.Queue()
        self._audio = None
        self._stream = None
        self._model = None
        self._asr = None
        self._capture_mode = "query"
        # Voice-note capture tap. PyAudio is opened ONCE and this engine owns
        # the device — a second stream would fail with "Device or resource
        # busy" — so recording a clip means teeing the frames the mic_reader
        # thread is already pulling, not opening the mic again.
        self._capture_sink = None
        self._capture_lock = threading.Lock()

        global _ACTIVE_ENGINE
        _ACTIVE_ENGINE = self

    def mute(self):
        """Mute the microphone (stop processing audio)."""
        self._muted.set()
        print("[STT] 🔇 Mic muted")

    def unmute(self):
        """Unmute the microphone (resume processing audio)."""
        # Request a flush BEFORE clearing muted so the VAD worker discards any
        # audio captured while Kiki was speaking (prevents the tail of TTS being
        # echoed back as a transcription) and resets the endpointer state.
        self._capture_flush_requested.set()
        self._flush_requested.set()
        self._muted.clear()
        print("[STT] 🔊 Mic unmuted")

    @property
    def is_muted(self):
        return self._muted.is_set()

    def set_capture_mode(self, mode: str):
        """Route subsequent STT events as ``query`` or ``ambient``.

        A mode switch resets the active VAD utterance but deliberately does not drain
        queued microphone frames.  This gives wake-word activation a clean boundary
        while retaining the words spoken immediately after "Kiki".
        """
        if mode not in ("query", "ambient"):
            raise ValueError("capture mode must be 'query' or 'ambient'")
        if mode != self._capture_mode:
            self._capture_mode = mode
            # Rolling partial ASR exists only to prefill an imminent spoken reply.
            # Ambient capture needs one final ASR request per utterance, not repeated
            # mid-speech requests throughout the day.
            self._ep_cfg["partial_asr_interval_ms"] = (
                self._query_partial_interval_ms if mode == "query" else 0
            )
            self._reset_requested.set()
            print(f"[STT] Capture mode: {mode}")

    def hold_open(self):
        """Push-to-talk hold (IR hand-hover): while set, the endpointer never
        commits on silence — the user talks/pauses freely until the hand lifts."""
        self._force_commit.clear()
        self._hold.set()
        print("[STT] ✋ Hold-to-talk open (endpoint suspended)")

    def hold_release(self, commit=True):
        """End of push-to-talk. commit=True force-commits the utterance
        immediately (no silence-window wait); commit=False just drops the hold
        (the settings menu took over — the mic gets muted right after)."""
        if commit:
            self._force_commit.set()
        self._hold.clear()
        print(f"[STT] ✋ Hold released ({'committing now' if commit else 'no commit'})")

    def commit_now(self):
        """End the current utterance immediately, whatever the VAD thinks.

        This is the explicit "I'm done talking" signal (thumbs-up gesture). It
        is the same mechanism as a push-to-talk hand release, minus the hold
        bookkeeping: in a crowded room the endpointer may never see enough
        trailing silence to commit on its own, so the user gets to say when.
        """
        self._hold.clear()
        self._force_commit.set()
        print("[STT] 👍 Commit requested — ending the utterance now")

    def record_clip(self, seconds=10.0, path=None, stop_event=None):
        """Record `seconds` of microphone audio and write it as a 16 kHz wav.

        Used by the `record_voice_note` tool so Kiki can send a WhatsApp voice
        note of what the user says next. This taps the frames the mic_reader
        thread is already reading — opening a second PyAudio input stream on
        the same device fails with "Device or resource busy".

        Recording works regardless of mute state (mute drops frames further
        down the pipeline, it does not stop capture), and it does NOT disturb
        the VAD/endpointer, so a clip cannot corrupt an in-flight utterance.

        Returns the wav path. Raises RuntimeError if capture never started or
        another recording is already running.
        """
        seconds = max(1.0, min(float(seconds), 60.0))
        if self._stream is None:
            raise RuntimeError("microphone is not open yet")

        with self._capture_lock:
            if self._capture_sink is not None:
                raise RuntimeError("a voice note is already being recorded")
            sink = []
            self._capture_sink = sink

        try:
            print(f"[STT] ⏺ Recording a {seconds:.0f}s clip…")
            deadline = time.time() + seconds
            while time.time() < deadline:
                if self._stop.is_set():
                    break
                if stop_event is not None and stop_event.is_set():
                    print("[STT] ⏹ Recording stopped early")
                    break
                time.sleep(0.05)
        finally:
            with self._capture_lock:
                self._capture_sink = None

        if not sink:
            raise RuntimeError("no audio was captured from the microphone")

        audio = np.concatenate(sink)
        if path is None:
            fd, path = tempfile.mkstemp(prefix="kiki_voice_", suffix=".wav")
            os.close(fd)
        with open(path, "wb") as fh:
            fh.write(_audio_to_wav_bytes(audio, self.sample_rate))
        print(f"[STT] ⏹ Recorded {len(audio) / self.sample_rate:.1f}s → {path}")
        return str(path)

    def stream(self):
        """
        Generator that yields transcription events.

        Yields:
            tuple: (event_type, data)
                   event_type: 'interim' | 'final' | 'endpoint'
                   data: transcript string for 'interim'/'final', None for 'endpoint'
        """
        if not _SILERO_OK:
            print(f"[STT] ❌ silero-vad/torch not installed: {_SILERO_IMPORT_ERR}\n"
                  f"      Run: pip install torch silero-vad", file=sys.stderr)
            self._evt_queue.put(("error", "silero-vad/torch not installed"))
            return

        # --- Load the VAD model + ASR client (once) ---
        print("[STT] Loading Silero VAD model (local CPU)...")
        self._model = load_silero_vad()
        self._asr = _AsrClient(self.whisper_url, self.language, self.max_tokens,
                               self.request_timeout, self.max_request_timeout)

        # Load RNNoise once at STT startup, never on the wake/response path.
        # Its native 48 kHz capture avoids upsampling 16 kHz microphone audio.
        capture_rate = self.sample_rate
        capture_frame = FRAME
        if self._ns_enabled:
            try:
                self._noise_suppressor = RNNoiseSuppressor(
                    max_process_ms=self._ns_max_process_ms,
                    slow_frame_limit=self._ns_slow_frame_limit,
                )
                self._noise_decimator = StreamingDecimator3()
                capture_rate = RNNOISE_SAMPLE_RATE
                capture_frame = RNNOISE_FRAME
                print(
                    "[STT] ✅ RNNoise neural suppression ready "
                    f"({capture_frame / capture_rate * 1000:.0f}ms streaming frames)"
                )
            except RNNoiseUnavailable as exc:
                print(f"[STT] ⚠ RNNoise unavailable; using raw microphone audio: {exc}")

        # --- PyAudio opened ONCE; output remains 512 samples @ 16 kHz for Silero ---
        self._audio = pyaudio.PyAudio()
        stream_kwargs = {
            "format": pyaudio.paInt16,
            "channels": self.channels,
            "rate": capture_rate,
            "input": True,
            "frames_per_buffer": capture_frame,
        }
        if self.device_index is not None:
            try:
                self._audio.get_device_info_by_index(self.device_index)
                stream_kwargs["input_device_index"] = self.device_index
            except Exception as e:
                print(f"[STT] Warning: device_index {self.device_index} is invalid ({e}). "
                      f"Falling back to default device.")

        try:
            self._stream = self._audio.open(**stream_kwargs)
        except Exception as exc:
            # Some USB microphones expose only 16 kHz.  Fall back without
            # preventing Kiki from listening.
            if self._noise_suppressor is None:
                raise
            print(
                f"[STT] ⚠ Microphone cannot capture 48 kHz ({exc}); "
                "using raw 16 kHz audio"
            )
            self._noise_suppressor.close()
            self._noise_suppressor = None
            self._noise_decimator = None
            capture_rate = self.sample_rate
            capture_frame = FRAME
            stream_kwargs["rate"] = capture_rate
            stream_kwargs["frames_per_buffer"] = capture_frame
            self._stream = self._audio.open(**stream_kwargs)

        # --- Mic reader thread: suppresses continuously, then emits 512 @ 16k ---
        def mic_reader():
            pcm_16k = np.zeros(0, dtype=np.float32)
            bypass_logged = False
            while not self._stop.is_set():
                try:
                    if self._capture_flush_requested.is_set():
                        self._capture_flush_requested.clear()
                        pcm_16k = np.zeros(0, dtype=np.float32)
                        if self._noise_decimator is not None:
                            self._noise_decimator.reset()
                        if (
                            self._noise_suppressor is not None
                            and self._noise_suppressor.active
                        ):
                            try:
                                self._noise_suppressor.reset()
                            except RNNoiseUnavailable as exc:
                                self._noise_suppressor.active = False
                                self._noise_suppressor.bypass_reason = (
                                    f"reset failed: {exc}"
                                )

                    data = self._stream.read(
                        capture_frame, exception_on_overflow=False
                    )
                    samples_i16 = np.frombuffer(data, dtype=np.int16)
                    if len(samples_i16) < capture_frame:
                        samples_i16 = np.pad(
                            samples_i16, (0, capture_frame - len(samples_i16))
                        )
                    elif len(samples_i16) > capture_frame:
                        samples_i16 = samples_i16[:capture_frame]

                    if self._noise_suppressor is not None:
                        # While muted, bypass the neural work unless record_clip
                        # is actively collecting a voice note.  State is reset
                        # at unmute, so Kiki's TTS cannot pollute noise history.
                        if not self._muted.is_set() or self._capture_sink is not None:
                            samples_48k, _speech_prob = self._noise_suppressor.process(
                                samples_i16
                            )
                        else:
                            samples_48k = samples_i16.astype(np.float32) / 32768.0
                        if (
                            not self._noise_suppressor.active
                            and not bypass_logged
                        ):
                            bypass_logged = True
                            print(
                                "[STT] ⚠ RNNoise real-time guard bypassed "
                                f"suppression ({self._noise_suppressor.bypass_reason})"
                            )
                        new_16k = self._noise_decimator.process(samples_48k)
                    else:
                        new_16k = samples_i16.astype(np.float32) / 32768.0

                    pcm_16k = np.concatenate((pcm_16k, new_16k))
                    while pcm_16k.size >= FRAME:
                        samples = np.ascontiguousarray(pcm_16k[:FRAME])
                        pcm_16k = pcm_16k[FRAME:]
                        # Voice-note tap stays before the VAD mute check: clips
                        # work while Kiki is mid-turn and now benefit from the
                        # same denoising as transcription.
                        sink = self._capture_sink
                        if sink is not None:
                            sink.append(samples)
                        self._frame_queue.put(samples)
                except Exception as e:
                    if not self._stop.is_set():
                        print(f"[STT] ⚠ Mic read error: {e}")
                    break

        # --- VAD worker: endpointing + speculative ASR; pushes events ---
        def event_name(name, capture_mode=None):
            mode = capture_mode or self._capture_mode
            return name if mode == "query" else f"ambient_{name}"

        def on_commit(text, capture_mode):
            # final must never be None; endpoint follows so main.py processes the turn.
            # Use the mode captured at speech onset. A wake word can arrive while
            # the final ambient ASR request is still resolving; that tail must not
            # become the first explicit user query by racing the mode switch.
            self._evt_queue.put((event_name("final", capture_mode), text))
            self._evt_queue.put((event_name("endpoint", capture_mode), None))
            if self.debug:
                print(f"[STT debug] Commit: {text!r}")

        def on_interim(text):
            # Ambient heartbeats/partials have no UI or speculative-reply consumer;
            # suppress them to keep the asyncio queue quiet during all-day capture.
            if self._capture_mode == "query":
                self._evt_queue.put(("interim", text))

        # Speculative-turn events (consumed by main.py; harmless if ignored).
        def on_speculative(text):
            if self._capture_mode == "query":
                self._evt_queue.put(("speculative", text))

        def on_spec_invalid():
            if self._capture_mode == "query":
                self._evt_queue.put(("spec_invalid", None))

        def on_partial(text):
            if self._capture_mode == "query":
                self._evt_queue.put(("partial", text))

        ep = _Endpointer(self._model, self._ep_cfg, self._asr, on_commit, on_interim,
                         on_speculative=on_speculative,
                         on_spec_invalid=on_spec_invalid,
                         on_partial=on_partial,
                         hold_event=self._hold,
                         context_provider=lambda: self._capture_mode)

        def vad_worker():
            while not self._stop.is_set():
                # Wake-word / IR cutover between passive and explicit-query speech.
                # Do not drain frames: words immediately following the wake word may
                # already be queued and must remain available to the new mode.
                if self._reset_requested.is_set():
                    self._reset_requested.clear()
                    ep.reset()

                # Flush on the muted→unmuted transition: drop buffered frames +
                # reset the endpointer so stale (TTS-era) audio is discarded.
                if self._flush_requested.is_set():
                    self._flush_requested.clear()
                    self._drain_frames()
                    ep.reset()
                    if self.debug:
                        print("[STT debug] Flushed on unmute")

                # Push-to-talk hand release → commit the utterance immediately.
                # If nothing committable was captured, still surface a bare
                # endpoint so main.py can flush any sentences it collected.
                if self._force_commit.is_set():
                    self._force_commit.clear()
                    try:
                        if not ep.force_commit():
                            self._evt_queue.put(("endpoint", None))
                    except Exception as e:
                        print(f"[STT] ⚠ Forced commit error: {e}")
                        ep.reset()
                        self._evt_queue.put(("endpoint", None))

                try:
                    frame = self._frame_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                # While muted, keep draining frames but don't process them.
                if self._muted.is_set():
                    if ep.in_speech:
                        ep.reset()
                    continue

                try:
                    ep.process(frame, time.time())
                except Exception as e:
                    print(f"[STT] ⚠ VAD/endpoint error: {e}")
                    ep.reset()

        mic_thread = threading.Thread(target=mic_reader, daemon=True)
        mic_thread.start()
        vad_thread = threading.Thread(target=vad_worker, daemon=True)
        vad_thread.start()

        # Verify the whisper server is reachable at startup
        try:
            self._asr.session.get(self.whisper_url.replace("/inference", "/"), timeout=3)
            print(f"[STT] ✅ Whisper server reachable ({self.whisper_url})")
        except Exception:
            print(f"[STT] ⚠ WARNING: Whisper server not reachable at {self.whisper_url} "
                  f"— STT will not work until the server is up!")

        print(f"[STT] Local whisper.cpp STT started (client-side Silero VAD)")
        print(f"[STT]   Server: {self.whisper_url}")
        print(f"[STT]   Endpoint silence: {self._ep_cfg['endpoint_ms']}ms | "
              f"Speculative at: {self._ep_cfg['spec_silence_ms']}ms")

        try:
            while not self._stop.is_set():
                try:
                    evt = self._evt_queue.get(timeout=0.1)
                    yield evt
                except queue.Empty:
                    continue
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _drain_frames(self):
        try:
            while True:
                self._frame_queue.get_nowait()
        except queue.Empty:
            pass

    def stop(self):
        """Stop the STT engine and clean up resources."""
        self._stop.set()
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._audio:
            try:
                self._audio.terminate()
            except Exception:
                pass
        if self._asr:
            self._asr.close()
        if self._noise_suppressor:
            self._noise_suppressor.close()
            self._noise_suppressor = None
        print("[STT] Engine stopped")


if __name__ == "__main__":
    print("=== STT Module Test (Local Whisper + client-side Silero VAD) ===")
    print("Listening... Press Ctrl+C to stop.\n")

    engine = STTEngine()
    collected = []

    try:
        for event, text in engine.stream():
            if event == "interim":
                print(f"  Interim: {text}", end='\r')
            elif event == "final":
                print(f"  Final: {text}          ")
                collected.append(text)
            elif event == "endpoint":
                if collected:
                    utterance = " ".join(collected)
                    print(f"\n>>> [ENDPOINT] Full: {utterance}\n")
                    collected = []
            elif event == "error":
                print(f"  Error: {text}")
                break
    except KeyboardInterrupt:
        engine.stop()
