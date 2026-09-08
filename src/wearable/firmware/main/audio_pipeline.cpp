#include "audio_pipeline.hpp"

#include "gateway_client.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "bsp/esp-bsp.h"
#include "driver/i2s_std.h"
#include "driver/i2s_tdm.h"
#include "esp_codec_dev.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "protocol.hpp"

#if CONFIG_KIKI_AEC_ENABLED
#include "esp_aec.h"
#include "esp_vad.h"
#endif

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_audio";
constexpr uint32_t kRate = 48000;
constexpr size_t kFrameSamples = 480;
constexpr size_t kTdmSlots = 4;
// 768 KiB is 8 seconds of 48 kHz mono. It has to hold whatever lead the
// gateway is running (2.5 s of speech, 4 s of music, and a ducked reply over a
// song is both at once) or the surplus is thrown away on arrival -- which is
// not a gap in the audio, it is missing words. Measured on an UNSHAPED LAN link
// before this change: 139 KB discarded across six replies, 1.45 s of speech the
// listener never heard. It costs nothing scarce: the ring lives in PSRAM, of
// which this board has about 8 MB free.
constexpr size_t kPlaybackRingBytes = 768 * 1024;
constexpr uint32_t kMicTaskStackBytes = 12 * 1024;

// Mono s16 at 48 kHz: one millisecond of audio is exactly 96 bytes.
constexpr size_t kBytesPerMs = (kRate * sizeof(int16_t)) / 1000;

// The jitter buffer we try to hold before feeding the codec, and how long we
// will wait to get it. On a healthy LAN the opening frames of a reply arrive
// back-to-back, so 100 ms of depth is reached almost immediately and the wait
// bound is what stops a slow first frame from being charged to
// time-to-first-word.
//
// Over a phone hotspot and a public tunnel the audio arrives in bursts with
// gaps between them, and a 40 ms gate meant playback started on almost nothing
// and then ran dry: measured 22 underruns and 1.77 s of silence across four
// replies, while buffered_ms swung between 1200 and 0. Time-to-first-word was
// fine; she just stuttered. 100 ms is what fixed that, and 100 ms is where it
// stays.
//
// THE START GATE, and nothing else. playback_task will not begin until this
// much audio is buffered, so every millisecond of it is a millisecond of
// time-to-first-word -- and it used to be adaptive, climbing 100 -> 340 ms
// after stuttered replies. Measured on the bench: six turns on an unshaped link
// walked it from 100 ms to 300 ms, i.e. the punishment for a bad link was a
// slower Kiki, permanently, for the rest of the session.
//
// It is now fixed. Jitter tolerance did not disappear; it moved to the
// gateway's send lead (request_lead below), which buys exactly the same cushion
// and costs nothing, because pacing only ever delays frames after the first.
constexpr uint32_t kPrebufferFloorMs = 100;
// Enough to ride out tunnel jitter, and still under a quarter second added to
// a reply that already takes ~930 ms to start.
// How much cushion to ask the gateway for, and how fast to ask. These are
// seconds of *its* send lead, not milliseconds of our start gate.
constexpr float kLeadStepSeconds = 0.75f;
constexpr float kLeadCeilingSeconds = 5.0f;

// esp_codec_dev_write returns once the I2S DMA has accepted the samples, not
// once the speaker has produced them. Declaring the turn drained at that moment
// re-opens the microphone while the last syllable is still in the air, which
// self-triggers the wake word. Hold the session open for one DMA tail.
constexpr int64_t kPlaybackTailUs = 120 * 1000;

// Backstop for a stream whose `audio_end` never arrives (gateway crash, dropped
// socket). Without it the microphone would stay gated forever and Kiki would go
// permanently deaf.
constexpr int64_t kStreamStallTimeoutUs = 3000 * 1000;

// One codec write. 10 ms keeps the loop responsive to aborts without making the
// per-write overhead significant.
constexpr size_t kWriteChunkSamples = 480;
constexpr size_t kWriteChunkBytes = kWriteChunkSamples * sizeof(int16_t);
// The TX engine owns six 240-frame DMA descriptors: 30 ms at 48 kHz. On an
// abort, push slightly more silence while the analog output is muted so even a
// descriptor which was already handed to I2S cannot reappear at the start of
// the next reply.
constexpr size_t kAbortFlushSamples = 40 * (kRate / 1000);
// Those same six descriptors are why the dance clock subtracts a tail: a write
// that has returned is 30 ms away from being audible, and a choreography
// anchored to the write position lands 30 ms early on every single beat.
constexpr uint64_t kCodecTailSamples = 30 * (kRate / 1000);

esp_codec_dev_handle_t g_speaker = nullptr;
esp_codec_dev_handle_t g_microphone = nullptr;
RingbufHandle_t g_playback = nullptr;
// FreeRTOS byte ringbuffers permit only one outstanding receive. Playback is
// therefore the sole consumer, including cancellation drains. The gateway
// task requests an abort and waits on this bounded acknowledgement instead of
// ever receiving from the ring itself.
std::atomic<bool> g_abort_requested{false};
SemaphoreHandle_t g_abort_complete = nullptr;
TaskHandle_t g_playback_task = nullptr;
MicFrameCallback g_callback = nullptr;
PlaybackDrainedCallback g_drained_callback = nullptr;

// A playback session is live from the first queued byte until a real drain.
std::atomic<bool> g_session_active{false};
// Set once the prebuffer gate has cleared for the current session.
std::atomic<bool> g_started{false};
std::atomic<bool> g_end_requested{false};
std::atomic<int64_t> g_session_started_us{0};
std::atomic<uint32_t> g_underruns{0};
std::atomic<uint32_t> g_underrun_ms{0};

// How much audio to hold before starting playback, in milliseconds. Raised when
// a reply stutters and lowered again when replies come through clean, so the
// board pays for jitter only on the link that actually has it: on the LAN this
// sits at the floor, and moving to a hotspot or the public tunnel walks it up
// within a turn or two without anyone configuring anything.
std::atomic<uint32_t> g_prebuffer_ms{kPrebufferFloorMs};
// Underrun count at the start of the current reply, so the adjustment at the
// end of it can tell whether *this* reply stuttered.
std::atomic<uint32_t> g_underruns_at_session_start{0};
// The dance clock. Two counters over the same byte stream: what has been
// handed to the ring, and what has actually left it for the codec. Their
// difference is exactly the audio still in flight, so marking `queued` when a
// song's first frame arrives and reading `played` later gives the song's
// current position to the sample -- which is the only clock a dance can be
// locked to. Wall time is not: it does not know about the prebuffer gate, the
// jitter buffer, or a network stall.
//
// Underrun silence is deliberately NOT counted. It is time the speaker spent
// waiting, not music it played, and counting it would walk the choreography
// off the beat by exactly the length of the stall.
std::atomic<uint64_t> g_queued_samples{0};
std::atomic<uint64_t> g_played_samples{0};
std::atomic<uint32_t> g_dropped_bytes{0};
std::atomic<uint32_t> g_forced_ends{0};
std::atomic<uint8_t> g_local_effect_pending{0};
std::atomic<bool> g_local_effect_active{false};
std::atomic<int> g_output_volume{CONFIG_KIKI_AUDIO_VOLUME};
std::atomic<uint32_t> g_mic_sequence{0};

#if CONFIG_KIKI_AEC_ENABLED
// ESP-SR accepts 16 kHz frames. The codecs stay at 48 kHz so music keeps its
// bandwidth; the mic and ES7210 analog playback reference are synchronously
// decimated here before entering direct AEC as M,R. The reference is captured
// by the same ADC and clocks as the
// microphones, avoiding the drift and unknown codec latency of a software
// playback copy. We intentionally use the direct AEC API: the threaded AFE
// wrapper overran its feed ring on this complete display/audio application and
// delivered only 0.7x real time. If direct processing ever misses its own
// deadline, the bounded queue disables AEC and restores raw listening rather
// than leaving Kiki deaf.
constexpr uint32_t kAecRate = 16000;
constexpr size_t kAecChannels = 2;
constexpr size_t kAecMaxChunk = 512;
constexpr UBaseType_t kAecQueueDepth = 4;
constexpr size_t kDecimTaps = 63;
constexpr std::array<float, kDecimTaps> kDecimKernel = {
    -0.0006643934F, 0.0000000000F, 0.0007938078F, 0.0010927515F, 0.0004271493F,
    -0.0009913698F, -0.0020678352F, -0.0014883835F, 0.0009537735F, 0.0035552328F,
    0.0036371479F, -0.0000000000F, -0.0051522953F, -0.0071409385F, -0.0027207442F,
    0.0060405670F, 0.0119505201F, 0.0081454566F, -0.0049569971F, -0.0176487451F,
    -0.0173799432F, 0.0000000000F, 0.0235016540F, 0.0324189109F, 0.0124893404F,
    -0.0286046813F, -0.0599669856F, -0.0450192582F, 0.0320878499F, 0.1499018245F,
    0.2568448306F, 0.2999235078F, 0.2568448306F, 0.1499018245F, 0.0320878499F,
    -0.0450192582F, -0.0599669856F, -0.0286046813F, 0.0124893404F, 0.0324189109F,
    0.0235016540F, 0.0000000000F, -0.0173799432F, -0.0176487451F, -0.0049569971F,
    0.0081454566F, 0.0119505201F, 0.0060405670F, -0.0027207442F, -0.0071409385F,
    -0.0051522953F, -0.0000000000F, 0.0036371479F, 0.0035552328F, 0.0009537735F,
    -0.0014883835F, -0.0020678352F, -0.0009913698F, 0.0004271493F, 0.0010927515F,
    0.0007938078F, 0.0000000000F, -0.0006643934F,
};

struct AecPacket {
    int16_t mic[kAecMaxChunk];
    int16_t reference[kAecMaxChunk];
};

aec_handle_t *g_aec = nullptr;
vad_handle_t g_aec_vad = nullptr;
QueueHandle_t g_aec_queue = nullptr;
size_t g_aec_chunk = 0;
size_t g_aec_fill = 0;
std::array<std::array<float, kDecimTaps - 1>, kAecChannels> g_decim_history{};
std::atomic<bool> g_aec_enabled{false};
std::atomic<uint32_t> g_aec_frames{0};
std::atomic<uint32_t> g_aec_vad_frames{0};
std::atomic<uint32_t> g_aec_feed_failures{0};
std::atomic<uint32_t> g_aec_max_process_us{0};
// Recovery state. Losing AEC used to be permanent for the boot, and because
// the gateway only accepts a barge-in from a frame the AEC has processed, that
// silently cost voice barge-in until someone power-cycled the board. Measured
// on 2026-08-17: twenty seconds of shaking fired ~30 motion reactions, each
// drawing a face and playing a local sound, which starved this task past its
// four-frame queue three times running -- and barge-in stayed dead for the
// hour that followed, with no symptom except that interrupting her did
// nothing.
std::atomic<bool> g_aec_recoverable{false};
std::atomic<uint32_t> g_aec_rearms{0};
std::atomic<int64_t> g_aec_stalled_at_us{0};
// Doubles with every stall so a genuinely overloaded board settles into raw
// listening instead of flapping, and halves nothing -- a boot is the reset.
uint32_t g_aec_quiet_ms = 0;
constexpr uint32_t kAecRearmQuietMsMin = 5000;
constexpr uint32_t kAecRearmQuietMsMax = 120000;
uint32_t g_aec_consecutive_overruns = 0;

int16_t clamp_s16(float value) {
    const int32_t rounded = static_cast<int32_t>(value >= 0.0F ? value + 0.5F : value - 0.5F);
    return static_cast<int16_t>(
        std::clamp(rounded, static_cast<int32_t>(-32768), static_cast<int32_t>(32767)));
}

// Returns 160 interleaved M,R frames for each 480-frame TDM read.
size_t decimate_aec_input(const int16_t *tdm, size_t frames, int16_t *out) {
    if (!tdm || !out || frames != kFrameSamples) return 0;
    static std::array<std::array<float, kDecimTaps - 1 + kFrameSamples>,
                      kAecChannels> work{};
    constexpr size_t slots[kAecChannels] = {
        BSP_AUDIO_TDM_SLOT_FL, BSP_AUDIO_TDM_SLOT_RE,
    };
    for (size_t channel = 0; channel < kAecChannels; ++channel) {
        std::copy(g_decim_history[channel].begin(), g_decim_history[channel].end(),
                  work[channel].begin());
        for (size_t i = 0; i < frames; ++i) {
            work[channel][kDecimTaps - 1 + i] =
                static_cast<float>(tdm[i * kTdmSlots + slots[channel]]);
        }
    }

    size_t produced = 0;
    for (size_t j = 0; j < frames; j += 3) {
        for (size_t channel = 0; channel < kAecChannels; ++channel) {
            float acc = 0.0F;
            for (size_t tap = 0; tap < kDecimTaps; ++tap) {
                acc += kDecimKernel[tap] * work[channel][j + tap];
            }
            out[produced * kAecChannels + channel] = clamp_s16(acc);
        }
        ++produced;
    }

    for (size_t channel = 0; channel < kAecChannels; ++channel) {
        std::copy(work[channel].end() - (kDecimTaps - 1), work[channel].end(),
                  g_decim_history[channel].begin());
    }
    return produced;
}

void record_aec_process_time(uint32_t elapsed_us) {
    uint32_t previous = g_aec_max_process_us.load();
    while (elapsed_us > previous &&
           !g_aec_max_process_us.compare_exchange_weak(previous, elapsed_us)) {}
}

void aec_process_task(void *) {
    AecPacket packet{};
    auto *output = static_cast<int16_t *>(heap_caps_aligned_alloc(
        16, kAecMaxChunk * sizeof(int16_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    if (!output) {
        ESP_LOGE(kTag, "AEC output allocation failed; restoring raw microphone");
        g_aec_enabled = false;
        vTaskDelete(nullptr);
        return;
    }
    while (true) {
        if (xQueueReceive(g_aec_queue, &packet, portMAX_DELAY) != pdTRUE) continue;
        if (!g_aec_enabled.load()) continue;
        const int64_t started_us = esp_timer_get_time();
        aec_process(g_aec, packet.mic, packet.reference, output);
        record_aec_process_time(static_cast<uint32_t>(esp_timer_get_time() - started_us));
        uint8_t flags = AudioFlags::AecProcessed;
        // ESP WebRTC VAD accepts 30 ms frames. The AEC chunk is 32 ms on S3;
        // vad_process reads the first 480 samples and the gateway performs its
        // own independent decision over the full 512.
        if (vad_process_with_trigger(g_aec_vad, output) == VAD_SPEECH) {
            flags |= AudioFlags::DeviceVadSpeech;
            g_aec_vad_frames.fetch_add(1);
        }
        g_aec_frames.fetch_add(1);
        if (g_callback) {
            g_callback(output, g_aec_chunk, kAecRate, flags,
                       g_mic_sequence.fetch_add(1), esp_timer_get_time());
        }
    }
}

bool start_aec() {
    g_aec = aec_create(kAecRate, 4, 1, AEC_MODE_FD_HIGH_PERF);
    if (!g_aec) return false;
    aec_set_nlp_level(g_aec, AEC_NLP_LEVEL_AGGR);
    g_aec_chunk = static_cast<size_t>(aec_get_chunksize(g_aec));
    if (g_aec_chunk == 0 || g_aec_chunk > kAecMaxChunk) {
        ESP_LOGE(kTag, "direct AEC chunk invalid: %u", static_cast<unsigned>(g_aec_chunk));
        aec_destroy(g_aec);
        g_aec = nullptr;
        return false;
    }
    g_aec_vad = vad_create_with_param(VAD_MODE_3, kAecRate, 30, 150, 180);
    g_aec_queue = xQueueCreateWithCaps(kAecQueueDepth, sizeof(AecPacket),
                                       MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!g_aec_vad || !g_aec_queue) {
        if (g_aec_vad) vad_destroy(g_aec_vad);
        if (g_aec_queue) vQueueDeleteWithCaps(g_aec_queue);
        aec_destroy(g_aec);
        g_aec_vad = nullptr;
        g_aec_queue = nullptr;
        g_aec = nullptr;
        return false;
    }
    if (xTaskCreatePinnedToCore(aec_process_task, "kiki_aec", 6144, nullptr, 17,
                                nullptr, 1) != pdPASS) {
        vQueueDeleteWithCaps(g_aec_queue);
        vad_destroy(g_aec_vad);
        aec_destroy(g_aec);
        g_aec_queue = nullptr;
        g_aec_vad = nullptr;
        g_aec = nullptr;
        return false;
    }
    g_aec_enabled = true;
    ESP_LOGI(kTag, "direct full-duplex AEC ready: %u samples, FD high-perf",
             static_cast<unsigned>(g_aec_chunk));
    return true;
}

// Step back to raw listening, and remember enough to step forward again.
void stall_aec() {
    g_aec_enabled = false;
    g_aec_recoverable = true;
    g_aec_stalled_at_us = esp_timer_get_time();
    g_aec_quiet_ms = g_aec_quiet_ms == 0
                         ? kAecRearmQuietMsMin
                         : std::min(g_aec_quiet_ms * 2, kAecRearmQuietMsMax);
    ESP_LOGE(kTag, "direct AEC missed real time; raw microphone for %ums",
             static_cast<unsigned>(g_aec_quiet_ms));
}

// Called from the microphone task on every frame that finds AEC off.
//
// Two conditions, and the second one is the load-bearing one. Waiting out the
// quiet interval is only about not thrashing; waiting for *silence* is about
// correctness. The gateway trusts the AEC-processed flag as its evidence that
// a frame cannot contain Kiki's own voice, so re-arming in the middle of a
// reply would hand it output from a filter that has not converged on the echo
// yet -- and the most likely thing it would then hear is Kiki interrupting
// herself. Re-arming only while nothing is playing makes the restart identical
// to the one at boot, which is the state this whole path is known good in.
void maybe_rearm_aec() {
    if (!g_aec_recoverable.load() || !g_aec_queue) return;
    if (audio_pipeline_is_playing()) return;
    const int64_t quiet_us = static_cast<int64_t>(g_aec_quiet_ms) * 1000;
    if (esp_timer_get_time() - g_aec_stalled_at_us.load() < quiet_us) return;

    // Flush before enabling, never after: the queue can still hold packets
    // from the moment of the stall, and processing seconds-old audio into the
    // barge-in detector is exactly the kind of thing that reads as a phantom
    // interruption. The decimator history goes with it for the same reason.
    xQueueReset(g_aec_queue);
    g_aec_fill = 0;
    g_aec_consecutive_overruns = 0;
    for (auto &channel : g_decim_history) channel.fill(0.0F);
    g_aec_rearms.fetch_add(1);
    g_aec_enabled = true;
    ESP_LOGW(kTag, "direct AEC re-armed after %ums quiet (attempt %u)",
             static_cast<unsigned>(g_aec_quiet_ms),
             static_cast<unsigned>(g_aec_rearms.load()));
}

void feed_aec(const int16_t *tdm, size_t frames) {
    static std::array<int16_t, (kFrameSamples / 3) * kAecChannels> narrow{};
    static AecPacket packet{};
    const size_t produced = decimate_aec_input(tdm, frames, narrow.data());
    size_t offset = 0;
    while (offset < produced) {
        const size_t take = std::min(produced - offset, g_aec_chunk - g_aec_fill);
        for (size_t i = 0; i < take; ++i) {
            packet.mic[g_aec_fill + i] = narrow[(offset + i) * kAecChannels];
            packet.reference[g_aec_fill + i] =
                narrow[(offset + i) * kAecChannels + 1];
        }
        g_aec_fill += take;
        offset += take;
        if (g_aec_fill == g_aec_chunk) {
            if (xQueueSend(g_aec_queue, &packet, 0) != pdTRUE) {
                g_aec_feed_failures.fetch_add(1);
                if (++g_aec_consecutive_overruns >= 3) {
                    stall_aec();
                }
            } else {
                g_aec_consecutive_overruns = 0;
            }
            g_aec_fill = 0;
        }
    }
}
#endif

constexpr float kTau = 6.28318530717958647692f;

size_t buffered_bytes() {
    if (!g_playback) return 0;
    const size_t free_bytes = xRingbufferGetCurFreeSize(g_playback);
    return free_bytes >= kPlaybackRingBytes ? 0 : kPlaybackRingBytes - free_bytes;
}

esp_err_t configure_codecs() {
    i2s_std_config_t tx = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kRate),
        .slot_cfg = I2S_STD_PHILIP_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = BSP_I2S_MCLK, .bclk = BSP_I2S_SCLK, .ws = BSP_I2S_LCLK,
            .dout = BSP_I2S_DOUT, .din = I2S_GPIO_UNUSED,
            .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
        },
    };
    i2s_tdm_config_t rx = {
        .clk_cfg = I2S_TDM_CLK_DEFAULT_CONFIG(kRate),
        .slot_cfg = I2S_TDM_PHILIP_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO,
            static_cast<i2s_tdm_slot_mask_t>(
                I2S_TDM_SLOT0 | I2S_TDM_SLOT1 | I2S_TDM_SLOT2 | I2S_TDM_SLOT3)),
        .gpio_cfg = {
            .mclk = BSP_I2S_MCLK, .bclk = BSP_I2S_SCLK, .ws = BSP_I2S_LCLK,
            .dout = I2S_GPIO_UNUSED, .din = BSP_I2S_DSIN,
            .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
        },
    };
    tx.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    rx.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    rx.clk_cfg.bclk_div = 8;
    rx.slot_cfg.total_slot = 4;
    ESP_RETURN_ON_ERROR(bsp_audio_init_tx_std_rx_tdm(&tx, &rx), kTag, "audio bus");

    g_speaker = bsp_audio_codec_speaker_init();
    g_microphone = bsp_audio_codec_microphone_init();
    if (!g_speaker || !g_microphone) return ESP_FAIL;

    esp_codec_dev_sample_info_t output = {
        .bits_per_sample = 16,
        .channel = 2,
        .channel_mask = 0,
        .sample_rate = kRate,
        .mclk_multiple = 256,
    };
    esp_codec_dev_sample_info_t input = {
        .bits_per_sample = 16,
        .channel = 4,
        .channel_mask = BSP_AUDIO_TDM_SLOT_MASK_FL | BSP_AUDIO_TDM_SLOT_MASK_RE |
                        BSP_AUDIO_TDM_SLOT_MASK_FR | BSP_AUDIO_TDM_SLOT_MASK_NA,
        .sample_rate = kRate,
        .mclk_multiple = 256,
    };
    ESP_RETURN_ON_FALSE(esp_codec_dev_open(g_speaker, &output) == 0, ESP_FAIL, kTag, "speaker open");
    ESP_RETURN_ON_FALSE(esp_codec_dev_open(g_microphone, &input) == 0, ESP_FAIL, kTag, "mic open");
    ESP_RETURN_ON_FALSE(
        esp_codec_dev_set_in_channel_gain(g_microphone,
                                          BSP_AUDIO_ES7210_CONNECTED_MIC_MASK, 24.0f) == 0,
        ESP_FAIL, kTag, "mic gain");
    esp_codec_dev_set_out_mute(g_speaker, false);
    esp_codec_dev_set_out_vol(g_speaker, CONFIG_KIKI_AUDIO_VOLUME);
    return ESP_OK;
}

void mic_task(void *) {
    static std::array<int16_t, kFrameSamples * kTdmSlots> tdm{};
    static std::array<int16_t, kFrameSamples> mono{};
    while (true) {
        if (esp_codec_dev_read(g_microphone, tdm.data(), tdm.size() * sizeof(int16_t)) != ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
#if CONFIG_KIKI_AEC_ENABLED
        if (!g_aec_enabled.load()) maybe_rearm_aec();
        if (g_aec_enabled.load()) {
            feed_aec(tdm.data(), kFrameSamples);
            continue;
        }
#endif
        for (size_t i = 0; i < kFrameSamples; ++i) mono[i] = tdm[i * kTdmSlots];
        if (g_callback) {
            g_callback(mono.data(), mono.size(), kRate, 0, g_mic_sequence++,
                       esp_timer_get_time());
        }
    }
}

size_t drain_ring() {
    // Called only by playback_task. See the single-consumer invariant above.
    if (!g_playback) return 0;
    size_t bytes = 0;
    size_t drained = 0;
    while (void *item = xRingbufferReceive(g_playback, &bytes, 0)) {
        drained += bytes;
        vRingbufferReturnItem(g_playback, item);
    }
    return drained;
}

// A reply that stuttered means the cushion was too thin. Ask the gateway to
// send further ahead rather than making the NEXT reply start later: its send
// lead buys the same jitter tolerance and costs the listener nothing, because
// pacing only ever delays frames after the first. The old version raised this
// board's own start gate, which is why a bad link used to make Kiki
// permanently slower to answer as well as choppy.
float g_requested_lead_s = 0.0f;

void request_more_lead() {
    const uint32_t stuttered = g_underruns.load() - g_underruns_at_session_start.load();
    if (stuttered == 0) return;
    if (g_requested_lead_s >= kLeadCeilingSeconds) return;
    g_requested_lead_s = std::min(g_requested_lead_s + kLeadStepSeconds, kLeadCeilingSeconds);
    char payload[48];
    std::snprintf(payload, sizeof(payload), "\"seconds\":%.2f",
                  static_cast<double>(g_requested_lead_s));
    ESP_LOGW(kTag, "reply stuttered (%u underruns); asking for %.1fs of send lead",
             static_cast<unsigned>(stuttered), static_cast<double>(g_requested_lead_s));
    // Queued, never sent from here: this runs on the playback task, which owes
    // the codec a sample every 10 ms and may not wait on a socket.
    gateway_client_send_event("request_lead", payload);
}

void end_session() {
    // Cancellation and the natural drain can meet on different tasks. Only
    // the winner retunes the adaptive buffer; the remaining state reset is
    // deliberately idempotent.
    const bool was_active = g_session_active.exchange(false);
    if (was_active) request_more_lead();
    g_started = false;
    g_end_requested = false;
}

// Writes one mono chunk to the codec as duplicated stereo. The scratch buffer is
// static because this runs at priority 19: a heap allocation per chunk was the
// original source of playback jitter.
void write_mono(const int16_t *samples, size_t count) {
    static int16_t stereo[kWriteChunkSamples * 2];
    while (count > 0) {
        const size_t n = std::min(count, kWriteChunkSamples);
        for (size_t i = 0; i < n; ++i) stereo[i * 2] = stereo[i * 2 + 1] = samples[i];
        esp_codec_dev_write(g_speaker, stereo, n * 2 * sizeof(int16_t));
        samples += n;
        count -= n;
    }
}

void write_silence(size_t samples) {
    static int16_t silence[kWriteChunkSamples * 2] = {};
    while (samples > 0) {
        const size_t n = std::min(samples, kWriteChunkSamples);
        esp_codec_dev_write(g_speaker, silence, n * 2 * sizeof(int16_t));
        samples -= n;
    }
}

uint32_t effect_duration_ms(LocalEffect effect) {
    switch (effect) {
        case LocalEffect::EyeLeft: return 260;
        case LocalEffect::EyeRight: return 300;
        case LocalEffect::Shell: return 180;
        case LocalEffect::Head: return 320;
        case LocalEffect::LeftClaw: return 220;
        case LocalEffect::RightClaw: return 360;
        case LocalEffect::Legs: return 380;
        case LocalEffect::Pickup: return 230;
        case LocalEffect::Alert: return 190;
        case LocalEffect::Wiggle: return 280;
        case LocalEffect::Annoyed: return 390;
        case LocalEffect::Dizzy: return 430;
        case LocalEffect::Rocking: return 310;
        case LocalEffect::Spin: return 410;
        case LocalEffect::Carried: return 220;
        case LocalEffect::UpsideDown: return 300;
        case LocalEffect::Bump: return 150;
        case LocalEffect::Freefall: return 250;
        case LocalEffect::Landing: return 320;
        case LocalEffect::Settled: return 210;
        case LocalEffect::Bored: return 460;
        case LocalEffect::Sleepy: return 390;
        case LocalEffect::FaceDown: return 280;
        case LocalEffect::Charging: return 320;
        case LocalEffect::Tired: return 350;
        case LocalEffect::Dance: return 230;
        case LocalEffect::FallAlarm: return 3000;
    }
    return 200;
}

float bell(float t, float start, float frequency, float decay) {
    const float age = t - start;
    if (age < 0.0f || age >= decay) return 0.0f;
    const float env = 1.0f - age / decay;
    return std::sin(kTau * frequency * age) * env * env;
}

float local_effect_sample(LocalEffect effect, float t, float duration, uint32_t &noise) {
    noise = noise * 1664525u + 1013904223u;
    const float white = (static_cast<int32_t>(noise >> 9) / 4194304.0f) - 1.0f;
    const float p = std::min(1.0f, t / duration);
    const float fade = std::min(1.0f, t / 0.006f) * (1.0f - p);
    switch (effect) {
        case LocalEffect::EyeLeft: {
            // A tiny descending cartoon yelp: unmistakably "ow", never shrill.
            const float f = 1250.0f - 720.0f * p;
            const float wobble = 1.0f + 0.12f * std::sin(kTau * 18.0f * t);
            return 0.31f * fade * std::sin(kTau * f * wobble * t);
        }
        case LocalEffect::EyeRight: {
            // The other eye answers differently: two startled "oi!" chirps.
            const float pulse = (std::fmod(t, 0.14f) < 0.075f) ? 1.0f : 0.0f;
            const float f = 780.0f + 330.0f * (1.0f - std::fmod(t, 0.14f) / 0.14f);
            return 0.29f * fade * pulse * std::sin(kTau * f * t);
        }
        case LocalEffect::Shell: {
            // A rubbery upward boop.
            const float f = 430.0f + 1250.0f * p * p;
            return 0.30f * fade * std::sin(kTau * f * t);
        }
        case LocalEffect::Head:
            // Three soft, affectionate pixel-bells.
            return 0.18f * (bell(t, 0.00f, 880.0f, 0.16f) +
                            bell(t, 0.075f, 1175.0f, 0.16f) +
                            bell(t, 0.150f, 1480.0f, 0.17f));
        case LocalEffect::LeftClaw: {
            // Two dry claw-snips with a small metallic ring.
            const float phase = std::fmod(t, 0.105f);
            const float click = phase < 0.018f ? (1.0f - phase / 0.018f) : 0.0f;
            return 0.20f * click * white + 0.13f * fade * std::sin(kTau * 1850.0f * t);
        }
        case LocalEffect::RightClaw: {
            // Kiki's absurdly tiny ray gun: laser sweep plus electronic fizz.
            const float f = 2100.0f - 1750.0f * p;
            const float laser = std::sin(kTau * f * t);
            return fade * (0.24f * laser + 0.055f * white * (1.0f - p));
        }
        case LocalEffect::Legs:
            // Three progressively lower cartoon impacts as the crab tumbles.
            return 0.24f * (bell(t, 0.00f, 145.0f, 0.12f) +
                            bell(t, 0.105f, 105.0f, 0.14f) +
                            bell(t, 0.230f, 72.0f, 0.15f)) + 0.035f * white * fade;
        case LocalEffect::Pickup:
            return 0.21F * (bell(t, 0.00F, 520.0F, 0.14F) +
                            bell(t, 0.075F, 910.0F, 0.16F));
        case LocalEffect::Alert:
            return 0.24F * bell(t, 0.00F, 1320.0F, 0.18F) +
                   0.10F * bell(t, 0.045F, 1760.0F, 0.13F);
        case LocalEffect::Wiggle: {
            const float f = 620.0F + 180.0F * std::sin(kTau * 11.0F * t);
            return 0.22F * fade * std::sin(kTau * f * t);
        }
        case LocalEffect::Annoyed: {
            const float f = 310.0F - 95.0F * p + 28.0F * std::sin(kTau * 17.0F * t);
            return fade * (0.19F * std::sin(kTau * f * t) + 0.045F * white);
        }
        case LocalEffect::Dizzy: {
            const float f = 480.0F + 210.0F * std::sin(kTau * 4.2F * t);
            return 0.20F * fade * std::sin(kTau * f * t);
        }
        case LocalEffect::Rocking:
            return 0.14F * (bell(t, 0.00F, 660.0F, 0.24F) +
                            bell(t, 0.145F, 825.0F, 0.17F));
        case LocalEffect::Spin: {
            const float f = 360.0F + 1100.0F * p;
            return 0.19F * fade * std::sin(kTau * f * t);
        }
        case LocalEffect::Carried:
            return 0.18F * (bell(t, 0.00F, 740.0F, 0.13F) +
                            bell(t, 0.080F, 980.0F, 0.13F));
        case LocalEffect::UpsideDown: {
            const float f = 980.0F - 560.0F * p;
            return 0.20F * fade * std::sin(kTau * f * t);
        }
        case LocalEffect::Bump:
            return 0.25F * bell(t, 0.00F, 105.0F, 0.14F) + 0.035F * white * fade;
        case LocalEffect::Freefall: {
            const float f = 510.0F + 980.0F * p * p;
            return 0.17F * fade * std::sin(kTau * f * t);
        }
        case LocalEffect::Landing:
            return 0.28F * bell(t, 0.00F, 82.0F, 0.20F) +
                   0.13F * bell(t, 0.120F, 330.0F, 0.18F) + 0.035F * white * fade;
        case LocalEffect::Settled:
            return 0.15F * (bell(t, 0.00F, 540.0F, 0.13F) +
                            bell(t, 0.070F, 680.0F, 0.13F));
        case LocalEffect::Bored: {
            const float f = 360.0F - 120.0F * p;
            return fade * (0.13F * std::sin(kTau * f * t) + 0.025F * white);
        }
        case LocalEffect::Sleepy:
            return 0.11F * bell(t, 0.00F, 420.0F, 0.30F) +
                   0.08F * bell(t, 0.165F, 315.0F, 0.20F);
        case LocalEffect::FaceDown:
            return fade * (0.12F * std::sin(kTau * 210.0F * t) + 0.065F * white);
        case LocalEffect::Charging:
            return 0.14F * (bell(t, 0.00F, 660.0F, 0.18F) +
                            bell(t, 0.095F, 990.0F, 0.19F) +
                            bell(t, 0.190F, 1320.0F, 0.13F));
        case LocalEffect::Tired: {
            const float f = 300.0F - 85.0F * p;
            return 0.11F * fade * std::sin(kTau * f * t);
        }
        case LocalEffect::Dance:
            return 0.16F * (bell(t, 0.00F, 880.0F, 0.10F) +
                            bell(t, 0.095F, 1175.0F, 0.11F));
        case LocalEffect::FallAlarm: {
            const float phase = std::fmod(t, 0.72F);
            const float f = phase < 0.36F ? 880.0F : 660.0F;
            const float pulse = std::fmod(t, 0.18F) < 0.14F ? 1.0F : 0.18F;
            return 0.68F * pulse * std::sin(kTau * f * t);
        }
    }
    return 0.0f;
}

void play_local_effect(LocalEffect effect) {
    const int restore_volume = g_output_volume.load();
    if (effect == LocalEffect::FallAlarm && g_speaker)
        esp_codec_dev_set_out_vol(g_speaker, 100);
    const uint32_t duration_ms = effect_duration_ms(effect);
    const size_t total = static_cast<size_t>(duration_ms) * (kRate / 1000);
    std::array<int16_t, kWriteChunkSamples> mono{};
    uint32_t noise = 0x4B494B49u ^ static_cast<uint8_t>(effect);
    size_t offset = 0;
    while (offset < total && !g_session_active.load() && !g_abort_requested.load()) {
        const size_t count = std::min(mono.size(), total - offset);
        for (size_t i = 0; i < count; ++i) {
            const float t = static_cast<float>(offset + i) / kRate;
            const float duration = static_cast<float>(duration_ms) / 1000.0f;
            const float sample = std::clamp(local_effect_sample(effect, t, duration, noise),
                                            -0.72f, 0.72f);
            mono[i] = static_cast<int16_t>(sample * 32767.0f);
        }
        write_mono(mono.data(), count);
        offset += count;
    }
    if (effect == LocalEffect::FallAlarm && g_speaker)
        esp_codec_dev_set_out_vol(g_speaker, restore_volume);
}

void playback_task(void *) {
    int64_t empty_since_us = 0;
    while (true) {
        if (g_abort_requested.exchange(false)) {
            // We are the sole ring consumer, so by the time we reach here any
            // block claimed before audio_stop has already been returned.
            const size_t drained = drain_ring();
            esp_codec_dev_set_out_mute(g_speaker, true);
            write_silence(kAbortFlushSamples);
            esp_codec_dev_set_out_mute(g_speaker, false);
            ESP_LOGI(kTag, "playback aborted; drained=%u bytes, TX tail flushed",
                     static_cast<unsigned>(drained));
            xSemaphoreGive(g_abort_complete);
            empty_since_us = 0;
            continue;
        }
        if (!g_session_active.load()) {
            empty_since_us = 0;
            const uint8_t requested = g_local_effect_pending.exchange(0);
            if (requested != 0) {
                g_local_effect_active = true;
                play_local_effect(static_cast<LocalEffect>(requested));
                g_local_effect_active = false;
                continue;
            }
            // audio_stop notifies this task so an idle pipeline acknowledges
            // immediately rather than waiting out the polling interval.
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(5));
            continue;
        }
        if (!g_started.load()) {
            const int64_t waited = esp_timer_get_time() - g_session_started_us.load();
            const uint32_t target_ms = g_prebuffer_ms.load();
            // The wait bound tracks the target: a target we refuse to wait for
            // is not a jitter buffer, which is exactly why 100 ms behind a
            // 40 ms gate never absorbed anything.
            if (buffered_bytes() >= target_ms * kBytesPerMs || g_end_requested.load() ||
                waited >= static_cast<int64_t>(target_ms) * 1000) {
                g_started = true;
            } else {
                vTaskDelay(pdMS_TO_TICKS(2));
                continue;
            }
        }

        size_t bytes = 0;
        auto *mono = static_cast<uint8_t *>(
            xRingbufferReceiveUpTo(g_playback, &bytes, pdMS_TO_TICKS(10), kWriteChunkBytes));
        if (mono) {
            empty_since_us = 0;
            const size_t samples = bytes / sizeof(int16_t);
            write_mono(reinterpret_cast<const int16_t *>(mono), samples);
            g_played_samples.fetch_add(samples);
            vRingbufferReturnItem(g_playback, mono);
            continue;
        }

        // Nothing buffered. Either the stream really finished, or the network
        // fell behind and we are underrunning.
        const int64_t now = esp_timer_get_time();
        if (g_end_requested.load()) {
            // Let the DMA tail actually reach the speaker before saying so.
            write_silence(kPlaybackTailUs / 1000 * (kRate / 1000));
            end_session();
            empty_since_us = 0;
            if (g_drained_callback) g_drained_callback();
            continue;
        }
        if (empty_since_us == 0) {
            empty_since_us = now;
            g_underruns.fetch_add(1);
            ESP_LOGW(kTag, "playback underrun (buffer empty mid-stream)");
        }
        const int64_t stalled_us = now - empty_since_us;
        if (stalled_us >= kStreamStallTimeoutUs) {
            ESP_LOGE(kTag, "no audio for %lld ms and no audio_end; ending stream",
                     static_cast<long long>(stalled_us / 1000));
            g_forced_ends.fetch_add(1);
            end_session();
            empty_since_us = 0;
            if (g_drained_callback) g_drained_callback();
            continue;
        }
        // Feed defined silence rather than letting the DMA run dry on stale
        // contents, and account for the gap.
        g_underrun_ms.fetch_add(10);
        write_silence(kWriteChunkSamples);
    }
}

}  // namespace

esp_err_t audio_pipeline_start(MicFrameCallback callback,
                               PlaybackDrainedCallback drained_callback) {
    g_callback = callback;
    g_drained_callback = drained_callback;
    ESP_RETURN_ON_ERROR(configure_codecs(), kTag, "codec setup");
    // The ring lives in PSRAM. It is 768 KiB against only ~166 KiB of internal
    // RAM on this chip, and it never needs to be DMA-capable: playback_task
    // copies out of it into a static scratch buffer before handing samples to
    // the codec. Keeping it internal starved the display's SPI driver of the
    // bounce buffer it needs to push a PSRAM draw buffer to the panel, which
    // showed up as a flood of "Failed to allocate priv TX buffer" and a dead
    // screen the moment the face started dirtying large regions.
    g_playback = xRingbufferCreateWithCaps(kPlaybackRingBytes, RINGBUF_TYPE_BYTEBUF,
                                           MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(g_playback, ESP_ERR_NO_MEM, kTag, "playback ring");
    g_abort_complete = xSemaphoreCreateBinary();
    ESP_RETURN_ON_FALSE(g_abort_complete, ESP_ERR_NO_MEM, kTag, "abort semaphore");
#if CONFIG_KIKI_AEC_ENABLED
    // Non-fatal by design. If the proprietary DSP cannot allocate or its
    // expected frame shape changes, raw listening still works and app_main's
    // playback gate remains closed, so failure cannot turn into self-barge-in.
    if (!start_aec()) {
        ESP_LOGE(kTag, "AEC unavailable; voice barge-in disabled (safe raw-mic fallback)");
    }
#endif
    // Audio lives on core 1 by itself. Core 0 carries the Wi-Fi stack and the
    // LVGL renderer, and once the face animates continuously that core has no
    // headroom to also guarantee a 10 ms codec deadline.
    // The 10 ms TDM and mono working buffers occupy 4.8 KiB by themselves.
    // Leave enough headroom for esp_codec_dev_read and the network callback.
    xTaskCreatePinnedToCore(mic_task, "kiki_mic", kMicTaskStackBytes, nullptr, 18,
                            nullptr, 1);
    xTaskCreatePinnedToCore(playback_task, "kiki_play", 6144, nullptr, 19,
                            &g_playback_task, 1);
    return ESP_OK;
}

void audio_pipeline_mark_playback_end() {
    if (!g_session_active.load()) {
        // `audio_end` for a stream that never delivered audio (empty reply, or
        // one that was cancelled before the first frame).
        if (g_drained_callback) g_drained_callback();
        return;
    }
    g_end_requested = true;
}

esp_err_t audio_pipeline_queue_playback(const uint8_t *pcm, size_t bytes) {
    if (!g_playback || !pcm || !bytes) return ESP_ERR_INVALID_ARG;
    // Never wait. With a 768 KiB ring a full buffer means a genuine overrun --
    // the gateway is further ahead than any lead it should be running -- and
    // blocking here stalls kiki_net_rx, which is the task draining the socket.
    // The 250 ms this used to wait was paid inside the websocket callback, with
    // the client mutex held, which stopped the board reading its own audio.
    if (xRingbufferSend(g_playback, pcm, bytes, 0) != pdTRUE) {
        g_dropped_bytes.fetch_add(bytes);
        return ESP_ERR_TIMEOUT;
    }
    g_queued_samples.fetch_add(bytes / sizeof(int16_t));
    if (!g_session_active.exchange(true)) {
        // A local cue that lost a race with network speech must not wait and
        // pop out after the reply has finished, detached from the gesture that
        // caused it.
        g_local_effect_pending = 0;
        g_started = false;
        g_end_requested = false;
        g_underruns_at_session_start = g_underruns.load();
        g_session_started_us = esp_timer_get_time();
    }
    return ESP_OK;
}

// Synchronous on purpose. An earlier version set an "abort" flag for the
// playback task to notice, which left a window where the next turn's first
// frames were queued before the flag was consumed and got drained with the
// cancelled ones.
void audio_pipeline_stop_playback() {
    // Mark inactive first, then make the sole ring consumer perform the drain.
    // Keeping the gateway task out of xRingbufferReceive is essential: byte
    // rings reject a second receive while playback owns a block, which was the
    // source of the old words surviving into the next response.
    end_session();
    g_local_effect_pending = 0;
    // Discard an old acknowledgement, then wake playback_task. A bounded wait
    // prevents a codec fault from wedging the WebSocket receive loop forever.
    xSemaphoreTake(g_abort_complete, 0);
    g_abort_requested = true;
    if (g_playback_task) xTaskNotifyGive(g_playback_task);
    if (xSemaphoreTake(g_abort_complete, pdMS_TO_TICKS(250)) != pdTRUE) {
        ESP_LOGE(kTag, "playback abort timed out; keeping new audio fail-closed");
    }
}

bool audio_pipeline_is_playing() {
    return g_session_active.load() || g_local_effect_active.load();
}

uint64_t audio_pipeline_queued_samples() { return g_queued_samples.load(); }

uint64_t audio_pipeline_played_samples() {
    const uint64_t played = g_played_samples.load();
    // What the codec has been handed is not yet what the ear has heard: the
    // TX DMA holds about 30 ms. Reporting the write position would put every
    // pose that fraction of a beat early, consistently, for the whole song.
    return played > kCodecTailSamples ? played - kCodecTailSamples : 0;
}

uint32_t audio_pipeline_prebuffer_ms() { return g_prebuffer_ms.load(); }

uint32_t audio_pipeline_buffered_ms() {
    return static_cast<uint32_t>(buffered_bytes() / kBytesPerMs);
}

void audio_pipeline_get_stats(PlaybackStats *out) {
    if (!out) return;
    out->underruns = g_underruns.load();
    out->underrun_ms = g_underrun_ms.load();
    out->dropped_bytes = g_dropped_bytes.load();
    out->forced_ends = g_forced_ends.load();
#if CONFIG_KIKI_AEC_ENABLED
    out->aec_enabled = g_aec_enabled.load();
    out->aec_frames = g_aec_frames.load();
    out->aec_vad_frames = g_aec_vad_frames.load();
    out->aec_feed_failures = g_aec_feed_failures.load();
    out->aec_max_process_us = g_aec_max_process_us.load();
    out->aec_rearms = g_aec_rearms.load();
#else
    out->aec_enabled = false;
    out->aec_frames = 0;
    out->aec_vad_frames = 0;
    out->aec_feed_failures = 0;
    out->aec_max_process_us = 0;
    out->aec_rearms = 0;
#endif
}

void audio_pipeline_set_volume(int percent) {
    const int bounded = std::clamp(percent, 0, 100);
    g_output_volume = bounded;
    if (g_speaker) esp_codec_dev_set_out_vol(g_speaker, bounded);
}

bool audio_pipeline_play_local_effect(LocalEffect effect) {
    if (g_session_active.load() || g_local_effect_active.load()) return false;
    g_local_effect_pending = static_cast<uint8_t>(effect);
    return true;
}

}  // namespace kiki
