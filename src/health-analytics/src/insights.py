"""
insights.py
------------
Assembles everything the pipeline has computed (daily summary, trends,
anomalies, risk score) into one structured JSON object per user, per day.
This is the contract between this telemetry/analytics layer and "Kiki"
(the consumer-facing assistant) -- Kiki should never need to touch raw
telemetry, only this insight object.

Schema (see build_insight() docstring) is intentionally flat-ish and
explicit so it's trivial to prompt an LLM with or render directly in a UI.
"""

import json
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/


def build_insight(user_id: str, daily_summary: pd.DataFrame, trends: pd.DataFrame,
                   risk_scores: pd.DataFrame, flagged: pd.DataFrame) -> dict:
    """
    Returns:
    {
      "user_id": str,
      "date": "YYYY-MM-DD",
      "summary": {total_steps, active_minutes, avg_hr, resting_hr_est, max_hr, min_hr},
      "baseline_comparison": {resting_hr_vs_recent_avg, steps_vs_recent_avg},
      "anomalies": {count_today, recent_examples: [...]},
      "trends": {resting_hr_trend, steps_trend, active_minutes_trend},
      "risk": {score, level, reasons: [...]},
      "narrative": str   # one human-readable sentence Kiki can say/display directly
    }
    """
    day_rows = daily_summary[daily_summary["user_id"] == user_id].sort_values("date")
    if day_rows.empty:
        return {}
    today = day_rows.iloc[-1]
    recent = day_rows.tail(7)

    trend_row = trends[trends["user_id"] == user_id]
    trend_row = trend_row.iloc[0] if not trend_row.empty else {}

    risk_row = risk_scores[risk_scores["user_id"] == user_id]
    risk_row = risk_row.iloc[0] if not risk_row.empty else {}

    day_flags = flagged[(flagged["user_id"] == user_id) &
                         (pd.to_datetime(flagged["timestamp"]).dt.date.astype(str) == today["date"])]
    anomaly_events = day_flags[day_flags.get("any_anomaly", False) == True]
    examples = anomaly_events[["timestamp", "heart_rate", "deviation_type"]].tail(5).to_dict("records")

    insight = {
        "user_id": user_id,
        "date": today["date"],
        "summary": {
            "total_steps": int(today["total_steps"]),
            "active_minutes": int(today["active_minutes"]),
            "avg_hr": float(today["avg_hr"]),
            "resting_hr_est": float(today["resting_hr_est"]),
            "max_hr": float(today["max_hr"]),
            "min_hr": float(today["min_hr"]),
        },
        "baseline_comparison": {
            "resting_hr_vs_7d_avg": round(float(today["resting_hr_est"] - recent["resting_hr_est"].mean()), 1),
            "steps_vs_7d_avg": round(float(today["total_steps"] - recent["total_steps"].mean()), 1),
        },
        "anomalies": {
            "count_today": int(today["confirmed_anomaly_minutes"]),
            "recent_examples": examples,
        },
        "trends": {
            "resting_hr_trend": trend_row.get("resting_hr_est_trend", "unknown"),
            "steps_trend": trend_row.get("total_steps_trend", "unknown"),
            "active_minutes_trend": trend_row.get("active_minutes_trend", "unknown"),
        },
        "risk": {
            "score": float(risk_row.get("risk_score", 0.0)),
            "level": risk_row.get("risk_level", "unknown"),
            "reasons": risk_row.get("reasons", []),
        },
    }

    insight["narrative"] = _narrative(insight)
    return insight


def _narrative(insight: dict) -> str:
    r = insight["risk"]
    trends = insight["trends"]
    bits = [f"Risk level today: {r['level']} (score {r['score']:.0f}/100)."]
    if r["reasons"]:
        bits.append("Contributing factors: " + "; ".join(r["reasons"]) + ".")
    if trends["resting_hr_trend"] != "stable":
        bits.append(f"Resting heart rate has been {trends['resting_hr_trend']} recently.")
    if trends["steps_trend"] == "falling":
        bits.append("Daily activity has been trending down.")
    return " ".join(bits)


if __name__ == "__main__":
    base = BASE_DIR / "data"
    daily_summary = pd.read_csv(base / "daily_summary.csv")
    trends = pd.read_csv(base / "trends.csv")
    risk_scores = pd.read_json(base / "risk_scores.json")
    flagged = pd.read_csv(base / "telemetry_flagged.csv")

    all_insights = [build_insight(u, daily_summary, trends, risk_scores, flagged)
                     for u in daily_summary["user_id"].unique()]

    out_path = base / "kiki_insights.json"
    with open(out_path, "w") as f:
        json.dump(all_insights, f, indent=2, default=str)

    print(json.dumps(all_insights, indent=2, default=str))
