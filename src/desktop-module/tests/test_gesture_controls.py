import asyncio
import unittest
from unittest.mock import Mock, patch

from core.gesture_controls import (
    activity_generation,
    activity_was_stopped,
    is_output_muted,
    mark_activities_stopped,
    set_output_muted,
    toggle_output_mute,
)
from core.tts import LCDOnlyStreamer, TTSStreamer


class GestureControlStateTests(unittest.TestCase):
    def setUp(self):
        set_output_muted(False)

    def tearDown(self):
        set_output_muted(False)

    def test_mute_gesture_toggles_and_factory_selects_lcd_output(self):
        self.assertFalse(is_output_muted())
        self.assertTrue(toggle_output_mute())
        self.assertIsInstance(TTSStreamer(), LCDOnlyStreamer)
        self.assertFalse(toggle_output_mute())
        self.assertFalse(is_output_muted())
        self.assertNotIsInstance(TTSStreamer(), LCDOnlyStreamer)

    def test_stop_generation_invalidates_in_flight_activity(self):
        token = activity_generation()
        self.assertFalse(activity_was_stopped(token))
        mark_activities_stopped()
        self.assertTrue(activity_was_stopped(token))

    def test_lcd_streamer_holds_speculative_text_until_release(self):
        display = Mock()
        display.begin_stream_session.return_value = 7
        display._clean_text.side_effect = lambda text: text
        streamer = LCDOnlyStreamer()
        streamer.hold_playback()

        with patch("core.lcd_display.lcd_manager", display), patch(
            "core.tts.time.sleep", return_value=None
        ):
            streamer.start()
            streamer.add_sentence("hello there")
            display.write.assert_not_called()
            streamer.release_playback()
            streamer.finish()

        self.assertTrue(streamer.first_play_event.is_set())
        self.assertTrue(display.write.called)
        display.end_stream_session.assert_called_once_with(7)

    def test_music_does_not_launch_while_muted(self):
        from tools_and_config import tools

        set_output_muted(True)
        with patch.object(tools.subprocess, "Popen") as popen:
            result = asyncio.run(tools.play_music("anything"))
        self.assertIn("muted", result.lower())
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
