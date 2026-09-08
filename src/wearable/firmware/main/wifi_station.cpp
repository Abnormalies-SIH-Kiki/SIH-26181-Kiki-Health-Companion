#include "wifi_station.hpp"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "nvs.h"
#include "nvs_flash.h"

namespace kiki {
namespace {

// Wi-Fi health, kept across reconnects so the next hello can explain the last
// failure. See wifi_station_health().
std::atomic<uint16_t> g_disconnects{0};
std::atomic<uint8_t> g_last_reason{0};

constexpr char kTag[] = "kiki_wifi";
constexpr char kNamespace[] = "kiki_wifi";
constexpr char kKeySsid[] = "ssid";
constexpr char kKeyPassword[] = "pass";
// Set when the user explicitly chose "Change Wi-Fi". Without it, erasing the
// stored credentials just let the build-time Kconfig SSID take over on the next
// boot, so the picker never appeared and the whole action looked like a plain
// restart.
constexpr char kKeyForgotten[] = "forgotten";

EventGroupHandle_t g_events = nullptr;
constexpr EventBits_t kConnected = BIT0;
constexpr EventBits_t kFailed = BIT1;

// While the provisioning screen is driving explicit connect attempts, the
// automatic retry must stay out of the way: otherwise a failed password
// triggers an endless reconnect loop that races the next attempt and the panel
// can never report a clean failure.
std::atomic<bool> g_auto_reconnect{false};
std::atomic<bool> g_connected{false};
bool g_started = false;

// True only between esp_wifi_connect() and the end of the wait it belongs to.
// wifi_station_connect() tears down any previous association first, and that
// esp_wifi_disconnect() delivers STA_DISCONNECTED *asynchronously* -- often
// after the event bits have been cleared for the new attempt. Counting it would
// fail a connection that is in fact still proceeding, and a spurious failure
// here is expensive: it is what drops the board onto the provisioning screen.
std::atomic<bool> g_connect_in_flight{false};

void event_handler(void *, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (g_auto_reconnect.load()) esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        g_connected = false;
        // Name the reason. Without it a wrong password, an AP that is not
        // there, and an AP that kicked us straight back off are the same
        // silence -- which is how a board that had quietly saved the wrong
        // network looked identical to a flaky link for hours.
        const auto *info = static_cast<wifi_event_sta_disconnected_t *>(data);
        if (info) {
            ESP_LOGW(kTag, "disconnected from %.*s, reason %d",
                     static_cast<int>(info->ssid_len), info->ssid, info->reason);
            // Recorded as well as logged. A disconnect kills the link the log
            // travels on, so the only place this can be read is the *next*
            // hello -- which is exactly when someone is asking why the board
            // keeps coming back.
            g_last_reason = static_cast<uint8_t>(info->reason);
        }
        g_disconnects.fetch_add(1);
        if (g_events && g_connect_in_flight.load()) xEventGroupSetBits(g_events, kFailed);
        if (g_auto_reconnect.load()) esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        g_connected = true;
        // The lease alone does not say whether we can look a hostname up, and a
        // network that hands out no DNS server fails resolution in five
        // milliseconds -- fast enough to read as "gateway unreachable" on every
        // slot rather than as the name lookup problem it is. Say which servers
        // we actually got, so the next occurrence is one line to diagnose.
        for (int i = 0; i < 3; ++i) {
            esp_netif_dns_info_t dns = {};
            if (esp_netif_get_dns_info(esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"),
                                       static_cast<esp_netif_dns_type_t>(i), &dns) != ESP_OK) {
                continue;
            }
            if (dns.ip.u_addr.ip4.addr == 0) continue;
            ESP_LOGI(kTag, "dns%d " IPSTR, i, IP2STR(&dns.ip.u_addr.ip4));
        }
        if (g_events) xEventGroupSetBits(g_events, kConnected);
    }
}

bool read_nvs(const char *key, char *out, size_t size) {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READONLY, &handle) != ESP_OK) return false;
    size_t length = size;
    const esp_err_t err = nvs_get_str(handle, key, out, &length);
    nvs_close(handle);
    return err == ESP_OK && out[0] != '\0';
}

}  // namespace

esp_err_t wifi_station_init() {
    if (g_started) return ESP_OK;
    ESP_RETURN_ON_ERROR(esp_netif_init(), kTag, "netif");
    const esp_err_t loop = esp_event_loop_create_default();
    if (loop != ESP_OK && loop != ESP_ERR_INVALID_STATE) return loop;
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init), kTag, "wifi init");
    g_events = xEventGroupCreate();
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, event_handler, nullptr);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, event_handler, nullptr);
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), kTag, "station mode");
    // The radio never sleeps, on USB or on battery. IDF defaults to
    // WIFI_PS_MIN_MODEM, so this has to be said explicitly every boot.
    //
    // Modem sleep dozes between DTIM beacons, which means every inbound packet
    // waits for the next wake -- hundreds of milliseconds, on a device whose
    // whole job is answering the moment it is spoken to. Kiki is a
    // conversational device first and a battery device second: a reply that
    // arrives late is the failure everyone notices, and longer runtime is not
    // worth buying with it.
    ESP_RETURN_ON_ERROR(esp_wifi_set_ps(WIFI_PS_NONE), kTag, "disable power save");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), kTag, "wifi start");
    g_started = true;
    return ESP_OK;
}

bool forgotten() {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READONLY, &handle) != ESP_OK) return false;
    uint8_t value = 0;
    const esp_err_t err = nvs_get_u8(handle, kKeyForgotten, &value);
    nvs_close(handle);
    return err == ESP_OK && value != 0;
}

bool wifi_station_saved_ssid(char *out, size_t size) {
    if (read_nvs(kKeySsid, out, size)) return true;
    // The Kconfig network is a first-boot convenience, not a network to fall
    // back onto after the user has deliberately forgotten one.
    if (!forgotten() && CONFIG_KIKI_WIFI_SSID[0] != '\0') {
        std::snprintf(out, size, "%s", CONFIG_KIKI_WIFI_SSID);
        return true;
    }
    out[0] = '\0';
    return false;
}

bool wifi_station_forgotten() { return forgotten(); }

bool wifi_station_has_credentials() {
    char ssid[33] = {};
    return wifi_station_saved_ssid(ssid, sizeof(ssid));
}

namespace {

// Slot 0 keeps the original "ssid"/"pass" keys so a board upgrading from a
// single-network build still finds the network it already had.
void slot_keys(int index, char *ssid_key, char *pass_key, size_t size) {
    if (index == 0) {
        std::snprintf(ssid_key, size, "%s", kKeySsid);
        std::snprintf(pass_key, size, "%s", kKeyPassword);
        return;
    }
    std::snprintf(ssid_key, size, "%s%d", kKeySsid, index);
    std::snprintf(pass_key, size, "%s%d", kKeyPassword, index);
}

struct Network {
    char ssid[33];
    char password[65];
};

}  // namespace

bool wifi_station_known(int index, char *ssid, size_t ssid_size,
                        char *password, size_t password_size) {
    if (index < 0 || index >= kKnownNetworks || !ssid || ssid_size == 0) return false;
    char ssid_key[16];
    char pass_key[16];
    slot_keys(index, ssid_key, pass_key, sizeof(ssid_key));
    ssid[0] = '\0';
    if (!read_nvs(ssid_key, ssid, ssid_size)) return false;
    if (password && password_size) {
        password[0] = '\0';
        read_nvs(pass_key, password, password_size);
    }
    return true;
}

void wifi_station_remember(const char *ssid, const char *password) {
    if (!ssid || !*ssid) return;

    // Read the list, drop any entry for this SSID, put it at the front. Done
    // in memory first so a power cut mid-write cannot leave a half-shifted
    // list -- the worst case is one stale slot, never a corrupt one.
    Network list[kKnownNetworks] = {};
    int count = 0;
    std::snprintf(list[count].ssid, sizeof(list[0].ssid), "%s", ssid);
    std::snprintf(list[count].password, sizeof(list[0].password), "%s",
                  password ? password : "");
    ++count;
    for (int i = 0; i < kKnownNetworks && count < kKnownNetworks; ++i) {
        Network existing = {};
        if (!wifi_station_known(i, existing.ssid, sizeof(existing.ssid),
                                existing.password, sizeof(existing.password))) {
            continue;
        }
        if (std::strcmp(existing.ssid, ssid) == 0) continue;  // the one we just promoted
        list[count++] = existing;
    }

    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READWRITE, &handle) != ESP_OK) return;
    for (int i = 0; i < kKnownNetworks; ++i) {
        char ssid_key[16];
        char pass_key[16];
        slot_keys(i, ssid_key, pass_key, sizeof(ssid_key));
        if (i < count) {
            nvs_set_str(handle, ssid_key, list[i].ssid);
            nvs_set_str(handle, pass_key, list[i].password);
        } else {
            nvs_erase_key(handle, ssid_key);
            nvs_erase_key(handle, pass_key);
        }
    }
    // Joining a network is the opposite of forgetting one.
    nvs_set_u8(handle, kKeyForgotten, 0);
    nvs_commit(handle);
    nvs_close(handle);
    // Never log the password, and never log it indirectly by logging length.
    ESP_LOGI(kTag, "remembered \"%s\" (%d network%s known)", ssid, count,
             count == 1 ? "" : "s");
}

void wifi_station_save(const char *ssid, const char *password) {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READWRITE, &handle) != ESP_OK) {
        ESP_LOGE(kTag, "could not open NVS to save credentials");
        return;
    }
    nvs_close(handle);
    // One path in and out of the known-network list, so a network chosen on the
    // panel is remembered exactly the way one joined automatically is.
    // remember() also clears the "forgotten" latch.
    wifi_station_remember(ssid, password);
}

void wifi_station_forget() {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READWRITE, &handle) != ESP_OK) return;
    // Every slot. "Change Wi-Fi" is a deliberate request for the picker, and
    // leaving remembered networks behind would let the board quietly rejoin one
    // of them -- which is indistinguishable from the button being broken, the
    // exact failure the "forgotten" latch exists to prevent.
    for (int i = 0; i < kKnownNetworks; ++i) {
        char ssid_key[16];
        char pass_key[16];
        slot_keys(i, ssid_key, pass_key, sizeof(ssid_key));
        nvs_erase_key(handle, ssid_key);
        nvs_erase_key(handle, pass_key);
    }
    nvs_set_u8(handle, kKeyForgotten, 1);
    nvs_commit(handle);
    nvs_close(handle);
}

esp_err_t wifi_station_connect(const char *ssid, const char *password, uint32_t timeout_ms) {
    if (!ssid || !*ssid) return ESP_ERR_INVALID_ARG;
    ESP_RETURN_ON_ERROR(wifi_station_init(), kTag, "init");

    g_auto_reconnect = false;
    esp_wifi_disconnect();

    wifi_config_t config = {};
    std::snprintf(reinterpret_cast<char *>(config.sta.ssid), sizeof(config.sta.ssid), "%s", ssid);
    std::snprintf(reinterpret_cast<char *>(config.sta.password), sizeof(config.sta.password),
                  "%s", password ? password : "");
    // An empty password means an open network; demanding WPA2 there would make
    // every open AP look like a wrong password.
    config.sta.threshold.authmode =
        (password && *password) ? WIFI_AUTH_WPA_WPA2_PSK : WIFI_AUTH_OPEN;
    config.sta.pmf_cfg.capable = true;
    config.sta.pmf_cfg.required = false;
    // Pick the strongest AP with this name, not the first one heard.
    //
    // IDF defaults to WIFI_FAST_SCAN, which stops at the first AP matching the
    // SSID. On a home network with one AP that is free and correct. On a campus
    // network where dozens of APs share one SSID it is a coin toss, and it is
    // how this board ended up associated at -79 dBm on NSUT_WIFI (2026-09-07)
    // while the hotspot in the same room measured -41. An all-channel scan
    // sorted by signal costs about a second, once, at connect time.
    config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    config.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    // Only honoured with an all-channel scan. ONE retry, not three.
    //
    // The sorted list is only worth having if the join can actually walk down
    // it. `kJoinTimeoutMs` is 20 s for the whole attempt, and an all-channel
    // scan already spends ~2.5 s of that; at three retries each, a couple of
    // unresponsive APs eat the entire budget and the join fails having never
    // reached a radio that would have accepted it. Sorting by signal puts the
    // STRONGEST first, and on a steered campus network the strongest is not
    // always the one willing to take another client. One retry, then move on.
    config.sta.failure_retry_cnt = 1;
    // 802.11k (radio measurement) and 802.11v (BSS transition management).
    //
    // Managed networks move clients between APs by deauthenticating them --
    // reason 36, "requested from peer STA as it is leaving the BSS", seen twice
    // on NSUT_WIFI on 2026-09-07. Without these the board only learns that it
    // was kicked, and pays a full scan, auth, DHCP, TLS and websocket rebuild
    // through the tunnel to get back. With them the AP's transition request and
    // its neighbour list are understood, so the move is directed rather than
    // rediscovered from nothing.
    //
    // Deliberately no bssid_set here. The API clears a pinned BSSID at the
    // first roam once BTM is on, and pinning the board to one AP is exactly
    // what makes a steered network kick it.
    config.sta.rm_enabled = 1;
    config.sta.btm_enabled = 1;
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &config), kTag, "wifi config");

    ESP_LOGI(kTag, "connecting to \"%s\" (%s)", ssid,
             (password && *password) ? "wpa2" : "open");
    xEventGroupClearBits(g_events, kConnected | kFailed);
    g_connect_in_flight = true;
    const esp_err_t started = esp_wifi_connect();
    if (started != ESP_OK) {
        g_connect_in_flight = false;
        ESP_LOGE(kTag, "connect could not be started: %s", esp_err_to_name(started));
        return started;
    }
    const EventBits_t bits = xEventGroupWaitBits(
        g_events, kConnected | kFailed, pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_ms));
    g_connect_in_flight = false;
    if (bits & kConnected) {
        // Only now arm the automatic retry, so an ordinary roam or AP reboot is
        // recovered without the panel being involved.
        g_auto_reconnect = true;
        return ESP_OK;
    }
    esp_wifi_disconnect();
    return (bits & kFailed) ? ESP_ERR_WIFI_NOT_CONNECT : ESP_ERR_TIMEOUT;
}

esp_err_t wifi_station_connect_saved(uint32_t timeout_ms) {
    char ssid[33] = {};
    char password[65] = {};
    if (!read_nvs(kKeySsid, ssid, sizeof(ssid))) {
        std::snprintf(ssid, sizeof(ssid), "%s", CONFIG_KIKI_WIFI_SSID);
        std::snprintf(password, sizeof(password), "%s", CONFIG_KIKI_WIFI_PASSWORD);
    } else {
        read_nvs(kKeyPassword, password, sizeof(password));
    }
    if (!ssid[0]) return ESP_ERR_NOT_FOUND;
    return wifi_station_connect(ssid, password, timeout_ms);
}

int wifi_station_scan(WifiAp *out, int max_results) {
    if (!out || max_results <= 0) return 0;
    if (wifi_station_init() != ESP_OK) return 0;

    wifi_scan_config_t scan = {};
    scan.show_hidden = false;
    if (esp_wifi_scan_start(&scan, true) != ESP_OK) return 0;

    uint16_t found = 0;
    esp_wifi_scan_get_ap_num(&found);
    if (found == 0) return 0;
    found = std::min<uint16_t>(found, kMaxScanResults * 2);
    auto *records = static_cast<wifi_ap_record_t *>(
        heap_caps_malloc(sizeof(wifi_ap_record_t) * found, MALLOC_CAP_DEFAULT));
    if (!records) return 0;
    if (esp_wifi_scan_get_ap_records(&found, records) != ESP_OK) {
        heap_caps_free(records);
        return 0;
    }

    int count = 0;
    for (uint16_t i = 0; i < found && count < max_results; ++i) {
        const char *ssid = reinterpret_cast<const char *>(records[i].ssid);
        if (!ssid[0]) continue;
        bool duplicate = false;
        for (int j = 0; j < count; ++j) {
            if (std::strcmp(out[j].ssid, ssid) == 0) { duplicate = true; break; }
        }
        if (duplicate) continue;  // the same SSID on 2.4 and 5 GHz, or a mesh
        std::snprintf(out[count].ssid, sizeof(out[count].ssid), "%s", ssid);
        out[count].rssi = records[i].rssi;
        out[count].open = records[i].authmode == WIFI_AUTH_OPEN;
        ++count;
    }
    heap_caps_free(records);
    // esp_wifi_scan returns strongest-first already, but sorting is cheap and
    // makes the panel order independent of that promise.
    std::sort(out, out + count,
              [](const WifiAp &a, const WifiAp &b) { return a.rssi > b.rssi; });
    return count;
}

int wifi_station_survey(WifiSurveyAp *out, int max_results) {
    if (!out || max_results <= 0) return 0;
    if (wifi_station_init() != ESP_OK) return 0;

    // A scan takes the radio off-channel for a moment per channel, so an
    // association survives it but in-flight packets may not. That is why this
    // is only ever reached from a button the user pressed, never from a timer.
    wifi_scan_config_t scan = {};
    scan.show_hidden = false;
    if (esp_wifi_scan_start(&scan, true) != ESP_OK) return 0;

    uint16_t found = 0;
    esp_wifi_scan_get_ap_num(&found);
    if (found == 0) return 0;
    found = std::min<uint16_t>(found, kMaxSurveyResults * 2);
    // PSRAM: roughly 100 bytes per record, and internal DRAM is the scarce pool
    // on this board (~33 kB free with the gateway up). Released before return,
    // so the analyser costs nothing while its screen is closed.
    auto *records = static_cast<wifi_ap_record_t *>(
        heap_caps_malloc(sizeof(wifi_ap_record_t) * found, MALLOC_CAP_SPIRAM));
    if (!records) return 0;
    if (esp_wifi_scan_get_ap_records(&found, records) != ESP_OK) {
        heap_caps_free(records);
        return 0;
    }

    int count = 0;
    for (uint16_t i = 0; i < found && count < max_results; ++i) {
        const char *ssid = reinterpret_cast<const char *>(records[i].ssid);
        if (!ssid[0]) continue;  // hidden; nothing to offer the user
        // No de-duplication on purpose. Two radios sharing an SSID are two
        // different answers to "where should this watch associate".
        std::snprintf(out[count].ssid, sizeof(out[count].ssid), "%s", ssid);
        std::memcpy(out[count].bssid, records[i].bssid, sizeof(out[count].bssid));
        out[count].rssi = records[i].rssi;
        out[count].channel = records[i].primary;
        out[count].open = records[i].authmode == WIFI_AUTH_OPEN;
        ++count;
    }
    heap_caps_free(records);
    std::sort(out, out + count,
              [](const WifiSurveyAp &a, const WifiSurveyAp &b) { return a.rssi > b.rssi; });
    return count;
}

bool wifi_station_password_for(const char *ssid, char *out, size_t size) {
    if (!ssid || !*ssid || !out || size == 0) return false;
    for (int i = 0; i < kKnownNetworks; ++i) {
        char known_ssid[33] = {};
        char known_password[65] = {};
        if (!wifi_station_known(i, known_ssid, sizeof(known_ssid), known_password,
                                sizeof(known_password))) {
            continue;
        }
        if (std::strcmp(known_ssid, ssid) != 0) continue;
        std::snprintf(out, size, "%s", known_password);
        return true;
    }
    return false;
}

WifiHealth wifi_station_health() {
    WifiHealth health{};
    health.disconnects = g_disconnects.load();
    health.last_reason = g_last_reason.load();
    wifi_ap_record_t ap{};
    // Signal strength is the single most useful number when a board keeps
    // reconnecting: a college AP at -80 dBm and a tunnel having a bad day look
    // identical from the gateway, and only one of them is fixable by moving.
    health.rssi = esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
    return health;
}

bool wifi_station_current_ssid(char *out, size_t size) {
    if (!out || size == 0) return false;
    out[0] = '\0';
    wifi_ap_record_t ap{};
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return false;
    std::snprintf(out, size, "%s", reinterpret_cast<const char *>(ap.ssid));
    return out[0] != '\0';
}

bool wifi_station_connected() { return g_connected.load(); }

}  // namespace kiki
