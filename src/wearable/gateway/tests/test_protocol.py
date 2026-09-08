import pytest

from kiki_gateway.protocol import AudioFlag, AudioFrame, BinaryKind, decode_event, encode_event


def test_audio_frame_round_trip():
    flags = int(AudioFlag.AEC_PROCESSED | AudioFlag.DEVICE_VAD_SPEECH)
    frame = AudioFrame(BinaryKind.MIC_PCM_S16_16K_MONO, flags, 7, 11, 123456, b"\x01\x00\x02\x00")
    assert AudioFrame.decode(frame.encode()) == frame


@pytest.mark.parametrize("raw", [b"short", b"NOPE" + b"\x00" * 20])
def test_audio_frame_rejects_invalid_header(raw):
    with pytest.raises(ValueError):
        AudioFrame.decode(raw)


def test_control_event_round_trip_and_version_gate():
    assert decode_event(encode_event("wake", source="touch"))["source"] == "touch"
    with pytest.raises(ValueError, match="version"):
        decode_event('{"v":2,"type":"wake"}')
