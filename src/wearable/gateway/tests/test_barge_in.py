import numpy as np
import pytest

from kiki_gateway.barge_in import BargeInDetector
from kiki_gateway.config import GatewayConfig
from kiki_gateway.session import DeviceSession


class FakeVAD:
    def __init__(self, probabilities):
        self.probabilities = iter(probabilities)
        self.reset_count = 0

    def probability(self, _frame):
        return next(self.probabilities)

    def reset(self):
        self.reset_count += 1


def frame(amplitude: float) -> np.ndarray:
    # Alternating signs avoid treating the test signal as DC while retaining a
    # precisely controlled RMS level.
    values = np.full(512, amplitude, dtype=np.float32)
    values[1::2] *= -1
    return values


def test_requires_board_vad_even_when_model_is_confident():
    detector = BargeInDetector(FakeVAD([0.99] * 12))
    assert all(
        detector.feed(frame(0.12), device_vad_speech=False) is None
        for _ in range(12)
    )


def test_requires_independent_model_even_when_board_vad_fires():
    detector = BargeInDetector(FakeVAD([0.1] * 12))
    assert all(
        detector.feed(frame(0.12), device_vad_speech=True) is None
        for _ in range(12)
    )


def test_adaptive_floor_rejects_speech_like_background_at_same_level():
    detector = BargeInDetector(FakeVAD([0.05] * 4 + [0.99] * 12))
    for _ in range(4):
        assert detector.feed(frame(0.04), device_vad_speech=False) is None
    assert all(
        detector.feed(frame(0.04), device_vad_speech=True) is None
        for _ in range(12)
    )


def test_short_transient_cannot_interrupt():
    detector = BargeInDetector(FakeVAD([0.99] * 2 + [0.05] + [0.99] * 2))
    results = [
        detector.feed(frame(0.12), device_vad_speech=value)
        for value in (True, True, False, True, True)
    ]
    assert results == [None] * 5


def test_sustained_near_end_speech_returns_complete_preroll():
    detector = BargeInDetector(FakeVAD([0.05] * 3 + [0.99] * 6))
    for _ in range(3):
        assert detector.feed(frame(0.01), device_vad_speech=False) is None
    decision = None
    for _ in range(6):
        decision = detector.feed(frame(0.12), device_vad_speech=True)
    assert decision is not None
    assert decision.probability == pytest.approx(0.99)
    assert decision.level_dbfs > -20.0
    assert decision.noise_floor_dbfs == pytest.approx(-40.0, abs=0.1)
    assert decision.audio.size == 9 * 512


def test_first_user_word_is_not_learned_as_noise_floor():
    detector = BargeInDetector(FakeVAD([0.99] * 6))
    decision = None
    for _ in range(6):
        decision = detector.feed(frame(0.02), device_vad_speech=True)
    assert decision is not None
    assert decision.noise_floor_dbfs == -90.0


def test_rejects_wrong_frame_size_and_invalid_vote_configuration():
    detector = BargeInDetector(FakeVAD([0.9]))
    with pytest.raises(ValueError, match="512"):
        detector.feed(np.zeros(160, dtype=np.float32), device_vad_speech=True)
    with pytest.raises(ValueError, match="vote configuration"):
        BargeInDetector(FakeVAD([]), window_frames=3, min_votes=4)


@pytest.mark.asyncio
async def test_device_setting_disables_barge_in_for_the_live_session():
    session = DeviceSession.__new__(DeviceSession)
    session.config = GatewayConfig()
    session.barge_in_enabled = True
    session.barge_in = FakeVAD([])

    await session.handle_control({"type": "set_barge_in", "enabled": False})

    assert session.barge_in_enabled is False
    assert session.barge_in.reset_count == 1
