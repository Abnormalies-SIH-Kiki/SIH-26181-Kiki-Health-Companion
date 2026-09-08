"""
TTS module for KikiFast voice assistant.
Supports Groq API, Inworld API, and the local omnivoice.cpp tts-server.

Configured via tools_and_config/config.json using the "provider" key
("groq", "inworld" or "local").
Client and config pre-initialized at import time for minimum latency.
"""

from pathlib import Path
import os
import re
import array
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from queue import Empty, Queue
import asyncio
import base64
import json
import time

import requests

import sys
import os

# Define the path you want to add.
# Use os.path.abspath and os.path.join for cross-platform compatibility.
new_library_path = str(Path(__file__).resolve().parents[1])

# Append the new path to sys.path
sys.path.append(new_library_path)


try:
    import websockets
except ImportError:
    websockets = None


try:
    from groq import Groq
except ImportError:
    Groq = None


from tools_and_config.config_loader import get_full_config, get_tts_config
from core.audio_output import ensure_bluetooth_sink, playback_environment
from core.gesture_controls import is_output_muted
from core import speech_recorder
from core.tts_sync import (
    LiveDisplaySegment, TimingCalibration, monotonic_time,
    romanize_hindi_for_lcd,
)

# --- Pre-initialize at import time ---
_TTS_CFG = get_tts_config()
_tts_provider = _TTS_CFG.get("provider", "groq").lower()

_api_key = os.getenv("GROQ_API_KEY")
if not _api_key or not Groq:
    # Do not raise exception at import in case only inworld is needed
    _client = None
else:
    _client = Groq(api_key=_api_key)

_MODEL = _TTS_CFG.get("model", "canopylabs/orpheus-v1-english")
_VOICE = _TTS_CFG.get("voice", "daniel")
_FORMAT = _TTS_CFG.get("response_format", "wav")

_INWORLD_API_KEY = os.getenv("INWORLD_API_KEY", "")  # set in .env if using the Inworld TTS provider
_INWORLD_VOICE = _TTS_CFG.get("inworld_voice", "default-hj_w4b63okr5pruvwx2erq__danielgroq")
_INWORLD_MODEL = _TTS_CFG.get("inworld_model", "inworld-tts-1.5-mini")

# --- Local omnivoice.cpp tts-server config (provider "local") ---
_LOCAL_TTS_URL = _TTS_CFG.get("local_url", "http://127.0.0.1:8082").rstrip("/")
_LOCAL_GAP_MS = _TTS_CFG.get("local_sentence_gap_ms", 120)
_LOCAL_TRIM = _TTS_CFG.get("local_trim_silence", True)
_LOCAL_SILENCE_THR = _TTS_CFG.get("local_silence_threshold", 300)
_LOCAL_EDGE_KEEP_MS = _TTS_CFG.get("local_edge_keep_ms", 50)
# Wall-clock cap on synthesizing ONE sentence. The requests read-timeout only
# fires on a gap between bytes, so a server that streams slowly-but-steadily (or
# never closes the response) would block the synth worker forever. This bounds it.
_LOCAL_SYNTH_HARD_TIMEOUT = float(_TTS_CFG.get("local_synth_hard_timeout_s", 45))
# omnivoice currently returns each response body after a full synthesis pass,
# even though the HTTP client asks for streaming. Hindi replies tend to contain
# several very short sentences/tags, making each one repay that fixed delay.
# Sentence 1 is NEVER coalesced (TTFW invariant); short Hindi continuations may
# be joined while already-buffered speech is playing.
_LOCAL_HINDI_COALESCE_MS = max(
    0.0, float(_TTS_CFG.get("local_hindi_coalesce_ms", 700)))
_LOCAL_HINDI_COALESCE_MIN_CHARS = max(
    1, int(_TTS_CFG.get("local_hindi_coalesce_min_chars", 48)))
_LOCAL_HINDI_COALESCE_MAX_CHARS = max(
    _LOCAL_HINDI_COALESCE_MIN_CHARS,
    int(_TTS_CFG.get("local_hindi_coalesce_max_chars", 180)),
)
_LOCAL_HINDI_SYNTH_RESERVE_S = max(
    0.0, float(_TTS_CFG.get("local_hindi_synth_reserve_s", 1.5)))
# Pipeline-lag offset (seconds) added to clock0 so the LCD word-reveal accounts
# for buffering between writing PCM to aplay's stdin and actual speaker output
# (kernel pipe + ALSA period buffer). Default 0; increase if LCD runs ahead.
_DISPLAY_LAG_S = float(_TTS_CFG.get("display_lag_s", 0.0))
_BLUETOOTH_CFG = get_full_config().get("bluetooth_speaker", {})

# Keep-alive session so per-sentence requests skip TCP/HTTP setup.
_LOCAL_SESSION = requests.Session()

# --- Switchable named voice (tts-server --voices-dir) ---------------------
# The tts-server preloads <name>.wav/<name>.txt pairs and selects one per
# request via the OAI "voice" field; empty means the server's default
# reference. Runtime-switchable via the switch_voice tool.
_LOCAL_VOICE = _TTS_CFG.get("local_voice", "")
_LOCAL_VOICE_LOCK = threading.Lock()


def get_local_voice():
    with _LOCAL_VOICE_LOCK:
        return _LOCAL_VOICE


def set_local_voice(name):
    """Switch the named voice used for every local synth request. Clears the
    PCM cache: cached bytes are keyed by text only and belong to the old voice."""
    global _LOCAL_VOICE
    with _LOCAL_VOICE_LOCK:
        _LOCAL_VOICE = (name or "").strip().lower()
    with _PCM_CACHE_LOCK:
        _PCM_CACHE.clear()
    print(f"[TTS Local] Voice switched to: {_LOCAL_VOICE or '(server default)'}")


def list_local_voices():
    """Names the tts-server has preloaded (GET /v1/voices). [] on failure."""
    try:
        r = _LOCAL_SESSION.get(f"{_LOCAL_TTS_URL}/v1/voices", timeout=(3, 5))
        if r.status_code == 200:
            return [v.get("name", "") for v in r.json().get("voices", []) if v.get("name")]
    except Exception as e:
        print(f"[TTS Local] list_local_voices failed: {e}")
    return []


def _local_synth_payload(speakable, voice=None):
    payload = {"input": speakable, "response_format": "pcm"}
    if voice is None:
        voice = get_local_voice()
    if voice:
        payload["voice"] = voice
    return payload

# --- Exact-text PCM cache -------------------------------------------------
# The tts-server is deterministic (fixed --seed per request), so identical
# sanitized text yields bit-identical PCM. Caching the trimmed PCM of short
# repeated fragments (expression tags like [laughter], tool fillers, bridges,
# short openers) makes their first-audio near-instant with ZERO quality
# change — playback is the same bytes the server would have returned.
# In-memory only: a tts-server restart with a different voice/config also
# restarts main.py in practice, so staleness can't leak across configs.
_PCM_CACHE_MAX_ITEMS = 256
_PCM_CACHE_MAX_TEXT = 60          # only fragments this short are cached
_PCM_CACHE = OrderedDict()        # sanitized text -> trimmed pcm bytes
_PCM_CACHE_LOCK = threading.Lock()


def _pcm_cache_get(text):
    with _PCM_CACHE_LOCK:
        pcm = _PCM_CACHE.get(text)
        if pcm is not None:
            _PCM_CACHE.move_to_end(text)
        return pcm


def _pcm_cache_put(text, pcm):
    if not pcm or len(text) > _PCM_CACHE_MAX_TEXT:
        return
    with _PCM_CACHE_LOCK:
        _PCM_CACHE[text] = pcm
        _PCM_CACHE.move_to_end(text)
        while len(_PCM_CACHE) > _PCM_CACHE_MAX_ITEMS:
            _PCM_CACHE.popitem(last=False)


def prewarm_tts_cache(texts, should_pause_fn=None):
    """Synthesize short fragments into the PCM cache (no playback). Run in a
    background thread at startup; pauses while a real turn is speaking so the
    tts-server's single synth slot is never contended."""
    if _tts_provider != "local":
        return
    done = 0
    for t in texts:
        speakable = sanitize_for_local_tts(t)
        if not speakable or not _HAS_WORD_RE.search(speakable):
            continue
        if len(speakable) > _PCM_CACHE_MAX_TEXT or _pcm_cache_get(speakable) is not None:
            continue
        while should_pause_fn is not None and should_pause_fn():
            time.sleep(0.5)
        try:
            r = _LOCAL_SESSION.post(
                f"{_LOCAL_TTS_URL}/v1/audio/speech",
                json=_local_synth_payload(speakable),
                timeout=(3, 30),
            )
            if r.status_code != 200:
                continue
            pcm = r.content
            if _LOCAL_TRIM:
                pcm = _trim_edge_silence(pcm, _LOCAL_SILENCE_THR, _LOCAL_EDGE_KEEP_MS)
            _pcm_cache_put(speakable, pcm)
            done += 1
        except Exception as e:
            print(f"[TTS Local] prewarm failed for {speakable[:30]!r}: {e}")
            return
    if done:
        print(f"[TTS Local] ⚡ PCM cache prewarmed with {done} fragments")

_SAMPLE_RATE = 24000
_BYTES_PER_MS = _SAMPLE_RATE * 2 // 1000
# Repair a stale auto_null default before the calibration fingerprints the
# output. Boot normally did this, but main.py can also be restarted directly.
_ACTIVE_AUDIO_SINK = ensure_bluetooth_sink(
    _BLUETOOTH_CFG, reconnect=True, timeout_seconds=6.0)
_DISPLAY_CALIBRATION = TimingCalibration.load(_TTS_CFG, _SAMPLE_RATE)
if not _DISPLAY_CALIBRATION.valid:
    print(f"[LCD Sync] Speech text disabled until calibrated: {_DISPLAY_CALIBRATION.reason}")

# === local TTS text sanitization (kept in sync with omnivoice voice_api.py) ===
# The local model only understands these bracket tags; everything else is
# stripped before synthesis so it's never read out literally.
SUPPORTED_TAGS = {
    "laughter", "sigh", "confirmation-en", "question-en", "question-ah",
    "question-oh", "question-ei", "question-yi", "surprise-ah", "surprise-oh",
    "surprise-wa", "surprise-yo", "dissatisfaction-hnn",
}
TAG_MAP = {
    "laugh": "laughter", "chuckle": "laughter", "giggle": "laughter",
    "giggling": "laughter", "gasp": "surprise-ah", "groan": "dissatisfaction-hnn",
    "sniffle": "sigh", "yawn": "sigh",
    # Common model spellings for an excited "ahh" vocalization.
    "exclamation-ah": "surprise-ah", "exclamation-ahh": "surprise-ah",
}
_BRACKET_TAG_RE = re.compile(r"\[([^\[\]]{1,40})\]")
_MOTION_RE = re.compile(r"<[^<>]{1,80}>")
_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")
_HAS_WORD_RE = re.compile(r"\w")
_DEVANAGARI_TEXT_RE = re.compile(r"[\u0900-\u097f]")


def sanitize_for_local_tts(text: str) -> str:
    def map_tag(m):
        tag = m.group(1).strip().lower()
        if tag in SUPPORTED_TAGS:
            return f"[{tag}]"
        if tag in TAG_MAP:
            return f"[{TAG_MAP[tag]}]"
        return ""
    text = _MOTION_RE.sub("", text)
    text = _BRACKET_TAG_RE.sub(map_tag, text)
    text = _EMOJI_RE.sub("", text)
    text = text.replace("*", "").replace("`", "").replace("#", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fire_oled_tag(text: str) -> str:
    """Apply any `<oled:…>` expression in `text` and return the text without it.

    Used by the providers that have no per-sentence playback marker (the cloud
    streamers and the LCD-only path): they fire the expression as the sentence
    is queued. The local provider does better — see `LocalTTSStreamer`, which
    fires on the sentence's first audible PCM chunk so the face lands exactly
    on the words.

    Also strips neck tags, which are dispatched separately in main.py: without
    this, a `<neck:left>` would be read aloud by the cloud voices (only
    `sanitize_for_local_tts` removed them, and that's local-only).
    """
    try:
        from robot.oled_tags import last_oled_tag, apply_oled, strip_oled_tags
        from robot.neck import strip_neck_tags
        name = last_oled_tag(text)
        if name:
            apply_oled(name)
        return strip_neck_tags(strip_oled_tags(text))
    except Exception:
        return text


def get_tts_system_prompt_note() -> str:
    """Extra system-prompt text for the active TTS provider.

    For the local provider the voice model only understands SUPPORTED_TAGS, so
    the LLM is told to restrict itself to them (anything else gets stripped by
    sanitize_for_local_tts and would just waste tokens)."""
    if _tts_provider != "local":
        return ""
    tags = ", ".join(f"[{t}]" for t in sorted(SUPPORTED_TAGS))
    return (
        "\n\n## VOICE TAG RESTRICTION (IMPORTANT — overrides any tag guidance above)\n"
        f"Your current voice engine supports ONLY these bracket expression tags: {tags}.\n"
        "Use [laughter] instead of [laugh]/[chuckle]/[giggle], [surprise-ah] instead of "
        "[gasp], [dissatisfaction-hnn] instead of [groan], and [sigh] instead of "
        "[sniffle]/[yawn]. Any other bracket tag — including tone/style directions like "
        "[cheerful], [whisper], [excited], [sarcastic] — is NOT supported and will be "
        "stripped before speaking, so do not use them."
    )


def _append_lcd_page(lines, text):
    """Append words to a 16x2 page, clearing only when both rows are full.

    Returns a new two-item list so callers can retain the page across sentence
    segments without relying on the LCD driver's scrolling-window behavior.
    """
    line1, line2 = lines
    for word in str(text).split():
        # A single token cannot span character cells cleanly. Preserve the
        # useful beginning instead of letting the LCD driver truncate blindly.
        word = word[:16]
        if not line1:
            line1 = word
            continue

        if not line2:
            candidate = f"{line1} {word}"
            if len(candidate) <= 16:
                line1 = candidate
            else:
                line2 = word
            continue

        candidate = f"{line2} {word}"
        if len(candidate) <= 16:
            line2 = candidate
        else:
            # The incoming word cannot fit anywhere: start the next page.
            line1, line2 = word, ""

    return [line1, line2]


def _trim_edge_silence(pcm: bytes, thr: int, keep_ms: int) -> bytes:
    """Strip model-baked leading/trailing silence so inter-sentence pauses are
    uniform (a fixed gap is re-inserted by the streamer)."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    n = len(a)
    i = 0
    while i < n and abs(a[i]) < thr:
        i += 1
    j = n
    while j > i and abs(a[j - 1]) < thr:
        j -= 1
    keep = _SAMPLE_RATE * keep_ms // 1000
    i = max(0, i - keep)
    j = min(n, j + keep)
    return a[i:j].tobytes()


class _StreamingEdgeTrim:
    """Incremental version of _trim_edge_silence for streamed PCM.

    feed() returns audio that is safe to play NOW: the silent head is dropped
    (keeping keep_ms before the first loud sample) and any run of trailing
    quiet samples is held back until a loud sample follows it — so when the
    stream ends, flush() can cap the trailing silence at keep_ms exactly like
    the buffered trimmer did. Intra-sentence pauses pass through unchanged."""

    def __init__(self, thr: int, keep_ms: int):
        self.thr = thr
        self.keep = _SAMPLE_RATE * keep_ms // 1000  # samples
        self.started = False
        self._lead = array.array("h")    # rolling tail of the silent head
        self._pending = array.array("h") # trailing quiet run held back
        self._rem = b""                  # odd byte between chunks

    def feed(self, chunk: bytes) -> bytes:
        data = self._rem + chunk
        cut = len(data) // 2 * 2
        self._rem = data[cut:]
        a = array.array("h")
        a.frombytes(data[:cut])
        if not a:
            return b""
        thr = self.thr
        if not self.started:
            first = next((i for i, s in enumerate(a) if abs(s) >= thr), None)
            if first is None:
                self._lead.extend(a)
                if len(self._lead) > self.keep:
                    del self._lead[: len(self._lead) - self.keep]
                return b""
            self.started = True
            head = self._lead
            self._lead = array.array("h")
            start = max(0, first - self.keep + len(head))
            a = head + a
            a = a[start:] if start else a
        last = next((i for i in range(len(a) - 1, -1, -1) if abs(a[i]) >= thr), None)
        if last is None:
            self._pending.extend(a)
            return b""
        out = self._pending + a[: last + 1]
        self._pending = a[last + 1 :]
        return out.tobytes()

    def flush(self) -> bytes:
        """End of stream: return at most keep_ms of the held-back tail. An
        all-silent stream yields its final keep_ms, matching _trim_edge_silence."""
        if not self.started:
            tail = self._lead[-self.keep :] if self.keep else array.array("h")
            self._lead = array.array("h")
            return tail.tobytes()
        tail = self._pending[: self.keep]
        self._pending = array.array("h")
        return tail.tobytes()


print(f"[TTS] Pre-initialized: provider={_tts_provider}")


class GroqTTSStreamer:
    """
    Streaming TTS with pre-fetch queue using Groq.
    """
    def __init__(self):
        self._sentence_queue = Queue()         # sentences waiting to be fetched
        self._audio_queue = Queue(maxsize=2)   # pre-fetched audio files ready to play
        self._fetch_thread = None
        self._play_thread = None
        self._first_play_event = threading.Event()  # set when first audio starts playing
        if not _client:
            print("[TTS] GROQ_API_KEY not found!")

    def start(self):
        self._fetch_thread = threading.Thread(target=self._fetch_worker, daemon=True)
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._fetch_thread.start()
        self._play_thread.start()

    def add_sentence(self, text):
        # No per-sentence playback marker on this provider, so the expression
        # fires as the sentence is queued rather than as it becomes audible.
        self._sentence_queue.put(_fire_oled_tag(text))

    def finish(self):
        self._sentence_queue.put(None)
        if self._fetch_thread:
            self._fetch_thread.join()
        if self._play_thread:
            self._play_thread.join()

    @property
    def first_play_event(self):
        return self._first_play_event

    def _fetch_worker(self):
        idx = 0
        while True:
            text = self._sentence_queue.get()
            if text is None:
                self._audio_queue.put(None)
                break

            idx += 1
            print(f"[TTS Fetch] Generating audio for sentence {idx}: {text[:40]}...")

            if not _client:
                continue

            fd, temp_path = tempfile.mkstemp(suffix=f".{_FORMAT}")
            os.close(fd)

            try:
                response = _client.audio.speech.create(
                    model=_MODEL,
                    voice=_VOICE,
                    input=text,
                    response_format=_FORMAT
                )
                response.write_to_file(temp_path)
                self._audio_queue.put(temp_path)
            except Exception as e:
                print(f"[TTS Fetch] Error on sentence {idx}: {e}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)

    def _play_worker(self):
        idx = 0
        while True:
            audio_file = self._audio_queue.get()
            if audio_file is None:
                break

            idx += 1
            if not self._first_play_event.is_set():
                self._first_play_event.set()

            print(f"[TTS Play] Playing sentence {idx}")
            try:
                subprocess.run(
                    ["mpv", "--no-video", "--audio-device=alsa", audio_file],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                print(f"[TTS Play] Playback error: {e}")
            finally:
                if os.path.exists(audio_file):
                    os.remove(audio_file)

        print("[TTS Play] All sentences played")


class InworldTTSStreamer:
    """
    Streaming TTS using Inworld WebSocket for lowest TTFB.
    """
    def __init__(self):
        self._first_play_event = threading.Event()
        self._sentence_queue = None
        self._loop = None
        self._thread = None

    @property
    def first_play_event(self):
        return self._first_play_event

    def start(self):
        if not websockets:
            print("[TTS Inworld] Error: websockets not installed.")
            return
            
        self._ready_event = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready_event.wait()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._sentence_queue = asyncio.Queue()
        self._ready_event.set()
        
        try:
            self._loop.run_until_complete(self._websocket_tts())
        except Exception as e:
            print(f"[TTS Inworld] Error in loop: {e}")
        finally:
            self._loop.close()

    def add_sentence(self, text):
        text = _fire_oled_tag(text)
        if self._loop and self._sentence_queue:
            self._loop.call_soon_threadsafe(self._sentence_queue.put_nowait, text)

    def finish(self):
        if self._loop and self._sentence_queue:
            self._loop.call_soon_threadsafe(self._sentence_queue.put_nowait, None)
        if self._thread:
            self._thread.join()

    async def _websocket_tts(self):
        url = "wss://api.inworld.ai/tts/v1/voice:streamBidirectional"
        headers = {"Authorization": f"Basic {_INWORLD_API_KEY}"}
        context_id = f"ctx-{time.time()}"
        
        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                create_msg = {
                    "context_id": context_id,
                    "create": {
                        "voice_id": _INWORLD_VOICE,
                        "model_id": _INWORLD_MODEL,
                        "audio_config": {
                            "audio_encoding": "OGG_OPUS",
                            "sample_rate_hertz": 24000,
                            "bit_rate": 32000
                        }
                    }
                }
                await ws.send(json.dumps(create_msg))
                
                # Wait for context creation
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if "error" in data:
                        print(f"[TTS Inworld] Error creating context: {data['error']}")
                        return
                    if "contextCreated" in data.get("result", {}):
                        break

                print("[TTS Inworld] Context created. Starting streaming audio play...")
                
                mpv_process = await asyncio.create_subprocess_exec(
                    "mpv", "--audio-device=alsa", "--no-video", "--untimed", "--audio-pitch-correction=no", "--cache-pause=no", "-",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )

                async def receive_audio():
                    async for message in ws:
                        data = json.loads(message)
                        if "error" in data:
                            print(f"[TTS Inworld] Error receiving: {data['error']}")
                            break
                        
                        result = data.get("result")
                        if not result:
                            if data.get("done"):
                                break
                            continue
                            
                        if "contextClosed" in result:
                            break
                            
                        if "audioChunk" in result:
                            if not self._first_play_event.is_set():
                                self._first_play_event.set()
                                
                            b64_content = result["audioChunk"].get("audioContent")
                            if b64_content:
                                audio_bytes = base64.b64decode(b64_content)
                                if mpv_process.returncode is None:
                                    try:
                                        mpv_process.stdin.write(audio_bytes)
                                        await mpv_process.stdin.drain()
                                    except (BrokenPipeError, ConnectionResetError):
                                        pass

                recv_task = asyncio.create_task(receive_audio())

                try:
                    while True:
                        text = await self._sentence_queue.get()
                        if text is None:
                            break
                        
                        print(f"[TTS Inworld] Sending text: {text[:40]}...")
                        text_msg = {
                            "context_id": context_id,
                            "send_text": {
                                "text": text,
                                "flush_context": {}
                            }
                        }
                        await ws.send(json.dumps(text_msg))
                finally:
                    close_msg = {"context_id": context_id, "close_context": {}}
                    try:
                        await ws.send(json.dumps(close_msg))
                    except:
                        pass
                    
                    await recv_task
                    
                    if mpv_process.returncode is None:
                        try:
                            mpv_process.stdin.close()
                        except:
                            pass
                        await mpv_process.wait()

        except Exception as e:
            print(f"[TTS Inworld] WebSocket connection error: {e}")


class LocalTTSStreamer:
    """
    Gapless streaming TTS against the local omnivoice.cpp tts-server
    (POST /v1/audio/speech, raw pcm). Adapted from newstt.py.

    Same interface as the other streamers (start/add_sentence/finish/
    first_play_event), so tool-call fillers and follow-up answers stream onto
    it unchanged. Low-latency design:
      - sentences synthesize in a worker thread while earlier audio plays
      - a single long-lived aplay pipe plays raw pcm, pre-spawned at start()
        so the ALSA/BT sink open never delays the first word
      - PCM streams to the player as it arrives from the server (no full-
        response buffering); playback begins on the first audible bytes
      - per-sentence edge silence is trimmed on the fly (_StreamingEdgeTrim)
        and a small fixed gap inserted, so pauses between sentences are uniform
    """

    def __init__(self):
        self._sentence_queue = Queue()        # text in (None = no more input)
        self._pcm_queue = Queue()             # raw pcm out (None = end)
        self._display_queue = Queue()         # live segments/fallback events for LCD
        self._first_play_event = threading.Event()
        self._play_gate = threading.Event()
        self._hold = False                    # speculative turn: synth but don't play
        self._aborted = False
        self._gap = b"\x00" * (_LOCAL_GAP_MS * _BYTES_PER_MS)
        self._bytes_per_sec = _SAMPLE_RATE * 2  # 16-bit mono
        self._audio_tail_at = None            # estimated audible end (monotonic)
        self._queued_pcm_bytes = 0            # PCM not yet handed to aplay
        self._queued_pcm_lock = threading.Lock()
        self._proc = None                     # aplay process (lazy)
        self._synth_thread = None
        self._play_thread = None
        self._display_thread = None
        self._synth_state = "Idle"
        self._segment_serial = 0
        self._segments = []
        self._display_session_id = None
        self._lcd_manager = None
        self._lcd_page = ["", ""]
        # These span every synthesized sentence in this assistant response.
        # Only one expression may appear, and only before the first real word.
        self._lcd_expression_shown = False
        self._lcd_has_spoken_word = False
        # None unless record_enabled is on. Only ever touched by the playback
        # thread, and only after PCM has been handed to aplay.
        self._recording = speech_recorder.new_recording(_SAMPLE_RATE)

    @property
    def first_play_event(self):
        return self._first_play_event

    def hold_playback(self):
        """Speculative turn: synthesize ahead but do NOT play anything until
        release_playback() confirms the transcript at the endpoint commit."""
        self._hold = True

    def release_playback(self):
        """Commit confirmed: audio (buffered or still streaming) may play."""
        self._hold = False
        self._play_gate.set()

    def start(self):
        # Pre-spawn the aplay pipe so the ALSA/BT sink open cost is paid during
        # LLM/TTS time, not on the first audio write (falls back to lazy open
        # in the play worker if this fails).
        try:
            self._proc = self._open_sink()
        except Exception as e:
            print(f"[TTS Local] Pre-spawn of aplay failed (will open lazily): {e}")
        try:
            from core.lcd_display import lcd_manager
            self._lcd_manager = lcd_manager
            self._display_session_id = lcd_manager.begin_stream_session()
        except Exception:
            self._lcd_manager = None
        self._synth_thread = threading.Thread(target=self._synth_worker, daemon=True)
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._display_thread = threading.Thread(target=self._display_worker, daemon=True)
        self._synth_thread.start()
        self._play_thread.start()
        self._display_thread.start()

    def add_sentence(self, text):
        self._sentence_queue.put(text)

    def finish(self):
        """Signal end of input and block until all queued audio has played.

        Hard cap: returns within ~35s even if threads are stuck. Forces
        abort() if the synth thread hasn't exited in 30s so the play
        thread gets its sentinel and can drain.
        """
        self._sentence_queue.put(None)
        if self._synth_thread:
            self._synth_thread.join(timeout=30)
            if self._synth_thread.is_alive():
                print(f"[TTS Local] ⚠ Synth thread stuck (state: {self._synth_state}) — forcing abort")
                self.abort()  # sets _aborted, kills aplay, sends sentinels
        self._play_gate.set()
        if self._play_thread:
            self._play_thread.join(timeout=180)
            if self._play_thread.is_alive():
                print("[TTS Local] ⚠ Play thread hung — killing aplay")
                proc = self._proc
                if proc is not None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                self._play_thread.join(timeout=3)
        # The display is never allowed to hold up audio. It normally exits as
        # soon as the audible tail is reached; retain a small safety timeout.
        if self._display_thread:
            self._display_thread.join(timeout=1.0)
        self._end_display_session()

    def abort(self):
        """Stop playback NOW (used by the 'stop it' hotword). pkill mpv does
        nothing for this provider, so we kill the aplay pipe ourselves."""
        self._aborted = True
        self._sentence_queue.put(None)
        self._display_queue.put(None)
        for segment in list(self._segments):
            segment.cancel()
        self._end_display_session()
        self._play_gate.set()
        self._first_play_event.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def _end_display_session(self):
        manager = self._lcd_manager
        stream_id = self._display_session_id
        if manager is not None and stream_id is not None:
            try:
                manager.end_stream_session(stream_id)
            except Exception:
                pass
        self._display_session_id = None

    # --- internals ---

    @staticmethod
    def _coalesce_length(text):
        """Visible length used only for continuation request sizing."""
        return len(_BRACKET_TAG_RE.sub("", sanitize_for_local_tts(text)).strip())

    def _put_pcm(self, pcm):
        """Queue PCM while tracking playback headroom across worker threads."""
        with self._queued_pcm_lock:
            self._queued_pcm_bytes += len(pcm)
        self._pcm_queue.put(("pcm", pcm))

    def _mark_pcm_consumed(self, pcm):
        with self._queued_pcm_lock:
            self._queued_pcm_bytes = max(0, self._queued_pcm_bytes - len(pcm))

    def _playback_headroom_s(self):
        """Audio already scheduled or queued, measured from right now."""
        now = monotonic_time()
        scheduled = max(0.0, (self._audio_tail_at or 0.0) - now)
        with self._queued_pcm_lock:
            queued = self._queued_pcm_bytes / self._bytes_per_sec
        return scheduled + queued

    def _coalesce_hindi_continuation(self, first, request_idx):
        """Join short queued Hindi continuations without touching sentence 1.

        Returns ``(text, saw_end, pending)``. Waiting is allowed only while the
        playback clock says existing audio has more headroom than the measured
        omnivoice body wait. Thus lookahead is hidden behind current speech; if
        playback is close to starving, synthesis starts immediately.
        """
        if (request_idx == 0 or _LOCAL_HINDI_COALESCE_MS <= 0
                or not _DEVANAGARI_TEXT_RE.search(first)
                or self._coalesce_length(first) >= _LOCAL_HINDI_COALESCE_MIN_CHARS):
            return first, False, None

        parts = [first]
        visible_chars = self._coalesce_length(first)
        deadline = monotonic_time() + _LOCAL_HINDI_COALESCE_MS / 1000.0
        saw_end = False
        pending = None

        while visible_chars < _LOCAL_HINDI_COALESCE_MIN_CHARS and not self._aborted:
            try:
                item = self._sentence_queue.get_nowait()
            except Empty:
                now = monotonic_time()
                audio_headroom = self._playback_headroom_s()
                safe_wait = min(deadline - now,
                                audio_headroom - _LOCAL_HINDI_SYNTH_RESERVE_S)
                if safe_wait <= 0:
                    break
                try:
                    item = self._sentence_queue.get(timeout=min(0.05, safe_wait))
                except Empty:
                    continue

            if item is None:
                saw_end = True
                break

            item_len = self._coalesce_length(item)
            separator_len = 1 if parts else 0
            if visible_chars + separator_len + item_len > _LOCAL_HINDI_COALESCE_MAX_CHARS:
                pending = item
                break
            parts.append(item)
            visible_chars += separator_len + item_len

        if len(parts) > 1:
            print(f"[TTS Local] ⚡ Coalesced {len(parts)} short Hindi continuations "
                  f"into one {visible_chars}-char request")
        return " ".join(parts), saw_end, pending

    def _synth_worker(self):
        idx = 0
        pending = None
        input_done = False
        # An expression tag whose sentence turned out to have no speakable
        # words (a tag-only fragment). Carry it to the next real sentence
        # instead of dropping the face change on the floor.
        pending_oled = None
        while True:
            if pending is not None:
                text, pending = pending, None
            elif input_done:
                break
            else:
                text = self._sentence_queue.get()
            if text is None:
                break
            if self._aborted:
                continue  # keep draining until the sentinel

            text, saw_end, pending = self._coalesce_hindi_continuation(text, idx)
            input_done = input_done or saw_end

            # Read the expression tag off the RAW text: sanitize_for_local_tts
            # strips every <...> via _MOTION_RE, so this has to happen first.
            # Nothing here touches the audio path — it's a regex over text that
            # is about to be POSTed anyway.
            try:
                from robot.oled_tags import last_oled_tag
                oled_name = last_oled_tag(text) or pending_oled
            except Exception:
                oled_name = pending_oled

            speakable = sanitize_for_local_tts(text)
            if not _HAS_WORD_RE.search(speakable):
                pending_oled = oled_name
                continue
            pending_oled = None
            idx += 1
            sentence_voice = get_local_voice()

            # Deterministic PCM cache: identical text → identical server
            # output, so a hit is byte-for-byte what a fresh synth would play.
            cached = _pcm_cache_get(speakable)
            if cached is not None:
                print(f"[TTS Local] ⚡ Sentence {idx} from PCM cache: {speakable[:40]}...")
                gap_bytes = len(self._gap) if idx > 1 else 0
                self._pcm_queue.put(
                    ("meta", speakable, gap_bytes, sentence_voice, oled_name))
                if gap_bytes:
                    self._put_pcm(self._gap)
                self._put_pcm(cached)
                self._pcm_queue.put(("end",))
                if not self._hold:
                    self._play_gate.set()
                continue

            print(f"[TTS Local] Synthesizing sentence {idx}: {speakable[:40]}...")

            sent_any = False
            cache_chunks = []      # trimmed pcm accumulated for the cache
            cache_ok = True        # False if the stream was cut/aborted
            try:
                self._synth_state = f"POSTing sentence {idx}"
                r = _LOCAL_SESSION.post(
                    f"{_LOCAL_TTS_URL}/v1/audio/speech",
                    json=_local_synth_payload(speakable, sentence_voice),
                    stream=True, timeout=(3, 30),
                )
                if r.status_code != 200:
                    print(f"[TTS Local] HTTP {r.status_code}: {r.text[:200]}")
                    self._synth_state = "Idle"
                    continue
                # Stream the PCM to the player as it arrives instead of
                # buffering the whole response: leading silence is dropped on
                # the fly and trailing silence held back (see _StreamingEdgeTrim),
                # so the first audible bytes reach aplay while the server is
                # still sending. The gap and LCD text travel as markers.
                gap_bytes = len(self._gap) if idx > 1 else 0
                trimmer = (_StreamingEdgeTrim(_LOCAL_SILENCE_THR, _LOCAL_EDGE_KEEP_MS)
                           if _LOCAL_TRIM else None)
                self._synth_state = f"Reading stream sentence {idx}"
                synth_start = time.time()
                with r:
                    for chunk in r.iter_content(chunk_size=4096):
                        if self._aborted:
                            cache_ok = False
                            break
                        out = trimmer.feed(chunk) if trimmer else chunk
                        if out:
                            if not sent_any:
                                sent_any = True
                                if idx == 1:
                                    print(f"[T] tts_first_pcm +{time.time() - synth_start:.3f}s after stream start")
                                self._pcm_queue.put(
                                    ("meta", speakable, gap_bytes,
                                     sentence_voice, oled_name))
                                if gap_bytes:
                                    self._put_pcm(self._gap)
                            self._put_pcm(out)
                            cache_chunks.append(out)
                            # First audible bytes of the turn: open the gate NOW
                            # (the old prebuffer wait added up to a full synth
                            # of latency before the first word) — unless a
                            # speculative turn is holding playback until commit.
                            if not self._hold:
                                self._play_gate.set()
                        # Guard against a server that streams forever / too slowly:
                        # the per-read timeout won't catch a steady dribble.
                        if time.time() - synth_start > _LOCAL_SYNTH_HARD_TIMEOUT:
                            print(f"[TTS Local] ⚠ Sentence {idx} synth exceeded "
                                  f"{_LOCAL_SYNTH_HARD_TIMEOUT:.0f}s — cutting it off")
                            cache_ok = False
                            break
                if trimmer and sent_any and not self._aborted:
                    tail = trimmer.flush()
                    if tail:
                        self._put_pcm(tail)
                        cache_chunks.append(tail)
                if sent_any:
                    self._pcm_queue.put(("end",))
                    if cache_ok:
                        _pcm_cache_put(speakable, b"".join(cache_chunks))
                self._synth_state = "Idle"
            except Exception as e:
                print(f"[TTS Local] Error on sentence {idx} (state: {self._synth_state}): {type(e).__name__} - {e}")
                self._synth_state = "Idle"
                if sent_any:
                    self._pcm_queue.put(("end",))
                continue

        # End of input: open the gate (zero-sentence replies must not hang the
        # player) and tell it to drain out. A held speculative turn keeps the
        # gate shut — release_playback()/abort() own it from here.
        self._pcm_queue.put(None)
        if not self._hold:
            self._play_gate.set()

    def _kill_stalled_sink(self):
        """Watchdog callback: the audio sink wedged, kill aplay to recover."""
        print("[TTS Local] ⚠ aplay sink stalled (not draining) — killing to recover")
        self._aborted = True
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def _write_pcm(self, pcm):
        """Write PCM to aplay, bounded by a watchdog.

        aplay only reads stdin as fast as the ALSA/Bluetooth sink drains it. If
        the sink stalls mid-speech (a common BT-speaker hiccup), aplay stays
        alive — so no BrokenPipeError — but stops reading; the kernel pipe fills
        and stdin.write() blocks FOREVER, freezing playback and the whole turn.
        A healthy write only buffers and returns in microseconds, so if one
        write takes much longer than the audio it carries, the sink is wedged:
        kill aplay so the write unblocks (BrokenPipeError) and we recover instead
        of hanging."""
        proc = self._proc
        if proc is None:
            return
        deadline = max(10.0, len(pcm) / self._bytes_per_sec + 8.0)
        wd = threading.Timer(deadline, self._kill_stalled_sink)
        wd.daemon = True
        wd.start()
        try:
            proc.stdin.write(pcm)
            proc.stdin.flush()
        finally:
            wd.cancel()

    def _open_sink(self):
        global _ACTIVE_AUDIO_SINK, _DISPLAY_CALIBRATION
        if shutil.which("aplay") is None:
            raise RuntimeError("aplay not found (install alsa-utils)")
        # Re-check at every new playback process. This repairs PulseAudio after
        # a transient Bluetooth drop and pins aplay to the selected sink so an
        # unrelated default-sink change cannot redirect speech to auto_null.
        sink = ensure_bluetooth_sink(
            _BLUETOOTH_CFG, reconnect=True, timeout_seconds=6.0)
        if not sink:
            raise RuntimeError("configured Bluetooth A2DP sink is unavailable")
        if sink != _ACTIVE_AUDIO_SINK or not _DISPLAY_CALIBRATION.valid:
            _ACTIVE_AUDIO_SINK = sink
            _DISPLAY_CALIBRATION = TimingCalibration.load(
                _TTS_CFG, _SAMPLE_RATE)
        return subprocess.Popen(
            ["aplay", "-q", "-f", "S16_LE", "-r", str(_SAMPLE_RATE),
             "-c", "1", "-t", "raw", "-"],
            stdin=subprocess.PIPE,
            env=playback_environment(sink),
        )

    def _play_worker(self):
        self._play_gate.wait()
        # Per-sentence state.  Crucially, the LCD segment is published on the
        # FIRST speech PCM chunk, never at synth completion.  This leaves TTFW
        # identical to the audio-only path.
        cur_segment = None
        cur_gap_remaining = 0
        cur_speech_len = 0
        cur_reason = ""
        cur_has_schedule = True
        cur_announced = False
        cur_latency_s = max(0.0, _DISPLAY_LAG_S)
        cur_speakable = ""
        cur_voice = ""
        cur_oled = None
        try:
            while True:
                try:
                    item = self._pcm_queue.get(timeout=5)
                except Exception:
                    # Queue timeout — check if we should give up
                    if self._aborted:
                        break
                    continue
                if item is None:
                    break
                if self._aborted:
                    continue  # drain without playing

                kind = item[0]
                if kind == "meta":
                    cur_speakable, cur_gap_remaining, cur_voice = item[1], item[2], item[3]
                    cur_oled = item[4] if len(item) > 4 else None
                    cur_segment = None
                    cur_reason = ""
                    cur_has_schedule = True
                    cur_latency_s = (_DISPLAY_CALIBRATION.output_latency_s
                                     if _DISPLAY_CALIBRATION.valid
                                     else max(0.0, _DISPLAY_LAG_S))
                    cur_speech_len = 0
                    cur_announced = False
                    if self._recording is not None:
                        self._recording.note_text(cur_speakable)
                    continue
                if kind == "end":
                    if cur_segment is not None and self._audio_tail_at is not None:
                        cur_segment.finish(self._audio_tail_at)
                    cur_segment = None
                    continue
                pcm = item[1]

                try:
                    if self._proc is None:
                        self._proc = self._open_sink()
                    if not self._first_play_event.is_set():
                        self._first_play_event.set()

                    # Preserve the TTFW critical path exactly: hand PCM to
                    # aplay before constructing or publishing any word schedule.
                    write_started = monotonic_time()
                    self._write_pcm(pcm)

                    # Archive the exact bytes that just went to the sink. This
                    # is a list append of a bytes object we already hold — no
                    # copy and no I/O — and it runs after the write, so TTFW is
                    # unchanged. The wav is encoded on the writer thread.
                    if self._recording is not None:
                        self._recording.feed(pcm)

                    # Estimate the audible window after the write. If synthesis
                    # or a tool call starves the pipe, max(now+latency, old_tail)
                    # re-anchors the stream instead of pretending it was gapless.
                    chunk_start = max(self._audio_tail_at or 0.0,
                                      write_started + cur_latency_s)
                    chunk_dur = len(pcm) / self._bytes_per_sec
                    chunk_end = chunk_start + chunk_dur

                    gap_part = min(cur_gap_remaining, len(pcm))
                    speech_part = len(pcm) - gap_part
                    display_event = None
                    if gap_part:
                        cur_gap_remaining -= gap_part
                    if speech_part > 0:
                        speech_start = chunk_start + gap_part / self._bytes_per_sec
                        speech_offset = cur_speech_len / self._bytes_per_sec
                        if not cur_announced and cur_oled:
                            # First audible chunk of the sentence that carried
                            # the tag — i.e. Kiki is saying those words RIGHT
                            # now, so the face changes with them. This runs
                            # after _write_pcm above, so TTFW is untouched.
                            try:
                                from robot.oled_tags import apply_oled
                                apply_oled(cur_oled)
                            except Exception:
                                pass
                            cur_oled = None
                        if not cur_announced:
                            self._segment_serial += 1
                            schedule, cur_reason = _DISPLAY_CALIBRATION.schedule(
                                cur_speakable, cur_voice)
                            cur_has_schedule = schedule is not None
                            # Tag-only fast-path fragments intentionally contain
                            # no LCD words. Play them exactly as before, but do
                            # not enqueue an empty segment that can overwrite or
                            # delay the following real word display.
                            cur_segment = (
                                LiveDisplaySegment(schedule, self._segment_serial)
                                if schedule is not None and schedule.cues else None
                            )
                            if cur_segment is not None:
                                self._segments.append(cur_segment)
                        if cur_segment is not None:
                            if not cur_announced:
                                cur_segment.start(speech_start)
                                display_event = cur_segment
                            else:
                                cur_segment.add_window(speech_offset, speech_start)
                        elif not cur_announced and not cur_has_schedule:
                            display_event = ("fallback", cur_reason)
                        cur_announced = True
                        cur_speech_len += speech_part

                    self._audio_tail_at = chunk_end
                    self._mark_pcm_consumed(pcm)
                    if display_event is not None:
                        self._display_queue.put(display_event)
                except Exception as e:
                    print(f"[TTS Local] Playback error: {e}")
        finally:
            # Hand off before the aplay drain below: this only queues the
            # buffer, and an aborted reply still saves what was actually said.
            if self._recording is not None:
                self._recording.close()
            self._display_queue.put(None)
            if self._proc is not None:
                try:
                    self._proc.stdin.close()
                    self._proc.wait(timeout=180)  # blocks until ALSA drains the last audio
                except subprocess.TimeoutExpired:
                    print("[TTS Local] ⚠ aplay did not exit in 180s — killing")
                    try:
                        self._proc.kill()
                        self._proc.wait(timeout=5)
                    except Exception:
                        pass
                except Exception:
                    pass
            # Safety: never leave the thinking-sound stopper waiting forever
            # (e.g. tts-server down and zero sentences synthesized).
            self._first_play_event.set()
        print("[TTS Local] All sentences played")

    def _sleep(self, secs):
        """Abort-aware sleep used by the display scroller."""
        end = time.time() + secs
        while not self._aborted:
            remaining = end - time.time()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.05))

    def _should_display_lcd_cue(self, cue):
        """Apply the one-leading-expression rule across the whole response."""
        if cue.is_expression:
            if self._lcd_expression_shown or self._lcd_has_spoken_word:
                return False
            self._lcd_expression_shown = True
        else:
            self._lcd_has_spoken_word = True
        return True

    def _display_worker(self):
        """Reveal calibrated words against the live, underrun-aware audio clock."""
        lcd_manager = self._lcd_manager
        while True:
            item = self._display_queue.get()
            if item is None:
                break
            if self._aborted or lcd_manager is None:
                continue
            if isinstance(item, tuple) and item[0] == "fallback":
                try:
                    lcd_manager.write("Speaking...", "Calibration req",
                                      stream_id=self._display_session_id)
                except Exception:
                    pass
                if item[1]:
                    print(f"[LCD Sync] {item[1]}")
                continue
            self._scroll_live_segment(lcd_manager, item)

    def _scroll_live_segment(self, lcd_manager, segment):
        lead = segment.schedule.lcd_write_lead_s
        for cue in segment.schedule.cues:
            while not self._aborted:
                target, end_at, cancelled = segment.target_for(cue)
                if cancelled or target is None:
                    return
                if end_at is not None and target >= end_at:
                    return
                wait = target - lead - monotonic_time()
                if wait <= 0:
                    break
                self._sleep(min(wait, 0.05))
            if self._aborted:
                return
            if not self._should_display_lcd_cue(cue):
                continue
            try:
                # Display-only conversion. The original cue/speakable text has
                # already driven TTS and timing; this runs on the independent
                # LCD thread after PCM entered the playback path.
                display_word = romanize_hindi_for_lcd(cue.word)
                self._lcd_page = _append_lcd_page(self._lcd_page, display_word)
                lcd_manager.write(*self._lcd_page,
                                  stream_id=self._display_session_id)
            except Exception:
                pass

        # Keep the completed text visible through tool/network and sentence
        # gaps. The next segment continues filling this page; no placeholder is
        # needed. We still wait for the audible end to preserve segment order.
        while not self._aborted:
            end_at, cancelled = segment.end_state()
            if cancelled:
                return
            if end_at is not None:
                wait = end_at - lead - monotonic_time()
                if wait <= 0:
                    return
                self._sleep(min(wait, 0.05))
            else:
                self._sleep(0.05)


class LCDOnlyStreamer:
    """TTS-compatible response streamer that writes words only to the LCD.

    It deliberately implements the same small interface as the audio
    streamers, including speculative hold/release.  No audio process, socket,
    or TTS request is created while output mute is active.
    """

    is_silent = True

    def __init__(self):
        self._sentence_queue = Queue()
        self._first_play_event = threading.Event()
        self._play_gate = threading.Event()
        self._hold = False
        self._aborted = False
        self._thread = None
        self._lcd_manager = None
        self._display_session_id = None
        self._lcd_page = ["", ""]

    @property
    def first_play_event(self):
        return self._first_play_event

    def hold_playback(self):
        self._hold = True

    def release_playback(self):
        self._hold = False
        self._play_gate.set()

    def start(self):
        try:
            from core.lcd_display import lcd_manager
            self._lcd_manager = lcd_manager
            self._display_session_id = lcd_manager.begin_stream_session()
        except Exception:
            self._lcd_manager = None
        if not self._hold:
            self._play_gate.set()
        self._thread = threading.Thread(
            target=self._display_text_worker,
            daemon=True,
            name="muted-lcd-output",
        )
        self._thread.start()

    def add_sentence(self, text):
        if not self._aborted:
            self._sentence_queue.put(_fire_oled_tag(text))

    def finish(self):
        self._sentence_queue.put(None)
        self._play_gate.set()
        if self._thread:
            self._thread.join(timeout=120)
        self._end_display_session()

    def abort(self):
        self._aborted = True
        self._sentence_queue.put(None)
        self._play_gate.set()
        self._first_play_event.set()
        self._end_display_session()

    def _end_display_session(self):
        if self._lcd_manager is not None and self._display_session_id is not None:
            try:
                self._lcd_manager.end_stream_session(self._display_session_id)
            except Exception:
                pass
        self._display_session_id = None

    def _display_text_worker(self):
        self._play_gate.wait()
        wrote_text = False
        while not self._aborted:
            item = self._sentence_queue.get()
            if item is None:
                break
            try:
                text = self._lcd_manager._clean_text(item) if self._lcd_manager else str(item)
                text = romanize_hindi_for_lcd(text)
                for word in text.split():
                    if self._aborted:
                        return
                    self._lcd_page = _append_lcd_page(self._lcd_page, word)
                    if self._lcd_manager is not None:
                        self._lcd_manager.write(
                            *self._lcd_page,
                            stream_id=self._display_session_id,
                        )
                    if not self._first_play_event.is_set():
                        self._first_play_event.set()
                    wrote_text = True
                    # Comfortable LCD reading pace; runs only in mute mode.
                    time.sleep(0.18)
            except Exception:
                pass
        if wrote_text and not self._aborted:
            # Leave the final 16x2 page readable before the normal idle/listen
            # status replaces the stream session.
            time.sleep(1.5)
        if not self._first_play_event.is_set():
            self._first_play_event.set()


def TTSStreamer():
    """Factory function returning the configured TTSStreamer."""
    # One in-memory flag lookup at construction time.  The unmuted path below
    # is otherwise byte-for-byte the original TTS path, preserving TTFW.
    if is_output_muted():
        return LCDOnlyStreamer()
    if _tts_provider == "inworld":
        return InworldTTSStreamer()
    elif _tts_provider == "local":
        return LocalTTSStreamer()
    else:
        return GroqTTSStreamer()


def speak_sentence(text):
    """Simple blocking TTS for a single sentence."""
    if is_output_muted() or _tts_provider in ("inworld", "local"):
        streamer = TTSStreamer()
        streamer.start()
        streamer.add_sentence(text)
        streamer.finish()
        return

    if not _client:
        print("[TTS] Cannot speak, Groq client not initialized")
        return
        
    fd, temp_path = tempfile.mkstemp(suffix=f".{_FORMAT}")
    os.close(fd)
    try:
        response = _client.audio.speech.create(
            model=_MODEL, voice=_VOICE, input=text, response_format=_FORMAT
        )
        response.write_to_file(temp_path)
        subprocess.run(
            ["mpv", "--no-video", "--audio-device=alsa", temp_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[TTS] Error: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    print("=== TTS Streamer Test ===")
    streamer = TTSStreamer()
    streamer.start()
    streamer.add_sentence("Hello! This is the first sentence.")
    streamer.add_sentence("And this is the second sentence, generated quickly.")
    streamer.finish()
    print("Done.")
