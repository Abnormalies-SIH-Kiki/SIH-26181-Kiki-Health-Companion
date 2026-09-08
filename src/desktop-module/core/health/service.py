"""Dedicated Raspberry Pi wearable-health HTTP service (default port 8091)."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any

from flask import Flask, jsonify, request, Response

from core.health.wearable import build_summary
from core.senior.care_plan import CarePlan, get_care_plan_path


_MAX_BODY = 64 * 1024


def _authorized() -> bool:
    token = os.getenv("KIKI_HEALTH_TOKEN", "").strip()
    if not token:
        return True
    supplied = request.headers.get("Authorization", "")
    return supplied == f"Bearer {token}"


def _notify_email(plan: CarePlan, alert: dict) -> list[dict]:
    """Use Kiki's already-connected Gmail MCP; failures are recorded, not raised."""
    outcomes = []
    contacts = plan.get_section("family_contacts") or []
    for contact in contacts:
        email = str(contact.get("email") or "").strip()
        notify_on = contact.get("notify_on") or ["alert"]
        if not email or "alert" not in notify_on:
            continue
        args = {
            "recipient": email,
            "subject": "Kiki wearable fall check-in alert",
            "body": alert["message"],
        }
        outcome = {"channel": "email", "recipient": email, "accepted": False}
        try:
            from core.self_extend import smithery_cli
            raw = smithery_cli.tool_call(
                "gmail", "Gmail_SendEmail",
                json.dumps(args, separators=(",", ":")), timeout=10,
            )
            outcome["accepted"] = not str(raw).lower().startswith("error:")
            outcome["provider_response"] = str(raw)[:600]
        except Exception as exc:
            outcome["error"] = str(exc)[:400]
        outcomes.append(outcome)
    return outcomes


def _fall_alert(plan: CarePlan, batch: dict) -> dict | None:
    fall = batch.get("fall") if isinstance(batch.get("fall"), dict) else {}
    if str(fall.get("status") or "").lower() not in {"confirmed", "escalate", "timeout"}:
        return None
    batch_id = str(batch.get("batch_id") or "")
    existing = [row for row in (plan.get_section("health_alerts") or [])
                if row.get("batch_id") == batch_id and row.get("kind") == "possible_fall"]
    if existing:
        return existing[-1]
    captured = str(batch.get("captured_at") or datetime.now().astimezone().isoformat())
    message = (
        f"Kiki wearable detected a possible fall at {captured}. The wearer did not cancel "
        "the on-device check-in. Please contact them directly. This automated notice is "
        "not an emergency-service dispatch."
    )
    contacts = plan.get_section("family_contacts") or []
    whatsapp = []
    for contact in contacts:
        notify_on = contact.get("notify_on") or ["alert"]
        recipient = contact.get("whatsapp") or contact.get("phone") or contact.get("name")
        if recipient and "alert" in notify_on:
            whatsapp.append(str(recipient))
    alert = {
        "batch_id": batch_id, "kind": "possible_fall", "status": "requested",
        "message": message, "whatsapp_recipients": whatsapp,
    }
    # Claim the idempotency key before any provider call. If the gateway loses
    # its HTTP response and retries while Gmail is still working, the retry sees
    # this row and cannot send the same alert twice.
    stored = plan.record_health_alert(alert)
    outcomes = _notify_email(plan, stored)
    for outcome in outcomes:
        plan.record_health_alert({
            "kind": "delivery_outcome", "alert_id": stored["id"],
            "batch_id": batch_id, **outcome,
            "delivery_confirmed": bool(outcome.get("delivery_confirmed", False)),
        })
    return {**stored, "email_outcomes": outcomes}


def _dashboard_html() -> str:
    return """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Kiki Wearable Health</title><style>
:root{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#09131a;color:#eaf8f4}body{margin:0;padding:28px;max-width:1100px;margin:auto}h1{font-size:clamp(28px,5vw,54px);margin:.2em 0}.sub{color:#8cb8ad}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin:24px 0}.card{background:#11242d;border:1px solid #24505a;border-radius:18px;padding:20px}.value{font-size:38px;font-weight:750;color:#73f1cb}.label{color:#9ec9bf;text-transform:uppercase;font-size:12px;letter-spacing:.12em}.wide{grid-column:1/-1}canvas{width:100%;height:180px}.advice{border-left:4px solid #ffd36a;padding-left:14px}.muted{color:#8aa49f;font-size:13px}</style></head>
<body><div class=label>SIH health · live from care_plan.json</div><h1>Wearable Kiki</h1><div class=sub id=status>Loading the latest wellness snapshot…</div>
<div class=grid><div class=card><div class=label>Heart rate</div><div class=value id=hr>—</div><div class=muted id=hrq></div></div><div class=card><div class=label>Steps today</div><div class=value id=steps>—</div></div><div class=card><div class=label>Experimental SpO2</div><div class=value id=spo2>—</div><div class=muted>Uncalibrated · never used alone for alerts</div></div><div class=card><div class=label>Wear state</div><div class=value id=worn>—</div><div class=muted id=activity></div></div><div class='card wide'><div class=label>Recent steps</div><canvas id=chart width=1000 height=180></canvas></div><div class='card wide'><div class=label>Advisories</div><div id=advisories class=advice>No current advisory.</div></div></div><div class=muted id=disclaimer></div>
<script>function chart(rows){const c=document.getElementById('chart'),x=c.getContext('2d');x.clearRect(0,0,c.width,c.height);const m=Math.max(1,...rows.map(r=>r.steps));rows.forEach((r,i)=>{const w=c.width/Math.max(1,rows.length),h=140*r.steps/m;x.fillStyle='#36cda1';x.fillRect(i*w+10,150-h,w-20,h);x.fillStyle='#9ec9bf';x.fillText(r.date.slice(5),i*w+10,172)})}async function load(){try{const r=await fetch('/api/health/v1/summary?days=7'),d=await r.json(),l=d.latest||{},h=l.heart_rate||{},s=l.spo2_experimental||{};status.textContent=l.last_seen?'Last received '+new Date(l.last_seen).toLocaleString():'Waiting for the first wearable batch';hr.textContent=h.value==null?'—':h.value+' bpm';hrq.textContent=h.quality||'';steps.textContent=(l.steps_today||0).toLocaleString();spo2.textContent=s.value==null?'—':s.value+'%';worn.textContent=l.worn?'Worn':'Off wrist';activity.textContent=l.activity||'';advisories.innerHTML=(d.advisories||[]).map(a=>'<p><b>'+a.text+'</b><br>'+a.advice+'</p>').join('')||'No current advisory.';disclaimer.textContent=d.disclaimer;chart(d.daily_steps||[])}catch(e){status.textContent='Health service unavailable: '+e}}load();setInterval(load,30000)</script></body></html>"""


def create_app(plan: CarePlan | None = None, environment_provider=None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = _MAX_BODY
    store = plan or CarePlan(get_care_plan_path())
    heartbeat_lock = threading.RLock()
    heartbeats: dict[str, dict[str, Any]] = {}
    advisory_claims: dict[str, dict[str, Any]] = {}

    @app.before_request
    def require_auth():
        if request.path == "/healthz" or _authorized():
            return None
        return jsonify({"error": "unauthorized"}), 401

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "service": "kiki-wearable-health"})

    @app.get("/")
    def dashboard():
        return Response(_dashboard_html(), mimetype="text/html")

    @app.post("/api/health/v1/ingest")
    def ingest():
        payload = request.get_json(silent=False)
        try:
            if environment_provider is not None:
                try:
                    environment = environment_provider.snapshot()
                    if environment.get("available"):
                        payload = {**payload, "environment": environment}
                except Exception:
                    pass
            result = store.record_wearable_batch(payload)
            summary = build_summary(store, 7)
            store.replace_health_advisories(summary["advisories"])
            alert = None if result.get("duplicate") else _fall_alert(store, payload)
            return jsonify({**result, "summary": summary, "alert_requested": alert})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/health/v1/environment")
    def environment():
        # Read the provider's cached snapshot; never fetch weather on a request.
        if environment_provider is None:
            return jsonify({"available": False, "state": "unavailable"})
        return jsonify(environment_provider.snapshot())

    @app.get("/api/health/v1/summary")
    def summary():
        try:
            days = int(request.args.get("days", 7))
        except ValueError:
            days = 7
        return jsonify(build_summary(store, days))

    @app.post("/api/health/v1/heartbeats")
    def heartbeat():
        payload = request.get_json(silent=True) or {}
        source = str(payload.get("source") or "unknown")[:40]
        with heartbeat_lock:
            heartbeats[source] = {**payload, "received_at": datetime.now().astimezone().isoformat()}
            return jsonify({"accepted": True, "heartbeats": dict(heartbeats)})

    @app.get("/api/health/v1/heartbeats")
    def heartbeat_status():
        with heartbeat_lock:
            return jsonify({"heartbeats": dict(heartbeats)})

    @app.post("/api/health/v1/advisories/<advisory_id>/claim")
    def claim_advisory(advisory_id):
        payload = request.get_json(silent=True) or {}
        speaker = str(payload.get("speaker") or "unknown")[:40]
        now = datetime.now().astimezone()
        with heartbeat_lock:
            existing = advisory_claims.get(advisory_id)
            if existing:
                try:
                    age = (now - datetime.fromisoformat(existing["claimed_at"])).total_seconds()
                except (KeyError, ValueError):
                    age = 999
                if age < 120 and existing.get("speaker") != speaker and not existing.get("acked"):
                    return jsonify({"claimed": False, "owner": existing.get("speaker")}), 409
            claim = {"speaker": speaker, "claimed_at": now.isoformat(), "acked": False}
            advisory_claims[advisory_id] = claim
            return jsonify({"claimed": True, **claim})

    @app.post("/api/health/v1/advisories/<advisory_id>/ack")
    def ack_advisory(advisory_id):
        payload = request.get_json(silent=True) or {}
        with heartbeat_lock:
            claim = advisory_claims.setdefault(advisory_id, {
                "speaker": str(payload.get("speaker") or "unknown")[:40],
                "claimed_at": datetime.now().astimezone().isoformat(),
            })
            claim["acked"] = True
            claim["acked_at"] = datetime.now().astimezone().isoformat()
            return jsonify({"acked": True, **claim})

    @app.post("/api/health/v1/falls")
    def falls():
        payload = request.get_json(silent=False)
        payload = {**payload, "fall": {**(payload.get("fall") or {}), "status": "confirmed"}}
        result = store.record_wearable_batch(payload)
        return jsonify({**result, "alert_requested": _fall_alert(store, payload)})

    @app.post("/api/health/v1/alert-outcomes")
    def alert_outcome():
        payload = request.get_json(silent=False)
        return jsonify(store.record_health_alert({"kind": "delivery_outcome", **payload}))

    return app


def main() -> None:
    provider = None
    try:
        from core.health.environment import get_environment_provider
        provider = get_environment_provider()
        provider.start()
    except Exception:
        provider = None
    app = create_app(environment_provider=provider)
    app.run(host=os.getenv("KIKI_HEALTH_HOST", "0.0.0.0"),
            port=int(os.getenv("KIKI_HEALTH_PORT", "8091")), threaded=True)


if __name__ == "__main__":
    main()
