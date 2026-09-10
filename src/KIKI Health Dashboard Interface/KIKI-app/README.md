# KIKI Health App — Developer & Contributor Guide

Welcome to the **KIKI Health Dashboard** project (Smart India Hackathon). This document provides an end-to-end walkthrough of the architecture, features, database structure, and guidelines for team members contributing to this repository.

---

## 📌 Project Overview

**KIKI** is a mobile-first personal health, environmental telemetry, and safety companion app built for Indian users. It combines real-time physiological indicators (heart rate, step cadence, metabolic burn, sleep stages) with hyper-local environmental intelligence (IMD heatwave alerts, UV indices, air quality) and safety triggers (fall detection, tachy/bradycardia symptom logs).

### Tech Stack
- **Framework**: Next.js 14+ (App Router)
- **Language**: TypeScript (`strict` mode)
- **Styling**: Tailwind CSS (Custom dark theme with glassmorphism and neon accents)
- **Icons & Fonts**: Google Material Symbols Outlined, *Inter*, *Hanken Grotesk*, and Devanagari calligraphy (*Rozha One*, *Yatra One*)
- **Database**: Embedded SQLite via `better-sqlite3` (zero cloud dependencies for local dev)

---

## 🗂 Project Structure

```text
Dashboard Frontend/
├── KIKI-app/                     # Main Next.js Web Application
│   ├── app/
│   │   ├── api/                      # Backend Route Handlers
│   │   │   ├── onboarding/route.ts   # Account creation endpoint
│   │   │   └── user/route.ts         # GET user, PUT updates, DELETE wipe
│   │   ├── dashboard/                # Home Screen
│   │   │   ├── HeartRateTrends.tsx   # Interactive Graph & Summary toggle
│   │   │   └── page.tsx              # Rings, Calories, Steps, Metric Cards
│   │   ├── onboarding/               # 3-Step First-Time Onboarding
│   │   │   ├── personal-details/     # Step 1: Name, Age, Gender + Namaste Mascot
│   │   │   ├── location/             # Step 2: India-locked & City selection
│   │   │   └── daily-goals/          # Step 3: Steps & Calorie targets + Buffer animation
│   │   ├── weather/                  # Weather, AQI & IMD Heatwave Section
│   │   │   ├── Weatherindexpage.tsx  # Core weather component
│   │   │   └── page.tsx              # Weather entry point
│   │   ├── sleep/                    # Sleep Quality, Hardware Sync & Sleep Trends
│   │   ├── profile/                  # Profile & Preferences (Neon Blue theme)
│   │   │   ├── edit/                 # Edit profile data
│   │   │   ├── edit-goals/           # Edit health targets
│   │   │   └── DeleteAccountButton.tsx# Account reset modal
│   │   ├── fall-detection/           # Fall alert log & emergency contacts
│   │   ├── heart-rate-alerts/        # Clinical HR assessment logs
│   │   │   ├── high/                 # High HR (>100 BPM) symptoms
│   │   │   └── low/                  # Low HR (<60 BPM) symptoms
│   │   ├── layout.tsx                # Global HTML shell & font imports
│   │   └── globals.css               # Global tokens, noise & dark mode CSS
│   ├── components/
│   │   ├── NamasteCalligraphy.tsx    # SVG Indian tricolor Devanagari calligraphy
│   │   └── SleepTrendsCard.tsx       # 7D / 14D sleep duration bar chart
│   ├── data/
│   │   └── vitality.db               # SQLite database file
│   ├── lib/
│   │   └── db.ts                     # Database connection & schema migration
│   └── public/
│       └── namaste_mascot.png        # Fit India Namaste character asset
├── stitch_vitality_health_dashboard/ # Exported UI designs and wireframes
└── SIH Dashboard APP Structure.md    # Product and feature specifications
```

---

## 🚀 Getting Started

### 1. Prerequisites
- **Node.js**: `v18.17.0` or higher (Node 20+ recommended)
- **Package Manager**: `npm` (comes with Node)

### 2. Installation
```bash
cd KIKI-app
npm install
```

### 3. Start Development Server
```bash
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

> [!TIP]
> **Mobile View Recommended**: Press `F12` to open Chrome/Edge DevTools and toggle **Device Toolbar** (`Cmd + Shift + M` or `Ctrl + Shift + M`), choosing an iPhone 14/15 or Pixel frame. The UI is custom tailored for mobile viewports.

---

## 💡 Key Architectural Concepts

### 1. Persistent Sessions & Genuinely New Users
- **Root Routing (`app/page.tsx`)**: Queries SQLite `SELECT * FROM users LIMIT 1`.
  - If **no user exists**: Redirects automatically to `/onboarding/personal-details`.
  - If **user exists**: Redirects straight to `/dashboard`.
- **Onboarding Auth Guard**: The 3 onboarding pages inspect `/api/user` on mount. A registered user cannot accidentally re-enter onboarding; they are redirected back to the dashboard.
- **Account Reset**: Inside `/profile`, clicking **Delete My Account** triggers `DELETE /api/user`, purging `users` and `health_data` tables and clearing local cache, returning the app to the initial clean onboarding state.

### 2. State & Database Layer (`lib/db.ts`)
We use `better-sqlite3` directly inside Next.js Server Components and Route Handlers for high performance and zero external setup:
```typescript
// Example: Querying in a Server Component
import db from '@/lib/db';

const user = db.prepare('SELECT * FROM users LIMIT 1').get();
```
Whenever profile details or daily goals are modified, the API routes invoke `revalidatePath(...)` to ensure server caches update instantly.

### 3. Visual & Aesthetic Guidelines
- **Palette**: Deep OLED blacks (`#070708`, `#0a0a0c`, `#141417`), dark gray card containers (`#1C1C1E`), with high-visibility accent colors:
  - **Dashboard**: Red highlight on bottom navigation (`#EF4444`).
  - **Calorie Rings**: Calorie Green (`#22C55E`).
  - **Steps Rings**: Electric Blue (`#3B82F6`).
  - **Profile Page**: Neon Cyan / Electric Blue accents.
  - **Onboarding**: Indian Saffron (`#FF671F`), Diamond White (`#FFFFFF`), and Emerald Green (`#046A38`).
- **Typography**: Google Fonts loaded in `app/layout.tsx`. Use clean semantic typography classes.

---

## 🤝 How to Contribute

### 1. Git Workflow
1. **Pull the latest branch**:
   ```bash
   git checkout <feature-branch>
   git pull origin <feature-branch>
   ```
2. **Create your sub-branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** according to the design system.
4. **Validate TypeScript**:
   ```bash
   npx tsc --noEmit
   ```
   *Ensure 0 compile errors before committing.*
5. **Commit with descriptive messages**:
   ```bash
   git commit -m "feat(weather): add 24-hour heat index trend line"
   ```
6. **Push and create a Pull Request**:
   ```bash
   git push origin feature/your-feature-name
   ```

### 2. Connecting Real IoT Sensors / ML Models
If you are working on the hardware or AI/ML backend:
- Add endpoints under `app/api/telemetry/` or `app/api/sensors/`.
- Insert readings into SQLite (`data/vitality.db`) or consume webhooks.
- The frontend components (e.g. `HeartRateTrends.tsx`, `SleepTrendsCard.tsx`) are modular and ready to accept live props or SWR/React Query hooks.

---

## 📞 Support & Contacts
If you run into any dependency issues, database lock warnings, or questions regarding layout styling, check the `SIH Dashboard APP Structure` document in this folder or message the project maintainer.
