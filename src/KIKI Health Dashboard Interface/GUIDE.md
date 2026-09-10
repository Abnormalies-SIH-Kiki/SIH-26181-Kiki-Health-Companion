# KIKI Health Dashboard — Complete Project & Integration Guide

Welcome to the **Kiki Health Dashboard** project (Smart India Hackathon). This comprehensive guide is designed for all team members — frontend developers, hardware/IoT engineers, and AI/backend specialists — explaining how to clone, run, view, and contribute to this GitHub repository.

---

## 📌 1. Project Overview

**Kiki** is an intelligent, mobile-first health monitoring, environmental telemetry, and safety companion app tailored for Indian users. It bridges real-time physiological hardware telemetry with hyper-local environmental forecasts (IMD heatwave alerts, UV indices, air quality) and emergency safety mechanisms (fall detection and heart rate anomaly symptom assessments).

### Key Architectural Pillars
- **Mobile-First UX**: Premium dark-mode glassmorphic interface (OLED blacks, neon cyan, electric blue, and Indian flag accents).
- **Persistent Local Database**: Zero cloud dependencies during local testing via embedded SQLite (`better-sqlite3`).
- **Reactive Server Synchronization**: Next.js Server Components automatically re-render when new hardware or API telemetry is inserted into the database.

---

## 🛠 2. Tech Stack

- **Framework**: Next.js 14+ (App Router)
- **Language**: TypeScript (`strict` mode)
- **Styling**: Tailwind CSS with custom glassmorphism tokens
- **Typography & Icons**: Google Fonts (*Rozha One*, *Yatra One*, *Inter*, *Hanken Grotesk*) & Material Symbols Outlined
- **Database**: Embedded SQLite (`data/vitality.db`) via `better-sqlite3`

---

## 🗂 3. Project Directory Map

```text
KIKI Health Dashboard Interface/
├── KIKI-app/                         # Next.js Application Core
│   ├── app/
│   │   ├── api/                      # Backend Route Handlers
│   │   │   ├── onboarding/route.ts   # User registration & goal initialization
│   │   │   └── user/route.ts         # GET profile, PUT updates, DELETE account wipe
│   │   ├── dashboard/                # Main Home Screen
│   │   │   ├── HeartRateTrends.tsx   # Interactive Graph & Summary list toggle
│   │   │   └── page.tsx              # Rings, Calories, Steps, Metric Cards
│   │   ├── onboarding/               # 3-Step First-Time Onboarding
│   │   │   ├── personal-details/     # Step 1: Mascot & Tricolor "नमस्ते" Calligraphy
│   │   │   ├── location/             # Step 2: India-locked & City selection dropdown
│   │   │   └── daily-goals/          # Step 3: Steps & Calorie targets + Buffer animation
│   │   ├── weather/                  # Weather, AQI & IMD Heatwave Section
│   │   │   ├── Weatherindexpage.tsx  # Core weather component with 5 heatwave protocols
│   │   │   └── page.tsx              # Weather entry point re-exporter
│   │   ├── sleep/                    # Sleep Quality, Hardware Sync & Sleep Trends
│   │   ├── profile/                  # Profile & Preferences (Neon Blue / Cyan theme)
│   │   │   ├── edit/                 # Edit profile data (syncs to DB)
│   │   │   ├── edit-goals/           # Edit health targets (syncs to DB & Dashboard)
│   │   │   └── DeleteAccountButton.tsx# Account reset modal (wipes DB & starts fresh)
│   │   ├── fall-detection/           # Fall alert log & emergency contacts
│   │   ├── heart-rate-alerts/        # Clinical HR assessment logs
│   │   │   ├── high/                 # High HR (>100 BPM) symptoms & ECG equalizer
│   │   │   └── low/                  # Low HR (<60 BPM) symptoms & baseline comparison
│   │   ├── layout.tsx                # Global HTML shell & font imports
│   │   └── globals.css               # Global tokens, noise & dark mode CSS
│   ├── components/
│   │   ├── NamasteCalligraphy.tsx    # SVG Indian flag tricolor Devanagari calligraphy
│   │   └── SleepTrendsCard.tsx       # 7D / 14D sleep duration bar chart
│   ├── data/
│   │   └── vitality.db               # Embedded SQLite database
│   ├── lib/
│   │   └── db.ts                     # Database connection & table schema
│   └── public/
│       └── namaste_mascot.png        # Fit India Namaste character asset
├── Design/                           # Exported UI designs and wireframes
└── SIH Dashboard APP Structure.md    # Product and feature specifications
```

---

## 🌐 4. How to Clone, View, and Work with This GitHub Repo

Follow these steps to pull the code, launch it locally, and explore each feature:

### Step 1: Clone and Checkout the Branch
```bash
# Clone the repository
git clone <your-repository-url>
cd <repository-folder-name>

# Fetch and switch to the project branch
git fetch origin
git checkout <target-branch-name>
```

### Step 2: Navigate to the Web App
```bash
cd "KIKI Health Dashboard Interface/KIKI-app"
# Or run from the repository root: npm run dev
```

### Step 3: Install Dependencies
```bash
npm install
```
> *Note: SQLite dependencies (`better-sqlite3`) will compile automatically. No external database setup or Docker container is required.*

### Step 4: Run the Development Server
```bash
npm run dev
```
Open your browser and navigate to:
👉 **`http://localhost:3000`**

### Step 5: How to View the App (Crucial Step)
Because **Vitality** is designed specifically for mobile screens:
1. Open Chrome, Edge, or Safari DevTools (`F12` or Right Click ➡️ **Inspect**).
2. Press `Cmd + Shift + M` (macOS) or `Ctrl + Shift + M` (Windows/Linux) to toggle the **Device Toolbar**.
3. Select a modern mobile preset such as **iPhone 14/15 Pro**, **Pixel 7**, or set the viewport to **`390 x 844 px`**.

---

## 📱 5. Interactive Walkthrough of User Flows

When testing or demonstrating the app, follow this progression:

### A. Genuinely New User Onboarding
1. **Fresh Start**: If no account exists in SQLite, visiting `http://localhost:3000` immediately routes to `/onboarding/personal-details`.
2. **Step 1 — Personal Details**:
   - Features the Fit India Mascot and the shiny tricolor **"नमस्ते"** Devanagari calligraphy.
   - Enter Full Name, choose Gender, and pick Age via the stepper.
3. **Step 2 — Activity & Location Baseline**:
   - Country is locked to **India (IN) 🇮🇳**.
   - Select your city from top Indian metros (*New Delhi, Mumbai, Kolkata, Chennai, Pune, Ahemdabad*), or pick **"Any other - please tell"** to write your custom city.
4. **Step 3 — Health Goals & Setup Buffer**:
   - Select daily baseline steps (5,000 to 12,000) and active calorie burn (300 to 900 kcal).
   - Click **Save goals & continue**: Watch the animated setup buffer (*"Saving health profile..."* ➡️ *"Calibrating activity baselines..."* ➡️ *"Finalizing dashboard..."*) transition smoothly to the dashboard.

### B. Daily Dashboard Experience (`/dashboard`)
- **Interactive Metric Rings**: Real-time rings displaying calories burned and step counts calibrated against your personal goals.
- **Heart Rate Trends**: Click the **Summary** tab to see exact timestamps with heart rate values in list format, or switch back to **Graph** view. Filter across `TODAY`, `7D`, `14D`, and `30D`.
- **Navigation Bar**: Bottom navigation with active red indicator on Home, routing to **Weather**, **Sleep**, and **Profile**.

### C. Weather & IMD Heatwave Protocol (`/weather`)
- Severe Weather Advisory banner from IMD.
- 24-hour heat index & temperature curves.
- Air Quality (AQI) with PM2.5 monitoring.
- Interactive list of the **5 Heatwave Protocols** (Hydrate Regularly, Light Clothing, Shade & Cooling, Symptom Watch, Vulnerables & Pets).

### D. Sleep Quality & Trends (`/sleep`)
- Sleep stages breakdown (Deep, Light, REM, Awake).
- Interactive **Sleep Trends Card** toggling 7-day and 14-day duration bars and score curves.
- The **4 Sleep Protocols** (Consistent Schedule, Sleep Environment, Wind-Down, Sunlight Exposure).

### E. Safety Center & Clinical Assessments
- **Fall Detection (`/fall-detection`)**: Simulated fall event logs, severity flags, and emergency action triggers.
- **High Heart Rate Assessment (`/heart-rate-alerts/high`)**: ECG equalizer animation, above-range banner (`118 BPM`), and symptom multi-select grid.
- **Low Heart Rate Assessment (`/heart-rate-alerts/low`)**: Baseline comparison bar (`48 BPM`) and clinical advisory.

### F. Profile, Edits & Account Reset (`/profile`)
- **Neon Blue & Electric Cyan** aesthetic.
- **Edit Profile (`/profile/edit`)** & **Edit Goals (`/profile/edit-goals`)**: Update your details and see them immediately sync with the database and dashboard rings.
- **Delete My Account**: Click the red button to open the confirmation modal. Deleting wipes all database records and local storage, immediately returning you to the 3 onboarding cards to start fresh.

---

## ⚡️ 6. Hardware & IoT Sensor Integration Guide

The frontend is built to accept incoming hardware streams without requiring redesigns. Here is how your hardware teammates can plug their sensors into the app:

### 6.1 Real-Time Heart Rate Stream (`HeartRateTrends.tsx`)
- **Hardware Source**: Photoplethysmography (PPG) sensors (e.g., MAX30102, MAX30100), Bluetooth LE heart rate straps, or wearable microcontrollers (ESP32 / Arduino / Raspberry Pi).
- **Connecting the Sensor**:
  Create an API endpoint `app/api/telemetry/heart-rate/route.ts`:
  ```typescript
  import db from '@/lib/db';
  import { revalidatePath } from 'next/cache';
  import { NextResponse } from 'next/server';

  export async function POST(req: Request) {
    const { bpm } = await req.json();
    const user = db.prepare('SELECT id FROM users LIMIT 1').get() as any;
    
    db.prepare(`
      INSERT INTO health_data (user_id, metric_type, value, recorded_at)
      VALUES (?, 'heart_rate', ?, datetime('now', 'localtime'))
    `).run(user.id, bpm);

    revalidatePath('/dashboard');
    return NextResponse.json({ success: true });
  }
  ```

### 6.2 Sleep Patterns & Stages (`SleepTrendsCard.tsx` & `/sleep`)
- **Hardware Source**: Movement actigraphy from 3-axis/6-axis IMUs (accelerometers) and nocturnal PPG heart rate variability (HRV).
- **Connecting the Sensor**:
  When the device completes a sleep session:
  ```typescript
  // Post sleep epoch data:
  // { duration: 450, deep: 25, light: 50, rem: 20, awake: 5, score: 85 }
  ```
  The sleep cards automatically render the duration bars and score curves directly from these database entries.

### 6.3 Fall Detection Telemetry (`/fall-detection`)
- **Hardware Source**: Accelerometer & Gyroscope (e.g., MPU6050, LSM6DS3, BNO055).
- **Trigger Logic**: High-G acceleration impact spike (`> 3.0g`) immediately followed by an orientation angle change and a period of zero movement (inactivity).
- The microcontroller sends a fall event:
  `{ timestamp: "10:45 AM", severity: "High Severity", location: "Living Room" }`
- The `/fall-detection` screen immediately renders the new event card with its severity badge.

---

## 💾 7. Database & Persistence Layer

- **Database Path**: `data/vitality.db` (SQLite)
- **Initialized in**: `lib/db.ts`
- **Tables**:
  - `users`: User demographic data, location (`${city}, India`), and daily targets (`daily_steps_goal`, `daily_calories_goal`).
  - `health_data`: Timestamped sensor readings (`metric_type`, `value`, `recorded_at`).

### Instant UI Synchronization (`revalidatePath`)
Because Next.js uses React Server Components, running `revalidatePath('/dashboard')` or `revalidatePath('/sleep')` after inserting database records causes Next.js to re-fetch the fresh data on the server and stream it to the client with **zero page reload flicker**.

---

## 🤝 8. Daily Git Contribution Workflow

When working on a new feature or sensor integration:

1. **Pull the latest target branch**:
   ```bash
   git fetch origin
   git checkout <target-branch>
   git pull origin <target-branch>
   ```

2. **Create a descriptive feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **Validate TypeScript before staging**:
   ```bash
   npx tsc --noEmit
   ```
   *Make sure there are 0 compilation errors.*

4. **Commit with clean conventional messages**:
   ```bash
   git commit -m "feat(sensor): add ESP32 telemetry ingestion endpoint"
   ```

5. **Push and create a Pull Request**:
   ```bash
   git push origin feature/your-feature-name
   ```
