#pragma once

#include <cstring>

// Which control events overtake microphone audio, and which are thrown away
// first when the uplink cannot keep up.
//
// Deliberately free of ESP-IDF so it can be tested on the host
// (firmware/tests/test_tx_priority.cpp), the same way the dance routine parser
// is. The list below is a behavioural contract, not a detail: dropping
// `cancel_turn` into the bulk class would make barge-in unreliable on exactly
// the links where barge-in matters most, and nothing would fail loudly.

namespace kiki {

enum class TxClass : unsigned char {
    // Someone is waiting on this, or it changes what the gateway does next.
    // Never dropped; if the queue is full the OLDEST urgent event is discarded,
    // because a stale press is worth less than the one just made.
    Urgent = 0,
    // Ordinary state the gateway should hear about promptly.
    Normal = 1,
    // Diagnostics. First to go, and skipped entirely while the link is in
    // trouble -- telemetry about a struggling uplink must not be what finishes
    // it off.
    Bulk = 2,
};

inline TxClass tx_classify(const char *type) {
    if (!type) return TxClass::Normal;
    static const char *const kUrgent[] = {
        "hello",            // must be first on the wire, and gates everything
        "push_to_talk",     // the talk button, pressed
        "commit_now",       // ...and released after a hold
        "listen_open",      // ...or released as a tap
        "cancel_turn",      // barge-in: the one event whose whole value is speed
        "wake",
        "sleep",
        "mute",
        "dance_stop",
        "set_barge_in",
        "shutdown",
        "playback_drained", // the gateway is deaf until this arrives
        "request_lead",     // a stuttering reply asking to be buffered further
        "health_fall",      // possible fall check/timeout must overtake audio
    };
    for (const char *name : kUrgent) {
        if (std::strcmp(type, name) == 0) return TxClass::Urgent;
    }
    if (std::strcmp(type, "device_log") == 0 || std::strcmp(type, "device_stats") == 0 ||
        std::strcmp(type, "health_telemetry") == 0) {
        return TxClass::Bulk;
    }
    return TxClass::Normal;
}

}  // namespace kiki
