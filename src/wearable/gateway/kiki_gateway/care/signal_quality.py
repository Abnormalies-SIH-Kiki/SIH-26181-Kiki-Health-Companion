"""Shared, deterministic acceptance checks for wearable optical readings."""

from __future__ import annotations

from typing import Any, Mapping


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def wearable_heart_is_coherent(reading: Mapping[str, Any] | None) -> bool:
    """Require a supported pulse rate and a physically sane PPG window.

    Firmware quality labels are claims, not a trust boundary.  Keeping the
    same objective checks on the Pi prevents a buggy or stale wearable build
    from placing an implausible number in desktop Kiki's live context.  The
    normal path requires independent peak/ACF agreement.  A specifically
    detected alternating-notch waveform may use paired peak intervals, but it
    needs more observed peaks and stronger red/IR agreement.
    """
    if not isinstance(reading, Mapping):
        return False
    signal = reading.get("signal")
    if not isinstance(signal, Mapping):
        return False
    value = _number(reading.get("value"))
    estimator_version = _number(signal.get("estimator_version"))
    acf_hr = _number(signal.get("acf_hr"))
    peak_hr = _number(signal.get("peak_hr"))
    acf = _number(signal.get("acf"))
    rr_cv = _number(signal.get("rr_cv"))
    peaks = _number(signal.get("n_peaks"))
    pi = _number(signal.get("pi"))
    channel = _number(signal.get("channel_corr"))
    glitches = _number(signal.get("i2c_glitches"))
    paired_peaks = signal.get("paired_peaks") is True
    if any(item is None for item in (
            value, estimator_version, acf_hr, peak_hr, acf, rr_cv, peaks, pi,
            channel, glitches)):
        return False
    assert value is not None and estimator_version is not None
    assert acf_hr is not None and peak_hr is not None
    assert acf is not None and rr_cv is not None and peaks is not None
    assert pi is not None and channel is not None and glitches is not None
    agreement = max(6.0, acf_hr * 0.08)
    reported_peak_agreement = max(3.0, peak_hr * 0.05)
    common = (
        estimator_version == 2
        and 35.0 <= value <= 220.0
        and 35.0 <= acf_hr <= 220.0
        and 35.0 <= peak_hr <= 220.0
        and abs(value - peak_hr) <= reported_peak_agreement
        and 0.015 <= pi <= 5.0
        and channel >= 0.60
        and 0 <= glitches <= 2
    )
    consensus = (
        peaks >= 7
        and acf >= 0.35
        and abs(acf_hr - peak_hr) <= agreement
        and 0.0 <= rr_cv <= 0.25
    )
    paired = (
        paired_peaks
        and peaks >= 8
        and 0.0 <= rr_cv <= 0.35
        and channel >= 0.80
    )
    return common and (consensus or paired)
