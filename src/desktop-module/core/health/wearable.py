"""Deterministic wearable summaries and advisory analytics.

This module intentionally contains no model calls.  It turns persisted Kiki
wearable batches into bounded facts for the dashboard, CARE NOW, and the
desktop gateway's existing live-context row.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import statistics
from typing import Any, Dict, Iterable, List

from core.health.signal_quality import wearable_heart_is_coherent


def _when(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        return parsed if parsed.tzinfo else parsed.astimezone()
    except (TypeError, ValueError):
        return None


def _recent(rows: Iterable[dict], days: int) -> List[dict]:
    cutoff = datetime.now().astimezone() - timedelta(days=max(1, min(90, days)))
    result = []
    for row in rows:
        captured = _when(row.get("captured_at"))
        if captured and captured >= cutoff:
            result.append(row)
    return sorted(result, key=lambda row: row.get("captured_at", ""))


def _heart_stats(measurements: Iterable[dict], days: int) -> Dict[str, Any]:
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    rows = []
    for row in measurements:
        if row.get("measurement") != "heart_rate" or row.get("quality") not in {"GOOD", "FAIR"}:
            continue
        if row.get("source") == "wearable_kiki" and not wearable_heart_is_coherent(row):
            continue
        measured = _when(row.get("measured_at"))
        if measured and measured >= cutoff:
            rows.append(row)
    values = [float(row["value"]) for row in rows]
    output: Dict[str, Any] = {"count": len(values), "recent": rows[-60:]}
    if values:
        ordered = sorted(values)
        p10_index = max(0, int((len(ordered) - 1) * 0.10))
        output.update({
            "latest": round(values[-1], 1),
            "median": round(statistics.median(values), 1),
            "minimum": round(min(values), 1),
            "maximum": round(max(values), 1),
            "baseline_p10": round(ordered[p10_index], 1),
            "days_with_readings": len({
                _when(row.get("measured_at")).date().isoformat()
                for row in rows if _when(row.get("measured_at"))
            }),
        })
    return output


def _step_days(events: Iterable[dict], days: int) -> List[dict]:
    totals: dict[str, int] = defaultdict(int)
    activity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in _recent(events, days):
        captured = _when(row.get("captured_at"))
        if not captured:
            continue
        day = captured.astimezone().date().isoformat()
        delta = max(0, int(row.get("steps_delta", 0) or 0))
        totals[day] += delta
        activity[day][str(row.get("activity") or "unknown")] += delta
    return [{"date": day, "steps": totals[day], "activity_steps": dict(activity[day])}
            for day in sorted(totals)]


def _environment_correlations(events: Iterable[dict], days: int) -> Dict[str, Any]:
    """Descriptive AQI/heat comparisons; no causal or medical claims."""
    daily: dict[str, dict] = {}
    for row in _recent(events, days):
        captured = _when(row.get("captured_at"))
        environment = row.get("environment") if isinstance(row.get("environment"), dict) else {}
        if not captured or not environment.get("available"):
            continue
        day = captured.astimezone().date().isoformat()
        target = daily.setdefault(day, {"steps": 0, "aqi_category": None,
                                        "apparent_temperature_c": None})
        target["steps"] += max(0, int(row.get("steps_delta", 0) or 0))
        target["aqi_category"] = environment.get("aqi_category")
        target["apparent_temperature_c"] = environment.get("apparent_temperature_c")
    groups: dict[str, list[int]] = defaultdict(list)
    for row in daily.values():
        if row.get("aqi_category"):
            groups[str(row["aqi_category"])].append(row["steps"])
    comparisons = {
        category: {"days": len(values), "median_steps": round(statistics.median(values))}
        for category, values in groups.items() if len(values) >= 2
    }
    return {
        "days_with_environment": len(daily),
        "steps_by_aqi_category": comparisons,
        "note": "Descriptive association only; it does not show that air quality caused activity changes.",
    }


def build_advisories(snapshot: dict, heart: dict, step_days: List[dict]) -> List[dict]:
    """Return conservative observations, never diagnoses or emergency claims."""
    now = str(snapshot.get("captured_at") or datetime.now().astimezone().isoformat())
    advisories: List[dict] = []
    if heart.get("count", 0) >= 12 and heart.get("days_with_readings", 0) >= 3:
        latest = float(heart.get("latest", 0))
        baseline = float(heart.get("baseline_p10", latest))
        if latest >= baseline + 15 and snapshot.get("activity") in {"still", "resting", "recovery"}:
            advisories.append({
                "id": "resting_hr_above_baseline",
                "severity": "notice",
                "created_at": now,
                "text": f"Resting heart rate is {round(latest - baseline)} bpm above the recent personal baseline.",
                "advice": "Pause, rest, and recheck when comfortable. This is a wellness observation, not a diagnosis.",
            })
        environment = snapshot.get("environment") if isinstance(snapshot.get("environment"), dict) else {}
        if (environment.get("heat_band") in {"caution", "high", "very high", "extreme"}
                and (latest >= baseline + 15 or snapshot.get("activity") in {"walking", "active", "exercise"})):
            advisories.append({
                "id": "heat_activity_attention",
                "severity": "notice",
                "created_at": now,
                "text": "Heat and current body activity suggest taking extra care outdoors.",
                "advice": "Consider shade, a comfortable pause, and normal hydration guidance. No dehydration claim is being made.",
            })
    if len(step_days) >= 3:
        prior = [row["steps"] for row in step_days[:-1] if row["steps"] > 0]
        today = step_days[-1]["steps"]
        if len(prior) >= 2 and today < statistics.median(prior) * 0.35:
            advisories.append({
                "id": "activity_below_recent_pattern",
                "severity": "info",
                "created_at": now,
                "text": "Today's movement is below the recent personal pattern.",
                "advice": "If it feels right, consider a gentle walk or prescribed movement routine.",
            })
    battery = snapshot.get("battery_percent")
    if isinstance(battery, (int, float)) and battery <= 15:
        advisories.append({
            "id": "wearable_low_battery", "severity": "device",
            "created_at": now, "text": "The wearable battery is low.",
            "advice": "Charge wearable Kiki so health tracking can continue.",
        })
    return advisories


def compact_context(snapshot: dict, advisories: List[dict]) -> str:
    if not snapshot:
        return ""
    parts = []
    heart = snapshot.get("heart_rate") or {}
    if heart.get("value") is not None:
        parts.append(f"HR {heart['value']:g} bpm ({str(heart.get('quality', 'unknown')).lower()})")
    parts.append(f"{int(snapshot.get('steps_today', 0) or 0):,} steps today")
    parts.append("worn" if snapshot.get("worn") else "not worn")
    activity = str(snapshot.get("activity") or "").strip().lower()
    if activity and activity != "unknown":
        parts.append(activity)
    fall = snapshot.get("fall") if isinstance(snapshot.get("fall"), dict) else {}
    fall_at = _when(fall.get("captured_at"))
    if fall_at and datetime.now().astimezone() - fall_at > timedelta(hours=1):
        fall = {}
    fall_status = str(fall.get("status") or "").lower()
    if fall_status == "pending":
        parts.append("possible-fall check pending")
    elif fall_status in {"confirmed", "timeout", "escalate"}:
        parts.append("possible-fall family alert requested")
    elif fall_status == "cancelled":
        parts.append("fall check cancelled by wearer")
    spo2 = snapshot.get("spo2_experimental") or {}
    if spo2.get("value") is not None:
        parts.append(f"experimental uncalibrated SpO2 {spo2['value']:g}%")
    if advisories:
        parts.append(f"advisory: {advisories[0].get('text', '')}")
    return "Wearable: " + "; ".join(parts) + "."


def build_summary(plan, days: int = 7) -> Dict[str, Any]:
    days = max(1, min(90, int(days or 7)))
    snapshot = plan.get_section("wearable_health") or {}
    last_seen = _when(snapshot.get("last_seen"))
    fresh = bool(last_seen and datetime.now().astimezone() - last_seen <= timedelta(hours=12))
    if snapshot:
        snapshot = {**snapshot, "fresh": fresh}
        # Older firmware briefly persisted POOR optical estimates into the
        # latest snapshot (though never into trusted trends). Sanitize on read
        # as well as on ingest so an already-stored bad BPM/SpO2 can never be
        # shown on the dashboard or injected into either Kiki's prompt.
        heart = snapshot.get("heart_rate") if isinstance(
            snapshot.get("heart_rate"), dict) else {}
        if (str(heart.get("quality") or "").upper() not in {"GOOD", "FAIR"}
                or not wearable_heart_is_coherent(heart)):
            snapshot.pop("heart_rate", None)
            snapshot.pop("spo2_experimental", None)
    fall = snapshot.get("fall") if isinstance(snapshot.get("fall"), dict) else {}
    fall_at = _when(fall.get("captured_at"))
    if fall_at and datetime.now().astimezone() - fall_at > timedelta(hours=1):
        snapshot = {**snapshot, "fall": {}}
    events = plan.get_section("wearable_events") or []
    measurements = plan.get_section("health_measurements") or []
    heart = _heart_stats(measurements, days)
    daily_steps = _step_days(events, days)
    environment = _environment_correlations(events, days)
    advisories = build_advisories(snapshot, heart, daily_steps)
    experimental_spo2 = []
    for row in _recent(events, days):
        event_heart = (row.get("readings") or {}).get("heart_rate") or {}
        if (str(event_heart.get("quality") or "").upper() not in {"GOOD", "FAIR"}
                or not wearable_heart_is_coherent(event_heart)):
            continue
        reading = (row.get("readings") or {}).get("spo2") or {}
        if reading.get("value") is not None:
            experimental_spo2.append({
                "measured_at": row.get("captured_at"),
                "value": reading.get("value"), "unit": "%",
                "calibrated": False,
            })
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "period_days": days,
        "latest": snapshot,
        "heart_rate": heart,
        "daily_steps": daily_steps,
        "environment_correlations": environment,
        "spo2_experimental": experimental_spo2[-60:],
        "advisories": advisories,
        "context": (compact_context(snapshot, advisories) if fresh else
                    ("Wearable: no current reading; last wearable update is stale."
                     if snapshot else "")),
        "disclaimer": "Wearable readings are wellness information, not a diagnosis. SpO2 is experimental and uncalibrated.",
    }
