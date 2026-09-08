"""A care session must END, and it must not leave its frozen plan copy behind.

Two failures observed on the live robot on 2026-08-29, both from the same root
cause -- the care model was the ONLY thing that could ever end a session, while
its own prompt tells it "usually it is `continue`":

* the 19:07 neck session ran eight turns and was still `active` afterwards,
  holding the care lock against every other due routine and swallowing
  unrelated conversation;
* the finished session stayed in the plan carrying `care_context`, a frozen
  copy of the WHOLE care plan. On the live file that was 18 kB of a 43 kB
  plan, and nothing ever removed it.

So ending a session is now deterministic underneath the model's wording: a
spoken stop, a turn ceiling, or the idle timeout will each close it.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.senior import care_plan as care_plan_module
from core.senior.care_voice_agent import user_asked_to_stop, _wrap_up_notice


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "care_plan.json"
    path.write_text(json.dumps({
        "senior": {"name": "Test", "language": "en"},
        "family_contacts": [], "reminders": [], "exercises": [],
        "approved_music": [], "approved_topics": [], "care_log": [],
        "metadata": {}, "health_measurements": [],
        "routine_events": [{
            "id": "evt00001", "title": "Neck Routine", "objective": "loosen neck",
            "category": "exercise",
            "schedule": {"kind": "once", "value": "2026-08-29T19:07:00"},
            "actions": [], "continuous_vision": False, "enabled": True,
            "source": "user", "adaptation": {},
        }],
        "active_session": None,
    }))
    monkeypatch.setattr(
        care_plan_module, "get_full_config",
        lambda: {"senior_mode": {"care_agent": {
            "session_idle_timeout_minutes": 20, "max_session_turns": 8}}},
        raising=False)
    return care_plan_module.CarePlan(Path(str(path)))


# --- the frozen plan copy must not outlive the session ---

def test_an_active_session_carries_the_frozen_plan(store):
    """The snapshot is load-bearing DURING the session: Cerebras is stateless,
    so the identical prefix is re-sent every turn."""
    store.start_care_session("evt00001")
    assert store.data["active_session"]["care_context"]["routine_events"]


def test_finishing_drops_the_frozen_plan_but_keeps_the_transcript(store):
    store.start_care_session("evt00001")
    store.record_care_turn(user_text="hello", assistant_text="hi there")
    store.finish_care_session("completed")

    session = store.data["active_session"]
    assert "care_context" not in session
    assert session["transcript"][0]["assistant"] == "hi there"


def test_the_drop_survives_a_reload(store):
    store.start_care_session("evt00001")
    store.finish_care_session("completed")
    reloaded = care_plan_module.CarePlan(store.file_path)
    assert "care_context" not in reloaded.data["active_session"]


def test_a_plan_written_before_archiving_heals_itself_on_load(store):
    """The live file already held a finished session with its 15 kB snapshot."""
    store.start_care_session("evt00001")
    store.data["active_session"]["status"] = "completed"   # pre-fix shape
    store.save()
    assert "care_context" in json.loads(store.file_path.read_text())["active_session"]

    reloaded = care_plan_module.CarePlan(store.file_path)
    assert "care_context" not in reloaded.data["active_session"]


def test_an_active_session_is_never_stripped_on_load(store):
    """Self-healing must not disarm a session that is genuinely still running."""
    store.start_care_session("evt00001")
    reloaded = care_plan_module.CarePlan(store.file_path)
    assert reloaded.data["active_session"]["care_context"]["routine_events"]


# --- session history ---

def test_a_finished_session_is_archived(store):
    store.start_care_session("evt00001")
    store.record_care_turn(user_text="done", assistant_text="well done")
    store.finish_care_session("completed")

    history = store.get_section("session_history")
    assert len(history) == 1
    assert history[0]["event_title"] == "Neck Routine"
    assert history[0]["status"] == "completed"
    assert history[0]["turn_count"] == 1
    assert history[0]["last_assistant"] == "well done"


def test_an_abandoned_session_is_archived_too(store):
    store.start_care_session("evt00001")
    store.data["active_session"]["updated_at"] = (
        datetime.now() - timedelta(minutes=90)).isoformat()
    store.save()
    assert store.expire_stale_care_session() is True

    history = store.get_section("session_history")
    assert history[0]["status"] == "abandoned"
    assert "no activity" in history[0]["end_reason"]


def test_history_is_bounded(store, monkeypatch):
    monkeypatch.setattr(care_plan_module, "_MAX_SESSION_HISTORY", 3)
    for _ in range(6):
        store.start_care_session("evt00001")
        store.finish_care_session("completed")
    assert len(store.get_section("session_history")) == 3


def test_history_can_be_read_by_day(store):
    """What an evening reflection asks for: today's confirmed outcomes."""
    store.start_care_session("evt00001")
    store.finish_care_session("completed")
    midnight = datetime.now().replace(hour=0, minute=0, second=0).isoformat()

    assert len(store.session_history_since(midnight)) == 1
    assert store.session_history_since("2099-01-01T00:00:00") == []


# --- the turn ceiling ---

def test_state_reports_the_turn_budget(store):
    store.start_care_session("evt00001")
    state = store.care_session_state()
    assert state["turn_limit"] == 8
    assert state["turns_remaining"] == 8
    assert state["turn_limit_reached"] is False


def test_the_ceiling_trips_once_the_turns_are_spent(store):
    store.start_care_session("evt00001")
    for _ in range(8):
        store.record_care_turn(user_text="ok", assistant_text="and again")
    state = store.care_session_state()
    assert state["turns_remaining"] == 0
    assert state["turn_limit_reached"] is True


def test_a_zero_limit_disables_the_ceiling(store, monkeypatch):
    monkeypatch.setattr(
        care_plan_module, "get_full_config",
        lambda: {"senior_mode": {"care_agent": {"max_session_turns": 0}}},
        raising=False)
    store.start_care_session("evt00001")
    for _ in range(50):
        store.record_care_turn(user_text="ok", assistant_text="still going")
    assert store.care_session_state()["turn_limit_reached"] is False


# --- the model is told to land the ending itself ---

def test_no_notice_while_the_session_is_young():
    assert _wrap_up_notice({"turns_remaining": 30}) == ""


def test_the_model_is_asked_to_close_before_the_ceiling():
    notice = _wrap_up_notice({"turns_remaining": 3})
    assert "RUN LONG" in notice
    assert "complete" in notice


def test_at_the_ceiling_the_notice_is_absolute():
    notice = _wrap_up_notice({"turns_remaining": 0})
    assert "OVER" in notice
    assert "do not start anything new" in notice.lower()


def test_a_session_with_no_limit_gets_no_notice():
    assert _wrap_up_notice({"turns_remaining": None}) == ""


# --- deterministic spoken stop ---

@pytest.mark.parametrize("said", [
    "stop", "stop it", "stop now", "I'm done", "im done", "that's enough",
    "that is all", "no more", "enough for today", "let's stop",
    "cancel this", "end the session", "we're done", "Okay STOP NOW please",
])
def test_english_stops_are_recognised(said):
    assert user_asked_to_stop(said) is True


@pytest.mark.parametrize("said", [
    "बस", "बस करो", "रुको", "रुक जाओ", "रोक दो", "खत्म", "ख़त्म",
    "बंद करो", "आज नहीं", "अभी नहीं", "छोड़ो", "रहने दो",
    "अच्छा बस करो अब", "नहीं करना",
])
def test_hindi_stops_are_recognised(said):
    assert user_asked_to_stop(said) is True


@pytest.mark.parametrize("said", [
    "",
    "no",                        # an ordinary answer to "any pain?"
    "नहीं",                       # the same, in Hindi
    "yes let's continue",
    "my neck hurts a little",
    "मुझे थोड़ा दर्द है",
    "can you repeat that",
    "बसंत ऋतु में",               # contains बस but is not बस
    "I need to stopwatch this",  # contains stop but is not "stop"
    "I do not want to stop",     # explicitly the opposite
    "I don't want to stop yet",
    "मुझे रुकना नहीं है",
    "we should not stop here",
])
def test_ordinary_conversation_does_not_end_the_session(said):
    assert user_asked_to_stop(said) is False


def test_a_stop_is_only_matched_on_a_real_boundary():
    """The failure mode this guards: a session that ends on a substring.

    Ending a session the person wanted costs one sentence to recover. Doing it
    on every utterance containing "bas" would be worse than the bug it fixes.
    """
    assert user_asked_to_stop("नमस्ते") is False
    assert user_asked_to_stop("unstoppable") is False


# --- the end reason is recorded truthfully ---

def test_the_reason_a_session_ended_is_persisted(store):
    store.start_care_session("evt00001")
    store.finish_care_session("cancelled", reason="person asked to stop")
    assert store.data["active_session"]["end_reason"] == "person asked to stop"
    assert store.get_section("session_history")[0]["end_reason"] == "person asked to stop"


def test_a_finished_session_no_longer_blocks_the_next_one(store):
    """The lock-holding half of the live failure."""
    store.start_care_session("evt00001")
    store.finish_care_session("completed")
    store.start_care_session("evt00001")      # must not raise
    assert store.care_session_state()["status"] == "active"


def test_finishing_is_still_rejected_when_nothing_is_running(store):
    with pytest.raises(ValueError):
        store.finish_care_session("completed")


# --- a conversation must never mute the microphone (observed loop) ---

from core.senior import care_voice_agent as cva  # noqa: E402

EXERCISE_SESSION = {"event": {"category": "exercise", "title": "Surya Namaskar"}}
ENGAGEMENT_SESSION = {"event": {"category": "memory",
                                "title": "Something to think about"}}


def test_an_engagement_turn_always_listens():
    """The live loop, 2026-08-31.

    "I'm getting bored" opened an engagement session. Kiki asked a real question
    about the person's own project, then logged "Form accepted - microphone
    stays muted", was handed "[NO REPLY - CONTINUE THE ROUTINE YOURSELF]", and
    repeated the identical question. The exercise auto-continue path had fired
    on a conversation, because `exercise_enabled` is a global config flag rather
    than a property of the session.
    """
    cva._set_last_directive(
        {"summary": "What happened after that pivot?", "reply_reason": "none"},
        ok=True, spoken="What happened after that pivot?",
        session=ENGAGEMENT_SESSION)
    assert cva.get_last_care_directive()["expect_reply"] is True


def test_an_exercise_turn_still_leads_itself():
    """Mid-asana the person is moving and must not have to talk to continue."""
    cva._set_last_directive(
        {"summary": "Hold it there.", "reply_reason": "none", "hold_seconds": 5},
        ok=True, spoken="Hold it there.", session=EXERCISE_SESSION)
    directive = cva.get_last_care_directive()
    assert directive["expect_reply"] is False
    assert directive["hold_seconds"] == 5


def test_an_unknown_session_keeps_the_exercise_behaviour():
    """Fallback stays where it was; the gate must not silently switch off."""
    cva._set_last_directive({"summary": "ok", "reply_reason": "none"},
                            ok=True, spoken="ok", session=None)
    assert cva.get_last_care_directive()["expect_reply"] is False


def test_a_legacy_exercise_record_is_still_physical():
    session = {"event": {"category": "other", "_legacy_kind": "exercise"}}
    assert cva._is_physical_session(session) is True


@pytest.mark.parametrize("category", ["memory", "social", "wellbeing",
                                      "morning", "sleep", "hydration"])
def test_every_non_exercise_category_is_a_conversation(category):
    assert cva._is_physical_session({"event": {"category": category}}) is False


def test_a_conversation_never_sees_the_exercise_instructions():
    """The root cause: the model set reply_reason="none" because the exercise
    block told it to default to not listening."""
    prompt = cva._prompt(
        {"event": {"category": "memory"}, "care_context": {}, "transcript": []},
        "hi", "none")
    assert "LEADING AN EXERCISE" not in prompt
    assert "Default to NOT listening" not in prompt
    assert "When you ask something, listen" in prompt


def test_a_physical_routine_still_gets_the_exercise_instructions():
    prompt = cva._prompt(
        {"event": {"category": "exercise"}, "care_context": {}, "transcript": []},
        "hi", "none")
    assert "LEADING AN EXERCISE" in prompt
    assert "LEADING A CONVERSATION" not in prompt


def test_both_session_kinds_keep_the_json_contract():
    for category in ("exercise", "memory"):
        prompt = cva._prompt(
            {"event": {"category": category}, "care_context": {},
             "transcript": []}, "hi", "none")
        assert "Return exactly one JSON object" in prompt
        assert '"session":"continue|complete|cancelled|declined"' in prompt


# --- the 2026-08-31 hijack: a session that outlived its process ---

def test_a_session_from_a_dead_process_is_ended(store, monkeypatch):
    """The four-minute hijack, in one test.

    A session opened at 19:35 was still `active` when Kiki restarted at 19:53.
    Routing is simply "is a session active", so the care agent then answered a
    maths question, the time, and the weather. The idle timeout could not help:
    each stolen turn refreshed `updated_at`, resetting the very clock that would
    have killed it.
    """
    store.start_care_session("evt00001")
    # Pretend this process started AFTER the session's last turn.
    monkeypatch.setattr(care_plan_module, "_PROCESS_STARTED_AT",
                        datetime.now() + timedelta(minutes=1))

    assert store.end_orphaned_session() is True
    state = store.care_session_state()
    assert state["status"] == "abandoned"
    assert "process that owned it restarted" in state["end_reason"]


def test_a_session_from_this_process_is_left_alone(store):
    """Must never cut off a live conversation on a subsequent read."""
    store.start_care_session("evt00001")
    assert store.end_orphaned_session() is False
    assert store.care_session_state()["status"] == "active"


def test_the_orphan_sweep_is_idempotent(store, monkeypatch):
    store.start_care_session("evt00001")
    monkeypatch.setattr(care_plan_module, "_PROCESS_STARTED_AT",
                        datetime.now() + timedelta(minutes=1))
    assert store.end_orphaned_session() is True
    assert store.end_orphaned_session() is False


def test_a_busy_session_still_dies_of_old_age(store, monkeypatch):
    """The other half: the idle timeout only sees SILENCE.

    A session being actively fed unrelated turns refreshes `updated_at` forever,
    so a wall-clock limit measured from `started_at` is the only thing that can
    stop it.
    """
    monkeypatch.setattr(
        care_plan_module, "get_full_config",
        lambda: {"senior_mode": {"care_agent": {
            "session_idle_timeout_minutes": 20, "max_session_minutes": 45}}},
        raising=False)
    store.start_care_session("evt00001")
    store.data["active_session"]["started_at"] = (
        datetime.now() - timedelta(minutes=50)).isoformat()
    # The sweep is self-healing on read, so recording the next stolen turn is
    # itself where the session dies -- which is the point: it does not need
    # anything to remember to check.
    store.record_care_turn(user_text="still talking", assistant_text="ok")

    state = store.care_session_state()
    assert state["status"] == "abandoned"
    assert "ran for" in state["end_reason"]


def test_a_young_busy_session_survives(store, monkeypatch):
    monkeypatch.setattr(
        care_plan_module, "get_full_config",
        lambda: {"senior_mode": {"care_agent": {
            "session_idle_timeout_minutes": 20, "max_session_minutes": 45}}},
        raising=False)
    store.start_care_session("evt00001")
    store.record_care_turn(user_text="hi", assistant_text="hello")
    assert store.expire_stale_care_session() is False


# --- the STT romanises Hindi, so Devanagari-only matching missed the stop ---

@pytest.mark.parametrize("said", [
    "bus", "bas", "ruko", "chup", "chup raho", "band karo", "khatam",
    "bas karo", "ruk jao", "rehne do", "abhi nahi", "shanti chahiye",
])
def test_romanised_hindi_stops_are_recognised(said):
    """Whisper transcribed "बस" as "bus" and the care model read it as Hindi
    *bas* meaning "just" -- replying "so let's just start then". A stop became a
    go-ahead purely because the matcher only knew Devanagari."""
    assert user_asked_to_stop(said) is True


@pytest.mark.parametrize("said", [
    "Just shut up and stop listening.",
    "stop listening", "shut up", "be quiet", "leave it",
    "मुझे चुप रहो", "दो second शांति चाहिए मुझे चुप रहो",
])
def test_the_stops_said_in_the_live_run_are_recognised(said):
    """Three different phrasings were tried live and none registered."""
    assert user_asked_to_stop(said) is True


@pytest.mark.parametrize("said", [
    "the bus is late",
    "I took the bus home",
    "my bus pass expired",
    "can you book a bus ticket",
])
def test_bus_the_vehicle_is_not_a_stop(said):
    """"bus" is standalone-only precisely because it is also an English noun."""
    assert user_asked_to_stop(said) is False


# --- the 2026-09-01 01:07 repeat loop ---------------------------------------
# A waist-exercise session opened with the scripted line out of
# `routine_events[].actions[0].instruction`:
#
#   "वैभव, कमर की कसरत का समय हो गया है! क्या आप तैयार हैं?"
#
# reply_reason was "none", so the microphone stayed muted -- so nobody could
# answer the question it had just asked -- so the next turn read the same first
# action and said the identical sentence. Nineteen times, five seconds apart,
# until the process was killed.

OPENER = "वैभव, कमर की कसरत का समय हो गया है! क्या आप तैयार हैं?"


def _exercise(transcript=None):
    return {"event": {"category": "exercise", "title": "Waist Exercise"},
            "transcript": list(transcript or [])}


def test_an_opening_question_is_listened_to():
    """The loop, killed at turn one. A routine's first line is almost always
    "are you ready?" -- muting the microphone there is never right."""
    cva._set_last_directive({"summary": OPENER, "reply_reason": "none",
                             "hold_seconds": 0}, ok=True, spoken=OPENER,
                            session=_exercise())
    assert cva.get_last_care_directive()["expect_reply"] is True


def test_an_opening_statement_still_drives_the_routine():
    """Only a QUESTION opens the microphone. "Stand up straight." does not --
    that is the behaviour the muted transition exists for."""
    cva._set_last_directive({"summary": "Stand up straight.", "reply_reason": "none",
                             "hold_seconds": 5}, ok=True,
                            spoken="Stand up straight.", session=_exercise())
    assert cva.get_last_care_directive()["expect_reply"] is False


def test_a_later_question_mid_routine_is_not_treated_as_an_opening():
    """Mid-asana the person is moving and should not have to talk. Only the
    first turn gets the opening rule."""
    session = _exercise([{"user": "", "assistant": "Lean to the left."}])
    cva._set_last_directive({"summary": "Feeling okay?", "reply_reason": "none",
                             "hold_seconds": 0}, ok=True, spoken="Feeling okay?",
                            session=session)
    assert cva.get_last_care_directive()["expect_reply"] is False


# --- repeat detection ---

def test_a_fresh_line_has_no_repeat_depth():
    assert cva._repeat_depth(_exercise(), OPENER) == 1


def test_saying_the_same_thing_again_is_counted():
    session = _exercise([{"user": "", "assistant": OPENER}])
    assert cva._repeat_depth(session, OPENER) == 2


def test_punctuation_and_case_do_not_hide_a_repeat():
    session = _exercise([{"user": "", "assistant": "Are you ready?"}])
    assert cva._repeat_depth(session, "  are you ready  ") == 2


def test_only_CONSECUTIVE_repeats_count():
    """Coming back to a line later is not a stuck loop."""
    session = _exercise([{"user": "", "assistant": OPENER},
                         {"user": "", "assistant": "Now lean left."}])
    assert cva._repeat_depth(session, OPENER) == 1


def test_an_empty_turn_is_not_a_repeat():
    session = _exercise([{"user": "", "assistant": ""}])
    assert cva._repeat_depth(session, "") == 0


def test_a_repeat_opens_the_microphone_even_mid_routine():
    """Saying the identical sentence twice is not progress, and continuing from
    vision only produces a third."""
    session = _exercise([{"user": "", "assistant": OPENER}])
    cva._set_last_directive({"summary": OPENER, "reply_reason": "none",
                             "hold_seconds": 0}, ok=True, spoken=OPENER,
                            session=session, repeat_depth=2)
    directive = cva.get_last_care_directive()
    assert directive["expect_reply"] is True
    assert directive["reply_reason"] == "choice"


def test_the_loop_cannot_survive_three_identical_turns():
    """Opening the microphone did not break it either. The turn ceiling is
    another three minutes of the same sentence away."""
    assert cva._MAX_REPEATS == 3


def test_an_unknown_session_does_not_gain_the_opening_rule():
    """Unknown means keep the existing exercise behaviour. A gate must not
    switch itself on just because it was handed nothing."""
    cva._set_last_directive({"reply_reason": "none"}, ok=True,
                            spoken="Now the other side?")
    assert cva.get_last_care_directive()["expect_reply"] is False
    assert cva._is_opening_turn(None) is False
