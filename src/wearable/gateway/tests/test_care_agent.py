"""The rule that a guided routine cannot survive breaking.

A model that writes the praise first and the observation second will confirm a
movement that never happened. On the RPi that produced a seven-turn neck session
in which six asanas were confirmed while the person sat motionless. The prompt
asks for the observation before the verdict; these tests are the half that does
not depend on the model cooperating.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kiki_gateway.care import agent  # noqa: E402


def turn(observation: str, verdict: str = "") -> dict:
    tagged = (f"{observation} [instruction_followed: {verdict}]"
              if verdict else observation)
    return {"user": "", "assistant": "Raise your arm.", "motion_observation": tagged}


def session(*turns, physical: bool = True) -> dict:
    return {
        "id": "s1",
        "status": "active",
        "motion_tracking": True,
        "event": {"category": "exercise" if physical else "wellbeing"},
        "transcript": list(turns),
    }


def final(**fields) -> dict:
    base = {"status": "completed", "motion_observation": "", "band_worn": "yes",
            "instruction_followed": "yes", "reply_reason": "none",
            "hold_seconds": 5, "cue": "", "summary": "Good, now the other arm.",
            "session": "continue"}
    base.update(fields)
    return base


# ------------------------------------------------------- missing evidence ---

def test_a_routine_that_expected_motion_and_got_none_hands_the_turn_back():
    corrected, spoken = agent._enforce_motion_reply_contract(
        final(), "Beautifully held, now the other side.",
        motion_observation="", has_motion_evidence=False,
        motion_expected=True, session=session())

    assert corrected["reply_reason"] == "choice"
    assert corrected["hold_seconds"] == 0, "do not time another hold blind"
    assert "not feeling any movement" in spoken
    assert "Beautifully held" not in spoken, "praise from no evidence is discarded"


def test_the_opening_turn_is_allowed_to_have_no_evidence_yet():
    """Nothing has been instructed, so there is no movement to have missed."""
    corrected, spoken = agent._enforce_motion_reply_contract(
        final(instruction_followed="not_applicable"),
        "Shall we start with your right arm?",
        motion_observation="", has_motion_evidence=False,
        motion_expected=False, session=session())
    assert spoken == "Shall we start with your right arm?"
    assert corrected["reply_reason"] == "none"


def test_a_band_that_is_not_worn_stops_the_routine():
    corrected, spoken = agent._enforce_motion_reply_contract(
        final(band_worn="no"), "Great, keep going.",
        motion_observation="no movement at all", has_motion_evidence=True,
        motion_expected=True, session=session())
    assert corrected["reply_reason"] == "aborted"
    assert corrected["hold_seconds"] == 0
    assert "not on your wrist" in spoken


# ------------------------------------------------------------- mismatches ---

def test_the_first_mismatch_is_corrected_out_loud_and_repeated_muted():
    """One wrong movement is something to fix mid-routine, not to interview about."""
    corrected, spoken = agent._enforce_motion_reply_contract(
        final(instruction_followed="no", hold_seconds=0),
        "Lovely, that's the one.",
        motion_observation="the wrist was still for the whole window",
        has_motion_evidence=True, motion_expected=True, session=session())

    assert corrected["reply_reason"] == "none", "the microphone stays muted"
    assert corrected["hold_seconds"] > 0, "the same movement runs again"
    assert corrected["cue"] == "wrong"
    assert "Lovely" not in spoken, "praise written before the verdict is discarded"
    assert "again" in spoken.lower()


def test_the_second_mismatch_in_a_row_stops_and_asks():
    corrected, spoken = agent._enforce_motion_reply_contract(
        final(instruction_followed="no"), "Nice work.",
        motion_observation="still again", has_motion_evidence=True,
        motion_expected=True,
        session=session(turn("the wrist was still", "no")))

    assert corrected["reply_reason"] == "incorrect_form"
    assert corrected["hold_seconds"] == 0
    assert spoken.endswith("?"), "stopping to wait REQUIRES asking something"


@pytest.mark.parametrize("verdict", ["no", "unclear", "partly", "incorrect"])
def test_anything_that_is_not_yes_blocks_the_advance(verdict):
    corrected, _spoken = agent._enforce_motion_reply_contract(
        final(instruction_followed=verdict), "On to the next one.",
        motion_observation="", has_motion_evidence=True,
        motion_expected=True, session=session())
    assert corrected["cue"] == "wrong" or corrected["reply_reason"] != "none"


def test_an_observation_that_admits_a_non_attempt_counts_as_a_mismatch():
    """Even when the structured verdict says yes, the words can give it away."""
    corrected, _spoken = agent._enforce_motion_reply_contract(
        final(instruction_followed="yes"), "Held perfectly.",
        motion_observation="the wrist was still; they did not move at all",
        has_motion_evidence=True, motion_expected=True, session=session())
    assert corrected["cue"] == "wrong"


def test_a_clean_movement_is_left_completely_alone():
    original = final(instruction_followed="yes")
    corrected, spoken = agent._enforce_motion_reply_contract(
        dict(original), "Good, now the other arm.",
        motion_observation="six movements counted, 240 degrees swept",
        has_motion_evidence=True, motion_expected=True, session=session())
    assert corrected == original
    assert spoken == "Good, now the other arm."


def test_a_session_that_is_ending_is_never_rewritten():
    """A closing line must not be replaced by a retry request."""
    corrected, spoken = agent._enforce_motion_reply_contract(
        final(session="complete", instruction_followed="no"),
        "That's everything for today, well done.",
        motion_observation="still", has_motion_evidence=False,
        motion_expected=True, session=session())
    assert spoken == "That's everything for today, well done."
    assert corrected["session"] == "complete"


def test_pain_stops_the_routine_the_first_time_it_is_raised():
    original = final(reply_reason="safety", summary="Are you feeling dizzy?")
    corrected, spoken = agent._enforce_motion_reply_contract(
        dict(original), "Are you feeling dizzy?",
        motion_observation="", has_motion_evidence=True,
        motion_expected=True, session=session())
    assert corrected["reply_reason"] == "safety"
    assert spoken == "Are you feeling dizzy?"


# --------------------------------------------------------- the streak read ---

def test_the_violation_streak_is_read_back_out_of_the_transcript():
    """It survives a restart because it lives in the session, not in memory."""
    assert agent._violation_streak(session()) == 0
    assert agent._violation_streak(session(turn("still", "no"))) == 1
    assert agent._violation_streak(
        session(turn("still", "no"), turn("still", "no"))) == 2
    assert agent._violation_streak(
        session(turn("moved", "yes"), turn("still", "no"))) == 1, (
        "a good turn ends the streak")


# ------------------------------------------------------------- directives ---

def test_an_opening_question_is_never_asked_into_a_muted_microphone():
    """"Are you ready?" is a real question to a real person, before any movement.

    Muting there is never right, whatever `reply_reason` says -- and it is the
    mirror of the guard below, which refuses to WAIT when nothing was asked.
    Mid-routine is deliberately the other way round: the person is moving, and
    stopping to ask permission between movements is what made an earlier
    version stall.
    """
    agent._set_last_directive(
        final(reply_reason="none", summary="Are you ready?"), ok=True,
        spoken="Are you ready?", session=session())  # empty transcript = opening
    assert agent.get_last_care_directive()["expect_reply"] is True


def test_mid_routine_kiki_keeps_leading_rather_than_asking_permission():
    agent._set_last_directive(
        final(reply_reason="none", summary="Good, now the other arm."), ok=True,
        spoken="Good, now the other arm.",
        session=session(turn("moved", "yes")))
    directive = agent.get_last_care_directive()
    assert directive["expect_reply"] is False
    assert directive["hold_seconds"] > 0


def test_falling_silent_after_a_statement_is_refused():
    """`reply_reason` only counts when something was actually asked."""
    agent._set_last_directive(
        final(reply_reason="choice", summary="That was the last one."), ok=True,
        spoken="That was the last one.", session=session(turn("moved", "yes")))
    directive = agent.get_last_care_directive()
    assert directive["reply_reason"] == "none"


def test_a_failed_turn_never_leaves_the_routine_driving_itself():
    agent._set_last_directive(None, ok=False)
    directive = agent.get_last_care_directive()
    assert directive["expect_reply"] is True
    assert directive["hold_seconds"] == 0


def test_a_repeated_line_opens_the_microphone_instead_of_saying_it_again():
    """Nineteen identical turns, five seconds apart, is a real observed number."""
    said = "Vaibhav, are you ready?"
    repeated = session(*[{"assistant": said, "motion_observation": ""}] * 2)
    agent._set_last_directive(final(reply_reason="none"), ok=True, spoken=said,
                              session=repeated, repeat_depth=3)
    assert agent.get_last_care_directive()["expect_reply"] is True


def test_a_hold_longer_than_the_ceiling_is_a_model_mistake_not_an_instruction():
    from kiki_gateway.care.cadence import clamp_hold_seconds

    assert clamp_hold_seconds(500) == 120
    assert clamp_hold_seconds(-3) == 0
    assert clamp_hold_seconds("eight") == 0
