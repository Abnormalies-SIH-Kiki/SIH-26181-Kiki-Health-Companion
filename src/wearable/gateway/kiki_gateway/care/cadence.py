"""Audible timing and cues for guided sessions, played on the board.

Vendored from the RPi's `core/senior/exercise_cadence.py`. The tone synthesis
and the wav cache are unchanged; playback and hold capture are not, because the
speaker and the sensor are both at the far end of a WebSocket here. See the
playback section below.



Two jobs, one synthesiser: the per-second hold countdown a physical routine
needs, and the short cues an engagement session uses to mark a round. Both go
through the same tone rendering, the same wav cache, and the same single-process
player, because the reason that machinery exists applies identically to both.

A guided routine is not a conversation. When Kiki says "hold this for ten
seconds", the person needs the ten seconds *marked* — the way an exercise video
ticks them off — not a sentence about them.

Two earlier behaviours this replaces, both seen live on 2026-08-29:

(The hold behaviour below is what the module was built for.)

* "Hold it there for five seconds." → TTS finished, the microphone opened, and
  Kiki waited for the person to say something. The hold was never timed; the
  session stalled until the person spoke.
* "hold that position for five seconds... four, three, two, one. Great job." →
  the model tried to count in its own summary, and TTS read the whole countdown
  aloud in under two seconds. The person got the words without the time.

The countdown is rendered as ONE wav and played with a single process. Spawning
a player per beep costs a fresh ALSA/Bluetooth sink open each time (measured at
300-500 ms in sound_effects.py), which would both smear the timing it exists to
convey and cost more than the beep itself.
"""

from __future__ import annotations

import math
import os
import time
import struct
import threading
import wave
from typing import Optional

SAMPLE_RATE = 24000          # matches the other sound_effects assets
_TICK_HZ = 1500.0            # per-second marker: short, bright, easy to count
_TICK_MS = 70
_FINAL_HZ = 950.0            # "done" tone: lower and longer, unmistakably an end
_FINAL_MS = 320
_AMPLITUDE = 0.35            # well under full scale; this plays between spoken
                             # instructions and must not startle anyone

# Rendered tracks are cached per duration — a routine repeats the same holds
# over and over, and re-synthesising 10 s of near-silence each time is waste.
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "_cadence_cache")
_CACHE_LOCK = threading.Lock()

# A hold longer than this is treated as a model mistake rather than an
# instruction. Standing still for minutes because a number had an extra digit
# is exactly the kind of thing an unattended person should not be asked to do.
MAX_HOLD_SECONDS = 120


def _tone(frequency: float, milliseconds: int) -> bytes:
    """A single 16-bit mono tone with short fades so it clicks at neither end."""
    total = int(SAMPLE_RATE * milliseconds / 1000.0)
    fade = max(1, int(SAMPLE_RATE * 0.005))       # 5 ms
    out = bytearray()
    for n in range(total):
        envelope = 1.0
        if n < fade:
            envelope = n / fade
        elif n > total - fade:
            envelope = max(0.0, (total - n) / fade)
        sample = _AMPLITUDE * envelope * math.sin(
            2.0 * math.pi * frequency * n / SAMPLE_RATE)
        out += struct.pack("<h", int(sample * 32767))
    return bytes(out)


def _silence(milliseconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * milliseconds / 1000.0)


def countdown_track(seconds: int) -> Optional[str]:
    """Path to a wav that ticks once per second for ``seconds``, then ends.

    The track is exactly ``seconds`` long plus the final tone, so playing it to
    completion *is* the hold. Returns None for a non-positive duration.
    """
    seconds = int(seconds)
    if seconds <= 0:
        return None
    seconds = min(seconds, MAX_HOLD_SECONDS)

    path = os.path.join(_CACHE_DIR, f"hold_{seconds}s.wav")
    with _CACHE_LOCK:
        if os.path.exists(path):
            return path
        os.makedirs(_CACHE_DIR, exist_ok=True)

        tick = _tone(_TICK_HZ, _TICK_MS)
        gap = _silence(1000 - _TICK_MS)
        frames = bytearray()
        for _ in range(seconds):
            frames += tick
            frames += gap
        frames += _tone(_FINAL_HZ, _FINAL_MS)

        tmp = path + ".tmp"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(bytes(frames))
        os.replace(tmp, path)          # never expose a half-written track
        return path


# --- Session cues -------------------------------------------------------
#
# Short marks for an engagement session: a round starting, an answer landing,
# time running out. They exist because an interaction that is only speech is
# just a conversation -- the sound is what makes it feel like an activity with
# shape, the same way the countdown is what makes a hold feel like a hold.
#
# Deliberately gentle, and NOTHING here is a buzzer. A harsh error tone aimed at
# an older person getting an answer wrong is humiliating, and one experience of
# it is enough to make someone never play again. "wrong" is a soft descending
# pair -- closer to a thoughtful "hmm" than a rejection.
_CUES: dict = {
    # name: [(hz, ms), ...]  -- None is a gap
    "start":    [(880.0, 90), (None, 60), (1320.0, 120)],
    "correct":  [(1046.5, 110), (None, 30), (1568.0, 170)],
    "wrong":    [(660.0, 130), (None, 40), (523.3, 200)],
    "timeup":   [(_FINAL_HZ, _FINAL_MS)],
    "applause": [(784.0, 90), (None, 25), (988.0, 90), (None, 25), (1318.5, 260)],
}

CUE_NAMES = tuple(_CUES)


def cue_track(name: str) -> Optional[str]:
    """Path to the wav for one cue, rendered and cached on first use."""
    name = str(name or "").strip().lower()
    steps = _CUES.get(name)
    if not steps:
        return None
    path = os.path.join(_CACHE_DIR, f"cue_{name}.wav")
    with _CACHE_LOCK:
        if os.path.exists(path):
            return path
        os.makedirs(_CACHE_DIR, exist_ok=True)
        frames = bytearray()
        for frequency, milliseconds in steps:
            frames += (_silence(milliseconds) if frequency is None
                       else _tone(frequency, milliseconds))
        tmp = path + ".tmp"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(bytes(frames))
        os.replace(tmp, path)
        return path


# --------------------------------------------------------------------------
# Playback, on this body.
#
# The RPi spawns `mpv` against the local ALSA sink. There is no speaker on the
# machine running this code -- the speaker is on the board, at the far end of a
# WebSocket -- so playback is an injected callable instead of a subprocess. The
# gateway session registers one at connect (`set_audio_player`), and everything
# below degrades to *timing without sound* when nothing is registered.
#
# That degradation is deliberate and is the safe half of the feature: the hold
# still lasts exactly as long, the microphone still stays muted for it, and the
# IMU window is still recorded across it. Only the beeps are missing. A hold
# that silently became a zero-second pause would break the routine; a hold that
# is quiet does not.
# --------------------------------------------------------------------------

_PLAYER_LOCK = threading.Lock()
_AUDIO_PLAYER = None
_MOTION_CAPTURE = None


def set_audio_player(player) -> None:
    """Register `player(wav_path, blocking) -> bool`, or None to unregister."""
    global _AUDIO_PLAYER
    with _PLAYER_LOCK:
        _AUDIO_PLAYER = player


def set_motion_capture(capture) -> None:
    """Register `capture(seconds) -> None`, which asks the board to record.

    Called once at the START of a hold rather than sampled during it: the board
    owns the buffer, the window is closed by the firmware when the requested
    duration elapses, and it arrives as one `imu_window` event. That is a
    better shape than the camera's mid-hold polling -- there is no way for the
    gateway's timing jitter to smear which samples belong to the hold.
    """
    global _MOTION_CAPTURE
    with _PLAYER_LOCK:
        _MOTION_CAPTURE = capture


def _play(path: Optional[str], blocking: bool) -> bool:
    if not path:
        return False
    with _PLAYER_LOCK:
        player = _AUDIO_PLAYER
    if player is None:
        return False
    try:
        return bool(player(path, blocking))
    except Exception as exc:
        print(f"[Cadence] playback skipped: {exc}")
        return False


def play_cue(name: str) -> bool:
    """Play one short cue. Non-blocking, and never worth failing a turn over."""
    return _play(cue_track(name), False)


def play_countdown(seconds: int, stop_event: Optional[threading.Event] = None,
                   capture_seconds: Optional[float] = None) -> bool:
    """Time a hold: beep it out, record the wrist across it, block until done.

    Blocking is deliberate: the caller keeps the microphone muted across this
    whole window, which is what stops Kiki from hearing her own beeps and
    treating them as a reply. Returns True if the hold ran to completion.

    The motion capture is armed FIRST and for slightly longer than the beeps,
    because the movement starts when the person hears the instruction end, not
    when the last tick plays. Capturing must never delay the countdown, so a
    failure to arm is logged and the hold runs anyway -- an unrecorded hold
    becomes "no motion data" on the next turn, which the care agent is required
    to say out loud rather than judge around.
    """
    held = clamp_hold_seconds(seconds)
    if held <= 0:
        return False

    with _PLAYER_LOCK:
        capture = _MOTION_CAPTURE
    if capture is not None:
        try:
            capture(float(capture_seconds or held) + 0.5)
        except Exception as exc:
            print(f"[Cadence] could not arm the motion window: {exc}")

    started = time.monotonic()
    played = _play(countdown_track(held), True)
    # The player may be absent, non-blocking, or have failed. Either way the
    # hold must occupy real time, or the model gets a window of nothing and the
    # person gets no chance to hold anything.
    while True:
        if stop_event is not None and stop_event.is_set():
            print(f"[Cadence] hold interrupted after "
                  f"{time.monotonic() - started:.1f}s of {held}s")
            return False
        remaining = held - (time.monotonic() - started)
        if remaining <= 0:
            break
        time.sleep(min(remaining, 0.1))
    print(f"[Cadence] held {held}s ({'with beeps' if played else 'silently'})")
    return True


def clamp_hold_seconds(value, maximum: int = MAX_HOLD_SECONDS) -> int:
    """Coerce a model-supplied hold into a safe integer number of seconds."""
    try:
        held = int(float(value))
    except (TypeError, ValueError):
        return 0
    if held <= 0:
        return 0
    return min(held, maximum)
