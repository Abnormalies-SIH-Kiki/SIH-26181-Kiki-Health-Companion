# Kiki ESP32-S3

This is an isolated implementation of Kiki for the Waveshare
ESP32-S3-Touch-AMOLED-1.75. It does not modify the existing `KikiFast` tree or
the laptop inference manager.

The latency-critical split is deliberate:

- ESP32: microphones, speaker, touch AMOLED, Wi-Fi, playback buffering and UI.
- Laptop gateway: RNNoise, Silero VAD, speculative Whisper, the existing Kiki
  conversation/tool/background runtime, llama.cpp and omnivoice streaming.

See `docs/ARCHITECTURE.md` and `docs/DEPLOY.md` for the measured rationale and
deployment instructions.
