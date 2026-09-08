"""
baseline.py
-----------
Builds a per-user, per-hour-of-day baseline (mean + std) for heart rate,
using a rolling window of history. This is the foundation everything else
(anomaly detection, risk scoring) is built on.

Why hour-of-day buckets instead of one flat baseline?
A single "normal HR = 68" baseline is useless -- HR at 3am is very different
from HR at 3pm. Bucketing by hour captures the circadian pattern with almost
no computational cost, which matters for later porting to ESP32.

The baseline is recomputed from "clean" data only (excluding points already
flagged as anomalies) so a few bad days don't drag the baseline off course --
a simple form of robust estimation without needing anything fancy.
"""

import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/


def compute_baseline(df: pd.DataFrame, min_samples_per_bucket: int = 5) -> pd.DataFrame:
    """
    df must have columns: user_id, timestamp (parseable), heart_rate.
    Optionally: is_injected_anomaly (excluded if present, since ground truth
    anomalies shouldn't pollute what we call "normal").

    Returns a baseline table: user_id, hour, hr_mean, hr_std, n_samples
    """
    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d["hour"] = d["timestamp"].dt.hour

    if "is_injected_anomaly" in d.columns:
        d = d[~d["is_injected_anomaly"]]

    # Bucket by hour-of-day AND activity level. Comparing a resting-HR
    # reading to a baseline that blends sedentary and vigorous samples
    # together makes the std huge and hides real anomalies -- bucketing by
    # activity_level too keeps the comparison "like for like".
    group_cols = ["user_id", "hour"]
    if "activity_level" in d.columns:
        group_cols.append("activity_level")

    grouped = d.groupby(group_cols)["heart_rate"].agg(["mean", "std", "count"]).reset_index()
    grouped = grouped.rename(columns={"mean": "hr_mean", "std": "hr_std", "count": "n_samples"})

    # Guard against tiny/zero std for sparse buckets: fall back to a sane
    # minimum std so z-scores don't explode on near-constant small samples.
    grouped["hr_std"] = grouped["hr_std"].fillna(3.0).clip(lower=2.0)
    grouped.loc[grouped["n_samples"] < min_samples_per_bucket, "hr_std"] = grouped["hr_std"] * 1.5

    return grouped


def compute_step_baseline(df: pd.DataFrame) -> pd.DataFrame:
    """Per-user average daily steps, used for trend/summary comparisons."""
    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d["date"] = d["timestamp"].dt.date
    daily = d.groupby(["user_id", "date"])["steps_in_interval"].sum().reset_index()
    baseline = daily.groupby("user_id")["steps_in_interval"].agg(["mean", "std"]).reset_index()
    baseline.columns = ["user_id", "daily_steps_mean", "daily_steps_std"]
    return baseline


if __name__ == "__main__":
    df = pd.read_csv(BASE_DIR / "data" / "telemetry.csv")
    hr_baseline = compute_baseline(df)
    step_baseline = compute_step_baseline(df)
    hr_baseline.to_csv(BASE_DIR / "data" / "hr_baseline.csv", index=False)
    step_baseline.to_csv(BASE_DIR / "data" / "step_baseline.csv", index=False)
    print(hr_baseline.head(10))
    print(step_baseline)
