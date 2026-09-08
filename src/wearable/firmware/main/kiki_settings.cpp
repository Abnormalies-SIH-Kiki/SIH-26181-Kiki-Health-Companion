#include "kiki_settings.hpp"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>

#include "audio_pipeline.hpp"
#include "bsp/esp-bsp.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gateway_client.hpp"
#include "kiki_power.hpp"
#include "kiki_care_ui.hpp"
#include "kiki_wifi_analyzer.hpp"
#include "kiki_theme.hpp"
#include "kiki_ui.hpp"
#include "kiki_wearable.hpp"
#include "nvs.h"
#include "wifi_station.hpp"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_settings";
constexpr int32_t kScreen = 466;
constexpr int32_t kPanelW = 320;

// ir_controls.py's VOL_STEP.
constexpr int kVolumeStep = 10;
// Digital gain lives on the gateway, ahead of the codec: volume is how loud the
// speaker is driven, gain is how hard the PCM is pushed before it gets there.
// Both matter, and only one of them was reachable without SSH.
constexpr float kGainStep = 0.2f;
constexpr float kGainMin = 1.0f;
constexpr float kGainMax = 5.0f;
// The panel is the biggest single load on the board, so this is a battery
// control as much as a comfort one.
constexpr int kBrightnessStep = 10;
constexpr char kSettingsNamespace[] = "kiki_settings";
constexpr char kBargeInKey[] = "barge_in";
constexpr char kFallKey[] = "fall_alerts";
constexpr char kMovementKey[] = "move_checks";

lv_obj_t *g_screen = nullptr;
lv_obj_t *g_return_screen = nullptr;
lv_obj_t *g_list = nullptr;
lv_obj_t *g_volume_label = nullptr;
lv_obj_t *g_gain_label = nullptr;
lv_obj_t *g_brightness_label = nullptr;
lv_obj_t *g_barge_in_label = nullptr;
lv_obj_t *g_shutdown_label = nullptr;
lv_obj_t *g_health_screen = nullptr;
lv_obj_t *g_health_status = nullptr;
lv_obj_t *g_health_progress = nullptr;
lv_obj_t *g_health_hr = nullptr;
lv_obj_t *g_health_spo2 = nullptr;
lv_obj_t *g_health_quality = nullptr;
lv_timer_t *g_health_timer = nullptr;
// Shutting down cannot be undone from the panel -- the panel is one of the
// things that goes off -- so it takes two taps, and the arming lapses.
lv_timer_t *g_shutdown_timer = nullptr;
bool g_shutdown_armed = false;
std::atomic<bool> g_active{false};
int g_volume = 100;
float g_gain = 3.2f;
std::atomic<bool> g_barge_in_enabled{true};
std::atomic<bool> g_fall_alerts_enabled{true};
std::atomic<bool> g_movement_checks_enabled{true};
lv_obj_t *g_fall_label = nullptr;
lv_obj_t *g_movement_label = nullptr;

void update_barge_in_label() {
    if (!g_barge_in_label) return;
    lv_label_set_text(g_barge_in_label,
                      g_barge_in_enabled.load() ? "Barge-in  On" : "Barge-in  Off");
}

void update_care_labels() {
    if (g_fall_label) {
        lv_label_set_text(g_fall_label, g_fall_alerts_enabled.load()
                          ? "Fall alerts  On" : "Fall alerts  Off");
    }
    if (g_movement_label) {
        lv_label_set_text(g_movement_label, g_movement_checks_enabled.load()
                          ? "Movement checks  On" : "Movement checks  Off");
    }
}

void save_care_switch(const char *key, bool value) {
    nvs_handle_t handle;
    if (nvs_open(kSettingsNamespace, NVS_READWRITE, &handle) != ESP_OK) {
        ESP_LOGW(kTag, "could not persist %s", key);
        return;
    }
    nvs_set_u8(handle, key, value ? 1 : 0);
    nvs_commit(handle);
    nvs_close(handle);
}

void apply_care_switches() {
    save_care_switch(kFallKey, g_fall_alerts_enabled.load());
    save_care_switch(kMovementKey, g_movement_checks_enabled.load());
    update_care_labels();
    // Tell the gateway too. Not because the board needs it to -- both switches
    // are enforced here -- but so health mode can SAY that movement checks are
    // off instead of reporting that the band sent nothing, which is the same
    // sentence a broken sensor would produce.
    char extra[96];
    std::snprintf(extra, sizeof(extra),
                  "\"fall_alerts\":%s,\"movement_checks\":%s",
                  g_fall_alerts_enabled.load() ? "true" : "false",
                  g_movement_checks_enabled.load() ? "true" : "false");
    gateway_client_send_event("care_options", extra);
    ESP_LOGI(kTag, "fall alerts %s, movement checks %s",
             g_fall_alerts_enabled.load() ? "on" : "off",
             g_movement_checks_enabled.load() ? "on" : "off");
}

void save_barge_in() {
    nvs_handle_t handle;
    if (nvs_open(kSettingsNamespace, NVS_READWRITE, &handle) != ESP_OK) {
        ESP_LOGW(kTag, "could not persist barge-in setting");
        return;
    }
    nvs_set_u8(handle, kBargeInKey, g_barge_in_enabled.load() ? 1 : 0);
    nvs_commit(handle);
    nvs_close(handle);
}

void apply_barge_in() {
    save_barge_in();
    update_barge_in_label();
    char extra[32];
    std::snprintf(extra, sizeof(extra), "\"enabled\":%s",
                  g_barge_in_enabled.load() ? "true" : "false");
    gateway_client_send_event("set_barge_in", extra);
    ESP_LOGI(kTag, "voice barge-in %s",
             g_barge_in_enabled.load() ? "enabled" : "disabled");
}

void apply_volume() {
    g_volume = std::clamp(g_volume, 0, 100);
    // Apply locally so the change is audible immediately, and tell the gateway
    // so it survives into config and into the legacy runtime's own idea of the
    // speaker level.
    audio_pipeline_set_volume(g_volume);
    char extra[32];
    std::snprintf(extra, sizeof(extra), "\"percent\":%d", g_volume);
    gateway_client_send_event("set_volume", extra);
    if (g_volume_label) {
        char text[32];
        std::snprintf(text, sizeof(text), "Volume  %d%%", g_volume);
        lv_label_set_text(g_volume_label, text);
    }
}

void apply_gain() {
    g_gain = std::clamp(g_gain, kGainMin, kGainMax);
    char extra[40];
    std::snprintf(extra, sizeof(extra), "\"value\":%.2f", static_cast<double>(g_gain));
    gateway_client_send_event("set_gain", extra);
    if (g_gain_label) {
        char text[32];
        std::snprintf(text, sizeof(text), "Gain  %.1fx", static_cast<double>(g_gain));
        lv_label_set_text(g_gain_label, text);
    }
}

void apply_brightness(int delta) {
    ui_set_brightness(ui_brightness() + delta);
    if (g_brightness_label) {
        char text[32];
        std::snprintf(text, sizeof(text), "Brightness  %d%%", ui_brightness());
        lv_label_set_text(g_brightness_label, text);
    }
}

// Called both from the lapse timer and directly. A one-shot timer deletes
// itself after its callback, so only the direct call may delete it.
void disarm_shutdown(lv_timer_t *timer) {
    g_shutdown_armed = false;
    if (!timer && g_shutdown_timer) lv_timer_delete(g_shutdown_timer);
    g_shutdown_timer = nullptr;
    if (g_shutdown_label) lv_label_set_text(g_shutdown_label, "Shut down");
}

// Cutting the rails straight from the LVGL callback would kill the panel
// mid-repaint and leave the gateway to discover the loss as a timeout -- which,
// after all the reconnect debugging, is exactly the kind of drop that should
// never be ambiguous again. So: say goodbye, let it flush, then pull the plug.
void shutdown_task(void *) {
    gateway_client_send_event("shutdown");
    vTaskDelay(pdMS_TO_TICKS(600));
    if (!power_shutdown()) {
        // Kiki is still running, and saying so is better than a dead menu item.
        ESP_LOGE(kTag, "PMIC refused the power-off; still running");
        if (bsp_display_lock(200) == ESP_OK) {
            if (g_shutdown_label) lv_label_set_text(g_shutdown_label, "Shutdown failed");
            bsp_display_unlock();
        }
        vTaskDelete(nullptr);
    }
    // The rails are going away underneath this task.
    vTaskDelay(portMAX_DELAY);
}

void request_shutdown() {
    if (!g_shutdown_armed) {
        g_shutdown_armed = true;
        if (g_shutdown_label) lv_label_set_text(g_shutdown_label, "Tap again to confirm");
        if (g_shutdown_timer) lv_timer_delete(g_shutdown_timer);
        g_shutdown_timer = lv_timer_create(disarm_shutdown, 5000, nullptr);
        lv_timer_set_repeat_count(g_shutdown_timer, 1);
        return;
    }
    if (g_shutdown_timer) {
        lv_timer_delete(g_shutdown_timer);
        g_shutdown_timer = nullptr;
    }
    g_shutdown_armed = false;
    if (g_shutdown_label) lv_label_set_text(g_shutdown_label, "Goodbye");
    ESP_LOGW(kTag, "shutting down at the user's request");
    xTaskCreate(shutdown_task, "kiki_shutdown", 3072, nullptr, 5, nullptr);
}

void close_settings() {
    g_active = false;
    wearable_cancel_measurement();
    if (g_health_timer) {
        lv_timer_delete(g_health_timer);
        g_health_timer = nullptr;
    }
    if (g_return_screen) lv_screen_load(g_return_screen);
}

void update_health_screen(lv_timer_t *) {
    if (!g_health_status) return;
    WearableMeasurementStatus status{};
    wearable_get_measurement_status(&status);
    lv_bar_set_value(g_health_progress, status.progress_percent, LV_ANIM_ON);

    const char *message = "Ready";
    switch (status.state) {
        case WearableMeasurementState::WaitingForContact:
            message = "Wear Kiki snugly on your wrist";
            break;
        case WearableMeasurementState::WaitingForStillness:
            message = "Keep your wrist still";
            break;
        case WearableMeasurementState::Measuring: {
            static char measuring[48];
            std::snprintf(measuring, sizeof(measuring), "Measuring... %u%%",
                          status.progress_percent);
            message = measuring;
            break;
        }
        case WearableMeasurementState::Complete:
            message = "Measurement complete";
            break;
        case WearableMeasurementState::Failed:
            message = !status.sensor_available
                          ? "Health sensor unavailable"
                          : (status.heart_rate > 0.0F
                                 ? "Low signal - best estimate shown"
                                 : "No pulse found - adjust fit and retry");
            break;
        case WearableMeasurementState::Idle:
            break;
    }
    lv_label_set_text(g_health_status, message);

    char line[64];
    if (status.heart_rate > 0.0F) {
        std::snprintf(line, sizeof(line), "Heart rate  %.0f bpm",
                      static_cast<double>(status.heart_rate));
    } else {
        std::snprintf(line, sizeof(line), "Heart rate  -- bpm");
    }
    lv_label_set_text(g_health_hr, line);
    if (status.spo2_experimental > 0.0F) {
        std::snprintf(line, sizeof(line), "Experimental SpO2  %.0f%%",
                      static_cast<double>(status.spo2_experimental));
    } else {
        std::snprintf(line, sizeof(line), "Experimental SpO2  --%%");
    }
    lv_label_set_text(g_health_spo2, line);
    std::snprintf(line, sizeof(line), "Signal %s   Steps %lu", status.quality,
                  static_cast<unsigned long>(status.steps));
    lv_label_set_text(g_health_quality, line);
}

void health_back_cb(lv_event_t *) {
    wearable_cancel_measurement();
    if (g_health_timer) {
        lv_timer_delete(g_health_timer);
        g_health_timer = nullptr;
    }
    if (g_return_screen && !g_active) lv_screen_load(g_return_screen);
    else if (g_screen) lv_screen_load(g_screen);
}

void health_retry_cb(lv_event_t *) {
    WearableMeasurementStatus status{};
    wearable_get_measurement_status(&status);
    if (status.state == WearableMeasurementState::Complete ||
        status.state == WearableMeasurementState::Failed ||
        status.state == WearableMeasurementState::Idle) {
        wearable_request_measurement();
        update_health_screen(nullptr);
    }
}

void build_health_screen() {
    if (g_health_screen) return;
    g_health_screen = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(g_health_screen, theme::background(), 0);
    lv_obj_set_style_border_width(g_health_screen, 0, 0);
    lv_obj_remove_flag(g_health_screen, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *title = lv_label_create(g_health_screen);
    lv_label_set_text(title, "Health check");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(title, theme::mascot(), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 45);

    g_health_status = lv_label_create(g_health_screen);
    lv_obj_set_width(g_health_status, 400);
    lv_obj_set_style_text_align(g_health_status, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(g_health_status, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(g_health_status, theme::text_primary(), 0);
    lv_obj_align(g_health_status, LV_ALIGN_TOP_MID, 0, 105);

    g_health_progress = lv_bar_create(g_health_screen);
    lv_obj_set_size(g_health_progress, 320, 14);
    lv_obj_align(g_health_progress, LV_ALIGN_TOP_MID, 0, 148);
    lv_bar_set_range(g_health_progress, 0, 100);
    lv_obj_set_style_bg_color(g_health_progress, theme::surface(), LV_PART_MAIN);
    lv_obj_set_style_bg_color(g_health_progress, theme::mascot(), LV_PART_INDICATOR);

    g_health_hr = lv_label_create(g_health_screen);
    lv_obj_set_style_text_font(g_health_hr, &lv_font_montserrat_28, 0);
    lv_obj_set_style_text_color(g_health_hr, theme::text_primary(), 0);
    lv_obj_align(g_health_hr, LV_ALIGN_TOP_MID, 0, 190);

    g_health_spo2 = lv_label_create(g_health_screen);
    lv_obj_set_style_text_font(g_health_spo2, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(g_health_spo2, theme::text_primary(), 0);
    lv_obj_align(g_health_spo2, LV_ALIGN_TOP_MID, 0, 242);

    g_health_quality = lv_label_create(g_health_screen);
    lv_obj_set_style_text_font(g_health_quality, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(g_health_quality, theme::text_secondary(), 0);
    lv_obj_align(g_health_quality, LV_ALIGN_TOP_MID, 0, 292);

    lv_obj_t *note = lv_label_create(g_health_screen);
    lv_label_set_text(note, "Wellness only - SpO2 is uncalibrated");
    lv_obj_set_style_text_font(note, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(note, theme::text_secondary(), 0);
    lv_obj_align(note, LV_ALIGN_TOP_MID, 0, 335);

    lv_obj_t *retry = lv_button_create(g_health_screen);
    lv_obj_set_size(retry, 195, 50);
    lv_obj_align(retry, LV_ALIGN_BOTTOM_LEFT, 30, -42);
    lv_obj_set_style_bg_color(retry, theme::mascot(), 0);
    lv_obj_add_event_cb(retry, health_retry_cb, LV_EVENT_CLICKED, nullptr);
    lv_obj_t *retry_label = lv_label_create(retry);
    lv_label_set_text(retry_label, "Measure again");
    lv_obj_set_style_text_font(retry_label, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(retry_label, theme::background(), 0);
    lv_obj_center(retry_label);

    lv_obj_t *back = lv_button_create(g_health_screen);
    lv_obj_set_size(back, 195, 50);
    lv_obj_align(back, LV_ALIGN_BOTTOM_RIGHT, -30, -42);
    lv_obj_set_style_bg_color(back, theme::surface(), 0);
    lv_obj_add_event_cb(back, health_back_cb, LV_EVENT_CLICKED, nullptr);
    lv_obj_t *back_label = lv_label_create(back);
    lv_label_set_text(back_label, LV_SYMBOL_LEFT "  Back");
    lv_obj_set_style_text_font(back_label, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(back_label, theme::text_primary(), 0);
    lv_obj_center(back_label);
}

void open_health_measurement() {
    build_health_screen();
    wearable_request_measurement();
    update_health_screen(nullptr);
    if (!g_health_timer) g_health_timer = lv_timer_create(update_health_screen, 250, nullptr);
    lv_screen_load(g_health_screen);
}

// Re-provisioning blocks on a scan and a connect, so it cannot run on the LVGL
// task -- that task is the one that has to keep painting the picker.
void reprovision_task(void *) {
    wifi_station_forget();
    ESP_LOGI(kTag, "Wi-Fi credentials cleared; restarting into setup");
    vTaskDelay(pdMS_TO_TICKS(400));
    esp_restart();
}

void item_cb(lv_event_t *event) {
    const auto action = reinterpret_cast<intptr_t>(lv_event_get_user_data(event));
    switch (action) {
        case 0: g_volume += kVolumeStep; apply_volume(); break;
        case 1: g_volume -= kVolumeStep; apply_volume(); break;
        case 5: g_gain += kGainStep; apply_gain(); break;
        case 6: g_gain -= kGainStep; apply_gain(); break;
        case 7: apply_brightness(kBrightnessStep); break;
        case 8: apply_brightness(-kBrightnessStep); break;
        case 10:
            g_barge_in_enabled = !g_barge_in_enabled.load();
            apply_barge_in();
            break;
        case 11: open_health_measurement(); break;
        case 13:
            g_fall_alerts_enabled = !g_fall_alerts_enabled.load();
            apply_care_switches();
            break;
        case 14:
            g_movement_checks_enabled = !g_movement_checks_enabled.load();
            apply_care_switches();
            break;
        case 12:
            // The care screen takes the display from here and returns to
            // whatever this settings screen was opened over, not to settings:
            // coming back into a menu you were only passing through is a step
            // nobody wants on the way out.
            g_active = false;
            care_ui_open(g_return_screen);
            break;
        case 15:
            // Same hand-off as the care screen: the analyser takes the display
            // and returns to whatever settings was opened over. Unlike case 2
            // it does NOT restart -- a survey leaves the association alone, and
            // only an actual join (which the analyser confirms first) reboots.
            g_active = false;
            wifi_analyzer_open(g_return_screen);
            break;
        case 2:
            // Restarting into the setup flow is deliberate: tearing the station
            // down underneath a live gateway socket to run a scan is a much
            // worse failure than a four-second reboot.
            xTaskCreate(reprovision_task, "kiki_reprov", 3072, nullptr, 5, nullptr);
            break;
        case 3: esp_restart(); break;
        case 9: request_shutdown(); return;  // must not fall through to disarm
        default: close_settings(); break;
    }
    // Touching anything else means the shutdown tap was not followed through.
    if (g_shutdown_armed) disarm_shutdown(nullptr);
}

lv_obj_t *add_item(const char *symbol, const char *text, intptr_t action) {
    lv_obj_t *button = lv_list_add_button(g_list, symbol, text);
    lv_obj_set_style_text_font(button, &lv_font_montserrat_24, 0);
    lv_obj_set_style_bg_color(button, theme::surface(), 0);
    lv_obj_set_style_text_color(button, theme::text_primary(), 0);
    lv_obj_set_style_border_width(button, 0, 0);
    lv_obj_set_style_margin_bottom(button, 4, 0);
    lv_obj_add_event_cb(button, item_cb, LV_EVENT_CLICKED,
                        reinterpret_cast<void *>(action));
    return button;
}

void build() {
    if (g_screen) return;
    g_screen = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(g_screen, theme::background(), 0);
    lv_obj_set_style_border_width(g_screen, 0, 0);
    lv_obj_remove_flag(g_screen, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *title = lv_label_create(g_screen);
    lv_label_set_text(title, "Settings");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(title, theme::mascot(), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 62);

    g_list = lv_list_create(g_screen);
    lv_obj_set_size(g_list, kPanelW, 280);
    lv_obj_set_pos(g_list, (kScreen - kPanelW) / 2, 112);
    lv_obj_set_style_bg_color(g_list, theme::background(), 0);
    lv_obj_set_style_border_width(g_list, 0, 0);

    g_volume_label = lv_obj_get_child(add_item(LV_SYMBOL_VOLUME_MAX, "Volume", 0), -1);
    add_item(LV_SYMBOL_DOWN, "Volume down", 1);
    g_gain_label = lv_obj_get_child(add_item(LV_SYMBOL_UP, "Gain", 5), -1);
    add_item(LV_SYMBOL_DOWN, "Gain down", 6);
    g_brightness_label = lv_obj_get_child(add_item(LV_SYMBOL_EYE_OPEN, "Brightness", 7), -1);
    add_item(LV_SYMBOL_DOWN, "Brightness down", 8);
    g_barge_in_label = lv_obj_get_child(add_item(LV_SYMBOL_AUDIO, "Barge-in", 10), -1);
    add_item(LV_SYMBOL_REFRESH, "Measure HR + SpO2", 11);
    add_item(LV_SYMBOL_LIST, "Care plan", 12);
    g_fall_label = lv_obj_get_child(add_item(LV_SYMBOL_WARNING, "Fall alerts", 13), -1);
    g_movement_label =
        lv_obj_get_child(add_item(LV_SYMBOL_SHUFFLE, "Movement checks", 14), -1);
    add_item(LV_SYMBOL_WIFI, "Wi-Fi analyser", 15);
    add_item(LV_SYMBOL_WIFI, "Change Wi-Fi", 2);
    add_item(LV_SYMBOL_REFRESH, "Restart Kiki", 3);
    g_shutdown_label = lv_obj_get_child(add_item(LV_SYMBOL_POWER, "Shut down", 9), -1);
    add_item(LV_SYMBOL_CLOSE, "Close", 4);
}

}  // namespace

bool settings_active() { return g_active.load(); }

void settings_init() {
    nvs_handle_t handle;
    if (nvs_open(kSettingsNamespace, NVS_READONLY, &handle) != ESP_OK) return;
    uint8_t enabled = 1;
    if (nvs_get_u8(handle, kBargeInKey, &enabled) == ESP_OK) {
        g_barge_in_enabled = enabled != 0;
    }
    uint8_t fall = 1;
    if (nvs_get_u8(handle, kFallKey, &fall) == ESP_OK) {
        g_fall_alerts_enabled = fall != 0;
    }
    uint8_t movement = 1;
    if (nvs_get_u8(handle, kMovementKey, &movement) == ESP_OK) {
        g_movement_checks_enabled = movement != 0;
    }
    nvs_close(handle);
    ESP_LOGI(kTag, "loaded voice barge-in %s",
             g_barge_in_enabled.load() ? "enabled" : "disabled");
}

bool settings_barge_in_enabled() { return g_barge_in_enabled.load(); }

bool settings_fall_alerts_enabled() { return g_fall_alerts_enabled.load(); }

bool settings_movement_checks_enabled() { return g_movement_checks_enabled.load(); }

void settings_measure_heart(lv_obj_t *return_screen) {
    g_return_screen = return_screen;
    g_active = false;
    open_health_measurement();
}

void settings_open(lv_obj_t *return_screen, int volume_percent, float gain) {
    g_return_screen = return_screen;
    g_volume = std::clamp(volume_percent, 0, 100);
    g_gain = std::clamp(gain, kGainMin, kGainMax);
    build();
    if (g_gain_label) {
        char text[32];
        std::snprintf(text, sizeof(text), "Gain  %.1fx", static_cast<double>(g_gain));
        lv_label_set_text(g_gain_label, text);
    }
    if (g_volume_label) {
        char text[32];
        std::snprintf(text, sizeof(text), "Volume  %d%%", g_volume);
        lv_label_set_text(g_volume_label, text);
    }
    apply_brightness(0);
    update_barge_in_label();
    update_care_labels();
    g_active = true;
    lv_screen_load(g_screen);
}

}  // namespace kiki
