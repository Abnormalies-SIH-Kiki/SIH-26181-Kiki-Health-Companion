#include "kiki_face.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "kiki_theme.hpp"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_face";

// oled_display.py's panel geometry. Everything below is expressed in these
// units and scaled on the way to the canvas.
constexpr int kLogicalW = 128;
constexpr int kLogicalH = 64;
constexpr int kCrabScale = 4;                              // CRAB_SCALE
constexpr int kCrabOx = (kLogicalW - 15 * kCrabScale) / 2;  // CRAB_OX
constexpr int kCrabOy = 0;                                  // CRAB_OY

constexpr float kTau = 6.28318530717958647692f;

lv_obj_t *g_canvas = nullptr;
uint16_t *g_buffer = nullptr;
int32_t g_canvas_w = 0;
int32_t g_canvas_h = 0;
float g_sx = 1.0f;
float g_sy = 1.0f;

char g_state[24] = "boot";
char g_detail[64] = "";
uint32_t g_frame = 0;
int64_t g_state_started_us = 0;

// The 1-bit panel could only do white-on-black. Here the shell is the mascot's
// clay and the "black" is the screen ground -- which matters because the eyes,
// mouth and blush are *cut out* of the shell rather than drawn on it, so they
// have to be whatever is behind it, not literal black.
// Updated once per frame from the current expression's coordinated palette.
// Keeping the old kWhite/kBlack names preserves the direct port's readable
// relationship to the original 1-bit PIL drawing: white means shell/prop and
// black means a cut-out into whatever mood background is currently active.
uint16_t kWhite = theme::kMascot565;       // shell / props
uint16_t kBlack = theme::kBackground565;   // background / facial cut-outs
uint16_t kGlow = theme::kAccent565;        // sound waves and sparkles

void apply_palette(const char *state) {
    const auto &palette = theme::visual(state);
    kWhite = theme::rgb565(palette.mascot);
    kBlack = theme::rgb565(palette.background);
    kGlow = theme::rgb565(palette.accent);
}

// ---------------------------------------------------------------- raster ---
// The canvas is filled directly rather than through lv_draw_*: a frame is a few
// dozen axis-aligned fills, and going straight at the buffer keeps the whole
// animation off LVGL's object and draw-descriptor paths.

inline void put_pixel(int x, int y, uint16_t colour) {
    if (x < 0 || y < 0 || x >= g_canvas_w || y >= g_canvas_h) return;
    g_buffer[y * g_canvas_w + x] = colour;
}

void fill_rect_px(int x0, int y0, int w, int h, uint16_t colour) {
    if (w <= 0 || h <= 0) return;
    const int x_end = std::min<int>(x0 + w, g_canvas_w);
    const int y_end = std::min<int>(y0 + h, g_canvas_h);
    x0 = std::max(0, x0);
    y0 = std::max(0, y0);
    for (int y = y0; y < y_end; ++y) {
        uint16_t *row = g_buffer + y * g_canvas_w;
        for (int x = x0; x < x_end; ++x) row[x] = colour;
    }
}

// A rectangle in logical panel coordinates. Edges are rounded independently so
// adjacent logical rectangles stay exactly adjacent on screen with no seam.
void prect(float x, float y, float w, float h, uint16_t colour = kWhite) {
    if (w <= 0 || h <= 0) return;
    const int x0 = static_cast<int>(lroundf(x * g_sx));
    const int y0 = static_cast<int>(lroundf(y * g_sy));
    const int x1 = static_cast<int>(lroundf((x + w) * g_sx));
    const int y1 = static_cast<int>(lroundf((y + h) * g_sy));
    fill_rect_px(x0, y0, x1 - x0, y1 - y0, colour);
}

// A rectangle in Clawd's logical *sprite* pixel coordinates (_urect).
void urect(float ox, float oy, float scale, float x, float y, float w, float h,
           uint16_t colour = kWhite) {
    prect(ox + x * scale, oy + y * scale, w * scale, h * scale, colour);
}

void pline(float x0, float y0, float x1, float y1, int width = 1,
           uint16_t colour = kWhite) {
    // Bresenham in screen space, thickened by drawing a width x width block.
    const int ax0 = static_cast<int>(lroundf(x0 * g_sx));
    const int ay0 = static_cast<int>(lroundf(y0 * g_sy));
    const int ax1 = static_cast<int>(lroundf(x1 * g_sx));
    const int ay1 = static_cast<int>(lroundf(y1 * g_sy));
    const int thickness = std::max(1, static_cast<int>(lroundf(width * g_sx)));
    int dx = std::abs(ax1 - ax0), sx = ax0 < ax1 ? 1 : -1;
    int dy = -std::abs(ay1 - ay0), sy = ay0 < ay1 ? 1 : -1;
    int err = dx + dy;
    int x = ax0, y = ay0;
    while (true) {
        fill_rect_px(x - thickness / 2, y - thickness / 2, thickness, thickness, colour);
        if (x == ax1 && y == ay1) break;
        const int e2 = 2 * err;
        if (e2 >= dy) { err += dy; x += sx; }
        if (e2 <= dx) { err += dx; y += sy; }
    }
}

// Arc segment between two angles (degrees, clockwise from +x as PIL measures
// them), used for the listening/speaking sound waves.
void parc(float cx, float cy, float r, float start_deg, float end_deg,
          int width = 1, uint16_t colour = kWhite) {
    if (end_deg < start_deg) end_deg += 360.0f;
    const float step = r > 0.0f ? std::max(0.6f, 40.0f / r) : 40.0f;
    for (float a = start_deg; a <= end_deg; a += step) {
        const float rad = a * (kTau / 360.0f);
        pline(cx + std::cos(rad) * r, cy + std::sin(rad) * r,
              cx + std::cos(rad) * r, cy + std::sin(rad) * r, width, colour);
    }
}

void pellipse_outline(float cx, float cy, float r, int width, uint16_t colour = kWhite) {
    parc(cx, cy, r, 0.0f, 360.0f, width, colour);
}

// ------------------------------------------------------------ small props ---

void pstar(float x, float y, float size = 2) {
    prect(x + size, y, size, size);
    prect(x, y + size, size * 3, size);
    prect(x + size, y + size * 2, size, size);
}

void pdots(const int (*pts)[2], size_t count, float x, float y, float scale) {
    for (size_t i = 0; i < count; ++i) {
        prect(x + pts[i][0] * scale, y + pts[i][1] * scale, scale, scale);
    }
}

void pquestion(float x, float y, float scale = 2) {
    static const int pts[][2] = {{1, 0}, {2, 0}, {0, 1}, {3, 1}, {3, 2},
                                 {2, 3}, {1, 4}, {1, 6}};
    pdots(pts, 8, x, y, scale);
}

void pexclaim(float x, float y, float scale = 2) {
    prect(x, y, scale * 2, scale * 4);
    prect(x, y + scale * 5, scale * 2, scale * 2);
}

void pz(float x, float y, float scale = 2, bool small = false) {
    static const int big[][2] = {{0, 0}, {1, 0}, {2, 0}, {3, 0}, {2, 1},
                                 {1, 2}, {0, 3}, {1, 3}, {2, 3}, {3, 3}};
    static const int tiny[][2] = {{0, 0}, {1, 0}, {1, 1}, {0, 2}, {1, 2}};
    if (small) pdots(tiny, 5, x, y, scale);
    else pdots(big, 10, x, y, scale);
}

void pheart(float x, float y, float scale = 2) {
    static const int pts[][2] = {{0, 0}, {1, 0}, {3, 0}, {4, 0},
                                 {0, 1}, {1, 1}, {2, 1}, {3, 1}, {4, 1},
                                 {1, 2}, {2, 2}, {3, 2}, {2, 3}};
    pdots(pts, 13, x, y, scale);
}

void pbulb(float x, float y, float scale = 2) {
    static const int pts[][2] = {{1, 0}, {2, 0}, {3, 0}, {0, 1}, {4, 1},
                                 {0, 2}, {4, 2}, {1, 3}, {2, 3}, {3, 3},
                                 {1, 4}, {3, 4}, {1, 5}, {2, 5}, {3, 5}};
    pdots(pts, 15, x, y, scale);
}

void psweat(float x, float y, float scale = 2) {
    static const int pts[][2] = {{1, 0}, {0, 1}, {1, 1}, {2, 1},
                                 {0, 2}, {1, 2}, {2, 2}, {1, 3}};
    pdots(pts, 8, x, y, scale);
}

void ppuff(float x, float y, float scale = 2) {
    static const int pts[][2] = {{1, 0}, {2, 0}, {0, 1}, {3, 1}, {1, 2}, {2, 2}};
    pdots(pts, 6, x, y, scale);
}

void pbt(float x, float y, float scale = 2) {
    static const int pts[][2] = {{3, 0}, {3, 1}, {3, 2}, {3, 3}, {3, 4}, {3, 5},
                                 {3, 6}, {4, 1}, {5, 2}, {4, 3}, {4, 5}, {5, 4},
                                 {1, 2}, {2, 3}, {1, 4}};
    pdots(pts, 15, x, y, scale);
}

// Blush hatch, drawn BLACK so it reads as shading cut into the white shell.
void pblush(float ox, float oy, float scale, float x, float y) {
    static const int pts[][2] = {{0, 0}, {2, 0}, {1, 1}, {3, 1}};
    for (const auto &p : pts) {
        urect(ox, oy, scale, x + p[0], y + p[1], 1, 1, kBlack);
    }
}

// ------------------------------------------------------------- the crab ----

enum class Eyes { Normal, Closed, Sad, Squint, X, Wide, Curve, Heart, Star,
                  Droopy, WinkL, WinkR, OuchL, OuchR };
enum class Mouth { None, Open, Smile, Yawn, Pout, Smirk };

struct ArmPose { int lx, ly, rx, ry; };

// _ARM_POSITIONS. The torso spans logical x=2..12 and arms are drawn *before*
// the shell's eye cut-outs, so any pose inside that span renders white-on-white
// and vanishes; every pose here stays at x<=1 or x>=11 to remain visible.
const ArmPose kArmNormal      {0, 9, 13, 9};
const ArmPose kArmUp          {0, 5, 13, 5};
const ArmPose kArmHigh        {1, 3, 12, 3};
const ArmPose kArmWaveLeft    {0, 5, 13, 9};
const ArmPose kArmWaveRight   {0, 9, 13, 5};
const ArmPose kArmChin        {0, 10, 11, 10};
const ArmPose kArmTypingA     {2, 11, 11, 12};
const ArmPose kArmTypingB     {2, 12, 11, 11};
const ArmPose kArmHeadScratch {-1, 6, 13, 9};
const ArmPose kArmConductA    {0, 4, 13, 8};
const ArmPose kArmConductB    {0, 8, 13, 4};
const ArmPose kArmSweep       {1, 9, 12, 10};
const ArmPose kArmDebug       {0, 10, 13, 10};
const ArmPose kArmCover       {0, 4, 13, 4};
const ArmPose kArmCheeks      {0, 8, 13, 8};
const ArmPose kArmHips        {0, 11, 13, 11};
const ArmPose kArmReach       {-1, 4, 14, 4};

struct CrabOpts {
    float ox = kCrabOx;
    float oy = kCrabOy;
    float scale = kCrabScale;
    float body_dx = 0;
    float body_dy = 0;
    Eyes eyes = Eyes::Normal;
    int look_x = 0;
    int look_y = 0;
    ArmPose arms = kArmNormal;
    int leg_phase = 0;
    Mouth mouth = Mouth::None;
};

void crab(const CrabOpts &o) {
    const float ox = o.ox + o.body_dx;
    const float oy = o.oy + o.body_dy;
    const float s = o.scale;

    // Legs. Walking alternates the middle/outer pairs by one logical pixel.
    int leg_y[4] = {13, 13, 13, 13};
    if (o.leg_phase == 1) { leg_y[0] = 12; leg_y[1] = 14; leg_y[2] = 14; leg_y[3] = 12; }
    else if (o.leg_phase == 2) { leg_y[0] = 14; leg_y[1] = 12; leg_y[2] = 12; leg_y[3] = 14; }
    const int leg_x[4] = {3, 5, 9, 11};
    for (int i = 0; i < 4; ++i) urect(ox, oy, s, leg_x[i], leg_y[i], 1, 2);

    // Torso: canonical 11x7 rectangle at (2,6).
    urect(ox, oy, s, 2, 6, 11, 7);

    // Arms: 2x2 blocks; only their placement changes by pose.
    urect(ox, oy, s, o.arms.lx, o.arms.ly, 2, 2);
    urect(ox, oy, s, o.arms.rx, o.arms.ry, 2, 2);

    // Eyes are cut out in black from the white shell.
    const int lx = o.look_x, ly = o.look_y;
    switch (o.eyes) {
        case Eyes::Closed:
            urect(ox, oy, s, 4, 9, 2, 1, kBlack);
            urect(ox, oy, s, 9, 9, 2, 1, kBlack);
            break;
        case Eyes::Sad:
            urect(ox, oy, s, 4, 8, 1, 1, kBlack);
            urect(ox, oy, s, 5, 9, 1, 1, kBlack);
            urect(ox, oy, s, 9, 9, 1, 1, kBlack);
            urect(ox, oy, s, 10, 8, 1, 1, kBlack);
            break;
        case Eyes::Squint:
            urect(ox, oy, s, 4 + lx, 9 + ly, 1, 1, kBlack);
            urect(ox, oy, s, 10 + lx, 9 + ly, 1, 1, kBlack);
            break;
        case Eyes::X:
            for (int cx : {4, 10}) {
                static const int pts[][2] = {{-1, -1}, {1, -1}, {0, 0}, {-1, 1}, {1, 1}};
                for (const auto &p : pts) urect(ox, oy, s, cx + p[0], 8 + p[1], 1, 1, kBlack);
            }
            break;
        case Eyes::Wide:
            urect(ox, oy, s, 4 + lx, 8 + ly, 2, 2, kBlack);
            urect(ox, oy, s, 9 + lx, 8 + ly, 2, 2, kBlack);
            break;
        case Eyes::Curve:
            for (int cx : {4, 9}) {
                urect(ox, oy, s, cx, 9, 1, 1, kBlack);
                urect(ox, oy, s, cx + 1, 8, 1, 1, kBlack);
                urect(ox, oy, s, cx + 2, 9, 1, 1, kBlack);
            }
            break;
        case Eyes::Heart:
            for (int cx : {4, 9}) {
                static const int pts[][2] = {{0, 8}, {2, 8}, {0, 9}, {1, 9}, {2, 9}, {1, 10}};
                for (const auto &p : pts) urect(ox, oy, s, cx + p[0], p[1], 1, 1, kBlack);
            }
            break;
        case Eyes::Star:
            for (int cx : {4, 9}) {
                static const int pts[][2] = {{1, 7}, {0, 8}, {1, 8}, {2, 8}, {0, 9}, {2, 9}};
                for (const auto &p : pts) urect(ox, oy, s, cx + p[0], p[1], 1, 1, kBlack);
            }
            break;
        case Eyes::Droopy:
            for (int cx : {4, 9}) urect(ox, oy, s, cx, 9, 2, 1, kBlack);
            break;
        case Eyes::WinkL:
        case Eyes::WinkR: {
            const int closed = o.eyes == Eyes::WinkL ? 4 : 9;
            const int open = o.eyes == Eyes::WinkL ? 9 : 4;
            urect(ox, oy, s, closed, 9, 2, 1, kBlack);
            urect(ox, oy, s, open + 1, 8 + ly, 1, 2, kBlack);
            break;
        }
        case Eyes::OuchL:
        case Eyes::OuchR: {
            const int hurt = o.eyes == Eyes::OuchL ? 4 : 10;
            const int open = o.eyes == Eyes::OuchL ? 10 : 4;
            static const int cross[][2] = {
                {-1, -1}, {1, -1}, {0, 0}, {-1, 1}, {1, 1},
            };
            for (const auto &p : cross) {
                urect(ox, oy, s, hurt + p[0], 8 + p[1], 1, 1, kBlack);
            }
            urect(ox, oy, s, open, 8, 2, 2, kBlack);
            break;
        }
        case Eyes::Normal:
        default:
            urect(ox, oy, s, 4 + lx, 8 + ly, 1, 2, kBlack);
            urect(ox, oy, s, 10 + lx, 8 + ly, 1, 2, kBlack);
            break;
    }

    switch (o.mouth) {
        case Mouth::Open:
            urect(ox, oy, s, 6, 10, 3, 2, kBlack);
            break;
        case Mouth::Smile:
            urect(ox, oy, s, 6, 11, 3, 1, kBlack);
            urect(ox, oy, s, 5, 10, 1, 1, kBlack);
            urect(ox, oy, s, 9, 10, 1, 1, kBlack);
            break;
        case Mouth::Yawn:
            urect(ox, oy, s, 6, 10, 3, 3, kBlack);
            urect(ox, oy, s, 7, 10, 1, 1, kWhite);
            break;
        case Mouth::Pout:
            urect(ox, oy, s, 6, 11, 3, 1, kBlack);
            urect(ox, oy, s, 5, 12, 1, 1, kBlack);
            urect(ox, oy, s, 9, 12, 1, 1, kBlack);
            break;
        case Mouth::Smirk:
            urect(ox, oy, s, 6, 11, 3, 1, kBlack);
            urect(ox, oy, s, 9, 10, 1, 1, kBlack);
            break;
        case Mouth::None:
        default:
            break;
    }
}

void sleeping_crab(float breathe) {
    const float ox = kCrabOx, oy = kCrabOy + breathe, s = kCrabScale;
    for (int x : {3, 5, 9, 11}) urect(ox, oy, s, x, 9, 1, 1);
    urect(ox, oy, s, 1, 10, 13, 5);
    urect(ox, oy, s, -1, 13, 2, 2);
    urect(ox, oy, s, 14, 13, 2, 2);
    urect(ox, oy, s, 4, 12, 2, 1, kBlack);
    urect(ox, oy, s, 9, 12, 2, 1, kBlack);
}

void debugger_scene(uint32_t frame) {
    const int f = frame % 32;
    const int crouch = 3 + (((f / 4) % 2) ? 1 : 0);
    CrabOpts o;
    o.ox = 32;
    o.body_dy = crouch;
    o.eyes = Eyes::Squint;
    o.look_x = 1;
    o.arms = kArmDebug;
    o.leg_phase = ((f / 4) % 2) ? 1 : 2;
    crab(o);
    const float cx = 88 + std::sin(f / 32.0f * kTau) * 10;
    const float cy = 40;
    pellipse_outline(cx, cy, 9, 3);
    pline(cx + 7, cy + 7, cx + 15, cy + 15, 3);
    pline(cx - 6, cy, cx + 6, cy, 1);
}

// ------------------------------------------------------------ mini bars ----
// The Pi taps real audio for the music spectrum. The board does not see the
// decoded stream (it arrives as paced PCM it only plays), so this is the
// procedural fallback oled_display.py already falls back to when levels are
// stale — same shape, same motion.
float g_bars[12] = {};

void mini_bars(float t) {
    const int n = static_cast<int>(sizeof(g_bars) / sizeof(g_bars[0]));
    const int bw = 4, gap = 1;
    const int total = n * bw + (n - 1) * gap;
    const int x0 = (kLogicalW - total) / 2;
    const int base_y = kLogicalH - 1;
    const float beat = 0.5f + 0.5f * std::sin(t * 4.0f);
    for (int i = 0; i < n; ++i) {
        const float tilt = 1.0f - 0.5f * (static_cast<float>(i) / n);
        float target = (0.5f + 0.5f * std::sin(t * 7 + i * 0.7f) +
                        0.4f * std::sin(t * 11 + i * 1.3f)) * tilt;
        target = std::clamp(target * (0.6f + 0.6f * beat), 0.0f, 1.0f);
        float cur = g_bars[i];
        cur += (target - cur) * (target > cur ? 0.6f : 0.25f);
        g_bars[i] = std::clamp(cur, 0.0f, 1.0f);
        const int h = static_cast<int>(1 + g_bars[i] * 11);
        prect(x0 + i * (bw + gap), base_y - h, bw, h + 1);
    }
}

// --------------------------------------------------------- state drawing ---

void draw_boot(float t, uint32_t frame) {
    const float p = std::min(1.0f, t / 2.2f);
    CrabOpts o;
    if (p < 1.0f) {
        o.ox = static_cast<int>(-60 + (kCrabOx + 60) * p);
        o.body_dy = (frame % 8 >= 2 && frame % 8 <= 4) ? -2 : 0;
        const int phase = ((frame / 4) % 2 == 0) ? 1 : 2;
        o.leg_phase = phase;
        o.arms = phase == 1 ? kArmWaveLeft : kArmWaveRight;
    } else {
        o.body_dy = (frame % 12 >= 5 && frame % 12 <= 7) ? 1 : 0;
        o.eyes = (frame % 20 < 2) ? Eyes::Closed : Eyes::Normal;
    }
    crab(o);
}

void draw_idle(float, uint32_t frame) {
    const int f = frame % 96;
    CrabOpts o;
    o.body_dy = (f % 19 >= 8 && f % 19 <= 12) ? 1 : 0;
    o.look_x = (f >= 11 && f <= 21) ? 1 : ((f >= 40 && f <= 49) ? -1 : 0);
    bool blink = f == 4 || f == 5 || f == 19 || f == 20 || f == 44 || f == 45 ||
                 f == 84 || f == 85;
    if (f >= 29 && f <= 35) {
        o.arms = kArmHeadScratch;
    } else if (f >= 59 && f <= 72) {  // a happy little stretch
        o.arms = f < 67 ? kArmHigh : kArmUp;
        blink = true;
        o.mouth = Mouth::Open;
        o.body_dy = f < 67 ? -2 : 1;
    }
    o.eyes = blink ? Eyes::Closed : Eyes::Normal;
    crab(o);
}

void draw_wake(float, uint32_t frame) {
    const int f = frame % 40;
    CrabOpts o;
    if (f < 10) {
        pexclaim(91, 7, 2);
        o.body_dx = -4;
        o.look_x = 1;
        crab(o);
        return;
    }
    const int jp = (f - 10) % 5;
    const int jump = (jp == 1 || jp == 2) ? -8 : 1;
    o.body_dy = jump;
    o.arms = jump < 0 ? kArmUp : kArmHigh;
    crab(o);
}

void draw_listening(float, uint32_t frame) {
    const int f = frame % 24;
    static const int looks[] = {-1, 0, 1, 0};
    CrabOpts o;
    o.eyes = (f == 10 || f == 11) ? Eyes::Closed : Eyes::Wide;
    o.look_x = looks[(f / 6) % 4];
    o.look_y = -1;
    o.arms = kArmUp;
    crab(o);
    const int phase = (frame / 2) % 3;
    for (int i = 0; i < 3; ++i) {
        if ((2 - i) != phase) continue;
        const float r = 6 + i * 7;
        parc(26, 24, r, 300, 60, 1, kGlow);   // left
        parc(102, 24, r, 120, 240, 1, kGlow); // right
    }
}

void draw_thinking(float, uint32_t frame) {
    const int f = frame % 32;
    CrabOpts o;
    o.body_dx = f < 8 ? -2 : (f >= 16 && f < 24 ? 2 : 0);
    o.eyes = (f == 15 || f == 16) ? Eyes::Closed : Eyes::Normal;
    o.look_x = 1;
    o.look_y = -1;
    o.arms = kArmChin;
    crab(o);
    prect(77, 3, 34, 15);
    prect(74, 6, 40, 9);
    prect(82, 18, 5, 4);
    prect(78, 22, 3, 3);
    const int shown = 1 + (f / 5) % 4;
    for (int i = 0; i < std::min(shown, 3); ++i) prect(84 + i * 8, 9, 3, 3, kBlack);
}

void draw_speaking(float, uint32_t frame) {
    const int f = frame % 16;
    static const int looks[] = {0, 1, 0, -1};
    CrabOpts o;
    o.look_x = looks[(f / 4) % 4];
    o.mouth = (f % 4) < 2 ? Mouth::Open : Mouth::None;
    crab(o);
    const int phase = (frame / 2) % 3;
    for (int i = 0; i < 3; ++i) {
        if (i != phase) continue;
        parc(90, 40, 6 + i * 7, 300, 60, 1, kGlow);
    }
}

void draw_music(float t, uint32_t frame) {
    const int f = frame % 32;
    CrabOpts o;
    o.oy = -12;
    o.body_dx = f < 16 ? -2 : 2;
    o.body_dy = (f % 8 >= 2 && f % 8 <= 4) ? -3 : 0;
    o.eyes = Eyes::Closed;
    o.arms = ((f / 4) % 2 == 0) ? kArmConductA : kArmConductB;
    o.mouth = (f % 8 < 4) ? Mouth::Open : Mouth::None;
    crab(o);
    mini_bars(t);
}

void draw_tool(float, uint32_t frame) {
    if (std::strstr(g_detail, "search")) {
        debugger_scene(frame);
        return;
    }
    const int f = frame % 16;
    static const int looks[] = {-1, 0, 1, 0};
    CrabOpts o;
    o.body_dy = (f % 2) ? 1 : 0;
    o.eyes = Eyes::Squint;
    o.look_x = looks[(f / 4) % 4];
    o.arms = (f % 2) ? kArmTypingA : kArmTypingB;
    crab(o);
    // Laptop in front of the crab.
    prect(47, 43, 34, 14);
    prect(44, 57, 40, 3);
    prect(61, 48, 4, 4, kBlack);
    static const int sparks[][2] = {{38, 31}, {48, 24}, {76, 27}, {88, 34}};
    for (int i = 0; i < 4; ++i) {
        const int yy = sparks[i][1] - static_cast<int>((frame * 2 + i * 7) % 15);
        if (yy > 5 && yy < 39) prect(sparks[i][0], yy, 2, 2);
    }
}

void draw_idle_mind(float, uint32_t frame) {
    const int f = frame % 32;
    const int bob = (f >= 8 && f < 16) ? -3 : 0;
    CrabOpts o;
    o.ox = 40;
    o.body_dy = bob;
    o.eyes = Eyes::Closed;
    o.arms = kArmWaveRight;
    crab(o);
    prect(50, 22 + bob, 30, 3);
    prect(56, 16 + bob, 18, 6);
    prect(61, 10 + bob, 10, 6);
    prect(68, 7 + bob, 5, 4);
    prect(61, 15 + bob, 3, 3, kBlack);
    pline(83, 42 + bob, 102, 22 + bob, 2);
    for (int i = 0; i < 4; ++i) {
        const float a = f / 32.0f * kTau + i * kTau / 4;
        pstar(101 + std::cos(a) * 16, 21 + std::sin(a) * 12, 1);
    }
}

void draw_summarizing(float, uint32_t frame) {
    const int f = frame % 32;
    CrabOpts o;
    o.ox = 29;
    o.body_dx = f / 8;
    o.arms = kArmSweep;
    o.eyes = Eyes::Squint;
    crab(o);
    const int sweep = f % 8;
    const int broom_x = 67 + sweep * 3;
    pline(61, 40, broom_x, 56, 3);
    prect(broom_x - 2, 54, 24, 4);
    for (int i = 0; i < 4; ++i) {
        prect(90 + ((frame * 4 + i * 9) % 34), 50 - ((frame + i * 3) % 7), 2, 2);
    }
}

void draw_warming(float, uint32_t frame) {
    const int f = frame % 32;
    CrabOpts o;
    if (f < 8) o.arms = kArmUp;
    else if (f < 16) o.arms = kArmHigh;
    else if (f < 24) o.arms = kArmUp;
    else o.arms = kArmNormal;
    o.body_dy = (f >= 4 && f < 20) ? -1 : 0;
    o.eyes = Eyes::Squint;
    o.mouth = (f >= 8 && f < 16) ? Mouth::Open : Mouth::None;
    crab(o);
}

void draw_vision(float, uint32_t frame) {
    const int f = frame % 32;
    crab(CrabOpts{});
    pline(64, 31, 64, 18, 2);
    prect(61, 14, 7, 5);
    const int phase = (f / 4) % 4;
    for (int r = 1; r <= phase; ++r) {
        const float radius = 5 + r * 6;
        parc(64, 16 + 4, radius, 195, 345);
    }
}

void draw_workers(float, uint32_t frame) {
    const int f = frame % 32;
    static const int looks[] = {-1, 0, 1, 0};
    CrabOpts o;
    o.body_dx = f < 16 ? -2 : 2;
    o.look_x = looks[(f / 4) % 4];
    o.arms = kArmUp;
    crab(o);
    for (int i = 0; i < 3; ++i) {
        const float a = (f / 32.0f) * kTau + i * kTau / 3;
        const float x = 64 + std::cos(a) * 28;
        const float y = 22 - std::fabs(std::sin(a)) * 15;
        prect(x - 2, y - 2, 5, 5);
    }
}

void draw_goodbye(float, uint32_t frame) {
    const int f = std::min<int>(frame, 27);
    CrabOpts o;
    o.body_dy = static_cast<int>((f / 27.0f) * 42);
    o.eyes = Eyes::Closed;
    crab(o);
    prect(0, 59, kLogicalW, kLogicalH - 59, kBlack);
    for (int i = 0; i < 6; ++i) {
        prect(36 + ((i * 13 + f * 4) % 57), 56 - ((f + i * 2) % 8), 2, 2);
    }
}

void draw_disconnected(float, uint32_t frame) {
    const int f = frame % 24;
    CrabOpts o;
    o.body_dx = (f >= 5 && f <= 12) ? 1 : 0;
    o.look_x = (f < 8 || f >= 18) ? 1 : -1;
    o.look_y = -1;
    crab(o);
    pexclaim(27, 30, 1);
    pbt(88, 8, 2);
    pline(88, 27, 104, 8, 2);
}

void draw_confused(float, uint32_t frame) {
    const int f = frame % 64;
    int dx;
    if (f >= 8 && f < 24) dx = -std::min(5, (f - 8) / 2);
    else if (f >= 24 && f < 32) dx = -std::max(0, 5 - (f - 24));
    else if (f >= 36 && f < 52) dx = std::min(5, (f - 36) / 2);
    else if (f >= 52 && f < 60) dx = std::max(0, 5 - (f - 52));
    else dx = 0;
    CrabOpts o;
    o.body_dx = dx;
    o.body_dy = dx ? ((f / 3) % 2) : 0;
    o.look_x = dx < 0 ? -1 : (dx > 0 ? 1 : 0);
    o.arms = kArmHeadScratch;
    o.leg_phase = dx ? (f / 4) % 3 : 0;
    crab(o);
    if (f >= 7 && f < 30) pquestion(17 + ((f - 7) / 8), 12 - ((f - 7) % 8) / 2, 2);
    if (f >= 34 && f < 59) pquestion(98 - ((f - 34) / 8), 12 - ((f - 34) % 8) / 2, 2);
}

void draw_dizzy(float, uint32_t frame) {
    const int f = frame % 48;
    static const int dys[] = {0, -1, -2, -1, 0, 1};
    const int dx = static_cast<int>(std::sin(f / 48.0f * kTau * 2) * 5);
    CrabOpts o;
    o.body_dx = dx;
    o.body_dy = dys[(f / 2) % 6];
    o.eyes = Eyes::X;
    o.arms = ((f / 8) % 2) ? kArmUp : kArmNormal;
    o.leg_phase = dx < 0 ? 1 : 2;
    crab(o);
    for (int i = 0; i < 3; ++i) {
        const float a = f / 48.0f * kTau * 1.5f + i * kTau / 3;
        const float radius = 23 + ((f + i * 5) % 10);
        pstar(64 + std::cos(a) * radius, 17 + std::sin(a) * (5 + i),
              ((f + i * 4) % 16 < 4) ? 2 : 1);
    }
}

void draw_happy(float, uint32_t frame) {
    static const int arc[] = {0, -1, -3, -6, -9, -11, -10, -8,
                              -5, -2, 0, 1, 2, 1, 0, 0};
    const int f = frame % 48;
    const int jump = arc[f % 16];
    CrabOpts o;
    o.body_dy = jump;
    o.eyes = Eyes::Curve;
    o.arms = jump < -2 ? kArmReach : kArmHigh;
    o.mouth = Mouth::Open;
    o.leg_phase = ((f / 2) % 2) ? 1 : 2;
    crab(o);
    const float radius = 4 + (f % 16) * 2;
    for (int ang : {210, 245, 295, 330}) {
        const float rad = ang * (kTau / 360.0f);
        pstar(64 + std::cos(rad) * radius, 31 + std::sin(rad) * radius,
              (f % 16 < 4) ? 2 : 1);
    }
}

void draw_sad(float, uint32_t frame) {
    const int f = frame % 56;
    int sink;
    if (f < 22) sink = std::min(4, f / 5);
    else if (f < 42) sink = 4;
    else sink = std::max(2, 4 - (f - 42) / 5);
    CrabOpts o;
    o.body_dx = (f == 30 || f == 31 || f == 36 || f == 37) ? -1 : 0;
    o.body_dy = sink;
    o.eyes = Eyes::Sad;
    o.mouth = Mouth::Pout;
    crab(o);
    const int tear_phase = f % 18;
    if (tear_phase < 14) prect(96 + (tear_phase / 5), 31 + tear_phase * 2, 2, 3);
    if (f >= 28 && f < 45) {
        const int lt = (f - 28) % 14;
        prect(29 - (lt / 6), 31 + lt * 2, 2, 3);
    }
}

void draw_sleeping(float, uint32_t frame) {
    static const int breathes[] = {0, 0, -1, -2, -2, -1, 0, 1};
    const int f = frame % 64;
    sleeping_crab(breathes[(f / 4) % 8]);
    for (int i = 0; i < 3; ++i) {
        const int z = (f + i * 18) % 54;
        if (z < 36) pz(80 + z / 6 + i * 2, 29 - z / 2, 1, z < 14);
    }
}

void draw_love(float, uint32_t frame) {
    static const int sways[] = {-2, -1, 0, 1, 2, 1, 0, -1};
    static const int pulses[] = {0, -1, -2, -1};
    const int f = frame % 48;
    CrabOpts o;
    o.body_dx = sways[(f / 3) % 8];
    o.body_dy = pulses[(f / 2) % 4];
    o.eyes = Eyes::Heart;
    o.arms = kArmCheeks;
    o.mouth = Mouth::Smile;
    crab(o);
    static const int hearts[][2] = {{22, 0}, {98, 12}, {14, 24}, {108, 36}};
    for (int i = 0; i < 4; ++i) {
        const int p = (f + hearts[i][1]) % 48;
        pheart(hearts[i][0] + std::sin((p + i) / 5.0f) * 3, 51 - p, p < 10 ? 2 : 1);
    }
}

void draw_shy(float, uint32_t frame) {
    static const int sways[] = {-2, -2, -1, 0, 1, 2, 2, 1, 0, -1};
    static const int bobs[] = {0, -1, -2, -1};
    const int f = frame % 56;
    const int sway = sways[(f / 3) % 10];
    const bool peek = (f >= 9 && f < 19) || (f >= 34 && f < 47);
    CrabOpts o;
    o.body_dx = sway;
    o.body_dy = peek ? bobs[(f / 2) % 4] : ((f % 14 < 2) ? 1 : 0);
    o.eyes = peek ? ((f % 8 < 3) ? Eyes::Normal : Eyes::Curve) : Eyes::Closed;
    o.look_x = f < 28 ? -1 : 1;
    o.arms = kArmCover;
    o.leg_phase = sway < 0 ? 1 : (sway > 0 ? 2 : 0);
    crab(o);
    pblush(kCrabOx + sway, kCrabOy, kCrabScale, 2, 10);
    pblush(kCrabOx + sway, kCrabOy, kCrabScale, 9, 10);
}

void draw_giggle(float, uint32_t frame) {
    static const int jiggles[] = {0, 2, -1, 1, -2, 1};
    const int f = frame % 24;
    const bool active = f < 9 || (f >= 13 && f < 22);
    const int jiggle = active ? jiggles[f % 6] : 0;
    CrabOpts o;
    o.body_dx = jiggle;
    o.body_dy = (active && (f % 3)) ? -2 : 0;
    o.eyes = Eyes::Curve;
    o.arms = active ? kArmCheeks : kArmNormal;
    o.mouth = active ? Mouth::Open : Mouth::Smile;
    o.leg_phase = jiggle < 0 ? 1 : 2;
    crab(o);
    static const int puffs[][2] = {{26, 20}, {96, 16}, {20, 34}};
    for (int i = 0; i < 3; ++i) {
        if (active && (f + i * 3) % 9 < 6) pstar(puffs[i][0], puffs[i][1] - (f % 4), 1);
    }
}

void draw_wink(float, uint32_t frame) {
    const int f = frame % 40;
    const bool wink = f >= 7 && f < 27;
    const ArmPose wave = ((f >= 10 && f < 16) || (f >= 21 && f < 27)) ? kArmWaveRight : kArmUp;
    CrabOpts o;
    o.body_dx = wink ? 1 : 0;
    o.body_dy = (f >= 7 && f < 12) ? -2 : 0;
    o.eyes = wink ? Eyes::WinkR : Eyes::Normal;
    o.arms = wink ? wave : kArmNormal;
    o.mouth = wink ? Mouth::Smirk : Mouth::Smile;
    crab(o);
    if (f >= 9 && f < 25) pstar(96 + (f % 3), 16 - ((f - 9) / 5), f < 14 ? 2 : 1);
}

void draw_excited(float, uint32_t frame) {
    static const int arc[] = {0, -1, -3, -6, -9, -11, -10, -8,
                              -5, -2, 0, 2, 1, 0, -1, 0};
    const int f = frame % 32;
    const int jump = arc[f % 16];
    CrabOpts o;
    o.body_dy = jump;
    o.eyes = Eyes::Wide;
    o.arms = jump < -5 ? kArmReach : kArmHigh;
    o.mouth = Mouth::Open;
    o.leg_phase = ((f / 2) % 2) ? 1 : 2;
    crab(o);
    pexclaim(26, 14, 2);
    pexclaim(98, 14, 2);
    const float radius = 5 + (f % 16) * 2;
    for (int ang : {200, 250, 290, 340}) {
        const float rad = ang * (kTau / 360.0f);
        pstar(64 + std::cos(rad) * radius, 30 + std::sin(rad) * radius,
              (f % 16 < 4) ? 2 : 1);
    }
}

void draw_curious(float, uint32_t frame) {
    static const int leans[] = {0, 1, 2, 3, 4, 4, 3, 2, 1, 0, 0, 0};
    const int f = frame % 48;
    CrabOpts o;
    o.body_dx = leans[f / 4];
    o.body_dy = (f >= 16 && f < 32) ? -1 : 0;
    o.eyes = Eyes::Wide;
    o.look_x = f < 12 ? -1 : ((f >= 28 && f < 40) ? 1 : 0);
    o.look_y = -1;
    o.arms = ((f / 8) % 2) ? kArmUp : kArmChin;
    crab(o);
    pquestion(22 + (f / 12), 14 - (f % 12) / 3, (f % 24 < 5) ? 2 : 1);
}

void draw_proud(float, uint32_t frame) {
    static const int puffs[] = {0, -1, -2, -3, -3, -2, -1, 0};
    const int f = frame % 48;
    CrabOpts o;
    o.body_dx = (f >= 24 && f < 36) ? 1 : 0;
    o.body_dy = puffs[(f / 3) % 8];
    o.eyes = Eyes::Curve;
    o.arms = (f % 16 < 12) ? kArmHips : kArmHigh;
    o.mouth = Mouth::Smile;
    o.leg_phase = (f >= 24 && f < 30) ? 1 : ((f >= 30 && f < 36) ? 2 : 0);
    crab(o);
    if (f >= 8 && f < 38) {
        const int p = f - 8;
        pstar(96 + p / 10, 25 - p / 3, p < 8 ? 2 : 1);
    }
}

void draw_sulk(float, uint32_t frame) {
    const int f = frame % 56;
    const int dx = f < 14 ? -std::min(4, f / 3)
                          : (f < 45 ? -4 : -std::max(0, 4 - (f - 45) / 2));
    const int tap = (f / 3) % 2;
    CrabOpts o;
    o.body_dx = dx;
    o.body_dy = tap;
    o.eyes = Eyes::Closed;
    o.mouth = Mouth::Pout;
    o.leg_phase = tap ? 1 : 2;
    crab(o);
    for (int phase : {0, 24}) {
        const int p = ((f - phase) % 56 + 56) % 56;
        if (p < 16) ppuff(94 + p, 29 - p, 1);
    }
}

void draw_surprised(float, uint32_t frame) {
    static const int jolt_seq[] = {-9, -9, -7, -5, -3, -1, 1, 0};
    static const int after[] = {-5, -3, -1, 0};
    static const int shakes[] = {-1, 1, 0, 1, -1, 0};
    static const int bobs[] = {-2, -1, 0, 1};
    const int f = frame % 36;
    const int jolt = f < 8 ? jolt_seq[f] : ((f >= 20 && f < 24) ? after[f - 20] : 0);
    CrabOpts o;
    o.body_dx = (f >= 8 && f < 32) ? shakes[f % 6] : 0;
    o.body_dy = jolt;
    o.eyes = Eyes::Wide;
    o.arms = f < 5 ? kArmReach : kArmHigh;
    o.mouth = Mouth::Open;
    o.leg_phase = (f % 4 < 2) ? 1 : 2;
    crab(o);
    const int bob = bobs[(f / 2) % 4];
    pexclaim(24, 12 + bob, 2);
    pexclaim(100, 12 - bob, 2);
    if (f < 7 || (f >= 20 && f < 24)) {
        for (int x : {30, 94}) pline(x, 40, x + (x < 64 ? 6 : -6), 46, 2);
    }
}

void draw_sleepy(float, uint32_t frame) {
    static const int nods[] = {0, 1, 2, 3, 3, 2, 1, 0};
    const int f = frame % 64;
    const bool yawning = f >= 16 && f < 37;
    CrabOpts o;
    o.body_dx = (f >= 42 && f < 50) ? -1 : 0;
    o.body_dy = nods[(f / 4) % 8] + (yawning ? 1 : 0);
    o.eyes = yawning ? Eyes::Closed : Eyes::Droopy;
    o.arms = yawning ? kArmUp : kArmNormal;
    o.mouth = yawning ? Mouth::Yawn : Mouth::None;
    crab(o);
    for (int phase : {0, 30}) {
        const int z = ((f - phase) % 64 + 64) % 64;
        if (z < 24) pz(92 + z / 5, 28 - z, 1, z < 10);
    }
}

void draw_idea(float, uint32_t frame) {
    static const int bobs[] = {0, -1, -2, -1};
    const int f = frame % 48;
    const bool on = f >= 12 && f < 43;
    const bool pop = f >= 12 && f < 19;
    CrabOpts o;
    o.body_dy = pop ? -4 : (on ? -1 : 0);
    o.eyes = on ? Eyes::Wide : Eyes::Squint;
    o.arms = pop ? kArmHigh : (on ? kArmUp : kArmChin);
    o.mouth = on ? Mouth::Smile : Mouth::None;
    o.leg_phase = pop ? (f / 3) % 3 : 0;
    crab(o);
    const int bulb_scale = (!on || f % 12 < 9) ? 2 : 1;
    const int bulb_bob = on ? bobs[f % 4] : 0;
    pbulb(22 + (2 - bulb_scale) * 2, 14 + (2 - bulb_scale) * 3 + bulb_bob, bulb_scale);
    if (on) {
        const float ray_len = 13 + ((f - 12) % 12);
        for (int ang : {200, 235, 270, 305, 340}) {
            const float rad = ang * (kTau / 360.0f);
            pline(30 + std::cos(rad) * 12, 20 + std::sin(rad) * 12,
                  30 + std::cos(rad) * ray_len, 20 + std::sin(rad) * ray_len, 2);
        }
    }
}

void draw_mischief(float, uint32_t frame) {
    const int f = frame % 48;
    const int step = f < 24 ? f : 47 - f;
    CrabOpts o;
    o.body_dx = -5 + step / 2;
    o.body_dy = ((f / 2) % 2) ? -2 : 0;
    o.eyes = Eyes::Squint;
    o.look_x = 1;
    o.arms = kArmChin;
    o.mouth = Mouth::Smirk;
    o.leg_phase = ((f / 3) % 2) ? 1 : 2;
    crab(o);
    if (f % 16 < 10) pstar(98 + (f % 3), 20 - (f % 10) / 2, (f % 16 < 3) ? 2 : 1);
}

void draw_scared(float, uint32_t frame) {
    static const int trembles[] = {-2, 1, -1, 2, -2, 2, -1, 1};
    static const int crouches[] = {0, 1, 2, 3, 3, 2, 1, 0};
    const int f = frame % 32;
    const int tremble = trembles[f % 8];
    CrabOpts o;
    o.body_dx = tremble;
    o.body_dy = crouches[(f / 2) % 8];
    o.eyes = Eyes::Wide;
    o.arms = kArmCover;
    o.leg_phase = tremble < 0 ? 1 : 2;
    crab(o);
    psweat(92 + (f / 8), 13 + (f % 16) * 2, (f % 16 < 8) ? 2 : 1);
    pexclaim(26, 16, 1);
}

void draw_awe(float, uint32_t frame) {
    static const int floats[] = {0, -1, -2, -3, -2, -1, 0};
    const int f = frame % 56;
    CrabOpts o;
    o.body_dy = floats[(f / 4) % 7];
    o.eyes = (f % 28 < 23) ? Eyes::Star : Eyes::Wide;
    o.arms = (f % 14 < 10) ? kArmCheeks : kArmReach;
    o.mouth = Mouth::Open;
    crab(o);
    for (int i = 0; i < 4; ++i) {
        const float a = f / 56.0f * kTau * 1.5f + i * (kTau / 4);
        pstar(64 + std::cos(a) * (36 + i * 2), 25 + std::sin(a) * (9 + i),
              ((f + i * 5) % 18 < 4) ? 2 : 1);
    }
}

// ----------------------------------------------------- motion reactions ---
// Board-local physical personality. These names are intentionally absent from
// the model's expression vocabulary: the IMU, not the LLM, owns them.

void draw_motion_bored(float, uint32_t frame) {
    const int f = frame % 72;
    CrabOpts o;
    o.body_dy = 3 + ((f >= 42 && f < 54) ? 1 : 0);
    o.eyes = (f >= 24 && f < 38) ? Eyes::Closed : Eyes::Droopy;
    o.look_x = f < 18 ? -1 : (f < 42 ? 1 : 0);
    o.arms = f < 48 ? kArmChin : kArmNormal;
    o.mouth = (f >= 48 && f < 62) ? Mouth::Yawn : Mouth::Pout;
    o.leg_phase = (f >= 14 && f < 22) ? ((f / 2) % 2 + 1) : 0;
    crab(o);
    for (int i = 0; i < 3; ++i) {
        const int pulse = (f + i * 12) % 72;
        if (pulse < 42) prect(92 + i * 7, 22 - pulse / 5, 3, 3, kGlow);
    }
}

void draw_motion_alert(float, uint32_t frame) {
    const int f = frame % 36;
    CrabOpts o;
    o.body_dy = f < 7 ? -7 + f : ((f % 12 < 3) ? -1 : 0);
    o.eyes = Eyes::Wide;
    o.look_x = f < 14 ? -1 : (f < 27 ? 1 : 0);
    o.look_y = -1;
    o.arms = f < 9 ? kArmReach : kArmUp;
    o.mouth = f < 8 ? Mouth::Open : Mouth::None;
    crab(o);
    const int phase = (f / 3) % 4;
    for (int i = 0; i <= phase; ++i) {
        parc(64, 20, 12 + i * 7, 200, 340, 1, kGlow);
    }
}

void draw_motion_annoyed(float, uint32_t frame) {
    static const int shakes[] = {-3, 2, -2, 3, -1, 1};
    const int f = frame % 42;
    CrabOpts o;
    o.body_dx = f < 24 ? shakes[f % 6] : -2;
    o.body_dy = (f / 3) % 2;
    o.eyes = f < 24 ? Eyes::Squint : Eyes::Closed;
    o.arms = f < 24 ? kArmHigh : kArmHips;
    o.mouth = Mouth::Pout;
    o.leg_phase = f < 24 ? ((f / 2) % 2 + 1) : 0;
    crab(o);
    for (int phase : {0, 17}) {
        const int puff = ((f - phase) % 42 + 42) % 42;
        if (puff < 13) ppuff(94 + puff, 29 - puff, 1);
    }
}

void draw_motion_rocking(float, uint32_t frame) {
    static const int sways[] = {-5, -4, -2, 0, 2, 4, 5, 4, 2, 0, -2, -4};
    const int f = frame % 48;
    CrabOpts o;
    o.body_dx = sways[f / 4];
    o.body_dy = (f / 6) % 2;
    o.eyes = Eyes::Curve;
    o.arms = kArmCheeks;
    o.mouth = Mouth::Smile;
    crab(o);
    if (f % 24 < 15) pheart(f < 24 ? 22 : 96, 14 - (f % 12) / 2, 1);
}

void draw_motion_carried(float, uint32_t frame) {
    const int f = frame % 32;
    CrabOpts o;
    o.body_dy = (f % 8 < 4) ? -3 : 0;
    o.body_dx = (f < 16) ? -2 : 2;
    o.eyes = Eyes::Wide;
    o.look_x = f < 10 ? -1 : (f < 22 ? 1 : 0);
    o.arms = (f / 8) % 2 ? kArmWaveLeft : kArmWaveRight;
    o.leg_phase = (f / 3) % 2 + 1;
    crab(o);
    for (int i = 0; i < 3; ++i) {
        const int x = 18 + ((f * 5 + i * 37) % 96);
        prect(x, 55 - i * 3, 5, 2, kGlow);
    }
}

void draw_motion_upside_down(float, uint32_t frame) {
    const int f = frame % 36;
    const int sway = (f < 18 ? f : 35 - f) / 4 - 2;
    // A deliberately literal upside-down crab: legs above the shell, eyes and
    // claws hanging toward the bottom of the screen.
    prect(40 + sway, 18, 48, 28);
    for (int x : {47, 56, 72, 81}) prect(x + sway, 10, 4, 10);
    prect(30 + sway, 38, 10, 8);
    prect(88 + sway, 38, 10, 8);
    prect(50 + sway, 35, 7, 7, kBlack);
    prect(72 + sway, 35, 7, 7, kBlack);
    prect(61 + sway, 24, 8, 3, kBlack);
    pquestion(99, 10 + (f % 5), 1);
}

void draw_motion_brace(float, uint32_t frame) {
    const int f = frame % 18;
    CrabOpts o;
    o.body_dy = 5 + (f % 2);
    o.body_dx = (f % 4 < 2) ? -1 : 1;
    o.eyes = Eyes::Wide;
    o.arms = kArmCover;
    o.mouth = Mouth::Open;
    o.leg_phase = (f / 2) % 2 + 1;
    crab(o);
    pexclaim(23, 12, 2);
    pexclaim(101, 12, 2);
}

void draw_motion_face_down(float, uint32_t frame) {
    const int f = frame % 44;
    prect(32, 39 + (f % 2), 64, 16);
    prect(25, 44, 9, 8);
    prect(95, 44, 9, 8);
    for (int x : {41, 53, 72, 84}) prect(x, 32, 5, 9);
    prect(48, 47, 10, 2, kBlack);
    prect(72, 47, 10, 2, kBlack);
    if (f < 14) ppuff(102 + f, 37 - f, 1);
}

void draw_motion_settled(float, uint32_t frame) {
    static const int bounce[] = {-6, -4, -2, 1, 0, 0, 0, 0};
    const int f = std::min<int>(frame, 31);
    CrabOpts o;
    o.body_dy = bounce[std::min(7, f / 3)];
    o.eyes = f < 18 ? Eyes::Curve : Eyes::Normal;
    o.arms = f < 15 ? kArmHigh : kArmNormal;
    o.mouth = Mouth::Smile;
    crab(o);
    if (f < 18) {
        pellipse_outline(64, 55, 7 + f, 1, kGlow);
    }
}

// ------------------------------------------------------ touch reactions ---
// Local, one-shot physical comedy. These never enter the model vocabulary;
// kiki_ui restores the current system face after their short timer expires.

void draw_tap_ouch(bool left, uint32_t frame) {
    const int f = frame % 24;
    CrabOpts o;
    o.body_dx = (left ? 1 : -1) * (4 - std::min(4, f / 3));
    o.body_dy = (f < 6) ? -3 : 1;
    o.eyes = left ? Eyes::OuchL : Eyes::OuchR;
    o.arms = kArmCover;
    o.mouth = Mouth::Open;
    crab(o);
    const float tear_x = left ? 49 : 78;
    const int drop = std::min(24, static_cast<int>(f * 1.5f));
    prect(tear_x, 34 + drop, 3, 5, kGlow);
    if (f < 9) pexclaim(left ? 19 : 105, 9, 2);
}

void draw_tap_ouch_left(float, uint32_t frame) { draw_tap_ouch(true, frame); }
void draw_tap_ouch_right(float, uint32_t frame) { draw_tap_ouch(false, frame); }

void draw_tap_boop(float, uint32_t frame) {
    const int f = frame % 32;
    const int squash = f < 8 ? (f / 2) : std::max(0, 5 - (f - 8) / 3);
    CrabOpts o;
    o.body_dy = squash;
    o.body_dx = ((f / 3) % 2) ? 1 : -1;
    o.eyes = f < 12 ? Eyes::Wide : Eyes::Squint;
    o.arms = kArmUp;
    o.mouth = f < 12 ? Mouth::Pout : Mouth::Smirk;
    crab(o);
    if (f < 18) {
        pellipse_outline(64, 37, 8 + f, 2, kGlow);
    } else {
        pquestion(98, 10, 1);
    }
}

void draw_tap_pat(float, uint32_t frame) {
    const int f = frame % 36;
    CrabOpts o;
    o.body_dy = (f % 12 < 5) ? 2 : 0;
    o.eyes = f < 25 ? Eyes::Curve : Eyes::Normal;
    o.arms = f < 20 ? kArmCheeks : kArmHips;
    o.mouth = Mouth::Smile;
    crab(o);
    // A hand-shaped three-pixel pat descends, then the pride sparkles arrive.
    if (f < 16) {
        const int y = 2 + std::min(12, f);
        prect(57, y, 14, 4, kGlow);
        prect(61, y + 4, 6, 4, kGlow);
    } else {
        pstar(27, 14 - (f % 4), 2);
        pstar(99, 20 - ((f + 2) % 5), 1);
    }
}

void draw_tap_pinch(float, uint32_t frame) {
    const int f = frame % 30;
    const int reach = std::min(6, f / 2);
    CrabOpts o;
    o.body_dx = -reach / 2;
    o.eyes = Eyes::Squint;
    o.look_x = -1;
    o.mouth = Mouth::Smirk;
    o.arms = ArmPose{-2 - reach, 7 + (f % 2), 13, 9};
    crab(o);
    const int claw_x = 17 - reach * 2;
    prect(claw_x, 28, 8, 4, kGlow);
    prect(claw_x, 38, 8, 4, kGlow);
    if (f == 12 || f == 20) pstar(12, 34, 1);
}

void draw_tap_blaster(float, uint32_t frame) {
    const int f = frame % 36;
    CrabOpts o;
    o.body_dx = -2;
    o.eyes = Eyes::Squint;
    o.look_x = 1;
    o.arms = ArmPose{0, 9, 13, 7};
    o.mouth = Mouth::Smirk;
    crab(o);
    // A deliberately toy-sized ray gun assembled out of crisp logical blocks.
    prect(96, 31, 18, 6, kGlow);
    prect(101, 37, 6, 8, kGlow);
    prect(113, 33, 8, 2, kGlow);
    if (f >= 7 && f < 25) {
        const int length = 4 + (f - 7) * 2;
        pline(121, 34, std::min(127, 121 + length), 34, 2, kGlow);
        pstar(119 + ((f - 7) % 5), 29, 1);
    }
}

void draw_tap_tumble(float, uint32_t frame) {
    const int f = frame % 42;
    if (f < 12) {
        CrabOpts o;
        o.body_dx = f * 2;
        o.body_dy = f / 2;
        o.eyes = f < 5 ? Eyes::Wide : Eyes::X;
        o.arms = kArmReach;
        o.leg_phase = (f / 2) % 3;
        crab(o);
        return;
    }
    // A sideways, flattened crab with dizzy eyes and legs in the air.
    const int bounce = (f < 20) ? (20 - f) / 2 : 0;
    prect(34, 43 - bounce, 62, 14);
    prect(27, 45 - bounce, 8, 8);
    prect(96, 45 - bounce, 8, 8);
    for (int x : {43, 55, 72, 84}) prect(x, 35 - bounce, 4, 10);
    for (int cx : {52, 78}) {
        pline(cx - 3, 46 - bounce, cx + 3, 52 - bounce, 2, kBlack);
        pline(cx + 3, 46 - bounce, cx - 3, 52 - bounce, 2, kBlack);
    }
    for (int i = 0; i < 3; ++i) {
        const float a = f / 42.0f * kTau + i * kTau / 3;
        pstar(65 + std::cos(a) * 31, 24 + std::sin(a) * 7, 1);
    }
}

// ------------------------------------------------------------- dispatch ----

struct StateEntry {
    const char *name;
    void (*draw)(float, uint32_t);
    uint8_t fps;
};

// _STATE_FPS: near-static states render slowest; bouncy/startled moods run a
// touch faster so the motion reads as lively rather than laggy.
const StateEntry kStates[] = {
    {"boot", draw_boot, 10},
    {"idle", draw_idle, 6},
    {"wake", draw_wake, 10},
    {"listening", draw_listening, 8},
    {"thinking", draw_thinking, 8},
    {"speaking", draw_speaking, 8},
    {"music", draw_music, 16},
    {"tool", draw_tool, 8},
    {"idle_mind", draw_idle_mind, 8},
    {"summarizing", draw_summarizing, 8},
    {"warming", draw_warming, 8},
    {"vision", draw_vision, 8},
    {"goodbye", draw_goodbye, 10},
    {"workers", draw_workers, 8},
    {"disconnected", draw_disconnected, 6},
    {"confused", draw_confused, 10},
    {"dizzy", draw_dizzy, 10},
    {"happy", draw_happy, 12},
    {"sad", draw_sad, 10},
    {"sleeping", draw_sleeping, 8},
    {"love", draw_love, 12},
    {"shy", draw_shy, 10},
    {"giggle", draw_giggle, 14},
    {"wink", draw_wink, 12},
    {"excited", draw_excited, 14},
    {"curious", draw_curious, 10},
    {"proud", draw_proud, 10},
    {"sulk", draw_sulk, 8},
    {"surprised", draw_surprised, 14},
    {"sleepy", draw_sleepy, 8},
    {"idea", draw_idea, 12},
    {"mischief", draw_mischief, 10},
    {"scared", draw_scared, 14},
    {"awe", draw_awe, 12},
    {"tap_ouch_left", draw_tap_ouch_left, 16},
    {"tap_ouch_right", draw_tap_ouch_right, 16},
    {"tap_boop", draw_tap_boop, 14},
    {"tap_pat", draw_tap_pat, 12},
    {"tap_pinch", draw_tap_pinch, 16},
    {"tap_blaster", draw_tap_blaster, 18},
    {"tap_tumble", draw_tap_tumble, 16},
    {"motion_bored", draw_motion_bored, 8},
    {"motion_alert", draw_motion_alert, 14},
    {"motion_annoyed", draw_motion_annoyed, 16},
    {"motion_rocking", draw_motion_rocking, 12},
    {"motion_carried", draw_motion_carried, 14},
    {"motion_upside_down", draw_motion_upside_down, 12},
    {"motion_brace", draw_motion_brace, 18},
    {"motion_face_down", draw_motion_face_down, 10},
    {"motion_settled", draw_motion_settled, 12},
};

const StateEntry &lookup(const char *name) {
    for (const auto &entry : kStates) {
        if (std::strcmp(entry.name, name) == 0) return entry;
    }
    return kStates[1];  // idle, matching the Python fallback
}

}  // namespace

void face_init(lv_obj_t *parent, int32_t x, int32_t y, int32_t w, int32_t h) {
    g_canvas_w = w;
    g_canvas_h = h;
    g_sx = static_cast<float>(w) / kLogicalW;
    g_sy = static_cast<float>(h) / kLogicalH;
    const size_t bytes = static_cast<size_t>(w) * h * sizeof(uint16_t);
    g_buffer = static_cast<uint16_t *>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM));
    if (!g_buffer) {
        g_buffer = static_cast<uint16_t *>(heap_caps_malloc(bytes, MALLOC_CAP_DEFAULT));
    }
    if (!g_buffer) {
        ESP_LOGE(kTag, "no memory for a %ldx%ld face canvas", (long)w, (long)h);
        return;
    }
    std::fill_n(g_buffer, static_cast<size_t>(w) * h, kBlack);
    g_canvas = lv_canvas_create(parent);
    lv_canvas_set_buffer(g_canvas, g_buffer, w, h, LV_COLOR_FORMAT_RGB565);
    lv_obj_set_pos(g_canvas, x, y);
    // The face covers most of the panel; if it swallowed touches, tap-to-wake
    // would only work in the margins.
    lv_obj_remove_flag(g_canvas, LV_OBJ_FLAG_CLICKABLE);
    g_state_started_us = esp_timer_get_time();
    ESP_LOGI(kTag, "face canvas %ldx%ld (%.2f px per logical pixel)", (long)w, (long)h,
             static_cast<double>(g_sx));
}

void face_set_state(const char *state, const char *detail) {
    if (!state || !*state) return;
    if (std::strcmp(state, g_state) != 0) {
        std::snprintf(g_state, sizeof(g_state), "%s", state);
        g_frame = 0;
        g_state_started_us = esp_timer_get_time();
    }
    std::snprintf(g_detail, sizeof(g_detail), "%s", detail ? detail : "");
}

const char *face_current_state() { return g_state; }

void face_set_visible(bool visible) {
    if (!g_canvas) return;
    if (visible) lv_obj_remove_flag(g_canvas, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(g_canvas, LV_OBJ_FLAG_HIDDEN);
}

uint32_t face_render_frame() {
    const StateEntry &entry = lookup(g_state);
    if (!g_buffer || !g_canvas) return 1000 / entry.fps;
    if (lv_obj_has_flag(g_canvas, LV_OBJ_FLAG_HIDDEN)) return 1000 / entry.fps;
    apply_palette(g_state);
    std::fill_n(g_buffer, static_cast<size_t>(g_canvas_w) * g_canvas_h, kBlack);
    const float t = (esp_timer_get_time() - g_state_started_us) / 1000000.0f;
    entry.draw(t, g_frame);
    ++g_frame;
    lv_obj_invalidate(g_canvas);
    return 1000 / entry.fps;
}

}  // namespace kiki
