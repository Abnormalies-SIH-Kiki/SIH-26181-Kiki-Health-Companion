"""Guided exercise must lead, not interview.

The 19:07 neck routine on 2026-08-29 was a question-and-answer session: every
step waited for the person to speak, and the model tried to time a hold by
saying "five seconds... four, three, two, one" — which TTS read out in under
two seconds. These cover the two halves of the fix: a hold that takes real
time, and a routine that carries on when nobody answers.
"""

import wave

import pytest

from core.senior import exercise_cadence
from core.senior import care_voice_agent


# --------------------------------------------------------------------------
# The hold takes the time it says it does
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seconds", [1, 5, 10, 30])
def test_a_hold_track_lasts_its_full_duration(seconds):
    path = exercise_cadence.countdown_track(seconds)
    with wave.open(path) as w:
        duration = w.getnframes() / w.getframerate()

    # The track IS the hold: playing it to completion times the movement.
    # It runs one final "done" tone past the last second.
    assert seconds <= duration <= seconds + 0.5


def test_a_hold_track_has_one_tick_per_second():
    """Silence between ticks is what makes the seconds countable."""
    path = exercise_cadence.countdown_track(5)
    with wave.open(path) as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())

    import struct
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)

    # Per-10ms block energy, not per-sample: a sine crosses zero every cycle,
    # so sample-level thresholding counts oscillations rather than beeps.
    block = rate // 100
    blocks = [
        max(abs(s) for s in samples[i:i + block])
        for i in range(0, len(samples) - block, block)
    ]
    loud = [b > 1000 for b in blocks]
    runs = sum(1 for i in range(1, len(loud)) if loud[i] and not loud[i - 1])
    runs += 1 if loud[0] else 0
    assert runs == 6, f"expected 5 ticks + 1 finish tone, got {runs}"

    # And each tick sits on its second boundary. Measured across the middle of
    # the tick, clear of the 5 ms anti-click fades at either edge.
    for second in range(5):
        start = int(second * rate) + int(rate * 0.02)
        peak = max(abs(s) for s in samples[start:start + int(rate * 0.02)])
        assert peak > 1000, f"no tick at second {second} (peak {peak})"

    # The half-second mark, by contrast, must be quiet.
    for second in range(5):
        mid = int((second + 0.5) * rate)
        quiet = max(abs(s) for s in samples[mid:mid + int(rate * 0.02)])
        assert quiet < 100, f"tick bleeding into the gap after second {second}"

    # Most of the track is silence — that gap is what makes seconds countable.
    assert sum(loud) < len(loud) * 0.25


def test_no_track_for_a_zero_or_negative_hold():
    assert exercise_cadence.countdown_track(0) is None
    assert exercise_cadence.countdown_track(-4) is None


def test_an_absurd_hold_is_clamped():
    """A stray digit must not pin someone in a stretch for an hour."""
    assert exercise_cadence.clamp_hold_seconds(99999) == exercise_cadence.MAX_HOLD_SECONDS
    assert exercise_cadence.clamp_hold_seconds(10, maximum=5) == 5


@pytest.mark.parametrize("value,expected", [
    (5, 5), ("7", 7), (5.9, 5), (None, 0), ("", 0), ("soon", 0), (-3, 0), (0, 0),
])
def test_hold_values_from_the_model_are_coerced_safely(value, expected):
    assert exercise_cadence.clamp_hold_seconds(value) == expected


def test_tracks_are_cached_rather_than_re_rendered():
    """A routine repeats the same holds; re-synthesising each time is waste."""
    first = exercise_cadence.countdown_track(7)
    second = exercise_cadence.countdown_track(7)
    assert first == second


# --------------------------------------------------------------------------
# The directive that drives the routine forward
# --------------------------------------------------------------------------

def test_a_timed_step_reports_its_hold_and_does_not_wait():
    care_voice_agent._set_last_directive(
        {"summary": "Tilt your head right and hold it there.",
         "hold_seconds": 10, "expect_reply": False}, ok=True)

    directive = care_voice_agent.get_last_care_directive()
    assert directive["hold_seconds"] == 10
    assert directive["expect_reply"] is False


def test_a_real_question_still_waits_for_an_answer():
    """Pain and stopping are worth interrupting for; the routine must pause."""
    care_voice_agent._set_last_directive(
        {"hold_seconds": 0, "reply_reason": "safety"}, ok=True,
        spoken="Are you feeling any sharp pain?")

    directive = care_voice_agent.get_last_care_directive()
    assert directive["hold_seconds"] == 0
    assert directive["expect_reply"] is True


def test_a_response_naming_no_reason_keeps_leading():
    """The default deliberately flipped.

    It used to be "absent guidance, listen", which is right for a conversation
    and wrong for a routine: it made every step wait for "okay". Leading is now
    the default, and stopping has to be justified by a named reply_reason.
    """
    care_voice_agent._set_last_directive({"summary": "Good work."}, ok=True,
                                         spoken="Good work.")

    assert care_voice_agent.get_last_care_directive()["expect_reply"] is False


def test_a_failed_turn_never_leaves_the_routine_self_driving():
    """A broken turn must fall back to listening, not beep and barrel on."""
    care_voice_agent._set_last_directive(
        {"hold_seconds": 30, "expect_reply": False}, ok=False)

    directive = care_voice_agent.get_last_care_directive()
    assert directive["hold_seconds"] == 0
    assert directive["expect_reply"] is True


def test_the_hold_is_clamped_by_config(monkeypatch):
    monkeypatch.setattr(care_voice_agent, "_exercise_cfg",
                        lambda: {"max_hold_seconds": 15})
    care_voice_agent._set_last_directive(
        {"hold_seconds": 600, "expect_reply": False}, ok=True)

    assert care_voice_agent.get_last_care_directive()["hold_seconds"] == 15


def test_the_prompt_forbids_counting_aloud_and_documents_the_fields():
    """The contract the model actually reads is part of the behaviour.

    The event must declare `category: "exercise"`. These instructions are now
    served only to physical routines -- handing "default to NOT listening" to a
    conversation is what made an engagement session mute the microphone after
    asking a question and then repeat itself. An event with no category is
    treated as a conversation, which is the safer default: listening when you
    could have continued costs a pause, muting when you should have listened
    costs the whole session.
    """
    session = {"care_context": {}, "transcript": [],
               "event": {"category": "exercise"},
               "continuous_vision": True}
    prompt = care_voice_agent._prompt(session, "okay", "a frame is attached")

    assert "hold_seconds" in prompt
    assert "reply_reason" in prompt
    assert "NEVER count out loud" in prompt
    # Every stopping reason the runtime honours must be described to the model.
    for reason in care_voice_agent._REPLY_REASONS:
        assert reason in prompt, f"prompt never explains reply_reason={reason}"
    # And the question requirement, which is what makes a pause answerable.
    assert "MUST end with the actual" in prompt
    # Silence during exercise must read as normal, not as a problem to probe.
    assert "NO REPLY - CONTINUE THE ROUTINE YOURSELF" in prompt
    assert "microphone STAYS MUTED" in prompt
    assert "did not attempt" in prompt
    assert "does NOT move on to the next movement" in prompt
    # The two-strike policy has to be described to the model, not only enforced
    # underneath it, or its own reply_reason fights the runtime every turn.
    assert "SECOND mismatch in a row" in prompt
    assert "person_in_frame" in prompt



# --------------------------------------------------------------------------
# Listening is the exception, and it must be earned
# --------------------------------------------------------------------------

@pytest.mark.parametrize("reason", ["aborted", "incorrect_form", "safety", "choice"])
def test_a_named_reason_with_a_question_stops_the_routine(reason):
    care_voice_agent._set_last_directive(
        {"reply_reason": reason, "hold_seconds": 0}, ok=True,
        spoken="Vaibhav, are you feeling any pain?")

    d = care_voice_agent.get_last_care_directive()
    assert d["expect_reply"] is True
    assert d["reply_reason"] == reason


def test_doing_the_exercise_quietly_does_not_stop_the_routine():
    """Silence while exercising is the normal case, not a problem."""
    care_voice_agent._set_last_directive(
        {"reply_reason": "none", "hold_seconds": 10}, ok=True,
        spoken="Good. Now tilt slowly to the left and hold it there.")

    d = care_voice_agent.get_last_care_directive()
    assert d["expect_reply"] is False
    assert d["hold_seconds"] == 10


def test_correct_form_continues_with_the_microphone_muted():
    from main import should_continue_care_without_listening

    assert should_continue_care_without_listening(
        True, {"expect_reply": False, "reply_reason": "none"}, True, True)


@pytest.mark.parametrize("reason", [
    "aborted", "incorrect_form", "safety", "choice",
])
def test_a_justified_problem_opens_listening_instead_of_auto_continuing(reason):
    from main import should_continue_care_without_listening

    assert not should_continue_care_without_listening(
        True, {"expect_reply": True, "reply_reason": reason}, True, True)


def test_a_finished_session_cannot_queue_another_silent_care_turn():
    from main import should_continue_care_without_listening

    assert not should_continue_care_without_listening(
        True, {"expect_reply": False, "reply_reason": "none"}, True, False)


def test_a_visually_admitted_non_attempt_cannot_be_praised_or_advanced():
    """First mismatch: the praise goes, the next asana waits — but so does the
    microphone. The correction is spoken and the same movement runs again."""
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        {"reply_reason": "none", "hold_seconds": 5},
        "बहुत अच्छे। अब अगले आसन पर चलते हैं।",
        "Vaibhav remained in Pranamasana, not yet attempting the instructed stretch.")

    assert final["reply_reason"] == "none"
    assert final["hold_seconds"] == 5
    assert "अगले आसन" not in spoken
    assert "?" not in spoken


def test_a_correct_pose_is_not_overridden_by_the_consistency_guard():
    original = {"reply_reason": "none", "hold_seconds": 5}
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        original, "Good. Now the next pose.",
        "Vaibhav reached the instructed pose and held it steadily, not showing pain.")

    assert final is original
    assert spoken == "Good. Now the next pose."


@pytest.mark.parametrize("bogus", ["", "maybe", "yes", None, "NONE", 5])
def test_an_unrecognised_reason_keeps_the_routine_moving(bogus):
    """A model drifting back to chat habits must not be able to stall the session."""
    care_voice_agent._set_last_directive(
        {"reply_reason": bogus}, ok=True, spoken="Now the other side?")

    assert care_voice_agent.get_last_care_directive()["expect_reply"] is False


def test_waiting_without_asking_anything_is_refused():
    """Falling silent after a statement leaves the person nothing to answer."""
    care_voice_agent._set_last_directive(
        {"reply_reason": "incorrect_form"}, ok=True,
        spoken="Your shoulders are a little high.")

    d = care_voice_agent.get_last_care_directive()
    assert d["expect_reply"] is False
    assert d["reply_reason"] == "none"


@pytest.mark.parametrize("reason", ["safety", "aborted"])
def test_no_reason_can_open_listening_without_a_question(reason):
    care_voice_agent._set_last_directive(
        {"reply_reason": reason}, ok=True,
        spoken="You look unsteady. Sit down.")

    assert care_voice_agent.get_last_care_directive()["expect_reply"] is False


@pytest.mark.parametrize("reason", [
    "incorrect_form", "aborted", "safety", "choice",
])
def test_runtime_adds_the_correct_question_before_listening(reason):
    spoken = care_voice_agent._ensure_reply_question(
        {"reply_reason": reason}, "Please pause there.")

    assert spoken.endswith("?")
    care_voice_agent._set_last_directive(
        {"reply_reason": reason}, ok=True, spoken=spoken)
    assert care_voice_agent.get_last_care_directive()["expect_reply"] is True


# --------------------------------------------------------------------------
# Frames from during the hold
# --------------------------------------------------------------------------

def test_frames_captured_during_a_hold_are_handed_to_the_next_turn():
    import asyncio
    exercise_cadence.clear_hold_frames()
    exercise_cadence._LAST_HOLD_FRAMES = ["frameA", "frameB", "frameC"]
    exercise_cadence._LAST_HOLD_AT = __import__("time").monotonic()

    images, status = asyncio.run(
        care_voice_agent._fresh_visual_frame({"continuous_vision": True}))

    assert images == ["frameA", "frameB", "frameC"]
    assert "hold that has just finished" in status
    # The wording must ask for a description, never supply a verdict the model
    # can hand straight back ("held the position steadily across both frames").
    assert "describe the body position you can actually see" in status
    assert "held steady" not in status
    # Consumed exactly once, so a later turn cannot re-judge an old movement.
    assert exercise_cadence.take_hold_frames() == []


def test_the_same_frames_twice_running_are_treated_as_a_frozen_camera():
    """A live sensor never returns the identical JPEG on two turns."""
    import asyncio
    care_voice_agent._LAST_IMAGE_STATE.update({"session_id": "", "digests": []})
    session = {"id": "s1", "continuous_vision": True}

    exercise_cadence._LAST_HOLD_FRAMES = ["identical"]
    exercise_cadence._LAST_HOLD_AT = __import__("time").monotonic()
    first, _ = asyncio.run(care_voice_agent._fresh_visual_frame(session))
    assert first == ["identical"]

    exercise_cadence._LAST_HOLD_FRAMES = ["identical"]
    exercise_cadence._LAST_HOLD_AT = __import__("time").monotonic()
    second, status = asyncio.run(care_voice_agent._fresh_visual_frame(session))

    assert second is None
    assert "frozen" in status
    assert "cannot see" in status


def test_a_new_session_is_not_judged_against_the_previous_one_frames():
    import asyncio
    care_voice_agent._LAST_IMAGE_STATE.update(
        {"session_id": "old", "digests": []})
    exercise_cadence._LAST_HOLD_FRAMES = ["shot"]
    exercise_cadence._LAST_HOLD_AT = __import__("time").monotonic()
    asyncio.run(care_voice_agent._fresh_visual_frame(
        {"id": "old", "continuous_vision": True}))

    exercise_cadence._LAST_HOLD_FRAMES = ["shot"]
    exercise_cadence._LAST_HOLD_AT = __import__("time").monotonic()
    images, _ = asyncio.run(care_voice_agent._fresh_visual_frame(
        {"id": "new", "continuous_vision": True}))

    assert images == ["shot"]


# --------------------------------------------------------------------------
# The verdict has to come from the pixels, and it has to be believed
# --------------------------------------------------------------------------

def _session_with_violations(count):
    """A transcript whose last `count` turns were recorded as mismatches."""
    return {"event": {"category": "exercise"},
            "transcript": [{"assistant": f"turn {i}",
                            "visual_observation":
                                f"He is facing forward. [instruction_followed: no]"}
                           for i in range(count)]}


@pytest.mark.parametrize("verdict", ["no", "unclear", "partly", "not_attempted"])
def test_a_first_negative_verdict_corrects_out_loud_and_repeats(verdict):
    """The 2026-09-02 neck session confirmed six asanas nobody performed.

    One wrong tilt is fixed mid-routine, not turned into an interview: the
    praise and the next asana both go, the correction is SPOKEN, a fresh hold
    is timed, and the microphone stays shut.
    """
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        {"reply_reason": "none", "hold_seconds": 5,
         "instruction_followed": verdict},
        "Perfect. Now tilt to the other side and hold it there.",
        "Vaibhav is sitting upright, facing the camera.",
        session=_session_with_violations(0))

    assert final["reply_reason"] == "none"
    assert final["hold_seconds"] == 5
    assert "other side" not in spoken
    assert "?" not in spoken
    assert spoken.strip()

    care_voice_agent._set_last_directive(
        final, ok=True, spoken=spoken, session=_session_with_violations(0))
    assert care_voice_agent.get_last_care_directive()["expect_reply"] is False


@pytest.mark.parametrize("verdict", ["no", "unclear"])
def test_a_second_consecutive_mismatch_stops_and_asks(verdict):
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        {"reply_reason": "none", "hold_seconds": 5,
         "instruction_followed": verdict},
        "Lovely. Now the other side.",
        "Vaibhav is sitting upright, facing the camera.",
        session=_session_with_violations(1))

    assert final["reply_reason"] == "incorrect_form"
    assert final["hold_seconds"] == 0
    assert spoken.endswith("?")


def test_a_correct_turn_resets_the_mismatch_streak():
    session = {"event": {"category": "exercise"}, "transcript": [
        {"visual_observation": "chin down. [instruction_followed: no]"},
        {"visual_observation": "ear to shoulder. [instruction_followed: yes]"},
    ]}
    assert care_voice_agent._violation_streak(session) == 0

    final, _spoken = care_voice_agent._enforce_visual_reply_contract(
        {"reply_reason": "none", "instruction_followed": "no"},
        "Great, next one.", "", session=session)
    assert final["reply_reason"] == "none"


def test_a_retry_hold_is_supplied_when_the_model_gave_none(monkeypatch):
    """Repeating a movement with hold_seconds=0 captures no frames to judge."""
    monkeypatch.setattr(care_voice_agent, "_exercise_cfg",
                        lambda: {"retry_hold_seconds": 9})
    final, _spoken = care_voice_agent._enforce_visual_reply_contract(
        {"reply_reason": "none", "hold_seconds": 0, "instruction_followed": "no"},
        "On to the next one.", "", session=_session_with_violations(0))

    assert final["hold_seconds"] == 9


def test_an_empty_frame_stops_the_routine_on_the_first_turn():
    """Nobody to correct, so there is nothing to gain from another hold."""
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        {"reply_reason": "none", "hold_seconds": 10,
         "person_in_frame": "no", "instruction_followed": "no"},
        "Beautiful, hold that.",
        "The chair is empty; nobody is visible in either frame.",
        session=_session_with_violations(0))

    assert final["reply_reason"] == "aborted"
    assert final["hold_seconds"] == 0
    assert spoken.endswith("?")


def test_the_models_own_form_complaint_also_gets_one_free_correction():
    """Its wording is kept — it is more specific than the generic line."""
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        {"reply_reason": "incorrect_form", "hold_seconds": 6,
         "instruction_followed": "no"},
        "Your shoulders crept up towards your ears there.",
        "Both shoulders are raised.", session=_session_with_violations(0))

    assert final["reply_reason"] == "none"
    assert final["hold_seconds"] == 6
    assert "shoulders crept up" in spoken
    assert "?" not in spoken


def test_pain_and_aborts_still_stop_on_the_very_first_turn():
    for reason in ("safety", "aborted", "choice"):
        original = {"reply_reason": reason, "instruction_followed": "no"}
        final, spoken = care_voice_agent._enforce_visual_reply_contract(
            original, "Are you feeling dizzy?", "He is holding the table.",
            session=_session_with_violations(0))
        assert final is original
        assert spoken == "Are you feeling dizzy?"


@pytest.mark.parametrize("verdict", ["yes", "not_applicable", "", None])
def test_a_positive_or_absent_verdict_keeps_leading(verdict):
    original = {"reply_reason": "none", "hold_seconds": 5,
                "instruction_followed": verdict}
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        original, "Good. Now the next pose.",
        "His chin is down against his chest in both frames.")

    assert final is original
    assert spoken == "Good. Now the next pose."


def test_a_vision_routine_that_went_blind_refuses_to_advance():
    """Losing the camera is not a reason to keep steering by it."""
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        {"reply_reason": "none", "hold_seconds": 8,
         "instruction_followed": "yes"},
        "Lovely, you held that beautifully. Now turn to the right.",
        "Visual feedback unavailable: the camera returned no fresh frame.",
        has_visual_evidence=False, visual_expected=True)

    assert final["reply_reason"] == "choice"
    assert final["hold_seconds"] == 0
    assert "beautifully" not in spoken
    assert spoken.endswith("?")

    care_voice_agent._set_last_directive(final, ok=True, spoken=spoken,
                                         session={"event": {"category": "exercise"},
                                                  "transcript": [{"assistant": "x"}]})
    assert care_voice_agent.get_last_care_directive()["expect_reply"] is True


def test_a_closing_turn_is_never_rewritten_by_either_guard():
    """Both guards stop the routine ADVANCING; an ending is not advancing."""
    for extra in ({"instruction_followed": "no"}, {}):
        original = {"reply_reason": "none", "session": "complete", **extra}
        final, spoken = care_voice_agent._enforce_visual_reply_contract(
            original, "That is the whole routine done. Rest your shoulders.",
            "Visual feedback unavailable: the camera returned no fresh frame.",
            has_visual_evidence=False, visual_expected=True)

        assert final is original
        assert spoken == "That is the whole routine done. Rest your shoulders."


def test_a_conversation_session_is_unaffected_by_the_blind_guard():
    original = {"reply_reason": "none"}
    final, spoken = care_voice_agent._enforce_visual_reply_contract(
        original, "That sounds lovely. What happened next?", "",
        has_visual_evidence=False, visual_expected=False)

    assert final is original
    assert spoken == "That sounds lovely. What happened next?"


def test_the_prompt_asks_for_the_observation_before_the_spoken_words():
    """Ordering IS the fix: a verdict written after the praise agrees with it."""
    session = {"care_context": {}, "transcript": [],
               "event": {"category": "exercise"},
               "continuous_vision": True}
    prompt = care_voice_agent._prompt(session, "", "two photographs are attached")

    assert prompt.index('"visual_observation"') < prompt.index('"summary"')
    assert prompt.index('"instruction_followed"') < prompt.index('"summary"')
    assert "Look before you speak" in prompt
    assert "Not moving is" in prompt


def test_stale_hold_frames_are_discarded():
    exercise_cadence._LAST_HOLD_FRAMES = ["old"]
    exercise_cadence._LAST_HOLD_AT = __import__("time").monotonic() - 600

    assert exercise_cadence.take_hold_frames() == []


def test_several_hold_frames_reach_cerebras_in_order():
    from core.brain.fast_cloud import _cerebras_user_content
    content = _cerebras_user_content("judge the hold", ["A", "B", "C"])

    assert content[0]["type"] == "text"
    urls = [p["image_url"]["url"] for p in content[1:]]
    assert urls == ["data:image/jpeg;base64,A",
                    "data:image/jpeg;base64,B",
                    "data:image/jpeg;base64,C"]


def test_a_single_image_still_works_unchanged():
    from core.brain.fast_cloud import _cerebras_user_content
    content = _cerebras_user_content("look", "OnlyOne")

    assert len(content) == 2
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,OnlyOne"
