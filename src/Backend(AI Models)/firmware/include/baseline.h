// baseline.h -- personal baseline engine: cold-start bootstrap, dual-timescale
// (acute + trend) Welford statistics, kept per activity state.
// Implements spec §2 (bootstrap), §3 (dual-timescale drift), §4 (activity-conditioned).
#ifndef BASELINE_H
#define BASELINE_H

#include <stdint.h>
#include <math.h>
#include "config.h"

// Welford's online mean/variance -- O(1) memory, no raw history needed.
typedef struct {
    uint32_t n;
    float mean;
    float M2;     // sum of squared deviations
    float std;
} welford_t;

static inline void welford_reset(welford_t *w) {
    w->n = 0; w->mean = 0.0f; w->M2 = 0.0f; w->std = 0.0f;
}

static inline void welford_update(welford_t *w, float x) {
    w->n += 1;
    float delta = x - w->mean;
    w->mean += delta / (float)w->n;
    float delta2 = x - w->mean;
    w->M2 += delta * delta2;
    w->std = (w->n > 1) ? sqrtf(w->M2 / (float)w->n) : 0.0f;
}

// A single metric (HR or SpO2) tracked at two timescales, for one activity state.
typedef struct {
    welford_t short_term;   // ~30 min rolling (EMA-approximated, see baseline_update)
    welford_t long_term;    // ~14 day rolling
    uint32_t total_samples; // used for the bootstrap weighting in phase 1
} metric_baseline_t;

typedef enum {
    ANOMALY_NONE = 0,
    ANOMALY_ACUTE,
    ANOMALY_TREND
} anomaly_type_t;

typedef struct {
    anomaly_type_t type;
    float z_score;
    bool low_confidence_widened;  // true if the widened (3.5 sigma) threshold from §4 was used
} anomaly_result_t;

// One metric_baseline_t per (activity state) for HR and SpO2 respectively.
extern metric_baseline_t g_hr_baseline[NUM_ACTIVITY_STATES];
extern metric_baseline_t g_spo2_baseline[NUM_ACTIVITY_STATES];

// Persistence tracking: consecutive-deviation counters per activity/metric.
// Reset to 0 whenever a sample is not deviating.
extern uint8_t g_hr_persist_count[NUM_ACTIVITY_STATES];
extern uint8_t g_spo2_persist_count[NUM_ACTIVITY_STATES];

void baseline_init();

// Returns which bootstrap phase we're in (0, 1, or 2) based on elapsed seconds
// since boot and how many valid samples this metric/activity pair has seen.
int baseline_bootstrap_phase(uint32_t elapsed_s, uint32_t total_samples);

// Updates the baseline for one metric/activity pair with a new (motion-gated-valid)
// sample, and returns whether it constitutes a persisted anomaly.
// activity_idx: 0..NUM_ACTIVITY_STATES-1 (from activity_tree.h's activity_t)
// activity_confidence: from the Decision Tree leaf purity (spec §6)
anomaly_result_t baseline_process_hr(int activity_idx, float hr, float activity_confidence, uint32_t elapsed_s);
anomaly_result_t baseline_process_spo2(int activity_idx, float spo2, float activity_confidence, uint32_t elapsed_s);

#endif // BASELINE_H
