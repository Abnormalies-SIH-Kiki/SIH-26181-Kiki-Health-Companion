# ESP32 Health Telemetry

---

## 📦 Project

### [`esp32-health-telemetry/`](esp32-health-telemetry/)

A wearable health-telemetry and edge-AI pipeline for ESP32 (QMI8658 IMU + MAX30102 PPG).

**What it does:**
- ❤️ Detects heart-rate and SpO₂ anomalies against a personal, per-activity baseline
- 🚶 Classifies activity (Resting / Inactive / Walking / Running / Transition) via a Decision Tree
- ⚠️ Detects falls on-device using a quantized 1D CNN (TFLite Micro) with a post-fall confirmation state machine
- 📊 Combines everything into an explainable risk score, output as structured JSON once per second

**Runs on synthetic sensor data** — no physical hardware required to build, train, or test the full pipeline end to end.

> **Note:** Real QMI8658/MAX30101 sensor integration is fully supported by the architecture — the pipeline (feature extraction, models, risk engine, JSON output) is sensor-agnostic by design. This repo currently represents the **code concept and full working pipeline** using synthetic data; swapping in real sensor reads is a drop-in change (replace two function calls in `main.cpp`) with no changes needed downstream.

👉 See [`esp32-health-telemetry/README.md`](esp32-health-telemetry/README.md) for full setup instructions, pipeline flowcharts, and the rationale behind each model choice.

---

## 🛠 Tech at a glance

| Layer | Tooling |
|---|---|
| Data generation & training | Python (numpy, scikit-learn, TensorFlow) |
| Firmware | C++ (Arduino framework via PlatformIO), TFLite Micro |
| Target hardware | ESP32-WROOM class boards |

---
