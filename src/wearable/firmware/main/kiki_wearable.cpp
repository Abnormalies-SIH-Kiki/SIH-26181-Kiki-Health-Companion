#include "kiki_wearable.hpp"
#include "kiki_fall_detector.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>

#include "driver/i2c_master.h"
#include "driver/gpio.h"
#include "audio_pipeline.hpp"
#include "esp_heap_caps.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "gateway_client.hpp"
#include "kiki_settings.hpp"
#include "kiki_ui.hpp"
#include "nvs.h"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_wearable";
constexpr gpio_num_t kSda = GPIO_NUM_18;
constexpr gpio_num_t kScl = GPIO_NUM_17;
constexpr uint8_t kAddress = 0x57;
constexpr int kSampleHz = 100;
constexpr int kWindowSeconds = 40;
// Keep manual acquisition continuous. Analysing at eight seconds used enough
// CPU time for the 32-sample hardware FIFO to fill before a two-second
// extension, which made the supposedly better retry window less reliable.
// Ten seconds still feels quick while normally containing enough pulse
// intervals to reject a transient wrist/contact artefact.
constexpr int kManualSeconds = 10;
constexpr int kSamples = kSampleHz * kWindowSeconds;
constexpr int kI2cAttempts = 5;
constexpr int kI2cRegisterTimeoutMs = 30;
// A worst-case 32-sample FIFO read is 192 data bytes. Including addressing and
// ACK bits it takes about 70 ms at 25 kHz; a 30 ms timeout made any full FIFO
// impossible to drain and turned one scheduling delay into a permanent busy
// cascade. Keep register operations short, but budget the actual burst length.
constexpr int kI2cFifoTimeoutMs = 120;
constexpr int kMaximumWindowGlitches = 3;
constexpr uint8_t kMaximumLedCurrent = 0x7F;
constexpr int64_t kWindowPeriodUs = 5LL * 60 * 1000000;
constexpr int64_t kFallResponseUs = 7LL * 1000000;

// MAX30102 register map.
constexpr uint8_t kRegIntStatus1 = 0x00;
constexpr uint8_t kRegIntEnable1 = 0x02;
constexpr uint8_t kRegIntEnable2 = 0x03;
constexpr uint8_t kRegFifoWrite = 0x04;
constexpr uint8_t kRegOverflow = 0x05;
constexpr uint8_t kRegFifoRead = 0x06;
constexpr uint8_t kRegFifoData = 0x07;
constexpr uint8_t kRegFifoConfig = 0x08;
constexpr uint8_t kRegMode = 0x09;
constexpr uint8_t kRegSpo2 = 0x0A;
constexpr uint8_t kRegLedRed = 0x0C;
constexpr uint8_t kRegLedIr = 0x0D;
constexpr uint8_t kRegPartId = 0xFF;

i2c_master_bus_handle_t g_bus = nullptr;
i2c_master_dev_handle_t g_sensor = nullptr;
std::atomic<bool> g_available{false};
std::atomic<bool> g_worn{false};
std::atomic<uint32_t> g_steps{0};
std::atomic<int> g_battery{-1};
std::atomic<float> g_linear_g{0.0F};
std::atomic<float> g_gyro_dps{0.0F};
std::atomic<int64_t> g_last_motion_us{0};
FallDetector g_fall_detector; // accessed only by the IMU sampling task
portMUX_TYPE g_move_mux = portMUX_INITIALIZER_UNLOCKED;
struct MoveBout { std::time_t at = 0; uint32_t seconds = 0; };
MoveBout g_move_bouts[12]{};
uint32_t g_move_seconds = 0;
int64_t g_move_last_tick = 0, g_move_last_active = 0;
int g_move_index = -1;
std::atomic<bool> g_activity_dirty{false};
uint32_t g_activity_day = 0, g_persisted_steps = 0;
int64_t g_activity_saved_us = 0;
std::atomic<int64_t> g_fall_candidate_us{0};
std::atomic<int64_t> g_fall_episode{0};
std::atomic<int64_t> g_fall_deadline_us{0};
std::atomic<bool> g_fall_pending{false};
std::atomic<bool> g_fall_confirmed{false};
std::atomic<uint32_t> g_sequence{0};
std::atomic<float> g_last_hr{0.0F};
std::atomic<int64_t> g_last_hr_us{0};
std::atomic<float> g_last_spo2{0.0F};
std::atomic<int> g_quality{0};  // 0 none, 1 poor, 2 fair, 3 good
std::atomic<WearableMeasurementState> g_manual_state{WearableMeasurementState::Idle};
std::atomic<bool> g_manual_requested{false};
std::atomic<int> g_manual_progress{0};
std::atomic<float> g_manual_hr{0.0F};
std::atomic<float> g_manual_spo2{0.0F};
std::atomic<int> g_manual_quality{0};
std::atomic<int> g_manual_attempt{0};
std::atomic<bool> g_manual_needs_reset{false};
std::atomic<bool> g_manual_cancel{false};
std::atomic<uint32_t> g_i2c_glitches{0};
std::atomic<uint32_t> g_fifo_overflows{0};
// This exact optical assembly repeatedly converges near 0x5d. Remember the
// latest gain instead of making every button press climb again from 0x1f.
// Only health_task accesses it, so it does not need cross-task locking.
uint8_t g_capture_led_current = 0x5D;

portMUX_TYPE g_activity_mux = portMUX_INITIALIZER_UNLOCKED;
char g_activity[16] = "unknown";
int64_t g_last_step_peak_us = 0;
int g_candidate_steps = 0;
bool g_step_bout = false;
bool g_step_above = false;
std::atomic<uint32_t> g_steps_last_sent{0};

portMUX_TYPE g_queue_mux = portMUX_INITIALIZER_UNLOCKED;
SemaphoreHandle_t g_storage_lock = nullptr;
char g_pending_ids[3][40]{};
char g_pending_json[3][760]{};
std::atomic<int64_t> g_last_send_us{0};

const char *quality_name(int quality) {
    if (quality >= 3) return "GOOD";
    if (quality == 2) return "FAIR";
    if (quality == 1) return "POOR";
    return "NONE";
}

esp_err_t write_reg(uint8_t reg, uint8_t value) {
    const uint8_t bytes[] = {reg, value};
    esp_err_t result = ESP_FAIL;
    for (int attempt = 0; attempt < kI2cAttempts; ++attempt) {
        result = i2c_master_transmit(
            g_sensor, bytes, sizeof(bytes), kI2cRegisterTimeoutMs);
        if (result == ESP_OK) return result;
        ++g_i2c_glitches;
        if (attempt == 1 && g_bus) i2c_master_bus_reset(g_bus);
        vTaskDelay(pdMS_TO_TICKS(4));
    }
    return result;
}

esp_err_t read_reg(uint8_t reg, uint8_t *value) {
    esp_err_t result = ESP_FAIL;
    for (int attempt = 0; attempt < kI2cAttempts; ++attempt) {
        result = i2c_master_transmit_receive(
            g_sensor, &reg, 1, value, 1, kI2cRegisterTimeoutMs);
        if (result == ESP_OK) return result;
        ++g_i2c_glitches;
        if (attempt == 1 && g_bus) i2c_master_bus_reset(g_bus);
        vTaskDelay(pdMS_TO_TICKS(4));
    }
    return result;
}

// Drain all samples currently waiting in one transaction, just like the
// proven max30102_read.py path. Besides preserving an evenly spaced waveform,
// this cuts the old three-I2C-transactions-per-sample load by roughly 10x.
esp_err_t read_fifo(uint32_t *red, uint32_t *ir, int capacity, int *out_count) {
    if (!red || !ir || !out_count || capacity <= 0) return ESP_ERR_INVALID_ARG;
    *out_count = 0;
    uint8_t write_ptr = 0, read_ptr = 0;
    esp_err_t result = read_reg(kRegFifoWrite, &write_ptr);
    if (result != ESP_OK) return result;
    if ((result = read_reg(kRegFifoRead, &read_ptr)) != ESP_OK) return result;
    int pending = (write_ptr - read_ptr) & 0x1F;
    bool overflowed = false;
    if (pending == 0) {
        uint8_t overflow = 0;
        if ((result = read_reg(kRegOverflow, &overflow)) != ESP_OK) return result;
        if (overflow == 0) return ESP_ERR_NOT_FOUND;
        pending = 32;
        overflowed = true;
        ++g_fifo_overflows;
    }
    pending = std::min(pending, std::min(capacity, 32));
    uint8_t bytes[32 * 6]{};
    result = ESP_FAIL;
    for (int attempt = 0; attempt < kI2cAttempts; ++attempt) {
        result = i2c_master_transmit_receive(
            g_sensor, &kRegFifoData, 1, bytes, pending * 6, kI2cFifoTimeoutMs);
        if (result == ESP_OK) break;
        ++g_i2c_glitches;
        if (attempt == 1 && g_bus) i2c_master_bus_reset(g_bus);
        vTaskDelay(pdMS_TO_TICKS(4));
    }
    if (result != ESP_OK) return result;
    for (int sample = 0; sample < pending; ++sample) {
        const int i = sample * 6;
        red[sample] = ((static_cast<uint32_t>(bytes[i]) << 16) |
                       (static_cast<uint32_t>(bytes[i + 1]) << 8) |
                       bytes[i + 2]) & 0x3FFFF;
        ir[sample] = ((static_cast<uint32_t>(bytes[i + 3]) << 16) |
                      (static_cast<uint32_t>(bytes[i + 4]) << 8) |
                      bytes[i + 5]) & 0x3FFFF;
    }
    if (overflowed) write_reg(kRegOverflow, 0);
    *out_count = pending;
    return ESP_OK;
}

esp_err_t sensor_configure(bool recover_bus) {
    if (recover_bus) {
        ESP_RETURN_ON_ERROR(i2c_master_bus_reset(g_bus), kTag, "reset I2C bus");
    }
    ESP_RETURN_ON_ERROR(write_reg(kRegMode, 0x40), kTag, "sensor reset");
    bool reset_complete = false;
    for (int attempt = 0; attempt < 100; ++attempt) {
        uint8_t mode = 0x40;
        const esp_err_t result = read_reg(kRegMode, &mode);
        if (result == ESP_OK && !(mode & 0x40)) {
            reset_complete = true;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (!reset_complete) return ESP_ERR_TIMEOUT;

    ESP_RETURN_ON_ERROR(write_reg(kRegIntEnable1, 0), kTag, "interrupt 1 disable");
    ESP_RETURN_ON_ERROR(write_reg(kRegIntEnable2, 0), kTag, "interrupt 2 disable");
    ESP_RETURN_ON_ERROR(write_reg(kRegFifoWrite, 0), kTag, "fifo write ptr");
    ESP_RETURN_ON_ERROR(write_reg(kRegOverflow, 0), kTag, "fifo overflow");
    ESP_RETURN_ON_ERROR(write_reg(kRegFifoRead, 0), kTag, "fifo read ptr");
    ESP_RETURN_ON_ERROR(write_reg(kRegFifoConfig, 0x50), kTag, "fifo config");
    ESP_RETURN_ON_ERROR(write_reg(kRegMode, 0x03), kTag, "spo2 mode");
    ESP_RETURN_ON_ERROR(write_reg(kRegSpo2, 0x6F), kTag, "spo2 config");
    ESP_RETURN_ON_ERROR(write_reg(kRegLedRed, 0x1F), kTag, "red led");
    ESP_RETURN_ON_ERROR(write_reg(kRegLedIr, 0x1F), kTag, "ir led");
    uint8_t ignored = 0;
    read_reg(kRegIntStatus1, &ignored);
    return ESP_OK;
}

esp_err_t sensor_init() {
    i2c_master_bus_config_t bus_config{};
    bus_config.i2c_port = I2C_NUM_0;
    bus_config.sda_io_num = kSda;
    bus_config.scl_io_num = kScl;
    bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
    bus_config.glitch_ignore_cnt = 7;
    // The proven diagnostic measured the module's own pull-ups at 1.8 V.
    // The breakout idles close to 1.8 V, below the ESP32-S3's guaranteed VIH.
    // Its external pull-ups remain primary; the weak (~45k) internal pull-ups
    // add a little noise margin without materially loading the sensor bus.
    bus_config.flags.enable_internal_pullup = true;
    esp_err_t result = i2c_new_master_bus(&bus_config, &g_bus);
    if (result != ESP_OK) return result;
    i2c_device_config_t device_config{};
    device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    device_config.device_address = kAddress;
    // This expansion bus has shown marginal-high/busy periods when the LEDs
    // are driven. 25 kHz is still ample for its small FIFO bursts and gives
    // the weakly pulled-up lines the widest practical timing margin.
    device_config.scl_speed_hz = 25000;
    if ((result = i2c_master_bus_add_device(g_bus, &device_config, &g_sensor)) != ESP_OK) {
        return result;
    }
    uint8_t part = 0;
    if ((result = read_reg(kRegPartId, &part)) != ESP_OK || part != 0x15) {
        ESP_LOGW(kTag, "MAX30102 part id unavailable (result=%s id=0x%02x)",
                 esp_err_to_name(result), part);
        return result == ESP_OK ? ESP_ERR_NOT_FOUND : result;
    }
    // Match the proven max30102_read.py default preset: 400 Hz with FIFO
    // average 4 -> 100 stored samples/s, 18-bit samples, 16384 nA ADC range.
    ESP_RETURN_ON_ERROR(sensor_configure(false), kTag, "configure MAX30102");
    ESP_LOGI(kTag, "MAX30102 active at 0x57 on isolated SDA=18 SCL=17 bus");
    return ESP_OK;
}

void set_activity(const char *name) {
    portENTER_CRITICAL(&g_activity_mux);
    std::snprintf(g_activity, sizeof(g_activity), "%s", name ? name : "unknown");
    portEXIT_CRITICAL(&g_activity_mux);
}

void get_activity(char *out, size_t size) {
    portENTER_CRITICAL(&g_activity_mux);
    std::snprintf(out, size, "%s", g_activity);
    portEXIT_CRITICAL(&g_activity_mux);
}

void save_queue() {
    if (!g_storage_lock || xSemaphoreTake(g_storage_lock, pdMS_TO_TICKS(2000)) != pdTRUE) return;
    auto *snapshot = static_cast<char (*)[760]>(
        heap_caps_malloc(sizeof(g_pending_json), MALLOC_CAP_SPIRAM));
    if (!snapshot) {
        xSemaphoreGive(g_storage_lock);
        return;
    }
    portENTER_CRITICAL(&g_queue_mux);
    std::memcpy(snapshot, g_pending_json, sizeof(g_pending_json));
    portEXIT_CRITICAL(&g_queue_mux);
    nvs_handle_t handle = 0;
    if (nvs_open("wearable", NVS_READWRITE, &handle) != ESP_OK) {
        heap_caps_free(snapshot);
        xSemaphoreGive(g_storage_lock);
        return;
    }
    for (int i = 0; i < 3; ++i) {
        char key[4];
        std::snprintf(key, sizeof(key), "q%d", i);
        if (snapshot[i][0]) nvs_set_str(handle, key, snapshot[i]);
        else nvs_erase_key(handle, key);
    }
    nvs_commit(handle);
    nvs_close(handle);
    heap_caps_free(snapshot);
    xSemaphoreGive(g_storage_lock);
}

void load_queue() {
    nvs_handle_t handle = 0;
    if (nvs_open("wearable", NVS_READONLY, &handle) != ESP_OK) return;
    char loaded[3][760]{};
    for (int i = 0; i < 3; ++i) {
        char key[4];
        std::snprintf(key, sizeof(key), "q%d", i);
        size_t size = sizeof(loaded[i]);
        nvs_get_str(handle, key, loaded[i], &size);
    }
    nvs_close(handle);
    portENTER_CRITICAL(&g_queue_mux);
    std::memcpy(g_pending_json, loaded, sizeof(loaded));
    for (int i = 0; i < 3; ++i) {
        const char *id = std::strstr(g_pending_json[i], "\"batch_id\":\"");
        if (id) {
            id += std::strlen("\"batch_id\":\"");
            const char *end = std::strchr(id, '"');
            const size_t count = end ? std::min<size_t>(end - id, sizeof(g_pending_ids[i]) - 1) : 0;
            std::memcpy(g_pending_ids[i], id, count);
            g_pending_ids[i][count] = 0;
        }
    }
    portEXIT_CRITICAL(&g_queue_mux);
}

uint32_t ist_day() {
    const auto now = std::time(nullptr);
    return now > 1700000000 ? static_cast<uint32_t>((now + 19800) / 86400) : 0;
}

struct ActivityStore {
    uint32_t version = 1, day = 0, steps = 0, move_seconds = 0;
    int32_t move_index = -1;
    MoveBout bouts[12]{};
};

void load_activity() {
    nvs_handle_t handle = 0;
    ActivityStore saved{};
    size_t size = sizeof(saved);
    if (nvs_open("wearable", NVS_READONLY, &handle) != ESP_OK) return;
    const esp_err_t result = nvs_get_blob(handle, "activity", &saved, &size);
    nvs_close(handle);
    if (result != ESP_OK || size != sizeof(saved) || saved.version != 1) return;
    const uint32_t today = ist_day();
    if (today && saved.day && saved.day != today) return;
    g_steps = saved.steps;
    g_steps_last_sent = saved.steps;
    g_persisted_steps = saved.steps;
    g_activity_day = saved.day;
    portENTER_CRITICAL(&g_move_mux);
    g_move_seconds = saved.move_seconds;
    g_move_index = std::clamp(static_cast<int>(saved.move_index), -1, 11);
    std::memcpy(g_move_bouts, saved.bouts, sizeof(g_move_bouts));
    portEXIT_CRITICAL(&g_move_mux);
    ESP_LOGI(kTag, "restored daily activity: %lu steps, %lu moving seconds",
             static_cast<unsigned long>(saved.steps),
             static_cast<unsigned long>(saved.move_seconds));
}

void persist_activity_if_due() {
    const uint32_t today = ist_day();
    if (today && g_activity_day && today != g_activity_day) {
        g_steps = 0;
        g_steps_last_sent = 0;
        g_persisted_steps = 0;
        portENTER_CRITICAL(&g_move_mux);
        g_move_seconds = 0;
        g_move_index = -1;
        std::memset(g_move_bouts, 0, sizeof(g_move_bouts));
        portEXIT_CRITICAL(&g_move_mux);
        g_activity_dirty = true;
    }
    if (today) g_activity_day = today;
    const int64_t now_us = esp_timer_get_time();
    const uint32_t steps = g_steps.load();
    const uint32_t new_steps = steps >= g_persisted_steps ? steps - g_persisted_steps : steps;
    if (!g_activity_dirty.load() ||
        (new_steps < 32 && now_us - g_activity_saved_us < 300000000)) return;
    ActivityStore saved{};
    saved.day = g_activity_day;
    saved.steps = steps;
    portENTER_CRITICAL(&g_move_mux);
    saved.move_seconds = g_move_seconds;
    saved.move_index = g_move_index;
    std::memcpy(saved.bouts, g_move_bouts, sizeof(saved.bouts));
    portEXIT_CRITICAL(&g_move_mux);
    if (!g_storage_lock || xSemaphoreTake(g_storage_lock, pdMS_TO_TICKS(2000)) != pdTRUE) return;
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open("wearable", NVS_READWRITE, &handle);
    if (result == ESP_OK) {
        result = nvs_set_blob(handle, "activity", &saved, sizeof(saved));
        if (result == ESP_OK) result = nvs_commit(handle);
        nvs_close(handle);
    }
    xSemaphoreGive(g_storage_lock);
    if (result == ESP_OK) {
        g_persisted_steps = steps;
        g_activity_saved_us = now_us;
        g_activity_dirty = false;
    }
}

void enqueue(const char *id, const char *json) {
    portENTER_CRITICAL(&g_queue_mux);
    int slot = -1;
    for (int i = 0; i < 3; ++i) if (!g_pending_json[i][0]) { slot = i; break; }
    if (slot < 0) {
        for (int i = 0; i < 2; ++i) {
            std::memcpy(g_pending_ids[i], g_pending_ids[i + 1], sizeof(g_pending_ids[i]));
            std::memcpy(g_pending_json[i], g_pending_json[i + 1], sizeof(g_pending_json[i]));
        }
        slot = 2;
    }
    std::snprintf(g_pending_ids[slot], sizeof(g_pending_ids[slot]), "%s", id);
    std::snprintf(g_pending_json[slot], sizeof(g_pending_json[slot]), "%s", json);
    portEXIT_CRITICAL(&g_queue_mux);
    save_queue();
}

void flush_queue() {
    if (!gateway_client_connected()) return;
    const int64_t now = esp_timer_get_time();
    if (now - g_last_send_us < 10000000) return;
    char payload[760]{};
    portENTER_CRITICAL(&g_queue_mux);
    for (int i = 0; i < 3; ++i) {
        if (g_pending_json[i][0]) {
            std::memcpy(payload, g_pending_json[i], sizeof(payload));
            payload[sizeof(payload) - 1] = 0;
            break;
        }
    }
    portEXIT_CRITICAL(&g_queue_mux);
    if (payload[0]) {
        gateway_client_send_event("health_telemetry", payload);
        g_last_send_us = now;
    }
}

void iso_now(char *out, size_t size) {
    const std::time_t now = std::time(nullptr);
    std::tm local{};
    localtime_r(&now, &local);
    std::strftime(out, size, "%Y-%m-%dT%H:%M:%S%z", &local);
}

struct Analysis {
    float hr = 0;
    float peak_hr = 0;
    float acf_hr = 0;
    float acf_score = 0;
    float acf_spread = 999;
    float channel_correlation = 0;
    float spo2 = 0;
    float pi = 0;
    float rr_cv = 1;
    float periodicity = 0;
    int peaks = 0;
    int quality = 1;
    int samples = 0;
    int read_errors = 0;
    float dc_ir = 0;
    float dc_red = 0;
    int led_current = 0;
    bool harmonic_corrected = false;
    bool paired_peaks = false;
};

struct RateEstimate {
    float hr = 0;
    float score = 0;
};

// Autocorrelation is an independent coherence check, not the rate source.
// Peak-to-peak timing below deliberately matches the standalone
// max30102_read.py path that is already proven on this optical assembly.
RateEstimate autocorrelation_rate(const float *pulse, int first, int last) {
    RateEstimate out{};
    const int count = last - first;
    constexpr int kMinLag = kSampleHz * 60 / 200;
    constexpr int kMaxLag = kSampleHz * 60 / 40;
    constexpr int kLagCount = kMaxLag - kMinLag + 1;
    if (!pulse || count < kMaxLag + kSampleHz) return out;

    double mean = 0;
    for (int i = first; i < last; ++i) mean += pulse[i];
    mean /= count;

    float correlations[kLagCount]{};
    int best_index = 0;
    float best_score = -1.0F;
    for (int index = 0; index < kLagCount; ++index) {
        const int lag = kMinLag + index;
        double cross = 0, left_power = 0, right_power = 0;
        for (int i = first; i < last - lag; ++i) {
            const double left = pulse[i] - mean;
            const double right = pulse[i + lag] - mean;
            cross += left * right;
            left_power += left * left;
            right_power += right * right;
        }
        const double denominator = std::sqrt(left_power * right_power);
        correlations[index] = denominator > 1e-9
                                  ? static_cast<float>(cross / denominator)
                                  : 0.0F;
        if (correlations[index] > best_score) {
            best_score = correlations[index];
            best_index = index;
        }
    }

    // Select the first strong local maximum. Later maxima are usually the
    // second/third repetition of the same pulse; a half-period dicrotic bump
    // is rejected unless it is nearly as coherent as the true fundamental.
    const float eligibility = std::max(0.28F, best_score * 0.78F);
    int chosen = best_index;
    for (int i = 1; i < kLagCount - 1; ++i) {
        if (correlations[i] >= eligibility &&
            correlations[i] >= correlations[i - 1] &&
            correlations[i] > correlations[i + 1]) {
            chosen = i;
            break;
        }
    }
    float lag = static_cast<float>(kMinLag + chosen);
    if (chosen > 0 && chosen + 1 < kLagCount) {
        const float left = correlations[chosen - 1];
        const float centre = correlations[chosen];
        const float right = correlations[chosen + 1];
        const float curvature = left - 2.0F * centre + right;
        if (std::fabs(curvature) > 1e-6F) {
            lag += std::clamp(0.5F * (left - right) / curvature, -0.5F, 0.5F);
        }
    }
    out.hr = 60.0F * kSampleHz / lag;
    out.score = correlations[chosen];
    return out;
}

Analysis analyse(const uint32_t *red, const uint32_t *ir, int count, int motion_rejects) {
    Analysis out{};
    if (!red || !ir || count < kSampleHz * kManualSeconds) return out;
    double red_sum = 0, ir_sum = 0;
    for (int i = 0; i < count; ++i) { red_sum += red[i]; ir_sum += ir[i]; }
    const double red_dc = red_sum / count, ir_dc = ir_sum / count;
    out.dc_red = static_cast<float>(red_dc);
    out.dc_ir = static_cast<float>(ir_dc);
    auto *red_smooth = static_cast<float *>(
        heap_caps_malloc(count * sizeof(float), MALLOC_CAP_SPIRAM));
    auto *ir_smooth = static_cast<float *>(
        heap_caps_malloc(count * sizeof(float), MALLOC_CAP_SPIRAM));
    auto *red_pulse = static_cast<float *>(
        heap_caps_malloc(count * sizeof(float), MALLOC_CAP_SPIRAM));
    auto *ir_pulse = static_cast<float *>(
        heap_caps_malloc(count * sizeof(float), MALLOC_CAP_SPIRAM));
    auto *ir_rate = static_cast<float *>(
        heap_caps_malloc(count * sizeof(float), MALLOC_CAP_SPIRAM));
    auto *integer_scratch = static_cast<int *>(
        heap_caps_calloc(4 * 160, sizeof(int), MALLOC_CAP_SPIRAM));
    if (!red_smooth || !ir_smooth || !red_pulse || !ir_pulse || !ir_rate ||
        !integer_scratch) {
        heap_caps_free(red_smooth);
        heap_caps_free(ir_smooth);
        heap_caps_free(red_pulse);
        heap_caps_free(ir_pulse);
        heap_caps_free(ir_rate);
        heap_caps_free(integer_scratch);
        return out;
    }
    int *peaks = integer_scratch;
    int *intervals = integer_scratch + 160;
    int *paired = integer_scratch + 320;
    int *accepted_intervals = integer_scratch + 480;

    // Direct port of max30102_read.py's proven bandpass: a 100 ms moving
    // average followed by subtraction of a 1.2 second moving baseline.
    constexpr int kSmoothWidth = kSampleHz / 10;
    constexpr int kBaselineWidth = kSampleHz * 12 / 10;
    for (int i = 0; i < count; ++i) {
        double rs = 0, is = 0;
        const int first = std::max(0, i - kSmoothWidth / 2);
        const int last = std::min(count, first + kSmoothWidth);
        for (int j = first; j < last; ++j) {
            rs += red[j];
            is += ir[j];
        }
        red_smooth[i] = static_cast<float>(rs / std::max(1, last - first));
        ir_smooth[i] = static_cast<float>(is / std::max(1, last - first));
    }
    for (int i = 0; i < count; ++i) {
        double rb = 0, ib = 0;
        const int first = std::max(0, i - kBaselineWidth / 2);
        const int last = std::min(count, first + kBaselineWidth);
        for (int j = first; j < last; ++j) {
            rb += red_smooth[j];
            ib += ir_smooth[j];
        }
        red_pulse[i] = red_smooth[i] - static_cast<float>(rb / std::max(1, last - first));
        ir_pulse[i] = ir_smooth[i] - static_cast<float>(ib / std::max(1, last - first));
    }

    // 150 ms running average used only by the rate estimator. The original
    // pulse remains untouched for PI and the red/IR SpO2 ratio.
    constexpr int kRateSmoothWidth = kSampleHz * 15 / 100;
    double rate_sum = 0;
    int rate_first = 0;
    for (int i = 0; i < count; ++i) {
        rate_sum += ir_pulse[i];
        while (i - rate_first + 1 > kRateSmoothWidth) {
            rate_sum -= ir_pulse[rate_first++];
        }
        ir_rate[i] = static_cast<float>(rate_sum / (i - rate_first + 1));
    }

    double red_var = 0, ir_var = 0;
    constexpr int kEdgeSamples = kSampleHz * 7 / 10;
    for (int i = kEdgeSamples; i < count - kEdgeSamples; ++i) {
        red_var += static_cast<double>(red_pulse[i]) * red_pulse[i];
        ir_var += static_cast<double>(ir_pulse[i]) * ir_pulse[i];
    }
    const int analysed_count = std::max(1, count - 2 * kEdgeSamples);
    const double red_ac = std::sqrt(red_var / analysed_count);
    const double ir_ac = std::sqrt(ir_var / analysed_count);
    const double threshold = ir_ac * 0.35;

    // Same local maximum/refractory replacement used by the working script.
    int peak_count = 0;
    constexpr int kMinGap = kSampleHz * 60 / 200;
    for (int i = kEdgeSamples + 1;
         i < count - kEdgeSamples - 1 && peak_count < 160; ++i) {
        if (ir_pulse[i] <= threshold || ir_pulse[i] < ir_pulse[i - 1] ||
            ir_pulse[i] <= ir_pulse[i + 1]) continue;
        if (peak_count && i - peaks[peak_count - 1] < kMinGap) {
            if (ir_pulse[i] > ir_pulse[peaks[peak_count - 1]]) peaks[peak_count - 1] = i;
        } else {
            peaks[peak_count++] = i;
        }
    }
    out.peaks = peak_count;

    int interval_count = 0;
    for (int i = 1; i < peak_count; ++i) intervals[interval_count++] = peaks[i] - peaks[i - 1];
    // A pronounced dicrotic notch can create alternating short/long peak
    // gaps. Detect that morphology before sorting: pairing adjacent peaks then
    // recovers the real beat period, while a genuine fast pulse has uniform
    // gaps and is left untouched.
    float paired_hr = 0.0F;
    float paired_cv = 1.0F;
    float alternating_imbalance = 0.0F;
    if (interval_count >= 6) {
        double even_sum = 0, odd_sum = 0;
        int even_count = 0, odd_count = 0;
        for (int i = 0; i < interval_count; ++i) {
            if (i & 1) { odd_sum += intervals[i]; ++odd_count; }
            else { even_sum += intervals[i]; ++even_count; }
        }
        const double even_mean = even_sum / std::max(1, even_count);
        const double odd_mean = odd_sum / std::max(1, odd_count);
        alternating_imbalance = static_cast<float>(
            std::fabs(even_mean - odd_mean) / std::max(1.0, (even_mean + odd_mean) * 0.5));

        const int paired_count = peak_count - 2;
        double paired_sum = 0;
        for (int i = 2; i < peak_count; ++i) {
            paired[i - 2] = peaks[i] - peaks[i - 2];
            paired_sum += paired[i - 2];
        }
        const double paired_mean = paired_sum / paired_count;
        double paired_variance = 0;
        for (int i = 0; i < paired_count; ++i) {
            const double delta = paired[i] - paired_mean;
            paired_variance += delta * delta;
        }
        std::sort(paired, paired + paired_count);
        paired_hr = static_cast<float>(60.0 * kSampleHz / paired[paired_count / 2]);
        paired_cv = static_cast<float>(
            std::sqrt(paired_variance / paired_count) / std::max(1.0, paired_mean));
    }
    if (interval_count >= 2) {
        std::sort(intervals, intervals + interval_count);
        const double median = intervals[interval_count / 2];
        int accepted = 0;
        for (int i = 0; i < interval_count; ++i) {
            if (intervals[i] > 0.5 * median && intervals[i] < 1.8 * median) {
                accepted_intervals[accepted++] = intervals[i];
            }
        }
        if (accepted > 0) {
            std::sort(accepted_intervals, accepted_intervals + accepted);
            const double accepted_median = accepted_intervals[accepted / 2];
            double sum = 0, variance = 0;
            for (int i = 0; i < accepted; ++i) sum += accepted_intervals[i];
            const double mean = sum / accepted;
            for (int i = 0; i < accepted; ++i) {
                const double delta = accepted_intervals[i] - mean;
                variance += delta * delta;
            }
            out.hr = static_cast<float>(60.0 * kSampleHz / accepted_median);
            out.peak_hr = out.hr;
            out.rr_cv = static_cast<float>(
                std::sqrt(variance / accepted) / std::max(1.0, mean));
            out.periodicity = std::clamp(1.0F - out.rr_cv, 0.0F, 1.0F);
        }
    }
    if (out.hr >= 105.0F && paired_hr >= 45.0F && paired_hr <= 110.0F &&
        alternating_imbalance >= 0.12F && paired_cv + 0.03F < out.rr_cv) {
        out.hr = paired_hr;
        out.peak_hr = paired_hr;
        out.rr_cv = paired_cv;
        out.periodicity = std::clamp(1.0F - paired_cv, 0.0F, 1.0F);
        out.harmonic_corrected = true;
        out.paired_peaks = true;
    }

    const RateEstimate full_rate = autocorrelation_rate(
        ir_rate, kEdgeSamples, count - kEdgeSamples);
    const int middle = count / 2;
    const RateEstimate first_rate = autocorrelation_rate(
        ir_rate, kEdgeSamples, middle);
    const RateEstimate second_rate = autocorrelation_rate(
        ir_rate, middle, count - kEdgeSamples);
    out.acf_hr = full_rate.hr;
    out.acf_score = std::clamp(full_rate.score, 0.0F, 1.0F);
    if (first_rate.hr > 0 && second_rate.hr > 0) {
        out.acf_spread = std::fabs(first_rate.hr - second_rate.hr);
    }
    // Never let a weak or harmonically wrong ACF replace the pulse-interval
    // result. That was the direct cause of adjacent 40/200 bpm outputs: scores
    // as low as 0.099 were nevertheless being promoted to the displayed HR.
    // ACF remains in telemetry and must agree before the ordinary FAIR/GOOD
    // path is trusted.

    double channel_cross = 0, channel_red = 0, channel_ir = 0;
    for (int i = kEdgeSamples; i < count - kEdgeSamples; ++i) {
        channel_cross += static_cast<double>(red_pulse[i]) * ir_pulse[i];
        channel_red += static_cast<double>(red_pulse[i]) * red_pulse[i];
        channel_ir += static_cast<double>(ir_pulse[i]) * ir_pulse[i];
    }
    const double channel_denominator = std::sqrt(channel_red * channel_ir);
    if (channel_denominator > 1e-9) {
        out.channel_correlation = static_cast<float>(channel_cross / channel_denominator);
    }
    out.pi = static_cast<float>(100.0 * ir_ac / std::max(1.0, ir_dc));
    const double ratio = (red_ac / std::max(1.0, red_dc)) /
                         std::max(0.00001, ir_ac / std::max(1.0, ir_dc));
    out.spo2 = static_cast<float>(std::clamp(
        -45.060 * ratio * ratio + 30.354 * ratio + 94.845, 70.0, 100.0));
    const bool plausible = out.hr >= 35 && out.hr <= 220;
    const float motion_fraction = static_cast<float>(motion_rejects) / count;
    const bool estimators_agree = out.peak_hr > 0.0F &&
        std::fabs(out.acf_hr - out.peak_hr) <=
            std::max(6.0F, out.acf_hr * 0.08F);
    // The mounted module's real wrist pulse is low-amplitude (observed PI
    // 0.021-0.022%) but strongly shared by RED and IR (correlation 0.77-0.85),
    // so correlation carries more weight than the finger probe's PI cutoff.
    // Ordinary morphology needs independent peak/ACF agreement. A pronounced
    // alternating notch is a special, explicitly detected morphology: paired
    // peak intervals can be accepted without ACF only when there are at least
    // eight observed peaks, strong RED/IR agreement and stable paired timing.
    // This rejects the five/six-peak 45/54 bpm artefacts while preserving a
    // well-supported notch-corrected pulse.
    const bool consensus_rate = peak_count >= 7 && out.acf_score >= 0.35F &&
                                estimators_agree && out.rr_cv <= 0.25F;
    const bool paired_rate = out.paired_peaks && peak_count >= 8 &&
                             out.rr_cv <= 0.35F &&
                             out.channel_correlation >= 0.80F;
    const bool stable_rate = consensus_rate || paired_rate;
    const bool optical_pulse =
        out.pi >= 0.015F && out.channel_correlation >= 0.60F;
    if (plausible && ir_dc >= 10000.0 && ir_dc <= 220000.0 &&
        consensus_rate && optical_pulse && out.pi <= 5.0F &&
        out.acf_score >= 0.60F && out.acf_spread <= 8.0F &&
        out.pi >= 0.20F && out.channel_correlation >= 0.65F &&
        motion_fraction <= 0.10F) {
        out.quality = 3;
    } else if (plausible && ir_dc >= 10000.0 && ir_dc <= 220000.0 &&
               out.pi <= 5.0F && stable_rate &&
               optical_pulse && motion_fraction <= 0.20F) {
        out.quality = 2;
    }
    heap_caps_free(red_smooth);
    heap_caps_free(ir_smooth);
    heap_caps_free(red_pulse);
    heap_caps_free(ir_pulse);
    heap_caps_free(ir_rate);
    heap_caps_free(integer_scratch);
    return out;
}

void build_and_queue(const Analysis *analysis, const char *fall_status = nullptr,
                     const char *fall_reason = nullptr) {
    char stamp[40];
    iso_now(stamp, sizeof(stamp));
    const uint32_t sequence = ++g_sequence;
    char id[40];
    std::snprintf(id, sizeof(id), "wk-%lu-%lu", static_cast<unsigned long>(std::time(nullptr)),
                  static_cast<unsigned long>(sequence));
    char activity[16];
    get_activity(activity, sizeof(activity));
    const uint32_t steps = g_steps.load();
    uint32_t previous_steps = g_steps_last_sent.load();
    while (steps > previous_steps &&
           !g_steps_last_sent.compare_exchange_weak(previous_steps, steps)) {}
    const uint32_t delta = steps > previous_steps ? steps - previous_steps : 0;
    char readings[360] = "{}";
    if (analysis) {
        std::snprintf(readings, sizeof(readings),
            "{\"heart_rate\":{\"value\":%.1f,\"quality\":\"%s\",\"signal\":{\"estimator_version\":2,\"pi\":%.3f,\"rr_cv\":%.3f,\"n_peaks\":%d,\"acf\":%.3f,\"acf_hr\":%.1f,\"peak_hr\":%.1f,\"acf_spread\":%.1f,\"channel_corr\":%.3f,\"i2c_glitches\":%d,\"harmonic_corrected\":%s,\"paired_peaks\":%s}},"
            "\"spo2\":{\"value\":%.1f,\"quality\":\"EXPERIMENTAL\",\"calibrated\":false}}",
            analysis->hr, quality_name(analysis->quality), analysis->pi,
            analysis->rr_cv, analysis->peaks, analysis->acf_score,
            analysis->acf_hr, analysis->peak_hr, analysis->acf_spread,
            analysis->channel_correlation, analysis->read_errors,
            analysis->harmonic_corrected ? "true" : "false",
            analysis->paired_peaks ? "true" : "false", analysis->spo2);
    }
    char fall[150] = "{}";
    if (fall_status) {
        std::snprintf(fall, sizeof(fall),
                      "{\"event_id\":\"fall-%lld\",\"status\":\"%s\",\"reason\":\"%s\"}",
                      static_cast<long long>(g_fall_episode.load()),
                      fall_status, fall_reason ? fall_reason : "");
    }
    char payload[760];
    std::snprintf(payload, sizeof(payload),
        "\"batch_id\":\"%s\",\"device_id\":\"wearable-kiki\","
        "\"captured_at\":\"%s\",\"sequence\":%lu,\"steps_delta\":%lu,"
        "\"steps_total\":%lu,\"activity\":\"%s\",\"worn\":%s,"
        "\"battery_percent\":%d,\"readings\":%s,\"fall\":%s",
        id, stamp, static_cast<unsigned long>(sequence), static_cast<unsigned long>(delta),
        static_cast<unsigned long>(steps), activity, g_worn.load() ? "true" : "false",
        g_battery.load(), readings, fall);
    enqueue(id, payload);
    if (fall_status && gateway_client_connected()) {
        // A fall check overtakes audio/diagnostics; the same id remains in the
        // durable queue until the Pi acknowledges it.
        gateway_client_send_event("health_fall", payload);
    }
    flush_queue();
}

__attribute__((noinline)) bool capture_window(Analysis *out, bool manual = false) {
    auto *red = static_cast<uint32_t *>(heap_caps_malloc(kSamples * sizeof(uint32_t), MALLOC_CAP_SPIRAM));
    auto *ir = static_cast<uint32_t *>(heap_caps_malloc(kSamples * sizeof(uint32_t), MALLOC_CAP_SPIRAM));
    if (!red || !ir) { heap_caps_free(red); heap_caps_free(ir); return false; }
    const uint32_t glitches_at_start = g_i2c_glitches.load();
    const uint32_t overflows_at_start = g_fifo_overflows.load();
    // The standalone script's default (non --wrist) preset is the one proven
    // on this exact optical placement. Converge its LED current toward an
    // unsaturated 85k..180k IR target before collecting the window.
    uint8_t led_current = g_capture_led_current;
    write_reg(kRegLedRed, led_current);
    write_reg(kRegLedIr, led_current);
    write_reg(kRegFifoWrite, 0);
    write_reg(kRegOverflow, 0);
    write_reg(kRegFifoRead, 0);
    vTaskDelay(pdMS_TO_TICKS(200));
    int count = 0, motion_rejects = 0, read_errors = 0;
    uint32_t gain_level = 0;
    for (int attempt = 0; attempt < 4; ++attempt) {
        uint64_t ir_sum = 0;
        int seen = 0;
        const int64_t gain_deadline = esp_timer_get_time() + 450000;
        while (esp_timer_get_time() < gain_deadline) {
            uint32_t sample_red[32]{}, sample_ir[32]{};
            int samples_read = 0;
            const esp_err_t read_result = read_fifo(
                sample_red, sample_ir, 32, &samples_read);
            if (read_result == ESP_OK) {
                for (int i = 0; i < samples_read; ++i) ir_sum += sample_ir[i];
                seen += samples_read;
            } else if (read_result != ESP_ERR_NOT_FOUND) {
                ++read_errors;
            }
            vTaskDelay(pdMS_TO_TICKS(40));
        }
        if (!seen) continue;
        const uint32_t level = static_cast<uint32_t>(ir_sum / seen);
        gain_level = level;
        int next = led_current;
        if (level >= 220000) next = std::max(0x08, static_cast<int>(led_current * 0.50F));
        else if (level > 180000) next = std::max(0x08, static_cast<int>(led_current * 0.70F));
        else if (level < 85000) {
            // Optical response is close to linear in this range. Jump toward
            // the middle of the proven window so a low-signal wrist does not
            // need four slow multiplicative passes.
            const float gain = std::clamp(120000.0F / std::max(1.0F, static_cast<float>(level)),
                                          1.1F, 3.0F);
            next = std::min(static_cast<int>(kMaximumLedCurrent),
                            std::max(static_cast<int>(led_current) + 4,
                                     static_cast<int>(led_current * gain)));
        }
        else break;
        if (next == led_current) break;
        led_current = static_cast<uint8_t>(next);
        write_reg(kRegLedRed, led_current);
        write_reg(kRegLedIr, led_current);
        vTaskDelay(pdMS_TO_TICKS(150));
    }
    const uint32_t gain_glitches = g_i2c_glitches.load() - glitches_at_start;
    ESP_LOGI(kTag, "PPG gain ready: led=0x%02x ir=%lu glitches=%lu",
             led_current, static_cast<unsigned long>(gain_level),
             static_cast<unsigned long>(gain_glitches));
    vTaskDelay(pdMS_TO_TICKS(500));
    write_reg(kRegFifoWrite, 0);
    write_reg(kRegOverflow, 0);
    write_reg(kRegFifoRead, 0);

    int target_samples = (manual ? kManualSeconds : kWindowSeconds) * kSampleHz;
    const int64_t deadline = esp_timer_get_time() +
                             (target_samples / kSampleHz + 8LL) * 1000000;
    int consecutive_errors = 0;
    const auto acquisition_faults = [&]() {
        const int glitches = static_cast<int>(g_i2c_glitches.load() - glitches_at_start);
        const int overflows = static_cast<int>(g_fifo_overflows.load() - overflows_at_start);
        return std::max(read_errors, glitches) + overflows;
    };
    TickType_t wake = xTaskGetTickCount();
    while (count < target_samples && g_worn.load() && esp_timer_get_time() < deadline &&
           !(manual && g_manual_cancel.load()) &&
           g_i2c_glitches.load() - glitches_at_start <= kMaximumWindowGlitches) {
        // If the wearer asks while an automatic window is already in flight,
        // turn that very window into the requested measurement. This avoids a
        // second 40-second wait and still keeps one owner of the sensor/FIFO.
        if (!manual && g_manual_requested.exchange(false)) {
            manual = true;
            target_samples = std::min(target_samples, kManualSeconds * kSampleHz);
            g_manual_state = WearableMeasurementState::Measuring;
        }
        int samples_read = 0;
        const esp_err_t read_result = read_fifo(
            red + count, ir + count, target_samples - count, &samples_read);
        if (read_result == ESP_OK) {
            consecutive_errors = 0;
            if (g_linear_g.load() > 0.12F || g_gyro_dps.load() > 24.0F) {
                motion_rejects += samples_read;
            }
            count += samples_read;
            if (manual) g_manual_progress = std::min(99, count * 100 / target_samples);
        } else if (read_result != ESP_ERR_NOT_FOUND) {
            ++read_errors;
            ++consecutive_errors;
            // One returned error already means all five low-level attempts
            // failed. Do not spend the whole window hammering a wedged bus or
            // mix a discontinuous waveform into a plausible-looking result.
            if (consecutive_errors >= 3 || read_errors >= 3) break;
        }
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(40));
    }
    write_reg(kRegLedRed, 0x1F);
    write_reg(kRegLedIr, 0x1F);
    if (count >= kSampleHz * kManualSeconds) {
        *out = analyse(red, ir, count, motion_rejects);
        out->samples = count;
        out->read_errors = acquisition_faults();
        out->led_current = led_current;
        if (out->read_errors > 2) out->quality = 1;
    }
    // Preserve fault evidence even when the bus failed before there were
    // enough samples to run analysis, or exactly after an evaluation point.
    out->samples = count;
    out->read_errors = acquisition_faults();
    out->led_current = led_current;
    if (out->read_errors > 2) out->quality = 1;
    if (out->read_errors <= 2 && out->dc_ir >= 30000.0F && out->dc_ir <= 180000.0F &&
        out->pi <= 5.0F) {
        g_capture_led_current = led_current;
    }
    if (out->read_errors > 2) {
        ESP_LOGW(kTag, "PPG bus fault: glitches=%d sda=%d scl=%d",
                 out->read_errors, gpio_get_level(kSda), gpio_get_level(kScl));
    }
    heap_caps_free(red);
    heap_caps_free(ir);
    return count >= kSampleHz * kManualSeconds && out->read_errors <= 2;
}

void finish_manual_measurement(const Analysis &analysis, bool captured) {
    ESP_LOGI(kTag,
             "manual PPG: captured=%d samples=%d errors=%d quality=%s "
             "hr=%.1f spo2=%.1f pi=%.3f",
             captured, analysis.samples, analysis.read_errors,
             quality_name(analysis.quality), static_cast<double>(analysis.hr),
             static_cast<double>(analysis.spo2), static_cast<double>(analysis.pi));
    ESP_LOGI(kTag,
             "manual signal: peaks=%d peak_hr=%.1f acf_hr=%.1f acf=%.3f "
             "spread=%.1f corr=%.3f rr_cv=%.3f",
             analysis.peaks, static_cast<double>(analysis.peak_hr),
             static_cast<double>(analysis.acf_hr), static_cast<double>(analysis.acf_score),
             static_cast<double>(analysis.acf_spread),
             static_cast<double>(analysis.channel_correlation),
             static_cast<double>(analysis.rr_cv));
    ESP_LOGI(kTag,
             "manual optical: dc_ir=%.0f dc_red=%.0f led=0x%02x harmonic=%d stack_free=%u",
             static_cast<double>(analysis.dc_ir), static_cast<double>(analysis.dc_red),
             analysis.led_current,
             analysis.harmonic_corrected,
             static_cast<unsigned>(uxTaskGetStackHighWaterMark(nullptr) *
                                   sizeof(StackType_t)));
    if (g_manual_cancel.exchange(false)) {
        g_manual_progress = 0;
        g_manual_state = WearableMeasurementState::Idle;
        return;
    }
    const bool trusted = captured && analysis.quality >= 2;
    const bool hardware_fault = !captured || analysis.read_errors > 2;
    if (!trusted && hardware_fault && g_manual_attempt.fetch_add(1) == 0) {
        // Recover and repeat only for an actual acquisition fault. A poor
        // optical window already ran for ten uninterrupted seconds; repeating
        // it silently would make one button press unnecessarily slow.
        g_manual_progress = 0;
        g_manual_state = WearableMeasurementState::WaitingForStillness;
        g_manual_needs_reset = true;
        g_manual_requested = true;
        return;
    }
    if (!trusted) {
        // A coherent best estimate is still useful on the local screen. It is
        // explicitly labelled LOW and is never queued to desktop Kiki.
        if (captured && analysis.read_errors <= 2 && analysis.peaks >= 7 &&
            analysis.hr >= 35.0F && analysis.hr <= 220.0F) {
            g_manual_hr = analysis.hr;
            g_manual_spo2 = analysis.spo2;
            g_manual_quality = 1;
        }
        g_manual_progress = 0;
        g_manual_state = WearableMeasurementState::Failed;
        return;
    }
    if (trusted) {
        g_last_hr = analysis.hr;
        g_last_hr_us = esp_timer_get_time();
        g_last_spo2 = analysis.spo2;
        g_quality = analysis.quality;
        g_manual_hr = analysis.hr;
        g_manual_spo2 = analysis.spo2;
        g_manual_quality = analysis.quality;
    }
    g_manual_progress = 100;
    // Only a locally trusted result becomes a reading or leaves the device.
    build_and_queue(&analysis);
    g_manual_state = WearableMeasurementState::Complete;
}

void fall_task(void *) {
    while (true) {
        const int64_t now = esp_timer_get_time();
        const int64_t candidate = g_fall_candidate_us.load();
        if (candidate && !g_fall_pending.load() &&
            now - candidate <= 2000000) {
            g_fall_pending = true;
            g_fall_deadline_us = now + kFallResponseUs;
            ui_set_lcd("Possible fall  7s", "Say OK/help; hold to cancel");
            ESP_LOGW(kTag, "fall check pending: qualified raw IMU impact");
            build_and_queue(nullptr, "pending", "raw_low_g_impact");
        }
        if (g_fall_pending.load() && now >= g_fall_deadline_us.load() &&
            g_fall_pending.exchange(false)) {
            g_fall_confirmed = true;
            g_fall_candidate_us = 0;
            ui_set_lcd("Fall alert queued", "Awaiting delivery");
            audio_pipeline_play_local_effect(LocalEffect::FallAlarm);
            vTaskDelay(pdMS_TO_TICKS(3100));
            build_and_queue(nullptr, "confirmed", "no_response_7s");
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void health_task(void *) {
    int contact_yes = 0, contact_no = 0;
    int64_t last_window_us = -kWindowPeriodUs;
    int64_t last_periodic_us = esp_timer_get_time();
    while (true) {
        uint32_t red[32]{}, ir[32]{};
        int samples_read = 0;
        if (read_fifo(red, ir, 32, &samples_read) == ESP_OK && samples_read > 0) {
            uint64_t ir_sum = 0;
            for (int i = 0; i < samples_read; ++i) ir_sum += ir[i];
            const uint32_t level = static_cast<uint32_t>(ir_sum / samples_read);
            if (level > 10000) { ++contact_yes; contact_no = 0; }
            else { ++contact_no; contact_yes = 0; }
            if (!g_worn.load() && contact_yes >= 2) g_worn = true;
            if (g_worn.load() && contact_no >= 4) g_worn = false;
        }
        const int64_t now = esp_timer_get_time();
        const bool still = now - g_last_motion_us.load() >= 8000000;
        if (g_manual_requested.load()) {
            if (g_manual_needs_reset.exchange(false)) {
                contact_yes = 0;
                contact_no = 0;
                g_worn = false;
                const esp_err_t reset_result = sensor_configure(true);
                if (reset_result != ESP_OK) {
                    ESP_LOGE(kTag, "MAX30102 recovery failed: %s",
                             esp_err_to_name(reset_result));
                    g_manual_requested = false;
                    g_manual_state = WearableMeasurementState::Failed;
                    continue;
                }
                ESP_LOGI(kTag, "MAX30102 recovered for manual measurement");
            }
            if (g_fall_pending.load()) {
                g_manual_state = WearableMeasurementState::WaitingForStillness;
            } else if (!g_worn.load()) {
                g_manual_state = WearableMeasurementState::WaitingForContact;
            } else {
                g_manual_requested = false;
                g_manual_state = WearableMeasurementState::Measuring;
                g_manual_progress = 0;
                Analysis analysis{};
                finish_manual_measurement(analysis, capture_window(&analysis, true));
                last_window_us = esp_timer_get_time();
                last_periodic_us = last_window_us;
            }
        } else if (g_worn.load() && still && now - last_window_us >= kWindowPeriodUs) {
            Analysis analysis{};
            ui_set_lcd("Health check", "Keep your wrist still");
            const bool captured = capture_window(&analysis);
            if (g_manual_state.load() == WearableMeasurementState::Measuring) {
                finish_manual_measurement(analysis, captured);
            } else if (captured && analysis.quality >= 2) {
                g_last_hr = analysis.hr;
                g_last_hr_us = esp_timer_get_time();
                g_last_spo2 = analysis.spo2;
                g_quality = analysis.quality;
                char line[48];
                std::snprintf(line, sizeof(line), "%.0f bpm  %lu steps", analysis.hr,
                              static_cast<unsigned long>(g_steps.load()));
                ui_set_lcd("Wearable health", line);
                build_and_queue(&analysis);
            } else {
                // Preserve cadence/wear-state delivery without manufacturing a
                // health reading from an untrustworthy optical window.
                build_and_queue(nullptr);
            }
            last_window_us = esp_timer_get_time();
            last_periodic_us = last_window_us;
        } else if (now - last_periodic_us >= kWindowPeriodUs) {
            // Moving/off-wrist periods still deliver steps, wear state and
            // freshness even when no trustworthy PPG window is possible.
            build_and_queue(nullptr);
            last_periodic_us = now;
        }
        flush_queue();
        persist_activity_if_due();
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}

}  // namespace

esp_err_t wearable_start() {
    const esp_err_t result = sensor_init();
    if (result != ESP_OK) return result;
    g_storage_lock = xSemaphoreCreateMutex();
    if (!g_storage_lock) return ESP_ERR_NO_MEM;
    load_queue();
    load_activity();
    if (xTaskCreatePinnedToCore(fall_task, "kiki_fall", 6144, nullptr, 2,
                              nullptr, 0) != pdPASS) return ESP_ERR_NO_MEM;
    g_available = true;
    // Signal analysis uses nested filtering/autocorrelation calls. Keep a
    // measured safety margin: the earlier 6144-byte stack overflowed exactly
    // as the first 8-second window entered analyse().
    if (xTaskCreatePinnedToCore(health_task, "kiki_health", 12288, nullptr, 3,
                                nullptr, 0) != pdPASS) {
        g_available = false;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void wearable_note_motion(float ax, float ay, float az,
                          float gx, float gy, float gz, const char *) {
    const float magnitude = std::sqrt(ax * ax + ay * ay + az * az);
    const float linear = std::fabs(magnitude - 1.0F);
    const float gyro = std::sqrt(gx * gx + gy * gy + gz * gz);
    g_linear_g = linear;
    g_gyro_dps = gyro;
    const int64_t now = esp_timer_get_time();
    // The detector still RUNS when alerts are switched off -- its ladder needs
    // continuous history, and re-arming it from cold on the first sample after
    // a toggle would mean the first real fall after switching back on is the
    // one it misses. Only the alert is suppressed.
    if (g_fall_detector.update(static_cast<uint32_t>(now / 1000), magnitude,
                               g_worn.load()) &&
        settings_fall_alerts_enabled()) {
        g_fall_candidate_us = now;
        g_fall_episode = static_cast<int64_t>(std::time(nullptr)) * 1000000 + now % 1000000;
        g_last_motion_us = now;
    }
    if (linear > 0.12F || gyro > 24.0F) {
        g_last_motion_us = now;
    }
    if (now - g_move_last_tick >= 1000000) {
        g_move_last_tick = now;
        if (g_worn.load() && (linear > 0.12F || gyro > 24.0F)) {
            const auto movement_at = std::time(nullptr);
            portENTER_CRITICAL(&g_move_mux);
            if (g_move_index < 0 || now - g_move_last_active > 30000000) {
                g_move_index = (g_move_index + 1) % 12;
                g_move_bouts[g_move_index] = {movement_at, 0};
            }
            ++g_move_bouts[g_move_index].seconds;
            ++g_move_seconds;
            g_activity_dirty = true;
            g_move_last_active = now;
            portEXIT_CRITICAL(&g_move_mux);
        }
    }

    // Wrist step detector: cadence + four-peak bout gate, only while worn and
    // outside dance/fall handling. The first four valid peaks are credited once
    // the bout proves real rather than discarded.
    if (!g_worn.load() || g_fall_pending.load() || g_fall_candidate_us.load()) return;
    const bool step_above = linear > 0.12F;
    if (step_above && !g_step_above) {
        const int64_t since = now - g_last_step_peak_us;
        if (since >= 250000 && since <= 1200000) {
            ++g_candidate_steps;
            if (!g_step_bout && g_candidate_steps >= 4) {
                g_step_bout = true;
                g_steps.fetch_add(static_cast<uint32_t>(g_candidate_steps));
                g_activity_dirty = true;
            } else if (g_step_bout) {
                g_steps.fetch_add(1);
                g_activity_dirty = true;
            }
        } else if (since > 2000000) {
            g_candidate_steps = 1;
            g_step_bout = false;
        }
        g_last_step_peak_us = now;
    }
    g_step_above = step_above;
    if (g_step_bout && now - g_last_step_peak_us > 2000000) {
        g_step_bout = false;
        g_candidate_steps = 0;
    }
    set_activity(g_step_bout ? "walking" : (linear > 0.12F ? "active" : "still"));
}

void wearable_note_motion_event(const char *event) {
    (void)event; // health classification no longer depends on animation events
}

void wearable_set_battery(int percent) { g_battery = percent; }

void wearable_handle_ack(const char *batch_id, bool accepted) {
    if (!accepted || !batch_id || !*batch_id) return;
    portENTER_CRITICAL(&g_queue_mux);
    for (int i = 0; i < 3; ++i) {
        if (std::strcmp(g_pending_ids[i], batch_id) != 0) continue;
        for (int j = i; j < 2; ++j) {
            std::memcpy(g_pending_ids[j], g_pending_ids[j + 1], sizeof(g_pending_ids[j]));
            std::memcpy(g_pending_json[j], g_pending_json[j + 1], sizeof(g_pending_json[j]));
        }
        g_pending_ids[2][0] = 0;
        g_pending_json[2][0] = 0;
        break;
    }
    portEXIT_CRITICAL(&g_queue_mux);
    save_queue();
    g_last_send_us = 0;
    flush_queue();
}

bool wearable_fall_check_pending() { return g_fall_pending.load(); }

void wearable_cancel_fall_check(const char *reason) {
    if (!g_fall_pending.exchange(false)) return;
    g_fall_candidate_us = 0;
    g_fall_deadline_us = 0;
    ui_set_lcd("Glad you are OK", "Fall alert cancelled");
    build_and_queue(nullptr, "cancelled", reason ? reason : "response");
}

void wearable_confirm_fall_check(const char *reason) {
    if (!g_fall_pending.exchange(false)) return;
    g_fall_candidate_us = 0;
    g_fall_deadline_us = 0;
    g_fall_confirmed = true;
    ui_set_lcd("Fall alert queued", "You asked for help");
    build_and_queue(nullptr, "confirmed", reason ? reason : "asked_for_help");
}

void wearable_get_snapshot(WearableSnapshot *out) {
    if (!out) return;
    out->sensor_available = g_available.load();
    out->worn = g_worn.load();
    out->steps = g_steps.load();
    out->heart_rate = g_last_hr.load();
    const int64_t measured = g_last_hr_us.load();
    out->heart_rate_age_seconds = measured > 0
        ? static_cast<uint32_t>((esp_timer_get_time() - measured) / 1000000)
        : UINT32_MAX;
    out->spo2_experimental = g_last_spo2.load();
    out->quality = quality_name(g_quality.load());
    static char activity[16];
    get_activity(activity, sizeof(activity));
    out->activity = activity;
    out->fall_check_pending = g_fall_pending.load();
}

void wearable_activity_detail(char *out, unsigned size) {
    if (!out || !size) return;
    MoveBout bouts[12];
    uint32_t seconds;
    int newest;
    portENTER_CRITICAL(&g_move_mux);
    std::memcpy(bouts, g_move_bouts, sizeof(bouts));
    seconds = g_move_seconds;
    newest = g_move_index;
    portEXIT_CRITICAL(&g_move_mux);
    int used = std::snprintf(out, size, "%lu steps\n%lu min %lu sec moving\nToday / saved across restarts\n\nRecent movement (IST):\n",
        static_cast<unsigned long>(g_steps.load()), static_cast<unsigned long>(seconds / 60),
        static_cast<unsigned long>(seconds % 60));
    if (newest < 0 && used > 0 && static_cast<unsigned>(used) < size)
        std::snprintf(out + used, size - used, "No movement recorded yet.");
    for (int i = 0; i < 12 && newest >= 0 && used > 0 && static_cast<unsigned>(used) < size; ++i) {
        const auto &bout = bouts[(newest - i + 12) % 12];
        if (!bout.seconds) continue;
        char stamp[40] = "Time not synced";
        if (bout.at > 1700000000) {
            auto local_at = bout.at + 19800;
            std::tm local{};
            gmtime_r(&local_at, &local);
            std::strftime(stamp, sizeof(stamp), "%d %b %I:%M %p", &local);
        }
        used += std::snprintf(out + used, size - used, "%s - %lus moving\n", stamp,
                              static_cast<unsigned long>(bout.seconds));
    }
}

bool wearable_request_measurement() {
    if (!g_available.load()) {
        g_manual_progress = 0;
        g_manual_state = WearableMeasurementState::Failed;
        return false;
    }
    const WearableMeasurementState state = g_manual_state.load();
    if (state == WearableMeasurementState::WaitingForContact ||
        state == WearableMeasurementState::WaitingForStillness ||
        state == WearableMeasurementState::Measuring) {
        return false;
    }
    g_manual_hr = 0.0F;
    g_manual_spo2 = 0.0F;
    g_manual_quality = 0;
    g_manual_attempt = 0;
    g_manual_cancel = false;
    g_manual_needs_reset = true;
    g_manual_progress = 0;
    g_manual_state = g_worn.load() ? WearableMeasurementState::WaitingForStillness
                                   : WearableMeasurementState::WaitingForContact;
    g_manual_requested = true;
    return true;
}

void wearable_cancel_measurement() {
    g_manual_requested = false;
    g_manual_needs_reset = false;
    g_manual_cancel = true;
    const WearableMeasurementState state = g_manual_state.load();
    if (state != WearableMeasurementState::Measuring) {
        g_manual_progress = 0;
        g_manual_state = WearableMeasurementState::Idle;
        g_manual_cancel = false;
    }
}

void wearable_get_measurement_status(WearableMeasurementStatus *out) {
    if (!out) return;
    out->state = g_manual_state.load();
    out->sensor_available = g_available.load();
    out->worn = g_worn.load();
    out->progress_percent = static_cast<uint8_t>(
        std::clamp(g_manual_progress.load(), 0, 100));
    out->steps = g_steps.load();
    out->heart_rate = g_manual_hr.load();
    out->spo2_experimental = g_manual_spo2.load();
    out->quality = quality_name(g_manual_quality.load());
}

}  // namespace kiki
