// risk_engine.h -- combines per-signal outputs into an explainable risk score
// and serializes the structured JSON payload for Kiki (spec §10).
#ifndef RISK_ENGINE_H
#define RISK_ENGINE_H

#include <ArduinoJson.h>
#include "baseline.h"
#include "fall_detector.h"

typedef struct {
    float avg_hr;
    float avg_spo2;
    int activity_label;          // activity_t value
    float activity_confidence;
    bool hr_anomaly;
    bool spo2_anomaly;
    bool trend_anomaly;
    bool fall_detected;
    int risk_score;              // 0-100
} risk_snapshot_t;

// Simple explainable scoring: each contributing factor adds a fixed weight,
// combined per spec §10 ("HR anomaly + extended inactivity -> elevated risk").
static inline int risk_engine_score(bool hr_anomaly, bool spo2_anomaly, bool trend_anomaly,
                                     bool fall_detected, int activity_label /* ACTIVITY_INACTIVE etc. */,
                                     uint32_t inactive_duration_s) {
    if (fall_detected) return 100; // fall always maximal/urgent
    int score = 0;
    if (hr_anomaly) score += 35;
    if (spo2_anomaly) score += 35;
    if (trend_anomaly) score += 15;
    if (inactive_duration_s > 3600) score += 15; // extended inactivity (>1h) compounds risk
    if (score > 99) score = 99; // reserve 100 exclusively for confirmed falls
    return score;
}

static inline const char *risk_engine_level(int score) {
    if (score >= 67) return "high";
    if (score >= 34) return "medium";
    return "low";
}

// Serializes the snapshot into the Kiki JSON schema from spec §10.
// buf/buf_len: caller-provided output buffer. Returns number of bytes written, or -1 on overflow.
int risk_engine_to_json(const risk_snapshot_t &snap, const char *activity_name,
                         const char *user_id, char *buf, size_t buf_len);

#endif // RISK_ENGINE_H
