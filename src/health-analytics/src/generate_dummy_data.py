"""
generate_dummy_data.py
-----------------------
Simulates wearable telemetry (heart rate, steps, activity level) for one or
more users over N days, sampled at a fixed interval (default: every minute).

Why simulate this way?
- Real wearable HR follows a circadian rhythm: low during sleep, elevated
  during the day, spikes during activity bursts.
- Step counts correlate with activity level (sedentary/light/moderate/vigorous).
- We inject *labelled* anomalies (tachycardia spikes, unusual drops, inactivity
  stretches) so downstream detection code can be validated against ground
  truth before any real device data is available.

Output: data/telemetry.csv with columns:
    user_id, timestamp, heart_rate, steps_in_interval, activity_level, is_injected_anomaly
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/
RNG = np.random.default_rng(42)

ACTIVITY_LEVELS = ["sedentary", "light", "moderate", "vigorous"]


def circadian_resting_hr(hour: float, user_baseline: float) -> float:
    """Smooth day/night resting HR curve. Lowest ~4am, highest ~4pm."""
    phase = (hour - 4) / 24 * 2 * np.pi
    swing = 8 * (1 - np.cos(phase)) / 2  # 0..8 bpm swing above resting
    return user_baseline + swing


def sample_activity_level(hour: float) -> str:
    """Rough day-shape: sleeping at night, mixed activity during the day."""
    if 0 <= hour < 6 or hour >= 23:
        probs = [0.97, 0.02, 0.01, 0.00]
    elif 6 <= hour < 9 or 17 <= hour < 20:
        probs = [0.35, 0.30, 0.25, 0.10]  # morning/evening exercise windows
    else:
        probs = [0.55, 0.30, 0.13, 0.02]
    return RNG.choice(ACTIVITY_LEVELS, p=probs)


def hr_for_activity(base_hr: float, activity: str) -> float:
    bumps = {"sedentary": 0, "light": 15, "moderate": 35, "vigorous": 60}
    noise = RNG.normal(0, 3)
    return base_hr + bumps[activity] + noise


def steps_for_activity(activity: str) -> int:
    rates = {"sedentary": (0, 2), "light": (20, 40), "moderate": (60, 90), "vigorous": (110, 150)}
    lo, hi = rates[activity]
    return int(RNG.integers(lo, hi + 1))


def generate_user_telemetry(user_id: str, days: int, resting_hr_baseline: float,
                             interval_minutes: int = 1, anomaly_rate: float = 0.0015) -> pd.DataFrame:
    start = datetime(2026, 1, 1, 0, 0, 0)
    n_points = int(days * 24 * 60 / interval_minutes)
    rows = []
    burst_remaining = 0  # counts down while an injected anomaly burst is active
    burst_offset = 0.0

    for i in range(n_points):
        ts = start + timedelta(minutes=i * interval_minutes)
        hour = ts.hour + ts.minute / 60.0
        activity = sample_activity_level(hour)
        base_hr = circadian_resting_hr(hour, resting_hr_baseline)
        hr = hr_for_activity(base_hr, activity)
        steps = steps_for_activity(activity)
        is_anomaly = False

        # Start a new anomaly burst (spans several consecutive minutes, like
        # a real physiological event would, rather than one isolated sample).
        if burst_remaining == 0 and RNG.random() < anomaly_rate:
            kind = RNG.choice(["spike", "drop", "silent_inactivity"])
            burst_remaining = int(RNG.integers(3, 8))
            burst_offset = RNG.uniform(35, 60) if kind == "spike" else \
                            -RNG.uniform(20, 30) if kind == "drop" else 0.0
            burst_kind = kind

        if burst_remaining > 0:
            is_anomaly = True
            if burst_kind in ("spike", "drop"):
                hr += burst_offset
            elif burst_kind == "silent_inactivity":
                steps = 0
                activity = "sedentary"
            burst_remaining -= 1

        hr = float(np.clip(hr, 35, 200))
        rows.append((user_id, ts.isoformat(), round(hr, 1), int(steps), activity, is_anomaly))

    return pd.DataFrame(rows, columns=[
        "user_id", "timestamp", "heart_rate", "steps_in_interval", "activity_level", "is_injected_anomaly"
    ])


def main():
    users = [
        {"user_id": "user_001", "days": 21, "resting_hr_baseline": 62},
        {"user_id": "user_002", "days": 21, "resting_hr_baseline": 74},
    ]
    frames = [generate_user_telemetry(**u) for u in users]
    df = pd.concat(frames, ignore_index=True)
    out_path = BASE_DIR / "data" / "telemetry.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows for {len(users)} users -> {out_path}")
    print(f"Injected anomalies: {df['is_injected_anomaly'].sum()}")


if __name__ == "__main__":
    main()
