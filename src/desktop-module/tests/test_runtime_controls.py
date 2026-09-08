import asyncio
import json

import pytest

from core import runtime_controls as controls


@pytest.fixture(autouse=True)
def reset_runtime_state(monkeypatch):
    config = {
        "llm": {"system_prompt": "the original default prompt"},
        "assistant_modes": {
            "active_on_startup": "default",
            "language_on_startup": "english",
            "followups_enabled_on_startup": True,
            "volume_step_percent": 10,
            "modes": {
                "default": {"system_prompt": None, "voice": ""},
                "funny": {"system_prompt": "be very funny", "voice": "comic"},
                "health": {
                    "system_prompt": "legacy replacement that must be ignored",
                    "inherit_default_prompt": True,
                    "system_prompt_addendum": "HEALTH LAYER",
                    "voice": "",
                },
                "amitabh": {"system_prompt": "speak like amitabh", "voice": "amitabh"},
                "Vaibhav": {"system_prompt": "speak like vaibhav", "voice": "vaibhav"},
            },
        },
    }
    monkeypatch.setattr(controls, "get_full_config", lambda: config)
    monkeypatch.setattr(controls, "_current_mode", None)
    monkeypatch.setattr(controls, "_followups_enabled", None)
    monkeypatch.setattr(controls, "_mode_revision", 0)
    return config


@pytest.mark.parametrize(("utterance", "expected"), [
    ("Switch to funny mode", ("switch_mode", {"mode": "funny"})),
    ("Switch back to default mode", ("switch_mode", {"mode": "default"})),
    ("Switch to Amitabh Bachchan mode", ("switch_mode", {"mode": "amitabh"})),
    ("Switch to Vaibav mode", ("switch_mode", {"mode": "Vaibhav"})),
    ("Switch to funy", ("switch_mode", {"mode": "funny"})),
    ("Sound like Vaibav", ("switch_mode", {"mode": "Vaibhav"})),
    ("Increase the volume", ("adjust_volume", {"action": "increase"})),
    ("Increase the volume by 20", ("adjust_volume", {"action": "increase", "amount": 20})),
    ("Speak quieter", ("adjust_volume", {"action": "decrease"})),
    ("Set volume to 37 percent", ("adjust_volume", {"action": "set", "amount": 37})),
    ("Disable follow ups", ("set_followups", {"enabled": False})),
    ("Only listen after the wake word", ("set_followups", {"enabled": False})),
    ("Turn on follow-ups", ("set_followups", {"enabled": True})),
    ("Like this song", ("like_current_song", {})),
    ("Add this song to my liked songs", ("like_current_song", {})),
    ("Play my liked songs", ("play_liked_songs", {})),
    ("Play the last song", ("play_last_song", {})),
    ("Next song", ("control_music", {"action": "next"})),
    ("Play the previous song", ("control_music", {"action": "previous"})),
    ("Pause music", ("control_music", {"action": "pause"})),
    ("Resume the song", ("control_music", {"action": "resume"})),
    ("Set a timer for five seconds", ("set_timer", {"duration": 5})),
    ("Start timer 2 minutes", ("set_timer", {"duration": 120})),
])
def test_spoken_control_parser(utterance, expected):
    assert controls.parse_spoken_control(utterance) == expected


@pytest.mark.parametrize("utterance", [
    "How do I increase the volume on Ubuntu?",
    "Tell me how to disable follow ups.",
    "Don't switch to funny mode.",
    "What happens if I set the volume to 90 percent?",
])
def test_questions_and_negations_are_not_executed(utterance):
    assert controls.parse_spoken_control(utterance) is None


@pytest.mark.parametrize(("requested", "expected"), [
    ("amitabh", "amitabh"),
    ("Amitabh Bachchan mode please", "amitabh"),
    ("amitab", "amitabh"),
    ("vaibav voice", "Vaibhav"),
    ("please switch into funny personality", "funny"),
    ("something completely unrelated", None),
])
def test_mode_name_resolution_is_exact_contained_and_fuzzy(requested, expected):
    assert controls.resolve_mode_name(requested) == expected


def test_ambiguous_fuzzy_mode_is_rejected(reset_runtime_state):
    reset_runtime_state["assistant_modes"]["modes"] = {
        "comic_one": {"system_prompt": "one"},
        "comic_two": {"system_prompt": "two"},
    }
    assert controls.resolve_mode_name("comic mode") is None


def test_mode_switch_changes_prompt_voice_and_revision(monkeypatch):
    applied = []
    monkeypatch.setattr(controls, "apply_active_voice",
                        lambda: applied.append(controls.get_active_voice()) or applied[-1])

    assert controls.get_active_mode() == "default"
    assert controls.get_active_system_prompt() == "the original default prompt"
    assert controls.get_mode_revision() == 0

    result = controls.switch_mode("Funny")

    assert controls.get_active_mode() == "funny"
    assert controls.get_active_system_prompt() == "be very funny"
    assert controls.get_mode_revision() == 1
    assert applied == ["comic"]
    assert "Switched to funny mode" in result


def test_english_language_does_not_change_any_mode_prompt(reset_runtime_state):
    assert controls.get_active_system_prompt() == "the original default prompt"
    controls._current_mode = "funny"
    assert controls.get_active_system_prompt() == "be very funny"


def test_capability_mode_layers_rules_on_the_complete_default_persona(
        reset_runtime_state):
    controls._current_mode = "health"

    assert controls.get_active_system_prompt() == (
        "the original default prompt\n\nHEALTH LAYER")
    assert controls.mode_has_own_character() is False


def test_hindi_language_suffix_applies_to_every_mode(reset_runtime_state):
    reset_runtime_state["assistant_modes"]["language_on_startup"] = "hindi"

    assert controls.get_active_system_prompt() == (
        "the original default prompt\n\nAlways output hindi devnagri."
    )
    controls._current_mode = "funny"
    assert controls.get_active_system_prompt() == (
        "be very funny\n\nAlways output hindi devnagri."
    )


def test_hindi_language_suffix_is_not_duplicated(reset_runtime_state):
    reset_runtime_state["assistant_modes"]["language_on_startup"] = "hindi"
    reset_runtime_state["assistant_modes"]["modes"]["funny"]["system_prompt"] = (
        "be funny. Always output hindi devnagri."
    )
    controls._current_mode = "funny"

    assert controls.get_active_system_prompt().count(
        "Always output hindi devnagri."
    ) == 1


def test_mode_switch_resolves_fuzzy_argument_to_config_key(monkeypatch):
    monkeypatch.setattr(controls, "apply_active_voice", controls.get_active_voice)

    result = controls.switch_mode("please use the Vaibav voice")

    assert controls.get_active_mode() == "Vaibhav"
    assert controls.get_active_voice() == "vaibhav"
    assert "Switched to Vaibhav mode" in result


def test_followups_are_persistent_until_explicitly_reenabled():
    assert controls.followups_enabled() is True
    controls.set_followups(False)
    assert controls.followups_enabled() is False
    assert controls.followups_enabled() is False
    controls.set_followups(True)
    assert controls.followups_enabled() is True


def test_clear_control_is_auto_routed_before_model(monkeypatch):
    import core.llm as llm

    monkeypatch.setattr(llm, "_SEND_TOOLS", True)
    monkeypatch.setattr(llm, "_MAIN_TOOL_NAMES", ["switch_mode"])
    events = list(llm.stream_response([
        {"role": "system", "content": "test"},
        {"role": "user", "content": "Switch to funny mode"},
    ]))

    assert [kind for kind, _ in events] == ["tool_calls", "done"]
    call = events[0][1]["calls"][0]
    assert call["name"] == "switch_mode"
    assert json.loads(call["arguments"]) == {"mode": "funny"}


def test_adjust_volume_uses_configured_step(monkeypatch):
    from core import ir_controls
    from tools_and_config import tools

    applied = []
    monkeypatch.setattr(ir_controls, "get_bt_volume", lambda: 45)
    monkeypatch.setattr(ir_controls, "set_bt_volume",
                        lambda value: applied.append(value) or value)

    result = asyncio.run(tools.adjust_volume("increase"))

    assert applied == [55]
    assert result == "Volume set to 55 percent."


# --- Persona reinforcement -------------------------------------------------
# Measured on the box: appending the 2159-char tools instruction to a short
# mode prompt flattened the character into a generic assistant. These guard the
# two structural properties of the fix — the character is repeated last, and
# its position is FIXED so the KV-cached prefix in Codestructure section 4
# cannot be invalidated per turn.

def _enable_reinforce(config, **overrides):
    settings = config["assistant_modes"]
    settings["persona_reinforce"] = {
        "enabled": True,
        "custom_prompt_modes_only": True,
        "max_character_chars": 700,
        "text": "\n\nSTAY IN CHARACTER.",
        **overrides,
    }
    return settings


def test_reinforcement_repeats_a_custom_mode_character(reset_runtime_state):
    _enable_reinforce(reset_runtime_state)
    controls.switch_mode("funny")

    reinforcement = controls.get_persona_reinforcement()

    assert reinforcement.startswith("be very funny")
    assert reinforcement.endswith("STAY IN CHARACTER.")


def test_default_mode_is_left_alone(reset_runtime_state):
    """`default` inherits the persona-heavy llm.system_prompt already."""
    _enable_reinforce(reset_runtime_state)

    assert controls.get_active_mode() == "default"
    assert controls.get_persona_reinforcement() == ""


def test_reinforcement_is_disabled_by_default(reset_runtime_state):
    controls.switch_mode("funny")
    assert controls.get_persona_reinforcement() == ""


def test_long_characters_are_clipped_at_a_sentence(reset_runtime_state):
    settings = _enable_reinforce(reset_runtime_state, max_character_chars=40)
    settings["modes"]["funny"]["system_prompt"] = (
        "You are a comedian. " + "You tell very long stories about nothing. " * 5
    )
    controls.switch_mode("funny")

    reinforcement = controls.get_persona_reinforcement()

    assert reinforcement.startswith("You are a comedian.")
    assert len(reinforcement) < 120


def test_tools_instruction_no_longer_rides_on_the_persona_message(monkeypatch):
    """The protocol gets its own message; the character gets the last word."""
    import core.llm as llm

    monkeypatch.setattr(llm, "_SEND_TOOLS", True)
    monkeypatch.setattr(llm, "_get_tools_instruction", lambda: "\n\n## TOOLS\ncall them")
    monkeypatch.setattr("core.runtime_controls.get_persona_reinforcement",
                        lambda: "BE ROHAN")

    out = llm._normalize_messages_for_local([
        {"role": "system", "content": [{"type": "text", "text": "you are rohan"}]},
        {"role": "system", "content": "your memories"},
        {"role": "user", "content": "hi"},
    ])

    assert [m["role"] for m in out] == ["system"] * 4 + ["user"]
    assert out[0]["content"] == "you are rohan"          # persona stays clean
    assert out[1]["content"] == "## TOOLS\ncall them"    # protocol split out
    assert out[2]["content"] == "your memories"
    assert out[3]["content"] == "BE ROHAN"               # character last


def test_reinforcement_position_does_not_move_as_the_chat_grows(monkeypatch):
    """A moving system message would re-prefill the box on every single turn."""
    import core.llm as llm

    monkeypatch.setattr(llm, "_SEND_TOOLS", True)
    monkeypatch.setattr(llm, "_get_tools_instruction", lambda: "## TOOLS")
    monkeypatch.setattr("core.runtime_controls.get_persona_reinforcement",
                        lambda: "BE ROHAN")

    base = [
        {"role": "system", "content": "you are rohan"},
        {"role": "system", "content": "your memories"},
    ]
    short = llm._normalize_messages_for_local(base + [
        {"role": "user", "content": "hi"},
    ])
    long = llm._normalize_messages_for_local(base + [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "system", "content": "Right now it's 9pm."},
        {"role": "user", "content": "still there?"},
    ])

    assert short[:4] == long[:4]
    assert [m["content"] for m in long].count("BE ROHAN") == 1


# --- Roleplay context isolation --------------------------------------------
# A mode prompt replaces the persona but NOT the ~10k chars of knowledge base
# and last-session summary main.py appends as message_history[1], so "you are
# Rohan" was still being briefed on Vaibhav's IMS portal. These lock the gate.

def test_every_context_source_is_on_by_default(reset_runtime_state):
    """`default` and any ordinary mode must behave exactly as before."""
    policy = controls.get_context_policy()

    assert set(policy) == set(controls._CONTEXT_SOURCES)
    assert all(policy.values())
    assert controls.context_enabled("memory") is True


def test_roleplay_suppresses_every_source(reset_runtime_state):
    reset_runtime_state["assistant_modes"]["modes"]["funny"]["roleplay"] = True
    controls.switch_mode("funny")

    assert not any(controls.get_context_policy().values())
    assert controls.context_enabled("memory") is False
    assert controls.context_enabled("face_events") is False


def test_a_context_block_overrides_roleplay_per_source(reset_runtime_state):
    """Let a character keep its eyes without regaining Kiki's memories."""
    mode = reset_runtime_state["assistant_modes"]["modes"]["funny"]
    mode["roleplay"] = True
    mode["context"] = {"vision": True}
    controls.switch_mode("funny")

    assert controls.context_enabled("vision") is True
    assert controls.context_enabled("memory") is False


def test_context_block_works_without_roleplay(reset_runtime_state):
    reset_runtime_state["assistant_modes"]["modes"]["funny"]["context"] = {
        "peeping": False}
    controls.switch_mode("funny")

    assert controls.context_enabled("peeping") is False
    assert controls.context_enabled("memory") is True


def test_unknown_context_keys_are_ignored(reset_runtime_state):
    reset_runtime_state["assistant_modes"]["modes"]["funny"]["context"] = {
        "not_a_source": False}
    controls.switch_mode("funny")

    assert set(controls.get_context_policy()) == set(controls._CONTEXT_SOURCES)
    assert all(controls.get_context_policy().values())


def test_context_gate_fails_open(monkeypatch):
    """Gating must never be the reason Kiki stops answering."""
    def boom():
        raise RuntimeError("config exploded")
    monkeypatch.setattr(controls, "get_context_policy", boom)

    assert controls.context_enabled("memory") is True


def test_switching_out_of_roleplay_restores_context(reset_runtime_state):
    reset_runtime_state["assistant_modes"]["modes"]["funny"]["roleplay"] = True
    controls.switch_mode("funny")
    assert controls.context_enabled("memory") is False

    controls.switch_mode("default")
    assert controls.context_enabled("memory") is True


def test_auto_recall_router_honours_the_mode_tool_list(monkeypatch):
    """recall_memory reads the memory the context gate just closed, so a mode
    that drops the tool must not have it fired by the code router either."""
    import core.llm as llm

    monkeypatch.setattr(llm, "_SEND_TOOLS", True)
    monkeypatch.setattr(llm, "_MAIN_TOOL_NAMES", ["recall_memory"])
    messages = [{"role": "user", "content": "what did we discuss yesterday"}]

    monkeypatch.setattr(llm, "_effective_main_tools", lambda: (["recall_memory"], []))
    assert llm._auto_memory_tool_event(messages, True, False) is not None

    monkeypatch.setattr(llm, "_effective_main_tools", lambda: (["search_web"], []))
    assert llm._auto_memory_tool_event(messages, True, False) is None


def test_tools_preamble_drops_recall_memory_when_the_mode_lacks_it(monkeypatch):
    """The preamble used to order EVERY mode to call recall_memory.

    A roleplay mode with no memory tools obeyed it and emitted the call anyway,
    which execute_tool would then have run against Kiki's real memories.
    """
    import core.llm as llm

    monkeypatch.setattr(llm, "_effective_main_tools",
                        lambda: (["play_music"], [{"function": {
                            "name": "play_music", "parameters": {}}}]))
    instruction = llm._get_tools_instruction()

    assert "recall_memory" not in instruction
    assert "MANDATORY" not in instruction
    assert "play_music" in instruction


def test_speaking_path_refuses_a_tool_outside_a_declared_mode_catalog(monkeypatch):
    """Defence in depth: the model can always name a tool it was not given."""
    import core.llm as llm

    ran = []
    monkeypatch.setattr(llm, "execute_tool",
                        lambda name, args: ran.append(name) or "secret memories")
    monkeypatch.setattr(llm, "_mode_tool_override", lambda: ["play_music"])

    out = llm.execute_tool_calls([
        {"name": "recall_memory", "arguments": '{"query": "everything"}'}])

    assert ran == []                      # never dispatched
    assert "secret memories" not in out
    assert "unavailable" in out

    llm.execute_tool_calls([{"name": "play_music", "arguments": "{}"}])
    assert ran == ["play_music"]          # allowed tools still run


def test_modes_without_a_declared_catalog_are_not_enforced(monkeypatch):
    """llm.main_tools is a PROMPT BUDGET, not a permission boundary.

    It omits live handlers such as get_current_time purely to keep the warm
    prefix small. Enforcing it would break default mode for no benefit, so
    enforcement applies only where a mode deliberately scoped its own tools.
    """
    import core.llm as llm

    ran = []
    monkeypatch.setattr(llm, "execute_tool",
                        lambda name, args: ran.append(name) or "9pm")
    monkeypatch.setattr(llm, "_mode_tool_override", lambda: None)

    llm.execute_tool_calls([{"name": "get_current_time", "arguments": "{}"}])

    assert ran == ["get_current_time"]


def test_every_curated_webui_control_resolves_to_a_real_config_path():
    """A curated control whose path does not exist renders as a blank box."""
    from webui import server
    from tools_and_config import config_loader

    cfg = config_loader.get_full_config()
    missing = [
        c["path"]
        for g in server._CONTROL_GROUPS + server._roleplay_groups(cfg)
        for c in g["controls"]
        if c["kind"] != "bool" and server._get_path(cfg, c["path"]) is None
    ]

    assert missing == []


def test_webui_exposes_a_toggle_for_every_configured_mode():
    """A mode added by hand to config.json must not be invisible in the UI."""
    from webui import server
    from tools_and_config import config_loader

    cfg = config_loader.get_full_config()
    modes = {m for m in cfg["assistant_modes"]["modes"] if not m.startswith("_")}
    groups = server._roleplay_groups(cfg)
    exposed = {c["path"].split(".")[-2] for c in groups[0]["controls"]}

    assert exposed == modes
