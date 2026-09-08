#include "kiki_dance.hpp"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>

// The routine parser is fed a string that came from a language model, over a
// websocket, and turns it into array indices on a device with no MMU. So the
// cases that matter here are the hostile ones: truncated records, out-of-range
// move numbers, and entries that would walk the render cursor backwards.
//
// Build and run on the host:
//   g++ -std=c++17 -I../main -o /tmp/test_dance_routine
//       test_dance_routine.cpp ../main/kiki_dance_routine.cpp
//   /tmp/test_dance_routine

using kiki::DanceEffect;
using kiki::DanceMove;
using kiki::DanceStep;

namespace {

size_t parse(const char *text, DanceStep *out, size_t max_steps = 64) {
    return kiki::dance_parse_routine(text, out, max_steps);
}

void test_a_well_formed_routine_round_trips() {
    DanceStep steps[64];
    const size_t count = parse("0,0,8,6,0;8,7,2,15,4;16,22,8,12,2", steps);
    assert(count == 3);
    assert(steps[0].beat == 0 && steps[0].move == 0 && steps[0].beats == 8);
    assert(steps[0].intensity == 6 && steps[0].effect == 0);
    assert(steps[1].move == static_cast<uint8_t>(DanceMove::Spin));
    assert(steps[1].effect == static_cast<uint8_t>(DanceEffect::Burst));
    assert(steps[2].move == static_cast<uint8_t>(DanceMove::Bow));
}

void test_an_unknown_move_is_dropped_and_the_rest_survives() {
    DanceStep steps[64];
    // 200 is not a move. Losing that entry is correct; losing the routine is
    // not, and indexing the move table with it would be a crash.
    const size_t count = parse("0,0,4,8,0;4,200,4,8,0;8,1,4,8,0", steps);
    assert(count == 2);
    assert(steps[0].beat == 0);
    assert(steps[1].beat == 8);
}

void test_a_truncated_record_does_not_desynchronise_the_next_one() {
    DanceStep steps[64];
    const size_t count = parse("0,0,4,8,0;4,;8,1,4,8,0", steps);
    assert(count == 2);
    assert(steps[1].beat == 8);
    assert(steps[1].move == static_cast<uint8_t>(DanceMove::Bounce));
}

void test_missing_trailing_fields_fall_back_to_safe_values() {
    DanceStep steps[64];
    const size_t count = parse("0,3", steps);
    assert(count == 1);
    assert(steps[0].move == static_cast<uint8_t>(DanceMove::SideStep));
    assert(steps[0].beats >= 1);       // never zero: the renderer divides by it
    assert(steps[0].effect < static_cast<uint8_t>(DanceEffect::Count));
}

void test_out_of_order_entries_are_refused() {
    DanceStep steps[64];
    // The render cursor only moves forwards. An entry that goes back in time
    // would either be unreachable or rewind the performance.
    const size_t count = parse("0,0,4,8,0;16,1,4,8,0;8,2,4,8,0;24,3,4,8,0", steps);
    assert(count == 3);
    assert(steps[0].beat == 0 && steps[1].beat == 16 && steps[2].beat == 24);
}

void test_values_are_clamped_into_range() {
    DanceStep steps[64];
    const size_t count = parse("0,0,9999,99,99", steps);
    assert(count == 1);
    assert(steps[0].beats <= 64);
    assert(steps[0].intensity <= 15);
    assert(steps[0].effect == 0);      // an unknown effect is simply none
}

void test_the_step_array_is_never_overrun() {
    char routine[4096] = {};
    size_t used = 0;
    for (int i = 0; i < 300; ++i) {
        used += static_cast<size_t>(
            std::snprintf(routine + used, sizeof(routine) - used, "%d,0,1,8,0;", i));
    }
    DanceStep steps[16];
    assert(parse(routine, steps, 16) == 16);
}

void test_empty_and_null_input_are_survivable() {
    DanceStep steps[8];
    assert(parse("", steps) == 0);
    assert(kiki::dance_parse_routine(nullptr, steps, 8) == 0);
    assert(parse(";;;;", steps) == 0);
    assert(parse("garbage", steps) == 0);
}

void test_energy_parses_as_hex_nibbles() {
    uint8_t energy[16];
    const size_t count = kiki::dance_parse_energy("0f7A", energy, 16);
    assert(count == 4);
    assert(energy[0] == 0 && energy[1] == 15 && energy[2] == 7 && energy[3] == 10);
    assert(kiki::dance_parse_energy("", energy, 16) == 0);
    assert(kiki::dance_parse_energy(nullptr, energy, 16) == 0);
    // Anything that is not a hex digit is skipped rather than ending the list.
    assert(kiki::dance_parse_energy("1-2", energy, 16) == 2);
}

void test_the_live_step_is_found_by_beat() {
    DanceStep steps[64];
    const size_t count = parse("0,0,4,8,0;8,1,4,8,0;16,2,8,8,0", steps);
    assert(kiki::dance_step_at(steps, count, -1.0f) == -1);
    assert(kiki::dance_step_at(steps, count, 0.0f) == 0);
    assert(kiki::dance_step_at(steps, count, 7.99f) == 0);
    assert(kiki::dance_step_at(steps, count, 8.0f) == 1);
    assert(kiki::dance_step_at(steps, count, 15.5f) == 1);
    // Past the end the last move holds rather than the crab freezing.
    assert(kiki::dance_step_at(steps, count, 900.0f) == 2);
    assert(kiki::dance_step_at(nullptr, 0, 4.0f) == -1);
}

void test_a_routine_that_starts_late_leaves_a_count_in() {
    DanceStep steps[8];
    const size_t count = parse("8,1,4,8,0", steps);
    assert(count == 1);
    // Before the first step there is no step: the renderer shows the warm-up.
    assert(kiki::dance_step_at(steps, count, 3.0f) == -1);
    assert(kiki::dance_step_at(steps, count, 8.0f) == 0);
}

}  // namespace

int main() {
    test_a_well_formed_routine_round_trips();
    test_an_unknown_move_is_dropped_and_the_rest_survives();
    test_a_truncated_record_does_not_desynchronise_the_next_one();
    test_missing_trailing_fields_fall_back_to_safe_values();
    test_out_of_order_entries_are_refused();
    test_values_are_clamped_into_range();
    test_the_step_array_is_never_overrun();
    test_empty_and_null_input_are_survivable();
    test_energy_parses_as_hex_nibbles();
    test_the_live_step_is_found_by_beat();
    test_a_routine_that_starts_late_leaves_a_count_in();
    std::printf("dance routine tests passed\n");
    return 0;
}
