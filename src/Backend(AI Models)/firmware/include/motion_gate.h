// motion_gate.h -- PPG motion-artifact rejection (spec §5).
// Reuses the acceleration-variance feature already computed for the Decision Tree,
// so this check costs no extra sensor work.
#ifndef MOTION_GATE_H
#define MOTION_GATE_H

#include "config.h"

// activity_idx must match the activity_t enum order in activity_tree.h.
static inline bool motion_gate_is_low_confidence(float acc_variance, int activity_idx) {
    if (activity_idx < 0 || activity_idx >= NUM_ACTIVITY_STATES) activity_idx = 0;
    return acc_variance > MOTION_GATE_THRESHOLD[activity_idx];
}

#endif // MOTION_GATE_H
