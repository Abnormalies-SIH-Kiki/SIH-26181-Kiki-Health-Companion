#pragma once

#include "esp_err.h"

namespace kiki {

// On-panel Wi-Fi provisioning, replacing KikiFast's IR + LCD picker.
//
// The Pi dictates the password through local Whisper. That cannot work here:
// the gateway, and therefore Whisper, is unreachable precisely when Wi-Fi is
// down. So the board takes the password on its own touch keyboard and stores it
// in NVS, which also means changing AP no longer requires reflashing.
//
// Must not be called from the LVGL task: it blocks while scanning and
// connecting, and drives the UI under the display lock.
esp_err_t setup_ensure_wifi();

// True while a setup screen owns the display, so the face and the ordinary
// touch surface stay out of the way.
bool setup_active();

}  // namespace kiki
