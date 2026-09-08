#pragma once

#include <cstring>

// What Kiki is doing, and what her face is doing about it.
//
// These are two different questions and they were one variable, which is how a
// mood tag came to disable the Stop button: `<oled:curious>` in the middle of a
// reply rewrote the state from "speaking" to "curious", so `busy()` went false
// and the panel's one button went back to meaning "talk to me" while she was
// still talking. Tapping it sent `push_to_talk`, which stops nothing, and which
// the gateway discards anyway because it is still playing.
//
// Deliberately free of ESP-IDF and LVGL so firmware/tests/test_panel_state.cpp
// can hold the line: a mood must never change what the button does.

namespace kiki {

inline constexpr const char *kExpressionStates[] = {
    "love", "shy", "giggle", "wink", "excited", "curious", "proud", "sulk",
    "surprised", "sleepy", "idea", "mischief", "scared", "awe",
    "happy", "sad", "dizzy", "confused", "sleeping",
};

inline bool is_expression(const char *name) {
    if (!name) return false;
    for (const char *candidate : kExpressionStates) {
        if (std::strcmp(name, candidate) == 0) return true;
    }
    return false;
}

// A mood only ever refines speaking or a tool round -- never listening, idle,
// warming or music. Because the state can no longer BE an expression, moods
// still chain within one reply: the state stays "speaking" throughout.
inline bool expression_may_override(const char *state) {
    return state && (std::strcmp(state, "speaking") == 0 ||
                     std::strcmp(state, "tool") == 0);
}

class PanelState {
  public:
    // The gateway's word on what she is doing. Resets the pose with it: a real
    // state change ends whatever mood was refining the previous one.
    void set_state(const char *state) {
        if (!state) return;
        copy(state_, state);
        copy(pose_, state);
    }

    // A mood. Changes the FACE only; `busy()` and the button are unaffected.
    // Returns false when the current state does not accept moods.
    bool set_expression(const char *name) {
        if (!is_expression(name) || !expression_may_override(state_)) return false;
        copy(pose_, name);
        return true;
    }

    // She is doing something a person may want to interrupt. This is what the
    // one button consults to decide whether it says Stop.
    bool busy() const {
        return std::strcmp(state_, "speaking") == 0 ||
               std::strcmp(state_, "thinking") == 0 ||
               std::strcmp(state_, "music") == 0 ||
               std::strcmp(state_, "tool") == 0;
    }

    bool listening() const {
        return std::strcmp(state_, "listening") == 0 ||
               std::strcmp(state_, "followup") == 0 ||
               std::strcmp(state_, "wake") == 0;
    }

    const char *state() const { return state_; }
    // What should be on the panel: the mood if one is active, else the state.
    const char *pose() const { return pose_; }

  private:
    template <size_t N>
    static void copy(char (&dst)[N], const char *src) {
        std::strncpy(dst, src, N - 1);
        dst[N - 1] = '\0';
    }
    char state_[24] = "booting";
    char pose_[24] = "booting";
};

}  // namespace kiki
