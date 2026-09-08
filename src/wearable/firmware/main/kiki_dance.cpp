#include "kiki_dance.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "bsp/esp-bsp.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "lvgl.h"

#include "audio_pipeline.hpp"
#include "gateway_client.hpp"
#include "kiki_theme.hpp"
#include "kiki_ui.hpp"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_dance";

// ------------------------------------------------------------- geometry ----
// The panel is a 466 px disc. Everything below is in canvas pixels, because
// unlike the resting face (which is a direct port of a 128x64 OLED layout and
// keeps those coordinates so the two cannot drift) this scene was drawn for
// this display and has nothing to stay in step with.
constexpr int kScreen = 466;
constexpr int kCentreX = kScreen / 2;
constexpr int kCentreY = kScreen / 2;
constexpr int kRadius = 232;      // one pixel inside the bezel
// The stage is drawn at half resolution and stretched over the panel.
//
// Measured on the board: 25 fps with the music stopped, 8 fps with it running,
// for the same scene. The difference is not CPU -- it is the PSRAM bus. A
// full-resolution frame is 434 KiB written by the renderer and read straight
// back by LVGL's flush, which is thirteen times the data cache, so every frame
// streams the whole cache out from under the audio path that shares that bus.
// At 233x233 the same scene is 108 KiB, a quarter of the traffic.
//
// It costs nothing visually that matters here: this is a chunky pixel crab on
// a 1.75-inch disc, and 2x nearest-neighbour scaling reads as deliberate pixel
// art rather than as a low-resolution photograph. Everything below still draws
// in 466-space; span() is the only place that knows.
// Measured, twice: at kSuper=2 the frame rate got *worse* (8 fps -> 4), because
// LVGL's stretched-image path costs more per output pixel than the straight
// copy it replaces -- it is still writing 217k pixels, now with transform maths
// per pixel. The PSRAM saving is real but smaller than that. Left as a single
// constant because the experiment is worth being able to repeat, not because
// the answer is in doubt: 1 is the right value on this hardware.
constexpr int kSuper = 1;
constexpr int kCanvas = kScreen / kSuper;
constexpr int kFloorY = 336;      // where Kiki stands, and the mirror line
constexpr float kBodyBaseY = 246.0f;
constexpr float kBodyHalfW = 76.0f;
constexpr float kBodyHalfH = 56.0f;

constexpr float kTau = 6.28318530717958647692f;
constexpr float kDeg = kTau / 360.0f;

// 25 fps is the target, not a promise -- see the governor in dance_render_frame.
constexpr uint32_t kFramePeriodMs = 40;
constexpr uint32_t kMinFramePeriodMs = 25;
constexpr uint32_t kSlowFramePeriodMs = 100;
constexpr uint32_t kStarvedFramePeriodMs = 200;
// How long one move takes to hand over to the next, in beats. A third of a
// beat is long enough that nothing teleports and short enough that the new
// move's own first accent still lands where the music put it.
constexpr float kBlendBeats = 0.33f;

// Music has to start within this long of the routine arriving, and must not
// stop for this long once it has. Both are backstops against the panel being
// left in a dance room with nothing playing -- the state this feature must
// never be able to get stuck in.
constexpr int64_t kMusicStartTimeoutUs = 12 * 1000 * 1000;
constexpr int64_t kMusicStallTimeoutUs = 3 * 1000 * 1000;

constexpr size_t kMaxParticles = 44;
constexpr int kEqBars = 11;

// ---------------------------------------------------------------- state ----

lv_obj_t *g_canvas = nullptr;
uint16_t *g_buffer = nullptr;
int16_t g_span[kCanvas];       // half-width of the disc on each canvas row

lv_obj_t *g_parent = nullptr;
std::atomic<bool> g_active{false};
std::atomic<bool> g_stop_pending{false};
char g_stop_reason[24] = "";
bool g_notify_on_stop = true;
TaskHandle_t g_stop_worker = nullptr;

float g_bpm = 112.0f;
float g_beat0 = 0.0f;          // seconds into the song of beat 0
char g_mood[16] = "excited";
DanceStep g_steps[kDanceMaxSteps];
size_t g_step_count = 0;
uint8_t g_energy[kDanceMaxEnergy];
size_t g_energy_count = 0;

// The clock. `g_media_mark` is the queued-sample count at the moment the
// song's first frame arrived, so (played - mark) is the song's position.
std::atomic<bool> g_awaiting_media{false};
std::atomic<uint64_t> g_media_mark{0};
std::atomic<bool> g_media_started{false};
int64_t g_armed_at_us = 0;
uint64_t g_last_played = 0;
int64_t g_last_progress_us = 0;

float g_beat = 0.0f;           // current position in beats, fractional
float g_prev_beat = 0.0f;
float g_blend_beats = 0.0f;    // how much of a move hand-over is left
int g_current_step = -1;
std::atomic<uint32_t> g_fps{0};
// Microseconds spent inside our own drawing, averaged over the last second.
// Next to the frame rate this separates "the scene is too expensive to draw"
// from "LVGL cannot push a full-screen canvas any faster", which need
// completely different fixes.
std::atomic<uint32_t> g_draw_us{0};
uint32_t g_draw_accum_us = 0;
// How much stage the board can afford right now.
//
// Measured on the device while a song streams: our own drawing is 36-46 ms of
// a frame, and LVGL's flush of the full-screen canvas is the rest -- up to
// 97 ms, because it reads 434 KiB back out of the same PSRAM the audio path is
// using. At 160 BPM a beat is 376 ms, so a 143 ms frame puts every pose up to
// a third of a beat late, which is exactly what "not on the beat" looks like.
//
// Rather than pick one scene and hope, the stage sheds its most expensive
// extras until the frame rate is high enough to land poses on the beat. The
// crab, the floor and the equaliser always survive; the reflection and the
// sweeping beams are what go.
int g_quality = 2;
bool g_stage_dirty = true;   // the ground needs one full repaint

struct Rect { int x0, y0, x1, y1; };
Rect g_last_dirty{0, 0, kScreen, kScreen};

Rect union_rect(const Rect &a, const Rect &b) {
    return {std::min(a.x0, b.x0), std::min(a.y0, b.y0),
            std::max(a.x1, b.x1), std::max(a.y1, b.y1)};
}
int64_t g_fps_window_us = 0;
uint32_t g_fps_frames = 0;

// Colours for the active mood, refreshed on dance_start.
uint16_t c_bg = 0, c_shell = 0, c_shell_dark = 0, c_accent = 0, c_glint = 0,
         c_deep = 0;

// ---------------------------------------------------------------- maths ----

inline float clampf(float value, float low, float high) {
    return value < low ? low : (value > high ? high : value);
}

inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }

// 1.0 exactly on the beat, decaying after it, with a small negative dip just
// before the next one. That dip is the anticipation, and it is most of what
// separates a pose that "lands" from a pose that merely changes.
inline float hit(float sub, float decay = 5.0f) {
    return std::exp(-sub * decay) - 0.16f * std::pow(sub, 8.0f);
}

inline float ease_out(float t) {
    const float inv = 1.0f - clampf(t, 0.0f, 1.0f);
    return 1.0f - inv * inv * inv;
}

// Overshoots and settles: the shape of anything that has weight.
inline float ease_back(float t) {
    t = clampf(t, 0.0f, 1.0f);
    const float inv = t - 1.0f;
    return 1.0f + inv * inv * (2.70158f * inv + 1.70158f);
}

inline float wave(float t) { return std::sin(t * kTau); }

uint32_t g_rand_state = 0x1234567u;
inline float frand() {
    g_rand_state = g_rand_state * 1664525u + 1013904223u;
    return static_cast<float>((g_rand_state >> 8) & 0xFFFF) / 65535.0f;
}

uint16_t lerp565(uint16_t a, uint16_t b, float t) {
    t = clampf(t, 0.0f, 1.0f);
    const int ar = (a >> 11) & 0x1F, ag = (a >> 5) & 0x3F, ab = a & 0x1F;
    const int br = (b >> 11) & 0x1F, bg = (b >> 5) & 0x3F, bb = b & 0x1F;
    const int r = ar + static_cast<int>((br - ar) * t);
    const int g = ag + static_cast<int>((bg - ag) * t);
    const int bl = ab + static_cast<int>((bb - ab) * t);
    return static_cast<uint16_t>((r << 11) | (g << 5) | bl);
}

// --------------------------------------------------------------- raster ----
// Every fill in this file goes through span(). That is what makes the floor
// reflection nearly free: one flag mirrors the whole scene about the floor
// line, so the reflection is the same drawing code run a second time rather
// than a copy of the framebuffer.

bool g_mirror = false;
uint8_t g_dim = 0;   // 0..255, how far this pass is faded toward the ground
// Clip box, in screen space. Everything is drawn through span(), so restricting
// it here restricts the whole scene -- the stage fill included, which is the
// expensive part. See the dirty-rectangle comment in dance_render_frame.
int g_clip_x0 = 0, g_clip_y0 = 0, g_clip_x1 = kScreen, g_clip_y1 = kScreen;

inline void span(int y, int x0, int x1, uint16_t colour) {
    if (g_mirror) y = 2 * kFloorY - y;
    if (y < g_clip_y0 || y >= g_clip_y1) return;
    if (x0 < g_clip_x0) x0 = g_clip_x0;
    if (x1 > g_clip_x1) x1 = g_clip_x1;
    if (x1 <= x0) return;
    // Into canvas space. Rounding the right edge outward keeps two adjacent
    // 466-space spans adjacent here rather than leaving a seam between them.
    y /= kSuper;
    // Every other line only: a scanlined reflection reads as a polished stage,
    // and costs half as much as a solid one. Counted in canvas rows, because in
    // screen rows at half resolution it would drop the reflection entirely.
    if (g_mirror && (y & 1) == 0) return;
    x0 /= kSuper;
    x1 = (x1 + kSuper - 1) / kSuper;
    if (y < 0 || y >= kCanvas) return;
    const int half = g_span[y];
    if (half <= 0) return;
    const int centre = kCanvas / 2;
    const int left = std::max(x0, centre - half);
    const int right = std::min(x1, centre + half);
    if (right <= left) return;
    uint16_t *row = g_buffer + static_cast<size_t>(y) * kCanvas;
    if (g_dim) {
        const float t = g_dim / 255.0f;
        colour = lerp565(colour, c_deep, t);
    }
    // Pairs first: PSRAM is the bottleneck here and a 32-bit store moves twice
    // as much per bus cycle as a 16-bit one.
    int x = left;
    if ((x & 1) && x < right) row[x++] = colour;
    const uint32_t pair = (static_cast<uint32_t>(colour) << 16) | colour;
    auto *wide = reinterpret_cast<uint32_t *>(row + x);
    int pairs = (right - x) >> 1;
    while (pairs-- > 0) *wide++ = pair;
    for (x = right - ((right - x) & 1); x < right; ++x) row[x] = colour;
}

void fill_rect(float fx, float fy, float fw, float fh, uint16_t colour) {
    const int y0 = static_cast<int>(std::lround(fy));
    const int y1 = static_cast<int>(std::lround(fy + fh));
    const int x0 = static_cast<int>(std::lround(fx));
    const int x1 = static_cast<int>(std::lround(fx + fw));
    for (int y = y0; y < y1; ++y) span(y, x0, x1, colour);
}

void fill_round_rect(float cx, float cy, float hw, float hh, float radius,
                     uint16_t colour) {
    if (hw <= 0.0f || hh <= 0.0f) return;
    radius = std::min(radius, std::min(hw, hh));
    const int y0 = static_cast<int>(std::lround(cy - hh));
    const int y1 = static_cast<int>(std::lround(cy + hh));
    for (int y = y0; y < y1; ++y) {
        const float dy = std::fabs((y + 0.5f) - cy) - (hh - radius);
        float inset = 0.0f;
        if (dy > 0.0f) {
            const float k = 1.0f - (dy / radius) * (dy / radius);
            inset = radius * (1.0f - std::sqrt(std::max(0.0f, k)));
        }
        span(y, static_cast<int>(std::lround(cx - hw + inset)),
             static_cast<int>(std::lround(cx + hw - inset)), colour);
    }
}

void fill_circle(float cx, float cy, float r, uint16_t colour) {
    if (r <= 0.0f) return;
    const int y0 = static_cast<int>(std::lround(cy - r));
    const int y1 = static_cast<int>(std::lround(cy + r));
    for (int y = y0; y <= y1; ++y) {
        const float dy = (y + 0.5f) - cy;
        const float half = r * r - dy * dy;
        if (half <= 0.0f) continue;
        const float w = std::sqrt(half);
        span(y, static_cast<int>(std::lround(cx - w)),
             static_cast<int>(std::lround(cx + w)), colour);
    }
}

void fill_ring(float cx, float cy, float r, float thickness, uint16_t colour) {
    const float outer = r + thickness * 0.5f;
    const float inner = std::max(0.0f, r - thickness * 0.5f);
    const int y0 = static_cast<int>(std::lround(cy - outer));
    const int y1 = static_cast<int>(std::lround(cy + outer));
    for (int y = y0; y <= y1; ++y) {
        const float dy = (y + 0.5f) - cy;
        const float ow = outer * outer - dy * dy;
        if (ow <= 0.0f) continue;
        const float outer_w = std::sqrt(ow);
        const float iw = inner * inner - dy * dy;
        if (iw <= 0.0f) {
            span(y, static_cast<int>(cx - outer_w), static_cast<int>(cx + outer_w), colour);
            continue;
        }
        const float inner_w = std::sqrt(iw);
        span(y, static_cast<int>(cx - outer_w), static_cast<int>(cx - inner_w), colour);
        span(y, static_cast<int>(cx + inner_w), static_cast<int>(cx + outer_w), colour);
    }
}

void fill_limb(float x0, float y0, float x1, float y1, float thickness,
               uint16_t colour) {
    const float dx = x1 - x0, dy = y1 - y0;
    const float length = std::sqrt(dx * dx + dy * dy);
    const int steps = std::max(1, static_cast<int>(length / (thickness * 0.4f)));
    for (int i = 0; i <= steps; ++i) {
        const float t = static_cast<float>(i) / steps;
        fill_circle(x0 + dx * t, y0 + dy * t, thickness * 0.5f, colour);
    }
}

// ------------------------------------------------------------- the pose ----

enum class Eyes : uint8_t { Open, Closed, Happy, Wide, WinkL, WinkR, Star, Heart };
enum class Mouth : uint8_t { Smile, Open, Smug, Cat, Flat };

struct Pose {
    float x = 0.0f;          // offset from stage centre
    float y = 0.0f;          // offset from kBodyBaseY, negative is up
    float scale = 1.0f;
    float squash = 1.0f;     // >1 wide and short, <1 tall and narrow
    float tilt = 0.0f;       // lean, in "pixels of shear per body height"
    float flip = 1.0f;       // horizontal scale; negative is turned around
    float arm_l = 12.0f;     // claw angle, degrees from straight down
    float arm_r = 12.0f;
    float leg = 0.0f;        // leg cycle phase, in turns
    float leg_spread = 1.0f;
    float look_x = 0.0f;
    float look_y = 0.0f;
    float blush = 0.0f;
    Eyes eyes = Eyes::Open;
    Mouth mouth = Mouth::Smile;
    float smoothing = 0.45f; // 1.0 snaps, lower is softer
};

Pose g_pose;

// Everything that moves, in one box: the crab at its largest, its reflection
// below the floor, the equaliser strip, and enough margin for the claws at full
// extension and a jump. Particles are not tracked individually -- they mostly
// fly out of the crab -- so the box is generous rather than tight.
Rect content_bounds(const Pose &pose) {
    const int cx = static_cast<int>(kCentreX + pose.x);
    const int cy = static_cast<int>(kBodyBaseY + pose.y);
    const int reach = static_cast<int>(200.0f * std::max(1.0f, pose.scale));
    Rect box{cx - reach, cy - reach, cx + reach, cy + reach};
    // The reflection is the scene mirrored about the floor line.
    const int mirrored_top = 2 * kFloorY - box.y1;
    const int mirrored_bottom = 2 * kFloorY - box.y0;
    box.y0 = std::min(box.y0, mirrored_top);
    box.y1 = std::max(box.y1, mirrored_bottom);
    // The equaliser lives along the bottom and reacts every frame.
    box.y1 = kScreen;
    box.x0 = std::max(0, std::min(box.x0, (kScreen - 366) / 2));
    box.x1 = std::min(kScreen, std::max(box.x1, kScreen - (kScreen - 366) / 2));
    box.y0 = std::max(0, box.y0);
    return box;
}

void blend(Pose &current, const Pose &target, float k) {
    k = clampf(k, 0.0f, 1.0f);
    current.x = lerpf(current.x, target.x, k);
    current.y = lerpf(current.y, target.y, k);
    current.scale = lerpf(current.scale, target.scale, k);
    current.squash = lerpf(current.squash, target.squash, k);
    current.tilt = lerpf(current.tilt, target.tilt, k);
    current.flip = lerpf(current.flip, target.flip, k);
    current.arm_l = lerpf(current.arm_l, target.arm_l, k);
    current.arm_r = lerpf(current.arm_r, target.arm_r, k);
    current.leg = target.leg;              // a phase, not a position
    current.leg_spread = lerpf(current.leg_spread, target.leg_spread, k);
    current.look_x = lerpf(current.look_x, target.look_x, k);
    current.look_y = lerpf(current.look_y, target.look_y, k);
    current.blush = lerpf(current.blush, target.blush, k);
    current.eyes = target.eyes;            // discrete: switch, never fade
    current.mouth = target.mouth;
    current.smoothing = target.smoothing;
}

// --------------------------------------------------------------- drawing ---

void draw_eyes(float cx, float cy, float hw, float hh, const Pose &pose) {
    const float sign = pose.flip < 0.0f ? -1.0f : 1.0f;
    const float eye_dx = hw * 0.42f * std::fabs(pose.flip);
    const float eye_y = cy - hh * 0.20f;
    const float socket = hh * 0.30f;
    for (int side = 0; side < 2; ++side) {
        const float x = cx + (side == 0 ? -eye_dx : eye_dx) * sign;
        const bool winking =
            (pose.eyes == Eyes::WinkL && side == 0) ||
            (pose.eyes == Eyes::WinkR && side == 1);
        if (pose.eyes == Eyes::Closed || winking) {
            // A closed eye is a curve, not a line: two stacked bars with the
            // upper one narrower reads as a lash from two metres away.
            fill_round_rect(x, eye_y + socket * 0.2f, socket * 0.75f, socket * 0.16f,
                            socket * 0.16f, c_bg);
            fill_round_rect(x, eye_y, socket * 0.5f, socket * 0.12f,
                            socket * 0.12f, c_bg);
            continue;
        }
        if (pose.eyes == Eyes::Happy) {
            // ^ ^ -- two angled bars.
            for (int i = 0; i < 2; ++i) {
                const float dir = i == 0 ? -1.0f : 1.0f;
                fill_limb(x, eye_y - socket * 0.25f,
                          x + dir * socket * 0.55f, eye_y + socket * 0.25f,
                          socket * 0.28f, c_bg);
            }
            continue;
        }
        const float grow = pose.eyes == Eyes::Wide ? 1.22f : 1.0f;
        fill_circle(x, eye_y, socket * grow, c_bg);
        if (pose.eyes == Eyes::Star) {
            for (int i = 0; i < 5; ++i) {
                const float a = i * kTau / 5.0f - kTau / 4.0f;
                fill_circle(x + std::cos(a) * socket * 0.42f,
                            eye_y + std::sin(a) * socket * 0.42f,
                            socket * 0.26f, c_glint);
            }
            fill_circle(x, eye_y, socket * 0.34f, c_glint);
            continue;
        }
        if (pose.eyes == Eyes::Heart) {
            fill_circle(x - socket * 0.24f, eye_y - socket * 0.14f, socket * 0.34f, c_glint);
            fill_circle(x + socket * 0.24f, eye_y - socket * 0.14f, socket * 0.34f, c_glint);
            for (int i = 0; i <= 6; ++i) {
                const float t = i / 6.0f;
                fill_circle(x, eye_y + socket * 0.10f + t * socket * 0.42f,
                            socket * (0.40f - t * 0.34f), c_glint);
            }
            continue;
        }
        // Pupil plus a glint. The glint is what makes her look alive rather
        // than switched on.
        const float px = x + pose.look_x * socket * 0.42f;
        const float py = eye_y + pose.look_y * socket * 0.38f;
        fill_circle(px, py, socket * 0.52f * grow, c_glint);
        fill_circle(px - socket * 0.20f, py - socket * 0.22f, socket * 0.18f, c_shell);
    }
}

void draw_mouth(float cx, float cy, float hh, const Pose &pose) {
    const float my = cy + hh * 0.42f;
    switch (pose.mouth) {
        case Mouth::Open:
            fill_circle(cx, my, hh * 0.19f, c_bg);
            fill_circle(cx, my + hh * 0.05f, hh * 0.11f, c_deep);
            break;
        case Mouth::Smug:
            fill_limb(cx - hh * 0.16f, my, cx + hh * 0.22f, my - hh * 0.10f,
                      hh * 0.09f, c_bg);
            break;
        case Mouth::Cat:  // the "w" mouth
            fill_limb(cx - hh * 0.24f, my - hh * 0.05f, cx - hh * 0.08f, my + hh * 0.07f,
                      hh * 0.08f, c_bg);
            fill_limb(cx - hh * 0.08f, my + hh * 0.07f, cx, my - hh * 0.05f,
                      hh * 0.08f, c_bg);
            fill_limb(cx, my - hh * 0.05f, cx + hh * 0.08f, my + hh * 0.07f,
                      hh * 0.08f, c_bg);
            fill_limb(cx + hh * 0.08f, my + hh * 0.07f, cx + hh * 0.24f, my - hh * 0.05f,
                      hh * 0.08f, c_bg);
            break;
        case Mouth::Flat:
            fill_round_rect(cx, my, hh * 0.18f, hh * 0.05f, hh * 0.05f, c_bg);
            break;
        case Mouth::Smile:
        default:
            // Corners up, centre down: a curve of small dots is cheaper than
            // an arc and reads better at this size.
            for (int i = -3; i <= 3; ++i) {
                const float t = i / 3.0f;
                fill_circle(cx + t * hh * 0.26f, my + hh * 0.10f * (1.0f - t * t),
                            hh * 0.07f, c_bg);
            }
            break;
    }
}

void draw_crab(const Pose &pose) {
    const float scale = pose.scale;
    const float hw = kBodyHalfW * scale * pose.squash * std::fabs(pose.flip);
    const float hh = kBodyHalfH * scale / pose.squash;
    const float cx = kCentreX + pose.x;
    const float cy = kBodyBaseY + pose.y;
    const float sign = pose.flip < 0.0f ? -1.0f : 1.0f;
    const float lean = pose.tilt * hh;

    // Legs first, so the shell overlaps their tops.
    const float foot_base = cy + hh * 0.92f;
    for (int side = 0; side < 2; ++side) {
        const float dir = (side == 0 ? -1.0f : 1.0f) * sign;
        for (int i = 0; i < 3; ++i) {
            const float hipx = cx + dir * hw * (0.30f + i * 0.26f);
            const float hipy = cy + hh * (0.55f - i * 0.06f);
            const float cycle = pose.leg * kTau + i * 0.8f + (side ? kTau * 0.5f : 0.0f);
            const float lift = std::max(0.0f, std::sin(cycle)) * 14.0f * scale;
            const float kneex = hipx + dir * 20.0f * scale * pose.leg_spread;
            const float kneey = hipy + 22.0f * scale - lift * 0.5f;
            const float footx = kneex + dir * 8.0f * scale * pose.leg_spread;
            const float footy = foot_base - lift;
            fill_limb(hipx, hipy, kneex, kneey, 11.0f * scale, c_shell_dark);
            fill_limb(kneex, kneey, footx, footy, 9.0f * scale, c_shell_dark);
            fill_circle(footx, footy, 6.0f * scale, c_shell);
        }
    }

    // Arms and claws.
    const float arm_len = 74.0f * scale;
    float claw_x[2], claw_y[2];
    for (int side = 0; side < 2; ++side) {
        const float dir = (side == 0 ? -1.0f : 1.0f) * sign;
        const float angle = (side == 0 ? pose.arm_l : pose.arm_r) * kDeg;
        const float shoulder_x = cx + dir * hw * 0.78f + lean * 0.35f;
        const float shoulder_y = cy - hh * 0.10f;
        const float x = shoulder_x + dir * std::sin(angle) * arm_len;
        const float y = shoulder_y + std::cos(angle) * arm_len;
        claw_x[side] = x;
        claw_y[side] = y;
        fill_limb(shoulder_x, shoulder_y, x, y, 13.0f * scale, c_shell_dark);
    }

    // Shell, then the pincers on top of it so a claw held across the face
    // reads as being in front.
    fill_round_rect(cx + lean * 0.5f, cy, hw, hh, hh * 0.55f, c_shell);
    // A soft top highlight gives the shell volume; without it she is a
    // flat blob at this size.
    fill_round_rect(cx + lean * 0.7f, cy - hh * 0.52f, hw * 0.72f, hh * 0.22f,
                    hh * 0.20f, lerp565(c_shell, c_glint, 0.22f));

    draw_eyes(cx + lean * 0.6f, cy, hw, hh, pose);
    draw_mouth(cx + lean * 0.6f, cy, hh, pose);
    if (pose.blush > 0.02f) {
        const uint16_t blush = lerp565(c_shell, c_glint, 0.45f * pose.blush);
        fill_circle(cx + lean * 0.6f - hw * 0.68f, cy + hh * 0.18f, hh * 0.17f, blush);
        fill_circle(cx + lean * 0.6f + hw * 0.68f, cy + hh * 0.18f, hh * 0.17f, blush);
    }

    for (int side = 0; side < 2; ++side) {
        const float dir = (side == 0 ? -1.0f : 1.0f) * sign;
        const float angle = (side == 0 ? pose.arm_l : pose.arm_r) * kDeg;
        fill_circle(claw_x[side], claw_y[side], 26.0f * scale, c_shell);
        // The pincer opening: a wedge of ground colour along the arm's
        // direction, which is cheap and unmistakably a claw.
        const float ox = dir * std::sin(angle), oy = std::cos(angle);
        fill_limb(claw_x[side] + ox * 6.0f * scale, claw_y[side] + oy * 6.0f * scale,
                  claw_x[side] + ox * 26.0f * scale, claw_y[side] + oy * 26.0f * scale,
                  9.0f * scale, c_bg);
    }
}

// ------------------------------------------------------------ particles ----

enum class Bit : uint8_t { Dot, Confetti, Heart, Ring, Dust };

struct Particle {
    float x, y, vx, vy, life, life0, size, gravity;
    uint16_t colour;
    Bit kind;
    bool used;
};

Particle g_particles[kMaxParticles];

Particle *free_particle() {
    for (auto &particle : g_particles) {
        if (!particle.used) return &particle;
    }
    // Steal the oldest rather than dropping the new one: a burst that lands on
    // the beat matters more than the tail of the last one.
    Particle *oldest = &g_particles[0];
    for (auto &particle : g_particles) {
        if (particle.life < oldest->life) oldest = &particle;
    }
    return oldest;
}

void spawn(Bit kind, float x, float y, float vx, float vy, float life,
           float size, uint16_t colour, float gravity = 0.0f) {
    Particle *particle = free_particle();
    *particle = {x, y, vx, vy, life, life, size, gravity, colour, kind, true};
}

void spawn_effect(DanceEffect effect, float cx, float cy, float intensity) {
    const int count = 6 + static_cast<int>(intensity * 10.0f);
    switch (effect) {
        case DanceEffect::Sparkle:
            for (int i = 0; i < count; ++i) {
                const float a = frand() * kTau;
                const float speed = 60.0f + frand() * 140.0f;
                spawn(Bit::Dot, cx + std::cos(a) * 40.0f, cy + std::sin(a) * 40.0f,
                      std::cos(a) * speed, std::sin(a) * speed,
                      0.5f + frand() * 0.4f, 3.0f + frand() * 4.0f, c_glint);
            }
            break;
        case DanceEffect::Confetti:
            for (int i = 0; i < count + 6; ++i) {
                spawn(Bit::Confetti, frand() * kScreen, -20.0f - frand() * 120.0f,
                      (frand() - 0.5f) * 60.0f, 90.0f + frand() * 120.0f,
                      2.2f + frand(), 5.0f + frand() * 6.0f,
                      (i & 1) ? c_accent : c_glint, 40.0f);
            }
            break;
        case DanceEffect::Hearts:
            for (int i = 0; i < count / 2 + 3; ++i) {
                spawn(Bit::Heart, cx + (frand() - 0.5f) * 150.0f, cy - frand() * 40.0f,
                      (frand() - 0.5f) * 40.0f, -60.0f - frand() * 70.0f,
                      1.4f + frand() * 0.6f, 9.0f + frand() * 7.0f, c_glint, -12.0f);
            }
            break;
        case DanceEffect::Burst:
            for (int i = 0; i < count + 4; ++i) {
                const float a = frand() * kTau;
                const float speed = 180.0f + frand() * 220.0f;
                spawn(Bit::Dot, cx, cy, std::cos(a) * speed, std::sin(a) * speed,
                      0.45f + frand() * 0.3f, 4.0f + frand() * 5.0f,
                      (i & 1) ? c_glint : c_accent, 120.0f);
            }
            spawn(Bit::Ring, cx, cy, 0.0f, 0.0f, 0.5f, 30.0f, c_accent);
            break;
        case DanceEffect::Rings:
            for (int i = 0; i < 3; ++i) {
                spawn(Bit::Ring, cx, cy + i * 6.0f, 0.0f, 0.0f,
                      0.75f + i * 0.14f, 24.0f + i * 20.0f, c_accent);
            }
            break;
        case DanceEffect::Flash:
            spawn(Bit::Ring, cx, cy, 0.0f, 0.0f, 0.32f, 20.0f, c_glint);
            for (int i = 0; i < 8; ++i) {
                const float a = frand() * kTau;
                spawn(Bit::Dot, cx, cy, std::cos(a) * 320.0f, std::sin(a) * 320.0f,
                      0.28f, 6.0f, c_glint);
            }
            break;
        case DanceEffect::Stars:
            for (int i = 0; i < count; ++i) {
                spawn(Bit::Dot, frand() * kScreen, frand() * kFloorY,
                      0.0f, -10.0f - frand() * 20.0f, 1.6f + frand(),
                      2.5f + frand() * 3.5f, c_glint);
            }
            break;
        case DanceEffect::None:
        default:
            break;
    }
}

void update_particles(float dt) {
    for (auto &particle : g_particles) {
        if (!particle.used) continue;
        particle.life -= dt;
        if (particle.life <= 0.0f) {
            particle.used = false;
            continue;
        }
        particle.x += particle.vx * dt;
        particle.y += particle.vy * dt;
        particle.vy += particle.gravity * dt;
    }
}

void draw_particles() {
    for (const auto &particle : g_particles) {
        if (!particle.used) continue;
        const float t = clampf(particle.life / particle.life0, 0.0f, 1.0f);
        const uint16_t colour = lerp565(c_bg, particle.colour, 0.25f + 0.75f * t);
        switch (particle.kind) {
            case Bit::Ring:
                fill_ring(particle.x, particle.y,
                          particle.size + (1.0f - t) * 190.0f,
                          2.0f + t * 6.0f, colour);
                break;
            case Bit::Confetti:
                // Spinning is faked by squeezing the width on a sine: at this
                // size it is indistinguishable from a rotating rectangle and
                // costs one multiply.
                fill_rect(particle.x, particle.y,
                          particle.size * std::fabs(std::sin(particle.life * 9.0f)) + 1.0f,
                          particle.size, colour);
                break;
            case Bit::Heart: {
                const float s = particle.size * (0.6f + 0.4f * t);
                fill_circle(particle.x - s * 0.3f, particle.y - s * 0.2f, s * 0.42f, colour);
                fill_circle(particle.x + s * 0.3f, particle.y - s * 0.2f, s * 0.42f, colour);
                for (int i = 0; i <= 4; ++i) {
                    const float k = i / 4.0f;
                    fill_circle(particle.x, particle.y + k * s * 0.7f,
                                s * (0.46f - k * 0.40f), colour);
                }
                break;
            }
            case Bit::Dust:
            case Bit::Dot:
            default:
                fill_circle(particle.x, particle.y, particle.size * (0.35f + 0.65f * t),
                            colour);
                break;
        }
    }
}

// ---------------------------------------------------------------- stage ----

float g_eq[kEqBars];
float g_energy_now = 0.5f;
float g_beat_pulse = 0.0f;

float energy_at(float beat) {
    if (g_energy_count == 0) return 0.6f;
    long index = static_cast<long>(beat);
    if (index < 0) index = 0;
    // Past the analysed window, loop it. The dynamics of the part we measured
    // are a far better guess for the rest of the song than a flat line.
    index %= static_cast<long>(g_energy_count);
    return g_energy[index] / 15.0f;
}

void draw_stage(float t) {
    const bool beams = g_quality >= 2;
    // Background: a vertical gradient that lifts toward the floor, brightened
    // on the beat. The pulse is small on purpose -- a full-screen flash every
    // beat is a strobe, not a mood.
    const float lift = 0.06f + 0.16f * g_beat_pulse * (0.4f + 0.6f * g_energy_now);
    const uint16_t top = lerp565(c_bg, c_deep, 0.35f);
    const uint16_t bottom = lerp565(c_bg, c_accent, lift);
    // Stepping by kSuper: two screen rows are one canvas row, so drawing both
    // is the same pixels written twice.
    for (int y = 0; y < kScreen; y += kSuper) {
        const float k = static_cast<float>(y) / kScreen;
        span(y, 0, kScreen, lerp565(top, bottom, k * k));
    }

    // Two spotlight beams, sweeping on a slow phrase-length cycle.
    for (int i = 0; beams && i < 2; ++i) {
        const float dir = i == 0 ? -1.0f : 1.0f;
        const float sweep = std::sin(t * 0.55f + i * 2.1f) * 0.34f;
        const float apex_x = kCentreX + dir * 150.0f;
        for (int y = 0; y < kFloorY; y += kSuper) {
            const float depth = static_cast<float>(y) + 40.0f;
            const float centre = apex_x - dir * 0.30f * depth + sweep * depth;
            const float half = 14.0f + depth * 0.16f;
            const float fade = 1.0f - static_cast<float>(y) / kFloorY;
            const uint16_t colour = lerp565(
                lerp565(top, bottom, (static_cast<float>(y) / kScreen) *
                                     (static_cast<float>(y) / kScreen)),
                c_accent, 0.10f + 0.12f * fade * (0.5f + 0.5f * g_energy_now));
            span(y, static_cast<int>(centre - half), static_cast<int>(centre + half),
                 colour);
        }
    }

    // The floor: a bright edge with a soft band under it.
    fill_rect(0, kFloorY - 3, kScreen, 3, lerp565(c_bg, c_accent, 0.45f));
    fill_rect(0, kFloorY, kScreen, kScreen - kFloorY, lerp565(c_bg, c_deep, 0.55f));
}

void draw_equalizer() {
    const float total = kScreen * 0.78f;
    const float bw = total / kEqBars;
    const float base = kScreen - 14.0f;
    for (int i = 0; i < kEqBars; ++i) {
        // A fake spectrum around the measured loudness: the bars have to move
        // differently from each other or they read as one block.
        const float tilt = 1.0f - 0.45f * std::fabs(i - (kEqBars - 1) * 0.5f) /
                                  ((kEqBars - 1) * 0.5f);
        const float target = clampf(
            g_energy_now * tilt *
                (0.65f + 0.5f * std::fabs(std::sin(g_beat * 3.1f + i * 0.9f))),
            0.0f, 1.0f);
        g_eq[i] += (target - g_eq[i]) * (target > g_eq[i] ? 0.55f : 0.16f);
        const float h = 6.0f + g_eq[i] * 54.0f;
        const float x = (kScreen - total) * 0.5f + i * bw;
        fill_round_rect(x + bw * 0.5f, base - h * 0.5f, bw * 0.32f, h * 0.5f,
                        bw * 0.28f, lerp565(c_accent, c_glint, g_eq[i] * 0.6f));
    }
}

void draw_shadow(const Pose &pose) {
    // Weight. The shadow shrinks and darkens as she leaves the ground, which
    // is most of what sells a jump.
    const float height = clampf(-pose.y / 90.0f, 0.0f, 1.0f);
    const float w = (70.0f - height * 26.0f) * pose.scale;
    fill_round_rect(kCentreX + pose.x * 0.85f, kFloorY - 4.0f, w, 9.0f - height * 3.0f,
                    9.0f, lerp565(c_bg, c_deep, 0.75f - height * 0.35f));
}

// ------------------------------------------------------------- the moves ---

struct MoveCtx {
    float beat;       // absolute, fractional
    float local;      // beats since this step started
    float sub;        // position inside the current beat, 0..1
    float phase;      // 0..1 through the whole step
    int index;        // whole beats since the step started
    float intensity;  // 0..1
    float energy;     // 0..1
};

using MoveFn = void (*)(const MoveCtx &, Pose &);

void move_groove(const MoveCtx &c, Pose &p) {
    const float k = hit(c.sub);
    p.y = -10.0f * k * (0.6f + c.intensity);
    p.squash = 1.0f + 0.09f * k;
    p.tilt = 0.10f * (c.index % 2 ? 1.0f : -1.0f) * (0.5f + 0.5f * k);
    p.arm_l = 18.0f + 12.0f * k;
    p.arm_r = 18.0f + 12.0f * (1.0f - k);
    p.leg = c.beat * 0.5f;
    p.look_x = (c.index % 2) ? 0.3f : -0.3f;
    p.mouth = Mouth::Smile;
}

void move_bounce(const MoveCtx &c, Pose &p) {
    const float k = hit(c.sub, 4.0f);
    p.y = -34.0f * k * (0.5f + c.intensity * 0.9f);
    p.squash = 1.0f + 0.26f * (1.0f - k) * k * 4.0f;
    p.arm_l = p.arm_r = 30.0f + 45.0f * k;
    p.leg = c.beat;
    p.eyes = (c.sub < 0.25f) ? Eyes::Happy : Eyes::Open;
    p.mouth = Mouth::Open;
    p.smoothing = 0.6f;
}

void move_hip_sway(const MoveCtx &c, Pose &p) {
    const float s = std::sin(c.beat * kTau * 0.25f);
    p.x = s * 52.0f * (0.5f + c.intensity * 0.8f);
    p.tilt = -s * 0.22f;
    p.y = -6.0f * std::fabs(std::sin(c.beat * kTau * 0.5f));
    p.arm_l = 26.0f + s * 26.0f;
    p.arm_r = 26.0f - s * 26.0f;
    p.leg = c.beat * 0.25f;
    p.look_x = s * 0.7f;
    p.eyes = Eyes::Happy;
    p.smoothing = 0.35f;
}

void move_side_step(const MoveCtx &c, Pose &p) {
    const int leg_of_four = c.index % 4;
    const float dir = leg_of_four < 2 ? 1.0f : -1.0f;
    const float step = ease_out(clampf(c.sub * 1.7f, 0.0f, 1.0f));
    p.x = dir * (leg_of_four % 2 == 0 ? step : 1.0f) * 46.0f;
    p.y = -12.0f * std::sin(clampf(c.sub * 1.7f, 0.0f, 1.0f) * kTau * 0.5f);
    p.tilt = dir * 0.12f;
    p.arm_l = 20.0f + (dir > 0 ? 40.0f : 0.0f);
    p.arm_r = 20.0f + (dir < 0 ? 40.0f : 0.0f);
    p.leg = c.beat * 0.75f;
    p.look_x = dir * 0.6f;
}

void move_body_roll(const MoveCtx &c, Pose &p) {
    // A wave travelling up the body, faked with an out-of-phase squash and
    // lean. On a round shell it reads exactly like the real move.
    const float w = c.beat * kTau * 0.5f;
    p.squash = 1.0f + 0.20f * std::sin(w) * (0.5f + c.intensity * 0.7f);
    p.tilt = 0.20f * std::sin(w - 0.9f);
    p.y = -8.0f * std::sin(w - 1.8f);
    p.arm_l = 40.0f + 18.0f * std::sin(w - 0.5f);
    p.arm_r = 40.0f - 18.0f * std::sin(w - 0.5f);
    p.leg = c.beat * 0.25f;
    p.eyes = Eyes::Closed;
    p.mouth = Mouth::Smug;
    p.smoothing = 0.3f;
}

void move_claw_wave(const MoveCtx &c, Pose &p) {
    const bool left = (static_cast<int>(c.local) / 2) % 2 == 0;
    const float swing = std::sin(c.beat * kTau * 0.5f) * 26.0f;
    p.arm_l = left ? 150.0f + swing : 14.0f;
    p.arm_r = left ? 14.0f : 150.0f - swing;
    p.tilt = left ? -0.12f : 0.12f;
    p.y = -8.0f * hit(c.sub);
    p.leg = c.beat * 0.25f;
    p.look_x = left ? -0.5f : 0.5f;
    p.look_y = -0.4f;
    p.eyes = Eyes::Happy;
    p.mouth = Mouth::Open;
}

void move_disco_point(const MoveCtx &c, Pose &p) {
    const bool up_right = c.index % 2 == 0;
    const float k = ease_back(clampf(c.sub * 3.2f, 0.0f, 1.0f));
    p.arm_r = up_right ? 20.0f + 140.0f * k : 8.0f;
    p.arm_l = up_right ? 8.0f : 20.0f + 140.0f * k;
    p.tilt = (up_right ? -1.0f : 1.0f) * 0.20f * k;
    p.y = -14.0f * k;
    p.squash = 1.0f - 0.06f * k;
    p.leg = c.beat * 0.5f;
    p.look_x = up_right ? 0.6f : -0.6f;
    p.look_y = -0.5f;
    p.mouth = Mouth::Smug;
    p.smoothing = 0.75f;
}

void move_spin(const MoveCtx &c, Pose &p) {
    const float turns = 1.0f + (c.intensity > 0.6f ? 1.0f : 0.0f);
    const float a = clampf(c.phase, 0.0f, 1.0f) * turns * kTau;
    p.flip = std::cos(a);
    // A spin that stays flat on the floor looks like a turntable; a small hop
    // through the middle of it looks like a dancer.
    p.y = -26.0f * std::sin(clampf(c.phase, 0.0f, 1.0f) * kTau * 0.5f);
    p.arm_l = p.arm_r = 96.0f;
    p.leg = c.beat * 1.5f;
    p.eyes = Eyes::Happy;
    p.mouth = Mouth::Open;
    p.smoothing = 0.9f;
}

void move_jump(const MoveCtx &c, Pose &p) {
    // One jump per beat: up on the first half, down on the second, with a
    // crouch loaded in the last fifth of the beat before the next one.
    const float air = std::max(0.0f, std::sin(clampf(c.sub, 0.0f, 1.0f) * kTau * 0.5f));
    const float crouch = c.sub > 0.82f ? (c.sub - 0.82f) / 0.18f : 0.0f;
    p.y = -96.0f * air * (0.55f + c.intensity * 0.7f) + crouch * 16.0f;
    p.squash = 1.0f + 0.30f * crouch - 0.16f * air;
    p.arm_l = p.arm_r = 30.0f + 130.0f * air;
    p.leg_spread = 1.0f - 0.5f * air;
    p.leg = 0.0f;
    p.eyes = air > 0.3f ? Eyes::Wide : Eyes::Open;
    p.mouth = Mouth::Open;
    p.smoothing = 0.85f;
}

void move_shimmy(const MoveCtx &c, Pose &p) {
    p.x = std::sin(c.beat * kTau * 2.0f) * 18.0f * (0.5f + c.intensity);
    p.tilt = std::sin(c.beat * kTau * 2.0f + 1.2f) * 0.14f;
    p.arm_l = p.arm_r = 92.0f;
    p.squash = 1.0f + 0.05f * std::sin(c.beat * kTau * 4.0f);
    p.leg = c.beat * 0.5f;
    p.eyes = Eyes::Happy;
    p.mouth = Mouth::Cat;
    p.smoothing = 0.8f;
}

void move_robot(const MoveCtx &c, Pose &p) {
    // Quantised to eighth notes and never smoothed: the whole point of the
    // move is that it does not interpolate.
    const int tick = static_cast<int>(c.beat * 2.0f);
    const int state = tick % 4;
    p.arm_l = (state == 0 || state == 3) ? 100.0f : 16.0f;
    p.arm_r = (state == 1 || state == 2) ? 100.0f : 16.0f;
    p.tilt = (state < 2 ? -1.0f : 1.0f) * 0.16f;
    p.y = (state % 2) ? -10.0f : 0.0f;
    p.leg = static_cast<float>(state) * 0.25f;
    p.eyes = Eyes::Wide;
    p.mouth = Mouth::Flat;
    p.look_x = (state < 2) ? -0.8f : 0.8f;
    p.smoothing = 1.0f;
}

void move_pop_lock(const MoveCtx &c, Pose &p) {
    const float k = c.sub < 0.16f ? 1.0f : 0.0f;   // hit, then hold
    const bool up = c.index % 2 == 0;
    p.arm_l = up ? 130.0f : 24.0f;
    p.arm_r = up ? 24.0f : 130.0f;
    p.squash = 1.0f + 0.16f * k;
    p.y = -16.0f * k;
    p.tilt = (up ? 0.14f : -0.14f);
    p.leg = static_cast<float>(c.index) * 0.5f;
    p.eyes = Eyes::Wide;
    p.mouth = Mouth::Smug;
    p.smoothing = 1.0f;
}

void move_moonwalk(const MoveCtx &c, Pose &p) {
    const float glide = std::fmod(c.local, 8.0f) / 8.0f;
    p.x = lerpf(70.0f, -70.0f, glide);
    p.tilt = 0.18f;
    p.y = -4.0f * std::fabs(std::sin(c.beat * kTau * 0.5f));
    p.arm_l = 62.0f;
    p.arm_r = 34.0f;
    p.leg = -c.beat * 1.25f;      // feet moving against the travel
    p.look_x = -0.5f;
    p.eyes = Eyes::Happy;
    p.mouth = Mouth::Smug;
    p.smoothing = 0.5f;
}

void move_kick(const MoveCtx &c, Pose &p) {
    const float k = ease_back(clampf(c.sub * 3.0f, 0.0f, 1.0f));
    const float dir = c.index % 2 == 0 ? 1.0f : -1.0f;
    p.x = -dir * 14.0f * k;
    p.tilt = -dir * 0.26f * k;
    p.leg_spread = 1.0f + 1.6f * k;
    p.arm_l = 40.0f + (dir < 0 ? 60.0f : 0.0f) * k;
    p.arm_r = 40.0f + (dir > 0 ? 60.0f : 0.0f) * k;
    p.leg = 0.0f;
    p.y = -8.0f * k;
    p.mouth = Mouth::Open;
    p.smoothing = 0.8f;
}

void move_head_bang(const MoveCtx &c, Pose &p) {
    const float k = std::sin(c.beat * kTau) * 0.5f + 0.5f;
    p.tilt = lerpf(-0.32f, 0.26f, k);
    p.y = -12.0f * k;
    p.squash = 1.0f + 0.10f * k;
    p.arm_l = p.arm_r = 120.0f;
    p.leg = c.beat * 0.5f;
    p.eyes = Eyes::Closed;
    p.mouth = Mouth::Open;
    p.smoothing = 0.75f;
}

void move_sprinkler(const MoveCtx &c, Pose &p) {
    const float sweep = std::fmod(c.local, 4.0f) / 4.0f;
    p.arm_r = lerpf(30.0f, 140.0f, sweep < 0.75f ? sweep / 0.75f : 1.0f);
    p.arm_l = 150.0f;                       // the hand behind the head
    p.tilt = lerpf(0.16f, -0.16f, sweep);
    p.y = -6.0f * hit(c.sub);
    p.leg = c.beat * 0.5f;
    p.look_x = lerpf(-0.7f, 0.7f, sweep);
    p.mouth = Mouth::Smug;
    p.smoothing = 0.6f;
}

void move_crab_walk(const MoveCtx &c, Pose &p) {
    const float travel = std::sin(c.beat * kTau * 0.125f);
    p.x = travel * 96.0f;
    p.y = 8.0f;
    p.squash = 1.12f;
    p.leg_spread = 1.4f;
    p.leg = c.beat * 2.0f;
    p.arm_l = p.arm_r = 74.0f;
    p.look_x = travel > 0.0f ? 0.8f : -0.8f;
    p.eyes = Eyes::Wide;
    p.mouth = Mouth::Cat;
    p.smoothing = 0.5f;
}

void move_starfish(const MoveCtx &c, Pose &p) {
    const float k = ease_back(clampf(c.sub * 2.6f, 0.0f, 1.0f));
    p.arm_l = p.arm_r = 20.0f + 118.0f * k;
    p.leg_spread = 1.0f + 1.2f * k;
    p.scale = 1.0f + 0.10f * k;
    p.y = -10.0f * k;
    p.leg = 0.0f;
    p.eyes = Eyes::Star;
    p.mouth = Mouth::Open;
    p.smoothing = 0.85f;
}

void move_heart_hands(const MoveCtx &c, Pose &p) {
    const float k = ease_out(clampf(c.phase * 2.4f, 0.0f, 1.0f));
    p.arm_l = 20.0f + 138.0f * k;
    p.arm_r = 20.0f + 138.0f * k;
    p.y = -8.0f - 6.0f * std::sin(c.beat * kTau * 0.5f);
    p.blush = k;
    p.eyes = Eyes::Heart;
    p.mouth = Mouth::Cat;
    p.look_y = -0.3f;
    p.leg = c.beat * 0.25f;
    p.smoothing = 0.4f;
}

void move_dab(const MoveCtx &c, Pose &p) {
    const float k = ease_back(clampf(c.sub * 3.4f, 0.0f, 1.0f));
    p.arm_l = 20.0f + 128.0f * k;     // across the face
    p.arm_r = 20.0f + 152.0f * k;     // out and up
    p.tilt = -0.28f * k;
    p.y = -6.0f * k;
    p.eyes = Eyes::Closed;
    p.mouth = Mouth::Smug;
    p.leg = 0.0f;
    p.smoothing = 0.9f;
}

void move_wink_push_in(const MoveCtx &c, Pose &p) {
    const float k = ease_out(clampf(c.phase * 1.5f, 0.0f, 1.0f));
    p.scale = 1.0f + 0.34f * k;       // toward the viewer
    p.y = 14.0f * k;
    p.arm_r = 60.0f + 60.0f * k;
    p.arm_l = 16.0f;
    p.blush = k;
    p.eyes = (c.phase > 0.45f) ? Eyes::WinkR : Eyes::Open;
    p.mouth = Mouth::Smug;
    p.leg = 0.0f;
    p.smoothing = 0.35f;
}

void move_snap_freeze(const MoveCtx &c, Pose &p) {
    // Everything happens in the first tenth of a beat and then absolutely
    // nothing happens, which is what makes it land.
    const float k = c.sub < 0.10f ? c.sub / 0.10f : 1.0f;
    p.arm_l = lerpf(20.0f, 148.0f, k);
    p.arm_r = lerpf(20.0f, 62.0f, k);
    p.tilt = lerpf(0.0f, -0.22f, k);
    p.squash = lerpf(1.0f, 0.92f, k);
    p.y = lerpf(0.0f, -12.0f, k);
    p.leg = 0.0f;
    p.leg_spread = 1.3f;
    p.eyes = Eyes::Wide;
    p.mouth = Mouth::Flat;
    p.smoothing = 1.0f;
}

void move_bow(const MoveCtx &c, Pose &p) {
    const float down = ease_out(clampf(c.phase * 2.2f, 0.0f, 1.0f));
    const float up = ease_out(clampf((c.phase - 0.72f) / 0.28f, 0.0f, 1.0f));
    const float k = down - up;
    p.tilt = 0.55f * k;
    p.y = 22.0f * k;
    p.squash = 1.0f + 0.16f * k;
    p.arm_l = 30.0f + 70.0f * k;
    p.arm_r = 30.0f + 70.0f * k;
    p.eyes = k > 0.4f ? Eyes::Closed : Eyes::Happy;
    p.mouth = Mouth::Smile;
    p.leg = 0.0f;
    p.smoothing = 0.25f;
}

void move_wave_crowd(const MoveCtx &c, Pose &p) {
    const float swing = std::sin(c.beat * kTau * 0.5f);
    p.arm_l = 150.0f + swing * 18.0f;
    p.arm_r = 150.0f - swing * 18.0f;
    p.tilt = swing * 0.12f;
    p.y = -10.0f * hit(c.sub);
    p.leg = c.beat * 0.25f;
    p.eyes = Eyes::Happy;
    p.mouth = Mouth::Open;
    p.look_y = -0.4f;
    p.smoothing = 0.45f;
}

void move_twirl(const MoveCtx &c, Pose &p) {
    const float a = clampf(c.phase, 0.0f, 1.0f) * kTau;
    p.flip = std::cos(a);
    p.arm_l = p.arm_r = 155.0f;
    p.scale = 1.0f + 0.06f * std::sin(clampf(c.phase, 0.0f, 1.0f) * kTau * 0.5f);
    p.y = -18.0f * std::sin(clampf(c.phase, 0.0f, 1.0f) * kTau * 0.5f);
    p.leg = c.beat;
    p.eyes = Eyes::Happy;
    p.mouth = Mouth::Smile;
    p.smoothing = 0.8f;
}

void move_stomp(const MoveCtx &c, Pose &p) {
    const float k = c.sub < 0.12f ? 1.0f - c.sub / 0.12f : 0.0f;
    const float lift = c.sub > 0.70f ? (c.sub - 0.70f) / 0.30f : 0.0f;
    p.y = -34.0f * lift + 10.0f * k;
    p.squash = 1.0f + 0.28f * k;
    p.arm_l = p.arm_r = 26.0f + 70.0f * lift;
    p.leg_spread = 1.35f;
    p.leg = 0.0f;
    p.tilt = 0.0f;
    p.eyes = k > 0.5f ? Eyes::Wide : Eyes::Open;
    p.mouth = Mouth::Open;
    p.smoothing = 0.95f;
}

const MoveFn kMoves[static_cast<size_t>(DanceMove::Count)] = {
    move_groove, move_bounce, move_hip_sway, move_side_step, move_body_roll,
    move_claw_wave, move_disco_point, move_spin, move_jump, move_shimmy,
    move_robot, move_pop_lock, move_moonwalk, move_kick, move_head_bang,
    move_sprinkler, move_crab_walk, move_starfish, move_heart_hands, move_dab,
    move_wink_push_in, move_snap_freeze, move_bow, move_wave_crowd, move_twirl,
    move_stomp,
};

// Moves that throw something of their own on the beat, on top of whatever
// effect the routine asked for.
void move_particles(DanceMove move, const MoveCtx &c, const Pose &pose) {
    const float fx = kCentreX + pose.x;
    const float fy = kFloorY - 6.0f;
    switch (move) {
        case DanceMove::Jump:
        case DanceMove::Stomp:
            if (c.sub < 0.08f) {
                for (int i = 0; i < 8; ++i) {
                    const float dir = (frand() - 0.5f) * 2.0f;
                    spawn(Bit::Dust, fx, fy, dir * 190.0f, -40.0f - frand() * 60.0f,
                          0.35f, 5.0f + frand() * 4.0f,
                          lerp565(c_shell, c_bg, 0.4f), 260.0f);
                }
            }
            break;
        case DanceMove::Spin:
        case DanceMove::Twirl:
            if (frand() < 0.4f) {
                spawn(Bit::Dot, fx + (frand() - 0.5f) * 150.0f,
                      kBodyBaseY + pose.y + (frand() - 0.5f) * 120.0f,
                      0.0f, -20.0f, 0.4f, 3.0f + frand() * 3.0f, c_glint);
            }
            break;
        case DanceMove::HeartHands:
            if (frand() < 0.25f) {
                spawn(Bit::Heart, fx + (frand() - 0.5f) * 90.0f,
                      kBodyBaseY + pose.y - 60.0f, (frand() - 0.5f) * 30.0f,
                      -70.0f, 1.3f, 10.0f + frand() * 6.0f, c_glint, -10.0f);
            }
            break;
        default:
            break;
    }
}

// ------------------------------------------------------------- warm-up -----
// What she does between the routine arriving and the first sample of music.
// Without it there is a visible pause where the panel has gone to the dance
// room and nothing is happening yet, which reads as a hang.
void warmup_pose(float t, Pose &p) {
    const float cycle = std::fmod(t, 4.0f);
    if (cycle < 1.4f) {                 // roll the claws
        const float k = cycle / 1.4f;
        p.arm_l = 20.0f + 130.0f * std::sin(k * kTau * 0.5f);
        p.arm_r = 20.0f + 130.0f * std::sin(k * kTau * 0.5f + 1.0f);
        p.tilt = 0.10f * std::sin(k * kTau);
        p.eyes = Eyes::Open;
    } else if (cycle < 2.6f) {          // a stretch
        const float k = (cycle - 1.4f) / 1.2f;
        const float s = std::sin(k * kTau * 0.5f);
        p.arm_l = p.arm_r = 20.0f + 140.0f * s;
        p.scale = 1.0f + 0.05f * s;
        p.y = -14.0f * s;
        p.eyes = s > 0.5f ? Eyes::Closed : Eyes::Open;
        p.mouth = Mouth::Open;
    } else {                            // shake it out
        const float k = (cycle - 2.6f) / 1.4f;
        p.x = std::sin(k * kTau * 3.0f) * 12.0f;
        p.tilt = std::sin(k * kTau * 3.0f + 1.0f) * 0.10f;
        p.arm_l = p.arm_r = 40.0f;
        p.leg = t * 1.5f;
        p.eyes = Eyes::Happy;
        p.mouth = Mouth::Cat;
    }
    p.y += -5.0f * std::fabs(std::sin(t * kTau * 0.9f));
    p.smoothing = 0.35f;
}

// ----------------------------------------------------------------- clock ---

void apply_mood(const char *mood) {
    const auto &palette = theme::persona(mood);
    c_bg = theme::rgb565(palette.background);
    c_shell = theme::rgb565(palette.mascot);
    c_accent = theme::rgb565(palette.accent);
    c_shell_dark = lerp565(c_shell, c_bg, 0.42f);
    c_glint = lerp565(c_accent, 0xFFFF, 0.55f);
    c_deep = lerp565(c_bg, 0x0000, 0.55f);
}

bool ensure_canvas() {
    if (g_buffer && g_canvas) return true;
    // Allocated on the first dance rather than at boot: a 434 KiB buffer is
    // cheap in PSRAM and free if nobody ever asks Kiki to dance.
    const size_t bytes = static_cast<size_t>(kCanvas) * kCanvas * sizeof(uint16_t);
    if (!g_buffer) {
        g_buffer = static_cast<uint16_t *>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM));
    }
    if (!g_buffer) {
        ESP_LOGE(kTag, "no PSRAM for the %dx%d dance stage", kCanvas, kCanvas);
        return false;
    }
    for (int y = 0; y < kCanvas; ++y) {
        const float dy = (y + 0.5f) * kSuper - kCentreY;
        const float w = kRadius * kRadius - dy * dy;
        g_span[y] = w > 0.0f ? static_cast<int16_t>(std::sqrt(w) / kSuper) : 0;
    }
    // The corners are outside the physical disc and are never redrawn, so they
    // are cleared once, here.
    std::fill_n(g_buffer, static_cast<size_t>(kCanvas) * kCanvas, c_deep);
    if (!g_canvas) {
        g_canvas = lv_canvas_create(g_parent ? g_parent : lv_screen_active());
        if (!g_canvas) return false;
        lv_canvas_set_buffer(g_canvas, g_buffer, kCanvas, kCanvas,
                             LV_COLOR_FORMAT_RGB565);
        lv_obj_set_pos(g_canvas, 0, 0);
        if (kSuper != 1) {
            lv_obj_set_size(g_canvas, kScreen, kScreen);
            lv_image_set_inner_align(g_canvas, LV_IMAGE_ALIGN_STRETCH);
        }
        // Taps must reach the screen's own handler, which is what stops the
        // dance.
        lv_obj_remove_flag(g_canvas, LV_OBJ_FLAG_CLICKABLE);
    }
    return true;
}

// Song position in seconds, or a negative number while the music has not
// started. Sample-exact: see the header.
float song_position() {
    if (!g_media_started.load()) return -1.0f;
    const uint64_t played = audio_pipeline_played_samples();
    const uint64_t mark = g_media_mark.load();
    if (played <= mark) return 0.0f;
    return static_cast<float>(played - mark) / 48000.0f;
}

void notify_stopped() {
    if (!g_notify_on_stop || !gateway_client_connected()) return;
    char payload[64];
    std::snprintf(payload, sizeof(payload), "\"reason\":\"%s\"", g_stop_reason);
    gateway_client_send_event("dance_stop", payload);
}

// Everything about stopping that can block: silencing the speaker (which waits
// for the playback task to acknowledge) and telling the gateway (which can sit
// on a congested socket for seconds). Neither may run on the LVGL task, and the
// touch handler that stops a dance IS the LVGL task.
void stop_worker(void *) {
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        // Silence first, and locally: a stop that waits for a round trip to
        // the laptop before the music dies does not feel like a stop button.
        audio_pipeline_stop_playback();
        notify_stopped();
    }
}

void finish_locked(const char *reason) {
    g_active = false;
    g_stop_pending = false;
    g_awaiting_media = false;
    g_media_started = false;
    ui_exit_dance();
    ESP_LOGI(kTag, "dance finished (%s) after %.1fs", reason,
             (esp_timer_get_time() - g_armed_at_us) / 1000000.0);
}

}  // namespace

// ------------------------------------------------------------------ API ----

void dance_attach(void *parent) {
    g_parent = static_cast<lv_obj_t *>(parent);
    // Low priority and off the audio core: this task only ever runs for the
    // few hundred milliseconds after a dance ends.
    if (!g_stop_worker) {
        xTaskCreatePinnedToCore(stop_worker, "kiki_dance_stop", 3072, nullptr, 3,
                                &g_stop_worker, 0);
    }
}

bool dance_start(float bpm, int32_t beat0_ms, const char *mood, const char *title,
                 const char *routine, const char *energy) {
    if (bpm < 40.0f || bpm > 220.0f) {
        ESP_LOGW(kTag, "refusing a %.1f BPM routine", static_cast<double>(bpm));
        return false;
    }
    if (bsp_display_lock(500) != ESP_OK) {
        ESP_LOGE(kTag, "could not take the display lock to start dancing");
        return false;
    }
    g_step_count = dance_parse_routine(routine, g_steps, kDanceMaxSteps);
    g_energy_count = dance_parse_energy(energy, g_energy, kDanceMaxEnergy);
    if (g_step_count == 0) {
        // A stage with no choreography is worse than no dance at all: the
        // music would play to a crab standing still.
        ESP_LOGW(kTag, "empty routine; not dancing");
        bsp_display_unlock();
        return false;
    }
    g_bpm = bpm;
    g_beat0 = static_cast<float>(beat0_ms) / 1000.0f;
    std::snprintf(g_mood, sizeof(g_mood), "%s", mood ? mood : "excited");
    apply_mood(g_mood);
    if (!ensure_canvas()) {
        bsp_display_unlock();
        return false;
    }
    for (auto &particle : g_particles) particle.used = false;
    for (float &bar : g_eq) bar = 0.0f;
    g_quality = 2;
    g_stage_dirty = true;
    g_last_dirty = {0, 0, kScreen, kScreen};
    g_pose = Pose{};
    g_beat = 0.0f;
    g_current_step = -1;
    g_fps_frames = 0;
    g_fps_window_us = esp_timer_get_time();
    g_armed_at_us = esp_timer_get_time();
    g_last_progress_us = g_armed_at_us;
    g_last_played = 0;
    g_media_started = false;
    g_awaiting_media = true;
    g_stop_pending = false;
    g_active = true;
    ui_enter_dance(g_mood);
    bsp_display_unlock();
    ESP_LOGI(kTag, "dancing to '%s': %.2f BPM, beat0 %dms, %u steps, %u energy beats",
             title ? title : "?", static_cast<double>(bpm), static_cast<int>(beat0_ms),
             static_cast<unsigned>(g_step_count), static_cast<unsigned>(g_energy_count));
    return true;
}

void dance_stop(const char *reason, bool notify) {
    if (!g_active.exchange(false)) return;
    std::snprintf(g_stop_reason, sizeof(g_stop_reason), "%s", reason ? reason : "stop");
    g_awaiting_media = false;
    g_media_started = false;
    g_notify_on_stop = notify;
    // Nothing that can block happens on this thread.
    //
    // The obvious version did the silencing and the websocket notify right
    // here -- and the commonest caller is the touch handler, which runs on the
    // LVGL task holding the display lock. `gateway_client_send_event` uses a
    // 5 s send timeout (deliberately: a shorter one tears the whole socket
    // down, see §8), and on a link congested by the very song being stopped
    // that send really does take seconds. The panel froze for all of them.
    // Observed live on 2026-08-18: tap to stop, display hangs.
    //
    // So the tap only latches the intent. The worker below does the blocking
    // work, and the next render tick -- 40 ms away, already under the display
    // lock -- puts the screen back.
    g_stop_pending = true;
    if (g_stop_worker) {
        xTaskNotifyGive(g_stop_worker);
        return;
    }
    // No worker (allocation failed at start-up): fall back to doing it here
    // rather than not stopping at all.
    audio_pipeline_stop_playback();
    notify_stopped();
}

bool dance_is_active() { return g_active.load() || g_stop_pending.load(); }

void dance_note_media_frame() {
    // The first media frame after arming is time zero for the whole
    // choreography: everything already in the ring belongs to whatever was
    // playing before, and everything after it is this song, in order.
    bool expected = true;
    if (!g_awaiting_media.compare_exchange_strong(expected, false)) return;
    g_media_mark = audio_pipeline_queued_samples();
    g_media_started = true;
    g_last_progress_us = esp_timer_get_time();
    ESP_LOGI(kTag, "dance clock armed at sample %llu",
             static_cast<unsigned long long>(g_media_mark.load()));
}

uint32_t dance_fps() { return g_fps.load(); }

uint32_t dance_draw_us() { return g_draw_us.load(); }

uint32_t dance_render_frame() {
    if (g_stop_pending.load()) {
        finish_locked(g_stop_reason);
        return kFramePeriodMs;
    }
    if (!g_active.load() || !g_buffer || !g_canvas) return kFramePeriodMs;

    const int64_t now_us = esp_timer_get_time();
    const float wall = (now_us - g_armed_at_us) / 1000000.0f;

    // --- the clock, and the two watchdogs that make it safe -----------------
    const float position = song_position();
    if (position < 0.0f) {
        if (now_us - g_armed_at_us > kMusicStartTimeoutUs) {
            ESP_LOGW(kTag, "no music arrived; leaving the stage");
            dance_stop("no_audio");
            return kFramePeriodMs;
        }
    } else {
        const uint64_t played = audio_pipeline_played_samples();
        if (played != g_last_played) {
            g_last_played = played;
            g_last_progress_us = now_us;
        } else if (now_us - g_last_progress_us > kMusicStallTimeoutUs) {
            // The song ended, was cancelled, or the link died. Whatever the
            // cause, a stage with no music on it is not a dance -- and this is
            // the backstop that makes it impossible to get stuck here.
            ESP_LOGI(kTag, "music stopped; leaving the stage");
            dance_stop("music_ended");
            return kFramePeriodMs;
        }
    }

    Pose target;
    DanceMove move = DanceMove::Groove;
    MoveCtx ctx{};
    bool index_changed = false;
    float delta_beats = 0.0f;
    if (position < 0.0f) {
        warmup_pose(wall, target);
        g_energy_now = 0.35f;
        g_beat_pulse = std::max(0.0f, 1.0f - std::fmod(wall, 0.6f) / 0.6f) * 0.5f;
    } else {
        g_prev_beat = g_beat;
        g_beat = (position - g_beat0) * g_bpm / 60.0f;
        delta_beats = g_beat - g_prev_beat;
        const int index = dance_step_at(g_steps, g_step_count, g_beat);
        const DanceStep &step =
            g_steps[index >= 0 ? static_cast<size_t>(index) : 0];
        if (index >= 0 && index != g_current_step) {
            g_current_step = index;
            index_changed = true;
            spawn_effect(static_cast<DanceEffect>(step.effect),
                         kCentreX + g_pose.x, kBodyBaseY + g_pose.y,
                         step.intensity / 15.0f);
        }
        move = static_cast<DanceMove>(step.move);
        ctx.beat = std::max(0.0f, g_beat);
        ctx.local = ctx.beat - static_cast<float>(step.beat);
        if (index < 0) ctx.local = ctx.beat;      // the count-in before step 0
        ctx.sub = ctx.beat - std::floor(ctx.beat);
        ctx.index = static_cast<int>(std::floor(std::max(0.0f, ctx.local)));
        ctx.phase = clampf(ctx.local / std::max(1.0f, static_cast<float>(step.beats)),
                           0.0f, 1.0f);
        ctx.intensity = step.intensity / 15.0f;
        ctx.energy = energy_at(ctx.beat);
        g_energy_now = lerpf(g_energy_now, ctx.energy, 0.25f);
        g_beat_pulse = hit(ctx.sub, 6.0f);
        if (index < 0) {
            warmup_pose(wall, target);
        } else {
            kMoves[step.move < static_cast<uint8_t>(DanceMove::Count) ? step.move : 0](
                ctx, target);
        }
    }

    // Beat-exact, except across a move change.
    //
    // The first version ran every frame through a first-order filter, with a
    // per-move strength. That is what "not on the beat" looked like: a filter
    // always lags, and at 160 BPM a beat is 376 ms, so a pose that should land
    // ON the beat arrived a quarter of a beat after it -- consistently, all
    // song. The filter was also doing a job nobody needed: the moves are
    // continuous functions of beat position and already carry their own easing
    // and anticipation, so between beats there is nothing to smooth.
    //
    // What genuinely needs blending is the seam where one move hands over to
    // the next, and only for a fraction of a beat. `smoothing` now scales that
    // hand-over -- a robot snaps through it, a body roll eases -- instead of
    // damping the whole performance.
    if (index_changed) g_blend_beats = kBlendBeats;
    if (g_blend_beats > 0.0f && delta_beats >= 0.0f) {
        g_blend_beats -= delta_beats;
        const float step_k = kBlendBeats > 0.0f
                                 ? clampf(delta_beats / kBlendBeats, 0.05f, 1.0f)
                                 : 1.0f;
        blend(g_pose, target, clampf(step_k + target.smoothing * 0.5f, 0.0f, 1.0f));
    } else {
        g_pose = target;
    }

    // --- draw ---------------------------------------------------------------
    //
    // Dirty rectangles, below the top quality level. Both halves of a frame --
    // our fill and LVGL's flush -- cost in proportion to the area touched, and
    // the flush is the larger of the two (measured: 97 ms of a 143 ms frame,
    // reading 434 KiB back out of the PSRAM the audio is also using). The crab,
    // its reflection, the particles and the equaliser are the only things that
    // move; the graded ground behind them does not need repainting 20 times a
    // second to look right.
    //
    // The one thing that has to go with it is the background's beat pulse,
    // which by definition changes every pixel -- so it stays at quality 2 and
    // the beat keeps showing up in the ring, the equaliser and Kiki herself.
    const int64_t draw_started_us = esp_timer_get_time();
    const bool full_frame = g_quality >= 2 || g_stage_dirty;
    Rect dirty = content_bounds(g_pose);
    if (full_frame) {
        g_clip_x0 = g_clip_y0 = 0;
        g_clip_x1 = g_clip_y1 = kScreen;
        g_stage_dirty = false;
    } else {
        // Union with last frame's box, or the pixels she has just left behind
        // keep the pose she was in.
        dirty = union_rect(dirty, g_last_dirty);
        g_clip_x0 = dirty.x0;
        g_clip_y0 = dirty.y0;
        g_clip_x1 = dirty.x1;
        g_clip_y1 = dirty.y1;
    }

    draw_stage(wall);

    if (g_quality >= 1) {
        g_mirror = true;
        g_dim = 150;
        draw_crab(g_pose);
        g_mirror = false;
        g_dim = 0;
    }

    draw_shadow(g_pose);
    draw_crab(g_pose);
    if (position >= 0.0f) move_particles(move, ctx, g_pose);
    draw_particles();
    draw_equalizer();

    update_particles(kFramePeriodMs / 1000.0f);
    g_last_dirty = content_bounds(g_pose);
    g_clip_x0 = g_clip_y0 = 0;
    g_clip_x1 = g_clip_y1 = kScreen;
    g_draw_accum_us += static_cast<uint32_t>(esp_timer_get_time() - draw_started_us);
    if (full_frame) {
        lv_obj_invalidate(g_canvas);
    } else {
        lv_area_t area;
        lv_area_set(&area, dirty.x0, dirty.y0, dirty.x1 - 1, dirty.y1 - 1);
        lv_obj_invalidate_area(g_canvas, &area);
    }

    ++g_fps_frames;
    if (now_us - g_fps_window_us >= 1000000) {
        g_draw_us = g_fps_frames ? g_draw_accum_us / g_fps_frames : 0;
        g_draw_accum_us = 0;
        g_fps = g_fps_frames;
        // Landing a pose on a 376 ms beat needs frames closer together than a
        // third of it. Below 15 fps the stage sheds an extra; above 24 it can
        // afford one back. The gap between the two stops it oscillating.
        if (g_fps_frames < 15 && g_quality > 0) {
            --g_quality;
            g_stage_dirty = true;
            ESP_LOGI(kTag, "stage quality down to %d (%u fps)", g_quality,
                     static_cast<unsigned>(g_fps_frames));
        } else if (g_fps_frames > 24 && g_quality < 2) {
            ++g_quality;
            g_stage_dirty = true;
            ESP_LOGI(kTag, "stage quality up to %d (%u fps)", g_quality,
                     static_cast<unsigned>(g_fps_frames));
        }
        g_fps_frames = 0;
        g_fps_window_us = now_us;
        // "Not on the beat" has two completely different causes -- a clock
        // that is off, and a frame rate too low to land a pose on a 376 ms
        // beat -- and they are indistinguishable from the sofa. One line a
        // second, only while dancing, tells them apart.
        ESP_LOGI(kTag, "fps=%u beat=%.2f pos=%.2fs buffered=%ums",
                 static_cast<unsigned>(g_fps.load()), static_cast<double>(g_beat),
                 static_cast<double>(position), 
                 static_cast<unsigned>(audio_pipeline_buffered_ms()));
    }

    // Audio always wins, in three steps rather than one.
    //
    // Rendering shares core 0 with the Wi-Fi stack that is feeding the song,
    // and a full-panel frame is 434 KiB of PSRAM traffic plus a full-screen
    // flush -- so on a link that is already marginal the animation can be the
    // difference between a song that plays and one that gaps. Measured on the
    // Tailscale funnel (the slow path, ~450 ms and well under the 768 kbps
    // uncompressed media needs): 102 underruns and 7.9 s of gap in 70 s of
    // dancing, with the buffer pinned at 0-30 ms against a 100 ms target.
    //
    // A single threshold was too blunt for that: by the time the buffer is
    // under 60 ms it is already empty half the time. Backing off as soon as it
    // falls below its own target, and hard when it is nearly dry, costs
    // nothing on a healthy LAN (where it sits at or above the target) and
    // gives the radio real room when it matters. A dropped frame is invisible
    // next to a gap in the audio.
    const uint32_t buffered = audio_pipeline_buffered_ms();
    if (g_media_started.load()) {
        const uint32_t target = audio_pipeline_prebuffer_ms();
        if (buffered < target / 2) return kStarvedFramePeriodMs;
        if (buffered < target) return kSlowFramePeriodMs;
    }
    const uint32_t spent =
        static_cast<uint32_t>((esp_timer_get_time() - now_us) / 1000);
    return spent >= kFramePeriodMs ? kMinFramePeriodMs : kFramePeriodMs - spent;
}

}  // namespace kiki
