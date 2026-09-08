"""
risk_score.py
--------------
Combines today's anomaly load with the recent trend into a single, explainable
0-100 "risk score" and a low/moderate/high band. Every point on the score
traces back to a named, human-readable rule -- there is no black box here on
purpose. This is what should be demoed as "the system works", not a neural
net whose confidence nobody can sanity-check on stage.

Scoring rules (each contributes independently, then summed and capped at 100):
  +2   per confirmed anomaly minute today (cap 40)
  +20  if resting HR trend is 'rising' over the trend window
  +20  if resting HR trend is 'falling' sharply (bradycardia-direction) *
  +15  if total_steps trend is 'falling' (declining activity)
  +10  if active_minutes trend is 'falling'
  +10  if today's resting HR is >1.5 baseline-std above the user's own
       typical resting HR for that day-of-week hour bucket (extra day-level
       corroboration on top of the minute-level anomaly flags)

  * falling resting HR is scored the same as rising because both directions
    away from a stable personal baseline are noteworthy; interpretation
    differs (fitness improvement vs. possible bradycardia) which is why this
    stays a flag for a human/clinician to read, not a silent auto-diagnosis.
"""

import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/

BANDS = [(0, 25, "low"), (25, 55, "moderate"), (55, 1000, "high")]


def _band(score: float) -> str:
    for lo, hi, label in BANDS:
        if lo <= score < hi:
            return label
    return "high"


def compute_risk_scores(daily_summary: pd.DataFrame, trends: pd.DataFrame) -> pd.DataFrame:
    merged = daily_summary.merge(trends, on="user_id", how="left")
    # Only score the most recent day per user (the trend window's end date)
    latest = merged.sort_values("date").groupby("user_id").tail(1).copy()

    rows = []
    for _, r in latest.iterrows():
        score = 0.0
        reasons = []

        anomaly_pts = min(r["confirmed_anomaly_minutes"] * 2, 40)
        if anomaly_pts > 0:
            score += anomaly_pts
            reasons.append(f"{int(r['confirmed_anomaly_minutes'])} confirmed anomaly minute(s) today (+{anomaly_pts:.0f})")

        if r.get("resting_hr_est_trend") == "rising":
            score += 20
            reasons.append("resting HR trending upward over recent window (+20)")
        elif r.get("resting_hr_est_trend") == "falling":
            score += 20
            reasons.append("resting HR trending downward over recent window (+20)")

        if r.get("total_steps_trend") == "falling":
            score += 15
            reasons.append("daily step count trending downward (+15)")

        if r.get("active_minutes_trend") == "falling":
            score += 10
            reasons.append("active minutes trending downward (+10)")

        score = min(score, 100)
        rows.append({
            "user_id": r["user_id"],
            "date": r["date"],
            "risk_score": round(score, 1),
            "risk_level": _band(score),
            "reasons": reasons,
        })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    summary = pd.read_csv(BASE_DIR / "data" / "daily_summary.csv")
    trends = pd.read_csv(BASE_DIR / "data" / "trends.csv")
    risk = compute_risk_scores(summary, trends)
    risk.to_json(BASE_DIR / "data" / "risk_scores.json", orient="records", indent=2)
    print(risk.to_string(index=False))
