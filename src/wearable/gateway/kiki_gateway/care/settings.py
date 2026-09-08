"""Tunables for `health_sih`, kept out of the shared config file.

The RPi reads these from `senior_mode.care_agent`. Reading that block here
would tie the health companion's session limits to a mode this package
promised not to touch -- and worse, a change made for one would silently move
the other. So `health_sih` gets its own section, `senior_mode` is accepted only
as a read-only fallback for anyone who already tuned it, and the defaults below
are the ones the RPi arrived at after the live session failures they name.

Nothing here is written back to disk. The section can be added by hand to the
shared `config.json` under `health_sih`, or left absent entirely -- the defaults
are a working system.
"""

from __future__ import annotations

import os
from typing import Any, Dict


DEFAULTS: Dict[str, Any] = {
    # --- session lifetime -------------------------------------------------
    # Each of these closes a session the model itself would not: it is told
    # "usually it is continue", and for a while it was the only thing that
    # could end one.
    "max_session_turns": 40,
    "max_session_minutes": 45,
    "session_idle_timeout_minutes": 20,
    "turn_deadline_seconds": 90,

    # --- the cloud care agent --------------------------------------------
    "max_turns": 5,
    "max_tool_calls": 6,
    "max_prompt_chars": 1_000_000,
    "max_tool_result_chars": 3000,
    "persona_chars": 3000,
    "history_chars": 9000,
    "history_record_chars": 700,
    "artifact_limit": 8,

    # --- guided exercise, IMU-grounded -----------------------------------
    # Two mismatched movements in a row stop the routine and ask. One is
    # corrected out loud and repeated with the microphone still muted, because
    # stopping on every mismatch turned the routine into an interview.
    "violations_before_listening": 2,
    "retry_hold_seconds": 8,
    # A hold shorter than this cannot produce a usable motion window; the
    # coach asks for evidence over a real interval, not a snapshot.
    "min_window_seconds": 2.0,
    # How long to wait for the board's window after the hold ends. The board
    # sends it as one event when the window closes.
    "window_timeout_seconds": 6.0,

    # --- proactive care ---------------------------------------------------
    # Seeded companion routines (morning briefing, evening reflection,
    # hydration, movement, sleep wind-down). Off by default: routines that
    # speak on a schedule are the single most intrusive thing this package can
    # do, and they should be switched on deliberately.
    "seed_companion_routines": False,
    # Poll interval for the environment provider, seconds.
    "environment_poll_seconds": 900,
}


def _section(config: Dict[str, Any], *path: str) -> Dict[str, Any]:
    node: Any = config
    for key in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def care_settings() -> Dict[str, Any]:
    """Defaults, overlaid by `senior_mode.care_agent`, then by `health_sih`."""
    try:
        from tools_and_config.config_loader import get_full_config

        config = get_full_config() or {}
    except Exception:
        config = {}
    merged = dict(DEFAULTS)
    merged.update(_section(config, "senior_mode", "care_agent"))
    merged.update(_section(config, "health_sih"))
    return merged


def setting(name: str, default: Any = None) -> Any:
    """One tunable, with `KIKI_GATEWAY_CARE_<NAME>` taking precedence.

    The environment override exists so a demo can be retuned from
    `gateway.env` without editing the shared config file that four other modes
    read.
    """
    env = os.environ.get("KIKI_GATEWAY_CARE_" + name.upper(), "").strip()
    fallback = DEFAULTS.get(name, default)
    if env:
        if isinstance(fallback, bool):
            return env.lower() not in {"0", "false", "no", "off"}
        try:
            return type(fallback)(env) if fallback is not None else env
        except (TypeError, ValueError):
            return env
    value = care_settings().get(name, fallback)
    return fallback if value is None else value


def number(name: str, default: float = 0.0) -> float:
    try:
        return float(setting(name, default))
    except (TypeError, ValueError):
        return float(DEFAULTS.get(name, default) or default)


def integer(name: str, default: int = 0) -> int:
    try:
        return int(float(setting(name, default)))
    except (TypeError, ValueError):
        return int(DEFAULTS.get(name, default) or default)


def flag(name: str, default: bool = False) -> bool:
    return bool(setting(name, default))


# Where the weather and air quality are read for. Open-Meteo needs no API key,
# so this is the whole configuration: coordinates and a name to say out loud.
# `KIKI_GATEWAY_CARE_LATITUDE` / `_LONGITUDE` / `_PLACE` override, so a demo in
# another city needs an env line rather than a config edit.
ENVIRONMENT_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "latitude": 28.6139,
    "longitude": 77.2090,
    "place": "Delhi",
    "poll_seconds": 900,
    "care_now_cooldown_seconds": 900,
}


def environment_config() -> Dict[str, Any]:
    """Config for the environment provider and the CARE NOW cooldown.

    The RPi reads `environment` out of the shared config.json. That key does
    not exist in either gateway runtime snapshot, so the defaults above are
    what actually run unless someone adds it -- which is the point: weather and
    air quality must work on a machine nobody has configured.
    """
    try:
        from tools_and_config.config_loader import get_full_config

        configured = (get_full_config() or {}).get("environment")
    except Exception:
        configured = None
    merged = dict(ENVIRONMENT_DEFAULTS)
    if isinstance(configured, dict):
        merged.update(configured)
    for key, env_name in (("latitude", "KIKI_GATEWAY_CARE_LATITUDE"),
                          ("longitude", "KIKI_GATEWAY_CARE_LONGITUDE"),
                          ("place", "KIKI_GATEWAY_CARE_PLACE")):
        value = os.environ.get(env_name, "").strip()
        if not value:
            continue
        if key == "place":
            merged[key] = value
        else:
            try:
                merged[key] = float(value)
            except ValueError:
                pass
    return merged
