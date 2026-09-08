#include "kiki_wifi_analyzer.hpp"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>

#include "bsp/esp-bsp.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "kiki_theme.hpp"
#include "kiki_wifi_score.hpp"
#include "lwip/inet.h"
#include "ping/ping_sock.h"
#include "wifi_station.hpp"

namespace kiki {
namespace {

using wifiscore::ApView;

constexpr char kTag[] = "kiki_wifi_ui";
constexpr int32_t kScreen = 466;
constexpr int32_t kPanelW = 360;

// Everything below runs on a worker task, never on the LVGL callback: a survey
// parks the radio for a couple of seconds and a ping run for several more, and
// doing either inside an event handler freezes the panel and the touch queue
// with it. 4 kB covers the scan, the ping session and snprintf.
constexpr uint32_t kWorkerStack = 4096;

lv_obj_t *g_screen = nullptr;
lv_obj_t *g_return_screen = nullptr;
lv_obj_t *g_link_label = nullptr;
lv_obj_t *g_verdict_label = nullptr;
lv_obj_t *g_list = nullptr;
lv_obj_t *g_scan_button = nullptr;
lv_obj_t *g_speed_button = nullptr;
lv_obj_t *g_bars[wifiscore::kMaxChannel] = {};

std::atomic<bool> g_active{false};
// One worker at a time. The buttons are disabled while it runs, but a stuck
// touch event or a double tap must not be able to start a second scan on top of
// a join that is already halfway through swapping networks.
std::atomic<bool> g_busy{false};

// Survey scratch, in PSRAM, alive only while the screen is. 32 records is about
// 1.4 kB -- small, but internal DRAM is the scarce pool on this board (~33 kB
// free with the gateway up) and there is no reason for this to touch it.
WifiSurveyAp *g_survey = nullptr;
ApView *g_views = nullptr;
int g_survey_count = 0;
int g_recommended = -1;

// Defined below, forward-declared here because the list row callback needs it.
// A block-scope `extern` would bind to this anonymous namespace instead of the
// enclosing one and fail to link.
void request_join(int index);

// ---------------------------------------------------------------- helpers ---

void set_text(lv_obj_t *label, const char *text) {
    if (!label) return;
    if (bsp_display_lock(500) != ESP_OK) return;
    lv_label_set_text(label, text);
    bsp_display_unlock();
}

void set_buttons_enabled(bool enabled) {
    if (bsp_display_lock(500) != ESP_OK) return;
    for (lv_obj_t *button : {g_scan_button, g_speed_button}) {
        if (!button) continue;
        if (enabled) {
            lv_obj_remove_state(button, LV_STATE_DISABLED);
        } else {
            lv_obj_add_state(button, LV_STATE_DISABLED);
        }
    }
    bsp_display_unlock();
}

// The SSID this board is on right now, plus whether we hold its passphrase.
// Captured before any join attempt so a failure can be undone.
struct Anchor {
    char ssid[33];
    char password[65];
    bool valid;
};

Anchor capture_anchor() {
    Anchor anchor{};
    anchor.valid = wifi_station_current_ssid(anchor.ssid, sizeof(anchor.ssid)) &&
                   anchor.ssid[0] != '\0';
    if (anchor.valid) {
        // An open network has no passphrase, and password_for() legitimately
        // returns false for it; the empty string is the right credential.
        wifi_station_password_for(anchor.ssid, anchor.password, sizeof(anchor.password));
    }
    return anchor;
}

// ------------------------------------------------------------- the survey ---

void refresh_views() {
    g_recommended = -1;
    if (!g_survey || !g_views || g_survey_count <= 0) return;
    for (int i = 0; i < g_survey_count; ++i) {
        char password[65] = {};
        g_views[i].rssi = g_survey[i].rssi;
        g_views[i].channel = g_survey[i].channel;
        g_views[i].open = g_survey[i].open;
        g_views[i].known =
            wifi_station_password_for(g_survey[i].ssid, password, sizeof(password));
    }
    g_recommended = wifiscore::recommend(g_views, g_survey_count);
}

void draw_channel_bars() {
    if (bsp_display_lock(1000) != ESP_OK) return;
    float peak = 0.0f;
    float congestion[wifiscore::kMaxChannel] = {};
    for (int channel = wifiscore::kMinChannel; channel <= wifiscore::kMaxChannel; ++channel) {
        const float value = wifiscore::channel_congestion(g_views, g_survey_count, channel);
        congestion[channel - 1] = value;
        peak = std::max(peak, value);
    }
    const int quietest = wifiscore::best_channel(g_views, g_survey_count);
    for (int channel = wifiscore::kMinChannel; channel <= wifiscore::kMaxChannel; ++channel) {
        lv_obj_t *bar = g_bars[channel - 1];
        if (!bar) continue;
        const int32_t value =
            peak > 0.0f ? static_cast<int32_t>(100.0f * congestion[channel - 1] / peak) : 0;
        lv_bar_set_value(bar, value, LV_ANIM_OFF);
        // The quietest channel is called out in the mascot colour; everything
        // else is graded by how crowded it is, so the shape of the band is
        // readable at a glance rather than needing the numbers.
        lv_color_t colour = theme::accent();
        if (channel == quietest) colour = theme::mascot();
        else if (value > 66) colour = theme::stop();
        lv_obj_set_style_bg_color(bar, colour, LV_PART_INDICATOR);
    }
    bsp_display_unlock();
}

void draw_list() {
    if (bsp_display_lock(1000) != ESP_OK) return;
    lv_obj_clean(g_list);
    for (int i = 0; i < g_survey_count; ++i) {
        char row[96];
        // BSSID tail rather than the whole address: enough to tell two radios of
        // one SSID apart, which is the entire point, without eating the row.
        std::snprintf(row, sizeof(row), "%s  %d dBm  ch%u%s%s",
                      g_survey[i].ssid, static_cast<int>(g_survey[i].rssi),
                      static_cast<unsigned>(g_survey[i].channel),
                      g_views[i].known ? "  " LV_SYMBOL_OK : (g_survey[i].open ? "  open" : "  " LV_SYMBOL_CLOSE),
                      i == g_recommended ? "  <" : "");
        lv_obj_t *button = lv_list_add_button(g_list, nullptr, row);
        lv_obj_set_style_text_font(button, &lv_font_montserrat_14, 0);
        lv_obj_set_style_bg_color(button, i == g_recommended ? theme::surface()
                                                             : theme::background(), 0);
        lv_obj_set_style_text_color(button, theme::text_primary(), 0);
        lv_obj_set_user_data(button, reinterpret_cast<void *>(static_cast<intptr_t>(i)));
        lv_obj_add_event_cb(button, [](lv_event_t *event) {
            auto index = static_cast<int>(reinterpret_cast<intptr_t>(
                lv_obj_get_user_data(static_cast<lv_obj_t *>(lv_event_get_target(event)))));
            request_join(index);
        }, LV_EVENT_CLICKED, nullptr);
    }
    bsp_display_unlock();
}

void describe_result() {
    char text[256];
    if (g_survey_count <= 0) {
        std::snprintf(text, sizeof(text), "No networks heard.\nThe scan found nothing.");
        set_text(g_verdict_label, text);
        return;
    }
    const int quietest = wifiscore::best_channel(g_views, g_survey_count);
    if (g_recommended >= 0) {
        const WifiSurveyAp &pick = g_survey[g_recommended];
        const bool can_join = wifiscore::joinable(g_views[g_recommended]);
        std::snprintf(text, sizeof(text),
                      "Best here: %s\n%d dBm (%s)  ch%u  %s\n"
                      "%d APs on 2.4 GHz - quietest ch%d",
                      pick.ssid, static_cast<int>(pick.rssi),
                      wifiscore::strength_word(pick.rssi),
                      static_cast<unsigned>(pick.channel),
                      can_join ? "tap to join" : "needs its password",
                      g_survey_count, quietest);
    } else {
        std::snprintf(text, sizeof(text), "%d APs on 2.4 GHz - quietest ch%d",
                      g_survey_count, quietest);
    }
    set_text(g_verdict_label, text);
}

void update_link_label() {
    char ssid[33] = {};
    char text[128];
    const WifiHealth health = wifi_station_health();
    wifi_ap_record_t ap = {};
    const bool associated = wifi_station_current_ssid(ssid, sizeof(ssid)) && ssid[0];
    const bool have_ap = esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
    if (associated) {
        std::snprintf(text, sizeof(text), "%s  %d dBm (%s)  ch%u  2.4 GHz", ssid,
                      static_cast<int>(health.rssi), wifiscore::strength_word(health.rssi),
                      have_ap ? static_cast<unsigned>(ap.primary) : 0u);
    } else {
        std::snprintf(text, sizeof(text), "Not associated");
    }
    set_text(g_link_label, text);
}

void scan_task(void *) {
    set_text(g_verdict_label, "Scanning 2.4 GHz...");
    g_survey_count = wifi_station_survey(g_survey, kMaxSurveyResults);
    refresh_views();
    ESP_LOGI(kTag, "survey: %d access points", g_survey_count);
    draw_channel_bars();
    draw_list();
    describe_result();
    update_link_label();
    set_buttons_enabled(true);
    g_busy = false;
    vTaskDelete(nullptr);
}

// -------------------------------------------------------------- the speed ---

// A ping run, summarised. `sent` and `received` give the loss the average hides.
struct PingResult {
    uint32_t sent;
    uint32_t received;
    uint32_t total_ms;
    bool ran;
};

void ping_success(esp_ping_handle_t handle, void *args) {
    auto *result = static_cast<PingResult *>(args);
    uint32_t elapsed = 0;
    esp_ping_get_profile(handle, ESP_PING_PROF_TIMEGAP, &elapsed, sizeof(elapsed));
    result->received += 1;
    result->total_ms += elapsed;
}

void ping_end(esp_ping_handle_t handle, void *args) {
    auto *result = static_cast<PingResult *>(args);
    esp_ping_get_profile(handle, ESP_PING_PROF_REQUEST, &result->sent, sizeof(result->sent));
}

// Blocking ping of one address. Returns loss and mean round trip.
//
// Ping rather than a throughput test on purpose. A megabits figure needs a
// server willing to send megabytes, and inventing one would put a new network
// dependency next to the thing this screen exists to protect. Latency and loss
// to the router are what actually predict whether speech will feel instant, and
// they need nothing but the link itself.
PingResult run_ping(const ip_addr_t &target, uint32_t count) {
    PingResult result{};
    esp_ping_config_t config = ESP_PING_DEFAULT_CONFIG();
    config.target_addr = target;
    config.count = count;
    config.timeout_ms = 1500;
    config.interval_ms = 300;
    config.task_stack_size = 3072;

    esp_ping_callbacks_t callbacks = {};
    callbacks.cb_args = &result;
    callbacks.on_ping_success = ping_success;
    callbacks.on_ping_end = ping_end;

    esp_ping_handle_t handle = nullptr;
    if (esp_ping_new_session(&config, &callbacks, &handle) != ESP_OK) return result;
    if (esp_ping_start(handle) == ESP_OK) {
        // Bounded wait: count intervals plus a timeout's worth of slack, so a
        // black-holed address cannot hold the worker task forever.
        const uint32_t budget = count * (config.interval_ms + config.timeout_ms) + 1000;
        const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(budget);
        while (xTaskGetTickCount() < deadline) {
            vTaskDelay(pdMS_TO_TICKS(100));
            uint32_t sent = 0;
            esp_ping_get_profile(handle, ESP_PING_PROF_REQUEST, &sent, sizeof(sent));
            if (sent >= count) {
                vTaskDelay(pdMS_TO_TICKS(config.timeout_ms));
                break;
            }
        }
        esp_ping_stop(handle);
        result.ran = true;
    }
    esp_ping_delete_session(handle);
    if (result.sent == 0) result.sent = count;
    return result;
}

void speed_task(void *) {
    set_text(g_verdict_label, "Testing the link...");

    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t info = {};
    char text[256];
    if (!netif || esp_netif_get_ip_info(netif, &info) != ESP_OK || info.gw.addr == 0) {
        set_text(g_verdict_label, "No IP address.\nNot associated to anything.");
        set_buttons_enabled(true);
        g_busy = false;
        vTaskDelete(nullptr);
        return;
    }

    ip_addr_t router = {};
    router.type = IPADDR_TYPE_V4;
    router.u_addr.ip4.addr = info.gw.addr;
    const PingResult local = run_ping(router, 5);

    ip_addr_t internet = {};
    internet.type = IPADDR_TYPE_V4;
    internet.u_addr.ip4.addr = ipaddr_addr("8.8.8.8");
    const PingResult remote = run_ping(internet, 5);

    const WifiHealth health = wifi_station_health();
    char router_text[16];
    std::snprintf(router_text, sizeof(router_text), IPSTR, IP2STR(&info.gw));

    // Loss is reported next to the average because they fail differently: a
    // 20 ms average with two packets missing is a far worse link for speech
    // than a steady 90 ms, and an average alone hides exactly that.
    auto mean = [](const PingResult &r) -> int {
        return r.received > 0 ? static_cast<int>(r.total_ms / r.received) : -1;
    };
    const int local_ms = mean(local);
    const int remote_ms = mean(remote);
    std::snprintf(text, sizeof(text),
                  "Router %s\n  %d ms, %lu/%lu lost\n"
                  "Internet\n  %d ms, %lu/%lu lost\nSignal %d dBm (%s)",
                  router_text, local_ms,
                  static_cast<unsigned long>(local.sent - local.received),
                  static_cast<unsigned long>(local.sent),
                  remote_ms,
                  static_cast<unsigned long>(remote.sent - remote.received),
                  static_cast<unsigned long>(remote.sent),
                  static_cast<int>(health.rssi),
                  wifiscore::strength_word(health.rssi));
    set_text(g_verdict_label, text);
    ESP_LOGI(kTag, "link test: router %d ms, internet %d ms, rssi %d", local_ms, remote_ms,
             static_cast<int>(health.rssi));
    update_link_label();
    set_buttons_enabled(true);
    g_busy = false;
    vTaskDelete(nullptr);
}

// --------------------------------------------------------------- the join ---

int g_join_index = -1;

void join_task(void *) {
    const int index = g_join_index;
    char text[224];
    if (index < 0 || index >= g_survey_count) {
        set_buttons_enabled(true);
        g_busy = false;
        vTaskDelete(nullptr);
        return;
    }

    const Anchor anchor = capture_anchor();
    char ssid[33];
    std::snprintf(ssid, sizeof(ssid), "%s", g_survey[index].ssid);
    char password[65] = {};
    wifi_station_password_for(ssid, password, sizeof(password));

    std::snprintf(text, sizeof(text), "Joining %s...", ssid);
    set_text(g_verdict_label, text);

    const esp_err_t joined = wifi_station_connect(ssid, password, 15000);
    if (joined == ESP_OK) {
        wifi_station_remember(ssid, password);
        wifi_station_save(ssid, password);
        ESP_LOGW(kTag, "joined \"%s\" from the analyser; restarting", ssid);
        // Restart rather than carry on, for the same reason "Change Wi-Fi"
        // does: the gateway socket, its TLS session and the DHCP lease all
        // belong to the network we just left. Letting them discover that
        // themselves is a minute of half-working reconnects; four seconds of
        // reboot is deterministic.
        std::snprintf(text, sizeof(text), "Joined %s.\nRestarting...", ssid);
        set_text(g_verdict_label, text);
        vTaskDelay(pdMS_TO_TICKS(1500));
        esp_restart();
    } else if (anchor.valid) {
        // The attempt disconnected us; put it back. This is the branch the
        // whole screen is designed around -- everything else is a convenience,
        // and this is the part that must not fail quietly.
        ESP_LOGW(kTag, "\"%s\" did not come up; restoring \"%s\"", ssid, anchor.ssid);
        esp_err_t restored = wifi_station_connect(anchor.ssid, anchor.password, 20000);
        if (restored != ESP_OK) {
            restored = wifi_station_connect(anchor.ssid, anchor.password, 20000);
        }
        if (restored == ESP_OK) {
            std::snprintf(text, sizeof(text), "%s did not come up.\nBack on %s.", ssid,
                          anchor.ssid);
        } else {
            // Both failed. Say so plainly and point at the flow that can always
            // recover, rather than leaving a screen that looks like it worked.
            std::snprintf(text, sizeof(text),
                          "%s failed and %s did not\ncome back. Use Settings >\n"
                          "Change Wi-Fi.",
                          ssid, anchor.ssid);
            ESP_LOGE(kTag, "could not restore \"%s\"", anchor.ssid);
        }
    } else {
        std::snprintf(text, sizeof(text), "%s did not come up.", ssid);
    }

    set_text(g_verdict_label, text);
    update_link_label();
    set_buttons_enabled(true);
    g_busy = false;
    vTaskDelete(nullptr);
}

bool start_worker(TaskFunction_t task, const char *name) {
    bool expected = false;
    if (!g_busy.compare_exchange_strong(expected, true)) return false;
    set_buttons_enabled(false);
    if (xTaskCreate(task, name, kWorkerStack, nullptr, 4, nullptr) != pdPASS) {
        g_busy = false;
        set_buttons_enabled(true);
        return false;
    }
    return true;
}

// ------------------------------------------------------------ the screen ----

void release_buffers() {
    heap_caps_free(g_survey);
    heap_caps_free(g_views);
    g_survey = nullptr;
    g_views = nullptr;
    g_survey_count = 0;
    g_recommended = -1;
}

void close_screen() {
    // A worker still holds pointers into the survey; let it finish rather than
    // freeing the ground out from under it.
    if (g_busy.load()) return;
    g_active = false;
    if (g_return_screen) lv_screen_load(g_return_screen);
    if (g_screen) {
        lv_obj_delete(g_screen);
        g_screen = nullptr;
        g_link_label = nullptr;
        g_verdict_label = nullptr;
        g_list = nullptr;
        g_scan_button = nullptr;
        g_speed_button = nullptr;
        std::memset(g_bars, 0, sizeof(g_bars));
    }
    release_buffers();
}

lv_obj_t *make_button(const char *text, int32_t x, int32_t y, int32_t width,
                      lv_color_t colour, lv_event_cb_t handler) {
    lv_obj_t *button = lv_button_create(g_screen);
    lv_obj_set_size(button, width, 44);
    lv_obj_align(button, LV_ALIGN_TOP_MID, x, y);
    lv_obj_set_style_radius(button, 22, 0);
    lv_obj_set_style_bg_color(button, colour, 0);
    lv_obj_add_event_cb(button, handler, LV_EVENT_CLICKED, nullptr);
    lv_obj_t *label = lv_label_create(button);
    lv_label_set_text(label, text);
    lv_obj_set_style_text_font(label, &lv_font_montserrat_18, 0);
    lv_obj_center(label);
    return button;
}

void build() {
    g_screen = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(g_screen, theme::background(), 0);
    lv_obj_set_style_border_width(g_screen, 0, 0);
    lv_obj_remove_flag(g_screen, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *title = lv_label_create(g_screen);
    lv_label_set_text(title, "Wi-Fi");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(title, theme::mascot(), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 40);

    g_link_label = lv_label_create(g_screen);
    lv_label_set_text(g_link_label, "...");
    lv_obj_set_style_text_font(g_link_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(g_link_label, theme::text_secondary(), 0);
    lv_obj_set_width(g_link_label, kPanelW);
    lv_obj_set_style_text_align(g_link_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(g_link_label, LV_ALIGN_TOP_MID, 0, 74);

    // The channel picture. Thirteen thin bars is the whole 2.4 GHz band, and
    // the shape of it says more than any number: one tall spike means a
    // neighbour to avoid, a flat wall means the band itself is full.
    constexpr int32_t kBarWidth = 20;
    constexpr int32_t kBarGap = 6;
    const int32_t span = wifiscore::kMaxChannel * (kBarWidth + kBarGap) - kBarGap;
    for (int channel = wifiscore::kMinChannel; channel <= wifiscore::kMaxChannel; ++channel) {
        lv_obj_t *bar = lv_bar_create(g_screen);
        lv_obj_set_size(bar, kBarWidth, 46);
        lv_bar_set_range(bar, 0, 100);
        lv_bar_set_value(bar, 0, LV_ANIM_OFF);
        lv_obj_set_style_bg_color(bar, theme::surface(), LV_PART_MAIN);
        lv_obj_set_style_bg_color(bar, theme::accent(), LV_PART_INDICATOR);
        lv_obj_set_style_radius(bar, 3, LV_PART_MAIN);
        lv_obj_align(bar, LV_ALIGN_TOP_MID,
                     -span / 2 + (channel - 1) * (kBarWidth + kBarGap) + kBarWidth / 2, 108);
        g_bars[channel - 1] = bar;
    }
    lv_obj_t *legend = lv_label_create(g_screen);
    lv_label_set_text(legend, "ch 1        6        11");
    lv_obj_set_style_text_font(legend, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(legend, theme::text_secondary(), 0);
    lv_obj_align(legend, LV_ALIGN_TOP_MID, 0, 156);

    g_verdict_label = lv_label_create(g_screen);
    lv_label_set_text(g_verdict_label, "Tap Scan to survey the band.");
    lv_obj_set_style_text_font(g_verdict_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(g_verdict_label, theme::text_primary(), 0);
    lv_obj_set_width(g_verdict_label, kPanelW);
    lv_obj_set_style_text_align(g_verdict_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(g_verdict_label, LV_ALIGN_TOP_MID, 0, 180);

    g_list = lv_list_create(g_screen);
    lv_obj_set_size(g_list, kPanelW, 150);
    lv_obj_set_pos(g_list, (kScreen - kPanelW) / 2, 262);
    lv_obj_set_style_bg_color(g_list, theme::background(), 0);
    lv_obj_set_style_border_width(g_list, 0, 0);

    g_scan_button = make_button(LV_SYMBOL_REFRESH "  Scan", -95, 418, 150, theme::accent(),
                                [](lv_event_t *) { start_worker(scan_task, "kiki_wifi_scan"); });
    g_speed_button = make_button(LV_SYMBOL_CHARGE "  Speed", 60, 418, 130, theme::surface(),
                                 [](lv_event_t *) { start_worker(speed_task, "kiki_wifi_speed"); });
    make_button(LV_SYMBOL_CLOSE, 165, 418, 60, theme::surface(),
                [](lv_event_t *) { close_screen(); });
}

void request_join(int index) {
    if (index < 0 || index >= g_survey_count) return;
    if (!wifiscore::joinable(g_views[index])) {
        char text[192];
        std::snprintf(text, sizeof(text),
                      "%s needs a password.\nUse Settings > Change Wi-Fi\nto enter it.",
                      g_survey[index].ssid);
        set_text(g_verdict_label, text);
        return;
    }
    g_join_index = index;
    start_worker(join_task, "kiki_wifi_join");
}

}  // namespace

bool wifi_analyzer_active() { return g_active.load(); }

void wifi_analyzer_open(lv_obj_t *return_screen) {
    if (g_active.load()) return;
    g_return_screen = return_screen;
    g_survey = static_cast<WifiSurveyAp *>(
        heap_caps_malloc(sizeof(WifiSurveyAp) * kMaxSurveyResults, MALLOC_CAP_SPIRAM));
    g_views = static_cast<ApView *>(
        heap_caps_malloc(sizeof(ApView) * kMaxSurveyResults, MALLOC_CAP_SPIRAM));
    if (!g_survey || !g_views) {
        // Degrade to not opening at all rather than to a screen that cannot
        // scan: an analyser with no survey is a dead end with a Close button.
        ESP_LOGE(kTag, "no PSRAM for the Wi-Fi analyser");
        release_buffers();
        return;
    }
    g_survey_count = 0;
    g_recommended = -1;
    build();
    lv_screen_load(g_screen);
    g_active = true;
    update_link_label();
}

}  // namespace kiki
