"""Find the hotword in Whisper's text instead of in the acoustic stream.

openWakeWord could only ever answer one question -- "did those 1.2 s of audio
sound like the word *kiki*?" -- and it had to answer before the sentence
existed. Three things follow from that, and all three are why this module
exists:

* the wake word had to come **first**, because the model saw the audio before
  the rest of the sentence was spoken;
* a mode could not have its own name as its wake word without training and
  shipping a new ONNX model per mode;
* "hey tiki" was simply a miss -- the acoustic model has no notion of *nearly*.

This gateway already runs Whisper over every utterance, awake or not: idle
speech is transcribed and kept as ambient context (``session._finalize_turn``).
So the name is in *text* well before anything has to act on it, and matching it
there costs no extra inference. What it buys: fuzzy spellings of a name the ASR
is guessing at, per-mode hotwords that are a config value rather than a model,
and a hotword that arrives at the *end* of the sentence it addresses.

**How similarity is scored.** Two measures, because neither is enough alone:

* ``SequenceMatcher`` on the raw letters catches substitutions that keep the
  spelling ("tiki" 0.75, "kikki" 0.89) but is blind to homophones written very
  differently -- "kicky" scores 0.67 and "keeki" 0.44 against "kiki".
* A phonetic *skeleton* -- consonant classes plus a vowel *class* per vowel run
  -- catches exactly those: "kicky", "keeki" and "kikki" all reduce to
  ``KIKI``, the same as "kiki".

Two details of the skeleton are load-bearing, and both were put there because
the looser version answered to ordinary conversation:

* **Vowel runs keep a class** (front ``I``, open ``A``, back ``U``) rather than
  collapsing to a single symbol. Dropping the class entirely made "cookie" and
  "kiki" identical -- and a wake word that fires on "cookie" fires all day.
* **Only an *exact* skeleton match scores.** A near-miss skeleton contributes
  nothing, because partial credit on a 4-symbol string is enormous: "okay"
  (``UKI``) scored 0.86 against "kiki" (``KIKI``) that way.

What survives is the intended behaviour and not much else: "hey tiki" matches
on raw letters (0.75), "kicky"/"keeki"/"kikki" match on sound, and "okay",
"quick", "cookie", "geeky", "nikki", "sticky" and "think" all miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


# Letters only: punctuation and digits are dropped, which is what makes
# "kiki?", "kiki," and "kiki's" all reduce to the token "kiki". \w minus digits
# and underscore keeps Devanagari, so Hindi mode is tokenized the same way.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_VOWELS = frozenset("aeiouy")

# A vowel run becomes one symbol, chosen by the run's first letter: front
# ("kiki"), open ("khaki") or back ("cookie"). Keeping the class is what stops
# every two-hard-consonant word from sounding like the hotword.
_VOWEL_CLASS = {"e": "I", "i": "I", "y": "I", "a": "A", "o": "U", "u": "U"}

# Spelling pairs Whisper alternates between for the same sound. Applied before
# the per-character pass so the digraphs cannot survive into the skeleton.
_DIGRAPHS = (("ck", "k"), ("qu", "k"), ("ph", "f"), ("wh", "w"), ("gh", "g"))

_CONSONANT_CLASS = {"c": "k", "q": "k", "x": "k", "z": "s"}

# Words that carry no query on their own. Their only job here is to decide
# whether an utterance was *just* an address ("hey kiki") -- which opens the
# listening window -- or an address plus a question, which is answered.
_FILLERS = frozenset(
    {
        "a", "ah", "an", "are", "aur", "bhai", "eh", "er", "hai", "hello",
        "hey", "hi", "hmm", "ho", "hola", "listen", "look", "mmm", "namaste",
        "now", "oh", "ok", "okay", "please", "right", "so", "sorry", "suno",
        "the", "there", "uh", "um", "well", "yaar", "yeah", "yes", "yo",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens, punctuation and digits removed."""
    return [word.lower() for word in _WORD_RE.findall(str(text or ""))]


def phonetic_skeleton(word: str) -> str:
    """Consonant classes plus one class symbol per vowel run.

    ``kiki``, ``kikki``, ``kicky`` and ``keeki`` all reduce to ``KIKI``; the
    point is to be blind to how the ASR chose to spell a sound, without being
    blind to which sound it was. ``cookie`` (``KUKI``) is the case that shows
    why vowel *classes* stay in: without them it is spelled differently and
    sounds different, yet reduces to the same string.
    """
    value = str(word or "").lower()
    for source, target in _DIGRAPHS:
        value = value.replace(source, target)
    skeleton: list[str] = []
    previous_vowel = False
    for char in value:
        vowel = char in _VOWELS
        if vowel and previous_vowel:
            # Same run: the class was decided by the letter that opened it.
            continue
        previous_vowel = vowel
        symbol = (
            _VOWEL_CLASS.get(char, "I") if vowel else _CONSONANT_CLASS.get(char, char).upper()
        )
        if not skeleton or skeleton[-1] != symbol:
            skeleton.append(symbol)
    return "".join(skeleton)


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


@dataclass(frozen=True)
class HotwordMatch:
    """Where the hotword landed in an utterance, and how sure we are."""

    hotword: str
    matched: str
    score: float
    exact: bool
    start: int
    length: int
    tokens: tuple[str, ...]

    @property
    def words_before(self) -> int:
        return self.start

    @property
    def words_after(self) -> int:
        return len(self.tokens) - self.start - self.length

    @property
    def residual(self) -> tuple[str, ...]:
        """Everything that is not the hotword and not a filler word."""
        rest = list(self.tokens[: self.start]) + list(self.tokens[self.start + self.length :])
        return tuple(word for word in rest if word not in _FILLERS)

    @property
    def is_bare(self) -> bool:
        """True when the utterance was an address and nothing more."""
        return not self.residual


class HotwordMatcher:
    """Match a mode's hotwords against transcribed text.

    Multi-word hotwords are supported ("hey kiki", "sun rohan"); the utterance
    is scanned with a window the width of each configured phrase.
    """

    def __init__(
        self,
        hotwords,
        threshold: float = 0.75,
        min_fuzzy_length: int = 4,
        skeleton_weight: float = 0.94,
    ):
        self.threshold = float(threshold)
        self.min_fuzzy_length = int(min_fuzzy_length)
        self.skeleton_weight = float(skeleton_weight)
        prepared: list[tuple[str, tuple[str, ...], str, str]] = []
        seen: set[str] = set()
        for entry in hotwords or ():
            phrase = " ".join(tokenize(entry))
            if not phrase or phrase in seen:
                continue
            seen.add(phrase)
            words = tuple(phrase.split(" "))
            joined = "".join(words)
            prepared.append((phrase, words, joined, phonetic_skeleton(joined)))
        self._prepared = tuple(prepared)

    @property
    def hotwords(self) -> tuple[str, ...]:
        return tuple(entry[0] for entry in self._prepared)

    def score(self, candidate: str, hotword_joined: str, hotword_skeleton: str) -> float:
        """Similarity of one candidate spelling to one hotword, 0..1."""
        if candidate == hotword_joined:
            return 1.0
        if len(hotword_joined) < self.min_fuzzy_length:
            # Below this a single substituted letter is most of the word, so
            # fuzzy matching a short hotword matches half the language.
            return 0.0
        letters = _ratio(candidate, hotword_joined)
        if phonetic_skeleton(candidate) == hotword_skeleton:
            # Sounding identical is worth slightly less than being spelled
            # identically, so an exact spelling always wins a tie -- but it
            # still clears the threshold on its own.
            return max(letters, self.skeleton_weight)
        return letters

    def find(self, text: str) -> HotwordMatch | None:
        """The best hotword occurrence in ``text``, or None.

        Ties are broken by the *earliest* occurrence, which is the conservative
        choice: the later a match sits, the more the carry-back rules in
        ``session`` are willing to do with it.
        """
        tokens = tuple(tokenize(text))
        if not tokens or not self._prepared:
            return None
        best: HotwordMatch | None = None
        for phrase, words, joined, skeleton in self._prepared:
            span = len(words)
            for start in range(0, len(tokens) - span + 1):
                candidate = "".join(tokens[start : start + span])
                value = self.score(candidate, joined, skeleton)
                if value < self.threshold:
                    continue
                if best is not None and (
                    value < best.score or (value == best.score and start >= best.start)
                ):
                    continue
                best = HotwordMatch(
                    hotword=phrase,
                    matched=" ".join(tokens[start : start + span]),
                    score=round(value, 3),
                    exact=candidate == joined,
                    start=start,
                    length=span,
                    tokens=tokens,
                )
        return best
