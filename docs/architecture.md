# System Architecture

## High-level flow

```text
Wearable (ESP32-S3)              Desktop (Raspberry Pi 5)
  MAX30102, QMI8658 IMU            Camera, mics, speaker, displays
        |                                   |
        | firmware: fall/step detection     | camera activity checks,
        | (works with zero connectivity)    | face recognition, reminders
        |                                   |
        +---------------+-------------------+
                         |
                         v
              Gateway (laptop, WebSocket)
                         |
        +----------------+-----------------+
        |                                  |
        v                                  v
  Local inference                   Cloud services
  (llama.cpp, whisper.cpp,          (Cerebras, Gemini)
   OmniVoice — no internet          (background reasoning,
   needed for speech)                multi-step agents)
        |                                  |
        +----------------+-----------------+
                         |
                         v
        Voice response, WhatsApp/email alert,
        Web dashboard (Next.js + SQLite)
```

## Components

### Wearable (ESP32-S3-Touch-AMOLED-1.75)
Runs C++ firmware on ESP-IDF 5.5. Handles on-demand heart rate/SpO₂ (MAX30102), step counting and fall detection (QMI8658 IMU). Fall detection and step counting run entirely on-device — no connectivity required for this layer.

### Desktop (Raspberry Pi 5)
Runs the Python desktop module. Handles voice conversation, camera-based activity detection and face recognition (via Hailo-8 accelerator), reminders, and the shared care plan.

### Gateway (laptop)
A WebSocket service that both devices connect to. Hosts local LLM inference (llama.cpp with gemma-4-26B-A4B), speech-to-text (whisper.cpp) and text-to-speech (OmniVoice). The devices reach the laptop over Tailscale.

### Cloud services
Cerebras and Gemini handle background/multi-step reasoning that doesn't need to be instant. Not required for core safety features (fall detection, offline alerts still work without them).

### Analytics dashboard
Next.js/TypeScript frontend backed by SQLite, built from Python/pandas processing of the captured sensor data. Surfaces heat index, AQI risk, HR/SpO₂ trends and sleep insights.

## For full technical detail

This file is deliberately a high-level map. For wiring, audio pipeline details, latency numbers, and the specific failures that shaped these design decisions, see [ENGINEERING.md](ENGINEERING.md).
