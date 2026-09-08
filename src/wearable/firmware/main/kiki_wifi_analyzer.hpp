#pragma once

#include "lvgl.h"

namespace kiki {

// The Wi-Fi analyser: what this radio can hear, how busy the band is, how the
// link actually performs, and which network to move to.
//
// It exists because "the watch is on NSUT_WIFI at -81 dBm" was, for most of a
// day, the only fact anybody had -- and it was not enough to decide anything.
// A phone showed a dozen strong access points, all of them on 5 GHz, which this
// board has no radio for. The analyser answers the question that actually
// matters before a demo: of the networks this board can physically join, from
// where it is standing, which one is best?
//
// THREE RULES, because this screen sits next to the one thing that must never
// break:
//
//  1. It never joins anything on its own. Scanning is a button, joining is a
//     button, and neither happens on a timer.
//  2. It only offers networks that need no new password -- ones already saved,
//     or open ones. Entering a passphrase stays with "Change Wi-Fi", the flow
//     that has always done it. No second credential path to get wrong.
//  3. A failed join is put back. The current network and its saved passphrase
//     are captured before any attempt, and restored if the new one does not
//     come up. A picker that can disconnect you without being able to reconnect
//     you is a trap, and this one runs on a device with no keyboard.
//
// Costs nothing while closed: no task, no timer, no allocation. The survey
// buffer is taken from PSRAM when the screen opens and released when it closes.

// Open the analyser. `return_screen` is loaded again when the user closes it.
// Must be called with the display lock held.
void wifi_analyzer_open(lv_obj_t *return_screen);

// True while the analyser owns the display.
bool wifi_analyzer_active();

}  // namespace kiki
