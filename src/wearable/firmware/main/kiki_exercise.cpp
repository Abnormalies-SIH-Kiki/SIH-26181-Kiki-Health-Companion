#include "kiki_exercise.hpp"

#include <cmath>
#include <cstdio>
#include <cstring>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "gateway_client.hpp"
#include "kiki_imu_wire.hpp"
#include "kiki_settings.hpp"
#include "kiki_wearable.hpp"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_exercise";

// 50 Hz, and the longest hold a care routine is allowed to ask for here. Twelve
// seconds covers every hold the model realistically sets (the RPi's observed
// values are 5-10 s) and bounds the buffer at a size that can live in internal
// RAM without competing with the audio pipeline for PSRAM.
constexpr int kHz = 50;
constexpr float kMaxWindowSeconds = 12.0F;
constexpr int kMaxSamples = static_cast<int>(kHz * kMaxWindowSeconds);

// Base64 of 12 bytes per sample, plus the JSON around it. Sized for the worst
// case so a long window cannot be silently truncated into a lie about what the
// wrist did.
constexpr size_t kPayloadBytes = kMaxSamples * imu_wire::kBytesPerSample;
constexpr size_t kBase64Bytes = ((kPayloadBytes + 2) / 3) * 4 + 1;

// All four buffers live in PSRAM, allocated once at start-up.
//
// As internal DRAM they measured 34 kB, and the board reported `internal_free`
// falling from 66 kB to 16 kB on the first flash that carried them. Internal
// RAM is what DMA descriptors, task stacks and the Wi-Fi driver come out of;
// spending a third of the headroom on an exercise buffer is not a trade worth
// making, and `kiki_wearable.cpp` already puts its analysis scratch in PSRAM
// for the same reason. There are ~7 MB of PSRAM free.
//
// Written from the sampler task under a spinlock, never from an ISR, so the
// cache is always enabled when these are touched.
int16_t (*g_samples)[imu_wire::kAxes] = nullptr;
volatile int g_write_index = 0;
volatile int g_wanted = 0;          // 0 = not recording
volatile bool g_ready = false;
volatile uint32_t g_window_serial = 0;
portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;

// Only the sender task touches these, so they need no lock -- and they are
// heap-allocated rather than stack, because 27 kB of them would not fit in a
// task stack worth having.
uint8_t *g_payload = nullptr;
char *g_base64 = nullptr;
char *g_json = nullptr;
constexpr size_t kJsonBytes = kBase64Bytes + 256;

void send_window() {
    int count = 0;
    uint32_t serial = 0;
    portENTER_CRITICAL(&g_mux);
    count = g_write_index;
    serial = g_window_serial;
    g_ready = false;
    portEXIT_CRITICAL(&g_mux);
    if (count <= 0) return;
    if (count > kMaxSamples) count = kMaxSamples;
    if (g_samples == nullptr || g_payload == nullptr || g_base64 == nullptr ||
        g_json == nullptr) {
        return;
    }

    // Explicit sizes, not sizeof(): these are pointers into PSRAM now, and
    // sizeof() on them is 4 -- which would silently truncate every window to a
    // fraction of a sample and hand the care model garbage it could not tell
    // from real movement.
    const size_t offset = imu_wire::serialize(g_samples, count, g_payload,
                                              kPayloadBytes);
    imu_wire::encode_base64(g_payload, offset, g_base64, kBase64Bytes);

    WearableSnapshot snapshot{};
    wearable_get_snapshot(&snapshot);

    // The payload is the bulk of this; build the JSON around it in place rather
    // than through cJSON, which would need a second copy of the base64.
    std::snprintf(g_json, kJsonBytes,
                  "\"window_id\":\"w%lu\",\"hz\":%d,\"samples\":%d,"
                  "\"worn\":%s,\"format\":\"i16x6\","
                  "\"scale\":{\"accel_g\":0.001,\"gyro_dps\":0.01},"
                  "\"data\":\"%s\"",
                  static_cast<unsigned long>(serial), kHz, count,
                  snapshot.worn ? "true" : "false", g_base64);
    gateway_client_send_event("imu_window", g_json);
    ESP_LOGI(kTag, "sent motion window w%lu: %d samples, worn=%d",
             static_cast<unsigned long>(serial), count, snapshot.worn ? 1 : 0);
}

const char *measurement_state_name(WearableMeasurementState state) {
    switch (state) {
        case WearableMeasurementState::WaitingForContact: return "waiting_for_contact";
        case WearableMeasurementState::WaitingForStillness: return "waiting_for_stillness";
        case WearableMeasurementState::Measuring: return "measuring";
        case WearableMeasurementState::Complete: return "complete";
        case WearableMeasurementState::Failed: return "failed";
        case WearableMeasurementState::Idle:
        default: return "idle";
    }
}

void report_measurement(const WearableMeasurementStatus &status) {
    char json[320]{};
    std::snprintf(json, sizeof(json),
                  "\"state\":\"%s\",\"progress_percent\":%u,\"worn\":%s,"
                  "\"sensor_available\":%s,\"heart_rate\":%.1f,"
                  "\"spo2_experimental\":%.1f,\"quality\":\"%s\"",
                  measurement_state_name(status.state),
                  static_cast<unsigned>(status.progress_percent),
                  status.worn ? "true" : "false",
                  status.sensor_available ? "true" : "false",
                  status.heart_rate, status.spo2_experimental,
                  status.quality ? status.quality : "NONE");
    gateway_client_send_event("measurement_status", json);
}

// A low-priority task, like the fall detector's: this must never sit in front
// of audio, and it must never wait behind the optical task's 40-second
// acquisition. It does two small things every 200 ms and sleeps.
void exercise_task(void *) {
    WearableMeasurementState last_state = WearableMeasurementState::Idle;
    bool watching_measurement = false;
    for (;;) {
        bool ready = false;
        portENTER_CRITICAL(&g_mux);
        ready = g_ready;
        portEXIT_CRITICAL(&g_mux);
        if (ready) send_window();

        WearableMeasurementStatus status{};
        wearable_get_measurement_status(&status);
        if (status.state != WearableMeasurementState::Idle) watching_measurement = true;
        if (watching_measurement && status.state != last_state) {
            report_measurement(status);
            last_state = status.state;
            if (status.state == WearableMeasurementState::Complete ||
                status.state == WearableMeasurementState::Failed) {
                watching_measurement = false;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

}  // namespace

esp_err_t exercise_start() {
    g_samples = static_cast<int16_t (*)[imu_wire::kAxes]>(
        heap_caps_malloc(sizeof(int16_t) * imu_wire::kAxes * kMaxSamples,
                         MALLOC_CAP_SPIRAM));
    g_payload = static_cast<uint8_t *>(
        heap_caps_malloc(kPayloadBytes, MALLOC_CAP_SPIRAM));
    g_base64 = static_cast<char *>(
        heap_caps_malloc(kBase64Bytes, MALLOC_CAP_SPIRAM));
    g_json = static_cast<char *>(
        heap_caps_malloc(kJsonBytes, MALLOC_CAP_SPIRAM));
    if (g_samples == nullptr || g_payload == nullptr || g_base64 == nullptr ||
        g_json == nullptr) {
        // Deliberately does NOT fall back to internal RAM. Losing guided
        // exercise is a feature outage; running the board out of internal DRAM
        // is a stability problem for everything else on it.
        heap_caps_free(g_samples);
        heap_caps_free(g_payload);
        heap_caps_free(g_base64);
        heap_caps_free(g_json);
        g_samples = nullptr;
        g_payload = nullptr;
        g_base64 = nullptr;
        g_json = nullptr;
        ESP_LOGE(kTag, "no PSRAM for the motion buffers; capture disabled");
        return ESP_ERR_NO_MEM;
    }

    const BaseType_t created = xTaskCreatePinnedToCore(
        exercise_task, "kiki_exercise", 4096, nullptr, 3, nullptr, 0);
    if (created != pdPASS) {
        ESP_LOGE(kTag, "could not start the exercise task");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(kTag, "guided-exercise motion capture ready (%d Hz, max %.0f s)",
             kHz, static_cast<double>(kMaxWindowSeconds));
    return ESP_OK;
}

void exercise_note_motion(float ax, float ay, float az,
                          float gx, float gy, float gz) {
    if (g_samples == nullptr) return;
    portENTER_CRITICAL(&g_mux);
    const int wanted = g_wanted;
    const int index = g_write_index;
    if (wanted > 0 && index < wanted && index < kMaxSamples) {
        imu_wire::pack_sample(ax, ay, az, gx, gy, gz, g_samples[index]);
        g_write_index = index + 1;
        if (g_write_index >= wanted) {
            // Closed by the SAMPLER, on the sample that completes it. The
            // window is then exactly the interval that was asked for, with no
            // dependence on when a timer task happened to be scheduled.
            g_wanted = 0;
            g_ready = true;
        }
    }
    portEXIT_CRITICAL(&g_mux);
}

void exercise_begin_window(float seconds) {
    if (seconds <= 0.0F || g_samples == nullptr) return;
    if (!settings_movement_checks_enabled()) {
        // Switched off by the wearer. Record nothing and send nothing: the
        // gateway was told about the switch when it was flipped, so health mode
        // says "movement checks are off" rather than reporting a sensor that
        // failed to answer.
        ESP_LOGI(kTag, "movement checks are off; no window recorded");
        return;
    }
    if (seconds > kMaxWindowSeconds) seconds = kMaxWindowSeconds;
    const int wanted = static_cast<int>(seconds * kHz);
    portENTER_CRITICAL(&g_mux);
    g_write_index = 0;
    g_wanted = (wanted > kMaxSamples) ? kMaxSamples : wanted;
    g_ready = false;
    // Written as a read-modify-write rather than `++`: incrementing a volatile
    // is deprecated in C++20 and warns. It is inside the spinlock either way.
    g_window_serial = g_window_serial + 1;
    portEXIT_CRITICAL(&g_mux);
    ESP_LOGI(kTag, "recording %.1f s of wrist motion (%d samples)",
             static_cast<double>(seconds), wanted);
}

void exercise_cancel_window() {
    portENTER_CRITICAL(&g_mux);
    g_wanted = 0;
    g_write_index = 0;
    g_ready = false;
    portEXIT_CRITICAL(&g_mux);
}

void exercise_request_measurement() {
    if (!wearable_request_measurement()) {
        // Refused, so nothing will advance the state machine and nothing else
        // would ever tell the gateway. Say so now: the care agent's rule is
        // that a measurement which did not happen is stated, never guessed at.
        WearableMeasurementStatus status{};
        wearable_get_measurement_status(&status);
        status.state = WearableMeasurementState::Failed;
        report_measurement(status);
    }
}

void exercise_cancel_measurement() {
    wearable_cancel_measurement();
}

}  // namespace kiki
