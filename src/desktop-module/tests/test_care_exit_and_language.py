"""Getting OUT of a care session, and not changing language on the way in.

Both failures were watched live on 2026-08-31, in the same four minutes:

* the person said "shut up" at 22:44:21. That is a terminal command handled in
  main.py before any model runs, so it never reached the care agent's stop
  words -- `active_session` stayed active and the session took the next turn
  anyway. Double-tapping the sensor and simply saying nothing were no better:
  each returned Kiki to idle while the session lived on, so the only real exit
  was the twenty-minute idle timeout.
* the session opened in Hindi and stayed there for four turns while the person
  kept speaking English. The prompt said "use the person's language" and the
  frozen plan snapshot said `language: "hi"`, so the model believed the file
  over the microphone.
"""

import json
from pathlib import Path

import pytest

from core.senior import care_plan as care_plan_module
from core.senior import care_voice_agent as cva


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real CarePlan on disk, wired in as THE store the whole process sees."""
    path = tmp_path / "care_plan.json"
    path.write_text(json.dumps({
        "senior": {"name": "Test", "language": "hi"},
        "family_contacts": [], "reminders": [], "exercises": [],
        "approved_music": [], "approved_topics": [], "care_log": [],
        "metadata": {}, "health_measurements": [],
        "routine_events": [{
            "id": "evt00001", "title": "Something to think about",
            "objective": "keep them company", "category": "memory",
            "schedule": {"kind": "once", "value": "2026-08-31T16:00:00"},
            "actions": [], "continuous_vision": False, "enabled": True,
            "source": "system", "adaptation": {},
        }],
        "active_session": None,
    }))
    plan = care_plan_module.CarePlan(Path(str(path)))
    monkeypatch.setattr(care_plan_module, "get_care_plan_store", lambda: plan,
                        raising=False)
    return plan


@pytest.fixture(autouse=True)
def clean_directive():
    cva._LAST_DIRECTIVE.update({"hold_seconds": 0, "expect_reply": True,
                                "reply_reason": "none", "cue": ""})
    yield


# --- the escape hatches ---

def test_an_outside_exit_closes_a_live_session(store):
    store.start_care_session("evt00001")
    assert cva.end_active_care_session("the person said shut up") is True
    assert store.care_session_state()["status"] != "active"


def test_the_exit_is_archived_with_its_reason(store):
    store.start_care_session("evt00001")
    cva.end_active_care_session("double-tap returned Kiki to idle")

    history = store.get_section("session_history")
    assert history[-1]["status"] == "cancelled"
    assert "double-tap" in history[-1]["end_reason"]


def test_the_exit_is_written_to_the_care_log(store):
    """Kiki's own record of the day should say the session was walked out of."""
    store.start_care_session("evt00001")
    cva.end_active_care_session("no reply during the listening window")
    assert "no reply" in store.get_section("care_log")[-1]["text"]


def test_the_microphone_is_not_left_open_for_an_answer(store):
    """main.py reads the directive right after a turn. A closed session must
    not leave it holding a listening window for a conversation that is over."""
    store.start_care_session("evt00001")
    cva._LAST_DIRECTIVE.update({"expect_reply": True, "hold_seconds": 9,
                                "cue": "start"})
    cva.end_active_care_session("the person said shut up")

    directive = cva.get_last_care_directive()
    assert directive["expect_reply"] is False
    assert directive["hold_seconds"] == 0
    assert directive["cue"] == ""


def test_no_session_is_not_an_error(store):
    """Every caller is an escape path. None of them may raise."""
    assert cva.end_active_care_session("double-tap") is False


def test_a_broken_plan_never_breaks_the_escape_path(monkeypatch):
    class Broken:
        def care_session_state(self):
            raise RuntimeError("plan unreadable")

    monkeypatch.setattr(care_plan_module, "get_care_plan_store", lambda: Broken(),
                        raising=False)
    assert cva.end_active_care_session("shut up") is False


def test_the_session_cannot_be_ended_twice(store):
    store.start_care_session("evt00001")
    assert cva.end_active_care_session("shut up") is True
    assert cva.end_active_care_session("shut up") is False
    assert len(store.get_section("session_history")) == 1


# --- which language to answer in ---

@pytest.mark.parametrize("said,expected", [
    ("I am getting bored, what should I do?", cva.ENGLISH),
    ("मुझे नींद नहीं आ रही", cva.HINDI),
    ("haan theek hai, bata do", cva.HINGLISH),
    ("kya kar rahe ho", cva.HINGLISH),
    ("", ""),
    ("   ", ""),
])
def test_one_utterance_is_classified_by_what_is_in_it(said, expected):
    assert cva._language_of(said) == expected


@pytest.mark.parametrize("said", [
    "the report is done", "hum along with the song", "just stop the bus",
    "who is he", "can you hear me",
])
def test_ordinary_english_is_not_read_as_hinglish(said):
    """A false Hinglish reading is a language switch of its own -- the exact
    failure this resolver exists to prevent."""
    assert cva._language_of(said) == cva.ENGLISH


def test_what_they_just_said_wins(store):
    session = {"transcript": [{"user": "मुझे नींद नहीं आ रही"}],
               "care_context": {"senior": {"language": "hi"}}}
    language, evidence = cva._person_language(session, "no, tell me about it")
    assert language == cva.ENGLISH
    assert evidence == "no, tell me about it"


def test_the_session_transcript_is_next(store):
    session = {"transcript": [{"user": "hello"}, {"user": "मैं ठीक हूँ"}],
               "care_context": {"senior": {"language": "en"}}}
    language, evidence = cva._person_language(session, "", ["earlier english"])
    assert language == cva.HINDI
    assert evidence == "मैं ठीक हूँ"


def test_a_scheduled_session_inherits_the_conversation_it_interrupts():
    """The live failure: an engagement session opened cold, so the ONLY
    evidence was the ordinary conversation happening seconds earlier."""
    session = {"transcript": [], "care_context": {"senior": {"language": "hi"}}}
    language, evidence = cva._person_language(
        session, "", ["what's going on in the world?", "Interesting, how is the air outside?"])
    assert language == cva.ENGLISH
    assert evidence == "Interesting, how is the air outside?"


def test_the_stored_preference_is_the_last_resort_only():
    session = {"transcript": [], "care_context": {"senior": {"language": "hi"}}}
    assert cva._person_language(session, "")[0] == cva.HINDI
    session["care_context"]["senior"]["language"] = "en"
    assert cva._person_language(session, "")[0] == cva.ENGLISH


def test_a_session_with_nothing_at_all_still_resolves():
    assert cva._person_language(None, "")[0] == cva.ENGLISH
    assert cva._person_language({}, "")[0] == cva.ENGLISH


# --- what the model is actually told ---

def _prompt_for(user_text, recent=None, stored="hi"):
    return cva._prompt(
        {"event": {"category": "memory"}, "transcript": [],
         "care_context": {"senior": {"language": stored}}},
        user_text, "no frame attached", recent)


def test_the_prompt_names_the_language_out_loud():
    prompt = _prompt_for("I am getting bored, what should I do?")
    assert "## LANGUAGE" in prompt
    assert "Write `summary` in English" in prompt


def test_the_prompt_never_shows_a_contradicting_stored_preference():
    """One paragraph of instruction cannot out-argue a 9,000-token snapshot
    that says `language: "hi"`, so the field is rewritten rather than argued
    with."""
    prompt = _prompt_for("I am getting bored, what should I do?")
    assert '"language": "hi"' not in prompt
    assert '"language": "english"' in prompt


def test_earlier_wording_is_marked_as_history():
    prompt = _prompt_for("I am getting bored, what should I do?")
    assert "history, not a template" in prompt


# --- the snapshot must not demonstrate the wrong language ---

def _snapshot():
    return {
        "senior": {"name": "Vaibhav", "language": "hi"},
        "session_history": [{
            "event_title": "Something to think about", "status": "completed",
            "turn_count": 3,
            "last_user": "फिर driving licence का printout लेना है ना",
            "last_assistant": "हाँ, ड्राइविंग लाइसेंस का प्रिंटआउट तो ज़रूरी है।",
        }],
        "routine_events": [{
            "id": "evt1", "title": "Neck Routine", "category": "exercise",
            "session_brief": '[{"instruction": "वैभव, कमर की कसरत का समय हो गया है!"}]',
            "actions": [{"type": "speak",
                         "instruction": "वैभव, कमर की कसरत का समय हो गया है!"},
                        {"type": "speak", "instruction": "Roll your shoulders."}],
        }],
        "care_log": [{"kind": "care_session", "text": "सत्र पूरा हुआ"}],
    }


def test_the_stored_language_field_is_rewritten_to_what_is_spoken():
    clean = cva._strip_foreign_speech(_snapshot(), cva.ENGLISH)
    assert clean["senior"]["language"] == cva.ENGLISH
    assert clean["senior"]["name"] == "Vaibhav"


def test_past_replies_in_another_language_are_dropped():
    """Self-perpetuating otherwise: every Hindi session writes more Hindi into
    session_history, which teaches the next session Hindi."""
    clean = cva._strip_foreign_speech(_snapshot(), cva.ENGLISH)
    record = clean["session_history"][0]
    assert "last_assistant" not in record
    assert "last_user" not in record
    # The session still happened -- only the wording went.
    assert record["event_title"] == "Something to think about"
    assert record["turn_count"] == 3


def test_scripted_instructions_in_another_language_are_dropped():
    clean = cva._strip_foreign_speech(_snapshot(), cva.ENGLISH)
    event = clean["routine_events"][0]
    assert "session_brief" not in event
    assert "instruction" not in event["actions"][0]
    assert event["actions"][1]["instruction"] == "Roll your shoulders."
    assert event["title"] == "Neck Routine"


def test_speech_in_the_SAME_language_is_left_alone():
    clean = cva._strip_foreign_speech(_snapshot(), cva.HINDI)
    assert clean["session_history"][0]["last_assistant"].startswith("हाँ")
    assert clean["routine_events"][0]["actions"][0]["instruction"]


def test_facts_are_kept_whatever_language_they_are_in():
    """Only things the model might read as "this is how to word it" are
    dropped. A care-log line is a fact about the day."""
    clean = cva._strip_foreign_speech(_snapshot(), cva.ENGLISH)
    assert clean["care_log"][0]["text"] == "सत्र पूरा हुआ"


def test_a_junk_snapshot_is_returned_untouched():
    class Unserialisable:
        pass

    payload = {"senior": Unserialisable()}
    assert cva._strip_foreign_speech(payload, cva.ENGLISH) is payload
    assert cva._strip_foreign_speech(None, cva.ENGLISH) == {}


def test_the_assembled_prompt_demonstrates_only_the_spoken_language():
    """The end-to-end check: no wording the model could copy is in the wrong
    language, while the facts it needs survive."""
    prompt = cva._prompt(
        {"event": {"category": "memory"}, "transcript": [],
         "care_context": _snapshot()},
        "I am getting bored, what should I do?", "no frame attached")

    assert "ड्राइविंग लाइसेंस" not in prompt        # a past reply
    assert "कमर की कसरत का समय" not in prompt        # a scripted instruction
    assert '"language": "hi"' not in prompt
    assert "Something to think about" in prompt      # the facts stay
    assert "Roll your shoulders." in prompt


def test_the_prompt_quotes_what_they_said():
    prompt = _prompt_for("", ["can you tell me the news"])
    assert 'They last said: "can you tell me the news"' in prompt


def test_hindi_speech_still_gets_hindi():
    prompt = _prompt_for("मुझे नींद नहीं आ रही")
    assert "Write `summary` in Hindi, in Devanagari script" in prompt


def test_the_summary_contract_points_at_the_language_block():
    """The old wording ("use the person's language") is what sent the model to
    the plan file for an answer."""
    prompt = _prompt_for("hello there")
    assert "use the person's language" not in prompt
    assert "the language named" in prompt
