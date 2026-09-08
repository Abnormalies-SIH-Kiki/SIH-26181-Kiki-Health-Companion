#include "kiki_ui.hpp"

#include "kiki_panel_state.hpp"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <ctime>
#include "audio_pipeline.hpp"
#include "bsp/esp-bsp.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs.h"
#include "gateway_client.hpp"
#include "kiki_dance.hpp"
#include "kiki_face.hpp"
#include "kiki_motion.hpp"
#include "kiki_wearable.hpp"
#include "kiki_settings.hpp"
#include "kiki_instructor.hpp"
#include "kiki_theme.hpp"
#include "lvgl.h"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_ui";

// The panel is a 466x466 *circle*. For a row at distance d from the centre the
// usable half-width is sqrt(233^2 - d^2), and everything below is placed
// against that. The first version put the text band at y=312..394, which is
// where the disc has already narrowed to ~330 px, so descenders and the second
// row were clipped by the bezel. The whole stack moved up and the band now ends
// at y=362, where there is ~388 px of width to spare.
constexpr int32_t kScreen = 466;
// The face sits as high as the disc allows. The crab occupies logical x=34..94
// of the 128-wide canvas, so at this width its top corners land at radius ~229
// of the 233 available -- any higher and the shell itself starts being clipped.
constexpr int32_t kFaceX = 63;
constexpr int32_t kFaceY = 18;
constexpr int32_t kFaceW = 340;
constexpr int32_t kFaceH = 170;
// What that buys is a real caption area: five or six lines instead of two rows.
constexpr int32_t kSpeechY = 200;
constexpr int32_t kSpeechH = 152;
constexpr int32_t kTextW = 340;
// y=372..430 with a 220-wide pill keeps its bottom corners at radius 226.
// Transport row. 60 px is about 5.7 mm on this panel (466 px across 1.75"),
// comfortably tappable without the row dominating the screen the way the
// first 96 px version did. Sits in the caption band, which is empty while a
// song is playing, with the track title above it.
constexpr int32_t kMediaBtn = 60;
constexpr int32_t kMediaGap = 20;
constexpr int32_t kMediaRowW = kMediaBtn * 3 + kMediaGap * 2;
constexpr int32_t kMediaY = 252;
constexpr int32_t kMediaTitleY = 214;

constexpr int32_t kTalkY = 366;
constexpr int32_t kTalkW = 220;
constexpr int32_t kTalkH = 58;

// The status row above the face: latency on the left, battery on the right.
// At y=34 the disc is ~268 px wide, so two labels either side of centre both
// clear the bezel with room to spare.
constexpr int32_t kStatusY = 34;
constexpr int32_t kStatusOffset = 70;

lv_obj_t *g_screen = nullptr;
lv_obj_t *g_line1 = nullptr;
lv_obj_t *g_line2 = nullptr;
lv_obj_t *g_speech = nullptr;
lv_obj_t *g_dashboard = nullptr;
lv_obj_t *g_dash_clock = nullptr, *g_dash_date = nullptr;
lv_obj_t *g_dash_steps = nullptr, *g_dash_heart = nullptr, *g_dash_hr_age = nullptr;
lv_obj_t *g_dash_weather = nullptr, *g_dash_humidity = nullptr;
lv_obj_t *g_dash_aqi = nullptr, *g_dash_air_age = nullptr;
lv_obj_t *g_dash_alerts = nullptr, *g_dash_updates = nullptr;
lv_obj_t *g_detail_screen = nullptr, *g_detail_title = nullptr, *g_detail_body = nullptr;
char g_weather_detail[1801] = "Weather unavailable";
char g_alert_detail[1801] = "Alerts unavailable";
char g_whatsapp_detail[2401] = "WhatsApp connecting";
int g_alert_count = 0, g_whatsapp_count = 0;
int64_t g_details_us = 0;
char g_environment[128] = "Weather unavailable\nAQI unavailable";
int64_t g_environment_us = 0;
int64_t g_dashboard_pause_until_us = 0;
lv_obj_t *g_latency = nullptr;
lv_obj_t *g_battery = nullptr;
lv_obj_t *g_talk = nullptr;
lv_obj_t *g_talk_label = nullptr;
lv_timer_t *g_face_timer = nullptr;
// Ambient capture never stops, and nothing else would clear it: the state stays
// `idle` while listening passively, so no status row arrives to take the band
// back. Without this the panel keeps showing a random overheard fragment
// indefinitely, which reads as a stuck screen.
lv_timer_t *g_passive_timer = nullptr;
// What Kiki is doing, and separately what her face is doing about it. The two
// used to be one buffer, which is how <oled:curious> mid-reply disabled the
// Stop button -- see kiki_panel_state.hpp, where the rule now lives and is
// tested on the host.
PanelState g_panel;
// The motion sampler runs on a different core. It may inspect these gates, but
// it must never race LVGL's mutable state-name buffer.
std::atomic<bool> g_motion_overlay_allowed{false};
std::atomic<bool> g_critical_motion_overlay_allowed{false};
int g_volume_percent = CONFIG_KIKI_AUDIO_VOLUME;

// Panel brightness. The default follows the build -- a low-power build starts
// dim -- but a value saved from the settings menu wins over it, because that is
// an explicit choice by whoever is holding the thing and a Kconfig default is
// not.
constexpr char kNamespace[] = "kiki_ui";
constexpr char kKeyBrightness[] = "bright";
#if CONFIG_KIKI_LOW_POWER
constexpr int kDefaultBrightness = CONFIG_KIKI_LOW_POWER_BRIGHTNESS;
#else
constexpr int kDefaultBrightness = 100;
#endif
constexpr int kMinBrightness = 5;  // below this the AMOLED is effectively off
int g_brightness = kDefaultBrightness;

int load_brightness() {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READONLY, &handle) != ESP_OK) return kDefaultBrightness;
    uint8_t value = 0;
    const esp_err_t err = nvs_get_u8(handle, kKeyBrightness, &value);
    nvs_close(handle);
    if (err != ESP_OK) return kDefaultBrightness;
    return std::clamp(static_cast<int>(value), kMinBrightness, 100);
}

void save_brightness(int percent) {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READWRITE, &handle) != ESP_OK) return;
    nvs_set_u8(handle, kKeyBrightness, static_cast<uint8_t>(percent));
    nvs_commit(handle);
    nvs_close(handle);
}

float g_gain = 3.2f;
bool g_holding = false;
bool g_long_hold = false;
// A Stop press is in progress and has not yet been escalated to "sleep".
bool g_stopping = false;

// Transport row. Only on screen while a song is loaded -- the panel is 1.75
// inches across and three permanent buttons would be taking space from the
// face for a control that is meaningless most of the time.
lv_obj_t *g_media_row = nullptr;
lv_obj_t *g_media_play_label = nullptr;
lv_obj_t *g_media_title = nullptr;
bool g_media_loaded = false;
bool g_media_paused = false;
// Dance mode owns the whole panel while it runs (see kiki_dance.cpp). Every
// status row, the transport, the talk button and the resting face are hidden;
// this remembers what to put back.
lv_obj_t *g_dance_hint = nullptr;
lv_timer_t *g_dance_hint_timer = nullptr;
bool g_dancing = false;

// While Kiki is talking the caption stays put. Status rows keep arriving
// underneath -- the legacy spinner cycling "Ruminating...", the follow-up
// window opening -- and every one of them used to replace the sentence she was
// still in the middle of saying.
bool g_caption_locked = false;

bool motion_reaction_allowed(bool critical) {
    // Nothing overlays the stage. A sulk face flashing over a dance because
    // she was nudged would break the one thing this feature is for.
    if (g_dancing) return false;
    return g_motion_overlay_allowed.load() ||
           (critical && g_critical_motion_overlay_allowed.load());
}

void apply_persona_background(const char *state) {
    if (!g_screen) return;
    lv_obj_set_style_bg_color(g_screen, theme::persona_background(state), 0);
}

bool kiki_is_busy() {
    return g_panel.busy();
}

bool kiki_is_listening() {
    return g_panel.listening();
}

void set_label(lv_obj_t *label, const char *text) {
    if (!label || !text) return;
    if (bsp_display_lock(100) == ESP_OK) {
        lv_label_set_text(label, text);
        bsp_display_unlock();
    }
}


// The request was for the *whole* sentence, not a 16x2 slice of it, so the font
// is chosen to make it fit rather than the text being cut to the font. The
// caption box is 340x152; Montserrat advances at roughly 0.55x its size, which
// gives about 88 characters at 28, 125 at 24 and 200 at 18.
const lv_font_t *font_for_length(size_t length) {
    if (length <= 80) return &lv_font_montserrat_28;
    if (length <= 120) return &lv_font_montserrat_24;
    return &lv_font_montserrat_18;
}

bool caption_holds() { return g_caption_locked; }

void show_speech(bool speaking) {
    if (!g_speech || !g_line1 || !g_line2) return;
    if (g_dashboard) lv_obj_add_flag(g_dashboard, LV_OBJ_FLAG_HIDDEN);
    if (speaking) {
        lv_obj_remove_flag(g_speech, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(g_line1, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(g_line2, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(g_speech, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(g_line1, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(g_line2, LV_OBJ_FLAG_HIDDEN);
    }
}

// One control, two jobs: hold it to talk when Kiki is idle, tap it to shut her
// up when she is not. Cancelling was previously only a tap on the background,
// which is not a thing anyone would discover.
void refresh_talk_button() {
    if (!g_talk || !g_talk_label) return;
    if (g_holding) {
        lv_obj_set_style_bg_color(g_talk, theme::accent(), 0);
        lv_obj_set_style_border_color(g_talk, theme::accent(), 0);
        lv_label_set_text(g_talk_label, "Listening...");
    } else if (kiki_is_busy()) {
        lv_obj_set_style_bg_color(g_talk, theme::stop(), 0);
        lv_obj_set_style_border_color(g_talk, theme::stop(), 0);
        lv_label_set_text(g_talk_label, "Stop");
    } else if (kiki_is_listening()) {
        lv_obj_set_style_bg_color(g_talk, theme::accent(), 0);
        lv_obj_set_style_border_color(g_talk, theme::accent(), 0);
        lv_label_set_text(g_talk_label, "Listening...");
    } else {
        lv_obj_set_style_bg_color(g_talk, theme::surface(), 0);
        lv_obj_set_style_border_color(g_talk, theme::border(), 0);
        lv_label_set_text(g_talk_label, "Tap or hold");
    }
}

// Press starts push-to-talk immediately rather than waiting for LVGL's 400 ms
// long-press: the Pi's IR hold fires on the first present sample, and a delay
// here would clip the beginning of whatever the user has already started
// saying. A stray tap is harmless -- force_commit on the gateway commits
// nothing below min_speech_ms.
void media_send(const char *action) {
    char payload[64];
    std::snprintf(payload, sizeof(payload), "\"action\":\"%s\"", action);
    gateway_client_send_event("media_control", payload);
}

void media_prev_cb(lv_event_t *) { media_send("previous"); }
void media_next_cb(lv_event_t *) { media_send("next"); }

void media_play_cb(lv_event_t *) {
    // Optimistic label flip so the button answers the finger immediately; the
    // gateway's media_state corrects it a moment later if the action failed.
    g_media_paused = !g_media_paused;
    if (g_media_play_label) {
        lv_label_set_text(g_media_play_label,
                          g_media_paused ? LV_SYMBOL_PLAY : LV_SYMBOL_PAUSE);
    }
    media_send("toggle");
}

// Stop is two controls in one, and the hold is deliberately a *superset* of the
// tap rather than a different action chosen at release: the cancel still goes
// out on the first touch, so barge-in stays instant, and holding past LVGL's
// long-press adds "and stop listening" on top. Waiting for release to decide
// would have put a whole press-and-lift between the user and silence, which is
// the one moment latency is most obvious.
// A hold has to survive the touch panel briefly losing the finger.
//
// LVGL raises LV_EVENT_PRESS_LOST whenever the press leaves the object -- a
// millimetre of drift on a capacitive panel is enough -- and that used to end
// the turn instantly, committing whatever fragment had been captured. Measured
// on the bench, presses meant as holds were arriving as 119 ms and 178 ms.
//
// So a lost press does not end the hold immediately: it starts this grace
// window, and only if the finger has not come back by the end of it is the
// sentence committed. A genuine release still ends the turn at once, because
// LV_EVENT_RELEASED is a different event and is handled separately.
constexpr uint32_t kHoldGraceMs = 250;
constexpr uint32_t kLongHoldMs = 400;
lv_timer_t *g_hold_grace = nullptr;
int64_t g_hold_started_us = 0;

void finish_talk_press(const char *why) {
    if (!g_holding) return;
    const uint32_t elapsed_ms = static_cast<uint32_t>(
        (esp_timer_get_time() - g_hold_started_us) / 1000);
    const bool held = g_long_hold || elapsed_ms >= kLongHoldMs;
    g_holding = false;
    g_long_hold = false;
    refresh_talk_button();
    ESP_LOGI(kTag, "talk: %s ended by %s after %u ms",
             held ? "hold" : "tap", why, static_cast<unsigned>(elapsed_ms));
    // Press began capture immediately so the first syllable is never clipped.
    // A tap leaves that window open like a hotword; a hold makes release the
    // one and only endpoint.
    gateway_client_send_event(held ? "commit_now" : "listen_open");
}

void hold_grace_cb(lv_timer_t *) {
    // Do NOT delete the timer here. It is created with a repeat count of one,
    // and LVGL deletes a one-shot itself as soon as the callback returns --
    // deleting it as well is a double free on the LVGL heap, which showed up as
    // the board rebooting whenever the talk button was touched. Clearing the
    // pointer is the whole job; cancel_hold_grace() only ever sees a timer that
    // has not fired yet.
    g_hold_grace = nullptr;
    finish_talk_press("press lost");
}

void cancel_hold_grace() {
    if (!g_hold_grace) return;
    lv_timer_delete(g_hold_grace);
    g_hold_grace = nullptr;
}

void talk_event_cb(lv_event_t *event) {
    switch (lv_event_get_code(event)) {
        case LV_EVENT_PRESSED:
            motion_note_interaction();
            if (kiki_is_busy()) {
                // Tap: stop, but stay listening so you can just keep talking.
                g_stopping = true;
                gateway_client_send_event("cancel_turn");
                break;
            }
            // The finger came back inside the grace window: this is the same
            // hold continuing, not a new one. Nothing to re-announce.
            if (g_holding) {
                cancel_hold_grace();
                break;
            }
            g_holding = true;
            g_long_hold = false;
            g_hold_started_us = esp_timer_get_time();
            refresh_talk_button();
            gateway_client_send_event("push_to_talk");
            break;
        case LV_EVENT_LONG_PRESSED:
            if (g_holding) g_long_hold = true;
            if (g_stopping) {
                // Hold: and go back to sleep, so the wake word is needed again.
                g_stopping = false;
                gateway_client_send_event("sleep");
            }
            break;
        case LV_EVENT_RELEASED:
            g_stopping = false;
            cancel_hold_grace();
            finish_talk_press("release");
            break;
        case LV_EVENT_PRESS_LOST:
            g_stopping = false;
            if (g_holding && !g_hold_grace) {
                g_hold_grace = lv_timer_create(hold_grace_cb, kHoldGraceMs, nullptr);
                if (g_hold_grace) {
                    lv_timer_set_repeat_count(g_hold_grace, 1);
                } else {
                    finish_talk_press("press lost");
                }
            }
            break;
        default:
            break;
    }
}

// Body taps are local physical comedy, not commands. They remain immediate
// even if the laptop is unreachable and cannot pollute conversation history.
lv_timer_t *g_reaction_timer = nullptr;

void restore_after_reaction(lv_timer_t *) {
    // One-shot timer: LVGL owns deletion after this callback returns.
    g_reaction_timer = nullptr;
    // Back to whatever was showing, mood included -- not to the bare state.
    apply_persona_background(g_panel.pose());
    face_set_state(g_panel.pose(), "");
}

void cancel_reaction_locked() {
    if (!g_reaction_timer) return;
    lv_timer_delete(g_reaction_timer);
    g_reaction_timer = nullptr;
}

struct BodyHit {
    const char *part;
    const char *reaction;
    LocalEffect effect;
    uint32_t duration_ms;
};

bool body_hit_at(const lv_point_t &p, BodyHit *hit) {
    if (!hit) return false;
    // Generous hit boxes surround the visible logical-pixel geometry; eyes are
    // checked before shell, and legs before belly, so overlapping edges choose
    // the part the finger visually appears to touch.
    if (p.y >= 90 && p.y <= 132 && p.x >= 184 && p.x <= 230) {
        *hit = {"left eye", "tap_ouch_left", LocalEffect::EyeLeft, 1050};
        return true;
    }
    if (p.y >= 90 && p.y <= 132 && p.x >= 236 && p.x <= 282) {
        *hit = {"right eye", "tap_ouch_right", LocalEffect::EyeRight, 1100};
        return true;
    }
    if (p.x >= 125 && p.x <= 183 && p.y >= 72 && p.y <= 160) {
        *hit = {"left claw", "tap_pinch", LocalEffect::LeftClaw, 1150};
        return true;
    }
    if (p.x >= 284 && p.x <= 342 && p.y >= 72 && p.y <= 160) {
        *hit = {"right claw", "tap_blaster", LocalEffect::RightClaw, 1250};
        return true;
    }
    if (p.x >= 174 && p.x <= 294 && p.y >= 145 && p.y <= 194) {
        *hit = {"legs", "tap_tumble", LocalEffect::Legs, 1350};
        return true;
    }
    if (p.x >= 168 && p.x <= 300 && p.y >= 44 && p.y < 90) {
        *hit = {"head", "tap_pat", LocalEffect::Head, 1150};
        return true;
    }
    if (p.x >= 171 && p.x <= 296 && p.y >= 78 && p.y <= 160) {
        *hit = {"shell", "tap_boop", LocalEffect::Shell, 1100};
        return true;
    }
    return false;
}

void play_body_hit(const BodyHit &hit) {
    motion_note_interaction();
    cancel_reaction_locked();
    apply_persona_background(hit.reaction);
    face_set_state(hit.reaction, hit.part);
    audio_pipeline_play_local_effect(hit.effect);
    g_reaction_timer = lv_timer_create(restore_after_reaction, hit.duration_ms, nullptr);
    if (g_reaction_timer) {
        lv_timer_set_repeat_count(g_reaction_timer, 1);
    }
    ESP_LOGI(kTag, "body tap: %s -> %s", hit.part, hit.reaction);
}

// The rest of the panel: a tap wakes Kiki or cancels whatever is running (the
// Pi's double-tap "stop and go idle"), and a long press opens settings (the
// Pi's both-sensor hold).
void surface_event_cb(lv_event_t *event) {
    // While she is dancing the whole panel is one control: touch it anywhere
    // and the performance ends. Body-tap comedy and the settings menu are
    // deliberately unreachable -- the stage has no other buttons, so anything
    // else would be a gesture nobody could discover or undo.
    if (g_dancing) {
        const lv_event_code_t code = lv_event_get_code(event);
        if (code == LV_EVENT_SHORT_CLICKED || code == LV_EVENT_LONG_PRESSED) {
            motion_note_interaction();
            dance_stop("tap");
        }
        return;
    }
    switch (lv_event_get_code(event)) {
        case LV_EVENT_SHORT_CLICKED:
            motion_note_interaction();
            if (lv_indev_t *indev = lv_event_get_indev(event)) {
                lv_point_t point{};
                lv_indev_get_point(indev, &point);
                BodyHit hit{};
                if (body_hit_at(point, &hit)) {
                    play_body_hit(hit);
                    break;
                }
            }
            gateway_client_send_event(kiki_is_busy() ? "cancel_turn" : "wake");
            break;
        case LV_EVENT_LONG_PRESSED:
            motion_note_interaction();
            if (wearable_fall_check_pending())
                wearable_cancel_fall_check("deliberate_long_press");
            else
                ui_open_settings();
            break;
        default:
            break;
    }
}

void passive_expiry_cb(lv_timer_t *) {
    g_passive_timer = nullptr;
    if (!caption_holds()) show_speech(false);
}

bool conversation_visible() {
    // Home is the one intentionally mascot-free screen. Boot, connecting,
    // warming, alerts and every conversational state retain the full original
    // face so status text never appears alone.
    return std::strcmp(g_panel.state(), "idle") != 0 &&
           std::strcmp(g_panel.state(), "music") != 0;
}

void detail_back(lv_event_t *) { lv_screen_load(g_screen); }

void dashboard_card_clicked(lv_event_t *event) {
    const auto kind = reinterpret_cast<intptr_t>(lv_event_get_user_data(event));
    motion_note_interaction();
    if (kind == 1) { settings_measure_heart(g_screen); return; }
    if (!g_detail_screen) {
        g_detail_screen = lv_obj_create(nullptr);
        lv_obj_set_style_bg_color(g_detail_screen, lv_color_hex(0x111318), 0);
        lv_obj_remove_flag(g_detail_screen, LV_OBJ_FLAG_SCROLLABLE);
        g_detail_title = lv_label_create(g_detail_screen);
        lv_obj_set_style_text_font(g_detail_title, &lv_font_montserrat_28, 0);
        lv_obj_set_style_text_color(g_detail_title, lv_color_hex(0xB8D8FF), 0);
        lv_obj_align(g_detail_title, LV_ALIGN_TOP_MID, 0, 48);
        auto *scroll = lv_obj_create(g_detail_screen);
        lv_obj_set_pos(scroll, 65, 96);
        lv_obj_set_size(scroll, 336, 254);
        lv_obj_set_style_bg_opa(scroll, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(scroll, 0, 0);
        lv_obj_set_style_pad_all(scroll, 8, 0);
        lv_obj_set_scroll_dir(scroll, LV_DIR_VER);
        g_detail_body = lv_label_create(scroll);
        lv_obj_set_width(g_detail_body, 306);
        lv_label_set_long_mode(g_detail_body, LV_LABEL_LONG_WRAP);
        lv_obj_set_style_text_font(g_detail_body, &lv_font_montserrat_18, 0);
        lv_obj_set_style_text_color(g_detail_body, lv_color_hex(0xE5E9EF), 0);
        auto *back = lv_button_create(g_detail_screen);
        lv_obj_set_size(back, 180, 54);
        lv_obj_set_pos(back, 143, 370);
        lv_obj_set_style_radius(back, 27, 0);
        lv_obj_add_event_cb(back, detail_back, LV_EVENT_CLICKED, nullptr);
        auto *label = lv_label_create(back);
        lv_label_set_text(label, LV_SYMBOL_LEFT "  Home");
        lv_obj_center(label);
    }
    char activity[1100];
    const char *title = "Activity", *body = activity;
    if (kind == 0) wearable_activity_detail(activity, sizeof(activity));
    else if (kind == 2 || kind == 3) { title = "Weather & air"; body = g_weather_detail; }
    else if (kind == 4) { title = "Alerts"; body = g_alert_detail; }
    else { title = "WhatsApp updates"; body = g_whatsapp_detail; }
    if (kind != 0 && esp_timer_get_time() - g_details_us > 120000000)
        body = "Updates unavailable. Reconnect to refresh this page.";
    lv_label_set_text(g_detail_title, title);
    lv_label_set_text(g_detail_body, body);
    lv_obj_scroll_to_y(lv_obj_get_parent(g_detail_body), 0, LV_ANIM_OFF);
    lv_screen_load(g_detail_screen);
}

lv_obj_t *dashboard_label(lv_obj_t *parent, int x, int y, int width,
                          const char *text, const lv_font_t *font, uint32_t color) {
    auto *label = lv_label_create(parent);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_width(label, width);
    lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
    lv_label_set_text(label, text);
    return label;
}

lv_obj_t *dashboard_card(int x, int y, const char *title, uint32_t color,
                         uint32_t background, intptr_t kind) {
    auto *card = lv_obj_create(g_dashboard);
    lv_obj_set_pos(card, x, y);
    lv_obj_set_size(card, 164, 68);
    lv_obj_set_style_radius(card, 22, 0);
    lv_obj_set_style_bg_color(card, lv_color_hex(background), 0);
    lv_obj_set_style_border_width(card, 0, 0);
    lv_obj_set_style_pad_all(card, 0, 0);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(card, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(card, dashboard_card_clicked, LV_EVENT_CLICKED, reinterpret_cast<void *>(kind));
    dashboard_label(card, 14, 7, 140, title, &lv_font_montserrat_14, color);
    return card;
}

void dashboard_set_text(lv_obj_t *label, const char *text) {
    // Static watch complications do not allocate/repaint every second.
    if (std::strcmp(lv_label_get_text(label), text) != 0) lv_label_set_text(label, text);
}

void dashboard_timer_cb(lv_timer_t *) {
    if (!g_dashboard) return;
    if (!g_dancing) {
        face_set_visible(std::strcmp(g_panel.state(), "idle") != 0 ||
                         wearable_fall_check_pending());
    }
    const bool show = !g_dancing && !caption_holds() && !g_passive_timer &&
        esp_timer_get_time() >= g_dashboard_pause_until_us &&
        std::strcmp(g_panel.state(), "idle") == 0 && !wearable_fall_check_pending();
    if (!show) {
        lv_obj_add_flag(g_dashboard, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    WearableSnapshot snapshot{};
    wearable_get_snapshot(&snapshot);
    char clock[40] = "--:--";
    char date[40] = "SYNCING TIME";
    std::time_t now = std::time(nullptr);
    if (now > 1700000000) {
        now += 19800; // Same Asia/Kolkata display timezone as the RPi.
        std::tm local{};
        gmtime_r(&now, &local);
        std::strftime(clock, sizeof(clock), "%I:%M", &local);
        std::strftime(date, sizeof(date), "%a, %d %b  /  %p IST", &local);
    }
    dashboard_set_text(g_dash_clock, clock);
    dashboard_set_text(g_dash_date, date);
    char steps[24];
    std::snprintf(steps, sizeof(steps), "%lu", static_cast<unsigned long>(snapshot.steps));
    dashboard_set_text(g_dash_steps, steps);
    char heart[32] = "--";
    char age[40] = "Measure in Settings";
    if (snapshot.heart_rate > 0 && snapshot.heart_rate_age_seconds != UINT32_MAX) {
        std::snprintf(heart, sizeof(heart), "%.0f", static_cast<double>(snapshot.heart_rate));
        std::snprintf(age, sizeof(age), "bpm / %lum ago",
            static_cast<unsigned long>(snapshot.heart_rate_age_seconds / 60));
    }
    dashboard_set_text(g_dash_heart, heart);
    dashboard_set_text(g_dash_hr_age, age);
    const char *environment = esp_timer_get_time() - g_environment_us < 120000000
        ? g_environment : "Weather unavailable\nAQI unavailable";
    int temperature = 0, humidity = 0, aqi = 0;
    char weather[24] = "--", moisture[40] = "Unavailable", air[24] = "--";
    const bool stale = std::strstr(environment, "stale") != nullptr;
    if (std::sscanf(environment, "%d C", &temperature) == 1) {
        std::snprintf(weather, sizeof(weather), "%d C", temperature);
        const char *rh = std::strstr(environment, "Humidity ");
        if (rh && std::sscanf(rh, "Humidity %d", &humidity) == 1)
            std::snprintf(moisture, sizeof(moisture), "RH %d%%%s", humidity, stale ? " / stale" : "");
        else std::snprintf(moisture, sizeof(moisture), "%s", stale ? "Stale reading" : "Outside");
    }
    const char *aqi_text = std::strstr(environment, "AQI ~");
    const bool has_aqi = aqi_text && std::sscanf(aqi_text, "AQI ~%d", &aqi) == 1;
    if (has_aqi) std::snprintf(air, sizeof(air), "~%d", aqi);
    dashboard_set_text(g_dash_weather, weather);
    dashboard_set_text(g_dash_humidity, moisture);
    dashboard_set_text(g_dash_aqi, air);
    dashboard_set_text(g_dash_air_age, !has_aqi ? "Unavailable" :
                       (stale ? "CPCB est. / stale" : "CPCB estimated"));
    char count[24];
    std::snprintf(count, sizeof(count), "%d", g_alert_count);
    dashboard_set_text(g_dash_alerts, count);
    std::snprintf(count, sizeof(count), "%d", g_whatsapp_count);
    dashboard_set_text(g_dash_updates, count);
    lv_obj_add_flag(g_line1, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(g_line2, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(g_speech, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(g_dashboard, LV_OBJ_FLAG_HIDDEN);
}

void face_timer_cb(lv_timer_t *timer) {
    if(instructor_active()) {lv_timer_set_period(timer,100);return;}
    // One render timer for both scenes. The dance sets its own period from the
    // time the last frame took and from how full the audio buffer is, so the
    // two cannot fight over the display or the CPU.
    lv_timer_set_period(timer,
                        dance_is_active() ? dance_render_frame() : face_render_frame());
}

void dance_hint_expired(lv_timer_t *) {
    g_dance_hint_timer = nullptr;
    if (g_dance_hint) lv_obj_add_flag(g_dance_hint, LV_OBJ_FLAG_HIDDEN);
}

}  // namespace

void ui_start() {
    // The BSP default leaves the LVGL task unpinned, which lets a continuously
    // animating face land on core 1 and miss the codec's 10 ms deadline. Audio
    // owns core 1; rendering shares core 0 with Wi-Fi and yields to it.
    bsp_display_cfg_t cfg = {
        .lv_adapter_cfg = ESP_LV_ADAPTER_DEFAULT_CONFIG(),
        .rotation = ESP_LV_ADAPTER_ROTATE_0,
        .tear_avoid_mode = ESP_LV_ADAPTER_TEAR_AVOID_MODE_NONE,
        .touch_flags = {.swap_xy = 0, .mirror_x = 1, .mirror_y = 1},
    };
    cfg.lv_adapter_cfg.task_core_id = 0;
    bsp_display_start_with_config(&cfg);
    ESP_LOGI(kTag, "BSP display started");
    if (bsp_display_lock(1000) != ESP_OK) {
        ESP_LOGE(kTag, "could not acquire LVGL lock");
        return;
    }

    lv_obj_t *screen = lv_screen_active();
    g_screen = screen;
    lv_obj_set_style_bg_color(screen, theme::background(), 0);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(screen, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(screen, surface_event_cb, LV_EVENT_SHORT_CLICKED, nullptr);
    lv_obj_add_event_cb(screen, surface_event_cb, LV_EVENT_LONG_PRESSED, nullptr);

    face_init(screen, kFaceX, kFaceY, kFaceW, kFaceH);
    // The stage canvas is allocated on the first dance, but it belongs to this
    // screen; telling it so now keeps it off whatever screen happens to be
    // loaded when someone asks Kiki to dance.
    dance_attach(screen);

    g_line1 = lv_label_create(screen);
    lv_label_set_long_mode(g_line1, LV_LABEL_LONG_DOT);
    lv_obj_set_width(g_line1, kTextW);
    lv_obj_set_style_text_align(g_line1, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(g_line1, &lv_font_montserrat_36, 0);
    lv_obj_set_style_text_color(g_line1, theme::text_primary(), 0);
    lv_obj_set_pos(g_line1, (kScreen - kTextW) / 2, kSpeechY + 14);

    g_line2 = lv_label_create(screen);
    lv_label_set_long_mode(g_line2, LV_LABEL_LONG_DOT);
    lv_obj_set_width(g_line2, kTextW);
    lv_obj_set_style_text_align(g_line2, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(g_line2, &lv_font_montserrat_28, 0);
    lv_obj_set_style_text_color(g_line2, theme::text_secondary(), 0);
    lv_obj_set_pos(g_line2, (kScreen - kTextW) / 2, kSpeechY + 62);

    g_speech = lv_label_create(screen);
    lv_label_set_long_mode(g_speech, LV_LABEL_LONG_WRAP);
    lv_obj_set_size(g_speech, kTextW, kSpeechH);
    lv_obj_set_style_text_align(g_speech, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(g_speech, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(g_speech, theme::text_primary(), 0);
    lv_obj_set_pos(g_speech, (kScreen - kTextW) / 2, kSpeechY);
    lv_label_set_text(g_speech, "");
    lv_obj_add_flag(g_speech, LV_OBJ_FLAG_HIDDEN);

    g_latency = lv_label_create(screen);
    lv_obj_set_style_text_font(g_latency, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(g_latency, theme::text_secondary(), 0);
    lv_obj_align(g_latency, LV_ALIGN_TOP_MID, -kStatusOffset, kStatusY);
    lv_label_set_text(g_latency, "");

    g_battery = lv_label_create(screen);
    lv_obj_set_style_text_font(g_battery, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(g_battery, theme::text_secondary(), 0);
    lv_obj_align(g_battery, LV_ALIGN_TOP_MID, kStatusOffset, kStatusY);
    lv_label_set_text(g_battery, "");

    // A visible control, not an invisible gesture. The first version mapped
    // push-to-talk onto transparent edge zones, which meant there was nothing
    // on screen telling anyone it existed.
    g_talk = lv_button_create(screen);
    lv_obj_set_size(g_talk, kTalkW, kTalkH);
    lv_obj_set_pos(g_talk, (kScreen - kTalkW) / 2, kTalkY);
    lv_obj_set_style_radius(g_talk, kTalkH / 2, 0);
    lv_obj_set_style_bg_color(g_talk, theme::surface(), 0);
    lv_obj_set_style_border_color(g_talk, theme::border(), 0);
    lv_obj_set_style_border_width(g_talk, 2, 0);
    lv_obj_set_style_shadow_width(g_talk, 0, 0);
    lv_obj_add_event_cb(g_talk, talk_event_cb, LV_EVENT_PRESSED, nullptr);
    lv_obj_add_event_cb(g_talk, talk_event_cb, LV_EVENT_LONG_PRESSED, nullptr);
    lv_obj_add_event_cb(g_talk, talk_event_cb, LV_EVENT_RELEASED, nullptr);
    lv_obj_add_event_cb(g_talk, talk_event_cb, LV_EVENT_PRESS_LOST, nullptr);
    // Give the finger room to wander without leaving the control. Cheaper than
    // a bigger button on a round 466 px panel where the layout is already tight.
    lv_obj_set_ext_click_area(g_talk, 24);

    // Previous / play-pause / next. Sized against the inscribed disc at this
    // height (usable half-width is sqrt(233^2 - d^2), so ~395 px at y=356) and
    // placed over the caption area, which is empty while music is playing.
    g_media_title = lv_label_create(screen);
    lv_label_set_long_mode(g_media_title, LV_LABEL_LONG_DOT);
    lv_obj_set_width(g_media_title, kTextW);
    lv_obj_set_style_text_align(g_media_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(g_media_title, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(g_media_title, theme::text_secondary(), 0);
    lv_obj_set_pos(g_media_title, (kScreen - kTextW) / 2, kMediaTitleY);
    lv_label_set_text(g_media_title, "");
    lv_obj_add_flag(g_media_title, LV_OBJ_FLAG_HIDDEN);

    g_media_row = lv_obj_create(screen);
    lv_obj_set_size(g_media_row, kMediaRowW, kMediaBtn);
    lv_obj_set_pos(g_media_row, (kScreen - kMediaRowW) / 2, kMediaY);
    lv_obj_set_style_bg_opa(g_media_row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(g_media_row, 0, 0);
    lv_obj_set_style_pad_all(g_media_row, 0, 0);
    lv_obj_remove_flag(g_media_row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(g_media_row, LV_OBJ_FLAG_HIDDEN);

    struct {
        const char *symbol;
        lv_event_cb_t handler;
        lv_obj_t **label_out;
    } buttons[] = {
        {LV_SYMBOL_PREV, media_prev_cb, nullptr},
        {LV_SYMBOL_PAUSE, media_play_cb, &g_media_play_label},
        {LV_SYMBOL_NEXT, media_next_cb, nullptr},
    };
    for (int i = 0; i < 3; ++i) {
        lv_obj_t *button = lv_button_create(g_media_row);
        lv_obj_set_size(button, kMediaBtn, kMediaBtn);
        lv_obj_set_pos(button, i * (kMediaBtn + kMediaGap), 0);
        lv_obj_set_style_radius(button, kMediaBtn / 2, 0);
        lv_obj_set_style_bg_color(button, theme::surface(), 0);
        lv_obj_set_style_border_color(button, theme::border(), 0);
        lv_obj_set_style_border_width(button, 2, 0);
        lv_obj_set_style_shadow_width(button, 0, 0);
        lv_obj_add_event_cb(button, buttons[i].handler, LV_EVENT_CLICKED, nullptr);
        lv_obj_t *label = lv_label_create(button);
        lv_obj_set_style_text_font(label, &lv_font_montserrat_18, 0);
        lv_obj_set_style_text_color(label, theme::text_primary(), 0);
        lv_label_set_text(label, buttons[i].symbol);
        lv_obj_center(label);
        if (buttons[i].label_out) *buttons[i].label_out = label;
    }

    g_talk_label = lv_label_create(g_talk);
    lv_obj_set_style_text_font(g_talk_label, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(g_talk_label, theme::text_primary(), 0);
    lv_label_set_text(g_talk_label, "Hold to talk");
    lv_obj_center(g_talk_label);

    lv_label_set_text(g_line1, "Starting up");
    lv_label_set_text(g_line2, "");

    // The AMOLED is a large share of the board's draw, so this is also the
    // cheapest power control there is -- which is why a low-power build simply
    // ships a dimmer default rather than owning the setting outright.
    g_brightness = load_brightness();
    bsp_display_brightness_set(g_brightness);
    ESP_LOGI(kTag, "panel brightness %d%%", g_brightness);

    g_face_timer = lv_timer_create(face_timer_cb, 100, nullptr);
    g_dashboard = lv_obj_create(screen);
    lv_obj_set_size(g_dashboard, kScreen, kScreen);
    lv_obj_set_pos(g_dashboard, 0, 0);
    lv_obj_set_style_bg_opa(g_dashboard, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(g_dashboard, 0, 0);
    lv_obj_set_style_pad_all(g_dashboard, 0, 0);
    lv_obj_remove_flag(g_dashboard, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(g_dashboard, LV_OBJ_FLAG_CLICKABLE);
    g_dash_clock = dashboard_label(g_dashboard, 83, 54, 300, "--:--",
                                   &lv_font_montserrat_36, 0xF7F8FC);
    lv_obj_set_style_text_align(g_dash_clock, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_letter_space(g_dash_clock, 4, 0);
    g_dash_date = dashboard_label(g_dashboard, 63, 100, 340, "SYNCING TIME",
                                  &lv_font_montserrat_14, 0xA6ACBC);
    lv_obj_set_style_text_align(g_dash_date, LV_TEXT_ALIGN_CENTER, 0);
    auto *steps_card = dashboard_card(64, 130, "ACTIVITY", 0xB8F568, 0x1C2819, 0);
    g_dash_steps = dashboard_label(steps_card, 14, 21, 140, "0", &lv_font_montserrat_28, 0xB8F568);
    dashboard_label(steps_card, 14, 51, 140, "steps / tap for minutes", &lv_font_montserrat_14, 0xA3B49A);
    auto *heart_card = dashboard_card(238, 130, "HEART RATE", 0xFF7597, 0x301B24, 1);
    g_dash_heart = dashboard_label(heart_card, 14, 21, 140, "--", &lv_font_montserrat_28, 0xFF7597);
    g_dash_hr_age = dashboard_label(heart_card, 14, 51, 140, "Tap to measure", &lv_font_montserrat_14, 0xCBA5B0);
    auto *weather_card = dashboard_card(64, 208, "WEATHER", 0x80CEFF, 0x182633, 2);
    g_dash_weather = dashboard_label(weather_card, 14, 21, 140, "--", &lv_font_montserrat_28, 0x80CEFF);
    g_dash_humidity = dashboard_label(weather_card, 14, 51, 140, "--", &lv_font_montserrat_14, 0x9FB8CB);
    auto *air_card = dashboard_card(238, 208, "AIR QUALITY", 0xFFD279, 0x2D2518, 3);
    g_dash_aqi = dashboard_label(air_card, 14, 21, 140, "--", &lv_font_montserrat_28, 0xFFD279);
    g_dash_air_age = dashboard_label(air_card, 14, 51, 140, "CPCB", &lv_font_montserrat_14, 0xC9B998);
    auto *alerts_card = dashboard_card(64, 286, "ALERTS", 0xFFA681, 0x302018, 4);
    g_dash_alerts = dashboard_label(alerts_card, 14, 24, 65, "0", &lv_font_montserrat_28, 0xFFA681);
    dashboard_label(alerts_card, 72, 41, 80, "View all", &lv_font_montserrat_14, 0xCCA890);
    auto *updates_card = dashboard_card(238, 286, "WHATSAPP", 0x83E7BC, 0x172C25, 5);
    g_dash_updates = dashboard_label(updates_card, 14, 24, 65, "0", &lv_font_montserrat_28, 0x83E7BC);
    dashboard_label(updates_card, 72, 41, 80, "Recent", &lv_font_montserrat_14, 0x9CC7B7);
    lv_obj_add_flag(g_dashboard, LV_OBJ_FLAG_HIDDEN);
    lv_timer_create(dashboard_timer_cb, 1000, nullptr);
    bsp_display_unlock();
    ESP_LOGI(kTag, "UI objects created");
}

void ui_open_settings() {
    // Not from the stage. Dance mode maps the entire panel to "stop", so a
    // long press there must not also drop a settings list on top of it.
    if (g_dancing) return;
    motion_note_interaction();
    if (bsp_display_lock(1000) != ESP_OK) return;
    settings_open(g_screen, g_volume_percent, g_gain);
    bsp_display_unlock();
}

bool ui_talk_held() { return g_holding; }

void ui_set_volume_percent(int percent) { g_volume_percent = percent; }

void ui_set_gain(float gain) { g_gain = gain; }

void ui_show() {
    if (!g_screen) return;
    if (bsp_display_lock(1000) == ESP_OK) {
        lv_screen_load(g_screen);
        bsp_display_unlock();
    }
}

void ui_set_state_detail(const char *state, const char *detail) {
    if (!state) return;
    const bool ordinary_motion = std::strcmp(state, "idle") == 0 ||
                                 std::strcmp(state, "disconnected") == 0 ||
                                 std::strcmp(state, "music") == 0;
    const bool critical_motion = std::strcmp(state, "boot") != 0 &&
                                 std::strcmp(state, "booting") != 0 &&
                                 std::strcmp(state, "warming") != 0 &&
                                 std::strcmp(state, "connecting") != 0 &&
                                 std::strcmp(state, "goodbye") != 0;
    g_motion_overlay_allowed = ordinary_motion;
    g_critical_motion_overlay_allowed = critical_motion;
    motion_note_system_state(state);
    g_panel.set_state(state);
    // The caption is held for the whole turn, not just the sentence: a tool
    // round drops through `tool` and back to `speaking`, and blanking her words
    // in between reads as a glitch. Anything else ends the turn and releases it.
    if (std::strcmp(state, "speaking") != 0 && std::strcmp(state, "tool") != 0) {
        g_caption_locked = false;
    }
    // The name is still recorded while dancing -- ui_exit_dance() restores the
    // face to it -- but nothing is drawn. The gateway sends `state: music` when
    // the song starts, and repainting the resting face over the stage would
    // undo the takeover a fraction of a second after it happened.
    if (g_dancing) return;
    if (bsp_display_lock(100) == ESP_OK) {
        if (g_dashboard) lv_obj_add_flag(g_dashboard, LV_OBJ_FLAG_HIDDEN);
        face_set_visible(conversation_visible());
        if (conversation_visible() && g_detail_screen && lv_screen_active() == g_detail_screen)
            lv_screen_load(g_screen);
        cancel_reaction_locked();
        apply_persona_background(state);
        face_set_state(state, detail);
        // Home hides all caption rows. Restore them when a conversation starts;
        // otherwise a thinking/warming transition shows only a juggling mascot.
        if (conversation_visible() && !caption_holds()) {
            show_speech(false);
            if (std::strcmp(state, "thinking") == 0 || std::strcmp(state, "warming") == 0) {
                lv_label_set_text(g_line1, std::strcmp(state, "warming") == 0
                    ? "Getting ready..." : "Thinking...");
                lv_label_set_text(g_line2, "Tap Stop to cancel");
            }
        }
        refresh_talk_button();
        dashboard_timer_cb(nullptr);
        bsp_display_unlock();
    }
}

void ui_set_state(const char *state) { ui_set_state_detail(state, ""); }

void ui_set_expression(const char *name) {
    if (g_dancing) return;
    // The pose, NOT the state. A mood is how she looks while she is speaking;
    // it is not a thing she is doing, and the Stop button has to keep working
    // through it. set_expression() refuses moods outside speaking/tool.
    if (!g_panel.set_expression(name)) return;
    if (bsp_display_lock(100) == ESP_OK) {
        apply_persona_background(name);
        face_set_state(name, "");
        bsp_display_unlock();
    }
}

bool ui_play_motion_reaction(const char *state, const char *detail,
                             LocalEffect effect, uint32_t duration_ms, bool critical) {
    if (!conversation_visible()) return false;
    if (!state || !*state || !motion_reaction_allowed(critical)) return false;
    if (bsp_display_lock(100) != ESP_OK) return false;
    // A gateway state update always cancels this timer in ui_set_state_detail;
    // this path never changes the panel state, so restoration returns to the real
    // conversation/music/connectivity state rather than to another reaction.
    cancel_reaction_locked();
    apply_persona_background(state);
    face_set_state(state, detail ? detail : "");
    if (duration_ms > 0) {
        g_reaction_timer = lv_timer_create(restore_after_reaction, duration_ms, nullptr);
        if (g_reaction_timer) {
            lv_timer_set_repeat_count(g_reaction_timer, 1);
        } else {
            apply_persona_background(g_panel.pose());
            face_set_state(g_panel.pose(), "");
            bsp_display_unlock();
            return false;
        }
    }
    bsp_display_unlock();
    audio_pipeline_play_local_effect(effect);
    return true;
}

void ui_set_lcd(const char *line1, const char *line2) {
    const bool alert = line1 && (std::strstr(line1, "fall") || std::strstr(line1, "Fall") ||
                                std::strstr(line1, "Glad"));
    if (std::strcmp(g_panel.state(), "idle") == 0 && !alert) return;
    // Never while she is speaking: the row would replace the sentence.
    // Never while she is dancing: the stage has no rows at all.
    if (caption_holds() || g_dancing) return;
    if (bsp_display_lock(100) == ESP_OK) {
        if (g_dashboard) lv_obj_add_flag(g_dashboard, LV_OBJ_FLAG_HIDDEN);
        if (alert) face_set_visible(true);
        show_speech(false);
        if (line1 && (std::strstr(line1, "fall") || std::strstr(line1, "Fall") ||
                      std::strstr(line1, "Glad"))) {
            g_dashboard_pause_until_us = esp_timer_get_time() + 10000000;
        }
        lv_label_set_text(g_line1, line1 ? line1 : "");
        lv_label_set_text(g_line2, line2 ? line2 : "");
        bsp_display_unlock();
    }
}

void ui_set_speech(const char *text) {
    if (!text || g_dancing) return;
    g_caption_locked = true;
    if (bsp_display_lock(100) == ESP_OK) {
        if (g_dashboard) lv_obj_add_flag(g_dashboard, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_text_font(g_speech, font_for_length(std::strlen(text)), 0);
        lv_obj_set_style_text_color(g_speech, theme::text_primary(), 0);
        lv_label_set_text(g_speech, text);
        show_speech(true);
        bsp_display_unlock();
    }
}

// What the room said: your speech, and passive capture. Same area as Kiki's
// caption but in the accent colour, so at a glance it is obvious who is
// talking. Deliberately does NOT take the caption lock -- that belongs to Kiki
// mid-sentence, and a transcript arriving then must not steal her words.
void ui_set_transcript(const char *text, bool passive) {
    if (passive || !conversation_visible() || !text || !*text || caption_holds() || g_dancing) return;
    if (bsp_display_lock(100) == ESP_OK) {
        lv_obj_set_style_text_font(g_speech, font_for_length(std::strlen(text)), 0);
        lv_obj_set_style_text_color(
            g_speech, passive ? theme::text_secondary() : theme::accent(), 0);
        lv_label_set_text(g_speech, text);
        show_speech(true);
        if (g_passive_timer) {
            lv_timer_delete(g_passive_timer);
            g_passive_timer = nullptr;
        }
        if (passive) {
            g_passive_timer = lv_timer_create(passive_expiry_cb, 10000, nullptr);
            lv_timer_set_repeat_count(g_passive_timer, 1);
        }
        bsp_display_unlock();
    }
}

void ui_set_dashboard_environment(const char *text) {
    if (bsp_display_lock(100) == ESP_OK) {
        std::snprintf(g_environment, sizeof(g_environment), "%s", text ? text : "");
        g_environment_us = esp_timer_get_time();
        bsp_display_unlock();
    }
}

void ui_set_dashboard_details(const char *weather, const char *alerts, const char *whatsapp,
                              int alert_count, int whatsapp_count) {
    if (bsp_display_lock(100) == ESP_OK) {
        std::snprintf(g_weather_detail, sizeof(g_weather_detail), "%s", weather ? weather : "Unavailable");
        std::snprintf(g_alert_detail, sizeof(g_alert_detail), "%s", alerts ? alerts : "Unavailable");
        std::snprintf(g_whatsapp_detail, sizeof(g_whatsapp_detail), "%s", whatsapp ? whatsapp : "Unavailable");
        g_alert_count = alert_count;
        g_whatsapp_count = whatsapp_count;
        g_details_us = esp_timer_get_time();
        bsp_display_unlock();
    }
}

void ui_set_text(const char *text) {
    if (!conversation_visible()) return;
    if (caption_holds() || g_dancing) return;
    set_label(g_line2, text);
}

void ui_set_connected(bool connected, const char *detail) {
    if (connected) {
        // Reconnecting is a message that has to be taken back. Leaving it on
        // screen is what made the panel read "Reconnecting" forever after the
        // socket had actually come up.
        ui_set_lcd("Connected", "");
        ui_set_state("idle");
    } else {
        ui_set_state("disconnected");
        ui_set_lcd("Reconnecting", detail ? detail : "");
    }
}

void ui_set_latency(int milliseconds) {
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%d ms", milliseconds);
    set_label(g_latency, buffer);
}

// Icon, percent, and a colour that says which of the three states this is:
// on the cable and charging, on the cable and full, or running down the cell.
void ui_set_battery(int percent, bool on_usb, bool charging) {
    if (!g_battery) return;
    char text[24];
    lv_color_t colour = theme::text_secondary();
    if (percent < 0) {
        // No cell attached. The plug alone is the honest reading -- a battery
        // outline with nothing behind it would be inventing a measurement.
        std::snprintf(text, sizeof(text), "%s", LV_SYMBOL_USB);
    } else {
        const char *icon = percent >= 90   ? LV_SYMBOL_BATTERY_FULL
                           : percent >= 65 ? LV_SYMBOL_BATTERY_3
                           : percent >= 40 ? LV_SYMBOL_BATTERY_2
                           : percent >= 15 ? LV_SYMBOL_BATTERY_1
                                           : LV_SYMBOL_BATTERY_EMPTY;
        if (charging) {
            icon = LV_SYMBOL_CHARGE;
            colour = theme::accent();
        } else if (on_usb) {
            // Full, still on the cable: not draining, so not a warning.
            colour = theme::accent();
        } else if (percent < 15) {
            colour = theme::stop();
        }
        std::snprintf(text, sizeof(text), "%s %d%%", icon, percent);
    }
    if (bsp_display_lock(100) != ESP_OK) return;
    lv_obj_set_style_text_color(g_battery, colour, 0);
    lv_label_set_text(g_battery, text);
    bsp_display_unlock();
}

int ui_brightness() { return g_brightness; }

void ui_set_brightness(int percent) {
    g_brightness = std::clamp(percent, kMinBrightness, 100);
    bsp_display_brightness_set(g_brightness);
    save_brightness(g_brightness);
}

void ui_enter_dance(const char *mood) {
    if (bsp_display_lock(300) != ESP_OK) return;
    g_dancing = true;
    cancel_reaction_locked();
    // "Remove all elements from the display": every status row, the caption,
    // the transport and the talk button go, and so does the resting face --
    // the stage canvas is the only thing left.
    for (lv_obj_t *object : {g_line1, g_line2, g_speech, g_dashboard, g_latency, g_battery,
                             g_talk, g_media_row, g_media_title}) {
        if (object) lv_obj_add_flag(object, LV_OBJ_FLAG_HIDDEN);
    }
    face_set_visible(false);
    lv_obj_set_style_bg_color(g_screen, theme::persona_background(mood), 0);

    // One exception, and only for a moment: nothing on the stage says how to
    // stop it, and a gesture nobody knows about is not a control. It fades out
    // after two and a half seconds and the panel is bare from then on.
    if (!g_dance_hint) {
        g_dance_hint = lv_label_create(g_screen);
        lv_obj_set_style_text_font(g_dance_hint, &lv_font_montserrat_18, 0);
        lv_obj_set_style_text_color(g_dance_hint, theme::text_secondary(), 0);
        lv_label_set_text(g_dance_hint, "tap to stop");
        lv_obj_align(g_dance_hint, LV_ALIGN_BOTTOM_MID, 0, -74);
    }
    lv_obj_remove_flag(g_dance_hint, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(g_dance_hint);
    if (g_dance_hint_timer) lv_timer_delete(g_dance_hint_timer);
    g_dance_hint_timer = lv_timer_create(dance_hint_expired, 2500, nullptr);
    if (g_dance_hint_timer) lv_timer_set_repeat_count(g_dance_hint_timer, 1);
    bsp_display_unlock();
    ESP_LOGI(kTag, "stage on: the panel belongs to the dance");
}

void ui_exit_dance() {
    if (bsp_display_lock(300) != ESP_OK) return;
    g_dancing = false;
    if (g_dance_hint_timer) {
        lv_timer_delete(g_dance_hint_timer);
        g_dance_hint_timer = nullptr;
    }
    if (g_dance_hint) lv_obj_add_flag(g_dance_hint, LV_OBJ_FLAG_HIDDEN);
    face_set_visible(true);
    for (lv_obj_t *object : {g_line1, g_line2, g_latency, g_battery, g_talk}) {
        if (object) lv_obj_remove_flag(object, LV_OBJ_FLAG_HIDDEN);
    }
    // The caption goes back to the two status rows rather than to whatever
    // sentence was on screen before: a dance ends the turn, so that sentence
    // is stale by definition.
    g_caption_locked = false;
    show_speech(false);
    if (g_media_row && g_media_loaded) {
        lv_obj_remove_flag(g_media_row, LV_OBJ_FLAG_HIDDEN);
        if (g_media_title) lv_obj_remove_flag(g_media_title, LV_OBJ_FLAG_HIDDEN);
    }
    apply_persona_background(g_panel.pose());
    face_set_state(g_panel.pose(), "");
    refresh_talk_button();
    bsp_display_unlock();
    ESP_LOGI(kTag, "stage off: back to %s", g_panel.pose());
}

void ui_set_media(bool loaded, bool paused, const char *title) {
    g_media_loaded = loaded;
    g_media_paused = paused;
    if (!g_media_row) return;
    // Recorded, not drawn: the transport row must not reappear over the stage.
    // ui_exit_dance() restores it from these two flags.
    if (g_dancing) return;
    if (bsp_display_lock(100) != ESP_OK) return;
    if (loaded) {
        lv_obj_remove_flag(g_media_row, LV_OBJ_FLAG_HIDDEN);
        if (g_media_title) {
            lv_obj_remove_flag(g_media_title, LV_OBJ_FLAG_HIDDEN);
            if (title) lv_label_set_text(g_media_title, title);
        }
    } else {
        lv_obj_add_flag(g_media_row, LV_OBJ_FLAG_HIDDEN);
        if (g_media_title) lv_obj_add_flag(g_media_title, LV_OBJ_FLAG_HIDDEN);
    }
    if (g_media_play_label) {
        lv_label_set_text(g_media_play_label,
                          paused ? LV_SYMBOL_PLAY : LV_SYMBOL_PAUSE);
    }
    bsp_display_unlock();
}

}  // namespace kiki
