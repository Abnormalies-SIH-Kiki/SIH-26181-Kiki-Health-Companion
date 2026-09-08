#include "kiki_motion.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "audio_pipeline.hpp"
#include "bsp/esp-bsp.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "gateway_client.hpp"
#include "kiki_dance.hpp"
#include "kiki_ui.hpp"
#include "kiki_exercise.hpp"
#include "kiki_wearable.hpp"
#ifdef M_PI
#undef M_PI
#endif
#include "qmi8658.h"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_motion";
constexpr float kMetersPerSecondSquaredPerG = 9.80665F;
constexpr uint32_t kSamplePeriodMs = 20;  // 50 Hz classifier input

qmi8658_dev_t g_imu{};
QueueHandle_t g_reaction_queue = nullptr;
std::atomic<bool> g_available{false};
std::atomic<bool> g_context_idle{false};
std::atomic<bool> g_context_music{false};
std::atomic<bool> g_charging{false};
std::atomic<bool> g_low_battery{false};
std::atomic<uint32_t> g_activity_sequence{0};
portMUX_TYPE g_stats_mux = portMUX_INITIALIZER_UNLOCKED;
MotionRuntimeStats g_stats{};

struct ReactionSpec {
    const char *face;
    const char *detail;
    LocalEffect effect;
    uint32_t duration_ms;
    bool critical;
};

ReactionSpec reaction_for(MotionSituation situation) {
    switch (situation) {
        case MotionSituation::PickedUp:
            return {"surprised", "picked up", LocalEffect::Pickup, 1500, false};
        case MotionSituation::UprightAlert:
            return {"motion_alert", "upright", LocalEffect::Alert, 2600, false};
        case MotionSituation::FaceDown:
            return {"motion_face_down", "face down", LocalEffect::FaceDown, 3800, false};
        case MotionSituation::UpsideDown:
            return {"motion_upside_down", "upside down", LocalEffect::UpsideDown, 3600, false};
        case MotionSituation::Sideways:
            return {"sleepy", "sideways", LocalEffect::Sleepy, 2800, false};
        case MotionSituation::GentleWiggle:
            return {"giggle", "wiggle", LocalEffect::Wiggle, 1900, false};
        case MotionSituation::Shake:
            return {"motion_annoyed", "shaken", LocalEffect::Annoyed, 2400, false};
        case MotionSituation::RepeatedShake:
            return {"motion_annoyed", "very annoyed", LocalEffect::Annoyed, 3600, false};
        case MotionSituation::DizzyAfterShake:
            return {"dizzy", "dizzy", LocalEffect::Dizzy, 3200, false};
        case MotionSituation::Rocking:
            return {"motion_rocking", "rocking", LocalEffect::Rocking, 3200, false};
        case MotionSituation::Spin:
            return {"dizzy", "spinning", LocalEffect::Spin, 3600, false};
        case MotionSituation::Carried:
            return {"motion_carried", "being carried", LocalEffect::Carried, 3000, false};
        case MotionSituation::Bump:
            return {"surprised", "bumped", LocalEffect::Bump, 1200, false};
        case MotionSituation::Freefall:
            return {"motion_brace", "falling", LocalEffect::Freefall, 1500, true};
        case MotionSituation::HardLanding:
            return {"tap_tumble", "hard landing", LocalEffect::Landing, 2600, true};
        case MotionSituation::SetDown:
            return {"motion_settled", "settled", LocalEffect::Settled, 1800, false};
        case MotionSituation::Bored:
            return {"motion_bored", "getting bored", LocalEffect::Bored, 4800, false};
        case MotionSituation::VeryBored:
            return {"motion_bored", "very bored", LocalEffect::Bored, 6500, false};
        case MotionSituation::Dozing:
            return {"sleeping", "dozing", LocalEffect::Sleepy, 12000, false};
        case MotionSituation::ChargingRest:
            return {"love", "charging", LocalEffect::Charging, 4500, false};
        case MotionSituation::MusicDance:
            return {"excited", "dancing", LocalEffect::Dance, 2400, false};
        case MotionSituation::LowBatteryTired:
            return {"sleepy", "low battery", LocalEffect::Tired, 4800, false};
        case MotionSituation::WakeFromSleep:
            return {"motion_alert", "woken up", LocalEffect::Pickup, 2300, false};
        case MotionSituation::None:
        case MotionSituation::Count:
        default:
            return {"idle", "", LocalEffect::Settled, 0, false};
    }
}

void update_stats(const MotionClassifierSnapshot &snapshot, uint32_t read_errors) {
    portENTER_CRITICAL(&g_stats_mux);
    g_stats.available = g_available.load();
    g_stats.read_errors = read_errors;
    g_stats.classifier = snapshot;
    portEXIT_CRITICAL(&g_stats_mux);
}

void reaction_task(void *) {
    MotionDecision decision{};
    while (true) {
        if (xQueueReceive(g_reaction_queue, &decision, portMAX_DELAY) != pdTRUE) continue;
        const ReactionSpec reaction = reaction_for(decision.situation);
        const bool shown = ui_play_motion_reaction(
            reaction.face, reaction.detail, reaction.effect, reaction.duration_ms,
            reaction.critical);
        ESP_LOGI(kTag, "%s posture=%s intensity=%.2f shown=%s",
                 motion_situation_name(decision.situation),
                 motion_posture_name(decision.posture),
                 static_cast<double>(decision.intensity), shown ? "yes" : "no");

        // Sampling never waits on the network. This low-priority consumer sends
        // only debounced semantic events, after the local reaction is already
        // visible; a congested websocket can therefore delay observability but
        // never damage classification latency.
        if (gateway_client_connected()) {
            char payload[192];
            std::snprintf(payload, sizeof(payload),
                          "\"event\":\"%s\",\"posture\":\"%s\","
                          "\"intensity\":%.2f,\"shown\":%s",
                          motion_situation_name(decision.situation),
                          motion_posture_name(decision.posture),
                          static_cast<double>(decision.intensity), shown ? "true" : "false");
            gateway_client_send_event("motion_event", payload);
        }
        wearable_note_motion_event(motion_situation_name(decision.situation));
    }
}

void sensor_task(void *) {
    MotionClassifier classifier;
    uint32_t observed_activity = g_activity_sequence.load();
    uint32_t read_errors = 0;
    TickType_t wake = xTaskGetTickCount();

    while (true) {
        bool ready = false;
        const esp_err_t ready_result = qmi8658_is_data_ready(&g_imu, &ready);
        if (ready_result != ESP_OK) {
            ++read_errors;
        } else if (ready) {
            qmi8658_data_t data{};
            const esp_err_t read_result = qmi8658_read_sensor_data(&g_imu, &data);
            if (read_result == ESP_OK) {
                const uint32_t now_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000);
                const uint32_t activity = g_activity_sequence.load();
                if (activity != observed_activity) {
                    observed_activity = activity;
                    classifier.note_interaction(now_ms);
                }
                MotionSample sample{};
                sample.ax = data.accelX / kMetersPerSecondSquaredPerG;
                sample.ay = data.accelY / kMetersPerSecondSquaredPerG;
                sample.az = data.accelZ / kMetersPerSecondSquaredPerG;
                sample.gx = data.gyroX;
                sample.gy = data.gyroY;
                sample.gz = data.gyroZ;
                sample.timestamp_ms = now_ms;
                wearable_note_motion(sample.ax, sample.ay, sample.az,
                                     sample.gx, sample.gy, sample.gz,
                                     motion_posture_name(classifier.snapshot().posture));
                // The same samples, offered to health mode's capture buffer.
                // A no-op -- one critical-section read of a zero -- unless a
                // window has actually been requested.
                exercise_note_motion(sample.ax, sample.ay, sample.az,
                                     sample.gx, sample.gy, sample.gz);
                MotionContext context{};
                context.idle = g_context_idle.load();
                context.music = g_context_music.load();
                context.charging = g_charging.load();
                context.low_battery = g_low_battery.load();
                if (const MotionDecision decision = classifier.update(sample, context)) {
                    xQueueSend(g_reaction_queue, &decision, 0);
                }
                // Turning her upside down stops a dance, and it stops it from
                // here rather than from a gateway round trip: this is a
                // physical gesture, so the response has to be physical-fast.
                // The classifier's own posture debounce is what keeps a single
                // noisy sample from ending a performance.
                if (dance_is_active() &&
                    classifier.snapshot().posture == MotionPosture::UpsideDown) {
                    dance_stop("upside_down");
                }
                update_stats(classifier.snapshot(), read_errors);
            } else {
                ++read_errors;
            }
        }
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(kSamplePeriodMs));
    }
}

}  // namespace

esp_err_t motion_start() {
    const i2c_master_bus_handle_t bus = bsp_i2c_get_handle();
    if (!bus) {
        ESP_LOGW(kTag, "QMI8658 disabled: board I2C bus unavailable");
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t result = qmi8658_init(&g_imu, bus, QMI8658_ADDRESS_HIGH);
    if (result != ESP_OK) {
        ESP_LOGW(kTag, "QMI8658 disabled: init failed: %s", esp_err_to_name(result));
        return result;
    }
    if ((result = qmi8658_set_accel_range(&g_imu, QMI8658_ACCEL_RANGE_8G)) != ESP_OK ||
        (result = qmi8658_set_accel_odr(&g_imu, QMI8658_ACCEL_ODR_500HZ)) != ESP_OK ||
        (result = qmi8658_set_gyro_range(&g_imu, QMI8658_GYRO_RANGE_1024DPS)) != ESP_OK ||
        (result = qmi8658_set_gyro_odr(&g_imu, QMI8658_GYRO_ODR_500HZ)) != ESP_OK) {
        ESP_LOGW(kTag, "QMI8658 disabled: configure failed: %s", esp_err_to_name(result));
        return result;
    }
    qmi8658_set_accel_unit_mps2(&g_imu, true);
    qmi8658_set_gyro_unit_rads(&g_imu, false);
    if ((result = qmi8658_write_register(&g_imu, QMI8658_CTRL5, 0x03)) != ESP_OK) {
        ESP_LOGW(kTag, "QMI8658 disabled: filter setup failed: %s", esp_err_to_name(result));
        return result;
    }

    g_reaction_queue = xQueueCreate(8, sizeof(MotionDecision));
    if (!g_reaction_queue) return ESP_ERR_NO_MEM;
    TaskHandle_t reaction_handle = nullptr;
    if (xTaskCreatePinnedToCore(reaction_task, "kiki_motion_rx", 4096, nullptr, 2,
                                &reaction_handle, 0) != pdPASS) {
        vQueueDelete(g_reaction_queue);
        g_reaction_queue = nullptr;
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreatePinnedToCore(sensor_task, "kiki_motion", 4096, nullptr, 4,
                                nullptr, 0) != pdPASS) {
        vTaskDelete(reaction_handle);
        vQueueDelete(g_reaction_queue);
        g_reaction_queue = nullptr;
        g_available = false;
        return ESP_ERR_NO_MEM;
    }
    g_available = true;
    update_stats({}, 0);
    ESP_LOGI(kTag, "QMI8658 active at 50 Hz (8 g, 1024 dps)");
    return ESP_OK;
}

void motion_note_system_state(const char *state) {
    const bool music = state && std::strcmp(state, "music") == 0;
    const bool idle = state && (std::strcmp(state, "idle") == 0 ||
                                std::strcmp(state, "disconnected") == 0);
    g_context_music = music;
    g_context_idle = idle;
    if (!idle && !music) ++g_activity_sequence;
}

void motion_note_interaction() { ++g_activity_sequence; }

void motion_set_power_state(int percent, bool on_usb, bool charging) {
    g_charging = on_usb && charging;
    g_low_battery = !on_usb && percent >= 0 && percent <= 18;
}

void motion_get_stats(MotionRuntimeStats *out) {
    if (!out) return;
    portENTER_CRITICAL(&g_stats_mux);
    *out = g_stats;
    portEXIT_CRITICAL(&g_stats_mux);
}

}  // namespace kiki
