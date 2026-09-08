"""
trend_detection.py
--------------------
Deliberately the simplest thing that could possibly work: fit a straight
line (least-squares slope) through the last N days of a metric and classify
the trend as rising / falling / stable based on the slope size relative to
the metric's own variability. No ARIMA, no Prophet, no LSTM -- for 7-30 day
windows on noisy daily health metrics, a robust-enough linear slope tells
you almost everything a fancier model would, and it's auditable and cheap
enough to eventually run on-device too.
"""

import numpy as np
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/


def _slope_trend(values: np.ndarray, rel_threshold: float = 0.15):
    """
    Fits y = a*x + b over the window. Classifies as 'rising'/'falling' if the
    total projected change over the window exceeds rel_threshold fraction of
    the metric's mean; otherwise 'stable'.
    """
    n = len(values)
    if n < 3 or np.all(values == values[0]):
        return "stable", 0.0
    x = np.arange(n)
    a, b = np.polyfit(x, values, 1)
    projected_change = a * (n - 1)
    mean_val = np.mean(values) if np.mean(values) != 0 else 1.0
    rel_change = projected_change / mean_val

    if rel_change > rel_threshold:
        return "rising", float(a)
    elif rel_change < -rel_threshold:
        return "falling", float(a)
    return "stable", float(a)


def detect_trends(daily_summary: pd.DataFrame, window_days: int = 7,
                   metrics=("resting_hr_est", "total_steps", "active_minutes")) -> pd.DataFrame:
    rows = []
    for user_id, grp in daily_summary.groupby("user_id"):
        grp = grp.sort_values("date")
        window = grp.tail(window_days)
        entry = {"user_id": user_id, "window_days": len(window),
                 "window_end_date": window["date"].iloc[-1] if len(window) else None}
        for metric in metrics:
            trend, slope = _slope_trend(window[metric].to_numpy(dtype=float))
            entry[f"{metric}_trend"] = trend
            entry[f"{metric}_slope_per_day"] = round(slope, 3)
        rows.append(entry)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    summary = pd.read_csv(BASE_DIR / "data" / "daily_summary.csv")
    trends = detect_trends(summary)
    trends.to_csv(BASE_DIR / "data" / "trends.csv", index=False)
    print(trends.to_string(index=False))
