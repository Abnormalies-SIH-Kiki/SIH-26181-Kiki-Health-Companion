"""Reaching the family, and saying honestly whether it worked.

Two channels, tried independently: WhatsApp through the runtime's MCP bridge,
and email through its configured Gmail tool. Neither is required for the other
to run, and a failure in one is reported as a failure in one.

The rule this module exists for: **prose is not a delivery receipt.** A tool
that returns "Message sent!" has told you a string, not an outcome. So every
result here is one of three explicit states -- `delivered` (a receipt exists),
`accepted` (handed to a service that took it, no receipt available), or
`failed` -- and the spoken summary the care agent gets back says which. Kiki
telling someone their family has been informed when nothing left the machine is
the single worst thing this feature could do.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional


LOG = logging.getLogger(__name__)

DELIVERED = "delivered"
ACCEPTED = "accepted"
FAILED = "failed"

# Substrings that mean a tool did NOT do the thing. Deliberately generous: an
# ambiguous result is treated as a failure, because the cost of over-reporting
# an alert is a person believing help is coming when it is not.
_FAILURE_MARKERS = (
    "error", "failed", "failure", "not sent", "could not", "couldn't",
    "unavailable", "unauthorized", "unauthenticated", "logged out", "timeout",
    "timed out", "no such tool", "traceback", "exception", "refused",
    "not configured", "missing",
)

_RECEIPT_MARKERS = ("message_id", "messageid", "id:", "delivered", "sent to",
                    "\"id\"")


def _classify(result: Any) -> tuple[str, str]:
    """(state, detail) for one channel's raw tool result."""
    text = ("" if result is None else str(result)).strip()
    if not text:
        return FAILED, "the channel returned nothing at all"
    lowered = text.lower()
    if any(marker in lowered for marker in _FAILURE_MARKERS):
        return FAILED, text[:300]
    if any(marker in lowered for marker in _RECEIPT_MARKERS):
        return DELIVERED, text[:300]
    return ACCEPTED, text[:300]


def _runtime_tool(name: str, args: Dict[str, Any]) -> Any:
    """Call one tool on whichever Kiki runtime is loaded, or raise."""
    from tools_and_config.tools import execute_tool

    return execute_tool(name, args)


def _send_email(to: str, subject: str, body: str) -> Dict[str, Any]:
    for tool in ("send_care_email", "send_email"):
        try:
            raw = _runtime_tool(tool, {"to": to, "subject": subject, "body": body})
        except Exception as exc:
            LOG.info("email tool %s unavailable: %s", tool, exc)
            continue
        state, detail = _classify(raw)
        return {"channel": "email", "to": to, "state": state, "detail": detail,
                "tool": tool}
    return {"channel": "email", "to": to, "state": FAILED,
            "detail": "no email tool is configured in this runtime"}


def _send_whatsapp(recipient: str, message: str) -> Dict[str, Any]:
    for tool in ("send_message", "send_whatsapp_message"):
        try:
            raw = _runtime_tool(tool, {"recipient": recipient, "message": message})
        except Exception as exc:
            LOG.info("whatsapp tool %s unavailable: %s", tool, exc)
            continue
        state, detail = _classify(raw)
        return {"channel": "whatsapp", "to": recipient, "state": state,
                "detail": detail, "tool": tool}
    return {"channel": "whatsapp", "to": recipient, "state": FAILED,
            "detail": "the WhatsApp bridge is not available in this runtime"}


def alert_family(reason: str, urgency: str = "normal") -> str:
    """Tell the family something. Returns a truthful, per-channel summary.

    The care log entry is written FIRST and unconditionally. If every channel
    fails, the concern is still recorded and Kiki still says out loud that
    nobody could be reached -- an alert that vanishes because the network was
    down is worse than one that was never attempted.
    """
    from .plan import get_care_plan_store

    reason = str(reason or "").strip()
    urgency = str(urgency or "normal").strip().lower()
    if urgency not in {"normal", "urgent"}:
        urgency = "normal"
    if not reason:
        return "CARE_ACTION_FAILED: an alert needs a reason. Nothing was sent."

    plan = get_care_plan_store()
    plan.add_care_log("alert", f"[{urgency}] {reason}")
    contacts = plan.contacts_for("alert")
    person = plan.get_section("senior").get("name") or "the person Kiki looks after"

    if not contacts:
        return ("NOT SENT: there are no family contacts with alerts enabled. "
                "The concern is written into the care log, but nobody was "
                "contacted. Say this plainly and offer to add a contact.")

    prefix = "URGENT: " if urgency == "urgent" else ""
    subject = f"{prefix}Kiki alert about {person}"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = (f"Kiki is flagging something about {person}:\n\n{reason}\n\n"
            f"Urgency: {urgency}. Time: {stamp}.")
    short = f"{prefix}Kiki about {person}: {reason} ({stamp})"

    outcomes: List[Dict[str, Any]] = []
    for contact in contacts:
        name = contact.get("name") or contact.get("email") or "contact"
        if contact.get("email"):
            outcome = _send_email(contact["email"], subject, body)
            outcome["name"] = name
            outcomes.append(outcome)
        phone = contact.get("phone") or contact.get("whatsapp")
        if phone:
            outcome = _send_whatsapp(str(phone), short)
            outcome["name"] = name
            outcomes.append(outcome)

    if not outcomes:
        return ("NOT SENT: the family contacts have no email address or phone "
                "number on file. The concern is in the care log only.")

    try:
        plan.add_care_log("alert_outcome", json.dumps(outcomes, ensure_ascii=False)[:900])
    except Exception:
        LOG.exception("could not record alert outcomes")

    reached = [o for o in outcomes if o["state"] in {DELIVERED, ACCEPTED}]
    failed = [o for o in outcomes if o["state"] == FAILED]
    parts = [f"{o['name']} by {o['channel']}: {o['state']}" for o in outcomes]
    if not reached:
        return ("ALERT FAILED: no channel accepted the message. "
                + " | ".join(parts)
                + " Tell them honestly that you could not reach anyone, and "
                  "suggest calling directly.")
    if failed:
        return ("PARTIAL: some channels worked and some did not. "
                + " | ".join(parts)
                + " Say which ones actually went through.")
    return ("SENT: " + " | ".join(parts)
            + ". 'accepted' means the service took the message; only "
              "'delivered' means a receipt came back.")
