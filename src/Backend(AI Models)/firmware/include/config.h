// config.h -- all tunable constants for the health telemetry pipeline.
// Values correspond directly to the executable spec (sections referenced in comments).
#ifndef CONFIG_H
#define CONFIG_H

// ---- Sampling rates (spec §1) ----
#define IMU_FS_HZ         50
#define VITALS_FS_HZ      1
#define IMU_WINDOW_S      1.0f         // window used for activity-tree features
#define IMU_WINDOW_LEN    (IMU_FS_HZ * (int)IMU_WINDOW_S)   // 50 samples

// ---- Bootstrap / cold-start (spec §2) ----
#define BOOTSTRAP_PHASE0_S        (5 * 60)     // 0-5min: population prior only
#define BOOTSTRAP_PHASE1_TARGET_N 500          // samples until fully personalized
#define PERSISTENCE_SAMPLES       3            // consecutive samples required to confirm an anomaly

// Population-prior gross thresholds used during Phase 0
#define POP_HR_LOW      50.0f
#define POP_HR_HIGH     100.0f
#define POP_HR_GROSS_HIGH 140.0f   // alert even in phase 0 if exceeded at rest
#define POP_SPO2_LOW     95.0f
#define POP_SPO2_GROSS_LOW 90.0f

// ---- Dual-timescale baseline (spec §3) ----
#define BASELINE_SHORT_WINDOW_S   (30 * 60)    // 30 min
#define BASELINE_LONG_WINDOW_S    (14 * 24 * 3600) // 14 days
#define ACUTE_ANOMALY_SIGMA       2.5f
#define TREND_ANOMALY_SIGMA       1.5f
#define TREND_SUSTAIN_S           (6 * 3600)   // 6 hours

// ---- Activity classes (spec §4/§6) ----
#define NUM_ACTIVITY_STATES 5   // Resting, Inactive, Walking, Running, Transition
#define ACTIVITY_CONFIDENCE_LOW 0.7f
#define LOW_CONFIDENCE_SIGMA_WIDEN 3.5f   // used instead of ACUTE_ANOMALY_SIGMA when confidence is low

// ---- PPG motion-artifact gate (spec §5) ----
// Acceleration-variance threshold above which HR/SpO2 samples are treated as low-confidence.
// Tuned per activity state; index matches activity_t enum order in activity_tree.h.
static const float MOTION_GATE_THRESHOLD[NUM_ACTIVITY_STATES] = {
    0.05f,   // Inactive
    0.10f,   // Resting
    0.60f,   // Running
    0.35f,   // Transition
    0.40f,   // Walking
};

// ---- Fall detection (spec §7/§8) ----
#define FALL_PROB_THRESHOLD        0.8f
#define FALL_CONFIRM_WINDOW_S      5
#define FALL_STILLNESS_ACC_TOL     0.05f   // |acc_mag - 1.0g| below this counts as "still"
#define FALL_STILLNESS_GYRO_TOL    3.0f    // deg/s
#define FALL_STILLNESS_MIN_S       3       // consecutive seconds of stillness to confirm

// ---- TFLite Micro tensor arena (spec §9 -- measure actual usage with
// tflite::RecordingMicroAllocator during bring-up and adjust) ----
#define TENSOR_ARENA_SIZE (60 * 1024)

// ---- Risk scoring ----
#define RISK_LOW_MAX      33
#define RISK_MED_MAX      66

// ---- Serial / debug ----
#define SERIAL_BAUD 115200

// ---- WiFi + FastAPI push endpoint ----
// Fill in your real WiFi credentials below. Do NOT use 127.0.0.1/localhost --
// that refers to the ESP32 itself, not your PC.
#define WIFI_SSID        "Redmi Note 10 Pro"
#define WIFI_PASSWORD    "ejehdmcgka75yvd"
#define FASTAPI_ENDPOINT "http://192.168.29.96:8000/telemetry"

#endif // CONFIG_H