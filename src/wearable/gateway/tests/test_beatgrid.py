"""Tempo and downbeat, measured from the audio.

The numbers asserted here are the ones the dance depends on, and both matter
for different reasons:

* **phase** decides whether a pose lands on the beat or beside it;
* **tempo** decides whether it is still landing on the beat three minutes in.
  0.2 % of tempo error is 0.4 s of drift by the end of a song -- a quarter of a
  beat -- which is why the fit is refined against the waveform rather than
  taken from the autocorrelation peak.
"""

import numpy as np
import pytest

from kiki_gateway.beatgrid import ANALYSIS_RATE, analyse


def click_track(bpm: float, seconds: float = 60.0, phase: float = 0.0,
                noise: float = 0.005, offbeat: float = 0.0,
                seed: int = 11) -> np.ndarray:
    """A kick on every beat, an accent every fourth, optional offbeat hats."""
    rng = np.random.default_rng(seed)
    samples = int(seconds * ANALYSIS_RATE)
    audio = (rng.standard_normal(samples) * noise).astype(np.float32)
    envelope = np.exp(-np.arange(int(ANALYSIS_RATE * 0.08)) / (ANALYSIS_RATE * 0.01))
    period = 60.0 / bpm
    beat = 0
    time = phase
    while time < seconds:
        tone = np.sin(2 * np.pi * (90 if beat % 4 == 0 else 180) *
                      np.arange(envelope.size) / ANALYSIS_RATE)
        hit = (envelope * tone).astype(np.float32) * (1.0 if beat % 4 == 0 else 0.6)
        start = int(time * ANALYSIS_RATE)
        room = max(0, min(hit.size, samples - start))
        audio[start:start + room] += hit[:room]
        if offbeat:
            start = int((time + period / 2) * ANALYSIS_RATE)
            room = max(0, min(hit.size, samples - start))
            audio[start:start + room] += hit[:room] * offbeat
        time += period
        beat += 1
    return audio


def phase_error(grid, bpm: float, phase: float) -> float:
    period = 60.0 / bpm
    return abs(((grid.beat0 - phase + period / 2) % period) - period / 2)


@pytest.mark.parametrize("bpm", [70.0, 90.0, 100.0, 120.0, 128.0, 140.0, 160.0])
@pytest.mark.parametrize("phase", [0.0, 0.137, 0.31])
def test_tempo_and_downbeat_are_recovered(bpm, phase):
    grid = analyse(click_track(bpm, phase=phase))
    assert grid is not None
    # Octave-tolerant: hearing 150 as 75 is a musical choice, not an error, and
    # the routine still lands on real beats either way.
    ratio = grid.bpm / bpm
    assert min(abs(ratio - 1), abs(ratio - 0.5), abs(ratio - 2)) < 0.002
    assert phase_error(grid, grid.bpm, phase) < 0.020


def test_tempo_is_accurate_enough_not_to_drift_over_a_whole_song():
    grid = analyse(click_track(128.0, phase=0.05))
    drift_ms = abs(grid.bpm - 128.0) / 128.0 * 180.0 * 1000.0
    assert drift_ms < 120.0


def test_offbeat_hats_do_not_double_the_tempo():
    grid = analyse(click_track(128.0, phase=0.05, offbeat=0.4))
    assert 127.0 < grid.bpm < 129.0
    assert phase_error(grid, grid.bpm, 0.05) < 0.020


def test_silence_has_no_beat():
    assert analyse(np.zeros(ANALYSIS_RATE * 10, dtype=np.float32)) is None


def test_a_clip_too_short_to_measure_is_refused():
    assert analyse((np.random.default_rng(3).standard_normal(ANALYSIS_RATE * 2)
                    ).astype(np.float32)) is None


def test_confidence_separates_a_beat_from_noise():
    music = analyse(click_track(120.0))
    noise = analyse((np.random.default_rng(5).standard_normal(ANALYSIS_RATE * 20) * 0.2
                     ).astype(np.float32))
    assert music.confidence > 0.5
    # Noise may still produce a "tempo"; what matters is that it is not
    # trusted, because the caller prefers the model's hint below 0.25.
    assert noise is None or noise.confidence < 0.25


def test_energy_is_one_value_per_beat_in_range():
    grid = analyse(click_track(120.0, seconds=30.0))
    assert 50 < len(grid.energy) <= 60
    assert all(0 <= value <= 15 for value in grid.energy)


def test_the_first_beat_is_reported_inside_the_first_bar():
    grid = analyse(click_track(96.0, phase=0.4))
    assert 0.0 <= grid.beat0 < grid.beat_seconds
