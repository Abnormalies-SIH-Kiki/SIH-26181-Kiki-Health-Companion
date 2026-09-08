"""The gate: nothing in the care package may run outside health mode.

This is the file to read first if you are wondering whether adding health mode
can break the Kiki that is being demonstrated tomorrow. Every test here is a
statement about what happens in `default`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kiki_gateway.care import mode  # noqa: E402
from kiki_gateway.care import tools as care_tools  # noqa: E402


@pytest.fixture
def config():
    """A config shaped like the real one: several modes, none of them ours."""
    return {
        "assistant_modes": {
            "active_on_startup": "default",
            "modes": {
                "default": {"system_prompt": None, "voice": ""},
                "senior": {"voice": "", "main_tools": ["search_web", "alert_family"]},
                "rohan": {"system_prompt": "You are Rohan.", "voice": "rohan"},
            },
        },
        "llm": {"main_tools": ["search_web", "play_music"]},
    }


def test_registering_the_mode_adds_it_and_touches_nothing_else(config):
    before = json.dumps(config["assistant_modes"]["modes"], sort_keys=True)
    mode.register_health_mode(config)
    modes = config["assistant_modes"]["modes"]

    assert mode.MODE_NAME in modes
    del modes[mode.MODE_NAME]
    assert json.dumps(modes, sort_keys=True) == before, (
        "registering health_sih rewrote another mode; default must be untouched")


def test_registration_is_idempotent_across_config_reloads(config):
    mode.register_health_mode(config)
    first = dict(config["assistant_modes"]["modes"][mode.MODE_NAME])
    # reload_config() deep-merges config.json back over the live dict, which is
    # why this runs again on every reload rather than once at startup.
    mode.register_health_mode(config)
    mode.register_health_mode(config)
    assert config["assistant_modes"]["modes"][mode.MODE_NAME] == first


def test_a_hand_written_mode_wins_over_the_bundled_one(config):
    config["assistant_modes"]["modes"][mode.MODE_NAME] = {
        "_user_owned": True, "capabilities": ["care"], "voice": "custom"}
    mode.register_health_mode(config)
    assert config["assistant_modes"]["modes"][mode.MODE_NAME]["voice"] == "custom"


def test_the_mode_declares_no_prompt_of_its_own(config):
    """Declaring one would cost Kiki her personality, memory and body context.

    `runtime_controls.mode_has_own_character()` treats a mode with a
    `system_prompt` as somebody other than Kiki, and the gateway then skips the
    battery personality, the embodied-context block and long-term memory. The
    health layer is appended by `_build_system_prompt` instead.
    """
    mode.register_health_mode(config)
    entry = config["assistant_modes"]["modes"][mode.MODE_NAME]
    assert "system_prompt" not in entry
    assert not entry.get("voice"), "health mode is still Kiki; she keeps her voice"
    assert "hotwords" not in entry, "she still answers to 'kiki', not to 'health'"


def test_the_capability_gate_fails_closed(monkeypatch):
    monkeypatch.setattr(mode, "active_mode", lambda: "")
    assert mode.care_active() is False
    assert mode.mode_has_capability("care") is False


@pytest.mark.parametrize("active,expected", [
    ("default", False),
    ("rohan", False),
    ("senior", False),
    ("health_sih", True),
])
def test_only_a_mode_that_declares_the_capability_gets_it(monkeypatch, config,
                                                          active, expected):
    mode.register_health_mode(config)
    monkeypatch.setattr(mode, "active_mode", lambda: active)
    monkeypatch.setattr(mode, "_mode_config",
                        lambda name: config["assistant_modes"]["modes"].get(name, {}))
    assert mode.care_active() is expected


def test_senior_mode_is_not_given_the_capability(config):
    """`senior` keeps working exactly as it does today, on the older stack."""
    mode.register_health_mode(config)
    assert "capabilities" not in config["assistant_modes"]["modes"]["senior"]


def test_care_tools_refuse_to_run_outside_health_mode(monkeypatch):
    monkeypatch.setattr(care_tools, "care_active", lambda: False)
    for name in sorted(care_tools.CARE_TOOL_NAMES):
        result = care_tools.execute_care_tool(name, {})
        assert result.startswith("BLOCKED"), (
            f"{name} ran outside health mode: {result[:120]}")


def test_an_unknown_tool_is_refused_even_in_health_mode(monkeypatch):
    monkeypatch.setattr(care_tools, "care_active", lambda: True)
    assert care_tools.execute_care_tool("rm_rf", {}).startswith("BLOCKED")


def test_the_runtime_is_not_even_built_in_default_mode(monkeypatch):
    from kiki_gateway import care
    from kiki_gateway.care import runtime as care_runtime

    monkeypatch.setattr(care_runtime, "_RUNTIME", None)
    monkeypatch.setattr(care, "care_active", lambda: False)

    assert care.sync_care_mode() is False
    assert care_runtime.get_care_runtime(create=False) is None
    assert care_runtime.care_runtime_if_live() is None


def test_the_health_layer_sends_schedules_to_the_care_plan_not_a_worker():
    """A care schedule that becomes a background worker is invisible.

    Live on 2026-09-07: "schedule taking medicine at three pm" went through
    `complex_query`, the action agent chose `schedule_worker`, Kiki said it was
    scheduled, and the care plan stayed empty. The action agent on this body
    has no care tools, so that route cannot edit the plan at all.
    """
    text = mode.SYSTEM_PROMPT_ADDENDUM
    assert "update_care_plan` YOURSELF" in text
    assert "schedule_worker" in text
    assert "NEVER route a care schedule through `complex_query`" in text
    assert "update_care_plan" in mode.MAIN_TOOLS
    assert "get_care_plan" in mode.MAIN_TOOLS
