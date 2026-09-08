from types import SimpleNamespace

import numpy as np

from kiki_gateway.endpointer import ActionKind, StreamingEndpointer


class FakeVAD:
    def __init__(self):
        self.value = 0.0

    def probability(self, _frame):
        return self.value

    def reset(self):
        pass


def config():
    return SimpleNamespace(
        preroll_ms=250,
        near_field_enabled=False,
        near_field_engage_dbfs=-50,
        near_field_open_margin_db=9,
        near_field_close_margin_db=4,
        vad_threshold=0.5,
        partial_asr_interval_ms=1000,
        min_speech_ms=250,
        speculative_silence_ms=240,
        endpoint_silence_ms=600,
        max_utterance_seconds=20,
    )


def feed(endpoint, vad, voiced, count):
    vad.value = 0.9 if voiced else 0.0
    actions = []
    for _ in range(count):
        actions.extend(endpoint.feed(np.zeros(512, dtype=np.float32)))
    return actions


def test_speculative_asr_precedes_conservative_commit():
    vad = FakeVAD()
    endpoint = StreamingEndpointer(vad, config())
    assert ActionKind.SPEECH_START in [a.kind for a in feed(endpoint, vad, True, 9)]
    actions = feed(endpoint, vad, False, 8)
    assert [a.kind for a in actions] == [ActionKind.SPECULATIVE]
    actions = feed(endpoint, vad, False, 11)
    assert [a.kind for a in actions] == [ActionKind.COMMIT]


def test_resumed_speech_invalidates_speculation():
    vad = FakeVAD()
    endpoint = StreamingEndpointer(vad, config())
    feed(endpoint, vad, True, 9)
    feed(endpoint, vad, False, 8)
    assert [a.kind for a in feed(endpoint, vad, True, 1)] == [ActionKind.INVALIDATE]


def test_releasing_a_hold_commits_without_waiting_out_the_silence_window():
    """The panel's hold-to-talk release must not cost another 600 ms."""
    vad = FakeVAD()
    endpoint = StreamingEndpointer(vad, config())
    feed(endpoint, vad, True, 9)
    actions = endpoint.force_commit()
    assert [a.kind for a in actions] == [ActionKind.COMMIT]
    assert actions[0].audio is not None and actions[0].audio.size > 0
    assert not endpoint.in_speech


def test_releasing_a_hold_with_no_speech_commits_nothing():
    vad = FakeVAD()
    endpoint = StreamingEndpointer(vad, config())
    feed(endpoint, vad, True, 2)  # below min_speech_ms
    assert endpoint.force_commit() == []
    assert not endpoint.in_speech


def test_force_commit_when_idle_is_harmless():
    vad = FakeVAD()
    endpoint = StreamingEndpointer(vad, config())
    assert endpoint.force_commit() == []


def test_a_physical_hold_cannot_endpoint_during_a_pause():
    vad = FakeVAD()
    endpoint = StreamingEndpointer(vad, config())
    feed(endpoint, vad, True, 9)

    vad.value = 0.0
    actions = []
    # Far beyond both the speculative and conservative silence thresholds.
    for _ in range(30):
        actions.extend(
            endpoint.feed(np.zeros(512, dtype=np.float32), defer_commit=True)
        )

    assert ActionKind.SPECULATIVE not in [action.kind for action in actions]
    assert ActionKind.COMMIT not in [action.kind for action in actions]
    assert endpoint.in_speech
    assert [action.kind for action in endpoint.force_commit()] == [ActionKind.COMMIT]
