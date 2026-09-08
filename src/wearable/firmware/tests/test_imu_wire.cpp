// The firmware half of the raw-IMU wire contract.
//
// `gateway/tests/test_care_imu.py::test_the_firmware_wire_vector_decodes` pins
// the SAME vector from the other side. If these two ever disagree, the care
// model is handed motion evidence that is quietly wrong -- plausible numbers
// about a movement that did not happen that way -- which is exactly the class
// of failure the whole grounding design exists to prevent. So the check is a
// literal string on both sides rather than a round trip within one language.

#include "kiki_imu_wire.hpp"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>

int main() {
    using namespace kiki::imu_wire;

    // Scale: 1 g is 1000 on the wire, 1 deg/s is 100.
    int16_t sample[kAxes];
    pack_sample(0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 0.0F, sample);
    assert(sample[2] == 1000);
    pack_sample(0.0F, 0.0F, 0.0F, 1.0F, -2.5F, 0.0F, sample);
    assert(sample[3] == 100);
    assert(sample[4] == -250);

    // Rounding is to nearest, not truncation: a wrist held at 0.9995 g must not
    // read as 999 milli-g on one platform and 1000 on another.
    pack_sample(0.0F, 0.0F, 0.9995F, 0.0F, 0.0F, 0.0F, sample);
    assert(sample[2] == 1000);

    // Saturation, rather than wrapping. A 40 g spike clamped to +32 g is still
    // obviously an impact; wrapped, it would read as a gentle movement.
    pack_sample(40.0F, -40.0F, 0.0F, 0.0F, 0.0F, 0.0F, sample);
    assert(sample[0] == 32767);
    assert(sample[1] == -32768);

    // Little-endian, explicitly.
    int16_t samples[2][kAxes] = {
        {1, -1, 1000, 0, 0, 0},
        {258, 0, 0, 0, 0, -2},
    };
    uint8_t bytes[64]{};
    const size_t length = serialize(samples, 2, bytes, sizeof(bytes));
    assert(length == 2 * kBytesPerSample);
    assert(bytes[0] == 0x01 && bytes[1] == 0x00);   // 1
    assert(bytes[2] == 0xFF && bytes[3] == 0xFF);   // -1
    assert(bytes[4] == 0xE8 && bytes[5] == 0x03);   // 1000
    assert(bytes[12] == 0x02 && bytes[13] == 0x01); // 258
    assert(bytes[22] == 0xFE && bytes[23] == 0xFF); // -2

    // A buffer too small truncates whole samples; it never overruns and never
    // emits half a sample the other side would read as garbage.
    uint8_t tiny[kBytesPerSample + 3]{};
    assert(serialize(samples, 2, tiny, sizeof(tiny)) == kBytesPerSample);

    // The exact vector the Python test decodes.
    char encoded[128]{};
    encode_base64(bytes, length, encoded, sizeof(encoded));
    const char *expected = "AQD//+gDAAAAAAAAAgEAAAAAAAAAAP7/";
    if (std::strcmp(encoded, expected) != 0) {
        std::printf("wire vector changed!\n  got:      %s\n  expected: %s\n",
                    encoded, expected);
        return 1;
    }

    // Padding, for a length that is not a multiple of three.
    char padded[16]{};
    const uint8_t one[1] = {0x4D};
    encode_base64(one, 1, padded, sizeof(padded));
    assert(std::strcmp(padded, "TQ==") == 0);

    std::puts("imu wire format checks passed");
    return 0;
}
