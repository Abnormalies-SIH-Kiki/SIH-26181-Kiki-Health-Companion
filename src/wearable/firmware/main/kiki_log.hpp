#pragma once

namespace kiki {

// Mirrors the ESP log to the gateway as `device_log` events, so the board can
// be debugged without a USB cable.
//
// Everything here is subordinate to the audio path. Logs are queued, never
// blocking; the queue drops its oldest line when full; and the sender pauses
// while audio is playing and hard-limits its rate on the remote link. A
// debugging aid that congests the uplink would be causing the class of problem
// it exists to diagnose -- on this board a busy uplink is what starves the
// websocket keepalive and drops the session.
void log_remote_start();

}  // namespace kiki
