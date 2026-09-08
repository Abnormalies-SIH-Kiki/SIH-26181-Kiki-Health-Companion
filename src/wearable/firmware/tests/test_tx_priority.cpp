#include "gateway_tx_priority.hpp"

#include <cassert>
#include <cstdio>

// Which events overtake microphone audio on a congested uplink.
//
// This is the ordering that makes the talk button feel instant on a college AP:
// the board streams ~31 microphone frames a second, and before this change a
// press queued behind all of them (and behind a 5 s socket send holding the
// websocket mutex). Getting an event into the wrong class fails silently -- the
// button simply feels slow again -- so the classification is pinned here.
//
// Build and run on the host:
//   g++ -std=c++17 -I../main -o /tmp/test_tx_priority test_tx_priority.cpp
//   /tmp/test_tx_priority

using kiki::TxClass;
using kiki::tx_classify;

namespace {

int failures = 0;

void expect(const char *type, TxClass want, const char *why) {
    const TxClass got = tx_classify(type);
    if (got != want) {
        std::printf("FAIL %-18s -> %d, wanted %d (%s)\n", type,
                    static_cast<int>(got), static_cast<int>(want), why);
        ++failures;
    }
}

void test_everything_a_person_is_waiting_on_is_urgent() {
    expect("push_to_talk", TxClass::Urgent, "the press that starts listening");
    expect("commit_now", TxClass::Urgent, "the release that ends a hold");
    expect("listen_open", TxClass::Urgent, "the release that ends a tap");
    expect("cancel_turn", TxClass::Urgent, "barge-in is only worth its latency");
    expect("wake", TxClass::Urgent, "a tap on the panel");
    expect("sleep", TxClass::Urgent, "a hold on Stop");
    expect("dance_stop", TxClass::Urgent, "a tap on the stage");
    expect("health_fall", TxClass::Urgent, "a fall timeout must reach family promptly");
}

void test_the_events_the_gateway_is_blocked_on_are_urgent() {
    // hello has to be the first thing on a new socket: the gateway will not
    // even set up the session without it, and it carries the link name that
    // decides the audio rate.
    expect("hello", TxClass::Urgent, "nothing else may precede it");
    // `playing` is cleared only by playback_drained. If this is ever delayed
    // behind a backlog the gateway discards every microphone frame and Kiki is
    // deaf until the session restarts -- known sharp edge #6.
    expect("playback_drained", TxClass::Urgent, "Kiki is deaf until it arrives");
    // A reply that stuttered is asking to be buffered further ahead. Sending it
    // late means the next reply stutters too.
    expect("request_lead", TxClass::Urgent, "it fixes the next reply");
}

void test_diagnostics_are_the_first_thing_dropped() {
    // Telemetry about a struggling uplink must never be what finishes it off.
    expect("device_stats", TxClass::Bulk, "5 s telemetry");
    expect("device_log", TxClass::Bulk, "the remote console");
    expect("health_telemetry", TxClass::Bulk, "periodic wellness telemetry");
}

void test_ordinary_state_is_neither() {
    expect("media_control", TxClass::Normal, "transport buttons");
    expect("motion_event", TxClass::Normal, "the IMU");
    expect("set_volume", TxClass::Normal, "settings");
    expect("ota_result", TxClass::Normal, "an update reporting back");
    expect("something_added_later", TxClass::Normal, "an unknown event");
}

void test_a_null_type_does_not_crash() {
    // Callers pass a string literal today, but a queue drain that reads past
    // its data must not take the board down.
    (void)tx_classify(nullptr);
}

}  // namespace

int main() {
    test_everything_a_person_is_waiting_on_is_urgent();
    test_the_events_the_gateway_is_blocked_on_are_urgent();
    test_diagnostics_are_the_first_thing_dropped();
    test_ordinary_state_is_neither();
    test_a_null_type_does_not_crash();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("test_tx_priority: all checks passed\n");
    return 0;
}
