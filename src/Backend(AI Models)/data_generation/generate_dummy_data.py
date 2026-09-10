"""
generate_dummy_data.py

Synthetic sensor-data generator for the ESP32 Health Telemetry project.
Produces two aligned CSVs:
  - imu_50hz.csv   : acc_x/y/z, gyro_x/y/z at 50 Hz
  - vitals_1hz.csv : heart_rate, spo2 at 1 Hz
plus a windows.csv describing labeled segments (activity or fall/hard-negative)
used by the training scripts.

Design follows the executable spec:
  Activities : Resting, Inactive, Walking, Running, Transition
  Events     : Fall (3-phase: free-fall dip, impact spike, post-impact stillness)
  Hard neg.  : Dropped device, Hard sit-down, Jump, Vigorous arm gesture

Usage:
    python generate_dummy_data.py --minutes 60 --seed 42 --out ../data
"""

import argparse
import os
import numpy as np
import pandas as pd

IMU_FS = 50   # Hz
HR_FS = 1     # Hz

ACTIVITY_PROFILES = {
    # accel/gyro magnitude ~ baseline + noise; hr/spo2 targets are steady-state means
    "Resting":    dict(acc_mean=1.00, acc_noise=0.02, gyro_mean=1.0,  gyro_noise=1.0,  hr_mean=68,  hr_noise=2, spo2_mean=98.0, spo2_noise=0.3),
    "Inactive":   dict(acc_mean=1.00, acc_noise=0.01, gyro_mean=0.3,  gyro_noise=0.3,  hr_mean=62,  hr_noise=1.5, spo2_mean=97.5, spo2_noise=0.3),
    "Walking":    dict(acc_mean=1.25, acc_noise=0.20, gyro_mean=25.0, gyro_noise=8.0,  hr_mean=98,  hr_noise=4, spo2_mean=97.0, spo2_noise=0.5),
    "Running":    dict(acc_mean=1.80, acc_noise=0.45, gyro_mean=70.0, gyro_noise=20.0, hr_mean=142, hr_noise=6, spo2_mean=96.0, spo2_noise=0.6),
}


def _walk_run_cycle(n, fs, stride_hz, acc_mean, acc_noise, gyro_mean, gyro_noise, rng):
    """Periodic gait-like signal for Walking/Running."""
    t = np.arange(n) / fs
    acc = acc_mean + 0.6 * acc_noise * np.sin(2 * np.pi * stride_hz * t) + rng.normal(0, acc_noise * 0.5, n)
    gyro = gyro_mean + 0.6 * gyro_noise * np.sin(2 * np.pi * stride_hz * t + 0.5) + rng.normal(0, gyro_noise * 0.5, n)
    return np.clip(acc, 0, None), np.clip(gyro, 0, None)


def gen_activity_segment(activity, duration_s, rng):
    """Return (acc_mag[n], gyro_mag[n]) at IMU_FS and (hr[m], spo2[m]) at HR_FS."""
    p = ACTIVITY_PROFILES[activity]
    n = int(duration_s * IMU_FS)
    m = int(duration_s * HR_FS)

    if activity in ("Walking", "Running"):
        stride_hz = 1.8 if activity == "Walking" else 2.8
        acc, gyro = _walk_run_cycle(n, IMU_FS, stride_hz, p["acc_mean"], p["acc_noise"], p["gyro_mean"], p["gyro_noise"], rng)
    else:
        acc = p["acc_mean"] + rng.normal(0, p["acc_noise"], n)
        gyro = np.abs(p["gyro_mean"] + rng.normal(0, p["gyro_noise"], n))
        acc = np.clip(acc, 0, None)

    hr = p["hr_mean"] + rng.normal(0, p["hr_noise"], m)
    spo2 = np.clip(p["spo2_mean"] + rng.normal(0, p["spo2_noise"], m), 85, 100)
    return acc, gyro, hr, spo2


def gen_transition_segment(duration_s, rng):
    """Ramp linearly between Walking and Running profiles (§6)."""
    n = int(duration_s * IMU_FS)
    m = int(duration_s * HR_FS)
    w, r = ACTIVITY_PROFILES["Walking"], ACTIVITY_PROFILES["Running"]

    ramp_n = np.linspace(0, 1, n)
    stride_hz = np.linspace(1.8, 2.8, n)
    t = np.arange(n) / IMU_FS
    acc_mean = w["acc_mean"] + ramp_n * (r["acc_mean"] - w["acc_mean"])
    gyro_mean = w["gyro_mean"] + ramp_n * (r["gyro_mean"] - w["gyro_mean"])
    phase = 2 * np.pi * np.cumsum(stride_hz) / IMU_FS
    acc = acc_mean + 0.15 * np.sin(phase) + rng.normal(0, 0.15, n)
    gyro = np.clip(gyro_mean + 5 * np.sin(phase + 0.5) + rng.normal(0, 5, n), 0, None)

    ramp_m = np.linspace(0, 1, m)
    hr_mean = w["hr_mean"] + ramp_m * (r["hr_mean"] - w["hr_mean"])
    hr = hr_mean + rng.normal(0, 4, m)
    spo2_mean = w["spo2_mean"] + ramp_m * (r["spo2_mean"] - w["spo2_mean"])
    spo2 = np.clip(spo2_mean + rng.normal(0, 0.5, m), 85, 100)
    return np.clip(acc, 0, None), gyro, hr, spo2


def gen_fall_window(rng, duration_s=4.0):
    """3-phase fall signature per spec §7: free-fall dip -> impact spike -> stillness."""
    fs = IMU_FS
    n = int(duration_s * fs)
    acc = np.ones(n)
    gyro = np.zeros(n)

    a_start = int(0.5 * fs)
    a_len = int(rng.uniform(0.2, 0.4) * fs)
    a_end = a_start + a_len
    acc[a_start:a_end] = np.linspace(1.0, 0.1, a_len)

    b_len = int(rng.uniform(0.05, 0.15) * fs)
    b_end = a_end + b_len
    acc[a_end:b_end] = rng.uniform(3.0, 8.0)
    gyro[a_end:b_end] = rng.uniform(150, 400)

    tail = n - b_end
    acc[b_end:] = 1.0 + rng.normal(0, 0.02, tail)
    gyro[b_end:] = np.abs(rng.normal(0, 2, tail))
    return np.clip(acc, 0, None), np.clip(gyro, 0, None)


def gen_hard_negative(kind, rng, duration_s=4.0):
    """Fall-like but non-fall motion signatures (§7) so the CNN learns precision, not just recall."""
    fs = IMU_FS
    n = int(duration_s * fs)
    acc = np.ones(n)
    gyro = np.abs(rng.normal(0, 2, n))

    if kind == "dropped_device":
        # sharp spike, no preceding free-fall dip, erratic motion after (device bouncing/sliding)
        spike_start = int(0.5 * fs)
        spike_len = int(rng.uniform(0.03, 0.08) * fs)
        acc[spike_start:spike_start + spike_len] = rng.uniform(4.0, 9.0)
        gyro[spike_start:spike_start + spike_len] = rng.uniform(200, 500)
        acc[spike_start + spike_len:] = 1.0 + rng.normal(0, 0.4, n - spike_start - spike_len)  # erratic, not still
        gyro[spike_start + spike_len:] = np.abs(rng.normal(0, 15, n - spike_start - spike_len))

    elif kind == "hard_sit":
        # moderate spike, no free-fall phase, quick return to low-motion sitting baseline
        spike_start = int(0.5 * fs)
        spike_len = int(rng.uniform(0.1, 0.2) * fs)
        acc[spike_start:spike_start + spike_len] = rng.uniform(1.8, 3.0)
        gyro[spike_start:spike_start + spike_len] = rng.uniform(40, 100)
        acc[spike_start + spike_len:] = 1.0 + rng.normal(0, 0.05, n - spike_start - spike_len)
        gyro[spike_start + spike_len:] = np.abs(rng.normal(0, 3, n - spike_start - spike_len))

    elif kind == "jump":
        # rhythmic repeated spikes, no sustained stillness
        t = np.arange(n) / fs
        acc = 1.0 + 1.2 * np.abs(np.sin(2 * np.pi * 1.5 * t)) + rng.normal(0, 0.1, n)
        gyro = 20 + 10 * np.abs(np.sin(2 * np.pi * 1.5 * t + 0.3)) + rng.normal(0, 5, n)

    elif kind == "arm_gesture":
        # gyro spike WITHOUT matching accel spike (waving, reaching)
        spike_start = int(0.5 * fs)
        spike_len = int(rng.uniform(0.2, 0.5) * fs)
        gyro[spike_start:spike_start + spike_len] = rng.uniform(100, 250)
        acc[spike_start:spike_start + spike_len] = 1.0 + rng.normal(0, 0.1, spike_len)

    return np.clip(acc, 0, None), np.clip(gyro, 0, None)


def mag_to_axes(mag, rng):
    """Split a magnitude series into randomized x/y/z axis components (unit vector direction)."""
    n = len(mag)
    v = rng.normal(0, 1, (n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    return mag[:, None] * v  # (n,3)


def build_dataset(total_minutes, rng):
    imu_rows = []
    vit_rows = []
    windows = []

    t_imu = 0.0
    t_vit = 0.0
    seg_id = 0
    activities = ["Resting", "Inactive", "Walking", "Running"]
    remaining_s = total_minutes * 60

    while remaining_s > 0:
        roll = rng.random()
        if roll < 0.08 and remaining_s > 20:
            # insert an event: fall or hard negative
            if rng.random() < 0.5:
                acc_mag, gyro_mag = gen_fall_window(rng)
                label = "Fall"
            else:
                kind = rng.choice(["dropped_device", "hard_sit", "jump", "arm_gesture"])
                acc_mag, gyro_mag = gen_hard_negative(kind, rng)
                label = f"HardNeg_{kind}"
            dur = len(acc_mag) / IMU_FS
            hr = np.full(int(dur * HR_FS), 80.0) + rng.normal(0, 5, int(dur * HR_FS))
            spo2 = np.full(int(dur * HR_FS), 97.0) + rng.normal(0, 0.4, int(dur * HR_FS))
        elif roll < 0.13 and remaining_s > 15:
            dur = rng.uniform(5, 15)
            acc_mag, gyro_mag, hr, spo2 = gen_transition_segment(dur, rng)
            label = "Transition"
        else:
            activity = rng.choice(activities)
            dur = rng.uniform(30, 180)
            acc_mag, gyro_mag, hr, spo2 = gen_activity_segment(activity, dur, rng)
            label = activity

        n = len(acc_mag)
        axes_acc = mag_to_axes(acc_mag, rng)
        axes_gyro = mag_to_axes(gyro_mag, rng)
        ts_imu = t_imu + np.arange(n) / IMU_FS
        for i in range(n):
            imu_rows.append((ts_imu[i], *axes_acc[i], *axes_gyro[i], seg_id, label))
        t_imu += n / IMU_FS

        m = len(hr)
        ts_vit = t_vit + np.arange(m) / HR_FS
        for i in range(m):
            vit_rows.append((ts_vit[i], hr[i], spo2[i], seg_id, label))
        t_vit += m / HR_FS

        windows.append((seg_id, label, n / IMU_FS))
        seg_id += 1
        remaining_s -= n / IMU_FS

    imu_df = pd.DataFrame(imu_rows, columns=["t", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z", "seg_id", "label"])
    vit_df = pd.DataFrame(vit_rows, columns=["t", "heart_rate", "spo2", "seg_id", "label"])
    win_df = pd.DataFrame(windows, columns=["seg_id", "label", "duration_s"])
    return imu_df, vit_df, win_df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60, help="total synthetic minutes to generate")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=str, default="../data")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    imu_df, vit_df, win_df = build_dataset(args.minutes, rng)

    os.makedirs(args.out, exist_ok=True)
    imu_df.to_csv(os.path.join(args.out, "imu_50hz.csv"), index=False)
    vit_df.to_csv(os.path.join(args.out, "vitals_1hz.csv"), index=False)
    win_df.to_csv(os.path.join(args.out, "windows.csv"), index=False)

    print(f"IMU rows:    {len(imu_df)}")
    print(f"Vitals rows: {len(vit_df)}")
    print(f"Segments:    {len(win_df)}")
    print(win_df["label"].value_counts())


if __name__ == "__main__":
    main()
