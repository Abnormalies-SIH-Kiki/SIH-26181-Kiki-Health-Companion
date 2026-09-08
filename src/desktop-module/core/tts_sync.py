"""Calibrated, recognition-free word timing for the speech LCD.

Whisper is deliberately not imported or called here.  The runtime consumes a
small profile produced by ``scripts/calibrate_lcd_sync.py`` and turns already-known
TTS text into word offsets.  Audio always remains the master clock.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass


PROFILE_VERSION = 1
_TAG_RE = re.compile(r"\[[^\[\]]{1,40}\]")
_TOKEN_RE = re.compile(r"\[[^\[\]]{1,40}\]|[^\s\[]+")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_TRAILING_PUNCT_RE = re.compile(r"[,;:!?।.]+[\"')\]]*$")

# ASCII Hindi romanization for the HD44780 speech display.  This deliberately
# lives in the dependency-free timing module: importing a transliteration
# package (or, worse, calling a service) is unnecessary for a 16x2 display.
# The function is called by the LCD worker only, after PCM has been handed to
# aplay, so the text sent to TTS and its time-to-first-word path are unchanged.
_DEVANAGARI_RUN_RE = re.compile(r"[\u0900-\u097f]+")
_INDEPENDENT_VOWELS = {
    "ऄ": "a", "अ": "a", "आ": "aa", "इ": "i", "ई": "i",
    "उ": "u", "ऊ": "u", "ऋ": "ri", "ॠ": "ri", "ऌ": "li",
    "ॡ": "lee", "ऍ": "e", "ऎ": "e", "ए": "e", "ऐ": "ai",
    "ऑ": "o", "ऒ": "o", "ओ": "o", "औ": "au",
}
_VOWEL_MARKS = {
    "ऺ": "e", "ऻ": "o", "ा": "aa", "ि": "i", "ी": "i",
    "ु": "u", "ू": "u", "ृ": "ri", "ॄ": "ri", "ॢ": "li",
    "ॣ": "lee", "ॅ": "e", "ॆ": "e", "े": "e", "ै": "ai",
    "ॉ": "o", "ॊ": "o", "ो": "o", "ौ": "au",
}
_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ऴ": "l",
    "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "r",
    "ढ़": "rh", "फ़": "f", "य़": "y",
}
_NUKTA_CONSONANTS = {
    "क": "q", "ख": "kh", "ग": "gh", "ज": "z",
    "ड": "r", "ढ": "rh", "फ": "f", "य": "y",
}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _next_consonant(text: str, start: int) -> str:
    for char in text[start:]:
        if char in _CONSONANTS:
            return char
        if char not in {"़", "्"}:
            break
    return ""


def _nasal_for(next_consonant: str) -> str:
    if next_consonant and next_consonant in "कखगघङ":
        return "ng"
    if next_consonant and next_consonant in "चछजझञ":
        return "ny"
    if next_consonant and next_consonant in "पफबभम":
        return "m"
    return "n"


def _romanize_devanagari_run(text: str) -> str:
    """Romanize one Devanagari run into compact, LCD-safe Hinglish."""
    units = []
    i = 0
    while i < len(text):
        char = text[i]
        if char in _CONSONANTS:
            consonant = _CONSONANTS[char]
            i += 1
            if i < len(text) and text[i] == "़":
                consonant = _NUKTA_CONSONANTS.get(char, consonant)
                i += 1
            vowel = "a"
            inherent = True
            if i < len(text) and text[i] in _VOWEL_MARKS:
                vowel = _VOWEL_MARKS[text[i]]
                inherent = False
                i += 1
            elif i < len(text) and text[i] == "्":
                vowel = ""
                inherent = False
                i += 1
            units.append({"text": consonant + vowel, "inherent": inherent,
                          "letter": True})
            continue
        if char in _INDEPENDENT_VOWELS:
            units.append({"text": _INDEPENDENT_VOWELS[char], "inherent": False,
                          "letter": True})
        elif char in ("ं", "ँ"):
            units.append({"text": _nasal_for(_next_consonant(text, i + 1)),
                          "inherent": False, "letter": False})
        elif char == "ः":
            units.append({"text": "h", "inherent": False, "letter": False})
        elif char in ("।", "॥"):
            units.append({"text": ".", "inherent": False, "letter": False})
        elif char in "०१२३४५६७८९":
            units.append({"text": char.translate(_DEVANAGARI_DIGITS),
                          "inherent": False, "letter": False})
        i += 1

    # Hindi normally suppresses a final inherent schwa (namak, not namaka).
    letter_indexes = [index for index, unit in enumerate(units) if unit["letter"]]
    if len(letter_indexes) > 1:
        last_letter = units[letter_indexes[-1]]
        if last_letter["inherent"]:
            last_letter["text"] = last_letter["text"][:-1]
            last_letter["inherent"] = False

    return "".join(unit["text"] for unit in units)


def romanize_hindi_for_lcd(text: str) -> str:
    """Convert Devanagari portions of display text to ASCII Hinglish locally.

    Latin text, spacing and ordinary punctuation are retained.  Keeping this
    as a pure function also makes display conversion deterministic and avoids
    API keys, network waits, model loading, and runtime package dependencies.
    """
    if not text or not _DEVANAGARI_RE.search(text):
        return text
    normalized = unicodedata.normalize("NFC", str(text))
    return _DEVANAGARI_RUN_RE.sub(
        lambda match: _romanize_devanagari_run(match.group(0)), normalized)

# Friendly LCD equivalents for the local voice model's non-verbal expression
# tags. These are deliberately plain text so every HD44780 LCD can render them.
TAG_DISPLAY_TEXT = {
    "laughter": "Ha ha ha!",
    "sigh": "Sigh...",
    "confirmation-en": "Mm-hmm.",
    "question-en": "Hmm?",
    "question-ah": "Ah?",
    "question-oh": "Oh?",
    "question-ei": "Eh?",
    "question-yi": "Yeah?",
    "surprise-ah": "Ahh!",
    "surprise-oh": "Oh!",
    "surprise-wa": "Wow!",
    "surprise-yo": "Yo!",
    "dissatisfaction-hnn": "Hnn...",
}


def configured_profile_path(tts_config: dict) -> str:
    sync_cfg = tts_config.get("display_sync", {})
    path = sync_cfg.get("profile_path", "tools_and_config/lcd_sync_calibration.json")
    if os.path.isabs(path):
        return path
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, path)


def active_sink_name(timeout: float = 0.5) -> str:
    """Best-effort PulseAudio sink fingerprint, resolved outside playback."""
    try:
        result = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def script_for_word(word: str) -> str:
    devanagari = len(_DEVANAGARI_RE.findall(word))
    latin = len(_LATIN_RE.findall(word))
    return "devanagari" if devanagari > latin else "latin"


def normalize_word(word: str) -> str:
    word = unicodedata.normalize("NFKC", word).casefold()
    return "".join(ch for ch in word if ch.isalnum() or 0x0900 <= ord(ch) <= 0x097F)


def visible_words(speakable: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(speakable)
            if not _TAG_RE.fullmatch(token)]


@dataclass(frozen=True)
class WordCue:
    word: str
    offset_s: float
    is_expression: bool = False


@dataclass(frozen=True)
class WordSchedule:
    cues: tuple[WordCue, ...]
    output_latency_s: float
    lcd_write_lead_s: float
    voice: str


class TimingCalibration:
    """Validated timing profile loaded once, never on the TTFW path."""

    def __init__(self, raw: dict | None, reason: str = ""):
        self.raw = raw or {}
        self.reason = reason

    @property
    def valid(self) -> bool:
        return not self.reason and self.raw.get("version") == PROFILE_VERSION

    @property
    def output_latency_s(self) -> float:
        if not self.valid:
            return 0.0
        return max(0.0, float(self.raw.get("output_latency_ms", 0.0)) / 1000.0)

    @classmethod
    def load(cls, tts_config: dict, sample_rate: int) -> "TimingCalibration":
        sync_cfg = tts_config.get("display_sync", {})
        if not sync_cfg.get("enabled", True):
            return cls(None, "display synchronization is disabled")
        path = configured_profile_path(tts_config)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return cls(None, f"calibration profile missing: {path}")
        except Exception as exc:
            return cls(None, f"invalid calibration profile: {exc}")
        if raw.get("version") != PROFILE_VERSION:
            return cls(raw, "calibration profile version mismatch")
        if int(raw.get("sample_rate", 0)) != int(sample_rate):
            return cls(raw, "calibration sample rate mismatch")
        expected_url = str(raw.get("tts_url", "")).rstrip("/")
        actual_url = str(tts_config.get("local_url", "")).rstrip("/")
        if expected_url and expected_url != actual_url:
            return cls(raw, "calibration belongs to a different TTS server")
        expected_sink = str(raw.get("sink_name", ""))
        current_sink = active_sink_name()
        if expected_sink and current_sink and expected_sink != current_sink:
            return cls(raw, f"speaker changed ({expected_sink} -> {current_sink})")
        return cls(raw)

    def schedule(self, speakable: str, voice: str) -> tuple[WordSchedule | None, str]:
        if not self.valid:
            return None, self.reason
        voice_key = (voice or "default").strip().lower() or "default"
        models = self.raw.get("profiles", {}).get(voice_key)
        if not isinstance(models, dict):
            return None, f"voice '{voice_key}' has not been calibrated"

        tokens = _TOKEN_RE.findall(speakable)
        required_scripts = {script_for_word(token) for token in tokens
                            if not _TAG_RE.fullmatch(token)}
        missing = sorted(script for script in required_scripts
                         if not isinstance(models.get(script), dict))
        if missing:
            return None, f"voice '{voice_key}' lacks {','.join(missing)} calibration"

        cues = []
        offset_ms = 0.0
        pending_tag_ms = 0.0
        has_spoken_word = False
        displayed_leading_tag = False
        for token in tokens:
            if _TAG_RE.fullmatch(token):
                # Expression tags can generate audible breaths/laughter before
                # the next spoken word. Only the first tag at the very start of
                # a sentence is useful on the LCD; later/mid-sentence tags stay
                # hidden so they do not interrupt the conversational text.
                offset_ms += pending_tag_ms
                pending_tag_ms = 0.0
                display_text = TAG_DISPLAY_TEXT.get(token[1:-1].strip().lower())
                if display_text and not has_spoken_word and not displayed_leading_tag:
                    cues.append(WordCue(
                        display_text,
                        max(0.0, offset_ms / 1000.0),
                        is_expression=True,
                    ))
                    displayed_leading_tag = True
                tag_model = models.get("tags", {})
                pending_tag_ms = float(tag_model.get("duration_ms", 0.0))
                continue
            model = models[script_for_word(token)]
            offset_ms += pending_tag_ms
            pending_tag_ms = 0.0
            if not has_spoken_word:
                offset_ms += float(model.get("initial_word_ms", {}).get(
                    normalize_word(token), model.get("initial_ms", 0.0)))
            cues.append(WordCue(token, max(0.0, offset_ms / 1000.0)))
            has_spoken_word = True

            letters = sum(ch.isalnum() for ch in token)
            marks = sum(0x0900 <= ord(ch) <= 0x097F and not ch.isalnum()
                        for ch in token)
            learned_word_ms = model.get("word_ms", {}).get(normalize_word(token))
            duration_ms = (float(learned_word_ms) if learned_word_ms is not None else (
                float(model.get("base_ms", 70.0))
                + letters * float(model.get("char_ms", 38.0))
                + marks * float(model.get("mark_ms", 18.0))
            ))
            punct = _TRAILING_PUNCT_RE.search(token)
            if punct and learned_word_ms is None:
                chars = punct.group(0)
                if any(ch in chars for ch in ".!?।"):
                    duration_ms += float(model.get("terminal_pause_ms", 180.0))
                else:
                    duration_ms += float(model.get("comma_pause_ms", 100.0))
            offset_ms += max(0.0 if learned_word_ms is not None else 40.0,
                             duration_ms)

        return WordSchedule(
            cues=tuple(cues),
            output_latency_s=float(self.raw.get("output_latency_ms", 0.0)) / 1000.0,
            lcd_write_lead_s=float(self.raw.get("lcd_write_lead_ms", 0.0)) / 1000.0,
            voice=voice_key,
        ), ""


class LiveDisplaySegment:
    """A word schedule whose clock can absorb PCM-stream underruns live."""

    def __init__(self, schedule: WordSchedule, segment_id: int):
        self.schedule = schedule
        self.segment_id = segment_id
        self._lock = threading.Lock()
        self._anchor = None
        self._stalls: list[tuple[float, float]] = []
        self._end_at = None
        self.cancelled = False

    def start(self, audible_at: float):
        with self._lock:
            self._anchor = audible_at

    def add_window(self, audio_offset_s: float, audible_at: float):
        """Shift future cues when the incoming PCM stream has underrun."""
        with self._lock:
            if self._anchor is None:
                self._anchor = audible_at - audio_offset_s
                return
            prior_shift = sum(delta for point, delta in self._stalls
                              if point <= audio_offset_s)
            expected = self._anchor + audio_offset_s + prior_shift
            delta = audible_at - expected
            if delta > 0.025:
                self._stalls.append((audio_offset_s, delta))

    def finish(self, audible_end_at: float):
        with self._lock:
            self._end_at = audible_end_at

    def cancel(self):
        with self._lock:
            self.cancelled = True

    def target_for(self, cue: WordCue) -> tuple[float | None, float | None, bool]:
        with self._lock:
            if self.cancelled or self._anchor is None:
                return None, self._end_at, self.cancelled
            shift = sum(delta for point, delta in self._stalls
                        if point <= cue.offset_s)
            target = self._anchor + cue.offset_s + shift
            return target, self._end_at, self.cancelled

    def end_state(self) -> tuple[float | None, bool]:
        with self._lock:
            return self._end_at, self.cancelled


def monotonic_time() -> float:
    return time.monotonic()
