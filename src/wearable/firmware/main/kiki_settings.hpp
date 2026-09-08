#pragma once

#include "lvgl.h"

namespace kiki {

// The runtime settings menu, ported from ir_controls.py's modal settings mode.
// `return_screen` is loaded again when the user closes it. Must be called with
// the display lock held.
void settings_open(lv_obj_t *return_screen, int volume_percent, float gain);
void settings_measure_heart(lv_obj_t *return_screen);

// Loaded from NVS once during boot. The board owns this preference so it
// survives both gateway and firmware restarts and can be applied before the
// first reply starts playing.
void settings_init();
bool settings_barge_in_enabled();

// Health-mode switches, persisted in NVS beside barge-in and applied before
// the first sample is taken. Both default ON.
//
// They are BOARD settings on purpose. Fall detection runs entirely in firmware
// and has to be switchable while the gateway is unreachable -- which is
// precisely when a false alert is hardest to stop -- and the person wearing it
// is the one who should be able to turn it off, without asking the laptop.
bool settings_fall_alerts_enabled();
bool settings_movement_checks_enabled();

bool settings_active();

}  // namespace kiki
