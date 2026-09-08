"""A care session must sound like Kiki, not like a stranger who just walked in.

Observed live on 2026-09-01 23:50. Mid-conversation about the day's build --
"yeah i've spent the entire day building you and a mini version of you" --
the person said "I'm getting bored", which opened an engagement session. The
session replied about an 'idle thinking worker' topic from a previous evening,
then produced an unprompted fact about Nalanda University.

Nothing was broken. The care prompt simply contained the care plan and nothing
else: no persona, no emotion or neck tags, and no idea what had been said sixty
seconds earlier. It could not continue a conversation it had never seen.

`action_agent._background` already reads persona and history out-of-band from
core.llm, so none of it lands in the speaking model's KV prefix. This is the
same treatment for the care agent.
"""

import pytest

from core import llm
from core.senior import care_voice_agent as cva

CONVERSATION = [
    {"role": "system", "content": "persona row"},
    {"role": "user",
     "content": "yeah i've spent the entire day building you and a mini version of you"},
    {"role": "assistant", "content": "[surprise-oh] Wait, really? The entire day?"},
    {"role": "user", "content": "Interesting. Now can you tell me I'm getting bored."},
]


@pytest.fixture
def live_conversation(monkeypatch):
    monkeypatch.setattr(llm, "persona_brief",
                        lambda n=1000: "You are Kiki, a real companion living "
                                       "in a home in New Delhi. Dry humour.")
    llm._note_conversation(CONVERSATION)
    return CONVERSATION


def _conversation_session(session_id=""):
    return {"id": session_id,
            "event": {"category": "memory", "title": "Something to think about"},
            "transcript": [], "care_context": {"senior": {"language": "en"}}}


def _exercise_session(session_id=""):
    return {"id": session_id, "event": {"category": "exercise", "title": "Neck Exercise"},
            "transcript": [], "care_context": {}}


@pytest.fixture(autouse=True)
def clean_background_cache():
    cva._BACKGROUND_CACHE.update({"session_id": "", "text": ""})
    yield
    cva._BACKGROUND_CACHE.update({"session_id": "", "text": ""})


# --- the persona goes to every session ---

def test_the_care_prompt_says_who_kiki_is(live_conversation):
    prompt = cva._prompt(_conversation_session(), "", "no frame")
    assert "WHO YOU ARE" in prompt
    assert "Dry humour" in prompt
    assert "does not replace any of it" in prompt


def test_an_exercise_session_is_still_kiki(live_conversation):
    assert "WHO YOU ARE" in cva._prompt(_exercise_session(), "ready", "frame")


def test_the_voice_markup_is_carried_into_the_session(live_conversation):
    """A care reply goes STRAIGHT to TTS -- no speaking model re-voices it, so
    the tags have to be in this prompt or they never appear at all."""
    for session in (_conversation_session(), _exercise_session()):
        prompt = cva._prompt(session, "", "no frame")
        assert "[chuckle]" in prompt
        assert "<neck:left>" in prompt
        assert "Keep your humour" in prompt


# --- the conversation the session interrupted ---

def test_a_conversation_session_sees_what_was_just_being_said(live_conversation):
    """The whole failure, in one assertion."""
    prompt = cva._prompt(_conversation_session(), "", "no frame")
    assert "THE CONVERSATION THIS SESSION INTERRUPTED" in prompt
    assert "mini version of you" in prompt
    assert "Continue from HERE" in prompt


def test_an_exercise_session_is_not_given_the_chat(live_conversation):
    """A guided routine is judged against camera frames and the instruction
    just given. Five minutes of unrelated talk is dilution, and that prompt is
    long already."""
    prompt = cva._prompt(_exercise_session(), "ready", "frame attached")
    assert "THE CONVERSATION THIS SESSION INTERRUPTED" not in prompt
    assert "mini version of you" not in prompt


def test_links_seen_recently_come_along(monkeypatch, live_conversation):
    monkeypatch.setattr(llm, "conversation_artifacts",
                        lambda limit=12: ["https://printables.com/model/69344"])
    prompt = cva._prompt(_conversation_session(), "", "no frame")
    assert "printables.com/model/69344" in prompt


def test_music_already_playing_is_stated(monkeypatch, live_conversation):
    class Manager:
        @staticmethod
        def snapshot():
            return {"current": {"title": "Kesariya"}}

    import core.media_manager as media
    monkeypatch.setattr(media, "music_manager", Manager)
    assert "Kesariya" in cva._prompt(_conversation_session(), "", "no frame")


# --- budgets ---

def test_the_persona_budget_is_bigger_than_the_action_agents(monkeypatch):
    """The action agent's 1000 is right for IT: the speaking model re-voices
    its summary and still holds the full persona. Nothing re-voices a care
    reply."""
    assert cva._context_cfg("persona_chars") >= 3000


def test_a_bad_config_value_falls_back_instead_of_raising(monkeypatch):
    monkeypatch.setattr(cva, "_cfg", lambda: {"history_chars": "lots"})
    assert cva._context_cfg("history_chars") == 9000


# --- a missing source must never cost the session ---

def test_a_broken_persona_source_does_not_break_the_prompt(monkeypatch):
    def boom(n=1000):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(llm, "persona_brief", boom)
    prompt = cva._prompt(_conversation_session(), "", "no frame")
    assert "Return exactly one JSON object" in prompt


def test_a_broken_history_source_does_not_break_the_prompt(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(llm, "conversation_snapshot", boom)
    prompt = cva._prompt(_conversation_session(), "", "no frame")
    assert "Return exactly one JSON object" in prompt


def test_an_empty_conversation_adds_no_empty_heading(monkeypatch):
    monkeypatch.setattr(llm, "persona_brief", lambda n=1000: "")
    monkeypatch.setattr(llm, "conversation_snapshot", lambda *a, **k: "")
    monkeypatch.setattr(llm, "conversation_artifacts", lambda limit=12: [])
    prompt = cva._prompt(_conversation_session(), "", "no frame")
    assert "THE CONVERSATION THIS SESSION INTERRUPTED" not in prompt
    assert "WHO YOU ARE" not in prompt
    assert prompt.lstrip().startswith("You are Kiki")


# --- frozen for the life of the session ---

def test_the_block_is_built_once_per_session(monkeypatch, live_conversation):
    """It sits at the top of the prompt. Anything that changed here would
    invalidate the whole Cerebras prefix every turn -- the live run caches
    9,216 of 10,163 tokens."""
    builds = []

    def counted(max_chars=28000, per_record_chars=1200):
        builds.append(1)
        return "user: something"

    monkeypatch.setattr(llm, "conversation_snapshot", counted)
    session = _conversation_session("sess0001")
    for _ in range(5):
        cva._prompt(session, "ok", "no frame")
    assert builds == [1]


def test_the_frozen_block_is_byte_identical_across_turns(monkeypatch,
                                                         live_conversation):
    session = _conversation_session("sess0001")
    first = cva._prompt(session, "", "no frame")
    head = first[:first.index("You are Kiki, currently conducting")]

    # The live conversation moves on underneath -- the session must not notice.
    llm._note_conversation(CONVERSATION + [
        {"role": "assistant", "content": "a care reply that must not echo back"}])
    second = cva._prompt(session, "and then?", "no frame")

    assert second.startswith(head)
    assert "must not echo back" not in second


def test_a_new_session_gets_a_fresh_block(live_conversation):
    cva._prompt(_conversation_session("sess0001"), "", "no frame")
    llm._note_conversation(CONVERSATION + [
        {"role": "user", "content": "a brand new subject entirely"}])
    second = cva._prompt(_conversation_session("sess0002"), "", "no frame")
    assert "a brand new subject entirely" in second


def test_a_session_without_an_id_is_never_cached(live_conversation):
    """Unit tests and any legacy record build fresh rather than poisoning the
    one slot."""
    cva._prompt(_conversation_session(), "", "no frame")
    assert cva._BACKGROUND_CACHE["session_id"] == ""
