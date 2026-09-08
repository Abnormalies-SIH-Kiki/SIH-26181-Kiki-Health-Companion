#include "kiki_dance.hpp"

#include <cstdlib>

// The routine interpreter, and nothing else. No LVGL, no ESP-IDF, no floats
// beyond the ones in the interface -- so the parser that turns a websocket
// string into array indices can be run, fuzzed and asserted on a laptop
// instead of only ever being exercised on a board across the room.

namespace kiki {
namespace {

// Reads one non-negative integer and leaves `cursor` on the delimiter that
// ended it. Returns false when there was no digit at all, which is what
// separates "field omitted" from "field is zero".
bool read_number(const char *&cursor, long *out) {
    char *end = nullptr;
    const long value = std::strtol(cursor, &end, 10);
    if (end == cursor) return false;
    cursor = end;
    *out = value;
    return true;
}

long clamp_long(long value, long low, long high) {
    return value < low ? low : (value > high ? high : value);
}

}  // namespace

size_t dance_parse_routine(const char *text, DanceStep *out, size_t max_steps) {
    if (!text || !out || max_steps == 0) return 0;
    size_t count = 0;
    const char *cursor = text;
    uint16_t previous_beat = 0;
    bool have_previous = false;

    while (*cursor && count < max_steps) {
        while (*cursor == ';' || *cursor == ' ' || *cursor == '\n') ++cursor;
        if (!*cursor) break;

        long fields[5] = {0, 0, 0, 0, 0};
        bool ok = true;
        for (int i = 0; i < 5 && ok; ++i) {
            if (!read_number(cursor, &fields[i])) {
                ok = false;
                break;
            }
            if (i < 4) {
                if (*cursor == ',') {
                    ++cursor;
                } else {
                    // Fewer fields than expected: keep what we have and let the
                    // clamps below supply the rest.
                    break;
                }
            }
        }
        // Skip to the end of this entry whatever happened, so one malformed
        // record cannot desynchronise every record after it.
        while (*cursor && *cursor != ';') ++cursor;

        if (!ok) continue;
        if (fields[1] < 0 || fields[1] >= static_cast<long>(DanceMove::Count)) continue;

        DanceStep step{};
        step.beat = static_cast<uint16_t>(clamp_long(fields[0], 0, 65535));
        step.move = static_cast<uint8_t>(fields[1]);
        step.beats = static_cast<uint8_t>(clamp_long(fields[2], 1, 64));
        step.intensity = static_cast<uint8_t>(clamp_long(fields[3], 0, 15));
        step.effect = static_cast<uint8_t>(
            fields[4] >= 0 && fields[4] < static_cast<long>(DanceEffect::Count)
                ? fields[4]
                : 0);

        // The renderer walks this list with a monotonic cursor, so an
        // out-of-order entry would be silently unreachable at best and would
        // rewind the routine at worst. Dropping it is the honest answer.
        if (have_previous && step.beat <= previous_beat) continue;
        previous_beat = step.beat;
        have_previous = true;
        out[count++] = step;
    }
    return count;
}

size_t dance_parse_energy(const char *text, uint8_t *out, size_t max_beats) {
    if (!text || !out || max_beats == 0) return 0;
    size_t count = 0;
    for (const char *cursor = text; *cursor && count < max_beats; ++cursor) {
        const char character = *cursor;
        uint8_t value;
        if (character >= '0' && character <= '9') {
            value = static_cast<uint8_t>(character - '0');
        } else if (character >= 'a' && character <= 'f') {
            value = static_cast<uint8_t>(character - 'a' + 10);
        } else if (character >= 'A' && character <= 'F') {
            value = static_cast<uint8_t>(character - 'A' + 10);
        } else {
            continue;
        }
        out[count++] = value;
    }
    return count;
}

int dance_step_at(const DanceStep *steps, size_t count, float beat) {
    if (!steps || count == 0) return -1;
    if (beat < static_cast<float>(steps[0].beat)) return -1;
    // Linear from the end: routines are short (<=256) and this is called once
    // per frame, but scanning backwards finds the answer in one or two steps
    // for the ordinary case of time moving forwards.
    for (size_t i = count; i > 0; --i) {
        if (beat >= static_cast<float>(steps[i - 1].beat)) {
            return static_cast<int>(i - 1);
        }
    }
    return -1;
}

}  // namespace kiki
