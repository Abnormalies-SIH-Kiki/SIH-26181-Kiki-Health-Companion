"""The speech codec that makes a weak link usable.

These guard the two things that are silent when wrong: the framing contract
with libopus (which only accepts exact frame sizes) and the kind number, which
lives in two files that no compiler checks against each other.
"""

import math
import re
import struct
from pathlib import Path

import pytest

from kiki_gateway.opus_codec import FRAME_BYTES, FRAME_SAMPLES, TtsOpusEncoder
from kiki_gateway.protocol import BinaryKind

opuslib = pytest.importorskip("opuslib")

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_HPP = ROOT / "firmware" / "main" / "protocol.hpp"

BITRATE = 32000


def _tone(samples: int, hz: float = 220.0, rate: int = 16000) -> bytes:
    """Something with structure -- silence encodes to nine bytes and would hide
    a bitrate mistake completely."""
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * n / rate)))
        for n in range(samples)
    )


def test_arbitrary_chunk_sizes_produce_whole_frames_and_carry_the_rest():
    encoder = TtsOpusEncoder(BITRATE)
    # Deliberately coprime with the frame size: the TTS synthesizer's chunks
    # never line up with 20 ms, which is the whole reason for the carry.
    chunk = _tone(457)
    packets = []
    for _ in range(20):
        packets.extend(encoder.encode(chunk))
    fed = 457 * 20
    assert len(packets) == fed // FRAME_SAMPLES
    # Everything that did not fill a frame is still held, not dropped.
    assert len(encoder._tail) == (fed % FRAME_SAMPLES) * 2
    packets.extend(encoder.flush())
    assert encoder._tail == b""


def test_every_packet_decodes_to_exactly_one_frame_of_pcm():
    encoder = TtsOpusEncoder(BITRATE)
    decoder = opuslib.Decoder(16000, 1)
    packets = encoder.encode(_tone(FRAME_SAMPLES * 8))
    assert packets
    for packet in packets:
        assert len(decoder.decode(packet, FRAME_SAMPLES)) == FRAME_BYTES


def test_reset_drops_the_carry_so_one_reply_cannot_prefix_the_next():
    encoder = TtsOpusEncoder(BITRATE)
    encoder.encode(_tone(100))
    assert encoder._tail
    encoder.reset()
    assert encoder._tail == b""
    assert encoder.flush() == []


def test_the_codec_is_actually_smaller_than_the_pcm_it_replaces():
    encoder = TtsOpusEncoder(BITRATE)
    speech = _tone(FRAME_SAMPLES * 50)  # one second
    packets = encoder.encode(speech)
    on_the_wire = sum(len(p) + 24 for p in packets)  # + protocol header
    pcm_16k = len(speech)
    # The entire point of the change. 16 kHz PCM is 32000 bytes per second;
    # if this ratio ever collapses the link stops working again.
    assert on_the_wire < pcm_16k / 4, (on_the_wire, pcm_16k)


def test_the_kind_number_matches_the_firmware():
    header = PROTOCOL_HPP.read_text()
    match = re.search(r"TtsOpus16k\s*=\s*(\d+)", header)
    assert match, "firmware protocol.hpp has no TtsOpus16k"
    assert int(match.group(1)) == int(BinaryKind.TTS_OPUS_16K_MONO)
