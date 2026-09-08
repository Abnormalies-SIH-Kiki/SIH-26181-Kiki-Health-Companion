#pragma once

#include <cstdint>

#include "esp_err.h"

namespace kiki {

// Raw-IMU capture for health mode's guided exercise, and the bridge that lets
// the gateway drive the wrist heart-rate sensor by voice.
//
// WHY THIS EXISTS AT ALL. On the Raspberry Pi body, a guided routine is judged
// from camera frames taken while the person holds a position. This body has no
// camera; it has this IMU, on the wrist that is doing the movement. So the
// gateway asks for a window, the sampler fills it, and the measured motion is
// what the care model is shown before it decides whether the instruction it
// gave was actually followed.
//
// WHY IT IS NOT PART OF kiki_wearable. That module owns the MAX30102 bus, its
// own task and a store-and-forward NVS queue, and it is on the critical path
// for fall detection. This is a plain ring buffer written from the existing
// sampler and drained on request. Keeping them apart means a bug in an exercise
// feature cannot cost anyone a fall alert.
//
// Everything here is INERT until the gateway asks. No window is recorded, no
// event is sent, and no measurement is started unless a command arrives. A
// gateway that never enters health mode never sends one.

esp_err_t exercise_start();

// Called from the existing 50 Hz QMI8658 sampler, on its task. Lock-free and
// bounded: it copies six values into a preallocated slot and returns.
void exercise_note_motion(float ax, float ay, float az,
                          float gx, float gy, float gz);

// `imu_window_start` from the gateway. Records for `seconds` (clamped) and
// sends one `imu_window` event when the window closes.
void exercise_begin_window(float seconds);
void exercise_cancel_window();

// `wearable_measure` from the gateway: starts or cancels an on-demand optical
// reading and reports `measurement_status` as the firmware's own state machine
// advances. The measurement itself is kiki_wearable's; this only drives it and
// narrates it, so the Settings screen and a spoken request run identical code.
void exercise_request_measurement();
void exercise_cancel_measurement();

}  // namespace kiki
