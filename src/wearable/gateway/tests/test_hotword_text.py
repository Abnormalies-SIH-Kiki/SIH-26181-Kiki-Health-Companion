"""The wake word lives in the transcript now, not in the audio.

Two things are being pinned here, and they fail in opposite directions:

* Kiki must answer to her name however Whisper spelled it -- "hey tiki",
  "kicky", "kikki" -- because an ASR guessing at a proper noun is the normal
  case, not the edge case.
* She must NOT answer to "okay", "quick", "cookie" or "sticky". A wake word
  that fires on ordinary conversation is worse than one that misses, because
  every false fire interrupts the room and spends a turn.
"""

from difflib import SequenceMatcher

from kiki_gateway.config import GatewayConfig
from kiki_gateway.hotword_text import HotwordMatcher, phonetic_skeleton, tokenize


def matcher(*hotwords: str) -> HotwordMatcher:
    return HotwordMatcher(hotwords or ("kiki",))


def test_punctuation_and_possessives_do_not_hide_the_name():
    assert tokenize("Kiki, what's up?") == ["kiki", "what", "s", "up"]
    assert matcher().find("Kiki!") is not None
    assert matcher().find("that is kiki's job") is not None


def test_the_spellings_whisper_actually_produces_all_match():
    for heard in ("kiki", "Kiki", "tiki", "kikki", "kicky", "keeki", "kimi"):
        assert matcher().find(f"hey {heard} are you there") is not None, heard


def test_ordinary_words_do_not_wake_her():
    # Each of these shares a consonant shape or a few letters with "kiki", and
    # each of them was accepted by an earlier, looser version of the score.
    for heard in (
        "okay", "quick", "cookie", "geeky", "sticky", "nikki", "vicky",
        "ricky", "khaki", "kinky", "think", "because", "kitty",
    ):
        assert matcher().find(f"so i said {heard} to him") is None, heard


def test_sounding_the_same_scores_below_being_spelled_the_same():
    exact = matcher().find("kiki")
    homophone = matcher().find("kicky")
    assert exact.exact and exact.score == 1.0
    assert not homophone.exact
    assert homophone.score < exact.score


def test_the_skeleton_keeps_vowel_colour():
    # Dropping the vowel class made "cookie" and "kiki" the same string, and a
    # hotword that fires on "cookie" fires all day.
    assert phonetic_skeleton("kiki") == phonetic_skeleton("kicky") == "KIKI"
    assert phonetic_skeleton("cookie") == "KUKI"
    assert phonetic_skeleton("khaki") != phonetic_skeleton("kiki")


def test_a_short_hotword_is_never_fuzzy_matched():
    # "bo" would otherwise match "go", "so", "no" and "oh".
    short = HotwordMatcher(["bo"])
    assert short.find("so we go") is None
    assert short.find("bo can you hear me") is not None


def test_position_is_reported_so_the_session_can_read_the_geometry():
    trailing = matcher().find("what do you think kiki")
    assert trailing.words_before == 4 and trailing.words_after == 0
    leading = matcher().find("kiki what is the weather")
    assert leading.words_before == 0 and leading.words_after == 4


def test_an_address_with_no_question_in_it_is_bare():
    assert matcher().find("kiki").is_bare
    assert matcher().find("hey kiki").is_bare
    assert matcher().find("so uh kiki, listen").is_bare
    assert not matcher().find("kiki what is the weather").is_bare


def test_a_multi_word_hotword_is_matched_as_a_phrase():
    phrase = HotwordMatcher(["hey kiki"])
    assert phrase.find("kiki") is None
    found = phrase.find("okay hey kiki listen")
    assert found is not None and found.length == 2


def test_a_mode_can_answer_to_its_own_name():
    rohan = matcher("rohan")
    assert rohan.find("rohan what are you doing") is not None
    assert rohan.find("rowan what are you doing") is not None
    assert rohan.find("kiki what are you doing") is None


def test_the_default_hotword_list_is_a_comma_string():
    assert GatewayConfig().default_hotwords() == ("kiki",)
    assert GatewayConfig(hotwords="kiki, Rohan ,").default_hotwords() == ("kiki", "rohan")


def test_the_threshold_documented_in_the_module_is_the_one_in_use():
    # "hey tiki" is the example the whole design was asked to survive, and it
    # sits exactly on the default threshold -- so a raise, however small,
    # silently drops it.
    assert SequenceMatcher(None, "kiki", "tiki").ratio() == 0.75
    assert GatewayConfig().hotword_similarity <= 0.75
