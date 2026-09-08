#pragma once

#include <cstddef>
#include <cstdint>
#include "esp_err.h"

namespace kiki {

using MicFrameCallback = void (*)(const int16_t *samples, size_t count,
                                  uint32_t sample_rate, uint8_t flags,
                                  uint32_t sequence, int64_t timestamp_us);
using PlaybackDrainedCallback = void (*)();

// Immediate procedural cues for touch and motion. They are synthesized on the
// board so physical reactions never wait for a gateway round trip or consume
// TTS/LLM time.
enum class LocalEffect : uint8_t {
    EyeLeft = 1,
    EyeRight,
    Shell,
    Head,
    LeftClaw,
    RightClaw,
    Legs,
    Pickup,
    Alert,
    Wiggle,
    Annoyed,
    Dizzy,
    Rocking,
    Spin,
    Carried,
    UpsideDown,
    Bump,
    Freefall,
    Landing,
    Settled,
    Bored,
    Sleepy,
    FaceDown,
    Charging,
    Tired,
    Dance,
    FallAlarm,
};

// Counters behind the "did the audio break?" question. Every one of these is a
// distinct failure with a distinct fix, so they are never summed into one
// number: underruns mean the network could not keep the buffer fed, drops mean
// the gateway sent faster than real time, and forced_ends mean an `audio_end`
// never arrived at all.
struct PlaybackStats {
    uint32_t underruns;
    uint32_t underrun_ms;
    uint32_t dropped_bytes;
    uint32_t forced_ends;
    bool aec_enabled;
    uint32_t aec_frames;
    uint32_t aec_vad_frames;
    uint32_t aec_feed_failures;
    uint32_t aec_max_process_us;
    uint32_t aec_rearms;
};

esp_err_t audio_pipeline_start(MicFrameCallback callback, PlaybackDrainedCallback drained_callback);
esp_err_t audio_pipeline_queue_playback(const uint8_t *mono_pcm, size_t bytes);
void audio_pipeline_mark_playback_end();
void audio_pipeline_stop_playback();

// True for the whole span between the first queued byte and a real drain. It
// deliberately stays true across a mid-sentence underrun: the microphone is
// gated on this, and re-opening it because the jitter buffer momentarily ran
// dry is how Kiki ends up hearing her own speaker.
bool audio_pipeline_is_playing();

uint32_t audio_pipeline_buffered_ms();

// The dance clock (see kiki_dance.cpp). `queued` counts every sample handed to
// the playback ring; `played` counts what has left it for the codec, minus the
// DMA tail, so it is where the *speaker* is. Mark `queued` when a song's first
// frame arrives and the difference from `played` afterwards is that song's
// position, to the sample, through prebuffering, jitter and network stalls.
uint64_t audio_pipeline_queued_samples();
uint64_t audio_pipeline_played_samples();

// How much audio the pipeline currently insists on holding before it starts a
// reply. Self-tuning: at the floor on a good link, higher on one that stutters.
// Reported in device_stats so the adaptation is visible from the gateway.
uint32_t audio_pipeline_prebuffer_ms();
void audio_pipeline_get_stats(PlaybackStats *out);
void audio_pipeline_set_volume(int percent);

// Queues one short local effect. Returns false when Kiki is already speaking
// or playing media; touch animation still runs, but we never mix a cue over
// intelligible audio.
bool audio_pipeline_play_local_effect(LocalEffect effect);

}  // namespace kiki
