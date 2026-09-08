import os
import sys
import threading
import time
import unittest
from queue import Queue
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tts_sync import (
    LiveDisplaySegment, TimingCalibration, WordCue, WordSchedule,
    romanize_hindi_for_lcd,
)


def calibration_raw():
    model = {
        "initial_ms": 80,
        "base_ms": 60,
        "char_ms": 35,
        "mark_ms": 15,
        "comma_pause_ms": 90,
        "terminal_pause_ms": 160,
    }
    return {
        "version": 1,
        "sample_rate": 24000,
        "output_latency_ms": 120,
        "lcd_write_lead_ms": 30,
        "profiles": {
            "default": {
                "latin": dict(model),
                "devanagari": dict(model),
                "tags": {"duration_ms": 180},
            }
        },
    }


class TestTimingCalibration(unittest.TestCase):
    def test_hindi_is_romanized_for_lcd_without_changing_latin_text(self):
        self.assertEqual(romanize_hindi_for_lcd("नमस्ते Kiki!"), "namaste Kiki!")
        self.assertEqual(romanize_hindi_for_lcd("हिंदी में धन्यवाद।"),
                         "hindi men dhanyavaad.")
        self.assertEqual(romanize_hindi_for_lcd("Already Hinglish 123"),
                         "Already Hinglish 123")

    def test_mixed_language_schedule_shows_friendly_tag_text(self):
        calibration = TimingCalibration(calibration_raw())
        schedule, reason = calibration.schedule(
            "[laughter] Okay, धूप and eggs.", "",
        )
        self.assertEqual(reason, "")
        self.assertEqual([cue.word for cue in schedule.cues],
                         ["Ha ha ha!", "Okay,", "धूप", "and", "eggs."])
        offsets = [cue.offset_s for cue in schedule.cues]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(offsets[0], 0.0)
        self.assertGreaterEqual(offsets[1], 0.18)

    def test_mid_sentence_expression_tag_is_hidden(self):
        calibration = TimingCalibration(calibration_raw())
        schedule, reason = calibration.schedule("Hello![surprise-ah]There.", "")
        self.assertEqual(reason, "")
        self.assertEqual([cue.word for cue in schedule.cues],
                         ["Hello!", "There."])

    def test_only_first_leading_expression_tag_is_displayed(self):
        calibration = TimingCalibration(calibration_raw())
        schedule, reason = calibration.schedule(
            "[sigh][laughter] Hello [surprise-ah]there.", "")
        self.assertEqual(reason, "")
        self.assertEqual([cue.word for cue in schedule.cues],
                         ["Sigh...", "Hello", "there."])
        self.assertTrue(schedule.cues[0].is_expression)
        self.assertFalse(schedule.cues[1].is_expression)

    def test_missing_voice_fails_closed(self):
        calibration = TimingCalibration(calibration_raw())
        schedule, reason = calibration.schedule("hello", "amitabh")
        self.assertIsNone(schedule)
        self.assertIn("not been calibrated", reason)

    def test_stream_underrun_shifts_only_future_words(self):
        schedule = WordSchedule(
            cues=(WordCue("one", 0.0), WordCue("two", 1.0)),
            output_latency_s=0.1,
            lcd_write_lead_s=0.02,
            voice="default",
        )
        segment = LiveDisplaySegment(schedule, 1)
        segment.start(10.0)
        first_before, _, _ = segment.target_for(schedule.cues[0])
        segment.add_window(0.5, 11.2)  # 0.7-second PCM starvation
        first_after, _, _ = segment.target_for(schedule.cues[0])
        second_after, _, _ = segment.target_for(schedule.cues[1])
        self.assertEqual(first_before, first_after)
        self.assertAlmostEqual(second_after, 11.7, places=3)


class _RecordingQueue(Queue):
    def __init__(self, actions):
        super().__init__()
        self.actions = actions

    def put(self, item, *args, **kwargs):
        if isinstance(item, LiveDisplaySegment):
            self.actions.append("display")
        return super().put(item, *args, **kwargs)


class _FakeCalibration:
    valid = True
    output_latency_s = 0.0

    def __init__(self, actions=None):
        self.actions = actions

    def schedule(self, speakable, voice):
        if self.actions is not None:
            self.actions.append("schedule")
        return WordSchedule(
            cues=(WordCue(speakable, 0.0),),
            output_latency_s=0.0,
            lcd_write_lead_s=0.0,
            voice=voice or "default",
        ), ""


class TestPlaybackCriticalPath(unittest.TestCase):
    def test_first_hindi_sentence_is_never_coalesced(self):
        from core import tts

        streamer = tts.LocalTTSStreamer()
        streamer._sentence_queue.put("यह अगला वाक्य है।")

        text, saw_end, pending = streamer._coalesce_hindi_continuation(
            "हाँ, मैं सुन रही हूँ।", request_idx=0)

        self.assertEqual(text, "हाँ, मैं सुन रही हूँ।")
        self.assertFalse(saw_end)
        self.assertIsNone(pending)
        self.assertEqual(streamer._sentence_queue.qsize(), 1)

    def test_short_hindi_continuations_share_one_tts_request(self):
        from core import tts

        streamer = tts.LocalTTSStreamer()
        streamer._audio_tail_at = tts.monotonic_time() + 5.0
        streamer._sentence_queue.put("अचानक इतनी चुप्पी क्यों है।")

        with patch.object(tts, "_LOCAL_HINDI_COALESCE_MIN_CHARS", 30):
            text, saw_end, pending = streamer._coalesce_hindi_continuation(
                "[laughter] क्या हुआ?", request_idx=1)

        self.assertEqual(
            text,
            "[laughter] क्या हुआ? अचानक इतनी चुप्पी क्यों है।",
        )
        self.assertFalse(saw_end)
        self.assertIsNone(pending)

    def test_queued_pcm_headroom_allows_hidden_hindi_lookahead(self):
        from core import tts

        streamer = tts.LocalTTSStreamer()
        streamer._put_pcm(b"\x00" * int(streamer._bytes_per_sec * 3.0))

        def queue_next_sentence():
            time.sleep(0.03)
            streamer._sentence_queue.put("अचानक इतनी चुप्पी क्यों है।")

        producer = threading.Thread(target=queue_next_sentence)
        producer.start()
        with patch.object(tts, "_LOCAL_HINDI_COALESCE_MIN_CHARS", 30):
            text, _, _ = streamer._coalesce_hindi_continuation(
                "[laughter] क्या हुआ?", request_idx=1)
        producer.join()

        self.assertIn("अचानक इतनी चुप्पी", text)

    def test_expression_tag_limit_spans_sentence_segments(self):
        from core import tts

        streamer = tts.LocalTTSStreamer()
        first = WordCue("Hmm?", 0.0, is_expression=True)
        word = WordCue("Loud", 0.1)
        later_laugh = WordCue("Ha ha ha!", 0.0, is_expression=True)

        accepted = [cue.word for cue in (first, word, later_laugh)
                    if streamer._should_display_lcd_cue(cue)]

        self.assertEqual(accepted, ["Hmm?", "Loud"])

    def test_lcd_page_fills_both_rows_before_flushing(self):
        from core.tts import _append_lcd_page

        page = _append_lcd_page(["", ""], "Hello there friend")
        self.assertEqual(page, ["Hello there", "friend"])
        page = _append_lcd_page(page, "this fits")
        self.assertEqual(page, ["Hello there", "friend this fits"])
        page = _append_lcd_page(page, "replacement")
        self.assertEqual(page, ["replacement", ""])

    def test_tag_only_pcm_dispatches_friendly_lcd_text(self):
        from core import tts

        actions = []
        streamer = tts.LocalTTSStreamer()
        streamer._display_queue = _RecordingQueue(actions)
        streamer._proc = MagicMock()
        streamer._write_pcm = lambda pcm: actions.append("pcm")
        streamer._play_gate.set()
        streamer._pcm_queue.put(("meta", "[question-en]", 0, ""))
        streamer._pcm_queue.put(("pcm", b"\x01\x00" * 240))
        streamer._pcm_queue.put(("end",))
        streamer._pcm_queue.put(None)

        with patch.object(tts, "_DISPLAY_CALIBRATION",
                          TimingCalibration(calibration_raw())):
            streamer._play_worker()

        self.assertEqual(actions, ["pcm", "display"])

    def test_first_pcm_write_precedes_lcd_dispatch(self):
        # This is the TTFW invariant: calibration is preloaded and the first
        # LCD queue operation happens only after first PCM is handed to aplay.
        from core import tts

        actions = []
        streamer = tts.LocalTTSStreamer()
        streamer._display_queue = _RecordingQueue(actions)
        streamer._proc = MagicMock()
        streamer._write_pcm = lambda pcm: actions.append("pcm")
        streamer._play_gate.set()
        streamer._pcm_queue.put(("meta", "hello", 0, ""))
        streamer._pcm_queue.put(("pcm", b"\x01\x00" * 240))
        streamer._pcm_queue.put(("end",))
        streamer._pcm_queue.put(None)

        with patch.object(tts, "_DISPLAY_CALIBRATION", _FakeCalibration(actions)):
            streamer._play_worker()

        self.assertIn("pcm", actions)
        self.assertIn("display", actions)
        self.assertLess(actions.index("pcm"), actions.index("display"))
        self.assertLess(actions.index("pcm"), actions.index("schedule"))


if __name__ == "__main__":
    unittest.main()
