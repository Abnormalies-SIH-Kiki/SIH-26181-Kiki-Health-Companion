# Kiki Health Companion – SIH 26181

**AI-powered Personal Health Companion with Edge AI, Voice Interface, and Disaster-Resilient Monitoring**

## 1. Project Information

- **Project Title:** Kiki Health Companion
- **PS ID:** SIH 26181
- **PS Title:** AI-powered Personal Health Companion
- **Category:** Hardware
- **Theme:** MedTech / BioTech / HealthTech

## 2. Problem Statement

India faces recurring health crises during heat waves, floods, pollution, and disasters. Vulnerable populations lack continuous, personalized health monitoring that works offline and respects privacy.

## 3. Proposed Solution

Kiki is a voice-first, privacy-preserving health companion that continuously integrates physiological data (heart rate, SpO₂, motion) with environmental data (temperature, humidity, AQI). On-device AI detects anomalies (e.g., elevated heart rate, fall, heat stress) and provides actionable alerts via voice, dashboard, and WhatsApp/email — even without internet.

## 4. Key Features

- Live vitals monitoring (HR, SpO₂, steps, ambient temp, pressure)
- Anomaly detection & voice alerts
- Fall detection & emergency SOS
- Disaster-specific warnings (heat, air quality)
- Personalized wellness recommendations
- Offline operation & edge AI privacy
- Voice interaction with Kiki (elderly/rural-friendly)

## 5. Technology Stack

- **Hardware:** Raspberry Pi 5, MAX30102, BMP280, ESP32-S3 wearable, IMU
- **Backend:** Python, FastAPI (or Flask), MQTT
- **AI/ML:** Rule-based anomaly detection, optional scikit-learn, edge inference
- **Frontend:** Web dashboard (React/Chart.js), Mobile App UI (Flutter/React Native)
- **Database:** SQLite/JSON (local)
- **Deployment:** Local / Docker optional

## 6. Architecture

See `docs/architecture.md`.

## 7. Repository Structure

```text
SIH-26181-Kiki-Health-Companion/
├── assets/
│   └── screenshots/
│       └── README.md
├── docs/
│   └── architecture.md
├── src/
│   ├── health_dashboard.py
│   └── main.py
├── submission/
│   ├── DEMO.md
│   └── PRESENTATION.md
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt
└── SUBMISSION_GUIDE.md
```

## 8. Run

**Start the main health companion:**

```bash
python src/main.py
```

**Start the web dashboard (separate terminal):**

```bash
python src/health_dashboard.py
```

**Make sure Kiki is running first** (if using the full voice companion).

For a minimal demo, use:

```bash
python src/main.py --no-voice
```

## 9. Installation

```bash
# 1. Clone the repository
git clone https://github.com/<YOUR_USERNAME>/SIH-26181-Kiki-Health-Companion.git
cd SIH-26181-Kiki-Health-Companion

# 2. Create a virtual environment (recommended)
python -m venv venv

# Activate it:
#   On Linux/Raspberry Pi/macOS:
source venv/bin/activate
#   On Windows:
.\venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment template and fill in values (if any)
cp .env.example .env
# edit .env as needed

# 5. Connect hardware sensors (MAX30102, BMP280, etc.) before running
```

## 10. Future Scope

- **Continuous wrist SpO₂ & body temperature** using improved sensor fusion and ML-based motion artifact removal.
- **GPS / location tracking** for outdoor fall detection and emergency response.
- **Automatic sleep quality analysis** using wearable accelerometer and heart rate variability.
- **Multi‑language voice support** (Hindi, Marathi, Bengali, etc.) via Bhashini/Sarvam APIs.
- **Integration with government health schemes** (Ayushman Bharat, Tele‑MANAS, e‑Sanjeevani) for referral and telehealth.
- **Federated learning** across devices to improve anomaly models without sharing raw health data.
- **BLE mesh / LoRa** for community‑level disaster alerts in low‑connectivity areas.
- **Clinical validation** and certification as a Class B medical device (CDSCO).
