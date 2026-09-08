"""
daily_summary.py
------------------
Rolls minute-level telemetry up into one row per user per day: total steps,
active minutes, resting/avg/max HR, and anomaly counts for that day. This is
the level most trend detection, risk scoring, and the "Kiki" insight feed
actually operate on -- nobody needs a decision made every minute, but a daily
digest is exactly the right granularity for spotting drift.
"""

import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/


def compute_daily_summary(flagged_df: pd.DataFrame) -> pd.DataFrame:
    d = flagged_df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d["date"] = d["timestamp"].dt.date

    def _resting_hr(sub: pd.DataFrame) -> float:
        # Approximate "resting HR" as the mean HR during sedentary minutes,
        # which is close to how consumer wearables estimate it without ECG.
        sed = sub[sub["activity_level"] == "sedentary"]
        return float(sed["heart_rate"].mean()) if len(sed) else float(sub["heart_rate"].mean())

    rows = []
    for (user_id, date), grp in d.groupby(["user_id", "date"]):
        rows.append({
            "user_id": user_id,
            "date": str(date),
            "total_steps": int(grp["steps_in_interval"].sum()),
            "active_minutes": int((grp["activity_level"] != "sedentary").sum()),
            "avg_hr": round(float(grp["heart_rate"].mean()), 1),
            "resting_hr_est": round(_resting_hr(grp), 1),
            "max_hr": round(float(grp["heart_rate"].max()), 1),
            "min_hr": round(float(grp["heart_rate"].min()), 1),
            "confirmed_anomaly_minutes": int(grp.get("any_anomaly", pd.Series(dtype=bool)).sum()),
        })

    return pd.DataFrame(rows).sort_values(["user_id", "date"]).reset_index(drop=True)


if __name__ == "__main__":
    flagged = pd.read_csv(BASE_DIR / "data" / "telemetry_flagged.csv")
    summary = compute_daily_summary(flagged)
    summary.to_csv(BASE_DIR / "data" / "daily_summary.csv", index=False)
    print(summary.to_string(index=False))
