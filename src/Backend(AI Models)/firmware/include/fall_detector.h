// fall_detector.h -- Tiny 1D CNN (INT8, via TFLite Micro) + post-fall confirmation FSM.
// Implements spec §7 (CNN inference) and §8 (confirmation state machine).
#ifndef FALL_DETECTOR_H
#define FALL_DETECTOR_H

#include <stdint.h>
#include "config.h"
#include "dummy_sensor.h"

typedef enum {
    FALL_STATE_IDLE = 0,
    FALL_STATE_SUSPECTED,
    FALL_STATE_CONFIRMED
} fall_state_t;

void fall_detector_init();

// Feed one 50Hz IMU sample into the rolling window used by the CNN.
void fall_detector_push_sample(const imu_sample_t &s);

// Call once per second (or per new IMU window) to run inference + advance the FSM.
// Returns the current FSM state after this update. When it transitions to
// FALL_STATE_CONFIRMED, the caller should raise an alert exactly once (edge-triggered
// -- check for the IDLE/SUSPECTED -> CONFIRMED transition, not the level).
fall_state_t fall_detector_update();

float fall_detector_last_probability();

// Call after the caller has raised the alert for a CONFIRMED fall, to return
// the FSM to IDLE and resume normal monitoring.
void fall_detector_reset_after_alert();

#endif // FALL_DETECTOR_H
