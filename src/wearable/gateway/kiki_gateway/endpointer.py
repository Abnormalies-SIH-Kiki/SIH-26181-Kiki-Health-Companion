from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import time
import numpy as np

from .audio import NearFieldGate


FRAME_SAMPLES = 512
FRAME_MS = 32.0


class ActionKind(str, Enum):
    SPEECH_START = "speech_start"
    PARTIAL = "partial"
    SPECULATIVE = "speculative"
    INVALIDATE = "invalidate"
    COMMIT = "commit"


@dataclass(frozen=True)
class EndpointAction:
    kind: ActionKind
    generation: int
    audio: np.ndarray | None = None


class StreamingEndpointer:
    def __init__(self, model, config):
        self.model = model
        self.config = config
        self.preroll = deque(maxlen=max(1, int(config.preroll_ms / FRAME_MS)))
        self.gate = NearFieldGate(
            config.near_field_engage_dbfs,
            config.near_field_open_margin_db,
            config.near_field_close_margin_db,
            FRAME_MS,
        )
        self.generation = 0
        self.reset(active=False)

    def reset(self, active: bool = False) -> None:
        self.in_speech = active
        self.frames: list[np.ndarray] = []
        self.speech_ms = 0.0
        self.silence_ms = 0.0
        self.spec_fired = False
        self.last_partial_ms = 0.0
        self.model.reset()

    def _snapshot(self) -> np.ndarray:
        return np.concatenate(self.frames) if self.frames else np.zeros(0, dtype=np.float32)

    def force_commit(self) -> list[EndpointAction]:
        """End the utterance now, the way releasing a hold-to-talk does.

        KikiFast's IR hold does not wait out the silence window on release; it
        force-commits so Kiki answers immediately. Without this the user lets go
        and then waits another endpoint_silence_ms for nothing.
        """
        actions: list[EndpointAction] = []
        if self.in_speech and self.speech_ms >= self.config.min_speech_ms:
            actions.append(EndpointAction(ActionKind.COMMIT, self.generation, self._snapshot()))
        self.preroll.clear()
        self.reset(active=False)
        return actions

    def feed(
        self,
        samples: np.ndarray,
        now: float | None = None,
        defer_commit: bool = False,
    ) -> list[EndpointAction]:
        """Feed one VAD frame.

        ``defer_commit`` is the physical push-to-talk contract: speech and
        partial captions are still collected while the finger is down, but
        silence cannot speculate or close the utterance. Release calls
        ``force_commit`` and is the only endpoint for a held press.
        """
        del now
        frame = np.asarray(samples, dtype=np.float32)
        probability = self.model.probability(frame)
        voiced = probability >= self.config.vad_threshold
        if self.config.near_field_enabled:
            voiced = voiced and self.gate.update(frame, voiced)
        actions: list[EndpointAction] = []

        if not self.in_speech:
            self.preroll.append(frame.copy())
            if not voiced:
                return actions
            self.generation += 1
            self.in_speech = True
            self.frames = list(self.preroll)
            self.speech_ms = FRAME_MS
            self.silence_ms = 0.0
            actions.append(EndpointAction(ActionKind.SPEECH_START, self.generation))
            return actions

        self.frames.append(frame.copy())
        if voiced:
            if self.spec_fired:
                self.spec_fired = False
                actions.append(EndpointAction(ActionKind.INVALIDATE, self.generation))
            self.speech_ms += FRAME_MS
            self.silence_ms = 0.0
            if (
                self.config.partial_asr_interval_ms > 0
                and self.speech_ms - self.last_partial_ms >= self.config.partial_asr_interval_ms
            ):
                self.last_partial_ms = self.speech_ms
                actions.append(EndpointAction(ActionKind.PARTIAL, self.generation, self._snapshot()))
        else:
            self.silence_ms += FRAME_MS
            if (
                not defer_commit
                and
                not self.spec_fired
                and self.speech_ms >= self.config.min_speech_ms
                and self.silence_ms >= self.config.speculative_silence_ms
            ):
                self.spec_fired = True
                actions.append(EndpointAction(ActionKind.SPECULATIVE, self.generation, self._snapshot()))

            elapsed = (self.speech_ms + self.silence_ms) / 1000.0
            if not defer_commit and (
                self.silence_ms >= self.config.endpoint_silence_ms
                or elapsed >= self.config.max_utterance_seconds
            ):
                if self.speech_ms >= self.config.min_speech_ms:
                    actions.append(EndpointAction(ActionKind.COMMIT, self.generation, self._snapshot()))
                self.preroll.clear()
                self.reset(active=False)
        return actions
