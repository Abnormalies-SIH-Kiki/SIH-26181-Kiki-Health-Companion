import numpy as np

from kiki_gateway.audio import Decimator48To16, PCMLinearUpsampler2x, upsample_pcm_24k_to_48k


def test_decimator_has_exact_streaming_rate_ratio():
    decimator = Decimator48To16()
    outputs = [decimator.process(np.zeros(480, dtype=np.float32)) for _ in range(100)]
    assert all(chunk.size == 160 for chunk in outputs)
    assert sum(chunk.size for chunk in outputs) == 16000


def test_stateful_upsampler_is_chunk_boundary_invariant():
    source = np.asarray([-1000, 0, 1000, 2000, -2000], dtype="<i2").tobytes()
    expected = upsample_pcm_24k_to_48k(source)
    converter = PCMLinearUpsampler2x()
    actual = b"".join(converter.process(part) for part in (source[:3], source[3:8], source[8:]))
    actual += converter.flush()
    assert actual == expected
    assert len(actual) == len(source) * 2
