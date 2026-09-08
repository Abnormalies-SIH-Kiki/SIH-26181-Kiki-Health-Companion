#pragma once

#include <cstdint>
#include "esp_err.h"
#include "esp_wifi_types.h"

namespace kiki {

constexpr int kMaxScanResults = 20;

struct WifiAp {
    char ssid[33];
    int8_t rssi;
    bool open;
};

// Brings up netif/event/Wi-Fi without connecting. Safe to call once at boot.
esp_err_t wifi_station_init();

// True when a network has been provisioned, from NVS or from the build-time
// Kconfig values. Credentials entered on the panel are stored in NVS and win,
// so changing AP no longer means reflashing.
bool wifi_station_has_credentials();

// True once the user has deliberately forgotten the network from the panel.
// Anything that reaches for the compiled-in credentials must check this first:
// they are a first-boot convenience, and silently rejoining them is exactly how
// "Change Wi-Fi" turns into a button that appears to do nothing.
bool wifi_station_forgotten();
bool wifi_station_saved_ssid(char *out, size_t size);

// Networks this board has actually joined, most recently successful first.
//
// One remembered network is not enough for a device that moves. Kiki lives at
// home and travels to college, and with a single slot every trip meant either
// re-picking on the panel or sitting through a 20-second join timeout for a
// network that is nowhere near. Remembering what worked, and trying only the
// ones the scan can currently see, removes both.
constexpr int kKnownNetworks = 4;

// Promote a network to the front of that list, de-duplicated by SSID. Called
// whenever a join succeeds, from any path -- the panel picker, the saved
// network, or the compiled-in fallback.
void wifi_station_remember(const char *ssid, const char *password);

// Read one slot. False when the slot is empty.
bool wifi_station_known(int index, char *ssid, size_t ssid_size,
                        char *password, size_t password_size);

// Blocking connect. `timeout_ms` bounds the whole attempt.
esp_err_t wifi_station_connect(const char *ssid, const char *password, uint32_t timeout_ms);
esp_err_t wifi_station_connect_saved(uint32_t timeout_ms);

void wifi_station_save(const char *ssid, const char *password);
void wifi_station_forget();

// Blocking scan. Returns the number of entries written, strongest first and
// de-duplicated by SSID.
int wifi_station_scan(WifiAp *out, int max_results);

// How many radios the analyser will look at. Deliberately larger than
// kMaxScanResults: that one counts NETWORKS, this one counts ACCESS POINTS, and
// a campus SSID is routinely a dozen of them.
constexpr int kMaxSurveyResults = 32;

// One access point as the analyser sees it: per-BSSID, with the channel.
struct WifiSurveyAp {
    char ssid[33];
    uint8_t bssid[6];
    int8_t rssi;
    uint8_t channel;
    bool open;
};

// A full survey of what this radio can hear, strongest first and NOT collapsed
// by SSID.
//
// This is the difference between the picker and the analyser, and it is the
// whole reason the analyser exists. `wifi_station_scan` keeps one entry per
// SSID, which is right for "which network do you want" and useless for "why is
// NSUT_WIFI weak here" -- the answer to that is that there are eleven of them
// and this board is hearing the wrong one. Answering it needs every BSSID and
// the channel each one sits on.
//
// Note this board is 2.4 GHz only (ESP32-S3 has no 5 GHz radio), so a survey
// can never show the 5 GHz APs a phone reports. That is a hardware fact and the
// analyser says so rather than appearing to have missed them.
//
// Scratch space is taken from PSRAM and released before returning. Blocking;
// call it from a task that may sleep for a couple of seconds, never from an
// LVGL callback.
int wifi_station_survey(WifiSurveyAp *out, int max_results);

// The saved password for `ssid`, if this board has ever joined it. Used to put
// a link back the way it was after a failed experiment: a picker that can
// disconnect you without being able to reconnect you is a trap.
bool wifi_station_password_for(const char *ssid, char *out, size_t size);

bool wifi_station_connected();

// Why the link keeps dropping. `rssi` is live (0 when not associated),
// `disconnects` and `last_reason` accumulate across reconnects so the next
// hello can explain the previous failure -- the log itself dies with the
// connection, so this is the only channel that survives one.
struct WifiHealth {
    int8_t rssi;
    uint16_t disconnects;
    uint8_t last_reason;
};
WifiHealth wifi_station_health();

// The SSID this board is associated to *right now* -- not what is saved, and
// not what was asked for. A board that quietly took the fallback network looks
// identical from the gateway to one that joined the network you chose, and the
// difference is the whole experiment when you are testing a weak link.
bool wifi_station_current_ssid(char *out, size_t size);

}  // namespace kiki
