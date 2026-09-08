"""Tempo, downbeat phase and per-beat energy, from the audio itself.

WHY THIS EXISTS
---------------
A dance is only convincing when the pose *lands* on the beat, and nothing else
in this system knows where the beats are. A language model asked for a song's
BPM answers from memory: right for famous tracks, confidently wrong for the
rest, and never right about *phase* -- which matters more, because a routine
half a beat out of step looks worse than one at the wrong tempo.

So the tempo comes out of the waveform. yt-dlp has already resolved a playable
URL by the time this runs, so one extra short ffmpeg pass over the first minute
gives a real beat grid before the first note reaches the speaker.

Deliberately numpy-only: librosa would do this in three lines and costs a
150 MB dependency tree on a laptop that also has to hold llama.cpp, Whisper and
a TTS server in memory. The three stages below (spectral flux -> autocorrelation
-> comb-filter phase) are the parts of that library this actually needs.

Nothing here is allowed to break playback. Every failure path returns None and
the caller falls back to the model's BPM hint; a dance slightly out of step is
a much smaller problem than a song that never starts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import math
import sys

import numpy as np


LOG = logging.getLogger(__name__)

# 22.05 kHz is plenty: everything that marks a beat (kick, snare, transients)
# lives well below 11 kHz, and halving the rate halves the analysis cost.
ANALYSIS_RATE = 22050
HOP = 256            # 11.6 ms per onset-envelope frame
WINDOW = 1024

# The search range. Below 60 BPM a routine has nothing to move to; above 180
# the detector is almost always locked onto a half-beat hi-hat rather than the
# pulse a person would clap.
MIN_BPM = 60.0
MAX_BPM = 180.0
# Where dance music actually lives. Used as a prior, not a limit, so a genuine
# 72 BPM ballad still wins when the evidence for it is strong.
PRIOR_CENTRE_BPM = 120.0
PRIOR_WIDTH_OCTAVES = 0.9


@dataclass(frozen=True)
class BeatGrid:
    """Where the beats are, and how hard each one hits."""

    bpm: float
    # Seconds into the song of the first beat. Always in [0, one beat).
    beat0: float
    # 0..1. Below `dance.min_beat_confidence` the caller prefers its own hint.
    confidence: float
    # One 0..15 loudness value per beat, from beat0 onward. The board uses it
    # to drive the stage lighting and the equaliser without doing any DSP.
    energy: tuple[int, ...]
    analysed_seconds: float

    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.5


def _onset_envelope(samples: np.ndarray) -> np.ndarray:
    """Half-wave-rectified spectral flux: how much new energy each frame adds.

    This is what makes percussive attacks stand out from sustained notes --
    a held chord contributes nothing, a kick contributes a spike across the
    whole spectrum.
    """
    frames = 1 + (samples.size - WINDOW) // HOP
    if frames < 8:
        return np.zeros(0, dtype=np.float32)
    # One strided view over the signal rather than a Python loop per frame.
    strided = np.lib.stride_tricks.as_strided(
        samples,
        shape=(frames, WINDOW),
        strides=(samples.strides[0] * HOP, samples.strides[0]),
        writeable=False,
    )
    window = np.hanning(WINDOW).astype(np.float32)
    spectra = np.abs(np.fft.rfft(strided * window, axis=1)).astype(np.float32)
    # Log compression keeps a quiet intro's beats comparable with a loud
    # chorus's; without it the autocorrelation is dominated by whichever
    # section happens to be loudest.
    spectra = np.log1p(spectra * 8.0)
    flux = np.diff(spectra, axis=0)
    np.maximum(flux, 0.0, out=flux)
    envelope = flux.sum(axis=1)
    # Subtract a local mean so a long crescendo does not read as one huge
    # onset, then rectify again.
    if envelope.size >= 16:
        kernel = np.ones(15, dtype=np.float32) / 15.0
        local = np.convolve(envelope, kernel, mode="same")
        envelope = np.maximum(envelope - local, 0.0)
    peak = float(envelope.max()) if envelope.size else 0.0
    if peak <= 0.0:
        return np.zeros(0, dtype=np.float32)
    return (envelope / peak).astype(np.float32)


def _autocorrelate(envelope: np.ndarray) -> np.ndarray:
    """Unbiased autocorrelation of the onset envelope, via FFT."""
    centred = envelope - envelope.mean()
    size = 1 << int(math.ceil(math.log2(centred.size * 2)))
    spectrum = np.fft.rfft(centred, size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum), size)[: centred.size]
    # Each lag is the sum of fewer overlapping products than the one before it;
    # without this correction long lags (slow tempi) are systematically
    # under-scored and the detector doubles the tempo of every slow song.
    counts = np.arange(centred.size, 0, -1, dtype=np.float32)
    correlation = correlation / np.maximum(counts, 1.0)
    if correlation[0] > 0:
        correlation = correlation / correlation[0]
    return correlation.astype(np.float32)


def _tempo_prior(bpm: np.ndarray) -> np.ndarray:
    octaves = np.log2(np.maximum(bpm, 1e-6) / PRIOR_CENTRE_BPM)
    return np.exp(-0.5 * (octaves / PRIOR_WIDTH_OCTAVES) ** 2).astype(np.float32)


def _estimate_tempo(envelope: np.ndarray, env_rate: float) -> tuple[float, float]:
    """Best tempo in BPM plus a 0..1 confidence."""
    correlation = _autocorrelate(envelope)
    min_lag = max(2, int(round(env_rate * 60.0 / MAX_BPM)))
    max_lag = min(correlation.size - 1, int(round(env_rate * 60.0 / MIN_BPM)))
    if max_lag <= min_lag:
        return 0.0, 0.0
    lags = np.arange(min_lag, max_lag + 1)
    scores = correlation[min_lag : max_lag + 1] * _tempo_prior(env_rate * 60.0 / lags)
    if not scores.size or float(scores.max()) <= 0.0:
        return 0.0, 0.0
    best = int(np.argmax(scores))
    lag = float(lags[best])
    # Parabolic interpolation around the peak: the lag grid is coarse at fast
    # tempi (one frame is ~2 BPM at 140), and a systematic error there walks
    # the routine off the beat over a three-minute song.
    if 0 < best < scores.size - 1:
        left, centre, right = (float(scores[best - 1]), float(scores[best]),
                               float(scores[best + 1]))
        denominator = left - 2.0 * centre + right
        if denominator != 0.0:
            lag += 0.5 * (left - right) / denominator
    bpm = env_rate * 60.0 / lag
    # How far the winning lag stands above the field, in absolute correlation.
    # Measured on synthetic material: a click track peaks near 0.95, band noise
    # and speech-shaped noise sit under 0.09 -- and the ratio forms that looked
    # natural here (peak/max, peak/mean) score noise as 1.0, because dividing a
    # peak by itself says nothing. This one separates them by an order of
    # magnitude, which is what the caller needs to decide whether to trust the
    # grid over the model's BPM hint.
    prominence = (float(scores.max()) - float(np.median(scores))) / 0.35
    return float(bpm), float(np.clip(prominence, 0.0, 1.0))


def _estimate_phase(envelope: np.ndarray, env_rate: float, bpm: float) -> float:
    """Where the first beat sits, in seconds.

    A comb filter over the whole envelope: the offset whose pulse train lines
    up with the most onset energy. Tempo without phase is useless here -- being
    consistently half a beat late is the single most obvious way an animation
    reads as "not actually dancing".
    """
    period = env_rate * 60.0 / bpm
    if period < 2.0 or envelope.size < period * 4:
        return 0.0
    offsets = np.arange(0.0, period, 0.25)
    pulses = np.arange(0, int((envelope.size - 1) / period))
    if pulses.size < 4:
        return 0.0
    positions = np.rint(offsets[:, None] + pulses[None, :] * period).astype(np.int32)
    np.clip(positions, 0, envelope.size - 1, out=positions)
    scores = envelope[positions].sum(axis=1)
    best_offset = float(offsets[int(np.argmax(scores))])
    return best_offset / env_rate


def _refine_grid(samples: np.ndarray, bpm: float, coarse: float) -> tuple[float, float]:
    """Fit tempo AND phase to the waveform, to about a millisecond.

    Two separate biases make the coarse estimate above unusable on its own for
    a three-minute song:

    * **Phase.** The spectral-flux envelope has one value per 11.6 ms hop and
      its peak fires as soon as the 1024-sample window *starts* to swallow the
      transient, so the coarse downbeat lands up to 46 ms early.
    * **Tempo.** The lag grid resolves to about 0.2 % even after parabolic
      interpolation. 0.2 % is 0.4 s of drift over three minutes -- a quarter of
      a beat -- so a routine that starts perfectly ends visibly behind.

    Both are fixed at once, because they trade against each other: a comb that
    is free to move the tempo will otherwise absorb a phase error into a tempo
    error and drift twice as fast. The fit is a joint search over a +/-2 % tempo
    window and one hop-width of phase, scored against a 1 ms attack envelope
    taken straight from the waveform. Cheap (one decimation plus a few million
    array lookups) and bounded, so it can refine the coarse answer but never
    replace it with a different beat.
    """
    block = max(1, ANALYSIS_RATE // 1000)
    usable = (samples.size // block) * block
    if usable < block * 2000:
        return bpm, coarse
    loudness = np.abs(samples[:usable]).reshape(-1, block).max(axis=1)
    attack = np.maximum(np.diff(loudness, prepend=loudness[0]), 0.0)
    # 22050/22 is 1002.27 Hz, not 1000. Calling it a millisecond cost a flat
    # -0.23 % on every tempo -- invisible in a bench print, 0.4 s of drift by
    # the end of a three-minute song.
    envelope_rate = ANALYSIS_RATE / block
    duration = attack.size / envelope_rate

    def fit(tempos: np.ndarray, offsets: np.ndarray,
            prior_centre: float | None) -> tuple[float, float, float]:
        # Default to the centre of this pass's own window, so a pass that can
        # score nothing hands back what it was asked to refine rather than
        # silently reverting to the estimate before it.
        best = (float(tempos[tempos.size // 2]), float(offsets[offsets.size // 2]), -1.0)
        highest_offset = float(offsets.max())
        for candidate_bpm in tempos:
            period = 60.0 / float(candidate_bpm)
            count = int((duration - highest_offset) / period)
            if count < 8:
                continue
            pulses = np.arange(count)
            positions = np.rint(
                (offsets[:, None] + pulses[None, :] * period) * envelope_rate
            ).astype(np.int32)
            np.clip(positions, 0, attack.size - 1, out=positions)
            # Mean, not sum: a faster candidate fits more pulses into the same
            # audio and would otherwise win on count alone.
            scores = attack[positions].mean(axis=1)
            if prior_centre is not None:
                # A gentle pull toward the flux estimate, floored at 0.75 so it
                # can only ever break a near-tie. The real evidence is an order
                # of magnitude stronger than this (a correct grid scores ~30x a
                # wrong one on a click track), so this cannot override it.
                delta = np.abs(((offsets - prior_centre + period / 2.0) % period)
                               - period / 2.0)
                scores = scores * (0.75 + 0.25 * np.exp(
                    -0.5 * (delta / (period / 4.0)) ** 2))
            index = int(np.argmax(scores))
            if float(scores[index]) > best[2]:
                best = (float(candidate_bpm), float(offsets[index]), float(scores[index]))
        return best

    # Coarse-to-fine, in two passes. One pass cannot do both jobs: a window
    # wide enough to contain the autocorrelation estimate's error needs steps
    # too coarse to leave under 50 ms of drift, and a window fine enough to be
    # accurate gets *pinned to its own edge* when the coarse estimate was
    # further out than that -- which is how a 160 BPM click track came back as
    # 163.3 with the phase dragged 99 ms along with it.
    #
    # The first pass searches a whole beat of phase. Constraining it near the
    # flux estimate looked safer (it cannot pick the offbeat) and was wrong:
    # that estimate is early at slow tempi and *late* at fast ones, where the
    # local-mean window used to flatten the envelope is itself half a beat
    # long. When the true phase fell outside the window the fit simply took the
    # best wrong answer available. Searching the full beat and trusting the
    # score is both simpler and what actually works.
    period_hint = 60.0 / bpm
    wide_bpm, wide_phase, _ = fit(
        bpm * (1.0 + np.linspace(-0.03, 0.03, 61)),          # 0.1 % steps
        np.arange(0.0, period_hint, 0.002),
        coarse,
    )
    refined_bpm, refined_phase, _ = fit(
        wide_bpm * (1.0 + np.linspace(-0.002, 0.002, 41)),   # 0.01 % steps
        wide_phase + np.arange(-0.012, 0.0121, 0.001),
        None,
    )
    # Keep the offset inside the first beat: the board treats beat0 as
    # "seconds until the first beat", so a negative value would start it late.
    return refined_bpm, float(refined_phase % (60.0 / refined_bpm))


def _beat_energy(samples: np.ndarray, bpm: float, beat0: float,
                 limit: int) -> tuple[int, ...]:
    """One 0..15 loudness value per beat.

    Scaled against this song's own 5th/95th percentiles rather than absolute
    dBFS: the stage lighting should react to *this* track's dynamics, and a
    quietly mastered song should not produce a permanently dim room.
    """
    beat_samples = ANALYSIS_RATE * 60.0 / bpm
    if beat_samples < 1.0:
        return ()
    start = int(beat0 * ANALYSIS_RATE)
    count = int((samples.size - start) / beat_samples)
    count = max(0, min(count, limit))
    if count <= 0:
        return ()
    edges = start + (np.arange(count + 1) * beat_samples).astype(np.int64)
    edges = np.clip(edges, 0, samples.size)
    squared = np.square(samples.astype(np.float32))
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    totals = cumulative[edges[1:]] - cumulative[edges[:-1]]
    widths = np.maximum(edges[1:] - edges[:-1], 1)
    rms = np.sqrt(totals / widths)
    low = float(np.percentile(rms, 5))
    high = float(np.percentile(rms, 95))
    if high - low < 1e-6:
        return tuple([8] * count)
    scaled = np.clip((rms - low) / (high - low), 0.0, 1.0) * 15.0
    return tuple(int(round(value)) for value in scaled)


def analyse(samples: np.ndarray, max_beats: int = 1024) -> BeatGrid | None:
    """Beat grid for mono float32 samples at ANALYSIS_RATE."""
    if samples.size < ANALYSIS_RATE * 4:
        return None
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak < 1e-4:
        return None  # silence: there is nothing to dance to
    samples = (samples / peak).astype(np.float32)
    envelope = _onset_envelope(samples)
    if envelope.size < 32:
        return None
    env_rate = ANALYSIS_RATE / HOP
    bpm, confidence = _estimate_tempo(envelope, env_rate)
    if bpm <= 0.0:
        return None
    coarse_phase = _estimate_phase(envelope, env_rate, bpm)
    bpm, beat0 = _refine_grid(samples, bpm, coarse_phase)
    energy = _beat_energy(samples, bpm, beat0, max_beats)
    return BeatGrid(
        bpm=round(bpm, 2),
        beat0=round(beat0, 4),
        confidence=round(confidence, 3),
        energy=energy,
        analysed_seconds=round(samples.size / ANALYSIS_RATE, 2),
    )


async def analyse_url(url: str, seconds: float = 60.0,
                      timeout: float = 20.0) -> BeatGrid | None:
    """Decode the first `seconds` of a playable URL and analyse it.

    A second, short-lived ffmpeg beside the playback one. They read independent
    HTTP ranges, so this costs no playback continuity -- and it is started
    alongside the choreography agent, whose cloud round trip is longer anyway.
    """
    if not url:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", url, "-t", f"{seconds:.1f}",
            "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1",
            "-ar", str(ANALYSIS_RATE), "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        LOG.info("beat analysis unavailable: ffmpeg could not be started")
        return None
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        LOG.info("beat analysis timed out after %.0fs", timeout)
        return None
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    if process.returncode or len(stdout) < ANALYSIS_RATE * 2:
        LOG.info("beat analysis got no usable audio (rc=%s, %d bytes)",
                 process.returncode, len(stdout))
        return None
    samples = np.frombuffer(stdout, dtype="<i2").astype(np.float32) / 32768.0
    try:
        grid = await asyncio.to_thread(analyse, samples)
    except Exception:
        LOG.exception("beat analysis failed")
        return None
    if grid is not None:
        LOG.info("beat grid: %.1f BPM, first beat at %.3fs, confidence %.2f, "
                 "%d beats of energy", grid.bpm, grid.beat0, grid.confidence,
                 len(grid.energy))
    return grid


if __name__ == "__main__":  # pragma: no cover - manual bench helper
    async def _main() -> None:
        grid = await analyse_url(sys.argv[1])
        print(grid)

    asyncio.run(_main())
