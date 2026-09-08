#include "kiki_ota.hpp"

#include <atomic>
#include <cstdio>
#include <cstring>
#include <string>

#include "esp_app_desc.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gateway_client.hpp"
#include "kiki_ui.hpp"
#include "nvs.h"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_ota";
constexpr char kNamespace[] = "kiki_ota";
constexpr char kKeyPending[] = "pending";
// How long a freshly installed image gets to reach the gateway before it is
// judged a failure. Generous on purpose: a cold gateway warms its models for
// well over a minute, and reverting good firmware because the laptop was busy
// would be a worse bug than the one this guards against.
constexpr int kProveWithinMs = 180000;
// How many times to re-download before giving up, and how long to wait between
// tries. Short enough that a real outage is not papered over for minutes.
constexpr int kMaxAttempts = 3;
// A download that stops arriving without the connection closing is the one
// failure the HTTP client's own timeout does not catch, and it is not
// hypothetical: killing the file server mid-download (which is what stopping
// the OTA tunnel does) left the update task blocked for over ten minutes with
// `g_running` latched, so every later offer was refused with "an update is
// already running" and the board could only be recovered by a power cycle.
// Progress, not connectivity, is the thing worth watching.
constexpr int64_t kStallTimeoutUs = 45LL * 1000 * 1000;
constexpr int64_t kAttemptTimeoutUs = 360LL * 1000 * 1000;
constexpr int kRetryDelayMs = 8000;

std::atomic<bool> g_running{false};
std::atomic<bool> g_confirmed{false};
std::string g_url;

void set_pending(bool pending) {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READWRITE, &handle) != ESP_OK) return;
    nvs_set_u8(handle, kKeyPending, pending ? 1 : 0);
    nvs_commit(handle);
    nvs_close(handle);
}

bool is_pending() {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READONLY, &handle) != ESP_OK) return false;
    uint8_t value = 0;
    const esp_err_t err = nvs_get_u8(handle, kKeyPending, &value);
    nvs_close(handle);
    return err == ESP_OK && value == 1;
}

void rollback_task(void *) {
    vTaskDelay(pdMS_TO_TICKS(kProveWithinMs));
    if (g_confirmed) vTaskDelete(nullptr);
    const esp_partition_t *previous = esp_ota_get_next_update_partition(nullptr);
    if (!previous) vTaskDelete(nullptr);
    ESP_LOGE(kTag,
             "new firmware never reached the gateway in %d s; going back to the "
             "previous image",
             kProveWithinMs / 1000);
    set_pending(false);
    if (esp_ota_set_boot_partition(previous) == ESP_OK) {
        ui_set_lcd("Update reverted", "restarting");
        vTaskDelay(pdMS_TO_TICKS(1500));
        esp_restart();
    }
    vTaskDelete(nullptr);
}

// One download-and-install attempt. Returns true only when a complete, verified
// image is sitting in the inactive slot and marked bootable; the caller reboots.
bool install_once(int attempt) {
    esp_http_client_config_t http = {};
    http.url = g_url.c_str();
    // The certificate bundle is for the public HTTPS path. A LAN http:// URL
    // has no certificate to check, and the gateway only ever sends one whose
    // host is a literal private address.
    const bool plaintext = g_url.rfind("http://", 0) == 0;
    if (!plaintext) {
        http.crt_bundle_attach = esp_crt_bundle_attach;
    }
    // A firmware download over a phone hotspot is not a 5-second operation, and
    // the websocket's short timeouts are wrong here.
    http.timeout_ms = 30000;
    http.keep_alive_enable = true;

    esp_https_ota_config_t cfg = {};
    cfg.http_config = &http;
    if (plaintext) {
        ESP_LOGW(kTag, "attempt %d over plain HTTP (LAN): %s", attempt, g_url.c_str());
    }

    esp_https_ota_handle_t handle = nullptr;
    esp_err_t err = esp_https_ota_begin(&cfg, &handle);
    if (err != ESP_OK || !handle) {
        ESP_LOGE(kTag, "attempt %d could not start: %s", attempt, esp_err_to_name(err));
        return false;
    }

    const int total = esp_https_ota_get_image_size(handle);
    int last_reported = -10;
    int last_len = 0;
    const int64_t started_us = esp_timer_get_time();
    int64_t progress_us = started_us;
    while ((err = esp_https_ota_perform(handle)) ==
           ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
        const int64_t now = esp_timer_get_time();
        const int read = esp_https_ota_get_image_len_read(handle);
        if (read != last_len) {
            last_len = read;
            progress_us = now;
        } else if (now - progress_us > kStallTimeoutUs) {
            ESP_LOGE(kTag, "attempt %d stalled at %d bytes; giving up on it",
                     attempt, read);
            err = ESP_ERR_TIMEOUT;
            break;
        }
        if (now - started_us > kAttemptTimeoutUs) {
            ESP_LOGE(kTag, "attempt %d ran past its %lld s budget", attempt,
                     static_cast<long long>(kAttemptTimeoutUs / 1000000));
            err = ESP_ERR_TIMEOUT;
            break;
        }
        if (total <= 0) continue;
        const int percent = esp_https_ota_get_image_len_read(handle) * 100 / total;
        if (percent >= last_reported + 10) {
            last_reported = percent;
            char line[24];
            std::snprintf(line, sizeof(line), "%d%%", percent);
            ui_set_lcd("Updating", line);
            ESP_LOGI(kTag, "update %d%%", percent);
        }
    }

    if (err != ESP_OK || !esp_https_ota_is_complete_data_received(handle)) {
        // Aborting matters: a partial image left marked bootable is exactly the
        // failure this whole path exists to avoid. It also releases the slot so
        // the next attempt starts from a clean partition rather than inheriting
        // half an image.
        ESP_LOGE(kTag, "attempt %d failed: %s", attempt, esp_err_to_name(err));
        esp_https_ota_abort(handle);
        return false;
    }

    err = esp_https_ota_finish(handle);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "attempt %d could not be installed: %s", attempt,
                 esp_err_to_name(err));
        return false;
    }
    return true;
}

void ota_task(void *) {
    ESP_LOGW(kTag, "firmware update from %s", g_url.c_str());
    ui_set_lcd("Updating", "please wait");

    // Three tries, not one. The download is ~1.8 MB over whatever link the
    // board happens to be on, and a single dropped TCP connection used to end
    // the update for good: the gateway deletes the pending file once it has
    // sent the URL, so nothing ever asked again. Retrying here is the only
    // place that can recover without a human noticing.
    bool installed = false;
    for (int attempt = 1; attempt <= kMaxAttempts && !installed; ++attempt) {
        if (attempt > 1) {
            char line[24];
            std::snprintf(line, sizeof(line), "retry %d of %d", attempt, kMaxAttempts);
            ui_set_lcd("Updating", line);
            ESP_LOGW(kTag, "retrying the download (%s)", line);
            vTaskDelay(pdMS_TO_TICKS(kRetryDelayMs));
        }
        installed = install_once(attempt);
    }

    if (!installed) {
        ESP_LOGE(kTag, "update abandoned after %d attempts; still on the old image",
                 kMaxAttempts);
        ui_set_lcd("Update failed", "kept old build");
        // Say so out loud. A silent failure is why a board can sit for days on
        // firmware someone believes they replaced.
        char detail[40];
        std::snprintf(detail, sizeof(detail), "\"ok\":false,\"attempts\":%d", kMaxAttempts);
        gateway_client_send_event("ota_result", detail);
        g_running = false;
        vTaskDelete(nullptr);
        return;
    }

    // Recorded before the reboot, because after it this code is the *new*
    // image and has no other way to know it arrived by OTA.
    set_pending(true);
    ESP_LOGW(kTag, "update installed; restarting");
    ui_set_lcd("Updated", "restarting");
    gateway_client_send_event("ota_result", "\"ok\":true");
    vTaskDelay(pdMS_TO_TICKS(1200));
    esp_restart();
}

}  // namespace

bool ota_in_progress() { return g_running.load(); }

void ota_start(const char *url) {
    if (!url || !*url) return;
    if (g_running.exchange(true)) {
        ESP_LOGW(kTag, "an update is already running");
        return;
    }
    g_url = url;
    xTaskCreate(ota_task, "kiki_ota", 8192, nullptr, 5, nullptr);
}

void ota_arm_rollback_watchdog() {
    if (!is_pending()) return;
    ESP_LOGW(kTag, "this image arrived by OTA; it has %d s to reach the gateway",
             kProveWithinMs / 1000);
    xTaskCreate(rollback_task, "kiki_ota_wd", 3072, nullptr, 4, nullptr);
}

void ota_confirm_alive() {
    if (g_confirmed.exchange(true)) return;
    set_pending(false);
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) != ESP_OK) return;
    if (state != ESP_OTA_IMG_PENDING_VERIFY) return;
    // Reaching the gateway is the acceptance test, and it has just passed.
    if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
        ESP_LOGW(kTag, "new firmware confirmed good (reached the gateway)");
    }
}

}  // namespace kiki
