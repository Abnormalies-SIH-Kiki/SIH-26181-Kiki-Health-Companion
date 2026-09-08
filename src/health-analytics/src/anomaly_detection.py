"""
anomaly_detection.py
---------------------
Flags heart-rate readings that deviate meaningfully from the user's personal,
hour-of-day baseline. Deliberately simple (z-score + persistence filter)
because a rule that's easy to reason about and rarely cries wolf beats an
opaque model during a demo.

Two-stage filter:
1. Point-level z-score: |hr - bucket_mean| / bucket_std > threshold
2. Persistence: require the deviation to hold for >= min_consecutive
   readings, which kills most single-sample sensor noise / motion artifacts
   without needing any ML.
"""

import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/


def flag_deviations(df: pd.DataFrame, hr_baseline: pd.DataFrame,
                     z_threshold: float = 2.2, min_consecutive: int = 2) -> pd.DataFrame:
    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d["hour"] = d["timestamp"].dt.hour

    merge_cols = ["user_id", "hour"]
    if "activity_level" in hr_baseline.columns and "activity_level" in d.columns:
        merge_cols.append("activity_level")
    d = d.merge(hr_baseline, on=merge_cols, how="left")
    # Fallback for any (user, hour, activity) combo unseen in baseline history
    d["hr_mean"] = d["hr_mean"].fillna(d.groupby("user_id")["heart_rate"].transform("mean"))
    d["hr_std"] = d["hr_std"].fillna(d.groupby("user_id")["heart_rate"].transform("std"))
    d["hr_zscore"] = (d["heart_rate"] - d["hr_mean"]) / d["hr_std"]
    d["point_flag"] = d["hr_zscore"].abs() > z_threshold

    # Persistence filter per user, in timestamp order.
    d = d.sort_values(["user_id", "timestamp"]).reset_index(drop=True)
    d["confirmed_anomaly"] = False
    for user_id, grp in d.groupby("user_id"):
        flags = grp["point_flag"].to_numpy()
        confirmed = np.zeros_like(flags, dtype=bool)
        run_len = 0
        for i, f in enumerate(flags):
            run_len = run_len + 1 if f else 0
            if run_len >= min_consecutive:
                confirmed[i - min_consecutive + 1:i + 1] = True
        d.loc[grp.index, "confirmed_anomaly"] = confirmed

    d["deviation_type"] = np.select(
        [d["confirmed_anomaly"] & (d["hr_zscore"] > 0),
         d["confirmed_anomaly"] & (d["hr_zscore"] < 0)],
        ["elevated", "depressed"],
        default="none",
    )

    # Separate, complementary signal: sustained zero-step stretches during
    # hours the user is normally active. HR-only detection can miss events
    # like "device removed" or "user incapacitated but HR stays in range" --
    # this rule exists precisely to cover that gap.
    d["inactive_flag"] = (d["steps_in_interval"] == 0) & (~d["hour"].between(0, 5))
    d["unexpected_inactivity"] = False
    for user_id, grp in d.groupby("user_id"):
        flags = grp["inactive_flag"].to_numpy()
        confirmed = np.zeros_like(flags, dtype=bool)
        run_len = 0
        min_run = 5  # need several consecutive silent minutes to flag, not just one gap
        for i, f in enumerate(flags):
            run_len = run_len + 1 if f else 0
            if run_len >= min_run:
                confirmed[i - min_run + 1:i + 1] = True
        d.loc[grp.index, "unexpected_inactivity"] = confirmed

    d["any_anomaly"] = d["confirmed_anomaly"] | d["unexpected_inactivity"]
    d.loc[d["unexpected_inactivity"] & ~d["confirmed_anomaly"], "deviation_type"] = "unexpected_inactivity"

    return d


if __name__ == "__main__":
    df = pd.read_csv(BASE_DIR / "data" / "telemetry.csv")
    hr_baseline = pd.read_csv(BASE_DIR / "data" / "hr_baseline.csv")
    flagged = flag_deviations(df, hr_baseline)

    confirmed = flagged[flagged["any_anomaly"]]
    print(f"Confirmed anomaly readings: {len(confirmed)} / {len(flagged)}")

    # Quick sanity check against injected ground truth (dummy-data-only check)
    if "is_injected_anomaly" in df.columns:
        truth = flagged["is_injected_anomaly"]
        pred = flagged["any_anomaly"]
        tp = int((truth & pred).sum())
        fp = int((~truth & pred).sum())
        fn = int((truth & ~pred).sum())
        print(f"vs injected ground truth -> TP:{tp} FP:{fp} FN:{fn}")

    flagged.to_csv(BASE_DIR / "data" / "telemetry_flagged.csv", index=False)
