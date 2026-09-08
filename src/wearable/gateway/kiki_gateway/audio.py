from __future__ import annotations

import ctypes
import importlib.metadata
import math
from pathlib import Path
import time
import numpy as np


RNNOISE_FRAME = 480


def _rnnoise_library() -> Path:
    dist = importlib.metadata.distribution("pyrnnoise")
    direct = Path(dist.locate_file("pyrnnoise/librnnoise.so"))
    if direct.is_file():
        return direct
    for entry in dist.files or ():
        if entry.name == "librnnoise.so":
            candidate = Path(dist.locate_file(entry))
            if candidate.is_file():
                return candidate
    raise RuntimeError("pyrnnoise is installed without librnnoise.so")


class RNNoise48k:
    def __init__(self, max_process_ms: float = 5.0, slow_limit: int = 3):
        self.lib = ctypes.CDLL(str(_rnnoise_library()))
        self.lib.rnnoise_create.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_create.restype = ctypes.c_void_p
        self.lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_process_frame.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
        ]
        self.lib.rnnoise_process_frame.restype = ctypes.c_float
        self.max_process_ms = max_process_ms
        self.slow_limit = slow_limit
        self.slow_count = 0
        self.active = True
        self.state = self.lib.rnnoise_create(None)
        if not self.state:
            raise RuntimeError("rnnoise_create failed")

    def process(self, pcm: np.ndarray) -> np.ndarray:
        raw = np.asarray(pcm, dtype=np.int16)
        if raw.size != RNNOISE_FRAME:
            raise ValueError("RNNoise requires 480 samples")
        work = raw.astype(np.float32)
        if not self.active:
            return work / 32768.0
        ptr = work.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        started = time.perf_counter()
        self.lib.rnnoise_process_frame(self.state, ptr, ptr)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.slow_count = self.slow_count + 1 if elapsed_ms > self.max_process_ms else 0
        if self.slow_count >= self.slow_limit:
            self.active = False
        return np.clip(work / 32768.0, -1.0, 1.0)

    def reset(self) -> None:
        if self.state:
            self.lib.rnnoise_destroy(self.state)
        self.state = self.lib.rnnoise_create(None)
        self.slow_count = 0

    def close(self) -> None:
        if self.state:
            self.lib.rnnoise_destroy(self.state)
            self.state = None


class Decimator48To16:
    def __init__(self, taps: int = 63, cutoff_hz: float = 7200.0):
        mid = (taps - 1) / 2
        pos = np.arange(taps, dtype=np.float64) - mid
        fc = cutoff_hz / 48000.0
        kernel = 2 * fc * np.sinc(2 * fc * pos) * np.hamming(taps)
        self.kernel = (kernel / np.sum(kernel)).astype(np.float32)
        self.history = np.zeros(taps - 1, dtype=np.float32)

    def process(self, samples: np.ndarray) -> np.ndarray:
        combined = np.concatenate((self.history, np.asarray(samples, dtype=np.float32)))
        filtered = np.convolve(combined, self.kernel, mode="valid")
        self.history = combined[-self.history.size :].copy()
        return np.ascontiguousarray(filtered[::3], dtype=np.float32)

    def reset(self) -> None:
        self.history.fill(0)


class NearFieldGate:
    def __init__(self, engage_dbfs=-50.0, open_margin=9.0, close_margin=4.0, frame_ms=32.0):
        self.engage_dbfs = engage_dbfs
        self.open_margin = open_margin
        self.close_margin = min(close_margin, open_margin)
        self.rise_step = 3.0 * frame_ms / 1000.0
        self.fall_step = 30.0 * frame_ms / 1000.0
        self.floor = -90.0
        self.open = False
        self.primed = False

    @staticmethod
    def dbfs(frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        return -90.0 if rms <= 1e-9 else max(-90.0, 20.0 * math.log10(rms))

    def update(self, frame: np.ndarray, speech: bool) -> bool:
        level = self.dbfs(frame)
        if not self.primed:
            self.floor, self.primed = level, True
        elif level < self.floor:
            self.floor = max(level, self.floor - self.fall_step)
        elif not speech:
            self.floor = min(level, self.floor + self.rise_step)
        if self.floor < self.engage_dbfs:
            self.open = False
            return True
        threshold = self.floor + (self.close_margin if self.open else self.open_margin)
        self.open = level >= threshold
        return self.open


class PCMLinearUpsampler2x:
    """Stateful 24-to-48 kHz resampler with continuity across HTTP chunks."""

    def __init__(self) -> None:
        self.pending_byte = b""
        self.previous: np.int16 | None = None

    def process(self, pcm: bytes) -> bytes:
        raw = self.pending_byte + pcm
        self.pending_byte = raw[-1:] if len(raw) % 2 else b""
        raw = raw[: len(raw) - len(self.pending_byte)]
        source = np.frombuffer(raw, dtype="<i2")
        if self.previous is not None:
            source = np.concatenate((np.asarray([self.previous], dtype=np.int16), source))
        if source.size == 0:
            return b""
        self.previous = source[-1]
        if source.size == 1:
            return b""
        output = np.empty((source.size - 1) * 2, dtype=np.int16)
        output[0::2] = source[:-1]
        output[1::2] = (
            (source[:-1].astype(np.int32) + source[1:].astype(np.int32)) // 2
        ).astype(np.int16)
        return output.tobytes()

    def flush(self) -> bytes:
        if self.pending_byte:
            raise ValueError("TTS stream ended with an incomplete int16 sample")
        if self.previous is None:
            return b""
        tail = np.asarray([self.previous, self.previous], dtype="<i2").tobytes()
        self.previous = None
        return tail


def upsample_pcm_24k_to_48k(pcm: bytes) -> bytes:
    resampler = PCMLinearUpsampler2x()
    return resampler.process(pcm) + resampler.flush()
