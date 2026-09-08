#pragma once

#include <cstddef>
#include <cstdint>

namespace kiki {

// Dance mode: the whole 466x466 panel becomes a stage.
//
// The gateway chooses the song, measures its beat grid and writes the routine
// (see gateway/kiki_gateway/dance.py). The board owns everything else -- the
// move library, the stage, and above all the CLOCK. Beat position comes from
// the media samples the codec has actually played, not from wall time and not
// from the gateway, because that is the only quantity that survives the
// prebuffer gate, the adaptive jitter buffer and a stalled network without
// walking the choreography off the beat.

// The move vocabulary. ORDER IS THE WIRE FORMAT: these values are the integers
// the gateway sends, and dance.py's MOVES tuple must stay identical. A move may
// be appended, never reordered. gateway/tests/test_dance.py parses this header
// and fails if the two ever drift.
enum class DanceMove : uint8_t {
    Groove = 0,
    Bounce,
    HipSway,
    SideStep,
    BodyRoll,
    ClawWave,
    DiscoPoint,
    Spin,
    Jump,
    Shimmy,
    Robot,
    PopLock,
    Moonwalk,
    Kick,
    HeadBang,
    Sprinkler,
    CrabWalk,
    Starfish,
    HeartHands,
    Dab,
    WinkPushIn,
    SnapFreeze,
    Bow,
    WaveCrowd,
    Twirl,
    Stomp,
    Count,
};

// Stage effects, same contract as the moves above.
enum class DanceEffect : uint8_t {
    None = 0,
    Sparkle,
    Confetti,
    Hearts,
    Burst,
    Rings,
    Flash,
    Stars,
    Count,
};

struct DanceStep {
    uint16_t beat;      // absolute beat of the song this move starts on
    uint8_t move;       // DanceMove
    uint8_t beats;      // how long it holds
    uint8_t intensity;  // 0..15
    uint8_t effect;     // DanceEffect, fired when the step begins
};

constexpr size_t kDanceMaxSteps = 256;
constexpr size_t kDanceMaxEnergy = 1024;

// --- The interpreter. Lives in kiki_dance_routine.cpp, which deliberately
// --- pulls in neither LVGL nor ESP-IDF so it can be compiled and exercised on
// --- the host (firmware/tests/test_dance_routine.cpp).

// "beat,move,beats,intensity,effect;..." -> steps. Returns how many were kept.
// Malformed entries are skipped rather than aborting the routine: one bad move
// should cost that move, not the dance.
size_t dance_parse_routine(const char *text, DanceStep *out, size_t max_steps);

// One hex digit of loudness per beat.
size_t dance_parse_energy(const char *text, uint8_t *out, size_t max_beats);

// Index of the step live at `beat`, or -1 before the routine starts.
int dance_step_at(const DanceStep *steps, size_t count, float beat);

// --- Lifecycle. Safe to call from any task; the display lock is taken inside.

// The screen the stage canvas belongs to. `void *` rather than `lv_obj_t *` so
// this header stays free of LVGL and the interpreter above stays host-testable.
void dance_attach(void *parent);

bool dance_start(float bpm, int32_t beat0_ms, const char *mood, const char *title,
                 const char *routine, const char *energy);

// Ends the dance and silences the music immediately -- locally, without waiting
// for the gateway, because a tap that takes a round trip to be felt does not
// feel like a stop button. Tells the gateway afterwards unless `notify` is
// false (i.e. the gateway is the one who asked).
void dance_stop(const char *reason, bool notify = true);

bool dance_is_active();

// Called for every media frame that arrives. The first one after dance_start
// anchors the clock.
void dance_note_media_frame();

// Draws one frame; returns the milliseconds until the next. Replaces
// face_render_frame while dancing.
uint32_t dance_render_frame();

// Rendered frames per second, for device_stats.
uint32_t dance_fps();

// Average microseconds per frame spent in our own drawing, as opposed to
// LVGL's flush of the result. The two need different fixes.
uint32_t dance_draw_us();

}  // namespace kiki
