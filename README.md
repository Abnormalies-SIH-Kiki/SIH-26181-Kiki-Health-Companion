**SIH 26181 · A secure, AI-powered Personal Health Companion**

Hardware · MedTech / BioTech / HealthTech

Kiki is a desktop companion and a wearable built around the same idea. The desktop stays in the room and handles conversation, reminders and camera-based activity checks. The wearable goes with the person and handles steps, on-demand heart rate and possible falls.

We're A3SCV (Team Abnormalies) from NSUT: Vaibhav Arora, Suyash Srivastava (team lead), Aniket Sharma, Chirag Goel, Aditi Sharma and Aavya.

<img width="1121" height="629" alt="Desktop and wearable health companion and laptop" src="https://github.com/user-attachments/assets/f0c9d039-fb39-44d2-8e02-21e835055e62" />


## What we're building

A health reminder is more useful when it fits the person's day. Kiki keeps routines and recent observations together, so someone can talk through a reminder or an exercise instead of working through an app. Voice interaction supports English and Hindi.

The desktop has a camera for activity detection, face recognition and exercise checks. The wearable uses wrist motion, with fall detection running in firmware. If it detects a possible fall, it gives the wearer time to respond before requesting a family alert through the gateway.

Both devices use a laptop for speech recognition, speech generation and the local conversation model. Some reasoning runs through cloud services. Local speech processing and firmware fall detection don't need internet, but the devices still need access to the laptop for conversation. Weather updates, cloud agents and WhatsApp/email alerts need connectivity.

The prototype currently includes:

- Scheduled care sessions, shared care history and spoken reminders.
- Heart-rate readings with signal-quality checks, plus steps and wear detection.
- Possible-fall check-ins and family alerts, with send and delivery status recorded separately.
- Weather and estimated AQI on the CPCB scale, with stale readings marked or removed.
- Camera activity checks and guided exercise sessions on the desktop.

The analytics pipeline and caregiver dashboard also run, but they're separate prototypes for now. The pipeline uses generated data, and the dashboard has its own SQLite database. Neither receives live wearable telemetry yet.

## Hardware and software

| Part | Current setup |
|---|---|
| Desktop | Raspberry Pi 5, Hailo-8 vision accelerator, USB webcam, 28BYJ-48 stepper, LCD and OLED displays, IR controls, Bluetooth speaker |
| Wearable | Waveshare ESP32-S3-Touch-AMOLED-1.75, MAX30102, QMI8658 IMU, microphones and speaker, 1000 mAh battery |
| Inference laptop | RTX 4060, llama.cpp with gemma-4-26B-A4B, whisper.cpp and OmniVoice |
| Device software | Python desktop module and WebSocket gateway; C++ firmware on ESP-IDF 5.5 |
| Cloud services | Cerebras for multi-step agents, Gemini for background reasoning |
| Analytics | Python, pandas and NumPy; Next.js/TypeScript dashboard with SQLite; optional TFLite experiment |

![System architecture](assets/CAD_model/renders/design_sheet.png)

The Pi and wearable handle the physical inputs and outputs. The laptop runs inference and the wearable gateway. Our setup connects the machines through Tailscale. The local model has one inference slot, so background work must leave it available when someone speaks.

The [engineering notes](docs/ENGINEERING.md) cover wiring, audio, latency and the failures that shaped the build.

## Run it

These commands start from the repository root, in separate terminals for each component. You'll need the hardware for the full demo, Python 3.12+ for the gateway, and ESP-IDF 5.5 for firmware builds. The model, speech and hardware services need to be set up separately; installing the Python packages doesn't start them.

**Desktop, on the Pi**

```bash
cd src/desktop-module
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp tools_and_config/config.example.json tools_and_config/config.json
# Set API keys and update service addresses for your machines.
python main.py
```

**Gateway, on the laptop**

```bash
cd src/wearable
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e gateway
cp gateway.env.example gateway.env
# Set the gateway token, inference URLs and shared care-plan paths.
./scripts/run_gateway.sh
```

**Wearable firmware**, from a shell with ESP-IDF loaded:

```bash
cd src/wearable/firmware
idf.py set-target esp32s3
idf.py menuconfig
# Set Wi-Fi, gateway address and the matching gateway token.
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

Change the serial port if your board appears elsewhere. More deployment details are in [docs/wearable/DEPLOY.md](docs/wearable/DEPLOY.md). Keep credentials in your local configuration files.

## Code and build files

| Location | Contents |
|---|---|
| [src/](src/README.md) | Code map, test commands and analytics setup |
| [docs/ENGINEERING.md](docs/ENGINEERING.md) | Technical notes and known limits |
| [docs/data/](docs/data/) | Captured system output and analytics sample |
| [assets/hardware/](assets/hardware/) | Build photos |
| [assets/circuit_diagram/](assets/circuit_diagram/) | Wiring diagrams and Fritzing sources |
| [assets/CAD_model/](assets/CAD_model/) | Enclosure source, renders and print files |
| [submission/PRESENTATION.md](submission/PRESENTATION.md) | Presentation details |
| [submission/DEMO.md](submission/DEMO.md) | Demo details |

## What's left

Fall detection uses heuristic thresholds. We've tried it during development, but controlled physical acceptance testing and clinical validation are still pending. The MAX30102's SpO₂ estimate is uncalibrated and isn't treated as a health reading, even though an early prototype photo shows it on the display.
