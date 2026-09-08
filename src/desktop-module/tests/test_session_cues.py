"""Short audio cues that give an engagement session shape.

An interaction that is only speech is just a conversation. The cue is what
makes a round feel like a round -- the same reason the hold countdown exists
for a physical routine, which is why both share one synthesiser and cache.
"""

import wave

import pytest

from core.senior import exercise_cadence as cadence
from core.senior.exercise_cadence import CUE_NAMES, cue_track, play_cue


@pytest.mark.parametrize("name", CUE_NAMES)
def test_every_cue_renders_to_a_playable_wav(name):
    path = cue_track(name)
    assert path
    with wave.open(path) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == cadence.SAMPLE_RATE
        assert 0.0 < w.getnframes() / w.getframerate() < 1.0


def test_the_cue_set_covers_a_round():
    assert {"start", "correct", "wrong", "timeup", "applause"} <= set(CUE_NAMES)


def test_an_unknown_cue_renders_nothing():
    assert cue_track("buzzer") is None
    assert cue_track("") is None
    assert cue_track(None) is None


def test_cues_are_cached_rather_than_re_synthesised():
    first = cue_track("correct")
    assert cue_track("correct") == first


def test_a_cue_never_startles():
    """Amplitude discipline is not cosmetic here.

    These play between spoken turns to someone who may be elderly and may be
    alone. A cue louder than the speech it follows is a fright, not a mark.
    """
    assert cadence._AMPLITUDE <= 0.4


def test_playing_an_unknown_cue_is_a_no_op(monkeypatch):
    monkeypatch.setattr(cadence.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("should not spawn a player"))
    assert play_cue("nope") is False


def test_a_missing_player_does_not_raise(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("mpv")

    monkeypatch.setattr(cadence.subprocess, "Popen", boom)
    assert play_cue("correct") is False


def test_playing_a_cue_does_not_block(monkeypatch):
    """A cue marks a moment that has already happened; waiting for it would only
    add latency to the person's next turn."""
    spawned = []

    class Proc:
        def wait(self, timeout=None):
            pytest.fail("play_cue must not wait on the player")

    monkeypatch.setattr(cadence.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a) or Proc())
    assert play_cue("start") is True
    assert len(spawned) == 1
