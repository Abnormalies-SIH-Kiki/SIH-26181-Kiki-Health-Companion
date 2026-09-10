// baseline.cpp -- see baseline.h for the design rationale.
#include <math.h>
#include "baseline.h"

metric_baseline_t g_hr_baseline[NUM_ACTIVITY_STATES];
metric_baseline_t g_spo2_baseline[NUM_ACTIVITY_STATES];
uint8_t g_hr_persist_count[NUM_ACTIVITY_STATES];
uint8_t g_spo2_persist_count[NUM_ACTIVITY_STATES];

// EMA smoothing factors approximating the two rolling windows from spec §3.
// alpha = 2 / (N + 1); N expressed in samples at 1 Hz (vitals rate).
static const float EMA_ALPHA_SHORT = 2.0f / (BASELINE_SHORT_WINDOW_S + 1.0f); // ~30 min
static const float EMA_ALPHA_LONG  = 2.0f / (2000.0f + 1.0f);                  // slow-moving proxy for 14-day trend
// NOTE: a true 14-day EMA (alpha ~= 2/(1.2M+1)) adapts too slowly to be useful during
// bench/demo runs with dummy data; EMA_ALPHA_LONG is set to a demo-scale slow constant.
// For a real long-duration deployment, recompute this from BASELINE_LONG_WINDOW_S.

void baseline_init() {
    for (int i = 0; i < NUM_ACTIVITY_STATES; i++) {
        welford_reset(&g_hr_baseline[i].short_term);
        welford_reset(&g_hr_baseline[i].long_term);
        g_hr_baseline[i].total_samples = 0;

        welford_reset(&g_spo2_baseline[i].short_term);
        welford_reset(&g_spo2_baseline[i].long_term);
        g_spo2_baseline[i].total_samples = 0;

        g_hr_persist_count[i] = 0;
        g_spo2_persist_count[i] = 0;
    }
}

int baseline_bootstrap_phase(uint32_t elapsed_s, uint32_t total_samples) {
    if (elapsed_s < BOOTSTRAP_PHASE0_S) return 0;
    if (total_samples < BOOTSTRAP_PHASE1_TARGET_N) return 1;
    return 2;
}

// EMA update: mean/var maintained incrementally, cheap O(1) memory (spec §3).
static void ema_update(welford_t *w, float x, float alpha) {
    if (w->n == 0) {
        w->mean = x;
        w->M2 = 0.0f;
    } else {
        float delta = x - w->mean;
        w->mean += alpha * delta;
        // exponentially-weighted variance approximation
        w->M2 = (1.0f - alpha) * (w->M2 + alpha * delta * delta);
    }
    w->n += 1;
    w->std = sqrtf(w->M2);
}

// Core shared logic for HR and SpO2 -- see baseline.h for anomaly_result_t semantics.
static anomaly_result_t process_metric(metric_baseline_t *b, uint8_t *persist_count,
                                        float value, float activity_confidence,
                                        uint32_t elapsed_s, float pop_low, float pop_high,
                                        float pop_gross_low, float pop_gross_high) {
    anomaly_result_t result = { ANOMALY_NONE, 0.0f, false };
    int phase = baseline_bootstrap_phase(elapsed_s, b->total_samples);

    if (phase == 0) {
        // Phase 0 (spec §2): population prior only, gross-deviation alerts.
        if (value < pop_gross_low || value > pop_gross_high) {
            (*persist_count)++;
        } else {
            *persist_count = 0;
        }
        if (*persist_count >= PERSISTENCE_SAMPLES) {
            result.type = ANOMALY_ACUTE;
            result.z_score = 0.0f; // no personal baseline yet, so no z-score
        }
        // Still accumulate stats so phase 1/2 have data once we cross the time threshold.
        ema_update(&b->short_term, value, EMA_ALPHA_SHORT);
        ema_update(&b->long_term, value, EMA_ALPHA_LONG);
        b->total_samples++;
        return result;
    }

    // Phase 1/2: blend population prior with personal short-term baseline (spec §2).
    float weight_personal = (phase == 2) ? 1.0f : fminf((float)b->total_samples / BOOTSTRAP_PHASE1_TARGET_N, 1.0f);
    float pop_mid = (pop_low + pop_high) / 2.0f;
    float pop_std = (pop_high - pop_low) / 2.0f; // rough spread estimate
    float effective_mean = weight_personal * b->short_term.mean + (1.0f - weight_personal) * pop_mid;
    float effective_std  = weight_personal * b->short_term.std  + (1.0f - weight_personal) * pop_std;
    if (effective_std < 1e-3f) effective_std = 1e-3f; // avoid div-by-zero early on

    float z = fabsf(value - effective_mean) / effective_std;

    float acute_threshold = ACUTE_ANOMALY_SIGMA;
    if (activity_confidence < ACTIVITY_CONFIDENCE_LOW) {
        acute_threshold = LOW_CONFIDENCE_SIGMA_WIDEN; // spec §4 misclassification safety net
        result.low_confidence_widened = true;
    }

    if (z > acute_threshold) {
        (*persist_count)++;
    } else {
        *persist_count = 0;
    }
    if (*persist_count >= PERSISTENCE_SAMPLES) {
        result.type = ANOMALY_ACUTE;
        result.z_score = z;
    }

    // Trend anomaly: short baseline drifted from long baseline (spec §3).
    if (b->long_term.n > 0 && b->long_term.std > 1e-3f) {
        float trend_z = fabsf(b->short_term.mean - b->long_term.mean) / b->long_term.std;
        if (trend_z > TREND_ANOMALY_SIGMA && result.type == ANOMALY_NONE) {
            result.type = ANOMALY_TREND;
            result.z_score = trend_z;
        }
    }

    ema_update(&b->short_term, value, EMA_ALPHA_SHORT);
    ema_update(&b->long_term, value, EMA_ALPHA_LONG);
    b->total_samples++;
    return result;
}

anomaly_result_t baseline_process_hr(int activity_idx, float hr, float activity_confidence, uint32_t elapsed_s) {
    return process_metric(&g_hr_baseline[activity_idx], &g_hr_persist_count[activity_idx],
                           hr, activity_confidence, elapsed_s,
                           POP_HR_LOW, POP_HR_HIGH, POP_HR_LOW - 20.0f, POP_HR_GROSS_HIGH);
}

anomaly_result_t baseline_process_spo2(int activity_idx, float spo2, float activity_confidence, uint32_t elapsed_s) {
    return process_metric(&g_spo2_baseline[activity_idx], &g_spo2_persist_count[activity_idx],
                           spo2, activity_confidence, elapsed_s,
                           POP_SPO2_LOW, 100.0f, POP_SPO2_GROSS_LOW, 101.0f);
}
