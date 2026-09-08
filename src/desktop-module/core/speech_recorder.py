"""
Background archive of everything Kiki actually speaks.

Enabled by ``record_enabled`` in config.json. When on, the exact PCM handed to
the audio sink is copied into memory and written to a wav file in
``kiki_speeches/`` by a single daemon writer thread.

Latency contract (this is the whole point of the module):
  - the capture side does nothing but ``list.append`` on a bytes object the
    caller already holds — no copy, no encode, no I/O, no lock contention with
    the writer;
  - the capture call sites sit AFTER the write to aplay, so time-to-first-word
    is untouched;
  - closing a recording only hands the buffer to a queue; the wav encode and
    the disk write happen on the writer thread, never on the speaking path.
"""

import os
import re
import threading
import unicodedata
import wave
from queue import Queue

from tools_and_config.config_loader import get_full_config

_CFG = get_full_config()
_ENABLED = bool(_CFG.get("record_enabled", False))
_DIR = _CFG.get(
    "record_dir",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "state/speeches"),
)

# Safety cap so a runaway/very long reply can never eat the Pi's RAM.
# 24 kHz * 2 bytes = 48 kB/s, so this is ~10 minutes of speech.
_MAX_BYTES = 30 * 1024 * 1024

_TAG_RE = re.compile(r"\[[^\]]*\]|<[^>]*>")

def _keep_char(ch):
    """Filename-safe characters. Combining marks are kept explicitly: \\w drops
    them, which silently mangles Devanagari (नमस्ते -> नमसत)."""
    return ch.isalnum() or ch in "-_" or unicodedata.category(ch).startswith("M")

_queue = Queue()
_writer_thread = None
_writer_lock = threading.Lock()


def is_enabled():
    return _ENABLED


def _first_two_words(text):
    words = _TAG_RE.sub(" ", text or "").split()
    parts = []
    for word in words:
        cleaned = "".join(ch for ch in word if _keep_char(ch))
        if cleaned:
            parts.append(cleaned)
        if len(parts) == 2:
            break
    return "_".join(parts)[:60] or "speech"


def _unique_path(stem):
    path = os.path.join(_DIR, f"{stem}.wav")
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(_DIR, f"{stem}_{n}.wav")
    return path


def _writer_loop():
    while True:
        item = _queue.get()
        if item is None:
            break
        stem, chunks, sample_rate = item
        try:
            os.makedirs(_DIR, exist_ok=True)
            path = _unique_path(stem)
            with wave.open(path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(b"".join(chunks))
            print(f"[Recorder] Saved {os.path.basename(path)}")
        except Exception as e:
            print(f"[Recorder] Failed to save speech: {e}")


def _ensure_writer():
    global _writer_thread
    with _writer_lock:
        if _writer_thread is None or not _writer_thread.is_alive():
            _writer_thread = threading.Thread(
                target=_writer_loop, daemon=True, name="speech-recorder")
            _writer_thread.start()


class SpeechRecording:
    """One assistant response. Not thread-safe by design: it is fed from the
    single playback thread only."""

    __slots__ = ("_chunks", "_bytes", "_stem", "_sample_rate", "_closed")

    def __init__(self, sample_rate):
        self._chunks = []
        self._bytes = 0
        self._stem = ""
        self._sample_rate = sample_rate
        self._closed = False

    def note_text(self, text):
        """First spoken sentence names the file; later ones are ignored."""
        if not self._stem and text:
            self._stem = _first_two_words(text)

    def feed(self, pcm):
        if self._bytes >= _MAX_BYTES:
            return
        self._chunks.append(pcm)
        self._bytes += len(pcm)

    def close(self):
        """Hand the buffer to the writer thread. Never blocks on I/O."""
        if self._closed:
            return
        self._closed = True
        if not self._chunks:
            return
        chunks, self._chunks = self._chunks, []
        try:
            _ensure_writer()
            _queue.put((self._stem or "speech", chunks, self._sample_rate))
        except Exception as e:
            print(f"[Recorder] Could not queue speech for saving: {e}")


def new_recording(sample_rate):
    """Return a recorder for one response, or None when recording is off."""
    if not _ENABLED:
        return None
    return SpeechRecording(sample_rate)
