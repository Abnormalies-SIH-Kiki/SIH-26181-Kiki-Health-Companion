# Source code

The code is split between the desktop, wearable and analytics work. These are snapshots of the source repositories. Their earlier Git history isn't included because it contained credentials.

| Path | Runs on | Purpose |
|---|---|---|
| `desktop-module/` | Raspberry Pi 5 | Conversation, tools, camera and Hailo vision, care sessions, displays and stepper control |
| `wearable/firmware/` | ESP32-S3 | Audio, watch UI, sensors, steps and fall detection; C++ on ESP-IDF 5.5 |
| `wearable/gateway/` | Laptop | Voice processing, care sessions and wearable telemetry |
| `wearable/scripts/` | Laptop | Build, flash and run helpers |
| `health-analytics/` | Laptop | Trend pipeline and caregiver dashboard |

The gateway still imports a few modules from `wearable/gateway/legacy_kiki/`, a bundled older copy of the desktop code.

## Analytics

`health-analytics/` is included with the author's permission from AditiS721/SIH-Abnormalies-Software, branch `DevAniket`.

Its `src/` folder contains the rule-based pipeline: baselines, anomalies, daily summaries, trends and risk scores. It currently runs on generated data. `vitality-app/` is the Next.js dashboard with its own SQLite database; connecting these to live wearable telemetry is still pending.

`src/train_tflite_model.py` and `esp32_export/` are a separate model experiment. They don't drive the watch's fall alerts. That detector lives in `wearable/firmware/main/kiki_fall_detector.hpp`.

## Local setup and checks

Start with the [root README](../README.md) for device setup. Each test command below starts from `src/`, with that component's environment active and its test dependencies installed:

```bash
# Desktop
cd desktop-module
PYTHONPATH=. python -m pytest -q
```

```bash
# Gateway
cd wearable/gateway
pip install -e '.[test]'
PYTHONPATH=. python -m pytest -q
```

The recorded run on 8 September 2026 had 1,101 desktop tests and 538 gateway tests passing. These suites don't need hardware or running inference services.

Analytics has an end-to-end script instead of a test suite. From `src/`:

```bash
cd health-analytics
pip install -r requirements.txt
python src/pipeline.py
```

It writes generated results to `data/`. A saved example is in [docs/data/analytics_pipeline_output.json](../docs/data/analytics_pipeline_output.json).

## What's excluded

Local credentials, runtime conversations, recordings, face data, contact details and build output aren't included. Use the example configuration files to create your own setup; update service addresses as well as keys. Firmware binaries are excluded because they contain the compiled gateway token.
