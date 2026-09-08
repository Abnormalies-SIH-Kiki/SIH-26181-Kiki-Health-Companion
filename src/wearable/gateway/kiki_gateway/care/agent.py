"""Conversational foreground agent for a live care session, on this body.

Vendored from the RPi's `core/senior/care_voice_agent.py`, with one structural
substitution: **the evidence is wrist motion, not a camera frame.**

The scheduler may start a session, but it never speaks. Every spoken turn is
owned by the normal gateway voice lifecycle (mute microphone, cloud care model,
streaming TTS, reopen microphone). The care plan is context, not a script
interpreter.

What changed from the RPi version, and why:

* `_fresh_visual_frame` -> `_fresh_motion_window`. This board has no camera. It
  has a 50 Hz IMU on the wrist, so a guided movement is judged from measured
  motion (`imu.py`) instead of pixels. Windows are captured during the hold the
  model itself asked for.
* `visual_observation` -> `motion_observation`, `person_in_frame` ->
  `band_worn`. Same position in the JSON contract, same reason: the observation
  is written BEFORE the verdict and the verdict BEFORE the praise, because a
  judgement written after the praise is written to agree with it.
* Cerebras is called with text only. The measured features go into the prompt
  as numbers; nothing here asks a language model to do signal processing.

Everything else -- the language handling, the deterministic session closers,
the repeat detector, the streak policy, the "never wait in silence" rule -- is
the RPi's hard-won behaviour and is deliberately unchanged.
"""

from __future__ import annotations

import asyncio
import json
from .instructor import GUIDANCE as INSTRUCTOR_GUIDANCE
import re
import threading
import time
from typing import Any, Dict, Optional

from . import imu
from .settings import care_settings


_CARE_SESSION_TOOLS = {
    "get_care_plan", "update_care_plan", "alert_family",
    "measure_heart_rate", "get_wearable_status",
    "play_music", "set_timer", "get_current_time", "recall_memory",
    # An engagement session is only as good as the material it is built from.
    # `conversation_topics` is what lets the agent open with something from this
    # person's actual life instead of a generic prompt, and `search_web` is what
    # keeps it about the world rather than only about the past.
    "conversation_topics", "search_web",
}


class _NullRecorder:
    """Observability is optional here, unlike on the RPi.

    Two different KikiFast snapshots can be loaded as the runtime and neither
    is guaranteed to carry `core.observability`. A care session must not fail
    because the thing that WATCHES care sessions is missing.
    """

    def start_session(self, *_args, **_kwargs):
        return None

    def end_session(self, *_args, **_kwargs) -> None:
        return None


def _recorder():
    try:
        from core.observability import get_recorder

        return get_recorder()
    except Exception:
        return _NullRecorder()


def _cfg() -> Dict[str, Any]:
    return care_settings()


def _exercise_cfg() -> Dict[str, Any]:
    """Guided-exercise tunables. Flat here; nested under `guided_exercise` on
    the RPi. Both are accepted so a config copied from KikiFast still works."""
    settings = care_settings()
    nested = settings.get("guided_exercise")
    merged = dict(settings)
    if isinstance(nested, dict):
        merged.update(nested)
    return merged


# What the turn that just spoke wants to happen next: how long to beep out a
# hold, and whether an answer is actually needed. main.py reads this
# immediately after awaiting run_care_voice_turn.
#
# Module state rather than a return value because the return type is the spoken
# string that main.py and the existing tests both depend on. Care turns are
# serialized by the single foreground turn lifecycle, so there is never a
# second one in flight to race with this.
_LAST_DIRECTIVE: Dict[str, Any] = {
    "hold_seconds": 0, "expect_reply": True, "reply_reason": "none", "cue": ""}

# Listening is the exception, and it has to be justified. Anything outside this
# set is treated as "no reason given", which means keep leading the routine —
# so a model that drifts back into conversational habits cannot stall the
# session just by omitting or inventing a value.
_REPLY_REASONS = {"aborted", "incorrect_form", "safety", "choice"}

# Deterministic "the person asked to stop", underneath the model's wording.
#
# Ending a session used to depend ENTIRELY on the model choosing to emit
# `session: "complete"`, and the same prompt tells it "usually it is continue".
# The observed result was an eight-turn neck session that never ended, held the
# care lock against every other due routine, and swallowed unrelated
# conversation until the idle timeout fired twenty minutes later.
#
# So a clear spoken stop now ends the session whatever the model returns. These
# are deliberately unambiguous exit phrases, not general negatives: "no" and
# "नहीं" are ordinary answers inside a care conversation ("any pain?" -> "no")
# and must never end it. Anchored to word boundaries so "बसंत" is not "बस" and
# "stopwatch" is not "stop".
# A bare "stop" is unambiguous as the WHOLE utterance and ambiguous inside a
# sentence -- "I do not want to stop" is the opposite instruction. So the single
# words are matched only as a complete utterance, and anything embedded in a
# longer sentence has to carry more evidence than one word.
_STANDALONE_STOP = {
    "stop", "enough", "done", "finish", "finished", "cancel", "quiet",
    "बस", "रुको", "रुकिए", "खत्म", "ख़त्म", "रोको", "रोक दो", "रुक जाओ",
    "बंद करो", "छोड़ो", "रहने दो", "चुप", "चुप रहो",
    # ROMANISED Hindi. The STT transliterates unpredictably: in one live run
    # "हम्म" and "पता नहीं" arrived in Devanagari while "बस" arrived as "bus" --
    # and the care model then read "bus" as Hindi *bas* meaning "just", replying
    # "तो बस शुरू करते हैं!" ("so let's just start then"). A stop became a
    # go-ahead. Matching Devanagari only made every romanised stop invisible.
    #
    # "bus" is also an English noun, which is why these are STANDALONE-only:
    # "bus" as an entire utterance mid-care-session is a stop; "the bus is late"
    # is not, and never reaches this set.
    "bus", "bas", "ruko", "rukiye", "roko", "rok do", "ruk jao",
    "band karo", "khatam", "khatm", "chup", "chup raho", "chhodo", "rehne do",
}

_STOP_PHRASES_EN = (
    r"stop (?:it|now|this|there|talking|listening|the (?:session|exercise|routine))",
    r"(?:i am|i'm|im) done", r"that(?:'s| is) (?:enough|all)",
    r"no more", r"enough for (?:now|today)", r"let(?:'s| us) stop",
    r"finish(?:ed)? (?:now|here|for today)", r"cancel (?:this|the session)",
    r"end (?:the )?session", r"we(?:'re| are) done",
    # Said three different ways in one live run, none of them matched:
    # "Just shut up and stop listening." was not a stop, and the session ran on.
    r"shut up", r"be quiet", r"leave (?:it|me)", r"forget it",
    r"not now", r"later",
)
_STOP_PHRASES_HI = (
    r"बस करो", r"बस कीजिए", r"बस अब", r"रुक जाओ", r"रोक दो", r"बंद करो",
    r"नहीं करना", r"नहीं करूँगा", r"नहीं करूंगा", r"आज नहीं", r"अभी नहीं",
    r"रहने दो", r"खत्म करो", r"ख़त्म करो", r"चुप रहो", r"चुप हो जाओ",
    r"शांति चाहिए", r"मत बोलो", r"बात मत करो",
)
# Romanised Hindi phrases live with the English regex: they are Latin script, so
# word boundaries behave, and they are multi-word enough not to need the
# standalone-only guard.
_STOP_PHRASES_ROMAN = (
    r"bas karo", r"bas kar", r"band karo", r"chup raho", r"chup ho ja",
    r"ruk jao", r"rok do", r"rehne do", r"abhi nahi", r"aaj nahi",
    r"shanti chahiye", r"mat bolo",
)
_STOP_RE_EN = re.compile(
    r"\b(?:" + "|".join(_STOP_PHRASES_EN + _STOP_PHRASES_ROMAN) + r")\b",
    re.IGNORECASE)
# Devanagari has no \b that Python's re understands the way Latin does, so the
# Hindi side is bounded by explicit non-Devanagari edges instead. Without this,
# "बसंत" (spring) contains "बस" and would end the session.
_STOP_RE_HI = re.compile(
    r"(?:^|[^ऀ-ॿ])(?:" + "|".join(_STOP_PHRASES_HI) + r")(?:$|[^ऀ-ॿ])")

# Said INSIDE a longer sentence, these reverse the meaning of a stop word.
_STOP_NEGATION_RE = re.compile(
    r"\b(?:do not|don'?t|never|can'?t|cannot|won'?t)\b[^.?!]{0,40}\bstop\b"
    r"|\bnot\s+(?:want|going)\b[^.?!]{0,20}\bstop\b"
    r"|(?:रुकना|रोकना|छोड़ना|बंद)\s*नहीं",
    re.IGNORECASE)

_STOP_PUNCT_RE = re.compile(r"[\s।,.!?\-]+")


def user_asked_to_stop(text: str) -> bool:
    """True when the person clearly asked to end the session.

    Kept conservative on purpose, and in this direction: a false positive cuts
    a session the person wanted, which they recover from in one sentence; the
    failure it replaces -- a session that never ends, holds the care lock, and
    swallows unrelated conversation -- lasted twenty minutes.

    Note "no" and "नहीं" are deliberately absent. They are the ordinary answer
    to "any pain?" inside a care conversation, and treating them as an exit
    would end almost every session on its first turn.
    """
    spoken = str(text or "").strip()
    if not spoken:
        return False
    normalized = _STOP_PUNCT_RE.sub(" ", spoken).strip().lower()
    if normalized in _STANDALONE_STOP:
        return True
    if _STOP_NEGATION_RE.search(spoken):
        return False
    return bool(_STOP_RE_EN.search(spoken) or _STOP_RE_HI.search(f" {spoken} "))

# --- Which language to answer in --------------------------------------------
#
# The care plan stores `senior.language`, and on this robot it says "hi". The
# prompt used to say only "use the person's language", so the model read that
# stored field and answered a wholly English conversation in Hindi -- a jarring
# switch at exactly the moment a session took the microphone over. Observed
# live on 2026-08-31: an engagement session opened in Devanagari and stayed
# there for four turns while the person kept speaking English.
#
# What the person just SAID outranks what the plan remembers about them.
HINDI, HINGLISH, ENGLISH = "hindi", "hinglish", "english"

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")
# Romanised Hindi markers. Deliberately excludes words that are also ordinary
# English ("the", "ho", "hum", "bas"): a false Hinglish reading is a language
# switch of its own, which is the failure being fixed.
_ROMAN_HINDI_RE = re.compile(
    r"\b(?:kya|kyun|kyunki|kaise|kahan|kab|hai|hain|hoon|nahi|nahin|haan|"
    r"aap|aapko|aapka|tumhe|tumhara|mujhe|mera|meri|humein|hamara|"
    r"karo|karna|karke|karta|karti|karenge|raha|rahi|rahe|"
    r"accha|acha|theek|thik|bahut|thoda|abhi|phir|matlab|chalo|"
    r"batao|bata|suno|dekho|yaar|bhai|kuch|koi|bilkul|zaroor|"
    r"shukriya|dhanyavaad|namaste)\b",
    re.IGNORECASE)

_LANGUAGE_NAMES = {
    HINDI: "Hindi, in Devanagari script",
    HINGLISH: ("Hinglish -- Hindi and English mixed the way they mix it, "
               "written in Latin script"),
    ENGLISH: "English",
}


def _language_of(text: str) -> str:
    """Classify one utterance, or "" when there is nothing to go on."""
    spoken = str(text or "").strip()
    if not spoken:
        return ""
    if _DEVANAGARI_RE.search(spoken):
        return HINDI
    if _ROMAN_HINDI_RE.search(spoken):
        return HINGLISH
    if re.search(r"[A-Za-z]", spoken):
        return ENGLISH
    return ""


def _person_language(session: Optional[dict], user_text: str = "",
                     recent_texts: Optional[list] = None) -> tuple[str, str]:
    """The language to answer in, and the words that decided it.

    Newest evidence first: what they just said, then this session's own
    transcript, then the ordinary conversation that was happening before the
    session opened. The stored preference is only reached when the person has
    not said anything at all yet.
    """
    session = session or {}
    candidates = [str(user_text or "")]
    for turn in reversed(session.get("transcript") or []):
        candidates.append(str((turn or {}).get("user") or ""))
    for text in reversed(list(recent_texts or [])):
        candidates.append(str(text or ""))

    for candidate in candidates:
        language = _language_of(candidate)
        if language:
            return language, " ".join(candidate.split())[:160]

    stored = str(((session.get("care_context") or {}).get("senior") or {})
                 .get("language") or "").strip().lower()
    return (HINDI if stored.startswith("hi") else ENGLISH), ""


def _language_directive(language: str, evidence: str) -> str:
    heard = f'They last said: "{evidence}"\n' if evidence else ""
    return (
        "## LANGUAGE\n\n"
        f"{heard}"
        f"Write `summary` in {_LANGUAGE_NAMES[language]}, and hold that script "
        "for the whole turn.\n"
        "A session must never switch the language the conversation was already "
        "being held in. If they switch, follow them on your next turn.\n"
        "Any wording that survives in the snapshot or the transcript from an "
        "earlier session is history, not a template. Never copy its language.\n\n")


# Fields of the frozen snapshot that are EXAMPLES OF SPEECH rather than facts.
# A 9,000-token snapshot showing Kiki's own past Hindi turns will out-argue one
# paragraph of instruction every time -- the model imitates what it is shown.
# Worse, it is self-perpetuating: each Hindi session writes more Hindi into
# `session_history`, which teaches the next session Hindi, and so on. Observed
# live on 2026-08-31: the language block said English, the snapshot carried
# 575 Devanagari characters of past replies plus `language: "hi"`, and the
# session opened in Hindi anyway.
def _strip_foreign_speech(context: dict, language: str) -> dict:
    """A copy of the snapshot with every contradicting speech example removed.

    Facts are kept whatever language they are in. Only the things the model
    might reasonably read as "this is how the last turn was worded" are
    dropped, and only when they contradict the language actually being spoken.
    """
    try:
        clean = json.loads(json.dumps(context or {}))
    except (TypeError, ValueError):
        return context or {}

    senior = clean.get("senior")
    if isinstance(senior, dict):
        # The single most authoritative-looking Hindi signal in the prompt.
        senior["language"] = language

    def _contradicts(text: Any) -> bool:
        found = _language_of(text if isinstance(text, str) else "")
        return bool(found) and found != language

    for record in clean.get("session_history") or []:
        if not isinstance(record, dict):
            continue
        for field in ("last_user", "last_assistant"):
            if _contradicts(record.get(field)):
                record.pop(field, None)

    for event in clean.get("routine_events") or []:
        if not isinstance(event, dict):
            continue
        if _contradicts(event.get("session_brief")):
            event.pop("session_brief", None)
        for action in event.get("actions") or []:
            if isinstance(action, dict) and _contradicts(action.get("instruction")):
                action.pop("instruction", None)
    return clean


# Grounded phrases that contradict reply_reason="none". The live model wrote
# "remained in Pranamasana ... not yet attempting the Haske Stretch" in its
# observation, then praised the person and advanced anyway. These are
# deliberately strong phrases, not a generic "not", so benign observations
# such as "not showing signs of pain" do not trigger a retry. "No movement",
# "did not move" and "remained still" are the motion-sensor shapes of the same
# admission and are added here.
_MOTION_RETRY_RE = re.compile(
    r"\b(?:did not attempt|didn't attempt|has not attempted|have not attempted|"
    r"not yet attempt(?:ing|ed)?|has not yet moved|have not yet moved|"
    r"not performing|not following|failed to (?:attempt|reach|perform)|"
    r"instead of|rather than|incorrect (?:form|pose|posture)|wrong (?:form|pose|posture)|"
    r"misaligned|no movement|did not move|didn't move|remained still|"
    r"stayed still|wrist was still|no motion)\b",
    re.IGNORECASE,
)


# The model's own structured verdict on the motion window it was just handed.
# It is asked for BEFORE `summary` in the JSON contract, because a judgement
# written after the praise is written to agree with it: on 2026-09-02 a
# seven-turn neck routine confirmed every single asana ("held the position
# steadily across both frames", turn after turn, in the prompt's own wording)
# while the person was not moving at all. Anything that is not an unambiguous
# "yes" stops the routine here, in code, rather than being argued with in the
# prompt. The sensor changed; the failure mode did not.
_FOLLOWED_YES = {"yes", "true", "correct", "followed"}
_FOLLOWED_NO = {"no", "false", "partly", "partial", "unclear", "unsure",
                "not_attempted", "incorrect"}

# "The band is not on a wrist at all." The motion-sensor equivalent of an empty
# frame, and it stops the routine for the same reason: samples from a device
# lying on a table are not a person to correct, so timing another hold against
# them is the routine talking to itself.
_NOT_WORN = {"no", "false", "absent", "off", "removed", "none", "not_worn"}

# One mismatch is not a conversation. Telling someone their tilt was wrong and
# then falling silent to wait for them to say something about it turns an
# exercise into an interview -- and the person is mid-routine, not at a desk.
# So a first mismatch is corrected OUT LOUD and the same movement is run again
# with the microphone still muted; only a SECOND consecutive mismatch, or an
# empty frame, is worth stopping for.
_DEFAULT_VIOLATIONS_BEFORE_LISTENING = 2
_DEFAULT_RETRY_HOLD_SECONDS = 8

# The verdict this module appends to each persisted motion_observation. Reading
# the streak back out of the transcript rather than holding a counter in memory
# means it survives a restart and belongs to the session it was recorded in.
_VERDICT_TAG_RE = re.compile(r"\[instruction_followed:\s*([a-z_]+)\]",
                             re.IGNORECASE)


def _violation_streak(session: Optional[dict]) -> int:
    """Consecutive mismatching turns already recorded, newest first."""
    streak = 0
    for turn in reversed((session or {}).get("transcript") or []):
        match = _VERDICT_TAG_RE.search(str((turn or {}).get("motion_observation") or ""))
        if not match or match.group(1).strip().lower() not in _FOLLOWED_NO:
            break
        streak += 1
    return streak


def _enforce_motion_reply_contract(final: Optional[dict], spoken: str,
                                   motion_observation: str,
                                   has_motion_evidence: bool = True,
                                   motion_expected: bool = False,
                                   session: Optional[dict] = None
                                   ) -> tuple[Optional[dict], str]:
    """Never advance past a mismatch — but only STOP when stopping is warranted.

    Deterministic, under the model's wording, in priority order:

    * motion evidence was expected and no window arrived -> say so, hand the
      turn back. There is nothing to judge and nothing to correct.
    * the band is not being worn -> say so, hand the turn back. Timing another
      hold against a device on a table helps nobody.
    * a mismatch (`instruction_followed` is not "yes", or an observation that
      admits a non-attempt) -> correct it OUT LOUD and run the same movement
      again with the microphone still muted. Only the SECOND consecutive
      mismatch stops the routine and waits for an answer: one wrong tilt is
      something to fix mid-routine, not something to hold an interview about.
    """
    if not isinstance(final, dict):
        return final, spoken
    reason = str(final.get("reply_reason", "none") or "none").strip().lower()
    # Nothing is being advanced INTO if the session is ending. A closing line
    # must not be replaced by a retry request or by "I cannot see you" — both
    # of these guards exist to stop the routine moving on, and it is not.
    if str(final.get("session", "continue") or "continue").strip().lower() \
            not in {"", "continue"}:
        return final, spoken
    # Pain, an abort and a real choice are the model's to call and they stop the
    # routine the first time they happen. A form complaint is not: it goes
    # through the same streak policy as one the code caught, so "your tilt was
    # short" cannot turn into a silence the person is expected to fill.
    if reason not in {"none", "incorrect_form"}:
        return final, spoken

    from .cadence import clamp_hold_seconds

    hindi = any("ऀ" <= ch <= "ॿ" for ch in str(spoken or ""))
    cfg = _exercise_cfg()

    def _stop(new_reason: str, line: str) -> tuple[dict, str]:
        corrected = dict(final)
        corrected["reply_reason"] = new_reason
        corrected["hold_seconds"] = 0
        corrected["cue"] = ""
        return corrected, line

    # No evidence, on a routine that asked to be able to feel the movement.
    # Continuing from motion that does not exist is exactly the failure the
    # "no motion data" prompt text was meant to prevent, and a model will
    # cheerfully ignore it -- so it is enforced here instead.
    if motion_expected and not has_motion_evidence:
        print("[Care] no motion window on a motion-tracked routine — refusing "
              "to advance blind; handing the turn back to the person")
        return _stop("choice", (
            "मुझे अभी बैंड से कोई हलचल महसूस नहीं हो रही, इसलिए मैं बता नहीं सकती कि "
            "आपने यह किया या नहीं। क्या बैंड आपकी कलाई पर है और हम आगे बढ़ें?"
            if hindi else
            "I am not feeling any movement from the band right now, so I cannot "
            "tell whether that was done. Is it on your wrist, and shall we carry "
            "on?"))

    # The band is off. Not a form problem: there is nobody to correct, and
    # beeping out another hold against a device on a table is the routine
    # talking to itself.
    if has_motion_evidence and str(
            final.get("band_worn", "") or "").strip().lower() in _NOT_WORN:
        print("[Care] the band reports it is not being worn — stopping the "
              "routine instead of timing another hold")
        return _stop("aborted", (
            "लगता है बैंड अभी आपकी कलाई पर नहीं है। क्या आप इसे पहनकर अभ्यास "
            "जारी रखना चाहेंगे?"
            if hindi else
            "It looks like the band is not on your wrist right now. Would you "
            "like to put it back on and carry on with the exercise?"))

    followed = str(final.get("instruction_followed", "") or "").strip().lower()
    admitted = bool(_MOTION_RETRY_RE.search(str(motion_observation or "")))
    # "yes" and an empty/unknown value both mean "no objection". Only an
    # explicit negative verdict counts, so a model that omits the field cannot
    # be worse off than before the field existed.
    verdict_failed = followed in _FOLLOWED_NO or admitted
    if not verdict_failed and reason == "none":
        return final, spoken

    streak = _violation_streak(session) + 1
    limit = max(1, int(cfg.get("violations_before_listening",
                               _DEFAULT_VIOLATIONS_BEFORE_LISTENING)))
    if streak >= limit:
        print(f"[Care] mismatch {streak}/{limit} — stopping the routine and "
              f"asking the person directly (instruction_followed="
              f"{followed or 'absent'}, admitted_in_text={admitted})")
        return _stop("incorrect_form", (
            "पिछली हरकत निर्देश के अनुसार नहीं हुई। कृपया वही एक बार फिर "
            "सही तरीके से कीजिए। क्या आप अभी उसे दोबारा करने के लिए तैयार हैं?"
            if hindi else
            "That last movement did not match what I asked for. Let's do the "
            "same one again, properly this time. Are you ready to try it "
            "again now?"))

    # First mismatch: SAY what was wrong, run the same movement again, and keep
    # the microphone shut. The person is mid-exercise; they need an instruction,
    # not a question.
    corrected = dict(final)
    corrected["reply_reason"] = "none"
    # A form retry repeats the previous agent-selected animation, too. A model
    # proposing the next move cannot make the picture contradict this correction.
    previous = ((session or {}).get("transcript") or [{}])[-1]
    corrected["instructor"] = previous.get("instructor")
    corrected["cue"] = "wrong"
    corrected["hold_seconds"] = (
        clamp_hold_seconds(final.get("hold_seconds"),
                           int(cfg.get("max_hold_seconds", 120)))
        or int(cfg.get("retry_hold_seconds", _DEFAULT_RETRY_HOLD_SECONDS)))
    again = (
        "यह वैसा महसूस नहीं हुआ जैसा मैंने कहा था। चलिए वही एक बार फिर करते हैं — "
        "धीरे से, और वहीं रोककर रखिए।"
        if hindi else
        "That did not feel like the movement I asked for. Let's do that same "
        "one again — slowly, and hold it there.")
    # A correction the MODEL wrote is more use than a generic one, as long as it
    # is not a question: asking something and then muting the microphone is the
    # one thing a guided routine must never do. Praise the model wrote while
    # believing the movement was correct is discarded outright.
    keep = bool(reason == "incorrect_form" and "?" not in str(spoken or "")
                and str(spoken or "").strip())
    line = f"{str(spoken).strip()} {again}" if keep else again
    print(f"[Care] mismatch {streak}/{limit} — correcting out loud and "
          f"repeating the same movement, microphone stays muted "
          f"(hold {corrected['hold_seconds']}s)")
    return corrected, line


def _ensure_reply_question(final: Optional[dict], spoken: str) -> str:
    """Every justified listening transition must first ask what to answer."""
    if not isinstance(final, dict) or "?" in str(spoken or ""):
        return spoken
    reason = str(final.get("reply_reason", "none") or "none").strip().lower()
    if reason not in _REPLY_REASONS:
        return spoken
    hindi = any("\u0900" <= ch <= "\u097f" for ch in str(spoken or ""))
    questions = {
        "incorrect_form": (
            "क्या आप उसी आसन को सही मुद्रा में अभी दोबारा करने के लिए तैयार हैं?"
            if hindi else
            "Are you ready to repeat that same asana in the correct position now?"),
        "aborted": (
            "क्या आप यह अभ्यास यहीं रोकना चाहते हैं?"
            if hindi else "Do you want to stop the exercise here?"),
        "safety": (
            "क्या आपको दर्द, चक्कर या सांस लेने में तकलीफ़ हो रही है?"
            if hindi else
            "Are you feeling pain, dizziness, or difficulty breathing?"),
        "choice": (
            "आप आगे क्या करना चाहेंगे?"
            if hindi else "What would you like to do next?"),
    }
    return (str(spoken or "").rstrip() + " " + questions[reason]).strip()


def end_active_care_session(reason: str, status: str = "cancelled") -> bool:
    """Close a live session from OUTSIDE the care turn loop. True if one closed.

    Every exit the person actually reaches for lives in main.py, not in this
    agent: saying "shut up", double-tapping the IR sensor, or simply not
    answering. Each of those returned Kiki to idle while `active_session`
    stayed active -- so the very next thing said, minutes later and about
    anything, was swallowed by the care agent again, and the only real way out
    was the twenty-minute idle timeout.

    Observed live on 2026-08-31: "shut up" at 22:44:21 was handled by the
    shut-up command path, never reached `user_asked_to_stop`, and the session
    carried straight on into its next turn.

    Silent by design -- all three are ways of asking Kiki to be quiet.
    """
    try:
        from .plan import get_care_plan_store
        plan = get_care_plan_store()
        session = plan.care_session_state()
        if session.get("status") != "active":
            return False
        title = session.get("event_title") or "Care session"
        plan.finish_care_session(status, reason=reason)
        plan.add_care_log("care_session", f"{title} ended ({reason}).")
    except Exception as exc:
        print(f"[Care] could not end the session ({reason}): {exc}")
        return False
    # main.py reads this right after a care turn; a closed session must not
    # leave it holding the microphone open for an answer to nothing.
    _LAST_DIRECTIVE.update({"expect_reply": False, "hold_seconds": 0,
                            "cue": "", "reply_reason": "none"})
    print(f"[Care] Session ended — {reason}.")
    return True


def get_last_care_directive() -> Dict[str, Any]:
    """Hold/reply intent from the most recent care turn (a copy)."""
    return dict(_LAST_DIRECTIVE)


# Categories where the person is MOVING and should not have to talk to keep the
# session going. Everything else is a conversation.
_PHYSICAL_CATEGORIES = {"exercise"}


def _is_physical_session(session: Optional[dict]) -> bool:
    """Is this session a physical routine rather than a conversation?

    Defaults to True when the session is unknown, so the exercise behaviour
    this gate protects stays the fallback rather than something that silently
    switches off.
    """
    if not isinstance(session, dict):
        return True
    event = session.get("event")
    if not isinstance(event, dict):
        return True
    if str(event.get("_legacy_kind", "")).strip().lower() == "exercise":
        return True
    return str(event.get("category", "")).strip().lower() in _PHYSICAL_CATEGORIES


# Two identical turns opens the microphone; three ends the session. Three is
# deliberately low: by the third the person has already heard the same sentence
# three times and nothing about a fourth is going to help.
_MAX_REPEATS = 3

_REPEAT_NORMALISE_RE = re.compile(r"[\s।,.!?\-—…\[\]]+")


def _repeat_depth(session: Optional[dict], spoken: str) -> int:
    """How many turns in a row have now said this same thing, counting this one.

    Observed live on 2026-09-01 01:07. A waist-exercise session opened with the
    scripted line from `routine_events[].actions[0].instruction` --
    "वैभव, कमर की कसरत का समय हो गया है! क्या आप तैयार हैं?" -- with
    reply_reason "none", so the microphone stayed muted, so nobody could answer
    the question it had just asked, so the next turn read the same first action
    and said the identical sentence. Nineteen times, five seconds apart, until
    the process was killed.

    Every ingredient was already known: the plan carries a literal script, and
    this box recites prompt examples word for word. What was missing was
    anything that noticed. Identical text is an unambiguous signal, so this is
    a code-level check rather than another paragraph asking the model to vary.
    """
    def key(text: str) -> str:
        return _REPEAT_NORMALISE_RE.sub(" ", str(text or "")).strip().lower()

    current = key(spoken)
    if not current:
        return 0
    depth = 1
    for turn in reversed((session or {}).get("transcript") or []):
        if key((turn or {}).get("assistant")) != current:
            break
        depth += 1
    return depth


def _is_opening_turn(session: Optional[dict]) -> bool:
    """Nothing has been said in this session yet.

    An absent session is NOT an opening turn. Unknown means keep the existing
    exercise behaviour, the same convention `_is_physical_session` follows: a
    gate must not switch itself on just because it was handed nothing.
    """
    if not isinstance(session, dict):
        return False
    return not (session.get("transcript") or [])


def _set_last_directive(final: Optional[dict], ok: bool,
                        spoken: str = "", session: Optional[dict] = None,
                        repeat_depth: int = 1) -> None:
    from .cadence import clamp_hold_seconds

    cfg = _exercise_cfg()
    # Clear on every turn, including failures: a previous demo is never a default.
    _LAST_DIRECTIVE["instructor"] = None
    if not ok or not isinstance(final, dict):
        # A failed turn must never leave the routine driving itself onward —
        # fall back to simply listening.
        _LAST_DIRECTIVE.update({
            "hold_seconds": 0, "expect_reply": True, "reply_reason": "none"})
        return

    reason = str(final.get("reply_reason", "none") or "none").strip().lower()
    if reason not in _REPLY_REASONS:
        reason = "none"

    # A reason to wait is only honoured if the person was actually asked
    # something. Falling silent after a statement is how the routine used to
    # stall: the person had nothing to answer and no idea they were expected to.
    if reason != "none" and "?" not in str(spoken or ""):
        print(f"[Care] reply_reason={reason} but no question was asked — "
              f"continuing the routine instead of waiting in silence")
        reason = "none"

    from .cadence import CUE_NAMES
    cue = str(final.get("cue", "") or "").strip().lower()
    if cue not in CUE_NAMES:
        cue = ""

    # Continuing without listening is an EXERCISE behaviour: mid-routine the
    # person is moving, and stopping to ask permission between asanas is what
    # made an earlier version stall. In a CONVERSATION it is exactly wrong, and
    # it was observed live -- an engagement session asked a real question about
    # the person's own project, muted the microphone because reply_reason was
    # "none", was handed "[NO REPLY - CONTINUE THE ROUTINE YOURSELF]", and
    # repeated the identical question. Kiki must never ask someone something and
    # then refuse to listen.
    physical = _is_physical_session(session)

    # A routine's FIRST line is almost always "are you ready?" -- an actual
    # question, to an actual person, before any movement has begun. Muting the
    # microphone there is never right, whatever reply_reason says. This is the
    # mirror of the guard above: that one refuses to wait when nothing was
    # asked; this one refuses to charge on when something was.
    if physical and reason == "none" and _is_opening_turn(session) \
            and "?" in str(spoken or ""):
        print("[Care] opening turn asked a question — listening for the answer "
              "instead of continuing from sensor evidence")
        reason = "choice"

    # Saying the identical sentence twice is not progress, and continuing from
    # sensor evidence only produces a third. Hand the turn back to the person.
    if physical and repeat_depth > 1:
        print(f"[Care] the same line has now been said {repeat_depth}x — "
              f"opening the microphone instead of continuing the routine")
        reason = "choice" if reason == "none" else reason

    _LAST_DIRECTIVE.update({
        "hold_seconds": clamp_hold_seconds(
            final.get("hold_seconds"),
            int(cfg.get("max_hold_seconds", 120))),
        "reply_reason": reason,
        "expect_reply": (reason != "none" or not physical
                         or repeat_depth > 1),
        "cue": cue,
    })
    if physical and reason == "none" and not _LAST_DIRECTIVE["expect_reply"] \
            and _LAST_DIRECTIVE["hold_seconds"] > 0 \
            and final.get("session", "continue") == "continue":
        from .instructor import validate
        _LAST_DIRECTIVE["instructor"] = validate(final.get("instructor"))


def _tool_catalog() -> str:
    """The tools a live care session may use, rendered for the prompt.

    Two sources, in this order: the care tools this package defines (the plan,
    the wearable, the family alert) and the generic ones the loaded Kiki
    runtime already has (music, timers, memory, search). The runtime's catalog
    is read defensively -- `gateway/legacy_kiki` and `gateway/kiki_runtime` are
    different snapshots of KikiFast and neither is guaranteed to carry any
    particular tool.
    """
    from .tools import care_tool_catalog_lines

    lines = list(care_tool_catalog_lines())
    try:
        from tools_and_config.tools import TOOLS
    except Exception:
        return "\n".join(lines)
    for tool in TOOLS:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if name not in _CARE_SESSION_TOOLS:
            continue
        if any(line.startswith(f"- {name}(") for line in lines):
            continue
        props = fn.get("parameters", {}).get("properties", {})
        required = set(fn.get("parameters", {}).get("required", []))
        args = []
        for key, spec in props.items():
            label = key if key in required else f"{key}?"
            if spec.get("enum"):
                label += "=" + "|".join(str(value) for value in spec["enum"])
            args.append(label)
        desc = str(fn.get("description", "")).splitlines()[0][:180]
        lines.append(f"- {name}({', '.join(args)}): {desc}")
    return "\n".join(lines)


def _execute_tool(name: str, args: dict) -> str:
    """Run one tool for the care agent. Care tools first, runtime tools after.

    The care tools are intercepted rather than delegated because the loaded
    runtime's `get_care_plan`/`update_care_plan` write the OLD `senior` mode's
    plan file. In `health_sih` they must reach this package's store, or the
    person's plan would be split across two files that never see each other.
    """
    name = str(name or "")
    from .tools import execute_care_tool, is_care_tool

    if is_care_tool(name):
        return execute_care_tool(name, args or {})
    if name not in _CARE_SESSION_TOOLS:
        return f"BLOCKED: {name!r} is not available in a live care session."
    try:
        from tools_and_config.tools import execute_tool
    except Exception as exc:
        return f"(tool {name} unavailable in this runtime: {str(exc)[:120]})"
    return execute_tool(name, args)


async def _fresh_motion_window(session: dict) -> tuple[Optional[Dict[str, Any]], str]:
    """Measured wrist motion for this turn, or an explicit statement of absence.

    Returns `(features, status_text)`. `features` is `imu.analyse()` output --
    counted movements, swept degrees, stillness, wear -- and `status_text` is
    the block that goes into the prompt verbatim.

    The absence path is the important one. A missing window must produce "I
    cannot feel any movement", never a guess dressed up as an observation: on
    the camera version of this routine a frozen frame had the model cheerfully
    confirming someone's form while they were out of the room. Here a stuck
    sensor is caught by digest in `ImuWindowStore.take()` and arrives as
    exactly the same kind of absence.
    """
    if not session.get("motion_tracking"):
        return None, ("No movement tracking was requested for this event, so "
                      "you have no sensor evidence this turn. Ask the person "
                      "how it went rather than asserting what they did.")

    # Switched off on the watch. Distinct from a sensor that failed: the person
    # chose this, so say so plainly and lead the routine by asking, instead of
    # implying something is broken.
    try:
        from .runtime import care_runtime_if_live

        runtime = care_runtime_if_live()
        if runtime is not None and not runtime.movement_checks_enabled:
            return None, (
                "Movement checks are switched OFF on the watch, so you cannot "
                "measure what they did and no sensor is broken. Lead the "
                "routine by asking them how each movement went, and never "
                "state or imply that you measured anything.")
    except Exception:
        pass

    timeout = float(_cfg().get("window_timeout_seconds", 6.0))
    store = imu.get_window_store()
    window, reason = await asyncio.to_thread(store.take, timeout)
    if window is None:
        print(f"[Care] no motion window this turn: {reason}")
        return None, imu.evidence_text(None, note=reason)
    features = imu.analyse(window)
    print(f"[Care] motion window: {features['seconds']}s, "
          f"{features['reps']} movement(s), {features['swept_degrees']} deg swept, "
          f"{features['intensity']}, worn={features['worn']}")
    return features, imu.evidence_text(features, note=window.note)


# How many turns from the ceiling the model is told to start closing. Landing
# the ending itself is much better than having code cut the conversation off
# mid-sentence; the hard limit is only there for when this is ignored.
_WRAP_UP_MARGIN = 5


def _environment_brief() -> str:
    """Live weather/air quality for the care agent, or an explicit absence.

    A morning briefing cannot honestly mention today's heat or air quality
    unless the model is actually holding the numbers, and the alternative --
    a tool call on a latency-critical spoken turn -- costs a round trip on
    every session that mentions the weather.

    The absence is stated explicitly rather than omitted. A silent gap invites
    the model to fill it from training data, which is exactly how a fabricated
    AQI reaches someone deciding whether it is safe to go for a walk.
    """
    try:
        from .mode import mode_has_capability
        if not mode_has_capability("environment"):
            return "Not tracked in this mode. Do not discuss current weather or air quality."
        from .environment import get_environment_provider
        snapshot = get_environment_provider().snapshot()
    except Exception:
        return "Unavailable. Say you do not have it rather than estimating."
    if not snapshot.get("available"):
        return ("Unavailable right now. Say you do not have today's reading "
                "rather than estimating one.")
    parts = []
    if snapshot.get("temperature_c") is not None:
        parts.append(f"{snapshot['temperature_c']:.0f}C")
    if snapshot.get("apparent_temperature_c") is not None:
        parts.append(f"feels {snapshot['apparent_temperature_c']:.0f}C "
                     f"(heat: {snapshot.get('heat_band')})")
    if snapshot.get("humidity_pct") is not None:
        parts.append(f"humidity {snapshot['humidity_pct']:.0f}%")
    if snapshot.get("aqi") is not None:
        parts.append(f"AQI ~{snapshot['aqi']} ({snapshot.get('aqi_category')}, "
                     f"driven by {snapshot.get('aqi_driver')}; PM2.5 "
                     f"{snapshot.get('pm2_5')}, PM10 {snapshot.get('pm10')})")
    stale = (" This reading is "
             f"{round((snapshot.get('age_seconds') or 0) / 60)} minutes old."
             if snapshot.get("state") == "stale" else "")
    return (f"{snapshot.get('place') or 'Home'}: " + ", ".join(parts) + "."
            + stale
            + " The AQI is an estimate on India's CPCB scale from current hourly"
              " PM, not an official station reading — describe it plainly and"
              " never quote it as an official figure.")


def _wrap_up_notice(session: dict) -> str:
    """A leading instruction to bring a long session to a close, or ""."""
    remaining = session.get("turns_remaining")
    if not isinstance(remaining, int) or remaining > _WRAP_UP_MARGIN:
        return ""
    if remaining <= 0:
        return ("THIS SESSION IS OVER. It has reached its turn limit. Say a "
                "short, warm closing line and set `session` to `complete`. Do "
                "not start anything new.\n\n")
    return (f"THIS SESSION HAS RUN LONG — about {remaining} turn(s) remain. "
            "Bring it to a natural close now: finish what is in progress, say "
            "a warm closing line, and set `session` to `complete`. Do not "
            "begin a new activity.\n\n")


_EXERCISE_GUIDANCE = """## LEADING AN EXERCISE (read this before answering during a physical routine)

You are the instructor, not an interviewer. The person is moving; they should
not have to talk to keep the routine going. Lead it.

You cannot see them. You are worn on their wrist, and what you get instead of a
photograph is a MEASUREMENT of how that wrist moved: how many distinct
movements, how many degrees of rotation, how hard, and whether it was still.
That is real evidence and it is enough to lead a routine with — but only for
the arm the band is on, and only for movement. It tells you nothing about the
rest of their body or their expression, so never claim otherwise.

* Give ONE instruction, then set `hold_seconds` to how long they should hold or
  keep moving. Kiki beeps once per second for exactly that long and records the
  wrist for that whole window, so the timing is real. NEVER count out loud in
  `summary` — writing "five, four, three, two, one" makes TTS say it in two
  seconds and the person gets no actual time. Write "hold it there" and put 5 in
  `hold_seconds`.
* After the hold, the microphone STAYS MUTED. You get the next turn immediately
  with the measured motion from that window and
  `[NO REPLY - CONTINUE THE ROUTINE YOURSELF]`. Judge whether the PREVIOUS
  instruction was actually followed before choosing what to say next.
* **Read the numbers before you speak.** Fill in `motion_observation` and
  `instruction_followed` from the measurements FIRST, then write `summary` to
  match that verdict. Going the other way round — writing the praise and then
  an observation that agrees with it — is the one failure this routine cannot
  survive: a live neck session on the camera version of this code confirmed six
  asanas in a row while the person sat motionless, because every observation was
  assembled from the instruction rather than from the evidence.
* **Movements are on the wrist the band is worn on.** If you ask for something
  that arm does not do — a neck turn, a leg raise, a breath — the sensor cannot
  confirm it, and that is `instruction_followed: "unclear"`, not `"yes"`. Prefer
  movements the wrist actually takes part in: arm raises, shoulder rolls,
  reaches, wrist and elbow circles, marching arms, stretches held with the arm.
* A still wrist is the COMMON case when someone is ignoring the routine. If the
  window says the wrist was still and you asked for a movement, it did not
  happen: that is `instruction_followed: "no"`, not encouragement.
* Use the measurement to correct, briefly and kindly, then keep going —
  "a little slower, and make the circle bigger" — rather than stopping to
  discuss it.
* Encourage in passing, in the same breath as the next instruction. Do not send
  a turn that is only praise, and do not ask permission between steps.

### Safety, which outranks finishing the routine

* Conservative movements only: slow, small, within a comfortable range, seated
  where that is a sensible option. Never full neck circles, never anything
  ballistic, never a stretch pushed "as far as it goes".
* Pain, dizziness, breathlessness or unsteadiness stops the routine
  immediately — `reply_reason: "safety"` and ask directly.
* A hard impact in the window (the evidence says so explicitly) means check on
  them before anything else.
* You are not a physiotherapist. Never diagnose, never work around a named
  condition by inventing a therapy, and never push someone through discomfort.

### When to stop and listen

Default to NOT listening. You stop the routine and wait for an answer only when
the measurement shows something you cannot resolve by carrying on, and you must
name which in `reply_reason`:

- `"aborted"` — they have stopped or are clearly no longer participating. The
  band being off the wrist is this, not a form problem — set `band_worn` to
  `"no"` as well.
- `"incorrect_form"` — this is the SECOND mismatch in a row. Something is not
  getting through, so stop, correct it, and end with a specific question asking
  them to retry or confirm readiness. For the FIRST mismatch use `"none"`
  instead: name what was wrong and run that same movement again with a fresh
  `hold_seconds`, without waiting to be answered.
- `"safety"` — signs of pain, dizziness, breathlessness, unsteadiness.
- `"choice"` — you genuinely need a decision (continue or finish, which side
  hurts).
- `"none"` — everything else. This is the common case. Keep leading.

Someone silently doing the instructed movement is `"none"`: briefly acknowledge
it and give the next instruction. `"none"` is NOT allowed when your own
`motion_observation` says they did not attempt or did not perform the previous
instruction. Never praise or advance when the measurement contradicts that.

**Whenever `reply_reason` is not `"none"`, `summary` MUST end with the actual
question you want answered** — a specific one they can answer in a word:
"Vaibhav, kya aapko dard ho raha hai?" or "Should we stop here?" Never fall
silent expecting them to guess, and never wait on a statement.
"""


_CONVERSATION_GUIDANCE = """## LEADING A CONVERSATION SESSION

This is a conversation, not a routine. The person is sitting and talking with
you, and the whole value is in what they say back.

* **When you ask something, listen.** Never ask a question and then keep
  talking. One turn, one thing to respond to, then stop.
* Build on their actual answer rather than moving to your next idea. A session
  that follows one thread properly beats one that covers five.
* If an answer is short or flat, that is information: ask about a different
  corner of it, or change the subject entirely. Do not repeat a question they
  have already heard -- rephrasing the same question is how a session stalls.
* Keep your turns shorter than theirs. You are drawing them out, not
  performing.
* `hold_seconds` is for giving them thinking time on something timed. Kiki
  beeps for that long and then LISTENS -- it does not skip their answer.
"""


def _session_guidance(session: dict) -> str:
    """The half of the prompt that depends on what kind of session this is.

    Handing the exercise instructions to a conversation was an observed failure,
    not a theoretical one. "Default to NOT listening", "you are the instructor,
    not an interviewer" and "[NO REPLY - CONTINUE THE ROUTINE YOURSELF]" are
    correct mid-asana and exactly backwards mid-question: the live engagement
    session asked about the person's own project, set reply_reason="none"
    because that is what this block told it to do, had the microphone muted
    underneath it, and repeated the identical question.
    """
    if _is_physical_session(session):
        return _EXERCISE_GUIDANCE
    return _CONVERSATION_GUIDANCE


# The care agent used to open with nothing but the care plan: no persona, no
# idea what was being said sixty seconds earlier. That is why an engagement
# session felt like a stranger taking over -- asked "I'm getting bored" in the
# middle of a conversation about the day's build, it reached past the live
# conversation entirely and opened on a topic from a previous evening.
#
# The action agent already solves this (`action_agent._background`), reading
# out-of-band from core.llm so none of it lands in the speaking model's KV
# prefix. The care agent gets the same treatment, with a bigger persona budget
# for one reason: the action agent's summary is re-voiced by the speaking model,
# which still holds the full persona. A care reply goes STRAIGHT to TTS. Here
# Kiki's voice has to already be in the prompt, because nothing downstream will
# put it back.
_CONTEXT_DEFAULTS = {"persona_chars": 3000, "history_chars": 9000,
                     "history_record_chars": 700, "artifact_limit": 8}


def _context_cfg(key: str) -> int:
    try:
        return int(_cfg().get(key, _CONTEXT_DEFAULTS[key]))
    except (TypeError, ValueError):
        return _CONTEXT_DEFAULTS[key]


# Frozen for the life of one session, like `care_context` and for the same two
# reasons. Semantically: the heading says "the conversation this session
# INTERRUPTED", and a block that drifted would start echoing the session's own
# replies back at it, duplicating the transcript below. Mechanically: this sits
# at the top of the prompt, so anything that changed here would invalidate the
# whole Cerebras prefix every turn -- the live run caches 9,216 of 10,163
# tokens, and that is the difference between a 0.6s turn and a slow one.
#
# One slot, because exactly one care session can be active at a time.
_BACKGROUND_CACHE: Dict[str, str] = {"session_id": "", "text": ""}


def _kiki_background(session: Optional[dict]) -> str:
    """Who Kiki is, and what was being said when this session opened.

    The conversation half is deliberately limited to conversation sessions. A
    guided exercise is judged against measured wrist motion and the instruction
    just given; what was said about WhatsApp five minutes ago is dilution, and
    the exercise prompt is long already.
    """
    session_id = str((session or {}).get("id") or "")
    if session_id and _BACKGROUND_CACHE["session_id"] == session_id:
        return _BACKGROUND_CACHE["text"]

    parts = []
    try:
        from core import llm
        persona = llm.persona_brief(_context_cfg("persona_chars"))
        if persona:
            parts.append("## WHO YOU ARE\n\nYou are the same Kiki described "
                         "here. A care session does not replace any of it:\n"
                         f"{persona}")
    except Exception as exc:
        print(f"[Care] persona unavailable: {exc}")

    if _is_physical_session(session):
        return _freeze_background(session_id, parts)

    try:
        from core import llm
        # Never re-snapshotted during a session: core.llm records this on the
        # speaking path, which a care turn does not take. So it stays exactly
        # what it was when the session opened -- which is what "what were we
        # just talking about" means -- and the prefix stays cacheable.
        history = llm.conversation_snapshot(
            _context_cfg("history_chars"), _context_cfg("history_record_chars"))
        if history:
            parts.append(
                "## THE CONVERSATION THIS SESSION INTERRUPTED (oldest first)\n\n"
                "This is what you and they were actually talking about moments "
                "ago. Continue from HERE. Opening on something unrelated -- a "
                "topic from another evening, a fact out of nowhere -- is what "
                "makes a session feel like a stranger took over. Background, "
                f"not a new instruction:\n{history}")
        artifacts = llm.conversation_artifacts(_context_cfg("artifact_limit"))
        if artifacts:
            parts.append("## LINKS AND IDS SEEN RECENTLY (newest first)\n\n"
                         + "\n".join(f"- {a}" for a in artifacts))
    except Exception as exc:
        print(f"[Care] conversation context unavailable: {exc}")

    try:
        from core.media_manager import music_manager
        current = music_manager.snapshot().get("current") or {}
        if current.get("title"):
            parts.append(f"## MUSIC PLAYING RIGHT NOW\n\n{current['title']}")
    except Exception:
        pass

    return _freeze_background(session_id, parts)


def _freeze_background(session_id: str, parts: list) -> str:
    text = "\n\n".join(parts) + ("\n\n" if parts else "")
    if session_id:
        _BACKGROUND_CACHE.update({"session_id": session_id, "text": text})
    return text


_VOICE_TAGS = """## HOW YOU SOUND

`summary` is spoken by the same voice as every other Kiki reply, so it carries
the same markup:

* Emotion tags in square brackets: [warm], [cheerful], [gentle], [chuckle],
  [sigh], [reassuring]. Use them the way you always do -- sparingly, where the
  feeling is real.
* Neck tags run silently and are never spoken: `<neck:left>`, `<neck:right>`,
  `<neck:center>`.
* No emoji, no markdown, no stray symbols. Keep your humour; a session is not
  a reason to become solemn.

"""


def _prompt(session: dict, user_text: str, motion_status: str,
            recent_texts: Optional[list] = None) -> str:
    # care_context was frozen when the event began. Re-sending that identical
    # prefix is required by a stateless API and is cheap on Cerebras prompt
    # caching; it also prevents live WebUI edits from silently changing a
    # session halfway through.
    language, evidence = _person_language(session, user_text, recent_texts)
    context = _strip_foreign_speech(session.get("care_context") or {}, language)
    transcript = session.get("transcript") or []
    event = session.get("event") or {}
    return f"""{_wrap_up_notice(session)}{_kiki_background(session)}You are Kiki, currently conducting one live care session by voice.
You—not a script runner—own the interaction from beginning to end. Understand
the complete hand-off and person context below, decide what matters now, and
speak naturally. The person may answer unexpectedly, ask a side question,
change direction, pause, repeat, or stop; respond to what they actually said.

The hand-off describes intentions and known facts. It is not proof that any
activity happened, and legacy `actions` fields are background material rather
than an execution queue. Never invent a person's reply or continue both sides
of the conversation. Produce one useful spoken turn, then listen.

Use your own care reasoning to make the session substantial and appropriate to
the available context. Do not diagnose, alter medicine/dose, fabricate clinical
authority, or claim certainty beyond what the attached measurement actually
shows. You have no camera on this body: never say or imply that you can see the
person or the room. If important context is missing, ask naturally. Use a tool
only when the turn actually requires an external action or measurement.

SESSION EVENT:
{json.dumps(event, ensure_ascii=False, default=str)}

COMPLETE CARE-PLAN SNAPSHOT FROM SESSION START:
{json.dumps(context, ensure_ascii=False, default=str)}

REAL SESSION TRANSCRIPT (oldest first):
{json.dumps(transcript, ensure_ascii=False, default=str)}

CURRENT MOTION EVIDENCE (measured on the wrist band):
{motion_status}

CURRENT OUTSIDE CONDITIONS:
{_environment_brief()}

CURRENT PERSON SPEECH:
{user_text if user_text.strip() else '[The scheduled session has just begun; nobody has replied yet.]'}

AVAILABLE TOOLS:
{_tool_catalog()}

{_session_guidance(session)}
{INSTRUCTOR_GUIDANCE if _is_physical_session(session) else ''}
{_VOICE_TAGS}{_language_directive(language, evidence)}Return exactly one JSON object.
To use tools: {{"tool_calls":[{{"tool":"name","args":{{...}}}}]}}
To speak now, emit the keys in EXACTLY this order — what you saw comes before
what you say about it, because a judgement written after the praise is written
to agree with the praise:
{{"status":"completed",
"motion_observation":"what the measured numbers actually say the wrist did, in your own words, or empty when no motion window was attached",
"band_worn":"yes|no|not_applicable",
"instruction_followed":"yes|no|unclear|not_applicable",
"reply_reason":"none|aborted|incorrect_form|safety|choice",
"hold_seconds":0,
"instructor":null,
"cue":"",
"summary":"exact words Kiki will say",
"session":"continue|complete|cancelled|declined"}}

`motion_observation` — write this FIRST, before you have decided anything.
Say what the MEASUREMENT reports: how many distinct movements were counted, how
many degrees were swept, whether the wrist was still, how the wrist angle
changed. Do not restate the instruction you gave and do not reuse the wording of
an earlier turn's observation — those describe what you asked for, not what
happened. Leave it empty when no motion window was attached, and never write an
observation you cannot point at in the numbers.

`band_worn` — does the band report that it is on a wrist? `"no"` when the
evidence says it is not being worn. `"not_applicable"` when no motion window is
attached. This is the one thing that stops a routine immediately: there is
nobody wearing it to correct.

`instruction_followed` — your verdict on the measurement you just described, for
the instruction you gave on the PREVIOUS turn. `"yes"` ONLY when those numbers
are consistent with that movement having happened. `"no"` when the wrist was
still, or moved far less or far more than the instruction called for. `"unclear"`
when the movement was one the wrist genuinely cannot report on — a neck turn, a
leg movement, a breath — or when the numbers do not settle it. `"not_applicable"`
when you gave no physical instruction last turn or no window is attached. So
`"yes"` is a statement about measurements, not encouragement.

Anything other than `"yes"` means Kiki does NOT move on to the next movement.
The FIRST such turn is corrected out loud and the same movement runs again with
the microphone still muted — say what was wrong and ask for that same movement
once more, do not ask a question. Only a SECOND mismatch in a row stops the
routine to wait for an answer.

`hold_seconds` — seconds to beep out after speaking, while the person holds the
position or keeps moving. 0 when there is nothing to time. Never counted aloud.
During the hold Kiki records the wrist at 50 Hz and hands you the measured
result on the next turn, so you can tell whether the movement actually happened.

`cue` — an optional short sound played right after you speak, to give an
activity shape: `start` (a round begins), `correct`, `wrong` (a soft descending
pair, never a buzzer), `timeup`, `applause` (they finished something well).
Leave it empty for ordinary conversation. Use it sparingly and only when it
marks a real moment — a sound on every turn stops meaning anything.

`reply_reason` — why the routine should stop and wait, per the rules above.
`"none"` keeps you leading. Anything else makes Kiki listen, and REQUIRES that
`summary` ends with the question you want answered.

`status=completed` means this MODEL TURN is ready for speech. The separate
`session` field says whether the overall care session continues. Usually it is
`continue`. End it only when the real conversation has reached an end or the
person asks to stop. The summary goes directly to TTS: no markdown, no JSON
commentary, no relay phrasing, and it is written in the language named
under LANGUAGE above.
"""


async def run_care_voice_turn(user_text: str = "",
                              stop_event: Optional[threading.Event] = None,
                              recent_texts: Optional[list] = None) -> str:
    """Run one real microphone turn through the persistent care conversation."""
    from core.agent_loop import run_agent_loop
    from core.brain import fast_cloud

    from .plan import get_care_plan_store

    recorder = _recorder()

    plan = get_care_plan_store()
    session = plan.care_session_state()
    if session.get("status") != "active":
        return "CARE_ACTION_FAILED: There is no active care session to continue."

    _language, _evidence = _person_language(session, user_text, recent_texts)
    print(f"[Care] Language: {_language}"
          + (f" (from {_evidence!r})" if _evidence else " (stored default)"))

    features, motion_status = await _fresh_motion_window(session)
    cfg = _cfg()
    deadline = float(cfg.get("turn_deadline_seconds", 90))
    owned_stop = stop_event is None
    stop_event = stop_event or threading.Event()
    timer = threading.Timer(deadline, stop_event.set)
    timer.daemon = True
    timer.start()
    started = time.time()
    sid = recorder.start_session(
        "care_voice", name=session.get("event_title", "care session"),
        model=fast_cloud.active_model(), event_id=session.get("event_id"),
        user_text=str(user_text)[:500], motion=motion_status[:1000],
        motion_window=bool(features),
        movements=(features or {}).get("reps"))

    def llm_fn(prompt_text: str) -> str:
        if stop_event.is_set():
            return ""
        # Text only. The motion evidence is already a block of measured
        # numbers in the prompt; there is no image on this body to attach, and
        # the older runtime snapshots do not accept one anyway.
        return fast_cloud.complete(
            prompt_text, provider="cerebras", stop_event=stop_event)

    def guidance(_total, _used):
        return ("Use the real tool result above, then emit the final JSON for "
                "this spoken turn with status, summary, and session. Do not "
                "simulate what the person says next.")

    try:
        ok, result, _speak, final, tools_used = await run_agent_loop(
            _prompt(session, str(user_text or ""), motion_status, recent_texts),
            llm_fn=llm_fn,
            max_turns=int(cfg.get("max_turns", 5)),
            label="CareVoice",
            stop_event=stop_event,
            min_tool_calls=0,
            max_tool_calls=int(cfg.get("max_tool_calls", 6)),
            max_calls_per_turn=1,
            max_prompt_chars=int(cfg.get("max_prompt_chars", 1_000_000)),
            max_tool_result_chars=int(cfg.get("max_tool_result_chars", 3000)),
            continue_guidance_fn=guidance,
            session_id=sid,
            tool_executor=_execute_tool,
        )
    except Exception as exc:
        ok, result, final, tools_used = False, str(exc), None, []
    finally:
        timer.cancel()

    spoken = str(result or "").strip()
    motion_observation = str(
        (final or {}).get("motion_observation") or "").strip()
    if features and not motion_observation:
        motion_observation = (
            "A measured motion window was supplied, but the model returned no "
            "separate motion-observation field.")
    elif not features:
        motion_observation = motion_status
    final, spoken = _enforce_motion_reply_contract(
        final, spoken, motion_observation,
        has_motion_evidence=bool(features),
        # Motion evidence was ASKED for by this event. Losing the sensor on a
        # routine that is steered by it is not a reason to keep steering. The
        # opening turn is exempt: nothing has been instructed yet, so there is
        # no movement to have missed — it is a greeting, not a judgement.
        motion_expected=bool(session.get("motion_tracking")
                             and _is_physical_session(session)
                             and not _is_opening_turn(session)),
        session=session)
    if isinstance(final, dict) and final.get("instruction_followed"):
        print(f"[Care] instruction_followed="
              f"{str(final.get('instruction_followed'))[:24]!r}"
              f" from {'a measured window' if features else 'no motion data'}")
    spoken = _ensure_reply_question(final, spoken)
    repeat_depth = _repeat_depth(session, spoken)
    directive = str((final or {}).get("session", "continue")).strip().lower()
    if directive not in {"continue", "complete", "cancelled", "declined"}:
        directive = "continue"

    # --- Deterministic session end, underneath the model's wording -----------
    # Both overrides exist because the model was previously the ONLY thing that
    # could end a session, and the same prompt tells it to usually continue.
    end_reason = ""
    if directive == "continue" and user_asked_to_stop(user_text):
        directive, end_reason = "cancelled", "person asked to stop"
    if directive == "continue" and session.get("turn_limit_reached"):
        directive, end_reason = (
            "complete", f"turn limit reached ({session.get('turn_limit')})")
    # Opening the microphone did not break the repeat either: the routine is
    # not going anywhere, and the turn ceiling is another three minutes of the
    # same sentence away.
    if directive == "continue" and repeat_depth >= _MAX_REPEATS:
        directive, end_reason = (
            "complete", f"the same line was repeated {repeat_depth}x")
    if end_reason:
        print(f"[Care] Forcing session end: {end_reason}")

    _set_last_directive(final, bool(ok and spoken), spoken, session,
                        repeat_depth=repeat_depth)
    # A forced end must not leave main.py holding the microphone open waiting
    # for an answer to a conversation that is over.
    if end_reason:
        _LAST_DIRECTIVE["expect_reply"] = False
        _LAST_DIRECTIVE["instructor"] = None
        _LAST_DIRECTIVE["hold_seconds"] = 0
        _LAST_DIRECTIVE["cue"] = ""

    # The verdict is recorded next to the observation, so later turns read a
    # transcript that says what was actually judged rather than a run of
    # interchangeable confirmations to pattern-match against.
    _verdict = str((final or {}).get("instruction_followed", "") or "").strip().lower()
    if _verdict and _verdict not in {"not_applicable", "n/a"}:
        motion_observation = (
            f"{motion_observation} [instruction_followed: {_verdict[:16]}]")

    if ok and spoken:
        plan.record_care_turn(
            user_text=user_text, assistant_text=spoken,
            motion_observation=motion_observation, tools_used=tools_used,
            instructor=_LAST_DIRECTIVE.get("instructor"))
        if directive != "continue":
            status = "completed" if directive == "complete" else directive
            plan.finish_care_session(status, reason=end_reason)
            plan.add_care_log(
                "care_session",
                f"{session.get('event_title', 'Care session')} ended as {status}"
                + (f" ({end_reason})." if end_reason else "."))
        recorder.end_session(
            sid, status="done", result=spoken[:800], directive=directive,
            tools_used=tools_used, seconds=round(time.time() - started, 2))
        return spoken

    # Failure language is not task guidance; it is a truthful safety boundary.
    fallback = ("यह देखभाल वाला जवाब अभी पूरा नहीं हो पाया। मैं आपकी ओर से "
                "कोई कदम पूरा मानकर आगे नहीं बढ़ूँगी।"
                if _person_language(session, user_text, recent_texts)[0] == HINDI
                else
                "I could not complete this care response, so I will not mark "
                "anything as done or continue on your behalf.")
    plan.record_care_turn(
        user_text=user_text, assistant_text=fallback,
        motion_observation=motion_observation,
        note=f"Agent failure: {spoken[:500]}",
        tools_used=tools_used)
    # A failing agent must not be able to trap the person in a session they
    # asked to leave. The stop is theirs, not the model's, so it still applies
    # on the path where the model produced nothing usable at all.
    if end_reason:
        try:
            plan.finish_care_session(
                "cancelled" if directive == "cancelled" else "completed",
                reason=end_reason)
            plan.add_care_log(
                "care_session",
                f"{session.get('event_title', 'Care session')} ended "
                f"({end_reason}) despite a failed care turn.")
        except Exception as exc:
            print(f"[Care] could not close the session after a failure: {exc}")
    recorder.end_session(
        sid, status="failed", result=spoken[:800],
        seconds=round(time.time() - started, 2), owned_stop=owned_stop)
    return "CARE_ACTION_FAILED: " + fallback
