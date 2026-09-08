#pragma once

namespace kiki {

// Sets the system clock from NTP, blocking up to `timeout_ms`.
//
// This is not a convenience. The ESP32 has no battery-backed RTC and boots
// believing it is 1 January 1970, and TLS certificate validation checks
// notBefore/notAfter -- so every certificate issued in this decade reads as
// "not yet valid" and every wss:// handshake fails before it starts. Plain
// ws:// on the LAN never noticed, which is exactly why this was missing for so
// long: the failure only appears on the path that leaves the building.
//
// Returns false on timeout, in which case TLS will not work and it is worth
// saying so in the log rather than letting it look like a network fault.
bool time_sync(int timeout_ms);

// True once the clock holds a plausible date, i.e. once TLS can validate a
// certificate. Cheap; safe to call from anywhere.
bool time_is_set();

// Keeps retrying in the background after time_sync() gives up. Without this a
// single missed NTP reply at boot means no TLS for the whole session, and the
// symptom -- "Reconnecting" forever -- looks nothing like a clock problem.
void time_sync_keep_trying();

}  // namespace kiki
