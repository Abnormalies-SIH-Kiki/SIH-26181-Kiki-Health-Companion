#include "kiki_setup.hpp"

#include <atomic>
#include <cstdio>
#include <cstring>

#include "bsp/esp-bsp.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "kiki_face.hpp"
#include "kiki_theme.hpp"
#include "lvgl.h"
#include "wifi_station.hpp"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_setup";

// Every element is sized against the inscribed disc of the 466x466 circle: at
// distance d from the centre the usable half-width is sqrt(233^2 - d^2). The
// keyboard is the binding constraint, which is why it is 290 wide and not the
// full panel.
constexpr int32_t kScreen = 466;
constexpr int32_t kPanelW = 320;
constexpr int32_t kKeyboardW = 290;
constexpr int32_t kKeyboardH = 228;
constexpr int32_t kKeyboardY = 185;

lv_obj_t *g_screen = nullptr;
lv_obj_t *g_title = nullptr;
lv_obj_t *g_subtitle = nullptr;
lv_obj_t *g_list = nullptr;
lv_obj_t *g_password_view = nullptr;
lv_obj_t *g_password_label = nullptr;
lv_obj_t *g_keys = nullptr;

EventGroupHandle_t g_events = nullptr;
constexpr EventBits_t kChoiceMade = BIT0;
constexpr EventBits_t kPasswordReady = BIT1;
constexpr EventBits_t kCancelled = BIT2;
constexpr EventBits_t kRescan = BIT3;

std::atomic<bool> g_active{false};
WifiAp g_networks[kMaxScanResults];
int g_network_count = 0;
int g_selected = -1;
char g_password[65] = {};
size_t g_password_length = 0;
bool g_shift = false;
int g_page = 0;  // 0 lowercase, 1 uppercase, 2 digits/symbols

// A 6-column matrix rather than lv_keyboard. The panel is 1.75 inches across,
// so a full QWERTY row of ten keys works out at about 3 mm per key -- too small
// to hit reliably. Six columns gives ~4.6 mm keys at the cost of an extra page.
const char *const kPageLower[] = {
    "a", "b", "c", "d", "e", "f", "\n",
    "g", "h", "i", "j", "k", "l", "\n",
    "m", "n", "o", "p", "q", "r", "\n",
    "s", "t", "u", "v", "w", "x", "\n",
    "y", "z", "ABC", "123", " ", LV_SYMBOL_BACKSPACE, "\n",
    LV_SYMBOL_CLOSE, LV_SYMBOL_OK, "",
};
const char *const kPageUpper[] = {
    "A", "B", "C", "D", "E", "F", "\n",
    "G", "H", "I", "J", "K", "L", "\n",
    "M", "N", "O", "P", "Q", "R", "\n",
    "S", "T", "U", "V", "W", "X", "\n",
    "Y", "Z", "abc", "123", " ", LV_SYMBOL_BACKSPACE, "\n",
    LV_SYMBOL_CLOSE, LV_SYMBOL_OK, "",
};
const char *const kPageSymbols[] = {
    "1", "2", "3", "4", "5", "0", "\n",
    "6", "7", "8", "9", "-", "_", "\n",
    "!", "@", "#", "$", "%", "&", "\n",
    "*", "(", ")", "+", "=", "?", "\n",
    ".", ",", "abc", "/", " ", LV_SYMBOL_BACKSPACE, "\n",
    LV_SYMBOL_CLOSE, LV_SYMBOL_OK, "",
};

void refresh_password_label() {
    if (!g_password_label) return;
    // The password is shown in full and never logged. Entering a WPA2 key blind
    // on a touch panel this small is how people end up blaming the AP for a
    // typo, so it stays visible and correctable on screen only.
    lv_label_set_text(g_password_label, g_password_length ? g_password : "(empty)");
}

void set_page(int page) {
    g_page = page;
    const char *const *map = page == 1 ? kPageUpper : (page == 2 ? kPageSymbols : kPageLower);
    lv_buttonmatrix_set_map(g_keys, map);
}

void key_cb(lv_event_t *event) {
    lv_obj_t *matrix = static_cast<lv_obj_t *>(lv_event_get_target(event));
    const uint32_t id = lv_buttonmatrix_get_selected_button(matrix);
    const char *text = lv_buttonmatrix_get_button_text(matrix, id);
    if (!text) return;

    if (std::strcmp(text, LV_SYMBOL_OK) == 0) {
        xEventGroupSetBits(g_events, kPasswordReady);
        return;
    }
    if (std::strcmp(text, LV_SYMBOL_CLOSE) == 0) {
        xEventGroupSetBits(g_events, kCancelled);
        return;
    }
    if (std::strcmp(text, LV_SYMBOL_BACKSPACE) == 0) {
        if (g_password_length > 0) g_password[--g_password_length] = '\0';
        refresh_password_label();
        return;
    }
    if (std::strcmp(text, "ABC") == 0) { set_page(1); return; }
    if (std::strcmp(text, "abc") == 0) { set_page(0); return; }
    if (std::strcmp(text, "123") == 0) { set_page(2); return; }

    const size_t length = std::strlen(text);
    if (g_password_length + length < sizeof(g_password)) {
        std::memcpy(g_password + g_password_length, text, length);
        g_password_length += length;
        g_password[g_password_length] = '\0';
        // Shift is one-shot, like every other soft keyboard.
        if (g_page == 1) set_page(0);
    }
    refresh_password_label();
}

void network_cb(lv_event_t *event) {
    const auto index = reinterpret_cast<intptr_t>(lv_event_get_user_data(event));
    if (index < 0) {
        xEventGroupSetBits(g_events, kRescan);
        return;
    }
    g_selected = static_cast<int>(index);
    xEventGroupSetBits(g_events, kChoiceMade);
}

lv_obj_t *make_label(lv_obj_t *parent, const lv_font_t *font, int32_t y, uint32_t colour) {
    lv_obj_t *label = lv_label_create(parent);
    lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
    lv_obj_set_width(label, kPanelW);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, lv_color_hex(colour), 0);
    lv_obj_set_pos(label, (kScreen - kPanelW) / 2, y);
    return label;
}

void build_screen() {
    if (g_screen) return;
    g_screen = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(g_screen, theme::background(), 0);
    lv_obj_set_style_border_width(g_screen, 0, 0);
    lv_obj_remove_flag(g_screen, LV_OBJ_FLAG_SCROLLABLE);

    g_title = make_label(g_screen, &lv_font_montserrat_32, 60, theme::kMascot);
    g_subtitle = make_label(g_screen, &lv_font_montserrat_18, 100, theme::kTextSecondary);

    g_list = lv_list_create(g_screen);
    lv_obj_set_size(g_list, kPanelW, 268);
    lv_obj_set_pos(g_list, (kScreen - kPanelW) / 2, 130);
    lv_obj_set_style_bg_color(g_list, theme::background(), 0);
    lv_obj_set_style_border_width(g_list, 0, 0);

    g_password_view = lv_obj_create(g_screen);
    lv_obj_set_size(g_password_view, kScreen, kScreen);
    lv_obj_set_pos(g_password_view, 0, 0);
    lv_obj_set_style_bg_color(g_password_view, theme::background(), 0);
    lv_obj_set_style_border_width(g_password_view, 0, 0);
    lv_obj_remove_flag(g_password_view, LV_OBJ_FLAG_SCROLLABLE);

    g_password_label = lv_label_create(g_password_view);
    lv_label_set_long_mode(g_password_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(g_password_label, kKeyboardW);
    lv_obj_set_style_text_align(g_password_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(g_password_label, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(g_password_label, theme::text_primary(), 0);
    lv_obj_set_pos(g_password_label, (kScreen - kKeyboardW) / 2, 120);

    g_keys = lv_buttonmatrix_create(g_password_view);
    lv_obj_set_size(g_keys, kKeyboardW, kKeyboardH);
    lv_obj_set_pos(g_keys, (kScreen - kKeyboardW) / 2, kKeyboardY);
    lv_obj_set_style_bg_opa(g_keys, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(g_keys, 0, 0);
    lv_obj_set_style_pad_all(g_keys, 2, 0);
    lv_obj_set_style_bg_color(g_keys, theme::surface(), LV_PART_ITEMS);
    lv_obj_set_style_text_color(g_keys, theme::text_primary(), LV_PART_ITEMS);
    lv_obj_set_style_border_width(g_keys, 0, LV_PART_ITEMS);
    lv_obj_set_style_radius(g_keys, 6, LV_PART_ITEMS);
    lv_obj_add_event_cb(g_keys, key_cb, LV_EVENT_VALUE_CHANGED, nullptr);
    set_page(0);

    lv_obj_add_flag(g_password_view, LV_OBJ_FLAG_HIDDEN);
}

void show_status(const char *title, const char *subtitle, bool show_list) {
    if (bsp_display_lock(1000) != ESP_OK) return;
    lv_label_set_text(g_title, title);
    lv_label_set_text(g_subtitle, subtitle ? subtitle : "");
    if (show_list) lv_obj_remove_flag(g_list, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(g_list, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(g_password_view, LV_OBJ_FLAG_HIDDEN);
    bsp_display_unlock();
}

void populate_list() {
    if (bsp_display_lock(1000) != ESP_OK) return;
    lv_obj_clean(g_list);
    for (int i = 0; i < g_network_count; ++i) {
        char row[64];
        std::snprintf(row, sizeof(row), "%.32s%s", g_networks[i].ssid,
                      g_networks[i].open ? "" : "  " LV_SYMBOL_CLOSE);
        lv_obj_t *button = lv_list_add_button(g_list, nullptr, row);
        lv_obj_set_style_text_font(button, &lv_font_montserrat_18, 0);
        lv_obj_set_style_bg_color(button, theme::surface(), 0);
        lv_obj_set_style_text_color(button, theme::text_primary(), 0);
        lv_obj_set_style_margin_bottom(button, 4, 0);
        lv_obj_add_event_cb(button, network_cb, LV_EVENT_CLICKED,
                            reinterpret_cast<void *>(static_cast<intptr_t>(i)));
    }
    lv_obj_t *rescan = lv_list_add_button(g_list, LV_SYMBOL_REFRESH, "Scan again");
    lv_obj_set_style_text_font(rescan, &lv_font_montserrat_18, 0);
    lv_obj_set_style_bg_color(rescan, theme::surface(), 0);
    lv_obj_set_style_text_color(rescan, theme::mascot(), 0);
    lv_obj_add_event_cb(rescan, network_cb, LV_EVENT_CLICKED,
                        reinterpret_cast<void *>(static_cast<intptr_t>(-1)));
    bsp_display_unlock();
}

void show_keyboard(const char *ssid) {
    g_password[0] = '\0';
    g_password_length = 0;
    if (bsp_display_lock(1000) != ESP_OK) return;
    lv_obj_add_flag(g_list, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(g_title, ssid);
    lv_label_set_text(g_subtitle, "Password");
    set_page(0);
    refresh_password_label();
    lv_obj_remove_flag(g_password_view, LV_OBJ_FLAG_HIDDEN);
    bsp_display_unlock();
}

}  // namespace

bool setup_active() { return g_active.load(); }

namespace {
// How long the picker waits for a finger before going back to trying the
// networks it already knows. Nobody may be standing there -- the board lives on
// a shelf and is expected to come back by itself after an AP reboots.
constexpr uint32_t kPickerIdleMs = 25000;
constexpr uint32_t kJoinTimeoutMs = 20000;

// Every network we have reason to believe in, best guess first: the ones this
// board has actually joined before, most recent first and only if the scan can
// see them, then the compiled-in one.
// True when this SSID is one the radio can currently hear. Trying a network
// that is not there costs a 20-second join timeout, and a board that moves
// between home and college would otherwise pay that on every arrival.
bool in_range(const WifiAp *scan, int count, const char *ssid) {
    for (int i = 0; i < count; ++i) {
        if (std::strcmp(scan[i].ssid, ssid) == 0) return true;
    }
    return false;
}

bool try_network(const char *ssid, const char *password, const char *why) {
    if (!ssid || !*ssid) return false;
    ESP_LOGI(kTag, "joining \"%s\" (%s)", ssid, why);
    if (wifi_station_connect(ssid, password, kJoinTimeoutMs) != ESP_OK) {
        ESP_LOGW(kTag, "\"%s\" did not come up", ssid);
        return false;
    }
    // Whatever actually worked goes to the front of the list, so the next boot
    // in this place starts with it. This is the whole of "Kiki remembers the
    // network she is on": it is recorded on success, from every path, rather
    // than only when someone types it into the panel.
    wifi_station_remember(ssid, password);
    return true;
}

bool join_known_networks() {
#if CONFIG_KIKI_WIFI_FORCE_BUILTIN
    // Pinned build: the built-in network or nothing. Said out loud every boot,
    // because the symptom of forgetting this is set -- a board that will not
    // join the network you just picked on its own panel -- is otherwise
    // extremely confusing.
    ESP_LOGW(kTag, "pinned to the built-in network \"%s\"", CONFIG_KIKI_WIFI_SSID);
    return try_network(CONFIG_KIKI_WIFI_SSID, CONFIG_KIKI_WIFI_PASSWORD, "pinned");
#else
    // One scan, then only the networks it can see. The scan costs a couple of
    // seconds; each network that is not there costs ten times that.
    static WifiAp scan[kMaxScanResults];
    const int found = wifi_station_scan(scan, kMaxScanResults);
    if (found > 0) {
        for (int i = 0; i < kKnownNetworks; ++i) {
            char ssid[33] = {};
            char password[65] = {};
            if (!wifi_station_known(i, ssid, sizeof(ssid), password, sizeof(password))) {
                continue;
            }
            if (!in_range(scan, found, ssid)) {
                ESP_LOGI(kTag, "known network \"%s\" is not in range", ssid);
                continue;
            }
            if (try_network(ssid, password, "remembered, in range")) return true;
        }
    } else {
        // A scan can fail on a busy radio. Falling back to the old blind
        // attempt is slower but never worse than not trying at all.
        ESP_LOGW(kTag, "no scan results; trying the saved network blind");
        char ssid[33] = {};
        char password[65] = {};
        if (wifi_station_known(0, ssid, sizeof(ssid), password, sizeof(password)) &&
            try_network(ssid, password, "saved")) {
            return true;
        }
    }

    // ...but never after "Change Wi-Fi". Forgetting is a deliberate request for
    // the picker, and quietly reconnecting to the built-in network instead is
    // indistinguishable from the button being broken.
    //
    // The compiled-in network is tried even when a saved one exists, and that
    // second attempt is the whole point: a saved network can be *wrong*, not
    // just absent. This board had "Kiki" -- an open AP provisioned once while
    // travelling -- sitting in NVS. At home that AP is in range, accepts the
    // association and drops it 73 ms later, so the saved network failed every
    // single boot and the board never once tried the house network it was built
    // with. It sat on the provisioning screen, silent, until someone noticed.
    if (!wifi_station_forgotten() && CONFIG_KIKI_WIFI_SSID[0] != '\0') {
        return try_network(CONFIG_KIKI_WIFI_SSID, CONFIG_KIKI_WIFI_PASSWORD,
                           "built in");
    }
    return false;
#endif
}
}  // namespace

esp_err_t setup_ensure_wifi() {
    if (wifi_station_init() != ESP_OK) {
        ESP_LOGE(kTag, "Wi-Fi could not be initialised");
        return ESP_FAIL;
    }

    // A provisioned network gets one quiet attempt before the panel appears, so
    // an ordinary reboot never makes the user re-enter anything.
    if (join_known_networks()) return ESP_OK;

    g_active = true;
    if (!g_events) g_events = xEventGroupCreate();
    if (bsp_display_lock(2000) == ESP_OK) {
        build_screen();
        lv_screen_load(g_screen);
        bsp_display_unlock();
    }

    const char *failure = "";
    while (true) {
        show_status("Wi-Fi", "Scanning...", false);
        g_network_count = wifi_station_scan(g_networks, kMaxScanResults);
        if (g_network_count == 0) {
            show_status("Wi-Fi", "No networks found", false);
            vTaskDelay(pdMS_TO_TICKS(1500));
            continue;
        }
        populate_list();
        show_status("Choose a network", failure, true);
        failure = "";

        xEventGroupClearBits(g_events, kChoiceMade | kRescan | kCancelled | kPasswordReady);
        // Wait forever only when the user asked to be here. After "Change
        // Wi-Fi" there is nothing left to retry and someone is standing at the
        // panel; the timeout exists for the *other* case, where the board is
        // unattended on a shelf and its network went away by itself.
        const TickType_t picker_wait =
            wifi_station_forgotten() ? portMAX_DELAY : pdMS_TO_TICKS(kPickerIdleMs);
        const EventBits_t choice = xEventGroupWaitBits(
            g_events, kChoiceMade | kRescan, pdTRUE, pdFALSE, picker_wait);
        // The wait used to be portMAX_DELAY. That is the bug that turned a
        // transient Wi-Fi failure into a board that was simply gone: it stopped
        // here, before the gateway client and before any telemetry task, so
        // there was not even a heartbeat to say it was waiting. An unattended
        // device must keep trying on its own.
        if (choice == 0) {
            show_status("Wi-Fi", "Retrying known networks", false);
            if (join_known_networks()) {
                g_active = false;
                return ESP_OK;
            }
            continue;
        }
        if (choice & kRescan) continue;

        const WifiAp &network = g_networks[g_selected];
        if (!network.open) {
            show_keyboard(network.ssid);
            const EventBits_t entered = xEventGroupWaitBits(
                g_events, kPasswordReady | kCancelled, pdTRUE, pdFALSE, portMAX_DELAY);
            if (entered & kCancelled) continue;
        } else {
            g_password[0] = '\0';
            g_password_length = 0;
        }

        char message[64];
        std::snprintf(message, sizeof(message), "Connecting to %s", network.ssid);
        show_status("Wi-Fi", message, false);
        if (wifi_station_connect(network.ssid, g_password, 20000) == ESP_OK) {
            wifi_station_save(network.ssid, g_password);
            // Do not leave the key sitting in RAM after it has been stored.
            std::memset(g_password, 0, sizeof(g_password));
            g_password_length = 0;
            g_active = false;
            return ESP_OK;
        }
        std::memset(g_password, 0, sizeof(g_password));
        g_password_length = 0;
        failure = network.open ? "Could not connect" : "Wrong password?";
    }
}

}  // namespace kiki
