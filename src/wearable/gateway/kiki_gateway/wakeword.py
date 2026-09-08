from __future__ import annotations

from pathlib import Path
import time
import numpy as np


class WakeWordDetector:
    CHUNK = 1280

    def __init__(self, model_path: str | Path, threshold: float = 0.5):
        from openwakeword.model import Model

        self.model = Model(
            wakeword_models=[str(model_path)],
            vad_threshold=0.5,
            inference_framework="onnx",
        )
        self.key = next(iter(self.model.models))
        self.threshold = threshold
        self.pending = np.zeros(0, dtype=np.int16)
        self.last_detection = 0.0

    def feed(self, float_audio: np.ndarray) -> float | None:
        pcm = (np.clip(float_audio, -1, 1) * 32767).astype(np.int16)
        self.pending = np.concatenate((self.pending, pcm))
        detected = None
        while self.pending.size >= self.CHUNK:
            chunk, self.pending = self.pending[: self.CHUNK], self.pending[self.CHUNK :]
            score = float(self.model.predict(chunk)[self.key])
            now = time.monotonic()
            if score >= self.threshold and now - self.last_detection >= 4.5:
                self.last_detection = now
                detected = score
        return detected

    def reset(self) -> None:
        self.pending = np.zeros(0, dtype=np.int16)
        self.model.reset()
