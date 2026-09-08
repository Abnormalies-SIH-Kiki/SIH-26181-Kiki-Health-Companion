# Source

Two codebases, published as working-tree snapshots rather than as imported git
history. History is not included because the private repositories carried
credentials in earlier commits.

| Path | Runs on | What it is |
|---|---|---|
| `desktop-module/` | Raspberry Pi 5 | The desktop unit. Conversation loop, tool calling, camera and Hailo vision, care agent, LCD/OLED, stepper. |
| `wearable/firmware/` | ESP32-S3 | Watch firmware. Audio, AMOLED UI, MAX30102, IMU, step counting, fall detection. C++, ESP-IDF 5.5. |
| `wearable/gateway/` | Laptop | What the watch connects to. Speech in, reply out, care sessions, telemetry ingest. |
| `wearable/scripts/` | Laptop | Build, flash, and run helpers. |

`wearable/gateway/legacy_kiki/` is a vendored older copy of the desktop code that
the gateway still reads a few modules from. It is not a second implementation.

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
