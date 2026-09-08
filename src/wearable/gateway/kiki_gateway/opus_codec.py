"""Opus encoding for Kiki's voice on a link that cannot carry PCM.

16 kHz 16-bit PCM is 256 kbps and it has to arrive faster than it plays,
continuously, or the speaker runs dry. On a weak link it does not -- and the
audio that cannot fit also sits ahead of the WebSocket keepalive PING on the
same TCP connection, so a link too slow to speak on is also a link that drops
every eighty seconds. Opus carries the same speech in a fraction of that.

Only the downlink is encoded. The microphone stays PCM on purpose: it is
already gated down to a ~5% duty cycle by the board's thrifty uplink, so it is
not what saturates the link, and putting a lossy codec in front of Whisper is a
transcription-accuracy question that deserves measurement rather than a guess.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
# 20 ms. Opus only accepts 2.5/5/10/20/40/60 ms, and 20 ms is the usual
# speech trade-off: longer frames amortise the 24-byte protocol header better
# but add their whole length to time-to-first-word.
FRAME_SAMPLES = SAMPLE_RATE // 50
FRAME_BYTES = FRAME_SAMPLES * 2


class OpusUnavailable(RuntimeError):
    """opuslib or libopus is missing; the caller should stay on PCM."""


class TtsOpusEncoder:
    """Re-frames a 16 kHz PCM stream into whole Opus packets.

    The TTS chunks arriving from the synthesizer are of arbitrary length, and
    Opus will only encode exact frame sizes, so whatever does not fill a frame
    is carried into the next chunk.
    """

    def __init__(self, bitrate: int) -> None:
        try:
            import opuslib
        except Exception as exc:  # pragma: no cover - exercised by the fallback
            raise OpusUnavailable(str(exc)) from exc
        self._encoder = opuslib.Encoder(
            SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP
        )
        self._encoder.bitrate = bitrate
        self.bitrate = bitrate
        self._tail = b""

    def reset(self) -> None:
        """Drop the partial frame carried between replies."""
        self._tail = b""

    def encode(self, pcm: bytes) -> list[bytes]:
        """Return every whole Opus packet this PCM completes."""
        buffer = self._tail + pcm
        packets: list[bytes] = []
        offset = 0
        while offset + FRAME_BYTES <= len(buffer):
            frame = buffer[offset : offset + FRAME_BYTES]
            offset += FRAME_BYTES
            packets.append(self._encoder.encode(frame, FRAME_SAMPLES))
        self._tail = buffer[offset:]
        return packets

    def flush(self) -> list[bytes]:
        """Emit the final partial frame, zero-padded, and clear the carry.

        Without this the last fraction of a frame of every reply is dropped.
        It is inaudible, but the alternative is that the carry leaks into the
        start of the *next* reply, which is not.
        """
        if not self._tail:
            return []
        frame = self._tail.ljust(FRAME_BYTES, b"\x00")
        self._tail = b""
        return [self._encoder.encode(frame, FRAME_SAMPLES)]
