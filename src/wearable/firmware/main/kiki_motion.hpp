#pragma once

#include <cstdint>

#include "esp_err.h"
#include "kiki_motion_classifier.hpp"

namespace kiki {

struct MotionRuntimeStats {
    bool available = false;
    uint32_t read_errors = 0;
    MotionClassifierSnapshot classifier{};
};

// Starts the QMI8658 sampler and the lower-priority reaction/event consumer.
// Failure is non-fatal: Kiki remains a complete voice companion without IMU.
esp_err_t motion_start();

// Hooks from the existing runtime. They are lock-free and safe from the
// websocket, LVGL, and telemetry tasks.
void motion_note_system_state(const char *state);
void motion_note_interaction();
void motion_set_power_state(int percent, bool on_usb, bool charging);
void motion_get_stats(MotionRuntimeStats *out);

}  // namespace kiki
