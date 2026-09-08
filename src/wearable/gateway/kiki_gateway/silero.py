from __future__ import annotations

from pathlib import Path
import numpy as np
import onnxruntime as ort


class SileroVAD:
    """Pure NumPy/ONNX wrapper matching silero_vad.OnnxWrapper at 16 kHz."""

    FRAME_SAMPLES = 512
    CONTEXT_SAMPLES = 64

    def __init__(self, model_path: str | Path):
        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"], sess_options=options
        )
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, self.CONTEXT_SAMPLES), dtype=np.float32)

    def probability(self, samples: np.ndarray) -> float:
        frame = np.asarray(samples, dtype=np.float32).reshape(1, -1)
        if frame.shape[1] != self.FRAME_SAMPLES:
            raise ValueError(f"Silero requires 512 samples, got {frame.shape[1]}")
        model_input = np.concatenate((self.context, frame), axis=1)
        output, self.state = self.session.run(
            None,
            {
                "input": model_input,
                "state": self.state,
                "sr": np.asarray(16000, dtype=np.int64),
            },
        )
        self.context = model_input[:, -self.CONTEXT_SAMPLES :]
        return float(np.asarray(output).reshape(-1)[0])
