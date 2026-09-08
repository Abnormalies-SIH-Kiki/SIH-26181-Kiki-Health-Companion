from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import numpy as np


FRAME_SAMPLES = 512
FRAME_MS = 32.0


@dataclass(frozen=True)
class BargeInDecision:
    """Confirmed near-end speech and the AEC-clean preroll that proved it."""

    audio: np.ndarray
    probability: float
    level_dbfs: float
    noise_floor_dbfs: float


class BargeInDetector:
    """Conservative second-stage detector for speech over Kiki's own voice.

    The device has already run full-duplex AEC, dual-mic enhancement and its
    own VAD. This detector deliberately requires all of those claims plus an
    independent Silero vote, absolute energy, an adaptive near-field margin,
    and sustained agreement. A click, one loud noise frame, residual echo, or
    a raw frame from old/failed firmware cannot stop playback.
    """

    def __init__(
        self,
        model,
        *,
        probability_threshold: float = 0.82,
        min_dbfs: float = -42.0,
        noise_margin_db: float = 6.0,
        window_frames: int = 10,
        min_votes: int = 6,
        min_consecutive: int = 3,
        preroll_frames: int = 12,
    ) -> None:
        if not 0.0 < probability_threshold < 1.0:
            raise ValueError("barge-in probability threshold must be between 0 and 1")
        if not 1 <= min_consecutive <= min_votes <= window_frames:
            raise ValueError("invalid barge-in vote configuration")
        self.model = model
        self.probability_threshold = probability_threshold
        self.min_dbfs = min_dbfs
        self.noise_margin_db = noise_margin_db
        self.window = deque(maxlen=window_frames)
        self.preroll = deque(maxlen=max(preroll_frames, window_frames))
        self.min_votes = min_votes
        self.min_consecutive = min_consecutive
        self.noise_floor_dbfs = -90.0
        self.floor_primed = False
        self.consecutive = 0

    @staticmethod
    def dbfs(frame: np.ndarray) -> float:
        values = np.asarray(frame, dtype=np.float32)
        rms = float(np.sqrt(np.mean(np.square(values, dtype=np.float64))))
        return -90.0 if rms <= 1e-9 else max(-90.0, 20.0 * math.log10(rms))

    def reset(self) -> None:
        self.window.clear()
        self.preroll.clear()
        self.consecutive = 0
        self.noise_floor_dbfs = -90.0
        self.floor_primed = False
        self.model.reset()

    def _update_floor(self, level: float, speech_like: bool) -> None:
        # Learn residual echo and room noise only from frames that neither VAD
        # regards as speech. Rise slowly so a passing noise burst cannot lift
        # the gate for seconds; follow quieter conditions faster.
        if not self.floor_primed:
            # Do not let the user's first word become the baseline. Until a
            # genuine non-speech frame arrives the absolute minimum remains
            # the active gate.
            if speech_like:
                return
            self.noise_floor_dbfs = level
            self.floor_primed = True
        elif not speech_like:
            delta = level - self.noise_floor_dbfs
            step = max(-2.0, min(0.5, delta))
            self.noise_floor_dbfs += step

    def feed(self, frame: np.ndarray, *, device_vad_speech: bool) -> BargeInDecision | None:
        samples = np.asarray(frame, dtype=np.float32)
        if samples.size != FRAME_SAMPLES:
            raise ValueError(f"barge-in requires {FRAME_SAMPLES} samples, got {samples.size}")
        samples = np.ascontiguousarray(samples.reshape(-1))
        probability = float(self.model.probability(samples))
        level = self.dbfs(samples)
        silero_speech = probability >= self.probability_threshold
        self._update_floor(level, bool(device_vad_speech or silero_speech))

        dynamic_threshold = max(
            self.min_dbfs,
            self.noise_floor_dbfs + self.noise_margin_db,
        )
        vote = bool(
            device_vad_speech
            and silero_speech
            and level >= dynamic_threshold
        )
        self.preroll.append(samples.copy())
        self.window.append(vote)
        self.consecutive = self.consecutive + 1 if vote else 0

        if (
            vote
            and self.consecutive >= self.min_consecutive
            and sum(self.window) >= self.min_votes
        ):
            decision = BargeInDecision(
                audio=np.concatenate(tuple(self.preroll)),
                probability=probability,
                level_dbfs=level,
                noise_floor_dbfs=self.noise_floor_dbfs,
            )
            # Latch once. The session immediately stops playback and switches
            # to the ordinary endpointer; another decision from these samples
            # would be a duplicate cancellation.
            self.window.clear()
            self.preroll.clear()
            self.consecutive = 0
            return decision
        return None
