"""The three ways one spoken turn dropped a tool call, 2026-07-29.

Traced from a single session (logs/kiki.log, 00:05-00:12) where Kiki failed to
act three separate times:

  00:07:41  volume sixty              -> adjust_volume({"amount": "60"})
                                         rejected by the schema gate; the
                                         handler's own int() would have taken it
  00:07:44  (same turn, follow-up)    -> adjust_volume({"amount": 60}) — the
                                         model FIXING itself, parsed, logged and
                                         thrown away because the follow-up
                                         stream handled only sentence/done
  00:10:36  play the last song        -> no tool call at all; Kiki said "Playing
  00:11:10  play it play it              Maafi again", copying the prose answer
                                         the previous tool turn had produced

Each class gets a test here so none of them can come back silently.
"""

import json

import pytest

import core.runtime_controls as runtime_controls
from main import MAX_FOLLOWUP_TOOL_ROUNDS, tool_result_note
from tools_and_config.tools import validate_tool_arguments


# --------------------------------------------------------------------------
# 1. The schema gate coerces what the handler would have coerced anyway
# --------------------------------------------------------------------------

def test_the_live_volume_failure_now_passes_the_gate():
    """The exact arguments from 00:07:41."""
    args = {"action": "set", "amount": "60"}
    valid, reason = validate_tool_arguments("adjust_volume", args)
    assert valid, reason
    # Coerced IN PLACE: every call site dispatches with this same dict.
    assert args == {"action": "set", "amount": 60}
    assert isinstance(args["amount"], int)


@pytest.mark.parametrize("name,args,expected", [
    ("adjust_volume", {"action": "set", "amount": "60"}, {"action": "set", "amount": 60}),
    ("adjust_volume", {"action": "set", "amount": "60.0"}, {"action": "set", "amount": 60}),
    ("adjust_volume", {"action": "increase", "amount": " 5 "}, {"action": "increase", "amount": 5}),
    ("set_followups", {"enabled": "true"}, {"enabled": True}),
    ("set_followups", {"enabled": "no"}, {"enabled": False}),
    ("set_followups", {"enabled": 1}, {"enabled": True}),
])
def test_unambiguous_scalars_are_coerced(name, args, expected):
    valid, reason = validate_tool_arguments(name, args)
    assert valid, reason
    assert args == expected


@pytest.mark.parametrize("value", ["sixty", "60%", "", "6 0", "1e400x"])
def test_a_value_that_is_not_a_number_is_still_refused(value):
    """Coercion is lossless-only. Guessing at "60%" would be worse than the
    error, which is what tells the model to send an integer next time."""
    valid, reason = validate_tool_arguments(
        "adjust_volume", {"action": "set", "amount": value})
    assert not valid
    assert "integer" in reason


def test_enums_are_never_coerced():
    """The other rejection in that day's log was update_knowledge with
    category="user_preference". That is a genuinely wrong value, and the error
    listing the real categories is what corrects it — do not paper over it."""
    valid, reason = validate_tool_arguments("update_knowledge", {
        "category": "user_preference", "action": "set", "key": "k", "value": "v"})
    assert not valid
    assert "must be one of" in reason


def test_every_tool_call_in_the_incident_log_would_now_run():
    """Replay of the 70 <tool_call>s in logs/kiki.log at the time of the fix:
    two were rejected, and only the wrong-enum one should stay rejected."""
    for raw, should_pass in [
        ('adjust_volume {"action": "set", "amount": "60"}', True),
        ('play_music {"song": "Kabhi Kisi Se Maafi Maang Lo Shreya Ghoshal"}', True),
        ('play_last_song {}', True),
        ('search_web {"query": "latest world news today"}', True),
        ('update_knowledge {"category": "user_preference", "action": "set", '
         '"key": "language_for_nana", "value": "Hindi (Devanagari)"}', False),
    ]:
        name, args = raw.split(" ", 1)
        valid, _ = validate_tool_arguments(name, json.loads(args))
        assert valid is should_pass, name


# --------------------------------------------------------------------------
# 2. A follow-up generation's tool call is honoured, but bounded
# --------------------------------------------------------------------------

def test_a_follow_up_tool_round_is_allowed_but_bounded():
    """0 would restore the drop-it bug; anything large lets a looping model
    hold the turn open, and an open turn keeps the wake word closed."""
    assert MAX_FOLLOWUP_TOOL_ROUNDS >= 1
    assert MAX_FOLLOWUP_TOOL_ROUNDS <= 3


def test_the_tools_instruction_shows_an_unquoted_number():
    """The root cause of the quoted "60": every value the model had ever been
    shown in this instruction was a quoted string."""
    from core.llm import _get_tools_instruction
    instruction = _get_tools_instruction()
    assert '"count": 5' in instruction
    assert "amount:int" in instruction, "non-string params must carry their type"
    assert "amount?:int" not in instruction, "optional marker is not a JSON key"


# --------------------------------------------------------------------------
# 3. The tool-result note stops teaching Kiki to answer without calling
# --------------------------------------------------------------------------

@pytest.fixture
def mode(monkeypatch):
    """Force an active mode without touching the real config."""
    def _set(name, prompt):
        monkeypatch.setattr(runtime_controls, "_current_mode", name, raising=False)
        monkeypatch.setattr(runtime_controls, "_modes",
                            lambda: {name: {"system_prompt": prompt}})
    return _set


def test_default_mode_gets_the_directive_note_back(mode):
    """704acd8 softened this note into "answer in YOUR OWN VOICE, fully in
    character" to protect a 253-char roleplay prompt. Kiki's own 7.9k prompt
    never needed it, and the prose form is what she copied on the repeat
    requests at 00:10 and 00:11."""
    mode("default", None)
    note = tool_result_note([{"name": "play_last_song"}], "Now playing Maafi again.")
    assert "directly and briefly" in note
    assert "YOUR OWN VOICE" not in note


def test_a_character_mode_keeps_the_in_character_note(mode):
    mode("rohan", "You are Rohan, a first-year student.")
    note = tool_result_note([{"name": "search_web"}], "RESULT")
    assert "YOUR OWN VOICE" in note


@pytest.mark.parametrize("name,prompt", [("default", None),
                                         ("rohan", "You are Rohan.")])
def test_both_notes_forbid_reusing_the_answer_next_time(mode, name, prompt):
    """The note is stored in history, so it keeps influencing later turns.
    Both forms must say a repeat request needs its own call."""
    mode(name, prompt)
    note = tool_result_note([{"name": "play_last_song"}], "Now playing Maafi again.")
    assert "call the tool again" in note
    assert note.endswith("Now playing Maafi again.")


def test_complex_query_note_is_untouched(mode):
    """The action agent already wrote a finished spoken reply; this note must
    keep asking for it COMPLETELY regardless of mode."""
    for name, prompt in (("default", None), ("rohan", "You are Rohan.")):
        mode(name, prompt)
        note = tool_result_note([{"name": "complex_query"}], "OUTCOME")
        assert "COMPLETELY" in note
        assert "never claim it worked" in note
