"""Care tools: the plan, the wearable, and the family alert.

`get_care_plan`, `update_care_plan`, `start_care_session` and their schedule
receipt are vendored from KikiFast's `tools_and_config/tools.py`, because the
model-facing contract is the part with all the observed-failure repairs baked
in: the section and action aliases a speaking model actually reaches for, the
bare `HH:MM` schedule, the Hindi string sent where an object was required, the
refusal to let `update_care_plan` start something *now*.

Three things are this body's own:

* `measure_heart_rate` drives the MAX30102 **on the board**, over the
  WebSocket, instead of the RPi's local I2C sensor.
* `get_wearable_status` reads the telemetry the band already sends, which this
  gateway stores locally -- no Raspberry Pi and no health service on port 8091
  are required for any of this.
* `alert_family` reaches WhatsApp and email through whichever managers the
  loaded runtime has, and reports what actually happened per channel.

These tools are only ever reachable in a mode that declares the `care`
capability. `execute_care_tool` re-checks that itself rather than trusting its
caller, because the one thing worse than a care tool that does not run is a
care tool that runs in `default` mode and emails somebody's family.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .mode import care_active


def _normalize_spoken_care_schedule(value: Any) -> Any:
    """Turn the compact time shapes used by the speaking model into a schedule.

    The local tool instruction only exposes ``data:obj`` to stay prompt-light,
    so the model commonly emits ``"schedule": "19:20"`` or a separate
    ``"time": "08:00 AM"``. Both are unambiguous daily schedules and should
    not be rejected merely because the model omitted the canonical wrapper.
    Unknown shapes are returned unchanged so the strict store still rejects
    them instead of guessing.
    """
    if isinstance(value, dict) or value in (None, ""):
        return value
    text = str(value).strip()
    for fmt in ("%H:%M", "%I:%M %p", "%I %p"):
        try:
            hhmm = datetime.strptime(text.upper(), fmt).strftime("%H:%M")
            return {"kind": "daily", "value": hhmm}
        except ValueError:
            pass
    try:
        datetime.fromisoformat(text)
        return {"kind": "once", "value": text}
    except ValueError:
        return value


# What a speaking model calls the fields, versus what the care plan calls them.
#
# Observed live on 2026-09-06 23:48, health mode, the very first attempt to add
# something to an empty plan:
#
#   update_care_plan(section="exercises", action="add",
#                    key="Hand Exercises (Up/Down)",
#                    value="Perform 10 repetitions of wrist flexion...")
#
# Every part of that is a reasonable reading of "add a thing to a plan", and
# every part of it was wrong for the schema, so nothing was saved. Rejecting it
# teaches the model nothing; renaming the keys saves the routine. The person's
# own words are never rewritten -- only which field they land in.
_FIELD_SYNONYMS = {
    "title": ("title", "name", "key", "task", "activity", "label", "item"),
    "body": ("session_brief", "brief", "description", "details", "value",
             "text", "body", "instructions", "notes", "message", "objective"),
}


def _first(d: Dict[str, Any], names) -> str:
    for name in names:
        value = d.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            return "; ".join(str(item).strip() for item in value if str(item).strip())
    return ""


def _normalize_care_fields(section: str, action: str, d: Dict[str, Any]) -> Dict[str, Any]:
    """Put the model's words into the fields this section actually reads."""
    if action not in {"add", "edit"}:
        return d
    title = _first(d, _FIELD_SYNONYMS["title"])
    body = _first(d, _FIELD_SYNONYMS["body"])

    if section == "routine_event":
        if title and not d.get("title"):
            d["title"] = title
        if body and not str(d.get("session_brief", "")).strip():
            # A brief is a hand-off to the care agent, not a script. When all
            # the model gave is a one-line description, that IS the brief --
            # padding it with invented context would be worse.
            d["session_brief"] = body
    elif section == "exercise":
        if title and not d.get("name"):
            d["name"] = title
        if body and not d.get("steps"):
            d["steps"] = [body]
    elif section == "reminder":
        if not str(d.get("message", "")).strip():
            d["message"] = body or title
    elif section in {"approved_music", "approved_topics"}:
        if not str(d.get("value", "")).strip():
            d["value"] = title or body
    return d


async def update_care_plan(section: str, action: str, data: Any = None) -> str:
    """Create or edit the caregiver care plan (voice-first) and re-sync workers.

    section: routine_event | care_session | reminder | exercise | family_contact |
             approved_music | approved_topics | senior | care_log
    action:  add | edit | remove | set
    data:    JSON object with the fields for that section/action, e.g.
             reminder add -> {"category":"medicine","message":"Take BP pill","schedule":{"kind":"daily","value":"09:00"}}
             reminder edit/remove -> {"id":"ab12cd34", ...changed fields}
             exercise add -> {"name":"Morning stretch","steps":["...","..."],"schedule":{"kind":"daily","value":"08:00"},"prescribed_by":"Dr. Rao"}
             family_contact add -> {"name":"Priya","email":"p@x.com","relationship":"daughter","notify_on":["alert","daily_summary"]}
             approved_music/approved_topics add|remove -> {"value":"old bollywood"}
             senior set -> {"name":"Amma","language":"hi","health_conditions":["diabetes"]}
             care_log add -> {"kind":"note","text":"..."}
    """
    try:
        from .plan import get_care_plan_store
        from .scheduling import get_senior_care_manager
        plan = get_care_plan_store()

        section = (section or "").strip().lower()
        action = (action or "").strip().lower()
        section = {
            "reminders": "reminder",
            "exercises": "exercise",
            "family_contacts": "family_contact",
            "contacts": "family_contact",
            "profile": "senior",
            "care_logs": "care_log",
            "routine": "routine_event",
            "routines": "routine_event",
            "routine_events": "routine_event",
            "daily_routine": "routine_event",
            "session": "care_session",
            "active_session": "care_session",
        }.get(section, section)
        action = {
            "create": "add",
            "update": "edit",
            "delete": "remove",
            # "start_session"/"begin" is what a model reaches for when asked to
            # start a routine now. care_session/start is the real spelling; an
            # unrecognised action just failed, and the agent retried it until
            # it ran out of turns and its raw JSON was read aloud.
            "start_session": "start",
            "begin": "start",
            "start_now": "start",
        }.get(action, action)

        # Starting a routine on a non-session section is a different tool.
        if action == "start" and section != "care_session":
            return ("ERROR: update_care_plan only schedules care for later. To "
                    "begin a routine RIGHT NOW, call "
                    "start_care_session(routine=\"<id or part of the title>\").")

        d: Dict[str, Any] = {}
        if isinstance(data, dict):
            d = data
        elif data and str(data).strip():
            try:
                d = json.loads(data)
            except json.JSONDecodeError:
                # Compatibility for the exact live failure where the model sent
                # a spoken reminder as raw Hindi instead of an object. Keep the
                # original Unicode text for natural Hindi TTS; LCD rendering
                # already derives Hinglish through romanize_hindi_for_lcd().
                if section == "reminder" and action == "add":
                    d = {"message": str(data).strip()}
                else:
                    return ("ERROR: No change was saved. 'data' must be a JSON "
                            f"object, not {data!r}.")
        if not isinstance(d, dict):
            return "ERROR: No change was saved. 'data' must be a JSON object."

        d = _normalize_care_fields(section, action, d)

        # Normalize the small, predictable variants the speaking model uses.
        # This includes both live failures: task/time keys and a bare HH:MM
        # schedule. Original Hindi text is preserved verbatim.
        if section == "reminder":
            if not d.get("message"):
                d["message"] = d.get("task") or d.get("text") or ""
            if not d.get("schedule") and d.get("time"):
                d["schedule"] = d["time"]
            if "schedule" in d:
                d["schedule"] = _normalize_spoken_care_schedule(d["schedule"])
        elif section == "exercise" and action == "add":
            if not d.get("schedule") and d.get("time"):
                d["schedule"] = d["time"]
            if "schedule" in d:
                d["schedule"] = _normalize_spoken_care_schedule(d["schedule"])
            # "Remind me to exercise" is a reminder, not a guided routine. The
            # model used the exercise section with reminder-shaped fields in the
            # live test; route that intent to a real schedulable reminder.
            if d.get("message") and not d.get("name") and not d.get("steps"):
                d = {
                    "category": "exercise",
                    "message": d["message"],
                    "schedule": d.get("schedule"),
                    "enabled": d.get("enabled", True),
                }
                section = "reminder"
        elif section == "routine_event":
            if not d.get("schedule") and d.get("time"):
                d["schedule"] = d["time"]
            if "schedule" in d:
                d["schedule"] = _normalize_spoken_care_schedule(d["schedule"])

        allowed_actions = {
            "reminder": {"add", "edit", "remove"},
            "exercise": {"add", "edit", "remove"},
            "family_contact": {"add", "remove"},
            "approved_music": {"add", "remove"},
            "approved_topics": {"add", "remove"},
            "senior": {"set", "edit"},
            "care_log": {"add"},
            "routine_event": {"add", "edit", "remove"},
            "care_session": {"start", "advance", "adapt", "set_vision", "complete", "cancel", "decline"},
        }
        if section not in allowed_actions:
            return (f"ERROR: No change was saved. Unknown section '{section}'. Use "
                    "reminder, exercise, family_contact, approved_music, "
                    "approved_topics, senior, care_log, routine_event, or care_session.")
        if action not in allowed_actions[section]:
            return (f"ERROR: No change was saved. Action '{action}' is not valid "
                    f"for section '{section}'.")

        if section == "reminder" and action == "add":
            if not str(d.get("message", "")).strip():
                return ("NEEDS_CLARIFICATION: No reminder was saved. Ask what the "
                        "reminder should say.")
            if not d.get("schedule"):
                return ("NEEDS_CLARIFICATION: No reminder was saved. Ask what time "
                        "it should run. For a daily reminder, save schedule as "
                        "{'kind':'daily','value':'HH:MM'} after the user answers.")
        if section == "routine_event" and action == "add":
            missing = [key for key in ("title", "schedule") if not d.get(key)]
            if not d.get("session_brief") and not d.get("actions"):
                missing.append("session_brief")
            if missing:
                return ("NEEDS_CLARIFICATION: No routine event was saved. Missing: "
                        + ", ".join(missing) + ".")
        ok = True
        msg = ""
        scheduled_item_id = ""
        # A stable machine-readable result lets the orchestrating agent verify
        # the exact worker without scraping translated/human-facing prose.
        receipt_metadata: Dict[str, str] = {}

        if section == "reminder":
            if action == "add":
                item = plan.add_reminder(d.get("category", "other"), d.get("message", ""),
                                         d.get("schedule", {}), d.get("enabled", True))
                msg = f"Added {item['category']} reminder (id {item['id']})."
                scheduled_item_id = item["id"]
            elif action == "edit":
                ok = plan.edit_reminder(d.get("id", ""), **{k: v for k, v in d.items() if k != "id"})
                msg = "Reminder updated." if ok else "No reminder with that id."
            elif action == "remove":
                ok = plan.remove_reminder(d.get("id", ""))
                msg = "Reminder removed." if ok else "No reminder with that id."
        elif section == "exercise":
            if action == "add":
                item = plan.add_exercise(d.get("name", ""), d.get("steps", []),
                                         d.get("schedule"), d.get("prescribed_by", ""),
                                         d.get("enabled", True))
                msg = f"Added exercise '{item['name']}' (id {item['id']})."
                scheduled_item_id = item["id"] if item.get("schedule") else ""
            elif action == "edit":
                ok = plan.edit_exercise(d.get("id", ""), **{k: v for k, v in d.items() if k != "id"})
                msg = "Exercise updated." if ok else "No exercise with that id."
            elif action == "remove":
                ok = plan.remove_exercise(d.get("id", ""))
                msg = "Exercise removed." if ok else "No exercise with that id."
        elif section == "routine_event":
            if action == "add":
                item = plan.add_routine_event(
                    title=d.get("title", ""),
                    category=d.get("category", "other"),
                    schedule=d.get("schedule", {}),
                    actions=d.get("actions", []),
                    enabled=d.get("enabled", True),
                    source=d.get("source", "user"),
                    evidence=d.get("evidence", ""),
                    adaptation=d.get("adaptation"),
                    continuous_vision=d.get("continuous_vision", False),
                    objective=d.get("objective", ""),
                    session_brief=d.get("session_brief", ""),
                )
                scheduled_item_id = item["id"]
                receipt_metadata = {
                    "section": "routine_event",
                    "item_id": scheduled_item_id,
                }
                msg = (("Routine event already existed" if item.get("_existing")
                        else "Added routine event")
                       + f" '{item['title']}' (id {item['id']}).")
            elif action == "edit":
                event_id = d.get("id", "")
                ok = plan.edit_routine_event(
                    event_id, **{k: v for k, v in d.items() if k != "id"})
                scheduled_item_id = event_id if ok else ""
                if scheduled_item_id:
                    receipt_metadata = {
                        "section": "routine_event",
                        "item_id": scheduled_item_id,
                    }
                msg = (f"Routine event updated (id {event_id})." if ok
                       else "No routine event with that id.")
            elif action == "remove":
                ok = plan.remove_routine_event(d.get("id", ""))
                msg = "Routine event removed." if ok else "No routine event with that id."
        elif section == "care_session":
            if action == "start":
                state = plan.start_care_session(d.get("event_id", ""))
            elif action == "advance":
                state = plan.advance_care_session(d.get("response", ""))
            elif action == "adapt":
                state = plan.adapt_care_session(
                    d.get("response", ""), d.get("remaining_actions", []),
                    d.get("reason", ""))
            elif action == "set_vision":
                state = plan.set_care_session_vision(d.get("enabled"))
            elif action in {"complete", "cancel", "decline"}:
                status = {"cancel": "cancelled", "decline": "declined"}.get(
                    action, "completed")
                state = plan.finish_care_session(status, d.get("response", ""))
            msg = "Care session state: " + json.dumps(state, ensure_ascii=False)
        elif section == "family_contact":
            if action == "add":
                c = plan.add_family_contact(d.get("name", ""), d.get("email", ""),
                                            d.get("relationship", ""), d.get("notify_on"))
                msg = f"Added family contact {c['name']}."
            elif action == "remove":
                ok = plan.remove_family_contact(d.get("name", ""))
                msg = "Contact removed." if ok else "No contact with that name."
        elif section in ("approved_music", "approved_topics"):
            if action == "add":
                ok = plan.add_to_list(section, d.get("value", ""))
            elif action == "remove":
                ok = plan.remove_from_list(section, d.get("value", ""))
            msg = f"{section} updated." if ok else f"Could not update {section}."
        elif section == "senior":
            ok = plan.set_senior_profile(**d)
            msg = "Senior profile updated." if ok else "Could not update profile."
        elif section == "care_log":
            plan.add_care_log(d.get("kind", "note"), d.get("text", ""))
            msg = "Care log entry added."
        # Re-sync workers so schedule changes take effect immediately.
        sync_problem = ""
        try:
            mgr = get_senior_care_manager()
            schedule_changed = section in {"reminder", "exercise", "routine_event"}
            if schedule_changed and mgr is not None and mgr.is_active():
                mgr.sync_workers()
                if (scheduled_item_id and hasattr(mgr, "is_item_scheduled")
                        and not mgr.is_item_scheduled(scheduled_item_id)):
                    sync_problem = (
                        f"Care item {scheduled_item_id} was saved, but its worker "
                        "was not scheduled.")
        except Exception as e:
            print(f"[update_care_plan] sync failed: {e}")
            if scheduled_item_id:
                sync_problem = f"Care item was saved, but worker sync failed: {e}"

        if not ok:
            return f"ERROR: No change was saved. {msg or 'No change made.'}"
        if sync_problem:
            return f"PARTIAL: {sync_problem} Do not promise that it will trigger."
        result = f"SUCCESS: {msg or 'Care plan updated.'}"
        if receipt_metadata:
            result += ("\nRECEIPT_JSON: "
                       + json.dumps(receipt_metadata, ensure_ascii=False,
                                    separators=(",", ":")))
        return result
    except ValueError as e:
        return f"ERROR: No change was saved. {e}"
    except Exception as e:
        return f"ERROR: No change was saved. Error updating care plan: {e}"


async def get_care_plan(section: str = "") -> str:
    """Read the care plan. section: (empty)=overview | reminder | exercise | family_contact |
    approved_music | approved_topics | senior | care_log. Returns JSON."""
    try:
        from .plan import get_care_plan_store
        plan = get_care_plan_store()
        section = (section or "").strip().lower()
        alias = {"reminder": "reminders", "exercise": "exercises",
                 "family_contact": "family_contacts", "routine": "routine_events",
                 "routine_event": "routine_events", "routines": "routine_events",
                 "day": "daily_routine", "daily_routine": "daily_routine",
                 "care_session": "active_session", "session": "active_session",
                 "health": "health_trend", "heart_rate": "health_trend",
                 "health_measurements": "health_measurements"}
        if not section:
            heart_rate = plan.health_trend("heart_rate", days=7, limit=1)
            heart_rate_summary = {
                key: value for key, value in heart_rate.items() if key != "recent"}
            if heart_rate.get("recent"):
                heart_rate_summary["latest_measured_at"] = (
                    heart_rate["recent"][-1].get("measured_at"))
            overview = {
                "senior": plan.get_section("senior"),
                "reminders": plan.get_section("reminders"),
                "exercises": plan.get_section("exercises"),
                "routine_events": plan.get_section("routine_events"),
                "daily_routine": plan.daily_routine(),
                "active_session": plan.care_session_state(),
                "family_contacts": plan.get_section("family_contacts"),
                "approved_music": plan.get_section("approved_music"),
                "approved_topics": plan.get_section("approved_topics"),
                "heart_rate_trend": heart_rate_summary,
            }
            return json.dumps(overview, ensure_ascii=False, indent=2)
        key = alias.get(section, section)
        value = (plan.care_session_state() if key == "active_session"
                 else plan.daily_routine() if key == "daily_routine"
                 else plan.health_trend("heart_rate") if key == "health_trend"
                 else plan.get_section(key))
        if key == "care_log" and isinstance(value, list):
            value = value[-30:]  # only recent entries
        if value is None:
            return f"No such section '{section}'."
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading care plan: {e}"


async def start_care_session(routine: str = "") -> str:
    """Begin a care routine right now rather than scheduling it for later."""
    try:
        from .scheduling import start_care_session_now
        return start_care_session_now(str(routine or "").strip())
    except Exception as e:
        return f"CARE_ACTION_FAILED: could not start the care session: {e}"


async def get_care_schedule_status(item_id: str = "") -> str:
    """Return a verified worker receipt with the exact next trigger time."""
    try:
        from .scheduling import get_senior_care_manager
        manager = get_senior_care_manager()
        if manager is None:
            return json.dumps({
                "status": "inactive", "item_id": item_id,
                "scheduled": False,
                "reason": "senior care manager is not initialized",
            })
        receipt = manager.schedule_receipt(str(item_id or "").strip())
        return json.dumps(receipt, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error", "item_id": item_id, "scheduled": False,
            "reason": str(e),
        }, ensure_ascii=False)


# ==========================================================================
# This body's own tools
# ==========================================================================

async def measure_heart_rate(action: str = "capture", context: str = "",
                             days: int = 7) -> str:
    """Run, cancel, inspect or trend a MAX30102 reading taken on the band.

    `action`: capture | status | cancel | trend.

    The RPi splits this into prepare/capture because its sensor needs a finger
    placed on it and a person told when to do that. The band is already on the
    wrist, and the firmware runs contact detection, stillness gating and the
    capture window itself -- so there is one call, and the states it reports
    back (waiting for contact, waiting for stillness, measuring) are what the
    care agent narrates while it waits.
    """
    from .heart_rate import get_heart_rate_controller
    from .plan import get_care_plan_store

    action = str(action or "capture").strip().lower()
    action = {"start": "capture", "measure": "capture", "read": "capture",
              "history": "trend"}.get(action, action)
    if action not in {"capture", "status", "cancel", "trend"}:
        return json.dumps({"status": "error", "reason": "invalid_action",
                           "allowed_actions": ["capture", "status", "cancel",
                                               "trend"]})
    plan = get_care_plan_store()
    controller = get_heart_rate_controller()

    if action == "trend":
        return json.dumps(plan.health_trend("heart_rate", days=days),
                          ensure_ascii=False)
    if action == "status":
        return json.dumps(controller.status(), ensure_ascii=False)
    if action == "cancel":
        return json.dumps(controller.cancel(), ensure_ascii=False)

    result = await asyncio.to_thread(controller.measure)
    if result.get("status") == "trusted_reading":
        session = plan.care_session_state()
        entry = plan.add_health_measurement(
            measurement="heart_rate", value=result["bpm"], unit="bpm",
            quality=result.get("quality", "FAIR"),
            site=result.get("site", "wrist"), context=context,
            signal=result.get("signal", {}),
            routine_event_id=session.get("event_id", ""),
            session_id=session.get("id", ""))
        result["record_id"] = entry["id"]
        result["measured_at"] = entry["measured_at"]
        result["trend"] = plan.health_trend("heart_rate", days=days, limit=7)
        plan.add_care_log(
            "heart_rate",
            f"Trusted reading recorded: {result['bpm']} bpm "
            f"({result.get('quality')}, on the wrist).")
    else:
        # Everything that is not a trusted reading is an ATTEMPT. It is logged
        # as one, it never reaches health_measurements, and it must never be
        # spoken as a number.
        plan.add_care_log(
            "heart_rate_attempt",
            "Heart-rate attempt was not recorded as a trusted reading: "
            + str(result.get("reason") or result.get("status")))
        result["speak"] = ("Say the measurement did not work and why. Do NOT "
                           "state a heart rate: there is no reading.")
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


async def get_wearable_status() -> str:
    """What the band has actually reported: steps, wear, last trusted rate.

    Read from the local store the telemetry already lands in, so it answers
    with the Raspberry Pi switched off.
    """
    from .plan import get_care_plan_store
    from .wearable_summary import build_summary

    try:
        return json.dumps(build_summary(get_care_plan_store()),
                          ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"status": "error", "reason": str(exc)[:200]})


async def alert_family(reason: str, urgency: str = "normal") -> str:
    """Alert the family by WhatsApp and email, and report what really happened."""
    from .alerts import alert_family as _alert

    return await asyncio.to_thread(_alert, reason, urgency)


# ==========================================================================
# Registry and dispatch
# ==========================================================================

_HANDLERS = {
    "get_care_plan": get_care_plan,
    "update_care_plan": update_care_plan,
    "start_care_session": start_care_session,
    "get_care_schedule_status": get_care_schedule_status,
    "measure_heart_rate": measure_heart_rate,
    "get_wearable_status": get_wearable_status,
    "alert_family": alert_family,
}

CARE_TOOL_NAMES = frozenset(_HANDLERS)


def is_care_tool(name: str) -> bool:
    return str(name or "") in _HANDLERS


# Schemas for the speaking model. Short on purpose: this text is part of the
# warm KV-cache prefix, so every character is paid for on every single turn.
CARE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_care_plan",
            "description": ("Health mode: read the care plan (routines, "
                            "medicines, contacts, vitals) to confirm before "
                            "answering."),
            "parameters": {
                "type": "object",
                "properties": {"section": {"type": "string", "description":
                                           "Empty for an overview."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_care_plan",
            "description": ("Health mode: edit the care plan. data MUST be an "
                            "object. Schedules a routine for LATER, never now."),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "action": {"type": "string",
                               "enum": ["add", "edit", "remove", "set"]},
                    "data": {"type": "object"},
                },
                "required": ["section", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_care_session",
            "description": ("Start a guided exercise, check-in or company "
                            "session RIGHT NOW and hand the conversation over "
                            "to it. Output ONLY this."),
            "parameters": {
                "type": "object",
                "properties": {"routine": {"type": "string", "description":
                                           "Part of the routine's title, or empty."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "measure_heart_rate",
            "description": ("Measure heart rate on the band's sensor. Never "
                            "state a rate this did not return."),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["capture", "status", "cancel", "trend"]},
                    "context": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_wearable_status",
            "description": "Steps, wear state and the last trusted heart rate.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "alert_family",
            "description": ("Alert family/caregivers by WhatsApp and email on "
                            "distress, a fall, or a request for help."),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["normal", "urgent"]},
                },
                "required": ["reason"],
            },
        },
    },
]


def care_tool_catalog_lines() -> List[str]:
    """One line per care tool, for the care agent's prompt."""
    lines = []
    for schema in CARE_TOOL_SCHEMAS:
        fn = schema["function"]
        props = fn.get("parameters", {}).get("properties", {})
        required = set(fn.get("parameters", {}).get("required", []))
        args = []
        for key, spec in props.items():
            label = key if key in required else f"{key}?"
            if spec.get("enum"):
                label += "=" + "|".join(str(value) for value in spec["enum"])
            args.append(label)
        lines.append(f"- {fn['name']}({', '.join(args)}): {fn['description']}")
    return lines


# "Schedule my medicine for 3pm" handed to `complex_query`, which on this body
# cannot touch the care plan: the action agent's catalogue has no care tools, so
# it reaches for `schedule_worker` and creates a background task instead. Kiki
# then says it is scheduled, the watch shows nothing, and no care session will
# ever run. Observed live on 2026-09-07 at 00:05.
#
# The prompt now tells the model to call `update_care_plan` itself. This is the
# half that does not depend on the model reading it.
# NOT a bare "plan": "what is in my care plan" is a question about the plan,
# and matching the noun as a verb sent it down the scheduling path.
_CARE_VERB = (r"schedul|remind|set\s+up|add|create|book|"
              r"plan\s+(?:a|an|my|the)|"
              r"शेड्यूल|याद|जोड़|लगा")
_CARE_NOUN = (r"medicine|medication|tablet|pill|dose|drug|"
              r"exercise|stretch|walk|workout|yoga|"
              r"water|hydrat|meal|breakfast|lunch|dinner|food|"
              r"care\s*plan|routine|check[-\s]?in|sleep|nap|"
              r"दवा|गोली|कसरत|व्यायाम|पानी|खाना|रूटीन")
_CARE_SCHEDULE_RE = re.compile(
    rf"(?=.*\b(?:{_CARE_VERB}))(?=.*(?:{_CARE_NOUN}))", re.IGNORECASE | re.DOTALL)


def care_schedule_request(text: str) -> bool:
    """Is this "put something in the care plan", however it was phrased?"""
    return bool(_CARE_SCHEDULE_RE.search(str(text or "")))


# Reading THIS PERSON's own health data. Also a care tool, for the same reason:
# `complex_query` has no access to the plan or the wearable store on this body,
# so it would answer from the conversation or from general knowledge -- which is
# a made-up number wearing the shape of a measurement.
#
# Deliberately requires a self-reference ("my", "मेरा"). "What is a normal
# resting heart rate" is a research question and belongs to `complex_query`;
# "what is MY resting heart rate" is a reading and belongs here.
_SELF_RE = re.compile(r"\b(my|mine|i|i've|am|do|did|have)\s*i?\b|\bmy\b|मेर|मुझ",
                      re.IGNORECASE)
_CARE_DATA_NOUN = re.compile(
    r"care\s*plan|routine|schedule|medicine|medication|tablet|pill|dose|"
    r"heart\s*rate|pulse|bpm|spo2|oxygen|steps|step\s*count|"
    r"vitals?|reading|measurement|"
    r"दवा|गोली|धड़कन|कदम|रूटीन",
    re.IGNORECASE)


# "What is in my care plan" contains a scheduling word in some phrasings, but
# it is a question about what already exists. An interrogative opener settles
# it: asking beats adding.
_QUESTION_RE = re.compile(
    r"^\s*(what|which|when|how|is|are|do|does|did|show|tell|read|list|"
    r"क्या|कब|कितन)", re.IGNORECASE)


def care_data_request(text: str) -> bool:
    """Is this "tell me about MY health data"?"""
    text = str(text or "")
    return bool(_SELF_RE.search(text) and _CARE_DATA_NOUN.search(text))


def redirect_complex_query(arguments: Dict[str, Any]) -> Optional[str]:
    """Refuse a care-plan read or write sent to `complex_query`.

    Returns None for every other request, so WhatsApp, search, email, research
    and the rest of `complex_query` are untouched -- including general health
    questions, which are exactly what it is good at. The line is whose data it
    is: this person's plan and this person's readings live in tools that
    `complex_query` cannot reach on this body.
    """
    if not care_active():
        return None
    request = ""
    if isinstance(arguments, dict):
        request = str(arguments.get("request") or arguments.get("query") or "")
    asking = bool(_QUESTION_RE.search(request))
    if care_data_request(request) and (asking or not care_schedule_request(request)):
        return (
            "REDIRECTED: nothing was read. complex_query cannot see this "
            "person's care plan or wearable data on this device, so it would "
            "answer from memory rather than from a measurement. Use "
            "get_care_plan() for the plan and routines, get_wearable_status() "
            "for steps, wear and the last trusted heart rate, or "
            "measure_heart_rate(action=\"capture\") to take a new reading. "
            "General health questions that are not about this person's own "
            "data are fine to answer normally."
        )
    if not care_schedule_request(request):
        return None
    return (
        "REDIRECTED: nothing was scheduled. complex_query cannot edit the care "
        "plan on this device -- it would create a background worker, which does "
        "not appear in the care plan, does not show on the watch, and never "
        "runs a care session. Call update_care_plan yourself instead, for "
        "example: update_care_plan(section=\"reminder\", action=\"add\", "
        "data={\"category\":\"medicine\",\"message\":\"<what to take>\","
        "\"schedule\":{\"kind\":\"daily\",\"value\":\"HH:MM\"}}). "
        "Ask for the time if you were not given one."
    )


def execute_care_tool(name: str, arguments: Dict[str, Any]) -> str:
    """Run one care tool synchronously. Refuses outside a care-capable mode.

    The capability is re-checked here rather than trusted from the caller. Both
    call sites (the speaking model's tool loop and the care agent's own tool
    executor) are reached by model output, and a model naming a tool it was
    never offered is not hypothetical -- the legacy runtime has a comment about
    a roleplay mode emitting `recall_memory` and being handed somebody's
    private memories.
    """
    handler = _HANDLERS.get(str(name or ""))
    if handler is None:
        return f"BLOCKED: {name!r} is not a care tool."
    if not care_active():
        return (f"BLOCKED: {name} is only available in health mode. "
                f"Nothing was read, changed or sent.")
    arguments = _fold_extra_arguments(handler, arguments)
    try:
        return str(_run(handler(**arguments)))
    except TypeError as exc:
        # Instructive, not just accurate. A model that gets "unexpected keyword
        # argument" back has no idea what the right call looks like and simply
        # tries again the same way until it runs out of turns -- which is how a
        # raw tool-call JSON blob ends up being read aloud.
        return (f"ERROR: {name} was called with the wrong arguments ({exc}). "
                f"Nothing was changed. Correct shape: "
                f"{_CALL_HINTS.get(name, 'see the tool list')}")
    except Exception as exc:
        return f"ERROR: {name} failed: {str(exc)[:300]}"


# What to tell a model that called a care tool wrongly. Short: these are read
# by the model mid-turn, not by a person.
_CALL_HINTS = {
    "update_care_plan": (
        'update_care_plan(section="routine_event", action="add", '
        'data={"title":"...","category":"exercise",'
        '"schedule":{"kind":"daily","value":"HH:MM"},"session_brief":"..."})'),
    "get_care_plan": 'get_care_plan(section="") for everything',
    "start_care_session": 'start_care_session(routine="part of the title")',
    "measure_heart_rate": 'measure_heart_rate(action="capture")',
    "alert_family": 'alert_family(reason="...", urgency="normal")',
}

# Keys a speaking model reaches for when it means "the fields of the thing".
# Observed live on 2026-09-06: the model called
#   update_care_plan(section="exercises", action="add",
#                    key="Hand Exercises (Up/Down)", value="Perform 10 reps...")
# which is a perfectly reasonable reading of "add a thing to a plan" and was
# rejected with a TypeError. Folding stray keys into `data` costs nothing and
# turns a dead end into a save.
_DATA_ALIASES = ("data", "fields", "item", "payload", "value", "values")


def _fold_extra_arguments(handler, arguments) -> Dict[str, Any]:
    """Move arguments the handler does not declare into its `data` parameter."""
    import inspect

    arguments = dict(arguments) if isinstance(arguments, dict) else {}
    try:
        accepted = set(inspect.signature(handler).parameters)
    except (TypeError, ValueError):
        return arguments
    if "data" not in accepted:
        # Nothing to fold into; drop unknown keys rather than raising, so a
        # stray argument cannot cost a measurement or an alert.
        return {k: v for k, v in arguments.items() if k in accepted}

    data = arguments.get("data")
    if isinstance(data, str) and data.strip():
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {"message": data.strip()}
    if not isinstance(data, dict):
        data = {}

    extras = {k: v for k, v in arguments.items() if k not in accepted}
    for key, value in extras.items():
        if key in _DATA_ALIASES and isinstance(value, dict):
            data.update(value)
        else:
            data.setdefault(key, value)
    folded = {k: v for k, v in arguments.items() if k in accepted}
    if data:
        folded["data"] = data
    return folded


def _run(coro) -> Any:
    """Await `coro` from a synchronous caller, loop or no loop.

    The speaking model's tool loop runs on a worker thread with no event loop;
    the care agent's executor can be called from inside one. Both have to work,
    so the running-loop case gets its own thread rather than deadlocking on
    `run_until_complete`.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
