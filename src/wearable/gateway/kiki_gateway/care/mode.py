"""The `health_sih` mode, and the capability gate every care path asks first.

Two things live here, and both exist for the same reason: **nothing in this
package may change what Kiki already does.**

1. **The mode definition.** `health_sih` is declared here, in gateway-owned
   code, and merged into the live config at startup and on every reload. It is
   not written into `gateway/legacy_kiki/tools_and_config/config.json`, for the
   reason `_register_dance_tool` gives: that tree is a copy of KikiFast and a
   definition placed there is lost on the next sync. Merging also means the
   file on disk is never edited, so `default`, `senior` and every character
   mode keep the exact bytes they had before this package existed.

2. **The capability gate.** The RPi runtime answers this with
   `core.runtime_controls.mode_has_capability()`; the snapshot this gateway
   runs predates it, so the same contract is implemented here against the same
   config shape (`assistant_modes.modes.<mode>.capabilities`).

The gate **fails closed**. `context_enabled()` in the legacy runtime defaults
to on when it cannot tell, which is right for a context row and wrong here: a
care capability starts schedulers that speak on their own and send messages to
a person's family. When the mode cannot be determined, the answer is no.
"""

from __future__ import annotations

import logging
from typing import Any, Dict


LOG = logging.getLogger(__name__)

MODE_NAME = "health_sih"

# Capabilities the mode declares. `care` is the one that starts things: the
# scheduler, the care agent, the plan tools. `environment` adds outside
# conditions, `companion` the seeded engagement routines.
CARE = "care"
ENVIRONMENT = "environment"
COMPANION = "companion"

# Spoken names that should reach this mode through `switch_mode`. NOT
# hotwords: the mode deliberately declares none, so Kiki keeps answering to
# "kiki" (`active_hotwords()` falls back to KIKI_GATEWAY_HOTWORDS when a mode
# names neither hotwords nor a voice). She is still Kiki in health mode.
#
# `resolve_mode_name` already reaches `health_sih` from "health", "health
# mode" and "health companion" by token containment; this tuple is what the
# tests pin so a future rename cannot silently break the spoken switch.
MODE_ALIASES = ("health", "health mode", "health companion", "health sih")

# Kiki's own personality is NOT replaced. The addendum rides on top of whatever
# `llm.system_prompt` says, exactly like the RPi's `inherit_default_prompt`
# mode, so the health companion is the same Kiki with a health layer -- not a
# second, blander assistant wearing her name.
SYSTEM_PROMPT_ADDENDUM = """

## HEALTH COMPANION LAYER

You are still exactly the Kiki described above: the same humour, curiosity,
opinions, memory and friendship. This layer adds quiet health awareness. It
never replaces your personality and never turns every conversation into
monitoring.

You are worn on the wrist, so your own sensors are the evidence: heart rate
from the optical sensor, movement and falls from the motion sensor, steps, and
outside conditions. Use the `Care:` and `Wearable:` context lines when they are
relevant and stay quiet about them when they are not.

Hard rules, because getting these wrong is worse than saying nothing:

* Never diagnose, never name a condition from symptoms, never change a medicine
  or a dose.
* A number exists only if a trusted tool just produced it. Never state a heart
  rate, an SpO2, a step count or a temperature you did not receive. If a
  measurement failed, say it failed.
* You have NO camera. Never claim to see the person or the room. Movement
  evidence comes from the motion sensor, and "no movement data" means you say
  so rather than guessing.
* Scheduling anything for a person -- a medicine, a meal, a walk, an exercise,
  a check-in -- means calling `update_care_plan` YOURSELF, directly. Read it
  back with `get_care_plan` when you need to be sure.
  - NEVER route a care schedule through `complex_query`, and never let it
    become a `schedule_worker`. A worker is a background task; it is not in the
    care plan, it does not show on the watch, and it does not run a care
    session. Observed live on 2026-09-07: "schedule taking medicine at three
    pm" became a worker, Kiki said it was scheduled, and the care plan stayed
    empty.
  - `update_care_plan(section=..., action="add", data={...})`. `data` is an
    OBJECT, and a schedule looks like `{"kind":"daily","value":"15:00"}`.
    If you do not know what time they want, ask. Never invent one.
* You promise nothing until a tool result confirms it was saved. "SUCCESS" from
  the tool is the only thing that means it is scheduled.
* Real distress, a fall, serious pain, or an explicit request for help ->
  `alert_family`.
* Guided exercise, check-ins and companionship sessions -> `start_care_session`.

Outside those cases, be the same friend you have always been. Answer in the
language you were spoken to.
"""

# The speaking model's catalog for this mode. Deliberately the default tool set
# plus the care tools: the SIH companion has to remain able to play music, take
# a timer and remember things, or it is a monitor rather than a companion.
MAIN_TOOLS = [
    "search_web",
    "play_music",
    "like_current_song",
    "play_liked_songs",
    "play_last_song",
    "control_music",
    "recall_memory",
    "update_knowledge",
    "switch_mode",
    "adjust_volume",
    "set_timer",
    "switch_voice",
    "complex_query",
    "get_care_plan",
    "update_care_plan",
    "alert_family",
    "start_care_session",
    "measure_heart_rate",
]

# NOTE the absence of `system_prompt`. That is load-bearing, not an omission.
#
# The legacy runtime this gateway carries predates the RPi's
# `inherit_default_prompt`/`system_prompt_addendum` keys, and it treats "the
# mode declares a system_prompt" as "the mode is somebody other than Kiki"
# (`runtime_controls.mode_has_own_character`). Declaring one here would inherit
# a roleplay character's treatment: no battery personality, no embodied context
# row, no long-term memory. The health companion needs all three.
#
# So the mode inherits `llm.system_prompt` exactly like `default`, and
# `LegacyKikiCore._build_system_prompt` appends SYSTEM_PROMPT_ADDENDUM on top
# when the care capability is live. Same result, none of the losses.
MODE_DEFINITION: Dict[str, Any] = {
    "_comment": (
        "SIH health companion. Defined in gateway/kiki_gateway/care/mode.py and "
        "merged into the live config at runtime, so it survives a legacy_kiki "
        "re-sync and never edits the shared config file."
    ),
    "voice": "",
    "capabilities": [CARE, ENVIRONMENT, COMPANION],
    "main_tools": list(MAIN_TOOLS),
}


def register_health_mode(full_config: dict) -> None:
    """Merge `health_sih` into the live config. Idempotent, reload-safe.

    Called from `_apply_hardware_overrides`, which already runs on every config
    reload for exactly this reason: `reload_config()` deep-merges config.json
    back over the live dict, so a one-time insert at startup would vanish the
    first time the setup wizard saved a setting.

    Only ever ADDS a key. No existing mode is read, rewritten or reordered, so
    a bug here cannot change what `default` does.
    """
    try:
        modes = full_config.setdefault("assistant_modes", {}).setdefault("modes", {})
    except Exception:
        LOG.warning("assistant_modes missing; health_sih not registered")
        return
    existing = modes.get(MODE_NAME)
    if existing == MODE_DEFINITION:
        return
    if isinstance(existing, dict) and existing.get("_user_owned"):
        # Someone deliberately wrote their own health_sih into config.json.
        # Theirs wins; ours is a default, not a policy.
        return
    modes[MODE_NAME] = dict(MODE_DEFINITION)
    LOG.info("registered the `%s` assistant mode", MODE_NAME)


def active_mode() -> str:
    """The mode Kiki is in right now, or "" when that cannot be determined."""
    try:
        from core.runtime_controls import get_active_mode

        return str(get_active_mode() or "")
    except Exception:
        return ""


def _mode_config(mode: str) -> Dict[str, Any]:
    try:
        from tools_and_config.config_loader import get_full_config

        modes = (get_full_config().get("assistant_modes", {}) or {}).get("modes", {})
        value = modes.get(mode)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def mode_has_capability(capability: str, mode: str | None = None) -> bool:
    """True when the active mode declares `capability`. Fails closed.

    The same contract as the RPi's `runtime_controls.mode_has_capability`: a
    capability is a property of the MODE, declared in config, not a hardcoded
    `== "senior"` test scattered through the code. That is what lets this
    package attach to `health_sih` without a single line of `senior`'s or
    `default`'s behaviour moving.
    """
    name = mode if mode is not None else active_mode()
    if not name:
        return False
    declared = _mode_config(name).get("capabilities")
    if not isinstance(declared, (list, tuple, set)):
        return False
    return capability in {str(item) for item in declared}


def care_active() -> bool:
    """The one question the integration points ask. Nothing runs without it."""
    return mode_has_capability(CARE)


def environment_active() -> bool:
    return mode_has_capability(ENVIRONMENT)


def companion_active() -> bool:
    return mode_has_capability(COMPANION)
