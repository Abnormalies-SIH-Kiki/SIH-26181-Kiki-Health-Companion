#pragma once

#include <cstdint>

#include "esp_err.h"

namespace kiki {

struct WearableSnapshot {
    bool sensor_available = false;
    bool worn = false;
    uint32_t steps = 0;
    float heart_rate = 0.0F;
    uint32_t heart_rate_age_seconds = UINT32_MAX;
    float spo2_experimental = 0.0F;
    const char *quality = "NONE";
    const char *activity = "unknown";
    bool fall_check_pending = false;
};

enum class WearableMeasurementState : uint8_t {
    Idle = 0,
    WaitingForContact,
    WaitingForStillness,
    Measuring,
    Complete,
    Failed,
};

struct WearableMeasurementStatus {
    WearableMeasurementState state = WearableMeasurementState::Idle;
    bool sensor_available = false;
    bool worn = false;
    uint8_t progress_percent = 0;
    uint32_t steps = 0;
    float heart_rate = 0.0F;
    float spo2_experimental = 0.0F;
    const char *quality = "NONE";
};

// MAX30102 uses its own expansion bus: SDA GPIO18, SCL GPIO17, address 0x57.
// It never touches the board display/touch/IMU bus on GPIO15/14.
esp_err_t wearable_start();

// Lock-free hooks from the existing 50 Hz QMI8658 sampler.
void wearable_note_motion(float ax, float ay, float az,
                          float gx, float gy, float gz, const char *posture);
void wearable_note_motion_event(const char *event);
void wearable_set_battery(int percent);

// Gateway acknowledgement for the persistent three-batch store-forward queue.
void wearable_handle_ack(const char *batch_id, bool accepted);

// A deliberate touch or explicit voice response cancels the 30-second check-in.
bool wearable_fall_check_pending();
void wearable_cancel_fall_check(const char *reason);
void wearable_confirm_fall_check(const char *reason);
void wearable_get_snapshot(WearableSnapshot *out);
// Estimated worn movement, since boot; bounded recent bouts, not medical exercise minutes.
void wearable_activity_detail(char *out, unsigned size);

// Starts an on-demand reading on the existing health task. It is deliberately
// asynchronous: LVGL remains responsive while contact/stillness is checked and
// the same result is queued through the normal desktop telemetry path.
bool wearable_request_measurement();
void wearable_cancel_measurement();
void wearable_get_measurement_status(WearableMeasurementStatus *out);

}  // namespace kiki
