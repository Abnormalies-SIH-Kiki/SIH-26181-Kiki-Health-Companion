#pragma once

namespace kiki {

// Downloads firmware from `url` into the inactive OTA slot and reboots into it.
// Runs on its own task and does not return on success. Safe to call from a
// websocket callback.
void ota_start(const char *url);

// Call once the gateway connection is known good. If this build arrived by OTA
// and has not been confirmed yet, this is what makes it permanent; without it
// the next boot goes back to the previous firmware.
//
// The confirmation is deliberately tied to reaching the gateway rather than to
// merely booting: an image that starts but cannot talk to the laptop is just a
// slower way of bricking a robot that lives at the other end of that socket.
void ota_confirm_alive();

// True while a download is in progress, so the UI can say so and the mic
// pipeline can stay out of the way.
bool ota_in_progress();

// Call at boot, after Wi-Fi. If the running image arrived by OTA and has not
// yet proved it can reach the gateway, this arms a timer that puts the previous
// firmware back and reboots.
//
// This is a software stand-in for bootloader rollback
// (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE), which is deliberately NOT enabled:
// turning it on changes the bootloader, and that would turn a one-file phone
// flash into a two-file one where the risky file is the one that makes the
// board boot at all. It covers the realistic OTA failure -- an image that runs
// but cannot connect -- and not a crash before this code is reached.
void ota_arm_rollback_watchdog();

}  // namespace kiki
