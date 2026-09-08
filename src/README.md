# Source

Three codebases, published as working-tree snapshots rather than as imported git
history. History is not included because the source repositories carried
credentials in earlier commits.

| Path | Runs on | What it is |
|---|---|---|
| `desktop-module/` | Raspberry Pi 5 | The desktop unit. Conversation loop, tool calling, camera and Hailo vision, care agent, LCD/OLED, stepper. |
| `wearable/firmware/` | ESP32-S3 | Watch firmware. Audio, AMOLED UI, MAX30102, IMU, step counting, fall detection. C++, ESP-IDF 5.5. |
| `wearable/gateway/` | Laptop | What the watch connects to. Speech in, reply out, care sessions, telemetry ingest. |
| `wearable/scripts/` | Laptop | Build, flash, and run helpers. |
| `health-analytics/` | Laptop | Trend and anomaly pipeline, plus the caregiver dashboard. |

`wearable/gateway/legacy_kiki/` is a vendored older copy of the desktop code that
the gateway still reads a few modules from. It is not a second implementation.

`health-analytics/` comes from
[AditiS721/SIH-Abnormalies-Software](https://github.com/AditiS721/SIH-Abnormalies-Software)
(branch `DevAniket`), included here with the author's permission. Inside it:

| | |
|---|---|
| `src/` | The Python pipeline: baseline, anomaly detection, daily summary, trend detection, risk score, and a JSON handoff for Kiki. Rule-based. |
| `src/train_tflite_model.py` | Optional and exploratory — "does ML help here?" — not something the pipeline depends on. |
| `esp32_export/` | An Arduino sketch that runs the anomaly model on the board. |
| `vitality-app/` | The caregiver dashboard. Next.js, local SQLite. |
| `stitch_vitality_health_dashboard/` | Design screens the dashboard was built from. |

Two things worth being clear about. The pipeline currently runs on generated
data (`generate_dummy_data.py`), not on live wearable telemetry — joining the two
is not done. And the anomaly model here is separate from the fall-detection
ladder in `wearable/firmware/main/kiki_fall_detector.hpp`; the firmware ladder is
what actually raises a fall alert on the watch today.

## What was removed before publishing

- `.env`, `gateway.env`, `tools_and_config/config.json`, the Vertex
  service-account key, `sdkconfig`, and every built `.bin` — the gateway token
  is compiled into a firmware image, so a published image would disclose it.
- Runtime state: conversation transcripts, recorded speech, the thinking
  journal, the care plan, face data, logs, and the WhatsApp message store.
- Build output, `managed_components/`, `whisper.cpp`, and the vendored Go
  toolchain tarball.
- Contact phone numbers in test fixtures, replaced with numbers in reserved
  ranges. The tests still assert the same behaviour.

`.env.example`, `tools_and_config/config.example.json` and
`gateway.env.example` list every setting with the values blanked.

## Tests

Neither suite needs hardware, a running model, or an API key.

```bash
cd desktop-module   && PYTHONPATH=. python -m pytest -q   # 1101 passed
cd wearable/gateway && PYTHONPATH=. python -m pytest -q   # 538 passed
```

`health-analytics` has no test suite. It has an end-to-end run instead, which
writes its output to `data/`:

```bash
cd health-analytics && pip install -r requirements.txt && python src/pipeline.py
```

A copy of what that produces is in [../docs/data/analytics_pipeline_output.json](../docs/data/analytics_pipeline_output.json).
