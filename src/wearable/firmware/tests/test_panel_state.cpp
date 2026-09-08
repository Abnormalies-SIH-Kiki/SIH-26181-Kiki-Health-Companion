#include "kiki_panel_state.hpp"

#include <cstdio>
#include <cstring>
#include <initializer_list>

// A mood must never change what the panel's one button does.
//
// It did, for months, and the failure was silent: `<oled:curious>` mid-reply
// rewrote the state from "speaking" to "curious", `busy()` went false, and
// tapping Stop sent `push_to_talk` instead of `cancel_turn`. She talked on. It
// looked intermittent because it depended on whether that particular reply had
// reached a mood tag yet -- so the same test, run by hand, passed half the time.
//
// Build and run on the host:
//   g++ -std=c++17 -I../main -o /tmp/test_panel_state test_panel_state.cpp
//   /tmp/test_panel_state

using kiki::PanelState;

namespace {

int failures = 0;

void check(bool ok, const char *what) {
    if (!ok) {
        std::printf("FAIL %s\n", what);
        ++failures;
    }
}

void test_a_mood_does_not_stop_her_being_busy() {
    PanelState panel;
    panel.set_state("speaking");
    check(panel.busy(), "speaking is busy");

    check(panel.set_expression("curious"), "a mood is accepted while speaking");
    check(panel.busy(), "STILL busy after <oled:curious> -- this is the bug");
    check(std::strcmp(panel.state(), "speaking") == 0, "the state is untouched");
    check(std::strcmp(panel.pose(), "curious") == 0, "but the face moved");

    // Moods chain within one reply; each one refines the same speaking state.
    check(panel.set_expression("idea"), "a second mood is accepted");
    check(panel.busy(), "still busy after the second mood");
    check(std::strcmp(panel.pose(), "idea") == 0, "the face moved again");
}

void test_a_mood_is_refused_outside_speaking_and_tool() {
    for (const char *state : {"listening", "idle", "followup", "warming", "music"}) {
        PanelState panel;
        panel.set_state(state);
        check(!panel.set_expression("happy"), "no mood outside speaking/tool");
        check(std::strcmp(panel.pose(), state) == 0, "the face stays on the state");
    }
    PanelState panel;
    panel.set_state("tool");
    check(panel.set_expression("idea"), "a tool round accepts a mood");
    check(panel.busy(), "a tool round is busy");
}

void test_a_real_state_change_clears_the_mood() {
    PanelState panel;
    panel.set_state("speaking");
    panel.set_expression("mischief");
    panel.set_state("listening");
    check(!panel.busy(), "listening is not busy");
    check(panel.listening(), "listening is listening");
    check(std::strcmp(panel.pose(), "listening") == 0,
          "the mood does not survive the turn that carried it");
}

void test_the_states_the_button_must_treat_as_interruptible() {
    for (const char *state : {"speaking", "thinking", "music", "tool"}) {
        PanelState panel;
        panel.set_state(state);
        check(panel.busy(), "this state must offer Stop");
    }
    for (const char *state : {"idle", "listening", "followup", "connecting"}) {
        PanelState panel;
        panel.set_state(state);
        check(!panel.busy(), "this state must offer the talk button");
    }
}

void test_rubbish_is_survivable() {
    PanelState panel;
    panel.set_state("speaking");
    panel.set_state(nullptr);
    check(std::strcmp(panel.state(), "speaking") == 0, "a null state is ignored");
    check(!panel.set_expression(nullptr), "a null mood is ignored");
    check(!panel.set_expression("not_a_mood"), "an unknown mood is ignored");
    panel.set_state("a_very_long_state_name_that_will_not_fit_in_the_buffer");
    check(std::strlen(panel.state()) == 23, "a long name is truncated, not overflowed");
}

}  // namespace

int main() {
    test_a_mood_does_not_stop_her_being_busy();
    test_a_mood_is_refused_outside_speaking_and_tool();
    test_a_real_state_change_clears_the_mood();
    test_the_states_the_button_must_treat_as_interruptible();
    test_rubbish_is_survivable();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("test_panel_state: all checks passed\n");
    return 0;
}
