#include "protocol.hpp"

#include <cstdio>
#include <cstring>
#include <vector>

// The parser must accept every frame the gateway can legally send.
//
// It did not, and the failure was silent in a way that defeated four rounds of
// instrumentation. parse_audio_frame required an even-length payload -- true of
// every payload the protocol had ever carried, because they were all 16-bit PCM
// samples. Opus packets are arbitrary lengths: about half are odd, and a silent
// 20 ms frame is nine bytes. Those frames were rejected before reaching the
// decoder, so roughly half of every reply vanished.
//
// Nothing counted it. The frames never got as far as the decoder (zero decode
// errors) or the playback ring (zero dropped bytes), and the gateway's own
// accounting showed the audio going out ahead of schedule. The speaker starved
// and cracked while every statistic looked clean.
//
// Build and run on the host:
//   g++ -std=c++17 -I../main -o /tmp/test_protocol_frames test_protocol_frames.cpp
//   /tmp/test_protocol_frames

using kiki::AudioHeader;
using kiki::BinaryKind;

namespace {

int failures = 0;

void check(bool ok, const char *what) {
    if (!ok) {
        std::printf("FAIL %s\n", what);
        ++failures;
    }
}

// A frame exactly as the gateway encodes one: 24-byte header, then payload.
std::vector<uint8_t> frame(BinaryKind kind, size_t payload_bytes) {
    std::vector<uint8_t> data(sizeof(AudioHeader) + payload_bytes, 0);
    auto *header = reinterpret_cast<AudioHeader *>(data.data());
    std::memcpy(header->magic, kiki::kMagic, sizeof(kiki::kMagic));
    header->header_size = sizeof(AudioHeader);
    header->kind = static_cast<uint8_t>(kind);
    return data;
}

bool parses(const std::vector<uint8_t> &data) {
    const AudioHeader *header = nullptr;
    const uint8_t *payload = nullptr;
    size_t payload_size = 0;
    return kiki::parse_audio_frame(data.data(), data.size(), &header, &payload,
                                   &payload_size);
}

}  // namespace

int main() {
    // The actual bug. Every odd length must parse, at the sizes Opus really
    // produces: 9 bytes is a silent frame, ~40-80 is ordinary speech at 32 kbps.
    for (size_t bytes : {9u, 33u, 41u, 57u, 79u, 101u}) {
        check(parses(frame(BinaryKind::TtsOpus16k, bytes)),
              "an odd-length opus packet must parse");
    }
    for (size_t bytes : {10u, 40u, 80u}) {
        check(parses(frame(BinaryKind::TtsOpus16k, bytes)),
              "an even-length opus packet must parse");
    }

    // PCM keeps the rule that protects it: an odd length there is a truncated
    // frame, because those payloads are whole 16-bit samples.
    check(parses(frame(BinaryKind::TtsPcmS16Mono16k, 640)),
          "whole-sample PCM must parse");
    check(!parses(frame(BinaryKind::TtsPcmS16Mono16k, 641)),
          "odd-length PCM is truncated and must be rejected");
    check(!parses(frame(BinaryKind::MediaPcmS16Mono48k, 321)),
          "odd-length media PCM must be rejected");

    // And the guards that have nothing to do with codecs.
    std::vector<uint8_t> short_frame(sizeof(AudioHeader) - 1, 0);
    check(!parses(short_frame), "a frame shorter than the header must be rejected");
    auto bad_magic = frame(BinaryKind::TtsOpus16k, 41);
    bad_magic[0] = 'X';
    check(!parses(bad_magic), "a frame with the wrong magic must be rejected");

    if (failures == 0) {
        std::printf("all protocol frame checks passed\n");
        return 0;
    }
    std::printf("%d failure(s)\n", failures);
    return 1;
}
