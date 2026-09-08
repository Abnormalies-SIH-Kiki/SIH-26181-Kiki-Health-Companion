"""The `health_sih` health-companion stack.

Everything in this package is inert until Kiki is switched into a mode that
declares the `care` capability. `mode.care_active()` is the gate, it fails
closed, and every integration point in the gateway asks it before touching
anything here. In `default` mode the only cost of this package existing is the
import.
"""

from typing import Any, Dict, List

from .mode import (  # noqa: F401
    MODE_NAME,
    care_active,
    companion_active,
    environment_active,
    mode_has_capability,
    register_health_mode,
)


def care_tools_for_catalog() -> List[Dict[str, Any]]:
    """The care tool schemas, for registration into the runtime's catalog."""
    from .tools import CARE_TOOL_SCHEMAS

    return list(CARE_TOOL_SCHEMAS)


def sync_care_mode(core=None) -> bool:
    """Start or stand down the care stack to match the active mode.

    The single entry point the gateway calls after anything that can change the
    mode. Safe to call from any thread, safe to call when nothing has changed,
    and it never raises: a failure to start health mode must not break the turn
    that switched into it.
    """
    try:
        from .runtime import get_care_runtime

        if not care_active():
            # Do not CREATE a runtime just to be told there is nothing to do.
            runtime = get_care_runtime(create=False)
            return runtime.sync_to_mode() if runtime is not None else False
        runtime = get_care_runtime()
        if core is not None:
            runtime.attach_core(core)
        return runtime.sync_to_mode()
    except Exception:  # pragma: no cover - defensive by design
        import logging

        logging.getLogger(__name__).exception("could not sync health mode")
        return False


__all__ = [
    "MODE_NAME",
    "care_active",
    "care_tools_for_catalog",
    "companion_active",
    "environment_active",
    "mode_has_capability",
    "register_health_mode",
    "sync_care_mode",
]
