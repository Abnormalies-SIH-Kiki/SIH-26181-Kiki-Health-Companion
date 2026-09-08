from pathlib import Path

import numpy as np

from kiki_gateway.silero import SileroVAD


def test_silero_model_executes_with_expected_state_shape():
    model = Path(__file__).parents[1] / "models" / "silero_vad.onnx"
    vad = SileroVAD(model)
    probability = vad.probability(np.zeros(512, dtype=np.float32))
    assert 0.0 <= probability <= 1.0
    assert vad.state.shape == (2, 1, 128)
