#include "audio_pipeline.hpp"
#include "gateway_client.hpp"
#include "kiki_ota.hpp"
#include "kiki_power.hpp"
#include "kiki_time.hpp"
#include "kiki_exercise.hpp"
#include "kiki_wearable.hpp"
#include "kiki_dance.hpp"
#include "kiki_log.hpp"
#include "kiki_motion.hpp"
#include "kiki_setup.hpp"
#include "kiki_settings.hpp"
#include "kiki_ui.hpp"
#include "protocol.hpp"
#include "wifi_station.hpp"

#include <algorithm>
#include <atomic>

#include <cstdio>
#include <cstring>
#include "bsp/esp-bsp.h"
#include "esp_app_desc.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"

namespace {
constexpr char kTag[] = "kiki_main";

const char *reset_reason_name(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_POWERON: return "power-on";
        case ESP_RST_EXT: return "external-pin";
        case ESP_RST_SW: return "software";
        case ESP_RST_PANIC: return "panic";
        case ESP_RST_INT_WDT: return "interrupt-watchdog";
        case ESP_RST_TASK_WDT: return "task-watchdog";
        case ESP_RST_WDT: return "watchdog";
        case ESP_RST_DEEPSLEEP: return "deep-sleep";
        case ESP_RST_BROWNOUT: return "brownout";
        case ESP_RST_SDIO: return "sdio";
        case ESP_RST_UNKNOWN:
        default: return "unknown";
    }
}

constexpr size_t kMicSamples = 512;
constexpr size_t kMicQueueDepth = 64;
struct MicPacket {
    int16_t samples[kMicSamples];
    uint16_t count;
    uint16_t sample_rate;
    uint8_t flags;
    uint32_t sequence;
    int64_t timestamp_us;
};
QueueHandle_t g_mic_queue = nullptr;
TaskHandle_t g_tx_task = nullptr;

void microphone_frame(const int16_t *samples, size_t count, uint32_t sample_rate,
                      uint8_t flags, uint32_t sequence, int64_t timestamp_us) {
    if (!g_mic_queue || !samples || count == 0 || count > kMicSamples ||
        sample_rate > UINT16_MAX) return;
    MicPacket packet{};
    memcpy(packet.samples, samples, count * sizeof(int16_t));
    packet.count = static_cast<uint16_t>(count);
    packet.sample_rate = static_cast<uint16_t>(sample_rate);
    packet.flags = flags;
    packet.sequence = sequence;
    packet.timestamp_us = timestamp_us;
    if (xQueueSend(g_mic_queue, &packet, 0) != pdTRUE) {
        static uint32_t drops = 0;
        if ((++drops % 100) == 1) ESP_LOGW(kTag, "microphone network queue full");
        return;
    }
    // The tx task sleeps on this notification rather than polling, so a frame
    // and a button press both wake it immediately.
    if (g_tx_task) xTaskNotifyGive(g_tx_task);
}

// Above this many frames waiting, the uplink is not keeping up and the backlog
// is thrown away rather than sent. AEC output is normally 32 ms per frame, so
// twelve frames cap stale audio near 384 ms instead of consuming half the
// queue.
constexpr UBaseType_t kMicBacklogDrop = 12;

// --- Uplink gating on a remote link ---------------------------------------
//
// Kiki uploads 16 kHz mono microphone audio continuously -- ~256 kbps, all the
// time, whether or not anyone is speaking, because the wake word is found in
// the transcript rather than in the audio. On the LAN that is free. On a phone
// hotspot or a contended college AP it is the whole problem: the moment the
// radio has a bad second the socket's send cannot complete, and
// esp_websocket_client treats a short write as fatal and aborts the entire
// connection (§8). Observed on 2026-08-18: sessions lasting 3.7 s to 280 s,
// dropping with `rcvd=None sent=None` or a keepalive timeout, with the mic
// stream reading a healthy 1.00x right up to the moment it died -- not
// congestion building, a single stall killing a session.
//
// So on a remote link that has *shown* it cannot hold the stream, the board
// sends audio around *speech* instead of continuously, using the device VAD it
// already computes for barge-in. A quiet room then costs almost nothing.
//
// "Has shown" is the important part, and it is why this is not simply on for
// every remote link: the same board on stable wifi far from home, through the
// same tunnel, runs for hours without a drop. The trigger is evidence, not
// geography -- one send that takes over a second, or one session already lost
// this boot. A good link never engages it; a hotspot engages it within a
// minute and keeps working.
//
// Three things keep this from making Kiki deaf, which would be a much worse
// bug than a reconnect:
//   * it only applies when the frame is AEC-processed, i.e. the DSP path that
//     produces the VAD vote is actually running. A raw fallback frame carries
//     no vote and is always sent.
//   * a preroll of the frames just before speech starts is flushed on the way
//     open, so the first syllable of "kiki" is never clipped.
//   * a short burst goes out every ten seconds regardless, so a VAD that is
//     systematically missing quiet speech shows up as thin ambient capture
//     rather than as silence nobody can explain.
constexpr int64_t kUplinkHangoverUs = 2000000;      // keep sending after speech
constexpr int64_t kUplinkBurstPeriodUs = 10000000;  // ...and a little anyway
constexpr int64_t kUplinkBurstLengthUs = 500000;
constexpr size_t kPrerollFrames = 12;               // ~384 ms at 32 ms a frame

// A frame carries 32 ms of audio. A send that takes eight times that long is
// not a busy moment: while it is in flight the client mutex is held, so nothing
// is being read from the socket and no keepalive can go out. The old threshold
// was 1.2 s, which is well past the point where the session is already lost.
constexpr int64_t kSlowSendUs = 250000;
constexpr int64_t kFastSendUs = 50000;
// How long to stop feeding a stalled uplink. Short to begin with, because a
// closed gate is speech nobody hears; doubling, because a link that stalls
// repeatedly is not going to be talked round.
constexpr int64_t kUplinkBackoffMinUs = 500000;
constexpr int64_t kUplinkBackoffMaxUs = 4000000;

MicPacket *g_preroll = nullptr;                     // PSRAM ring
size_t g_preroll_head = 0;
size_t g_preroll_count = 0;
int64_t g_speech_until_us = 0;
int64_t g_burst_started_us = 0;
bool g_uplink_open = true;
int64_t g_uplink_closed_until_us = 0;
int64_t g_uplink_backoff_us = kUplinkBackoffMinUs;

void remember_preroll(const MicPacket &packet) {
    if (!g_preroll) {
        // PSRAM, not internal: internal RAM is the scarce pool on this board
        // and 12 KiB of it is a display flush.
        g_preroll = static_cast<MicPacket *>(
            heap_caps_malloc(sizeof(MicPacket) * kPrerollFrames, MALLOC_CAP_SPIRAM));
        if (!g_preroll) return;
    }
    g_preroll[g_preroll_head] = packet;
    g_preroll_head = (g_preroll_head + 1) % kPrerollFrames;
    if (g_preroll_count < kPrerollFrames) ++g_preroll_count;
}

void flush_preroll() {
    if (!g_preroll || g_preroll_count == 0) return;
    const size_t first = (g_preroll_head + kPrerollFrames - g_preroll_count) % kPrerollFrames;
    for (size_t i = 0; i < g_preroll_count; ++i) {
        const MicPacket &packet = g_preroll[(first + i) % kPrerollFrames];
        kiki::gateway_client_send_mic(packet.samples, packet.count, packet.sample_rate,
                                      packet.flags, packet.sequence, packet.timestamp_us);
    }
    g_preroll_count = 0;
}

// Thrifty is now the DEFAULT on a remote link rather than something the board
// switches on after it has already lost a session. The old rule -- wait for
// evidence, then latch for the rest of the boot -- got both halves wrong: the
// evidence only ever arrives *after* the first session dies, and a latch means
// a link that recovers never gets its ambient capture back. This is a decision
// per link, not per boot.
//
// It is still not the real fix, and it is not meant to be: gating exists only
// because a 32 ms frame currently costs 1 KiB on the wire. Once that frame is
// ~96 bytes of Opus, continuous capture fits on any of these links and this
// gate can go back to being what it was written as -- an emergency measure.
std::atomic<bool> g_uplink_thrifty{false};

void note_uplink_trouble(const char *why) {
    const int64_t now = esp_timer_get_time();
    if (!g_uplink_thrifty.exchange(true)) {
        ESP_LOGW(kTag, "uplink is marginal (%s); sending microphone audio "
                       "around speech from now on", why);
    }
    g_uplink_closed_until_us = now + g_uplink_backoff_us;
    g_uplink_backoff_us = std::min(g_uplink_backoff_us * 2, kUplinkBackoffMaxUs);
}

// A send that completed promptly is the only evidence the link recovered, so
// recovery is measured, not assumed -- and unlike the old latch it is
// reversible, which is what lets a board that walked back into good Wi-Fi start
// listening properly again without a reboot.
void note_uplink_healthy() {
    if (g_uplink_backoff_us > kUplinkBackoffMinUs) {
        g_uplink_backoff_us = std::max(kUplinkBackoffMinUs, g_uplink_backoff_us / 2);
    }
}

bool uplink_wants(const MicPacket &packet) {
    if (!kiki::gateway_client_is_remote()) return true;
    if (!g_uplink_thrifty.load()) {
        // A remote link starts thrifty. One lost session used to be the
        // trigger; now it is simply being remote, because every remote link
        // this board has met has been the one that could not hold the stream.
        note_uplink_trouble("a remote link");
    }
    if (kiki::ui_talk_held()) return true;      // a silent hold is still a turn
    const int64_t now = esp_timer_get_time();
    // The link is stalled. Feeding it is what turns a stall into a dropped
    // session, and the frames sent during one are the frames that push it over.
    if (now < g_uplink_closed_until_us) return false;
    if (!(packet.flags & kiki::AudioFlags::AecProcessed)) return true;
    if (packet.flags & kiki::AudioFlags::DeviceVadSpeech) {
        g_speech_until_us = now + kUplinkHangoverUs;
    }
    if (now < g_speech_until_us) return true;
    if (now - g_burst_started_us >= kUplinkBurstPeriodUs) g_burst_started_us = now;
    return now - g_burst_started_us < kUplinkBurstLengthUs;
}

void microphone_network_task(void *) {
    MicPacket packet{};
    while (true) {
        // Control before audio, every time round. This is the ordering that
        // makes a button press feel instant on a congested link: the press does
        // not queue behind a second of microphone PCM, it overtakes it.
        while (kiki::gateway_client_flush_events()) {
        }
        if (xQueueReceive(g_mic_queue, &packet, 0) != pdTRUE) {
            // Nothing to do. Sleep until a frame or an event is queued; the
            // timeout is only insurance against a missed notification.
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100));
            continue;
        }
        if (!kiki::gateway_client_connected()) continue;
        // Raw microphone audio remains fail-closed during playback. Only the
        // ESP-SR output explicitly marked AEC-cleaned may reach the gateway;
        // an AEC allocation/configuration failure therefore cannot make Kiki
        // wake herself up.
        if (kiki::audio_pipeline_is_playing() &&
            (!kiki::settings_barge_in_enabled() ||
             !(packet.flags & kiki::AudioFlags::AecProcessed))) continue;
        // Nothing competes with a firmware download. 768 kbps of ambient audio
        // sharing the uplink with a 1.8 MB fetch is why the LAN update stalled
        // at 0% for sixteen seconds and took three times longer than the same
        // image did over the tunnel -- where the talk-button gate had already
        // silenced the microphone by accident. An update is seconds of work and
        // ends in a reboot; there is nothing to hear in the meantime.
        if (kiki::ota_in_progress()) continue;
        // Shed congestion HERE, never by letting the send time out. A short
        // write makes esp_websocket_client abort the connection outright, so a
        // slow uplink used to become a dropped session every few minutes; a
        // discarded microphone frame is just a discarded frame. Sequence
        // numbers still advance, so the gateway sees a real audio_gap rather
        // than silently mistaking the backlog for continuous speech.
        if (uxQueueMessagesWaiting(g_mic_queue) > kMicBacklogDrop) {
            static uint32_t shed = 0;
            if ((++shed % 100) == 1) {
                ESP_LOGW(kTag, "uplink behind; shedding microphone backlog");
            }
            continue;
        }
        if (!uplink_wants(packet)) {
            remember_preroll(packet);
            g_uplink_open = false;
            continue;
        }
        if (!g_uplink_open) {
            // Opening: the syllable that woke the VAD started before it fired.
            flush_preroll();
            g_uplink_open = true;
        }
        // Timed, because how long a send takes is the only early warning there
        // is. While this call is inside the client mutex the websocket task
        // cannot read the socket and cannot answer a PING, so a send that took
        // a quarter of a second has already cost Kiki audio and may cost the
        // session.
        const int64_t send_started_us = esp_timer_get_time();
        kiki::gateway_client_send_mic(packet.samples, packet.count, packet.sample_rate,
                                      packet.flags, packet.sequence, packet.timestamp_us);
        const int64_t took_us = esp_timer_get_time() - send_started_us;
        if (took_us > kSlowSendUs) note_uplink_trouble("a slow send");
        else if (took_us < kFastSendUs) note_uplink_healthy();
    }
}


void playback_drained() { kiki::gateway_client_send_event("playback_drained"); }

// Playback health is invisible from the laptop otherwise: the gateway can see
// that it sent every byte on time and still have no idea the speaker stuttered.
// Reporting the device-side counters is what makes "the audio broke" a number
// instead of an argument.
void telemetry_task(void *) {
    kiki::PlaybackStats previous{};
    kiki::PowerState was{};
    int ticks = 0;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        ++ticks;

        // Read and display power first, and outside the connected check: the
        // battery gauge is the one reading that matters most when the gateway
        // is unreachable, because on battery there is no USB serial either.
        kiki::PowerState power{};
        const bool have_power = kiki::power_read(&power);
        if (have_power) {
            kiki::ui_set_battery(power.percent, power.usb, power.charging);
            kiki::motion_set_power_state(power.percent, power.usb, power.charging);
            kiki::wearable_set_battery(power.percent);
            // Every source change, and otherwise once a minute -- at 5 s it
            // buried everything else in the log.
            if (power.usb != was.usb || power.present != was.present ||
                power.charging != was.charging ||
                power.charge_limited != was.charge_limited || (ticks % 12) == 0) {
                kiki::power_log("live");
            }
            was = power;
        }

        if (!kiki::gateway_client_connected()) continue;
        kiki::PlaybackStats stats{};
        kiki::audio_pipeline_get_stats(&stats);
        // static, not on the stack. 768 was not enough once the Opus counters
        // were added, and growing it to 1280 put a third of this task's 4 KB
        // stack into one buffer -- on top of build_event's own health[200],
        // ssid[33] and std::string work, which are live at the same time. The
        // task overflowed and the board rebooted every few minutes with no
        // interaction at all.
        //
        // Only kiki_telemetry ever runs this loop, so a single shared buffer is
        // safe and costs the stack nothing. The other failure mode is still
        // guarded below: snprintf truncating mid-string makes the board send
        // invalid JSON, and the telemetry added to diagnose a problem becomes
        // the thing that stops arriving.
        static char extra[1280];
        int n = snprintf(extra, sizeof(extra),
                         "\"buffered_ms\":%u,\"underruns\":%u,\"underrun_ms\":%u,"
                         "\"dropped_bytes\":%u,\"forced_ends\":%u,\"heap_free\":%u,"
                         "\"internal_free\":%u,\"dma_largest\":%u,\"prebuffer_ms\":%u,"
                         "\"aec_enabled\":%s,\"aec_frames\":%u,\"aec_vad_frames\":%u,"
                         "\"aec_feed_failures\":%u,\"aec_max_process_us\":%u,"
                         "\"aec_rearms\":%u,\"send_worst_ms\":%u,"
                         "\"tx_dropped\":%u,\"rx_dropped\":%u,"
                         "\"uplink_thrifty\":%s,\"mic_queued\":%u,\"rx_stack_free\":%u,"
                         "\"opus_us_max\":%u,\"opus_frames\":%u,\"opus_pcm_per_frame\":%u,"
                         "\"telemetry_stack_free\":%u",
                         static_cast<unsigned>(kiki::audio_pipeline_buffered_ms()),
                         static_cast<unsigned>(stats.underruns),
                         static_cast<unsigned>(stats.underrun_ms),
                         static_cast<unsigned>(stats.dropped_bytes),
                         static_cast<unsigned>(stats.forced_ends),
                         static_cast<unsigned>(esp_get_free_heap_size()),
                         static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
                         static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)),
                         static_cast<unsigned>(kiki::audio_pipeline_prebuffer_ms()),
                         stats.aec_enabled ? "true" : "false",
                         static_cast<unsigned>(stats.aec_frames),
                         static_cast<unsigned>(stats.aec_vad_frames),
                         static_cast<unsigned>(stats.aec_feed_failures),
                         static_cast<unsigned>(stats.aec_max_process_us),
                         static_cast<unsigned>(stats.aec_rearms),
                         static_cast<unsigned>(kiki::gateway_client_worst_send_ms()),
                         static_cast<unsigned>(kiki::gateway_client_tx_dropped()),
                         static_cast<unsigned>(kiki::gateway_client_rx_dropped()),
                         g_uplink_thrifty.load() ? "true" : "false",
                         static_cast<unsigned>(g_mic_queue
                                                   ? uxQueueMessagesWaiting(g_mic_queue)
                                                   : 0),
                         static_cast<unsigned>(kiki::gateway_client_rx_stack_free()),
                         static_cast<unsigned>(kiki::gateway_client_opus_decode_us_max()),
                         static_cast<unsigned>(kiki::gateway_client_opus_frames()),
                         static_cast<unsigned>(kiki::gateway_client_opus_pcm_per_frame()),
                         // This task's own remaining stack. It overflowed once,
                         // silently, and the only symptom was a board that
                         // rebooted every few minutes.
                         static_cast<unsigned>(uxTaskGetStackHighWaterMark(nullptr) *
                                               sizeof(StackType_t)));
        if (n >= static_cast<int>(sizeof(extra))) {
            ESP_LOGE(kTag, "device_stats truncated: needed %d bytes, have %u",
                     n, static_cast<unsigned>(sizeof(extra)));
        }
        if (have_power && n > 0 && n < static_cast<int>(sizeof(extra))) {
            snprintf(extra + n, sizeof(extra) - n,
                     ",\"battery_percent\":%d,\"battery_mv\":%u,\"on_usb\":%s,"
                     "\"charging\":%s,\"charge_limited\":%s,\"charger\":\"%s\"",
                     power.percent, static_cast<unsigned>(power.vbat_mv),
                     power.usb ? "true" : "false",
                     power.charging ? "true" : "false",
                     power.charge_limited ? "true" : "false", power.charger);
        }
        kiki::MotionRuntimeStats motion{};
        kiki::motion_get_stats(&motion);
        if (motion.available) {
            const size_t used = strnlen(extra, sizeof(extra));
            if (used < sizeof(extra)) {
                snprintf(extra + used, sizeof(extra) - used,
                         ",\"rssi\":%d,\"wifi_drops\":%u,"
                         "\"imu_posture\":\"%s\",\"motion_event\":\"%s\","
                         "\"imu_accel_g\":%.3f,\"imu_linear_g\":%.3f,"
                         "\"imu_gyro_dps\":%.1f,\"motion_annoyance\":%.2f,"
                         "\"motion_events\":%u,\"imu_read_errors\":%u",
                         static_cast<int>(kiki::wifi_station_health().rssi),
                         static_cast<unsigned>(kiki::wifi_station_health().disconnects),
                         kiki::motion_posture_name(motion.classifier.posture),
                         kiki::motion_situation_name(motion.classifier.last_situation),
                         static_cast<double>(motion.classifier.accel_g),
                         static_cast<double>(motion.classifier.linear_g),
                         static_cast<double>(motion.classifier.gyro_dps),
                         static_cast<double>(motion.classifier.annoyance),
                         static_cast<unsigned>(motion.classifier.event_count),
                         static_cast<unsigned>(motion.read_errors));
            }
        }
        kiki::WearableSnapshot wearable{};
        kiki::wearable_get_snapshot(&wearable);
        if (wearable.sensor_available) {
            const size_t used = strnlen(extra, sizeof(extra));
            if (used < sizeof(extra)) {
                snprintf(extra + used, sizeof(extra) - used,
                         ",\"wearable_worn\":%s,\"wearable_steps\":%u,"
                         "\"wearable_hr\":%.1f,\"wearable_quality\":\"%s\","
                         "\"wearable_activity\":\"%s\",\"fall_check_pending\":%s",
                         wearable.worn ? "true" : "false",
                         static_cast<unsigned>(wearable.steps),
                         static_cast<double>(wearable.heart_rate), wearable.quality,
                         wearable.activity,
                         wearable.fall_check_pending ? "true" : "false");
            }
        }
        if (kiki::dance_is_active()) {
            // Only while it matters. The dance's frame rate is the one number
            // that says whether the stage is keeping up with the music, and it
            // has no meaning at all when nobody is dancing.
            const size_t used = strnlen(extra, sizeof(extra));
            if (used < sizeof(extra)) {
                snprintf(extra + used, sizeof(extra) - used,
                         ",\"dancing\":true,\"dance_fps\":%u,\"dance_draw_us\":%u",
                         static_cast<unsigned>(kiki::dance_fps()),
                         static_cast<unsigned>(kiki::dance_draw_us()));
            }
        }
        const bool changed = stats.underruns != previous.underruns ||
                             stats.dropped_bytes != previous.dropped_bytes ||
                             stats.forced_ends != previous.forced_ends;
        if (changed) {
            ESP_LOGW(kTag, "playback health: underruns=%u (%u ms) dropped=%u forced_ends=%u",
                     static_cast<unsigned>(stats.underruns),
                     static_cast<unsigned>(stats.underrun_ms),
                     static_cast<unsigned>(stats.dropped_bytes),
                     static_cast<unsigned>(stats.forced_ends));
        }
        previous = stats;
        kiki::gateway_client_send_event("device_stats", extra);
    }
}
}  // namespace

extern "C" void app_main(void) {
    const esp_reset_reason_t boot_reset_reason = esp_reset_reason();
    esp_err_t nvs = nvs_flash_init();
    if (nvs == ESP_ERR_NVS_NO_FREE_PAGES || nvs == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs);
    kiki::settings_init();
    kiki::ui_start();
    // Shown before anything else can fail. Without a serial console there is
    // otherwise no way to answer "did the flash actually take?" -- the build
    // stamp changes every build, so a stale screen is obvious at a glance.
    {
        // The build ID is the first four bytes of the ELF SHA-256, NOT the
        // compile date. esp_app_desc.c is not always recompiled, so its date
        // and time can be hours stale while the code around them is current --
        // which made the panel claim an old build after a correct flash, and
        // sent us chasing a download that was never wrong. The ELF hash is
        // recomputed at every link, so it cannot lie about what is running.
        const esp_app_desc_t *app = esp_app_get_description();
        char stamp[40];
        std::snprintf(stamp, sizeof(stamp), "build %02x%02x%02x%02x",
                      app->app_elf_sha256[0], app->app_elf_sha256[1],
                      app->app_elf_sha256[2], app->app_elf_sha256[3]);
        ESP_LOGW(kTag, "Kiki %s (%s, esp_app_desc date %s %s)", stamp, app->version,
                 app->date, app->time);
        kiki::ui_set_lcd("Kiki", stamp);
    }
    kiki::ui_set_state("connecting");
    ESP_LOGI(kTag, "display and touch ready");
    // 64 slots of just over a kilobyte each is 66 KiB. That came out of the
    // ~166 KiB of internal heap this app actually has, in direct competition
    // display's DMA buffers; PSRAM has megabytes spare and the queue is never
    // touched from an ISR.
    g_mic_queue = xQueueCreateWithCaps(kMicQueueDepth, sizeof(MicPacket), MALLOC_CAP_SPIRAM);
    ESP_ERROR_CHECK(g_mic_queue ? ESP_OK : ESP_ERR_NO_MEM);
    xTaskCreatePinnedToCore(microphone_network_task, "kiki_net_tx", 6144, nullptr, 17,
                            &g_tx_task, 0);
    kiki::gateway_client_set_tx_task(g_tx_task);
    // Internal RAM is the scarce resource on this board (~166 KiB of heap), and the
    // display's SPI driver competes for the DMA-capable part of it on every
    // flush. Print both pools so a "Failed to allocate priv TX buffer" flood is
    // one line away from being explained.
    ESP_LOGI(kTag, "heap: internal %u free (%u largest DMA), psram %u free",
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
    ESP_LOGI(kTag, "starting audio codecs");
    kiki::power_init();
    ESP_ERROR_CHECK(kiki::audio_pipeline_start(microphone_frame, playback_drained));
    ESP_LOGI(kTag, "audio codecs ready");
    // Physical personality is deliberately non-fatal and starts before Wi-Fi:
    // the face can still react offline, while the state gate suppresses motion
    // comedy during provisioning and boot screens.
    const esp_err_t motion_result = kiki::motion_start();
    if (motion_result != ESP_OK) {
        ESP_LOGW(kTag, "motion reactions unavailable: %s", esp_err_to_name(motion_result));
    }
    // Blocks on the panel when no network is provisioned or the saved one is
    // gone. Audio is already running, so the codecs are warm by the time the
    // user finishes typing.
    // Armed BEFORE the join, not after it.
    //
    // The watchdog reverts to the previous image unless this one reaches the
    // gateway within three minutes, and the case that matters most is a build
    // that cannot get onto Wi-Fi at all. `setup_ensure_wifi()` handles that by
    // showing the provisioning panel and waiting forever, so arming afterwards
    // meant the one failure the watchdog exists for was the one failure it
    // could never see: the board would sit on the setup screen, on the bad
    // image, until somebody found a USB cable. Arming here costs nothing --
    // `is_pending()` is false unless this image arrived by OTA -- and the
    // display is already up, because the panel below draws on it.
    kiki::ota_arm_rollback_watchdog();
    ESP_ERROR_CHECK(kiki::setup_ensure_wifi());
    kiki::ui_show();
    ESP_LOGI(kTag, "Wi-Fi connected");
    // Before the gateway, because the gateway may need TLS and TLS needs a
    // real date. Blocking is the point: a wss:// attempt made while the clock
    // still says 1970 fails certificate validation and looks like a network
    // fault instead of a clock fault.
    if (!kiki::time_sync(8000)) kiki::time_sync_keep_trying();
    ESP_ERROR_CHECK(kiki::gateway_client_start());
    const esp_err_t wearable_result = kiki::wearable_start();
    if (wearable_result != ESP_OK) {
        ESP_LOGW(kTag, "wearable health unavailable: %s",
                 esp_err_to_name(wearable_result));
    }
    // Health mode's motion capture. Non-fatal, like motion and wearable: it
    // records nothing until a gateway in health mode asks for a window, so a
    // failure here costs only the guided-exercise feature.
    const esp_err_t exercise_result = kiki::exercise_start();
    if (exercise_result != ESP_OK) {
        ESP_LOGW(kTag, "guided-exercise capture unavailable: %s",
                 esp_err_to_name(exercise_result));
    }
    kiki::log_remote_start();
    // Startup UART often disappears with the reset itself. Repeat this after
    // remote logging is armed so the next unexpected reboot is diagnosable
    // from the laptop without attaching a serial monitor first.
    ESP_LOGW(kTag, "boot reset reason: %s (%d)",
             reset_reason_name(boot_reset_reason),
             static_cast<int>(boot_reset_reason));
    // 4096 was survivable while the stats payload was small. It is not a
    // number to leave at the edge: this task calls build_event, which does
    // std::string concatenation, and every field added to device_stats pushes
    // it further. Reported as telemetry_stack_free in the payload it builds.
    xTaskCreatePinnedToCore(telemetry_task, "kiki_telemetry", 6144, nullptr, 2, nullptr, 0);
    ESP_LOGI(kTag, "Kiki ESP32 started; gateway=%s", CONFIG_KIKI_GATEWAY_URI);
}
