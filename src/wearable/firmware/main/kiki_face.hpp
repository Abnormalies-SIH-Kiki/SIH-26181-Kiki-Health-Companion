#pragma once

#include <cstdint>
#include "lvgl.h"

namespace kiki {

// The Clawd pixel-crab face, ported from KikiFast's core/oled_display.py.
//
// The port keeps that module's *logical* 128x64 coordinate system rather than
// re-laying-out for the AMOLED, so every literal coordinate in every pose and
// prop transcribes across unchanged and the two faces cannot drift. The canvas
// scales those logical pixels up to whatever rectangle face_init is given.
//
// Rendering is local to the board on purpose: the face has to keep animating
// while the gateway is warming, unreachable, or not yet configured, which is
// exactly when a network-driven face would freeze.

void face_init(lv_obj_t *parent, int32_t x, int32_t y, int32_t w, int32_t h);

// `state` is one of oled_display.py's VALID_STATES; unknown names fall back to
// idle, matching the Python. `detail` is the optional status line.
void face_set_state(const char *state, const char *detail);
const char *face_current_state();

// Draws one frame. Returns the milliseconds to wait before the next one, which
// varies per state exactly as _STATE_FPS does on the Pi.
uint32_t face_render_frame();

void face_set_visible(bool visible);

}  // namespace kiki
