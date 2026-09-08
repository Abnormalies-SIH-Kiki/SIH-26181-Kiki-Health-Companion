"""Keep the test suite off the robot's live state.

`care_plan.json` is a RUNTIME file that the running Kiki writes to constantly,
and several modules read it through a process-wide singleton. That made test
results depend on whatever the robot happened to be doing at the moment pytest
ran, which is not a theoretical concern:

    $ pytest tests/ -q      # while a care session was active
    19 failed, 568 passed
    $ pytest tests/ -q      # minutes later, session cancelled
    9 failed, 556 passed

Nothing changed but the robot. All ten `test_action_agent` failures moved with
it, because `core.llm._should_route_complex_query` sends *everything* to the
complex agent while a care session is live — so `_should_route_complex_query("hi")`
is True mid-session and False otherwise, and the suite's answer to "is this a
regression?" depended on the time of day.

This points every test at a throwaway care plan instead. Tests that want their
own file still build `CarePlan(path)` directly and are unaffected.
"""

import json
import threading

import pytest


@pytest.fixture(autouse=True)
def isolate_active_mode():
    """Pin the assistant mode per test, and put it back afterwards.

    Same class of bug as the care plan below: the active mode is process-wide
    runtime state seeded from `assistant_modes.active_on_startup`, which is
    currently "senior". Four `test_history_view` tests assume Kiki's own persona
    and were failing purely because of that -- they pass the moment anything
    happens to leave the mode as "default", which made them order-dependent
    rather than genuinely broken. Tests that need a specific mode call
    `switch_mode` themselves and are unaffected.
    """
    from core import runtime_controls

    previous = runtime_controls.get_active_mode()
    runtime_controls.switch_mode("default")
    yield
    runtime_controls.switch_mode(previous)


@pytest.fixture(autouse=True)
def isolate_care_plan(tmp_path, monkeypatch):
    """Point the care-plan singleton at an empty per-test file."""
    import tools_and_config.config_loader as config_loader
    from core.senior import care_plan as care_plan_module

    path = tmp_path / "care_plan.json"
    path.write_text(json.dumps({
        "senior": {"name": "", "language": "hi", "notes": "",
                   "health_conditions": []},
        "family_contacts": [], "reminders": [], "exercises": [],
        "approved_music": [], "approved_topics": [], "care_log": [],
        "metadata": {}, "routine_events": [], "health_measurements": [],
        "active_session": None,
    }))

    real_get_full_config = config_loader.get_full_config

    def care_isolated_config():
        cfg = real_get_full_config()
        # Copy shallowly enough to redirect the one key without mutating the
        # cached config every other consumer shares.
        cfg = dict(cfg)
        cfg["senior_mode"] = dict(cfg.get("senior_mode", {}))
        cfg["senior_mode"]["care_plan_file"] = str(path)
        return cfg

    monkeypatch.setattr(config_loader, "get_full_config", care_isolated_config)
    monkeypatch.setattr(care_plan_module, "get_full_config",
                        care_isolated_config, raising=False)

    # Drop any singleton built from the live file, and rebuild from ours.
    monkeypatch.setattr(care_plan_module, "_care_plan_instance", None,
                        raising=False)
    monkeypatch.setattr(care_plan_module, "_singleton_lock", threading.Lock(),
                        raising=False)
    yield path
    care_plan_module._care_plan_instance = None
