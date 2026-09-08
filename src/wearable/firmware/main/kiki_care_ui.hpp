#pragma once

#include "cJSON.h"
#include "lvgl.h"

namespace kiki {

// The care plan, on the watch.
//
// Health mode's plan lives on the laptop, which is the right place for it --
// but a schedule you cannot see is a schedule you do not trust, and one you can
// only change by talking to it is one you cannot fix while it is talking. So
// the board renders the plan and can act on it: view a routine, start it now,
// turn it off, or delete it.
//
// The board is a VIEW. It holds no care state of its own beyond what was last
// pushed to it, every action is sent to the gateway, and the list is only
// redrawn when the gateway sends the plan back. That ordering means the screen
// can never show a deletion that did not actually happen -- the same rule the
// rest of health mode follows about not claiming an action succeeded.

// Open the screen. `return_screen` is loaded again when the user closes it.
// Must be called with the display lock held.
void care_ui_open(lv_obj_t *return_screen);

// A `care_plan` event from the gateway: the whole plan, every time. Safe to
// call whether or not the screen is open, and from the websocket task.
void care_ui_set_plan(const cJSON *root);

// True while the care screen owns the display.
bool care_ui_active();

}  // namespace kiki
