"""Tests for the `<oled:name>` expression tags and the display's state registry.

All hardware-free: `core.oled_display` degrades to emulated mode when the
board/PIL libs are missing, and none of this touches I2C.
"""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import llm
from core.oled_display import (EXPRESSION_STATES, VALID_STATES, _STATE_FPS,
                               OLEDDisplayManager, get_oled_tag_prompt_note,
                               oled_manager)
from robot.oled_tags import extract_oled_tags, last_oled_tag, strip_oled_tags


class TestTagParsing(unittest.TestCase):
    def test_extracts_mid_sentence(self):
        self.assertEqual(
            extract_oled_tags("that is wild <oled:surprised> I did not expect it"),
            ["surprised"])

    def test_extracts_multiple_in_order(self):
        self.assertEqual(
            extract_oled_tags("hi <oled:shy> ok <oled:giggle> done"),
            ["shy", "giggle"])

    def test_last_tag_wins_for_a_sentence(self):
        self.assertEqual(last_oled_tag("hi <oled:shy> ok <oled:giggle> done"), "giggle")
        self.assertIsNone(last_oled_tag("no tags at all here"))

    def test_case_insensitive(self):
        self.assertEqual(extract_oled_tags("hey <OLED:Love> there"), ["love"])

    def test_unknown_names_are_not_extracted(self):
        self.assertEqual(extract_oled_tags("hey <oled:banana> there"), [])

    def test_unknown_names_are_still_stripped(self):
        """A hallucinated expression must never be spoken aloud."""
        self.assertEqual(strip_oled_tags("hey <oled:banana> there"), "hey  there")

    def test_strip_leaves_neck_tags_alone(self):
        """The two tag families are dispatched by different code paths."""
        self.assertEqual(
            strip_oled_tags("hi <oled:shy> and <neck:left> bye"),
            "hi  and <neck:left> bye")

    def test_strip_is_none_safe(self):
        self.assertEqual(strip_oled_tags(None), "")
        self.assertEqual(extract_oled_tags(None), [])


class TestStateRegistry(unittest.TestCase):
    def test_every_expression_is_a_valid_state(self):
        self.assertTrue(EXPRESSION_STATES <= VALID_STATES)

    def test_every_state_has_a_draw_fn_and_fps(self):
        for name in VALID_STATES:
            self.assertTrue(hasattr(OLEDDisplayManager, f"_draw_{name}"),
                            f"{name} has no _draw_{name}")
            self.assertIn(name, _STATE_FPS, f"{name} has no FPS entry")

    def test_no_orphan_draw_fns_or_fps_entries(self):
        drawn = {n[len("_draw_"):] for n in dir(OLEDDisplayManager)
                 if n.startswith("_draw_")}
        self.assertEqual(drawn, set(VALID_STATES))
        self.assertEqual(set(_STATE_FPS), set(VALID_STATES))

    def test_crowd_state_is_gone(self):
        """`too_many_people` fired constantly; it was removed deliberately."""
        self.assertNotIn("too_many_people", VALID_STATES)
        self.assertFalse(hasattr(oled_manager, "show_crowd"))

    def test_expressions_exclude_system_states(self):
        """A reply may colour its face, but not claim to be doing something."""
        for name in ("listening", "thinking", "tool", "music", "face",
                     "workers", "boot", "goodbye", "idle", "speaking"):
            self.assertNotIn(name, EXPRESSION_STATES)

    def test_prompt_note_lists_exactly_the_drawable_expressions(self):
        note = get_oled_tag_prompt_note()
        for name in EXPRESSION_STATES:
            self.assertIn(f"<oled:{name}>", note)
        self.assertIn("NEVER start a reply or a sentence", note)

    def test_prompt_note_is_byte_stable(self):
        """It joins the warmed KV-cache prefix, so it must not vary per run."""
        self.assertEqual(get_oled_tag_prompt_note(), get_oled_tag_prompt_note())


class TestExpressionPriority(unittest.TestCase):
    def setUp(self):
        self.mgr = oled_manager
        self.addCleanup(self.mgr.set_state, "idle")

    def test_applies_while_speaking(self):
        self.mgr.set_state("speaking")
        self.assertTrue(self.mgr.set_expression("giggle"))
        self.assertEqual(self.mgr._state, "giggle")

    def test_applies_over_another_expression(self):
        self.mgr.set_state("speaking")
        self.mgr.set_expression("giggle")
        self.assertTrue(self.mgr.set_expression("proud"))
        self.assertEqual(self.mgr._state, "proud")

    def test_rejected_from_protected_states(self):
        for protected in ("listening", "music", "face", "workers", "idle",
                          "thinking", "goodbye"):
            self.mgr.set_state(protected)
            self.assertFalse(self.mgr.set_expression("love"),
                             f"expression wrongly overrode {protected}")
            self.assertEqual(self.mgr._state, protected)

    def test_rejects_non_expression_states(self):
        """A tag can't be used to fake a system state."""
        self.mgr.set_state("speaking")
        self.assertFalse(self.mgr.set_expression("music"))
        self.assertFalse(self.mgr.set_expression("listening"))
        self.assertEqual(self.mgr._state, "speaking")

    def test_system_state_always_wins_back(self):
        self.mgr.set_state("speaking")
        self.mgr.set_expression("love")
        self.mgr.set_state("tool", "search_web")
        self.assertEqual(self.mgr._state, "tool")
        self.assertFalse(self.mgr._expr_hold)

    def test_tagged_oneshot_is_held_not_auto_reverted(self):
        """`happy`/`sad` are one-shots for play_oneshot, but a tagged mood must
        survive past _ONE_SHOT_FRAMES until the turn ends."""
        self.mgr.set_state("speaking")
        self.mgr.set_expression("happy")
        self.assertTrue(self.mgr._expr_hold)

    def test_speaking_baseline_does_not_wipe_a_held_expression(self):
        """main.py sets "speaking" from the first-play callback at the same
        instant the player fires a sentence-1 tag; the expression must win."""
        self.mgr.set_state("speaking")
        self.mgr.set_expression("love")
        self.mgr.set_state("speaking", "TTFW: 0.8s")   # the racing call
        self.assertEqual(self.mgr._state, "love")
        self.assertTrue(self.mgr._expr_hold)

    def test_turn_end_releases_the_hold(self):
        self.mgr.set_state("speaking")
        self.mgr.set_expression("love")
        self.mgr.set_state("idle")
        self.assertEqual(self.mgr._state, "idle")
        self.assertFalse(self.mgr._expr_hold)
        # A fresh turn starts from the plain speaking face again.
        self.mgr.set_state("speaking")
        self.assertEqual(self.mgr._state, "speaking")

    def test_working_status_no_longer_shows_idle(self):
        self.mgr.update_status("Working", "")
        self.assertEqual(self.mgr._state, "thinking")

    def test_play_oneshot_still_auto_reverts(self):
        self.mgr.set_state("idle")
        self.mgr.play_oneshot("happy", fallback="idle")
        self.assertEqual(self.mgr._state, "happy")
        self.assertFalse(self.mgr._expr_hold)


class TestLeadingTagDoesNotCostTTFW(unittest.TestCase):
    """A silent tag flushed as the first fragment must NOT disarm the eager
    first-sentence path — nothing audible was produced yet."""

    def test_has_spoken_word(self):
        self.assertFalse(llm._has_spoken_word("<oled:excited>"))
        self.assertFalse(llm._has_spoken_word("<neck:left:30>"))
        self.assertFalse(llm._has_spoken_word("[laughter]"))
        self.assertFalse(llm._has_spoken_word("<oled:shy> <neck:center>"))
        self.assertTrue(llm._has_spoken_word("<oled:shy> hello"))
        self.assertTrue(llm._has_spoken_word("hello"))

    def _first_audible_at(self, text):
        """Chars generated before the first AUDIBLE fragment reaches TTS."""
        seen = {"n": 0}

        def deltas():
            for ch in text:
                seen["n"] += 1
                yield ch

        for evt, data in llm._scan_cloud_deltas(deltas(), {"emitted": False}):
            if evt == "sentence" and llm._has_spoken_word(data):
                return seen["n"]
        return None

    def test_mid_sentence_tag_costs_nothing(self):
        baseline = self._first_audible_at("Yeah, I saw that this morning. Wild.")
        tagged = self._first_audible_at(
            "Yeah, I saw that <oled:excited> this morning. Wild.")
        self.assertEqual(baseline, tagged)

    def test_leading_tag_still_flushes_eagerly(self):
        """Before the fix this waited for a full sentence terminator."""
        n = self._first_audible_at("<oled:excited> Yeah, I saw that this morning. Wild.")
        self.assertIsNotNone(n)
        # "<oled:excited> Yeah," == 21 chars; the un-fixed path needed the whole
        # first sentence (46). Assert we are nowhere near that.
        self.assertLess(n, 30)

    def test_leading_neck_tag_still_flushes_eagerly(self):
        n = self._first_audible_at("<neck:center> Yeah, I saw that this morning. Wild.")
        self.assertIsNotNone(n)
        self.assertLess(n, 30)


if __name__ == "__main__":
    unittest.main()
