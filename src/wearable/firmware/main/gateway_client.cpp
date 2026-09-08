#include "gateway_client.hpp"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "audio_pipeline.hpp"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_check.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/ringbuf.h"
#include "freertos/task.h"
#include "nvs.h"
#include "kiki_ota.hpp"
#include "kiki_settings.hpp"
#include "kiki_time.hpp"
#include "kiki_dance.hpp"
#include "wifi_station.hpp"
#include "kiki_ui.hpp"
#include "kiki_wizard.hpp"
#include "kiki_care_ui.hpp"
#include "kiki_exercise.hpp"
#include "kiki_instructor.hpp"
#include "kiki_wearable.hpp"
#include "gateway_tx_priority.hpp"
#include "protocol.hpp"
#include "esp_audio_dec.h"
#include "esp_opus_dec.h"

#ifdef CONFIG_ESP_WS_CLIENT_SEPARATE_TX_LOCK
#error "Kiki requires one WebSocket lock: separate TX teardown races mbedTLS writes"
#endif

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_gateway";

// Two addresses for one gateway. The LAN one is always tried first and always
// preferred: audio runs continuously at 100 frames a second, and sending that
// out through a public ingress and back adds latency to the whole voice loop
// for nothing when the laptop is one hop away. The public URI exists so Kiki
// still works away from home, not as an equal alternative.
constexpr const char *kPrimaryUri = CONFIG_KIKI_GATEWAY_URI;
constexpr const char *kFallbackUri = CONFIG_KIKI_GATEWAY_URI_FALLBACK;
// Failed attempts on one address before trying the other. The client retries
// every reconnect_timeout_ms (1 s), so this rides out a Wi-Fi hiccup without
// making "walked out of the house" mean a long silence.
constexpr int kAttemptsBeforeSwitch = 4;

// How long any single send may take before esp_websocket_client gives up on it.
//
// This is not a nicety, it is the connection's life. esp_websocket_client treats
// a short write as fatal: `if (wlen < 0 || (wlen == 0 && need_write != 0))` it
// aborts the whole connection -- and the timeout it hands to the transport is
// the one passed in *here*, not network_timeout_ms. Events used to pass 100 ms,
// so any moment the socket was not writable for a tenth of a second tore the
// session down. On the LAN, where the microphone is streaming 768 kbps into the
// same socket and the gateway's event loop stalls for over a second at a time
// doing model work, that happened every two to five minutes.
//
// Five seconds is long enough to ride out those stalls and still far below the
// 120 s ping/pong timeout that catches a genuinely dead link. Blocking a sender
// task meanwhile is the correct behaviour: it is backpressure, and the
// microphone queue sheds on depth rather than piling up behind it.
constexpr TickType_t kSendTimeout = pdMS_TO_TICKS(5000);

const char *gateway_uri();
bool uri_is_tls(const char *uri);
std::string with_explicit_port(const char *uri);
bool have_fallback();
bool store_fallback_uri(const char *uri);

// The learned public URL, held here because the websocket client keeps a
// pointer to whatever URI string it is given.
constexpr char kNamespace[] = "kiki_gw";
constexpr char kKeyFallback[] = "fallback";
std::string g_learned_fallback;
std::string g_current_uri;

// --- Nothing may block on this socket except the one task that owns it -----
//
// esp_websocket_client is built with ONE mutex (CONFIG_ESP_WS_CLIENT_SEPARATE_
// TX_LOCK is off above, deliberately -- a separate TX lock races mbedTLS
// teardown and reboots the board). Every send takes `client->lock`, and so does
// the client's own task to read the socket and to answer a PING. That single
// fact produced four separate symptoms on a slow link, and they are all the
// same bug seen from different sides:
//
//   * a microphone send stuck behind a congested uplink held the lock, so the
//     client task could not read -- Kiki's own speech stopped being drained
//     from the socket and the speaker stuttered;
//   * ...and could not send the keepalive PING, so the gateway reaped a board
//     that was alive and busy ("keepalive ping timeout", every few minutes);
//   * the LVGL touch callback called straight into send_event() and waited on
//     the same lock, which is why "tap or hold" hung -- and after the timeout
//     the event was silently dropped, so the press did nothing at all;
//   * inbound frames were parsed INSIDE the websocket callback, still holding
//     the lock, where handle_json takes the display lock and handle_binary can
//     block on the playback ring. A busy render or a full ring therefore froze
//     the socket for 100-250 ms per frame.
//
// So the socket now has exactly one owner. Everything else queues:
//
//   TX  producers (UI, motion, telemetry, log, settings, OTA) enqueue and
//       return immediately; the tx task drains urgent control, then ordinary
//       control, then bulk, then one microphone frame. A button press
//       therefore overtakes a backlog of audio instead of waiting behind it.
//   RX  the websocket callback copies the assembled message into a ring and
//       returns in microseconds; kiki_net_rx does the parsing, the display
//       work and the playback queueing well away from the lock.
//
// Wire order is preserved on the way in (one ring, not one per kind): a
// late-processed audio_stop is harmless because it carries stream watermarks,
// but an audio_end reordered ahead of its own PCM would end a reply early.
constexpr size_t kEventBytes = 960;   // device_stats is the longest, ~800 bytes
struct TxEvent {
    uint16_t len;
    char json[kEventBytes];
};

// TxClass and tx_classify live in gateway_tx_priority.hpp: which events
// overtake audio is a behavioural contract, so it is kept free of ESP-IDF and
// tested on the host (firmware/tests/test_tx_priority.cpp).
// One staging buffer, not one per caller. A TxEvent is ~960 bytes and
// gateway_client_send_event() is called from tasks with 3-4 KiB of stack --
// the motion reaction task, the log tap, the shutdown task, the LVGL timer --
// where putting it on the stack alongside the telemetry task's own 768-byte
// snprintf buffer is a stack overflow waiting for a busy moment. Senders hold
// this only for a memcpy and a queue send, so contention is measured in
// microseconds; the tx task never takes it at all.
SemaphoreHandle_t g_tx_staging_lock = nullptr;
TxEvent *g_tx_staging = nullptr;

QueueHandle_t g_tx_urgent = nullptr;
QueueHandle_t g_tx_normal = nullptr;
QueueHandle_t g_tx_bulk = nullptr;
TaskHandle_t g_tx_task = nullptr;
RingbufHandle_t g_rx_ring = nullptr;
std::atomic<uint32_t> g_tx_dropped{0};
std::atomic<uint32_t> g_rx_dropped{0};

// How long the last few sends actually took. This is the only early warning a
// board gets that its uplink is about to cost it the session, and app_main's
// governor reads it to decide whether to keep streaming audio.
constexpr size_t kSendSamples = 16;
std::atomic<uint32_t> g_send_us[kSendSamples];
std::atomic<uint32_t> g_send_index{0};
std::atomic<uint32_t> g_send_slow{0};

void note_send_time(int64_t micros) {
    const uint32_t slot = g_send_index.fetch_add(1) % kSendSamples;
    g_send_us[slot] = static_cast<uint32_t>(micros < 0 ? 0 : micros);
}

esp_websocket_client_handle_t g_client = nullptr;
std::atomic<int> g_uri_index{0};  // 0 = LAN, 1 = learned, 2 = compiled-in
std::atomic<int> g_failed_attempts{0};
std::atomic<bool> g_switch_requested{false};
// A switch to one *specific* slot, as opposed to "the next one that might
// work". Set when a better address becomes known rather than when the current
// one breaks: -1 means nothing requested.
std::atomic<int> g_switch_to{-1};
// Set when the server closed politely and the client will not retry by itself.
// Handled by failover_task, which reconnects to the SAME address.
std::atomic<bool> g_restart_requested{false};
std::atomic<bool> g_connected{false};
std::atomic<bool> g_audio_stream_ready{false};
// Reassembly buffer for a fragmented websocket message. Byte 0 holds the
// opcode so the completed message can go into the ring as a single item.
std::vector<uint8_t> g_rx;

// Cancelling a turn does not un-send the audio already in flight. With a
// half-second of send lead there can be that much PCM sitting in the laptop's
// socket buffer and the AP's queue when the user barges in, and every byte of it
// arrives *after* the audio_stop. These watermarks are what stop it from being
// played on top of the next reply. TTS and media are tracked separately because
// the gateway numbers the two streams in independent ranges.
std::atomic<uint32_t> g_last_tts_stream{0};
std::atomic<uint32_t> g_min_tts_stream{0};
std::atomic<uint32_t> g_last_media_stream{0};
std::atomic<uint32_t> g_min_media_stream{0};
// Websocket sessions lost since boot. Reported in hello, because the log that
// would have explained each one died with it.
std::atomic<uint32_t> g_socket_drops{0};

// Idempotent: a second audio_stop with nothing new in flight must not advance
// the watermark past the stream that is about to start, or the next reply is
// silently discarded.
// One failure counter for both failure events, because from here they mean the
// same thing: this address is not working.
void note_connection_failure() {
    if (!have_fallback()) return;
    if (++g_failed_attempts >= kAttemptsBeforeSwitch) {
        g_failed_attempts = 0;
        g_switch_requested = true;
    }
}

void raise_audio_watermark(const cJSON *explicit_stream, std::atomic<uint32_t> &last,
                           std::atomic<uint32_t> &minimum) {
    uint32_t cutoff = last.load();
    if (cJSON_IsNumber(explicit_stream) && explicit_stream->valuedouble >= 0) {
        cutoff = std::max(cutoff, static_cast<uint32_t>(explicit_stream->valuedouble));
    }
    if (cutoff >= minimum.load() && cutoff != UINT32_MAX) minimum = cutoff + 1;
}

void abandon_in_flight_audio(const cJSON *root) {
    // New gateways include explicit stream cutoffs. This closes the race where
    // audio_stop is parsed before a delayed PCM frame from that stream, so the
    // board's locally observed `last` value is not yet high enough to reject
    // it. Missing fields retain compatibility with old gateways.
    raise_audio_watermark(cJSON_GetObjectItemCaseSensitive(root, "tts_stream_id"),
                          g_last_tts_stream, g_min_tts_stream);
    raise_audio_watermark(cJSON_GetObjectItemCaseSensitive(root, "media_stream_id"),
                          g_last_media_stream, g_min_media_stream);
}

void handle_json(const char *data, size_t size) {
    cJSON *root = cJSON_ParseWithLength(data, size);
    if (!root) return;
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "type");
    if (!cJSON_IsString(type)) {
        cJSON_Delete(root);
        return;
    }
    if (strcmp(type->valuestring, "hello_ack") == 0) {
        // The gateway tells the device where to find it from outside. A quick
        // Cloudflare tunnel gets a new hostname every restart, so a URL
        // compiled into the firmware would be stale within a day; learning it
        // over the LAN link and keeping it in NVS means a changed hostname
        // costs a reconnect, not a reflash.
        const cJSON *fallback = cJSON_GetObjectItemCaseSensitive(root, "fallback_uri");
        if (cJSON_IsString(fallback) && fallback->valuestring[0]) {
            const bool learned_new = store_fallback_uri(fallback->valuestring);
            // The backup address exists to be escaped from. A quick Cloudflare
            // tunnel is renamed every time the laptop reboots, so after a
            // restart the address in NVS is dead and the board can only get
            // back in through the Tailscale funnel -- which works, but measured
            // 447 ms against Cloudflare's 20.6 ms because its ingress is in
            // Dubai. Reaching the gateway *at all* is how we are told the new
            // fast address, so the funnel's real job is bootstrapping, and
            // staying on it afterwards would mean paying 20x for the rest of
            // the session. Waiting for four failures would never happen: the
            // funnel is not failing.
            //
            // Only ever an upgrade. From the LAN there is nothing better to
            // move to, and index 1 is not tried again from itself.
            if (learned_new && g_uri_index.load() == 2) {
                ESP_LOGW(kTag, "learned a faster gateway; leaving the backup for %s",
                         fallback->valuestring);
                g_switch_to = 1;
            }
        }
        // A completed handshake is the strongest proof this build works.
        ota_confirm_alive();
    } else if (strcmp(type->valuestring, "firmware_update") == 0) {
        const cJSON *url = cJSON_GetObjectItemCaseSensitive(root, "url");
        if (cJSON_IsString(url)) ota_start(url->valuestring);
    } else if (strcmp(type->valuestring, "health_telemetry_ack") == 0) {
        const cJSON *batch = cJSON_GetObjectItemCaseSensitive(root, "batch_id");
        const cJSON *accepted = cJSON_GetObjectItemCaseSensitive(root, "accepted");
        if (cJSON_IsString(batch)) {
            wearable_handle_ack(batch->valuestring, cJSON_IsTrue(accepted));
        }
    } else if (strcmp(type->valuestring, "care_plan") == 0) {
        care_ui_set_plan(root);
    } else if (strcmp(type->valuestring, "exercise_instructor") == 0) {
        instructor_command(root);
    } else if (strcmp(type->valuestring, "imu_window_start") == 0) {
        // Health mode is timing a hold and wants the wrist recorded across it.
        // Ignored by a gateway in any other mode simply by never being sent.
        const cJSON *seconds = cJSON_GetObjectItemCaseSensitive(root, "seconds");
        if (cJSON_IsNumber(seconds)) {
            exercise_begin_window(static_cast<float>(seconds->valuedouble));
        }
    } else if (strcmp(type->valuestring, "imu_window_cancel") == 0) {
        exercise_cancel_window();
    } else if (strcmp(type->valuestring, "wearable_measure") == 0) {
        const cJSON *action = cJSON_GetObjectItemCaseSensitive(root, "action");
        const char *what = cJSON_IsString(action) ? action->valuestring : "start";
        if (strcmp(what, "cancel") == 0) {
            exercise_cancel_measurement();
        } else {
            exercise_request_measurement();
        }
    } else if (strcmp(type->valuestring, "fall_check_cancel") == 0) {
        wearable_cancel_fall_check("voice_ok");
    } else if (strcmp(type->valuestring, "fall_check_confirm") == 0) {
        wearable_confirm_fall_check("voice_help");
    } else if (strcmp(type->valuestring, "dashboard") == 0) {
        const cJSON *environment = cJSON_GetObjectItemCaseSensitive(root, "environment");
        if (cJSON_IsString(environment)) ui_set_dashboard_environment(environment->valuestring);
        const auto text_field = [root](const char *name) {
            const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, name);
            return cJSON_IsString(value) ? value->valuestring : "Unavailable";
        };
        const cJSON *alerts = cJSON_GetObjectItemCaseSensitive(root, "alert_count");
        const cJSON *updates = cJSON_GetObjectItemCaseSensitive(root, "whatsapp_count");
        ui_set_dashboard_details(text_field("weather"), text_field("alerts"), text_field("whatsapp"),
                                 cJSON_IsNumber(alerts) ? alerts->valueint : 0,
                                 cJSON_IsNumber(updates) ? updates->valueint : 0);
    } else if (strcmp(type->valuestring, "state") == 0) {
        const cJSON *state = cJSON_GetObjectItemCaseSensitive(root, "state");
        const cJSON *detail = cJSON_GetObjectItemCaseSensitive(root, "detail");
        if (cJSON_IsString(state)) {
            ui_set_state_detail(state->valuestring,
                                cJSON_IsString(detail) ? detail->valuestring : "");
            if (strcmp(state->valuestring, "warming") == 0) {
                g_audio_stream_ready = false;
            } else {
                g_audio_stream_ready = true;
            }
        }
    } else if (strcmp(type->valuestring, "transcript_final") == 0 ||
               strcmp(type->valuestring, "transcript_partial") == 0 ||
               strcmp(type->valuestring, "ambient") == 0) {
        // What the room said. `response_sentence` deliberately does NOT land
        // here: it arrives as the sentence is generated, well before it is
        // spoken, and painting it now is what put the caption ahead of the
        // voice. The gateway sends a `speech` event when the audio is due.
        const cJSON *text = cJSON_GetObjectItemCaseSensitive(root, "text");
        if (cJSON_IsString(text)) {
            ui_set_transcript(text->valuestring,
                              strcmp(type->valuestring, "ambient") == 0);
        }
    } else if (strcmp(type->valuestring, "speech") == 0) {
        const cJSON *text = cJSON_GetObjectItemCaseSensitive(root, "text");
        if (cJSON_IsString(text)) ui_set_speech(text->valuestring);
    } else if (strcmp(type->valuestring, "lcd") == 0) {
        // A mirrored commit from the legacy runtime's 16x2 display.
        const cJSON *line1 = cJSON_GetObjectItemCaseSensitive(root, "line1");
        const cJSON *line2 = cJSON_GetObjectItemCaseSensitive(root, "line2");
        ui_set_lcd(cJSON_IsString(line1) ? line1->valuestring : "",
                   cJSON_IsString(line2) ? line2->valuestring : "");
    } else if (strcmp(type->valuestring, "volume") == 0) {
        const cJSON *percent = cJSON_GetObjectItemCaseSensitive(root, "percent");
        if (cJSON_IsNumber(percent)) {
            audio_pipeline_set_volume(percent->valueint);
            // Keep the settings menu opening on the real level rather than the
            // build-time default.
            ui_set_volume_percent(percent->valueint);
        }
    } else if (strcmp(type->valuestring, "config_options") == 0) {
        wizard_offer(cJSON_GetObjectItemCaseSensitive(root, "options"));
    } else if (strcmp(type->valuestring, "config_saved") == 0) {
        const cJSON *restart = cJSON_GetObjectItemCaseSensitive(root, "restart_required");
        if (cJSON_IsTrue(restart)) {
            ui_set_lcd("Saved", "restart needed");
        }
    } else if (strcmp(type->valuestring, "gain") == 0) {
        const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, "value");
        if (cJSON_IsNumber(value)) ui_set_gain(static_cast<float>(value->valuedouble));
    } else if (strcmp(type->valuestring, "audio_stop") == 0) {
        instructor_stop();
        abandon_in_flight_audio(root);
        audio_pipeline_stop_playback();
    } else if (strcmp(type->valuestring, "audio_end") == 0) {
        // Ignore the end marker of a stream we already abandoned; honouring it
        // would cut the *next* reply short. `kind` disambiguates the two
        // independent stream-id ranges; an older gateway omits it and is
        // accepted unconditionally, exactly as before.
        const cJSON *stream = cJSON_GetObjectItemCaseSensitive(root, "stream_id");
        const cJSON *stream_kind = cJSON_GetObjectItemCaseSensitive(root, "kind");
        bool stale = false;
        if (cJSON_IsNumber(stream) && cJSON_IsString(stream_kind)) {
            const auto id = static_cast<uint32_t>(stream->valuedouble);
            stale = strcmp(stream_kind->valuestring, "media") == 0
                        ? id < g_min_media_stream.load()
                        : id < g_min_tts_stream.load();
        }
        if (!stale) audio_pipeline_mark_playback_end();
    } else if (strcmp(type->valuestring, "first_pcm") == 0) {
        const cJSON *ttfw = cJSON_GetObjectItemCaseSensitive(root, "ttfw_ms");
        if (cJSON_IsNumber(ttfw)) ui_set_latency(ttfw->valueint);
    } else if (strcmp(type->valuestring, "media_state") == 0) {
        const cJSON *loaded = cJSON_GetObjectItemCaseSensitive(root, "loaded");
        const cJSON *paused = cJSON_GetObjectItemCaseSensitive(root, "paused");
        const cJSON *mtitle = cJSON_GetObjectItemCaseSensitive(root, "title");
        ui_set_media(cJSON_IsTrue(loaded), cJSON_IsTrue(paused),
                     cJSON_IsString(mtitle) ? mtitle->valuestring : nullptr);
    } else if (strcmp(type->valuestring, "expression") == 0) {
        const cJSON *name = cJSON_GetObjectItemCaseSensitive(root, "name");
        if (cJSON_IsString(name)) ui_set_expression(name->valuestring);
    } else if (strcmp(type->valuestring, "dance_start") == 0) {
        // The routine always arrives before the first sample of its song, so
        // arming here is enough -- dance_note_media_frame() below anchors the
        // clock the moment that sample turns up.
        const cJSON *bpm = cJSON_GetObjectItemCaseSensitive(root, "bpm");
        const cJSON *beat0 = cJSON_GetObjectItemCaseSensitive(root, "beat0_ms");
        const cJSON *mood = cJSON_GetObjectItemCaseSensitive(root, "mood");
        const cJSON *dtitle = cJSON_GetObjectItemCaseSensitive(root, "title");
        const cJSON *routine = cJSON_GetObjectItemCaseSensitive(root, "routine");
        const cJSON *energy = cJSON_GetObjectItemCaseSensitive(root, "energy");
        if (cJSON_IsNumber(bpm) && cJSON_IsString(routine)) {
            dance_start(static_cast<float>(bpm->valuedouble),
                        cJSON_IsNumber(beat0) ? beat0->valueint : 0,
                        cJSON_IsString(mood) ? mood->valuestring : "excited",
                        cJSON_IsString(dtitle) ? dtitle->valuestring : "",
                        routine->valuestring,
                        cJSON_IsString(energy) ? energy->valuestring : "");
        } else {
            ESP_LOGW(kTag, "ignoring a dance_start with no tempo or routine");
        }
    } else if (strcmp(type->valuestring, "dance_stop") == 0) {
        // The gateway is the one asking, so there is nothing to tell it.
        dance_stop("gateway", false);
    }
    cJSON_Delete(root);
}

void queue_playback_from_16k(const uint8_t *payload, size_t bytes, bool new_stream,
                            bool media);

// Kiki's voice arrives as Opus on a link that cannot carry PCM. The board only
// ever decodes -- the gateway does the encoding -- and decoding is roughly a
// tenth of the cost of encoding, which is why this can live here at all: the
// AEC already uses 23 ms of every 32 ms frame on core 1, and had this needed an
// encoder there would have been nowhere to put it. handle_binary runs on
// kiki_net_rx (core 0), so this never competes with the AEC.
void *g_opus_dec = nullptr;
int16_t *g_opus_pcm = nullptr;
// 120 ms at 16 kHz is the largest frame Opus defines. Sizing for it means a
// gateway that switches frame duration can never overflow this buffer.
constexpr size_t kOpusPcmSamples = 1920;

bool opus_decoder_ready() {
    if (g_opus_dec) return true;
    if (!g_opus_pcm) {
        // PSRAM: internal RAM is the scarce pool on this board and this buffer
        // is only ever touched by one task, at 50 Hz.
        g_opus_pcm = static_cast<int16_t *>(
            heap_caps_malloc(kOpusPcmSamples * sizeof(int16_t), MALLOC_CAP_SPIRAM));
        if (!g_opus_pcm) {
            ESP_LOGE(kTag, "no PSRAM for the opus output buffer");
            return false;
        }
    }
    esp_opus_dec_cfg_t cfg = ESP_OPUS_DEC_CONFIG_DEFAULT();
    cfg.sample_rate = 16000;
    cfg.channel = 1;
    cfg.frame_duration = ESP_OPUS_DEC_FRAME_DURATION_20_MS;
    // Bare packets, matching the gateway's encoder. If these two ever disagree
    // every packet decodes to noise rather than failing cleanly, so they are
    // stated explicitly on both sides rather than left to a default.
    cfg.self_delimited = false;
    const esp_audio_err_t err = esp_opus_dec_open(&cfg, sizeof(cfg), &g_opus_dec);
    if (err != ESP_AUDIO_ERR_OK) {
        ESP_LOGE(kTag, "opus decoder would not open (%d)", static_cast<int>(err));
        g_opus_dec = nullptr;
        return false;
    }
    ESP_LOGI(kTag, "opus decoder open (16 kHz mono, 20 ms frames)");
    return true;
}

// Decode cost and output size, both reported in device_stats. The failure
// this exists to diagnose is silent: the board starves with an empty buffer
// while the gateway's own accounting shows the audio went out ahead of
// schedule, and nothing is dropped or logged anywhere. Either the decode is
// too slow for real time (50 frames a second, 20 ms each -- anything near
// 20 ms per frame cannot keep up) or it is producing less audio than it
// should. One of these two numbers says which.
std::atomic<uint32_t> g_opus_decode_us_max{0};
std::atomic<uint32_t> g_opus_frames{0};
std::atomic<uint32_t> g_opus_pcm_bytes{0};

void decode_opus_to_playback(const uint8_t *payload, size_t bytes, bool new_stream) {
    if (!opus_decoder_ready()) return;
    if (new_stream) {
        // A fresh reply must not inherit the previous one's decoder state.
        esp_opus_dec_reset(g_opus_dec);
    }
    esp_audio_dec_in_raw_t raw = {};
    raw.buffer = const_cast<uint8_t *>(payload);
    raw.len = static_cast<uint32_t>(bytes);
    esp_audio_dec_out_frame_t out = {};
    out.buffer = reinterpret_cast<uint8_t *>(g_opus_pcm);
    out.len = static_cast<uint32_t>(kOpusPcmSamples * sizeof(int16_t));
    // dec_info is documented [out] and looks optional, but the decoder
    // validates it and rejects a null one with "Invalid parameter 'opus
    // information'" -- every packet failing, which reads on the panel as a
    // reply that arrives and makes no sound at all. Every example in the
    // component's own tests passes a real struct; so does this.
    esp_audio_dec_info_t info = {};
    const int64_t started_us = esp_timer_get_time();
    const esp_audio_err_t err = esp_opus_dec_decode(g_opus_dec, &raw, &out, &info);
    const uint32_t took_us = static_cast<uint32_t>(esp_timer_get_time() - started_us);
    if (took_us > g_opus_decode_us_max.load()) g_opus_decode_us_max = took_us;
    if (err != ESP_AUDIO_ERR_OK) {
        // One bad packet is a click, not a reason to tear the stream down.
        static uint32_t failures = 0;
        if ((++failures % 50) == 1) {
            ESP_LOGW(kTag, "opus decode failed (%d)", static_cast<int>(err));
        }
        return;
    }
    if (out.decoded_size == 0) return;
    g_opus_frames.fetch_add(1);
    g_opus_pcm_bytes.fetch_add(out.decoded_size);
    queue_playback_from_16k(reinterpret_cast<const uint8_t *>(g_opus_pcm),
                            out.decoded_size, new_stream, false);
}

void handle_binary(const uint8_t *data, size_t size) {
    const AudioHeader *header = nullptr;
    const uint8_t *payload = nullptr;
    size_t payload_size = 0;
    if (!parse_audio_frame(data, size, &header, &payload, &payload_size)) {
        ESP_LOGW(kTag, "bad binary frame (%u bytes)", static_cast<unsigned>(size));
        return;
    }
    const auto kind = static_cast<BinaryKind>(header->kind);
    if (kind == BinaryKind::TtsOpus16k) {
        if (header->stream_id < g_min_tts_stream.load()) return;
        const bool new_stream = header->stream_id != g_last_tts_stream.load();
        g_last_tts_stream = header->stream_id;
        decode_opus_to_playback(payload, payload_size, new_stream);
        return;
    }
    if (kind == BinaryKind::TtsPcmS16Mono48k || kind == BinaryKind::TtsPcmS16Mono16k) {
        if (header->stream_id < g_min_tts_stream.load()) return;
        const bool new_stream = header->stream_id != g_last_tts_stream.load();
        g_last_tts_stream = header->stream_id;
        if (kind == BinaryKind::TtsPcmS16Mono16k) {
            queue_playback_from_16k(payload, payload_size, new_stream, false);
            return;
        }
    } else if (kind == BinaryKind::MediaPcmS16Mono48k ||
               kind == BinaryKind::MediaPcmS16Mono16k) {
        if (header->stream_id < g_min_media_stream.load()) return;
        const bool new_stream = header->stream_id != g_last_media_stream.load();
        g_last_media_stream = header->stream_id;
        // Before the queue, not after: the mark has to be the sample count as
        // it was *in front of* this chunk, or the dance starts one frame late.
        dance_note_media_frame();
        if (kind == BinaryKind::MediaPcmS16Mono16k) {
            queue_playback_from_16k(payload, payload_size, new_stream, true);
            return;
        }
    } else {
        return;
    }
    if (audio_pipeline_queue_playback(payload, payload_size) != ESP_OK) {
        ESP_LOGW(kTag, "playback queue full; dropping %u bytes", static_cast<unsigned>(payload_size));
    }
}

// The codec runs at 48 kHz for everything, so 16 kHz speech from the remote
// link is expanded here rather than by reconfiguring it mid-reply -- media
// playback is still 48 kHz and would have to be switched back.
//
// Linear interpolation, deliberately: the images it leaves sit above 8 kHz,
// where this speaker has very little output anyway, and the alternative costs
// a filter pass on every reply for something nobody can hear on a 20 mm driver.
void queue_playback_from_16k(const uint8_t *payload, size_t bytes, bool new_stream,
                            bool media) {
    // Speech and music each keep their own last sample. They interleave (a
    // reply over a ducked song), and sharing one carried value would let the
    // tail of a sentence become the first interpolation step of the music.
    static int16_t previous_speech = 0;
    static int16_t previous_media = 0;
    int16_t &previous = media ? previous_media : previous_speech;
    // Starting a fresh reply from the tail of the last one would put an audible
    // step at the top of every sentence.
    if (new_stream) previous = 0;

    const auto *in = reinterpret_cast<const int16_t *>(payload);
    const size_t count = bytes / sizeof(int16_t);
    static int16_t out[3 * 256];
    size_t consumed = 0;
    while (consumed < count) {
        const size_t chunk = (count - consumed) < 256 ? (count - consumed) : 256;
        size_t produced = 0;
        for (size_t i = 0; i < chunk; ++i) {
            const int32_t current = in[consumed + i];
            const int32_t step = current - previous;
            out[produced++] = static_cast<int16_t>(previous + step / 3);
            out[produced++] = static_cast<int16_t>(previous + (2 * step) / 3);
            out[produced++] = static_cast<int16_t>(current);
            previous = static_cast<int16_t>(current);
        }
        if (audio_pipeline_queue_playback(reinterpret_cast<const uint8_t *>(out),
                                          produced * sizeof(int16_t)) != ESP_OK) {
            ESP_LOGW(kTag, "playback queue full; dropping %u bytes",
                     static_cast<unsigned>(produced * sizeof(int16_t)));
        }
        consumed += chunk;
    }
}

void websocket_event(void *, esp_event_base_t, int32_t event_id, void *event_data) {
    auto *event = static_cast<esp_websocket_event_data_t *>(event_data);
    switch (event_id) {
        case WEBSOCKET_EVENT_CONNECTED:
            g_connected = true;
            g_audio_stream_ready = false;
            // A new session restarts the gateway's stream numbering: it builds
            // a fresh DeviceSession per connection, with tts_stream_id back at
            // 0 and media_stream_id back at 1000. The watermarks, being
            // globals, survived the reconnect -- so every audio_stop in the
            // PREVIOUS session (a duck, a pause, a barge-in) left a floor that
            // the new session's ids start out below, and the board silently
            // discarded Kiki's speech. That is the "she talks, the caption
            // appears, no sound comes out" failure: nothing is broken
            // anywhere else, the frames are simply dropped on arrival. It
            // cleared itself only once enough turns pushed the id back above
            // the floor, and every new audio_stop raised it again.
            //
            // Nothing can be in flight on a socket that has just opened, so
            // there is nothing for a watermark to suppress here.
            g_min_tts_stream = 0;
            g_last_tts_stream = 0;
            g_min_media_stream = 0;
            g_last_media_stream = 0;
            g_failed_attempts = 0;
            // Whatever was still queued belongs to the session that just died:
            // a stale cancel_turn or media_control replayed into a fresh
            // session acts on a turn nobody started. hello must also be the
            // first thing on this wire.
            for (QueueHandle_t queue : {g_tx_urgent, g_tx_normal, g_tx_bulk}) {
                if (queue) xQueueReset(queue);
            }
            ESP_LOGI(kTag, "connected via %s", gateway_uri());
            ui_set_connected(true);
            gateway_client_send_event("hello");
            gateway_client_send_event(
                "set_barge_in",
                settings_barge_in_enabled() ? "\"enabled\":true" : "\"enabled\":false");
            break;
        case WEBSOCKET_EVENT_CLOSED:
            // A close the server asked for politely, with a close frame --
            // which is what the gateway now sends when it shuts down for a
            // deploy, and when it evicts a superseded socket. The client
            // treats that as the session ending on purpose and does NOT
            // auto-reconnect, so without this the board sits there, on Wi-Fi,
            // pingable, and never comes back. It looks exactly like a dead
            // board: the buttons do nothing because there is no socket for
            // their events to travel on.
            //
            // Restarting must not happen in this callback -- stopping the
            // client from its own task deadlocks -- so ask the failover task
            // to do it, and stay on the same address, because a polite close
            // says nothing bad about the address.
            g_connected = false;
            g_audio_stream_ready = false;
            g_socket_drops.fetch_add(1);
            dance_stop("disconnected", false);
            instructor_stop();
            ui_set_connected(false, "reconnecting");
            g_restart_requested = true;
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
            g_connected = false;
            g_audio_stream_ready = false;
            g_socket_drops.fetch_add(1);
            // No link means no music, so the stage has to close. Doing it here
            // rather than waiting for the media watchdog keeps the reconnect
            // screen from appearing three seconds into an empty dance floor.
            dance_stop("disconnected", false);
            instructor_stop();
            {
                // Names the actual suspect. A wss:// address with no clock is
                // the one combination that can never work, and it used to look
                // identical to a network outage.
                const int index = g_uri_index.load();
                const char *where = index == 0 ? "lan" : (index == 1 ? "cloud" : "backup");
                char detail[40];
                std::snprintf(detail, sizeof(detail), "%s%s", where,
                              uri_is_tls(gateway_uri()) && !time_is_set() ? " - NO CLOCK" : "");
                ui_set_connected(false, detail);
            }
            note_connection_failure();
            break;
        case WEBSOCKET_EVENT_DATA: {
            if (event->payload_offset == 0) {
                // The opcode rides in byte 0 of the same buffer, so one message
                // is one ring item. Two items (opcode, then payload) would
                // desync forever the first time the second send failed.
                g_rx.assign(1, static_cast<uint8_t>(event->op_code));
                g_rx.reserve(1 + event->payload_len);
            }
            g_rx.insert(g_rx.end(), event->data_ptr, event->data_ptr + event->data_len);
            if (event->fin && g_rx.size() == static_cast<size_t>(event->payload_len) + 1) {
                // This runs on the websocket task with client->lock held, so it
                // does nothing but copy. Parsing here is what used to freeze the
                // socket behind an LVGL render or a full playback ring.
                const uint8_t opcode = g_rx[0];
                if ((opcode == 0x1 || opcode == 0x2) && g_rx_ring) {
                    // Never wait. A full ring means kiki_net_rx is starved, and
                    // blocking to fix that would hold the very lock starving it.
                    if (xRingbufferSend(g_rx_ring, g_rx.data(), g_rx.size(), 0) != pdTRUE) {
                        if (g_rx_dropped.fetch_add(1) % 50 == 0) {
                            ESP_LOGW(kTag, "inbound ring full; dropping a frame");
                        }
                    }
                }
                g_rx.clear();
            }
            break;
        }
        case WEBSOCKET_EVENT_ERROR:
            // Counted HERE, not only on DISCONNECTED. A connect attempt that
            // never completes raises ERROR and nothing else -- DISCONNECTED is
            // for an established connection going away. Counting only the
            // latter meant a board that could not reach the LAN gateway at all
            // retried that one address forever and never tried the public one,
            // which is precisely the "Reconnecting" loop seen from a hotspot.
            ESP_LOGW(kTag, "websocket transport error on %s", gateway_uri());
            note_connection_failure();
            break;
        default:
            break;
    }
}

// Everything the old websocket callback used to do, moved somewhere it is
// allowed to be slow. Wire order is preserved because there is one ring and one
// consumer.
TaskHandle_t g_rx_task = nullptr;

void rx_task(void *) {
    while (true) {
        size_t bytes = 0;
        auto *item = static_cast<uint8_t *>(
            xRingbufferReceive(g_rx_ring, &bytes, portMAX_DELAY));
        if (!item) continue;
        if (bytes >= 1) {
            const uint8_t opcode = item[0];
            if (opcode == 0x1) {
                handle_json(reinterpret_cast<const char *>(item + 1), bytes - 1);
            } else if (opcode == 0x2) {
                handle_binary(item + 1, bytes - 1);
            }
        }
        vRingbufferReturnItem(g_rx_ring, item);
    }
}

bool uri_is_tls(const char *uri) { return uri && strncmp(uri, "wss://", 6) == 0; }

// Returns the URI with an explicit port, because esp_websocket_client_set_uri()
// only overrides the port when the new URI carries one. Switching from
// ws://host:8765 to wss://host/ therefore kept 8765 and dialled the public
// gateway on a port nothing listens on -- a silent timeout that looks exactly
// like the host being unreachable.
std::string with_explicit_port(const char *uri) {
    if (!uri || !*uri) return "";
    const bool tls = uri_is_tls(uri);
    const size_t scheme = tls ? 6 : (strncmp(uri, "ws://", 5) == 0 ? 5 : 0);
    if (scheme == 0) return uri;
    std::string out(uri);
    size_t host_end = out.find('/', scheme);
    if (host_end == std::string::npos) host_end = out.size();
    if (out.find(':', scheme) < host_end) return out;  // already explicit
    out.insert(host_end, tls ? ":443" : ":80");
    return out;
}

// Three addresses, tried in order and cycled through on repeated failure:
// the LAN, then whatever public URL the gateway last taught this board, then
// the one compiled in. The learned URL goes first because it is the current
// truth and usually the fast one; the compiled-in URL stays in the rotation
// because a learned hostname can go stale -- a Cloudflare quick tunnel gets a
// new name every restart -- and a stale entry must not be able to hide a
// working one.
const char *uri_at(int index) {
    switch (index) {
        case 1: return g_learned_fallback.c_str();
        case 2: return kFallbackUri;
        default: return kPrimaryUri;
    }
}
bool have_fallback() { return uri_at(1)[0] != '\0' || uri_at(2)[0] != '\0'; }
const char *gateway_uri() { return uri_at(g_uri_index.load()); }

// Skips the empty slots so an unconfigured address never costs a retry cycle.
int next_uri_index(int from) {
    for (int step = 1; step <= 3; ++step) {
        const int candidate = (from + step) % 3;
        if (uri_at(candidate)[0] != '\0') return candidate;
    }
    return 0;
}

void load_fallback_uri() {
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READONLY, &handle) != ESP_OK) return;
    size_t length = 0;
    if (nvs_get_str(handle, kKeyFallback, nullptr, &length) == ESP_OK && length > 1 &&
        length < 256) {
        std::string value(length, '\0');
        if (nvs_get_str(handle, kKeyFallback, value.data(), &length) == ESP_OK) {
            value.resize(strlen(value.c_str()));
            g_learned_fallback = value;
            ESP_LOGI(kTag, "remembered public gateway %s", g_learned_fallback.c_str());
        }
    }
    nvs_close(handle);
}

// Returns true only when this is a *different* URL from the one already known.
// That distinction is what makes the bootstrap below safe: a quick tunnel hands
// out the same hostname for as long as it lives, so a repeat cannot re-trigger
// a switch and the board cannot ping-pong between two addresses.
bool store_fallback_uri(const char *uri) {
    if (g_learned_fallback == uri) return false;
    g_learned_fallback = uri;
    ESP_LOGI(kTag, "public gateway is now %s", uri);
    nvs_handle_t handle;
    // Still "new" even if it cannot be persisted: the address in hand is better
    // than the one we were using, and failing to remember it for next boot is a
    // separate problem from failing to use it now.
    if (nvs_open(kNamespace, NVS_READWRITE, &handle) != ESP_OK) return true;
    nvs_set_str(handle, kKeyFallback, uri);
    nvs_commit(handle);
    nvs_close(handle);
    return true;
}

// The switch cannot happen in the websocket event callback: stopping the client
// from its own task deadlocks, because stop waits for that task to finish.
void failover_task(void *) {
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(500));
        const int target = g_switch_to.exchange(-1);
        const bool requested = g_switch_requested.exchange(false);
        if (g_restart_requested.exchange(false) && target < 0 && !requested) {
            // Same address, fresh client. The gateway went away on purpose and
            // is coming back on the same port.
            ESP_LOGW(kTag, "gateway closed the session; reconnecting to %s",
                     g_current_uri.c_str());
            esp_websocket_client_stop(g_client);
            vTaskDelay(pdMS_TO_TICKS(1000));
            esp_websocket_client_start(g_client);
            continue;
        }
        if (target < 0 && !requested) continue;
        if (target >= 0 && uri_at(target)[0] != '\0') {
            g_uri_index = target;
            g_current_uri = with_explicit_port(gateway_uri());
            ESP_LOGW(kTag, "moving to the better gateway %s", g_current_uri.c_str());
        } else {
            g_uri_index = next_uri_index(g_uri_index.load());
            g_current_uri = with_explicit_port(gateway_uri());
            ESP_LOGW(kTag, "gateway unreachable; trying %s", g_current_uri.c_str());
        }
        esp_websocket_client_stop(g_client);
        esp_websocket_client_set_uri(g_client, g_current_uri.c_str());
        g_failed_attempts = 0;
        esp_websocket_client_start(g_client);
    }
}

}  // namespace

esp_err_t gateway_client_start() {
    load_fallback_uri();
    // PSRAM for all of it. Internal RAM is the scarce pool on this board
    // (~166 KiB of heap, and the display's SPI driver competes for the DMA-capable part
    // of it on every flush), while PSRAM has megabytes free.
    g_tx_urgent = xQueueCreateWithCaps(8, sizeof(TxEvent), MALLOC_CAP_SPIRAM);
    g_tx_normal = xQueueCreateWithCaps(12, sizeof(TxEvent), MALLOC_CAP_SPIRAM);
    g_tx_bulk = xQueueCreateWithCaps(8, sizeof(TxEvent), MALLOC_CAP_SPIRAM);
    g_tx_staging_lock = xSemaphoreCreateMutex();
    g_tx_staging = static_cast<TxEvent *>(
        heap_caps_malloc(sizeof(TxEvent), MALLOC_CAP_SPIRAM));
    // NOSPLIT so one websocket message is one item: a byte ring would hand the
    // reader half a frame and the other half of the previous one.
    g_rx_ring = xRingbufferCreateWithCaps(128 * 1024, RINGBUF_TYPE_NOSPLIT,
                                          MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!g_tx_urgent || !g_tx_normal || !g_tx_bulk || !g_rx_ring ||
        !g_tx_staging_lock || !g_tx_staging) return ESP_ERR_NO_MEM;
    // Priority 6: above the UI and the log tap, below audio. It must keep up
    // with the socket, but it must never outrank the 10 ms codec deadline.
    //
    // 5120 bytes was enough while this task only parsed frames. It is not
    // enough now that it decodes Opus: libopus keeps its decode scratch on the
    // stack, and the overflow landed exactly where the first packet of a reply
    // arrives -- so the board rebooted every time she was about to speak, which
    // reads as "she never speaks" all over again. The real usage is reported as
    // rx_stack_free in device_stats rather than left to another guess.
    xTaskCreatePinnedToCore(rx_task, "kiki_net_rx", 16384, nullptr, 6, &g_rx_task, 0);
    esp_websocket_client_config_t config = {};
    g_current_uri = with_explicit_port(gateway_uri());
    config.uri = g_current_uri.c_str();
    config.buffer_size = 16384;
    config.network_timeout_ms = 5000;
    config.reconnect_timeout_ms = 1000;
    config.ping_interval_sec = 10;
    // Always attached. TLS configuration cannot be added to a client after it
    // is built, and the URI can become a wss:// one at any point -- either by
    // failing over, or by the gateway teaching this device a new public URL.
    config.crt_bundle_attach = esp_crt_bundle_attach;
    g_client = esp_websocket_client_init(&config);
    if (!g_client) return ESP_ERR_NO_MEM;
    ESP_RETURN_ON_ERROR(
        esp_websocket_register_events(g_client, WEBSOCKET_EVENT_ANY, websocket_event, nullptr),
        kTag, "register events");
    ESP_LOGI(kTag, "connecting to %s", config.uri);
    // Started unconditionally: a board that has never met the gateway has no
    // fallback yet, and will be given one on its first hello_ack.
    xTaskCreate(failover_task, "kiki_failover", 4096, nullptr, 4, nullptr);
    return esp_websocket_client_start(g_client);
}

bool gateway_client_is_remote() { return g_uri_index.load() != 0; }

// Which of the three addresses is in use, by name. Reported in `hello` so the
// gateway log records it: the board's own "switching now" message is written
// microseconds before the socket is torn down for that switch, so it never
// reaches anyone. Consecutive hellos reading `backup` then `cloud` are the
// durable evidence that the bootstrap worked.
const char *gateway_client_link_name() {
    switch (g_uri_index.load()) {
        case 0: return "lan";
        case 1: return "cloud";
        default: return "backup";
    }
}

uint32_t gateway_client_socket_drops() { return g_socket_drops.load(); }

bool gateway_client_connected() {
    return g_connected.load() && g_audio_stream_ready.load();
}

namespace {

// 48 kHz mono s16 is 768 kbps. Measured on the phone hotspot + public tunnel
// the board actually gets ~396 kbps, so the microphone alone was roughly twice
// the available uplink: of 3449 frames captured during one test, 74 arrived.
// Hold-to-talk could not work there, and no buffering fixes a link that is
// simply too slow.
//
// Nothing downstream wants 48 kHz anyway -- the gateway immediately low-passes
// and decimates to 16 kHz for the wake word, the VAD and Whisper. Doing that
// here instead costs 256 kbps on the wire and loses nothing but the RNNoise
// denoise pass, which needs 48 kHz input and is a fair trade for audio that
// actually arrives.
//
// Same 7.2 kHz windowed-sinc as the gateway's Decimator48To16, so the two paths
// sound alike: -0.4 dB at 3 kHz, -35 dB by 12 kHz.
constexpr size_t kDecimTaps = 15;
constexpr size_t kMicFrameSamples = 480;
constexpr float kDecimK[kDecimTaps] = {
    0.00111830f, -0.00389476f, -0.01603492f, -0.02036377f, 0.02095181f,
    0.12449781f, 0.24450683f, 0.29843741f, 0.24450683f, 0.12449781f,
    0.02095181f, -0.02036377f, -0.01603492f, -0.00389476f, 0.00111830f};

// Carried between frames so the filter does not restart at every packet
// boundary, which would put a click every 10 ms into the stream.
float g_decim_history[kDecimTaps - 1] = {};

// Only ever called from the microphone network task, which is why the working
// buffers can be static rather than 2 KB of stack in a 6 KB task.
size_t decimate_to_16k(const int16_t *in, size_t count, int16_t *out) {
    static float work[kDecimTaps - 1 + kMicFrameSamples];
    for (size_t i = 0; i < kDecimTaps - 1; ++i) work[i] = g_decim_history[i];
    for (size_t i = 0; i < count; ++i) work[kDecimTaps - 1 + i] = static_cast<float>(in[i]);

    size_t produced = 0;
    for (size_t j = 0; j < count; j += 3) {
        float acc = 0.0f;
        for (size_t t = 0; t < kDecimTaps; ++t) acc += kDecimK[t] * work[j + t];
        const int32_t rounded = static_cast<int32_t>(acc >= 0 ? acc + 0.5f : acc - 0.5f);
        out[produced++] = static_cast<int16_t>(rounded > 32767    ? 32767
                                               : rounded < -32768 ? -32768
                                                                  : rounded);
    }
    const size_t tail = kDecimTaps - 1 + count;
    for (size_t i = 0; i < kDecimTaps - 1; ++i) g_decim_history[i] = work[tail - (kDecimTaps - 1) + i];
    return produced;
}

}  // namespace

esp_err_t gateway_client_send_mic(const int16_t *samples, size_t count,
                                  uint32_t sample_rate, uint8_t flags,
                                  uint32_t sequence, int64_t timestamp_us) {
    if (!g_connected || !g_client) return ESP_ERR_INVALID_STATE;
    if (sample_rate != 16000 && sample_rate != 48000) return ESP_ERR_INVALID_ARG;
    BinaryKind kind = sample_rate == 16000 ? BinaryKind::MicPcmS16Mono16k
                                           : BinaryKind::MicPcmS16Mono48k;
    const int16_t *payload = samples;
    size_t payload_count = count;
    static int16_t narrow[kMicFrameSamples / 3];
    if (sample_rate == 48000 && gateway_client_is_remote() && count == kMicFrameSamples) {
        payload_count = decimate_to_16k(samples, count, narrow);
        payload = narrow;
        kind = BinaryKind::MicPcmS16Mono16k;
    }
    auto frame = make_audio_frame(kind, flags, 0, sequence,
                                  static_cast<uint64_t>(timestamp_us), payload,
                                  payload_count * sizeof(int16_t));
    // Deliberately generous, and NOT a load-shedding mechanism. A timeout here
    // returns 0, and esp_websocket_client treats a short write as fatal:
    //
    //     if (wlen < 0 || (wlen == 0 && need_write != 0)) { ...
    //         esp_websocket_client_abort_connection(...)
    //
    // so every expired send tears the socket down. Shortening this to 200 ms
    // to "drop a frame under congestion" therefore did the opposite of what it
    // looked like -- it made the abort five times more likely and the board
    // reconnected constantly. Congestion is shed in microphone_network_task,
    // before the send, where dropping a frame is actually just a dropped
    // frame. This timeout is only the backstop for a genuinely dead link.
    const int64_t started = esp_timer_get_time();
    int sent = esp_websocket_client_send_bin(
        g_client, reinterpret_cast<const char *>(frame.data()), frame.size(),
        kSendTimeout);
    // Every send is timed in one place, because how long a send takes is the
    // whole early-warning system: while this call is inside the client mutex
    // nothing is read from the socket and no PING can go out.
    note_send_time(esp_timer_get_time() - started);
    return sent == static_cast<int>(frame.size()) ? ESP_OK : ESP_FAIL;
}

namespace {

// The only place in the firmware that actually writes to the socket. Everything
// else queues; see the note beside the queue declarations.
esp_err_t transmit_text(const char *json, size_t len) {
    if (!g_connected || !g_client) return ESP_ERR_INVALID_STATE;
    const int64_t started = esp_timer_get_time();
    const int sent = esp_websocket_client_send_text(g_client, json, len, kSendTimeout);
    note_send_time(esp_timer_get_time() - started);
    return sent == static_cast<int>(len) ? ESP_OK : ESP_FAIL;
}

QueueHandle_t queue_for(TxClass klass) {
    switch (klass) {
        case TxClass::Urgent: return g_tx_urgent;
        case TxClass::Bulk: return g_tx_bulk;
        default: return g_tx_normal;
    }
}

// Drops the OLDEST, not the newest. A queue that has backed up is holding stale
// state -- an old battery reading, a superseded log line -- and the message
// worth keeping is always the one describing what is happening now.
bool enqueue(TxClass klass, const std::string &message) {
    QueueHandle_t queue = queue_for(klass);
    if (!queue || !g_tx_staging || message.size() >= kEventBytes) return false;
    // A short wait, not none: losing a button press to a microsecond of
    // contention would be a worse bug than the one this whole path fixes. It is
    // still far too short to be the blocking that made the panel hang.
    if (xSemaphoreTake(g_tx_staging_lock, pdMS_TO_TICKS(20)) != pdTRUE) {
        g_tx_dropped.fetch_add(1);
        return false;
    }
    g_tx_staging->len = static_cast<uint16_t>(message.size());
    memcpy(g_tx_staging->json, message.data(), message.size());
    bool queued = xQueueSend(queue, g_tx_staging, 0) == pdTRUE;
    if (!queued && klass == TxClass::Urgent) {
        // Make room by discarding the OLDEST. A queue that has backed up holds
        // stale intent -- a press from before the last reply -- and the event
        // worth keeping is always the one describing what just happened.
        // The staging buffer doubles as the scratch the discarded item is read
        // into -- its contents are expendable, the send above already copied
        // them into the queue -- and is then refilled from `message`.
        xQueueReceive(queue, g_tx_staging, 0);
        g_tx_staging->len = static_cast<uint16_t>(message.size());
        memcpy(g_tx_staging->json, message.data(), message.size());
        queued = xQueueSend(queue, g_tx_staging, 0) == pdTRUE;
        if (queued) g_tx_dropped.fetch_add(1);
    }
    xSemaphoreGive(g_tx_staging_lock);
    if (!queued) g_tx_dropped.fetch_add(1);
    return queued;
}

std::string build_event(const char *type, const char *extra_json) {
    std::string message = "{\"v\":1,\"type\":\"" + std::string(type) + "\"";
    if (strcmp(type, "hello") == 0) {
        // The build ID, not a hand-maintained version string. "0.1.0" was true
        // of every image ever built and so answered nobody's question; the
        // first four bytes of the ELF SHA-256 change on every link, so the
        // gateway log can always say exactly which image is talking to it.
        // This is the same stamp the panel shows at boot.
        static const std::string build = [] {
            const esp_app_desc_t *app = esp_app_get_description();
            char id[16];
            std::snprintf(id, sizeof(id), "%02x%02x%02x%02x", app->app_elf_sha256[0],
                          app->app_elf_sha256[1], app->app_elf_sha256[2],
                          app->app_elf_sha256[3]);
            return std::string(id);
        }();
        // The link matters to the gateway: it decides the audio rate from it.
        // Only the board knows -- it is the one that chose between the LAN URI
        // and the tunnel, and from the gateway's side both tunnelled traffic
        // and a local test client arrive from 127.0.0.1.
        // Health from the session that just died. The device log travels on
        // the connection that failed, so this is the only place the reason can
        // arrive: a board that keeps reconnecting explains itself on the way
        // back in.
        const WifiHealth wifi = wifi_station_health();
        char ssid[33] = {};
        wifi_station_current_ssid(ssid, sizeof(ssid));
        char health[200];
        std::snprintf(health, sizeof(health),
                      ",\"rssi\":%d,\"wifi_drops\":%u,\"wifi_reason\":%u,"
                      "\"ws_drops\":%u,\"ssid\":\"%s\"",
                      static_cast<int>(wifi.rssi),
                      static_cast<unsigned>(wifi.disconnects),
                      static_cast<unsigned>(wifi.last_reason),
                      static_cast<unsigned>(g_socket_drops.load()),
                      ssid);
        message += ",\"token\":\"" CONFIG_KIKI_GATEWAY_TOKEN
                   "\",\"device\":\"waveshare-amoled-1.75\",\"firmware\":\"" +
                   build + "\",\"link\":\"" + gateway_client_link_name() + "\"" +
                   // What this image can decode, best first. The gateway picks
                   // from this list rather than assuming, so an older gateway
                   // simply keeps sending PCM and an older board -- which sends
                   // no list at all -- also keeps getting PCM. Neither side has
                   // to be upgraded in step with the other.
                   ",\"codecs\":[\"opus\",\"pcm\"]" +
                   health;
    }
    if (extra_json && *extra_json) {
        message += ",";
        message += extra_json;
    }
    message += "}";
    return message;
}

}  // namespace

// Enqueue and return. This is called from LVGL touch callbacks, the motion
// reaction task, the OTA task and the log tap; not one of them may wait on the
// websocket, and "tap or hold hangs" was exactly that wait.
esp_err_t gateway_client_send_event(const char *type, const char *extra_json) {
    if (!g_connected || !g_client || !type) return ESP_ERR_INVALID_STATE;
    if (!enqueue(tx_classify(type), build_event(type, extra_json))) return ESP_FAIL;
    if (g_tx_task) xTaskNotifyGive(g_tx_task);
    return ESP_OK;
}

// Called by the tx task before every microphone frame, so a button press
// overtakes a backlog of audio rather than queueing behind it. Returns true if
// it sent something, so the caller can keep draining control before audio.
bool gateway_client_flush_events() {
    TxEvent event{};
    for (QueueHandle_t queue : {g_tx_urgent, g_tx_normal, g_tx_bulk}) {
        if (queue && xQueueReceive(queue, &event, 0) == pdTRUE) {
            transmit_text(event.json, event.len);
            return true;
        }
    }
    return false;
}

void gateway_client_set_tx_task(TaskHandle_t task) { g_tx_task = task; }

// The slowest of the last few sends, in milliseconds. app_main's uplink
// governor reads this: a send that took far longer than the audio it carried is
// a session about to be lost, and the frames after it are what push it over.
uint32_t gateway_client_worst_send_ms() {
    uint32_t worst = 0;
    for (size_t i = 0; i < kSendSamples; ++i) worst = std::max(worst, g_send_us[i].load());
    return worst / 1000;
}

uint32_t gateway_client_tx_dropped() { return g_tx_dropped.load(); }
uint32_t gateway_client_rx_dropped() { return g_rx_dropped.load(); }

uint32_t gateway_client_opus_decode_us_max() { return g_opus_decode_us_max.load(); }
uint32_t gateway_client_opus_frames() { return g_opus_frames.load(); }
// Average decoded PCM bytes per frame. 20 ms of 16 kHz mono is 640; anything
// short of that is the decoder producing less audio than the wire promised.
uint32_t gateway_client_opus_pcm_per_frame() {
    const uint32_t frames = g_opus_frames.load();
    return frames ? g_opus_pcm_bytes.load() / frames : 0;
}

size_t gateway_client_rx_stack_free() {
    // Smallest headroom this task has ever had, in bytes. A decoder that
    // outgrows its stack corrupts memory and reboots the board with no useful
    // message on a link where serial is not attached; this is the number that
    // says how close it came.
    return g_rx_task ? uxTaskGetStackHighWaterMark(g_rx_task) * sizeof(StackType_t) : 0;
}

}  // namespace kiki
