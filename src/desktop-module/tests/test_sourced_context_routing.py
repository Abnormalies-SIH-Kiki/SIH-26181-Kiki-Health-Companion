"""A background finding must survive the hand-off into Kiki's next turn.

The live failure on 2026-08-31 was not a WhatsApp-reader failure.  Unified Idle
Mind had already read the messages and selected the useful ARES update, but the
speaking layer discarded its WhatsApp provenance, split the user's intent over
three turns, and then let the generic memory-tool instruction win.  These tests
pin the contract at each boundary so another persona/mode cannot regress it.
"""

import time


def _idle_manager(tmp_path, monkeypatch):
    from core.brain import unified_idle_mind as uim
    from tools_and_config.config_loader import get_full_config

    monkeypatch.setattr(uim, "_state_path", lambda: tmp_path / "idle-state.json")
    cfg = get_full_config()
    cfg = dict(cfg)
    cfg["idle_mind"] = dict(cfg.get("idle_mind", {}))
    cfg["idle_mind"]["state_file"] = str(tmp_path / "idle-state.json")
    cfg["idle_mind"]["direct_reply_max_age_minutes"] = 180
    return cfg, uim.UnifiedIdleMindManager(
        loop=None, message_history=[], worker_manager=None, full_config=cfg)


def test_note_injection_preserves_source_reason_and_freshness(tmp_path, monkeypatch):
    from core.brain import unified_idle_mind as uim

    cfg, manager = _idle_manager(tmp_path, monkeypatch)

    uim.set_next_turn_note(
        "The ARES team is pushing to finish goibibo tonight.",
        reason="Urgent project deadline in the ARES WhatsApp group.",
        source="whatsapp",
    )
    injected = manager.get_pending_injection()

    assert "VERIFIED CURRENT CONTEXT" in injected
    assert "SOURCE=whatsapp" in injected
    assert "ARES WhatsApp group" in injected
    assert "goibibo" in injected


def test_fresh_whatsapp_note_answers_the_exact_live_question_directly(
        tmp_path, monkeypatch):
    from core.brain import unified_idle_mind as uim
    import core.llm as llm

    cfg, manager = _idle_manager(tmp_path, monkeypatch)
    uim.set_next_turn_note(
        "The ARES team is pushing to finish the goibibo task tonight and "
        "wants everyone active for Round 2. Tomorrow's CAO class is cancelled.",
        reason="Recent ARES WhatsApp activity.",
        source="whatsapp",
    )
    injection = manager.get_pending_injection()
    messages = [
        {"role": "system", "content": injection},
        {"role": "user", "content": "What's going on right now?"},
        {"role": "assistant", "content": "Just the usual."},
        {"role": "user", "content": "yes but what's going on"},
        {"role": "assistant", "content": "What do you mean?"},
        {"role": "user", "content": "I mean in my WhatsApp in my world"},
    ]

    reply = llm._direct_sourced_context_reply(messages, verify_prefill=True)

    assert reply is not None
    assert "WhatsApp" in reply
    assert "goibibo" in reply
    assert "CAO" in reply


def test_old_or_actionable_note_does_not_block_a_live_whatsapp_lookup(
        tmp_path, monkeypatch):
    from core.brain import unified_idle_mind as uim
    import core.llm as llm

    cfg, manager = _idle_manager(tmp_path, monkeypatch)
    uim.set_next_turn_note(
        "The ARES team had a hardware discussion.",
        reason="ARES WhatsApp group.", source="whatsapp")
    state = uim.load_idle_state()
    state["next_turn_note"]["created_at"] = time.time() - 4 * 3600
    uim.save_idle_state(state)
    injection = manager.get_pending_injection()

    stale = [
        {"role": "system", "content": injection},
        {"role": "user", "content": "What's going on in my WhatsApp?"},
    ]
    action = [
        {"role": "system", "content": injection},
        {"role": "user", "content": "Reply to ARES saying I will join."},
    ]
    assert llm._direct_sourced_context_reply(stale, verify_prefill=True) is None
    assert llm._direct_sourced_context_reply(action, verify_prefill=True) is None


def test_legacy_unknown_note_cannot_answer_a_source_specific_question(
        tmp_path, monkeypatch):
    from core.brain import unified_idle_mind as uim
    import core.llm as llm

    cfg, manager = _idle_manager(tmp_path, monkeypatch)
    uim.set_next_turn_note(
        "I noticed you were overwhelmed earlier; I can keep things quiet.",
        reason="Vaibhav requested some space.", source="unknown")
    injection = manager.get_pending_injection()
    messages = [
        {"role": "system", "content": injection},
        {"role": "user", "content": "What's going on in my WhatsApp?"},
    ]

    assert llm._direct_sourced_context_reply(
        messages, verify_prefill=True) is None
    assert llm._should_route_complex_query(messages[-1]["content"], messages)


def test_note_does_not_hijack_ordinary_questions_or_controls(tmp_path, monkeypatch):
    from core.brain import unified_idle_mind as uim
    import core.llm as llm

    cfg, manager = _idle_manager(tmp_path, monkeypatch)
    uim.set_next_turn_note(
        "The ARES team needs everyone active for Round 2.",
        reason="Recent ARES WhatsApp activity.", source="whatsapp")
    injection = manager.get_pending_injection()

    for utterance in (
        "What is seven times eight?",
        "Play some music",
        "Switch to default mode",
        "How is the air outside?",
    ):
        messages = [
            {"role": "system", "content": injection},
            {"role": "user", "content": utterance},
        ]
        assert llm._direct_sourced_context_reply(
            messages, verify_prefill=True) is None, utterance


def test_old_status_question_does_not_make_next_unrelated_turn_use_note(
        tmp_path, monkeypatch):
    from core.brain import unified_idle_mind as uim
    import core.llm as llm

    cfg, manager = _idle_manager(tmp_path, monkeypatch)
    uim.set_next_turn_note(
        "The ARES team needs everyone active for Round 2.",
        reason="Recent ARES WhatsApp activity.", source="whatsapp")
    injection = manager.get_pending_injection()
    messages = [
        {"role": "system", "content": injection},
        {"role": "user", "content": "What's going on?"},
        {"role": "assistant", "content": "What do you mean?"},
        {"role": "user", "content": "How is the air outside?"},
    ]

    assert llm._direct_sourced_context_reply(
        messages, verify_prefill=True) is None


def test_unused_note_is_rearmed_when_a_new_process_has_a_new_history(
        tmp_path, monkeypatch):
    from core.brain import unified_idle_mind as uim

    cfg, first = _idle_manager(tmp_path, monkeypatch)
    uim.set_next_turn_note(
        "Tomorrow's CAO class is cancelled.",
        reason="Recent class WhatsApp activity.", source="whatsapp")
    first_injection = first.get_pending_injection()
    assert first_injection
    assert uim.load_idle_state()["next_turn_note"]["injected"] is True

    second = uim.UnifiedIdleMindManager(
        loop=None, message_history=[], worker_manager=None, full_config=cfg)
    second_injection = second.get_pending_injection()
    assert second_injection
    assert "CAO class" in second_injection


def test_split_turn_whatsapp_clarification_routes_with_both_user_phrasings():
    import core.llm as llm

    messages = [
        {"role": "user", "content": "What's going on right now?"},
        {"role": "assistant", "content": "Just the usual."},
        {"role": "user", "content": "yes but what's going on"},
        {"role": "assistant", "content": "What do you mean?"},
        {"role": "user", "content": "I mean in my WhatsApp in my world"},
    ]

    assert llm._should_route_complex_query(messages[-1]["content"], messages)
    request = llm._resolved_complex_request(messages)
    assert "yes but what's going on" in request
    assert "I mean in my WhatsApp in my world" in request


def test_unrelated_whatsapp_mention_does_not_borrow_an_old_action():
    import core.llm as llm

    messages = [
        {"role": "user", "content": "What's going on right now?"},
        {"role": "assistant", "content": "Nothing much."},
        {"role": "user", "content": "I use WhatsApp every day."},
    ]
    assert not llm._should_route_complex_query(messages[-1]["content"], messages)


def test_health_mode_keeps_whatsapp_in_the_complex_query_contract(monkeypatch):
    import core.llm as llm
    from core import runtime_controls

    monkeypatch.setattr(runtime_controls, "get_active_mode", lambda: "health_sih")
    monkeypatch.setattr(runtime_controls, "mode_has_capability",
                        lambda capability, mode=None: capability == "care")

    instruction = llm._get_tools_instruction()

    assert "WhatsApp, email, Notion" in instruction
    assert "current WhatsApp/email/Notion" in instruction
    assert "Care-plan reads/edits and multi-step work" not in instruction
