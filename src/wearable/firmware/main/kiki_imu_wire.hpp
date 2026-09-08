#pragma once

// The wire format for one raw-IMU window, and nothing else.
//
// This is a contract between two languages: the firmware packs the samples here
// and `gateway/kiki_gateway/care/imu.py` unpacks them. A disagreement about
// scale or byte order does not fail loudly -- it produces plausible-looking
// motion evidence that is wrong, which the care model would then use to decide
// whether somebody actually did their exercise. So the encoding lives in one
// ESP-IDF-free header, pinned on this side by `firmware/tests/test_imu_wire.cpp`
// and on the other by `gateway/tests/test_care_imu.py`, both against the same
// vector.
//
// Layout: six int16 per sample, little-endian, in the order
// ax, ay, az, gx, gy, gz. Acceleration in milli-g, rotation in centi-deg/s.
// +-32 g and +-327 deg/s of headroom against the QMI8658's configured 8 g and
// 1024 dps -- far more range than needed, at a resolution far finer than any of
// the analysis cares about.

#include <cmath>
#include <cstddef>
#include <cstdint>

namespace kiki {
namespace imu_wire {

constexpr float kAccelToWire = 1000.0F;  // g -> milli-g
constexpr float kGyroToWire = 100.0F;    // dps -> centi-dps
constexpr int kAxes = 6;
constexpr int kBytesPerSample = kAxes * 2;

inline int16_t clamp16(float value) {
    if (value > 32767.0F) return 32767;
    if (value < -32768.0F) return -32768;
    return static_cast<int16_t>(std::lround(value));
}

// One sample, from physical units into the wire's int16 slots.
inline void pack_sample(float ax, float ay, float az,
                        float gx, float gy, float gz, int16_t *out) {
    out[0] = clamp16(ax * kAccelToWire);
    out[1] = clamp16(ay * kAccelToWire);
    out[2] = clamp16(az * kAccelToWire);
    out[3] = clamp16(gx * kGyroToWire);
    out[4] = clamp16(gy * kGyroToWire);
    out[5] = clamp16(gz * kGyroToWire);
}

// Little-endian, explicitly, rather than memcpy of the native layout. The
// ESP32-S3 is little-endian and so is every machine the gateway runs on, but a
// format defined by "whatever this compiler did" is a format nobody can read
// back in five years.
inline size_t serialize(const int16_t (*samples)[kAxes], int count,
                        uint8_t *out, size_t out_size) {
    size_t offset = 0;
    for (int i = 0; i < count; ++i) {
        if (offset + kBytesPerSample > out_size) break;
        for (int axis = 0; axis < kAxes; ++axis) {
            const int16_t value = samples[i][axis];
            out[offset++] = static_cast<uint8_t>(value & 0xFF);
            out[offset++] = static_cast<uint8_t>((value >> 8) & 0xFF);
        }
    }
    return offset;
}

inline const char *base64_alphabet() {
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
}

// Standard base64 with padding. Truncates rather than overruns: a short buffer
// costs samples off the end of a window, never memory that belongs to the audio
// pipeline.
inline size_t encode_base64(const uint8_t *data, size_t length,
                            char *out, size_t out_size) {
    const char *alphabet = base64_alphabet();
    size_t written = 0;
    for (size_t i = 0; i < length; i += 3) {
        if (written + 4 >= out_size) break;
        const uint32_t a = data[i];
        const uint32_t b = (i + 1 < length) ? data[i + 1] : 0;
        const uint32_t c = (i + 2 < length) ? data[i + 2] : 0;
        const uint32_t triple = (a << 16) | (b << 8) | c;
        out[written++] = alphabet[(triple >> 18) & 0x3F];
        out[written++] = alphabet[(triple >> 12) & 0x3F];
        out[written++] = (i + 1 < length) ? alphabet[(triple >> 6) & 0x3F] : '=';
        out[written++] = (i + 2 < length) ? alphabet[triple & 0x3F] : '=';
    }
    if (out_size > 0) out[written] = 0;
    return written;
}

}  // namespace imu_wire
}  // namespace kiki
