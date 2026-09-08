#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

namespace kiki {

constexpr uint8_t kProtocolVersion = 1;
constexpr char kMagic[4] = {'K', 'K', 'A', '1'};

enum class BinaryKind : uint8_t {
    MicPcmS16Mono48k = 1,
    TtsPcmS16Mono48k = 2,
    MediaPcmS16Mono48k = 3,
    // The microphone at 16 kHz. ESP-SR AFE output uses this on every link;
    // legacy raw audio is also decimated to it on the remote link.
    MicPcmS16Mono16k = 4,
    // Kiki's voice at 16 kHz, sent instead of kind 2 on the remote link.
    TtsPcmS16Mono16k = 5,
    // Music at 16 kHz, sent instead of kind 3 on the remote link. Speech
    // dropped to 16 kHz long ago; media did not, so a song over a tunnel was
    // still asking for 768 kbps on a link measured at ~396 -- which is a
    // stutter, not a song.
    MediaPcmS16Mono16k = 6,
    // Kiki's voice as Opus, 16 kHz mono, one 20 ms packet per message. Sent
    // instead of kind 5 to a board that advertises "opus" in its hello.
    //
    // This is the codec that makes a weak link usable at all. 16 kHz PCM is
    // 256 kbps and has to arrive faster than it plays, continuously; at
    // -92 dBm it simply does not, and the reply that cannot fit also blocks
    // the keepalive PING behind it on the same TCP connection -- which is why
    // a link too slow to speak on was also a link that dropped every 80 s.
    // Opus carries the same speech in ~24 kbps.
    TtsOpus16k = 7,
};

// Microphone-frame flags. Barge-in is fail-closed: the gateway will never run
// speech detection over playback unless AecProcessed is present, and it also
// requires the board-side VAD vote before accepting its own Silero vote.
enum AudioFlags : uint8_t {
    AecProcessed = 1U << 0,
    DeviceVadSpeech = 1U << 1,
};

#pragma pack(push, 1)
struct AudioHeader {
    char magic[4];
    uint8_t kind;
    uint8_t flags;
    uint16_t header_size;
    uint32_t stream_id;
    uint32_t sequence;
    uint64_t timestamp_us;
};
#pragma pack(pop)

static_assert(sizeof(AudioHeader) == 24);

inline std::vector<uint8_t> make_audio_frame(BinaryKind kind, uint8_t flags,
                                             uint32_t stream_id,
                                             uint32_t sequence, uint64_t timestamp_us,
                                             const void *pcm, size_t pcm_bytes) {
    std::vector<uint8_t> frame(sizeof(AudioHeader) + pcm_bytes);
    auto *header = reinterpret_cast<AudioHeader *>(frame.data());
    std::memcpy(header->magic, kMagic, sizeof(kMagic));
    header->kind = static_cast<uint8_t>(kind);
    header->flags = flags;
    header->header_size = sizeof(AudioHeader);
    header->stream_id = stream_id;
    header->sequence = sequence;
    header->timestamp_us = timestamp_us;
    std::memcpy(frame.data() + sizeof(AudioHeader), pcm, pcm_bytes);
    return frame;
}

inline bool parse_audio_frame(const uint8_t *data, size_t size,
                              const AudioHeader **header,
                              const uint8_t **payload, size_t *payload_size) {
    if (size < sizeof(AudioHeader)) return false;
    const auto *candidate = reinterpret_cast<const AudioHeader *>(data);
    if (std::memcmp(candidate->magic, kMagic, sizeof(kMagic)) != 0 ||
        candidate->header_size != sizeof(AudioHeader)) return false;
    *header = candidate;
    *payload = data + sizeof(AudioHeader);
    *payload_size = size - sizeof(AudioHeader);
    // PCM payloads are whole 16-bit samples, and an odd length there means a
    // truncated frame worth rejecting. Opus packets are *arbitrary* lengths --
    // about half of them are odd, and a silent 20 ms frame is nine bytes -- so
    // applying that rule to them threw away half of every reply before it
    // reached the decoder. Nothing counted it: the frames never got as far as
    // the decoder or the playback ring, so the codec and buffer stats all
    // looked clean while the speaker starved and cracked.
    if (candidate->kind == static_cast<uint8_t>(BinaryKind::TtsOpus16k)) return true;
    return (*payload_size % sizeof(int16_t)) == 0;
}

}  // namespace kiki
