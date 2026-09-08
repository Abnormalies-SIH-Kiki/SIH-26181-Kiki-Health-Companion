# Personal Health Telemetry Prototype

A rule-based-first health telemetry pipeline: dummy wearable data -> personal
baseline -> anomaly detection -> daily summaries -> trend detection ->
rule-based risk score -> structured JSON for **Kiki** to consume. A tiny
optional TFLite Micro model for ESP32 is included, with findings on where it
actually helps.

Guiding principle: **a baseline/anomaly system that reliably works in a demo
beats an unverifiable "AI predicts disease" story.** Nothing here claims to
diagnose anything; it flags deviations from *this specific person's* own
patterns and explains why in plain language.

## Quick start

```bash
cd telemetry_system
pip install -r requirements.txt
python3 src/pipeline.py          # generates data, runs the full rule-based pipeline
python3 src/train_tflite_model.py  # optional: trains + exports the ESP32 model
```

Final output for Kiki: `data/kiki_insights.json` (one structured object per user/day).

## Pipeline stages

| Stage | File | What it does |
|---|---|---|
| 1. Ingest | `generate_dummy_data.py` | Simulates minute-level HR/steps/activity with circadian rhythm + injected, labelled anomalies (for validation only -- a real device feed replaces this file) |
| 2. Baseline | `baseline.py` | Per-user, per-hour-of-day, per-activity-level mean/std HR; per-user average daily steps |
| 3. Deviation detection | `anomaly_detection.py` | Z-score vs. baseline + persistence filter (kills single-sample noise); separate "unexpected inactivity" rule for gaps HR alone misses |
| 4. Daily summary | `daily_summary.py` | Steps, active minutes, resting/avg/max/min HR, anomaly-minute count per user/day |
| 5. Trend detection | `trend_detection.py` | Rolling linear-regression slope over 7 days, classified rising/falling/stable relative to the metric's own scale |
| 6. Risk score | `risk_score.py` | Sums named, capped point contributions (anomaly load, HR trend, activity trend) into a 0-100 score + low/moderate/high band, with a plain-English reason list |
| 7. Insights | `insights.py` | Merges everything into the Kiki-facing JSON contract (schema below) |

Run any file standalone against the CSVs it depends on, or run all of them via `pipeline.py`.

### Validating detection against ground truth

`generate_dummy_data.py` labels its injected anomalies (`is_injected_anomaly`).
`anomaly_detection.py` prints a TP/FP/FN comparison against that ground truth
on every run -- current tuning catches roughly 85% of injected events with a
modest false-positive rate. This is a debugging aid only; it disappears once
real device data replaces the simulator (real data has no ground-truth labels).

## Kiki insight JSON schema

```json
{
  "user_id": "user_001",
  "date": "2026-01-21",
  "summary": {"total_steps": 28332, "active_minutes": 506, "avg_hr": 75.4,
              "resting_hr_est": 65.8, "max_hr": 137.0, "min_hr": 54.2},
  "baseline_comparison": {"resting_hr_vs_7d_avg": 0.3, "steps_vs_7d_avg": -1557.0},
  "anomalies": {"count_today": 17, "recent_examples": [{"timestamp": "...", "heart_rate": 136.2, "deviation_type": "elevated"}]},
  "trends": {"resting_hr_trend": "stable", "steps_trend": "stable", "active_minutes_trend": "stable"},
  "risk": {"score": 34.0, "level": "moderate", "reasons": ["17 confirmed anomaly minute(s) today (+34)"]},
  "narrative": "Risk level today: moderate (score 34/100). Contributing factors: ..."
}
```

Kiki can render `narrative` directly, or use the structured fields to decide
tone/follow-up questions without ever touching raw telemetry.

## Where TensorFlow Lite / Edge AI actually helps (investigation findings)

This was tested directly, not assumed -- `train_tflite_model.py` trains a
tiny (4->4->2->4->4) dense autoencoder on (heart_rate, steps, sin(hour),
cos(hour)) to catch *joint* pattern anomalies a single-variable z-score can
miss, then quantizes it to int8 for TFLite Micro (~3.2 KB model file).

**Result on this dummy dataset: the rule-based detector outperformed the
autoencoder.** Rule-based caught ~420/492 injected anomalies with 181 false
positives; the autoencoder caught ~123/492 with 600 false positives, using
the same 99th-percentile-of-normal threshold approach. Conclusion for this
prototype:

- **Keep rule-based for the core detection path.** Z-score + persistence is
  cheap, interpretable, tunable by a human without retraining, and simply
  won here on the data we could construct. This should stay the primary
  signal reported to Kiki and shown in any demo.
- **A learned model is worth revisiting once real (not simulated) data
  exists**, specifically for multivariate/joint patterns that are hard to
  hand-write as rules -- e.g. HR-variability-derived stress patterns, or
  combining HR with accelerometer-derived motion signatures. Dummy data is
  too clean/well-behaved for an autoencoder to find structure rules don't
  already capture; this needs real physiological noise to evaluate properly.
- **Genuine edge-AI candidates for a v2**, once real datasets are in hand:
  - Arrhythmia-pattern classification from raw PPG/ECG waveforms (this is
    where CNNs/1D-conv nets meaningfully beat hand rules -- see MIT-BIH /
    PPG-DaLiA below).
  - Activity/gesture classification from raw accelerometer data (a classic,
    well-proven TFLite Micro use case on ESP32-class hardware).
  - Sleep-stage estimation from combined HR + motion.
- **What should stay off an ESP32 regardless:** anything that needs a large
  labelled clinical dataset to be trustworthy (e.g. actual disease
  prediction). That belongs in a cloud-side model with proper validation,
  not a microcontroller demo.

The ESP32 export (`esp32_export/anomaly_inference/`, containing
`anomaly_inference.ino` + `model_data.h`) is provided as a working example of
the *mechanics* (train -> quantize -> C header -> TFLite Micro inference
loop) so the path is ready when a task that actually needs it comes along,
per the findings above. The sketch targets the official TFLite Micro Arduino
library's `AllOpsResolver` + `ErrorReporter` API pattern -- if you install a
different TFLite Micro port, the interpreter setup code in `setup()` may
need adjusting to match that library's own bundled example sketch (the model
bytes in `model_data.h` itself don't change either way).

## Public / government datasets for future model development

| Dataset | Source | Useful for |
|---|---|---|
| MIT-BIH Arrhythmia Database | PhysioNet (physionet.org) | ECG arrhythmia classification, gold-standard benchmark |
| PPG-DaLiA | PhysioNet / UCI | PPG-based HR estimation during real daily activities (closest match to a wrist wearable) |
| WESAD (Wearable Stress and Affect Detection) | Uni Siegen / PhysioNet | Stress detection from wearable HR/EDA/motion |
| MIMIC-III / MIMIC-IV Waveform | PhysioNet (credentialed access) | ICU-grade vitals, HR/SpO2 time series at scale |
| Apple Heart Study / Stanford data releases | Stanford Medicine | Large-scale wearable-detected arrhythmia population data |
| UK Biobank accelerometer data | UK Biobank (application required) | Large-N free-living activity data for population activity baselines |
| NHANES | CDC | Population-level HR/activity/health baseline statistics, useful for sanity-checking "normal" ranges by age/sex |
| Fitbit/Apple Watch open research datasets (e.g. via Kaggle "Fitbit Fitness Tracker Data") | Kaggle (community-curated, not government) | Cheap, fast prototyping of daily-summary-level logic before touching clinical data |

PhysioNet is the strongest starting point: most of the above HR/PPG/ECG
sets are hosted or indexed there under a consistent access model, and several
(MIT-BIH, PPG-DaLiA, WESAD) are openly downloadable without a credentialing
process, unlike MIMIC.

## File layout

```
telemetry_system/
  data/                    generated CSV/JSON at each pipeline stage
  models/                  trained .tflite model + feature_scaler.json
  esp32_export/
    anomaly_inference/     the Arduino sketch folder (anomaly_inference.ino + model_data.h)
  src/
    generate_dummy_data.py
    baseline.py
    anomaly_detection.py
    daily_summary.py
    trend_detection.py
    risk_score.py
    insights.py
    pipeline.py            runs stages 1-7 in order
    train_tflite_model.py  optional, separate from the core pipeline
  requirements.txt
  README.md
```

## Honest limitations of this prototype

- Dummy data only -- no real device has fed this pipeline yet. Thresholds
  (z=2.2, persistence=2, risk-score weights) are reasonable starting points,
  not clinically validated.
- Risk score is additive and capped, not probabilistic -- treat the number
  as a triage signal, not a calibrated probability of anything.
- "Resting HR" is estimated from sedentary-minute averages, not a proper
  sleep-HR estimate (would need sleep-stage detection to do that well).
- None of this should be presented as diagnosing a condition. It flags
  deviation from a person's own baseline and says so explicitly.
