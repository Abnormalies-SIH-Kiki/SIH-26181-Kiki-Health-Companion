"""End-to-end endpointer behaviour in a crowded room.

The bug: Silero calls crowd babble "speech", so ``silence_ms`` never grew, the
endpoint never fired, and the 1 s interim heartbeat kept the listen window open
forever. These tests drive the real ``_Endpointer`` with a stubbed VAD model to
show that (a) babble alone no longer commits or heartbeats forever, (b) a
near-field speaker still commits normally, and (c) a quiet room is unaffected.
"""

import sys
import types
import unittest
from unittest.mock import Mock

import numpy as np

# core.stt imports torch/silero at module load. Provide the surface the
# endpointer actually uses so these tests run without the models present.
if "torch" not in sys.modules:
    torch_stub = types.ModuleType("torch")
    torch_stub.from_numpy = lambda arr: arr
    torch_stub.set_num_threads = lambda n: None
    sys.modules["torch"] = torch_stub
if "silero_vad" not in sys.modules:
    silero_stub = types.ModuleType("silero_vad")
    silero_stub.load_silero_vad = lambda *a, **k: None
    sys.modules["silero_vad"] = silero_stub

from core import stt  # noqa: E402

FRAME = stt.FRAME
FRAME_MS = stt.FRAME_MS


class _FakeVad:
    """Silero stand-in: reports speech whenever the frame carries any energy.

    That is the real failure mode — Silero cannot tell the user apart from the
    room, because background chatter genuinely is speech.
    """

    def __init__(self):
        self.reset_calls = 0

    def __call__(self, frame, sample_rate):
        level = float(np.sqrt(np.mean(np.square(np.asarray(frame, dtype=np.float64)))))
        return types.SimpleNamespace(item=lambda: 0.9 if level > 1e-4 else 0.0)

    def reset_states(self):
        self.reset_calls += 1


def base_cfg(**overrides):
    cfg = {
        "vad_threshold": 0.5,
        "endpoint_ms": 600,
        "spec_silence_ms": 240,
        "min_speech_ms": 250,
        "preroll_ms": 250,
        "max_utterance_seconds": 20.0,
        "partial_asr_interval_ms": 0,
        "near_field_gate": {
            "enabled": True,
            "engage_floor_dbfs": -50.0,
            "open_margin_db": 9.0,
            "close_margin_db": 4.0,
            "floor_rise_per_s_db": 3.0,
            "floor_fall_per_s_db": 30.0,
        },
    }
    cfg.update(overrides)
    return cfg


class _Harness:
    def __init__(self, cfg=None):
        self.commits = []
        self.interims = []
        self.asr = Mock()
        self.asr.submit.side_effect = lambda audio: _done_future("transcript")
        self.ep = stt._Endpointer(
            _FakeVad(),
            cfg or base_cfg(),
            self.asr,
            on_commit=lambda text: self.commits.append(text),
            on_interim=lambda text: self.interims.append(text),
        )
        self.now = 1000.0
        self.rng = np.random.default_rng(42)

    def feed(self, db, seconds):
        n = int(round(seconds * 1000.0 / FRAME_MS))
        for _ in range(n):
            if db is None:
                frame = np.zeros(FRAME, dtype=np.float32)
            else:
                frame = self.rng.standard_normal(FRAME).astype(np.float32) * (
                    10 ** (db / 20.0)
                )
            self.ep.process(frame, self.now)
            self.now += FRAME_MS / 1000.0


def _done_future(value):
    from concurrent.futures import Future

    fut = Future()
    fut.set_result((value, 0.05))
    return fut


class CrowdedRoomEndpointerTests(unittest.TestCase):
    def test_babble_alone_never_commits_and_stops_heartbeating(self):
        h = _Harness()
        h.feed(-34.0, 30.0)          # half a minute of pure room noise
        self.assertEqual(h.commits, [], "room chatter must not become a user turn")
        self.assertFalse(h.ep.in_speech)
        # The old code emitted an interim every second forever, which is what
        # reset main.py's listen-window timer indefinitely.
        self.assertLessEqual(
            len(h.interims), 2,
            f"heartbeat must stop once the gate engages (got {len(h.interims)})",
        )

    def test_near_field_speech_still_endpoints_over_babble(self):
        h = _Harness()
        h.feed(-34.0, 6.0)           # learn the crowd floor
        h.feed(-16.0, 1.5)           # user speaks up close
        h.feed(-34.0, 1.5)           # user stops; babble continues
        self.assertEqual(h.commits, ["transcript"])

    def test_utterance_ends_even_though_babble_never_stops(self):
        """The core regression: trailing 'silence' is babble, and must still end the turn."""
        h = _Harness()
        h.feed(-34.0, 6.0)
        h.feed(-16.0, 1.0)
        self.assertTrue(h.ep.in_speech)
        h.feed(-34.0, 1.0)           # only the crowd is left
        self.assertFalse(h.ep.in_speech, "endpoint must fire while the room is still noisy")
        self.assertEqual(len(h.commits), 1)


class QuietRoomEndpointerTests(unittest.TestCase):
    """A quiet home must behave exactly as before — the gate stays inert."""

    def test_normal_speech_commits(self):
        h = _Harness()
        h.feed(-62.0, 3.0)
        h.feed(-30.0, 1.0)
        h.feed(None, 1.0)            # true silence
        self.assertEqual(h.commits, ["transcript"])
        self.assertFalse(h.ep.gate.engaged)

    def test_quiet_speech_is_not_gated_out(self):
        h = _Harness()
        h.feed(-62.0, 3.0)
        h.feed(-42.0, 1.0)           # soft talker, quiet room
        h.feed(None, 1.0)
        self.assertEqual(h.commits, ["transcript"])


class PushToTalkOverrideTests(unittest.TestCase):
    def test_hold_bypasses_the_gate_so_a_quiet_user_is_still_captured(self):
        """A hand on the IR sensor is explicit intent; never gate it out."""
        import threading

        hold = threading.Event()
        cfg = base_cfg()
        h = _Harness(cfg)
        h.ep.hold_event = hold
        h.feed(-34.0, 6.0)           # noisy room, gate engages
        self.assertTrue(h.ep.gate.engaged)

        hold.set()
        h.feed(-30.0, 1.0)           # user quieter than the gate would allow
        self.assertTrue(h.ep.in_speech, "push-to-talk must capture regardless of level")
        self.assertEqual(h.commits, [], "hold suspends the endpoint")

        # Hand released -> force commit, exactly like the thumbs-up path.
        hold.clear()
        self.assertTrue(h.ep.force_commit())
        self.assertEqual(h.commits, ["transcript"])


if __name__ == "__main__":
    unittest.main()
