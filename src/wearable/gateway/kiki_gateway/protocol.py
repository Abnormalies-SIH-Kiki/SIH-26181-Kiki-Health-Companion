from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
import json
import struct


MAGIC = b"KKA1"
HEADER = struct.Struct("<4sBBHIIQ")
PROTOCOL_VERSION = 1


class BinaryKind(IntEnum):
    MIC_PCM_S16_48K_MONO = 1
    TTS_PCM_S16_48K_MONO = 2
    MEDIA_PCM_S16_48K_MONO = 3
    # The microphone already decimated to 16 kHz by the board. Sent instead of
    # kind 1 on the remote link, where 768 kbps of 48 kHz audio does not fit.
    MIC_PCM_S16_16K_MONO = 4
    # Kiki's own voice at 16 kHz, sent instead of kind 2 on the remote link.
    TTS_PCM_S16_16K_MONO = 5
    # Music at 16 kHz, sent instead of kind 3 on the remote link. Speech
    # dropped to 16 kHz long ago; media did not, so a song over a tunnel still
    # asked for 768 kbps on a link measured at ~396 -- which is a stutter, not
    # a song.
    MEDIA_PCM_S16_16K_MONO = 6

    # Kiki's voice as Opus, 16 kHz mono, one 20 ms packet per message. Sent
    # instead of kind 5 to a board that advertises "opus" in its hello.
    #
    # This is the codec that makes a weak link usable at all. 16 kHz PCM is
    # 256 kbps and has to arrive faster than it plays, continuously; at
    # -92 dBm it simply does not, and the reply that cannot fit also blocks
    # the keepalive PING behind it on the same TCP connection -- which is why
    # a link too slow to speak on was also a link that dropped every 80 s.
    # Opus carries the same speech in ~24 kbps.
    TTS_OPUS_16K_MONO = 7


class AudioFlag(IntFlag):
    """Trusted device-side processing claims carried by microphone frames."""

    AEC_PROCESSED = 1 << 0
    DEVICE_VAD_SPEECH = 1 << 1


@dataclass(frozen=True)
class AudioFrame:
    kind: BinaryKind
    flags: int
    stream_id: int
    sequence: int
    timestamp_us: int
    payload: bytes

    def encode(self) -> bytes:
        return HEADER.pack(
            MAGIC,
            int(self.kind),
            self.flags & 0xFF,
            HEADER.size,
            self.stream_id,
            self.sequence,
            self.timestamp_us,
        ) + self.payload

    @classmethod
    def decode(cls, raw: bytes) -> "AudioFrame":
        if len(raw) < HEADER.size:
            raise ValueError("binary frame shorter than protocol header")
        magic, kind, flags, header_size, stream_id, sequence, timestamp_us = HEADER.unpack_from(raw)
        if magic != MAGIC:
            raise ValueError("invalid audio magic")
        if header_size != HEADER.size:
            raise ValueError(f"unsupported header size {header_size}")
        try:
            frame_kind = BinaryKind(kind)
        except ValueError as exc:
            raise ValueError(f"unsupported binary kind {kind}") from exc
        payload = raw[header_size:]
        if len(payload) % 2:
            raise ValueError("PCM payload must contain complete int16 samples")
        return cls(frame_kind, flags, stream_id, sequence, timestamp_us, payload)


def encode_event(event_type: str, **fields: object) -> str:
    return json.dumps({"v": PROTOCOL_VERSION, "type": event_type, **fields}, separators=(",", ":"))


def decode_event(raw: str) -> dict:
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("control event must be a JSON object")
    if obj.get("v") != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version {obj.get('v')!r}")
    if not isinstance(obj.get("type"), str):
        raise ValueError("control event is missing type")
    return obj
