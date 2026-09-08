#include "kiki_time.hpp"

#include <ctime>

#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "esp_sntp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace kiki {
namespace {
constexpr char kTag[] = "kiki_time";
// Anything after 2023 means a real answer arrived; the 1970 default is what we
// are guarding against, and a plausibility check is cheaper than tracking the
// callback.
constexpr time_t kSane = 1700000000;
bool sntp_bring_up();
// Whether an SNTP service exists at all, as opposed to existing but not having
// been answered yet. The retry loop has to tell those apart: restarting a
// service that was never created just spins.
bool g_service_up = false;
}  // namespace

bool time_is_set() { return time(nullptr) >= kSane; }

namespace {
void retry_task(void *) {
    while (!time_is_set()) {
        vTaskDelay(pdMS_TO_TICKS(30000));
        if (time_is_set()) break;
        ESP_LOGW(kTag, "still no clock; asking NTP again");
        if (!g_service_up && !sntp_bring_up()) continue;
        esp_netif_sntp_start();
        esp_netif_sntp_sync_wait(pdMS_TO_TICKS(10000));
    }
    if (time_is_set()) ESP_LOGW(kTag, "clock acquired late; TLS can validate now");
    vTaskDelete(nullptr);
}
}  // namespace

void time_sync_keep_trying() {
    if (time_is_set()) return;
    xTaskCreate(retry_task, "kiki_ntp", 3072, nullptr, 3, nullptr);
}

namespace {
// Returns true once an SNTP service is actually running.
//
// The DHCP-supplied server is worth having -- a hotspot that blocks or hijacks
// pool.ntp.org usually still answers its own NTP -- but asking for it requires
// CONFIG_LWIP_DHCP_GET_NTP_SRV, and esp_netif_sntp_init() rejects the whole
// config with ESP_ERR_INVALID_ARG when that is off. That is exactly how the
// clock silently stayed at 1970 and made every wss:// certificate read as
// not-yet-valid. So: ask for DHCP, and if the build cannot do it, fall back to
// the fixed list rather than giving up on having a clock at all.
//
// Three fixed servers, not one, because pool.ntp.org is a rotating DNS name and
// some of what it hands out simply never answers -- measured from this network,
// one boot got a clock in 700 ms and the next timed out entirely against
// 162.159.200.123. One dead draw should cost a retry, not the clock.
bool sntp_bring_up() {
    if (g_service_up) return true;
    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG_MULTIPLE(
        3, ESP_SNTP_SERVER_LIST("pool.ntp.org", "time.google.com", "time.cloudflare.com"));
    // Kiki reconnects and runs for days; letting the clock drift back out of
    // certificate validity would break TLS again in a way that looks random.
    config.start = true;
    config.server_from_dhcp = true;
    config.renew_servers_after_new_IP = true;
    config.index_of_first_server = 1;
    esp_err_t err = esp_netif_sntp_init(&config);
    if (err == ESP_OK) {
        g_service_up = true;
        return true;
    }

    ESP_LOGW(kTag, "SNTP with DHCP servers refused (%s); using the fixed servers only",
             esp_err_to_name(err));
    esp_netif_sntp_deinit();
    esp_sntp_config_t fixed = ESP_NETIF_SNTP_DEFAULT_CONFIG_MULTIPLE(
        3, ESP_SNTP_SERVER_LIST("pool.ntp.org", "time.google.com", "time.cloudflare.com"));
    fixed.start = true;
    err = esp_netif_sntp_init(&fixed);
    if (err == ESP_OK) {
        g_service_up = true;
        return true;
    }
    ESP_LOGE(kTag, "could not start SNTP (%s); TLS will fail", esp_err_to_name(err));
    return false;
}
}  // namespace

bool time_sync(int timeout_ms) {
    if (!sntp_bring_up()) return false;
    if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(timeout_ms)) != ESP_OK) {
        ESP_LOGE(kTag,
                 "no NTP answer in %d ms -- the clock is still 1970, so every "
                 "wss:// certificate will read as not-yet-valid",
                 timeout_ms);
        return false;
    }
    const time_t now = time(nullptr);
    if (now < kSane) {
        ESP_LOGE(kTag, "NTP returned an implausible time (%lld)", static_cast<long long>(now));
        return false;
    }
    char stamp[32];
    struct tm utc;
    gmtime_r(&now, &utc);
    strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &utc);
    ESP_LOGW(kTag, "clock set to %s UTC; TLS can validate now", stamp);
    return true;
}

}  // namespace kiki
