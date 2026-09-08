#pragma once

#include <cstdint>
#include <cstring>
#include "lvgl.h"

namespace kiki {

// One palette for every screen: the face, the status rows, settings and the
// Wi-Fi picker. Built around the mascot's clay (#D97757) rather than the pure
// black-and-white the 1-bit OLED was limited to.
//
// It stays deliberately narrow -- a warm near-black ground, the clay, two warm
// greys for text, and exactly two functional colours (a muted teal for Kiki's
// sound waves, a muted red for stop). Anything more and a 1.75-inch panel
// starts looking like a toy.

namespace theme {

// LVGL widgets take 24-bit hex.
constexpr uint32_t kBackground = 0x12100E;  // warm near-black, not blue-black
constexpr uint32_t kMascot     = 0xD97757;  // the crab
constexpr uint32_t kTextPrimary   = 0xF2EBE4;
constexpr uint32_t kTextSecondary = 0x9A8F86;
constexpr uint32_t kSurface    = 0x241F1B;  // buttons, list rows
constexpr uint32_t kBorder     = 0x3A322C;
constexpr uint32_t kAccent     = 0x5E9C93;  // muted teal: listening / speaking
constexpr uint32_t kStop       = 0xB4544A;  // muted red: cancel

// A mood changes the whole visual atmosphere, not just one decorative pixel.
// Every model-selectable <oled:...> expression therefore owns a coordinated
// background, shell and prop/accent triplet. The grounds stay near-black for
// the round AMOLED and the colours stay muted enough to remain Kiki rather
// than turning the panel into a traffic light:
//
//   * warm coral/amber = open, playful, high-energy moods
//   * teal/green       = curiosity and disorientation
//   * violet           = pride, mischief and wonder
//   * cool blue/slate  = low-energy or vulnerable moods
//
// Shell-to-ground contrast is at least 4.8:1 in every palette, so the block
// crab remains crisp even at the low-power brightness setting.
struct PersonaPalette {
    const char *name;
    uint32_t background;
    uint32_t mascot;
    uint32_t accent;
};

constexpr PersonaPalette kDefaultPalette{"", kBackground, kMascot, kAccent};
constexpr PersonaPalette kPersonaPalettes[] = {
    {"love",       0x1C0E14, 0xE3848C, 0xF0B1B8},
    {"shy",        0x1A1013, 0xD58A7E, 0xE6A4A4},
    {"giggle",     0x191109, 0xE29A55, 0xF2C070},
    {"wink",       0x17100D, 0xD98262, 0xEEB084},
    {"excited",    0x171305, 0xE6AD45, 0xF6D06F},
    {"curious",    0x0C1716, 0x63A99C, 0x9BD0C6},
    {"proud",      0x15101B, 0xA98BC6, 0xD0B6E3},
    {"sulk",       0x150F13, 0x9B7882, 0xC0A0AA},
    {"surprised",  0x181306, 0xDDB45D, 0xF4D48A},
    {"sleepy",     0x0C1219, 0x7892AC, 0xACBFD0},
    {"idea",       0x171505, 0xD6B84F, 0xF1DB79},
    {"mischief",   0x150E19, 0xA777BC, 0xD2A5E2},
    {"scared",     0x0D1316, 0x8FA2AE, 0xC4D2D8},
    {"awe",        0x0E111C, 0x8C99D0, 0xC3CAF0},
    {"happy",      0x191008, 0xDF8B55, 0xF2BC78},
    {"sad",        0x0D1218, 0x708AA3, 0xA3B7C9},
    {"dizzy",      0x10150F, 0x91A48A, 0xC2D0B3},
    {"confused",   0x16130D, 0xB19A6C, 0xD8C493},
    {"sleeping",   0x0B1016, 0x71869B, 0xA8BAC9},
};

inline const PersonaPalette &persona(const char *name) {
    if (name) {
        for (const auto &palette : kPersonaPalettes) {
            if (std::strcmp(name, palette.name) == 0) return palette;
        }
    }
    return kDefaultPalette;
}

// Touch reactions borrow an existing emotional palette so the interaction
// feels like the same character language as model-selected moods, without
// polluting the 19-tag prompt vocabulary with internal animation names.
inline const char *reaction_mood(const char *name) {
    if (!name) return name;
    if (std::strcmp(name, "tap_ouch_left") == 0 ||
        std::strcmp(name, "tap_ouch_right") == 0) return "scared";
    if (std::strcmp(name, "tap_boop") == 0) return "curious";
    if (std::strcmp(name, "tap_pat") == 0) return "proud";
    if (std::strcmp(name, "tap_pinch") == 0) return "giggle";
    if (std::strcmp(name, "tap_blaster") == 0) return "mischief";
    if (std::strcmp(name, "tap_tumble") == 0) return "dizzy";
    if (std::strcmp(name, "motion_bored") == 0) return "sleepy";
    if (std::strcmp(name, "motion_alert") == 0 ||
        std::strcmp(name, "motion_carried") == 0) return "curious";
    if (std::strcmp(name, "motion_annoyed") == 0 ||
        std::strcmp(name, "motion_face_down") == 0) return "sulk";
    if (std::strcmp(name, "motion_rocking") == 0 ||
        std::strcmp(name, "motion_settled") == 0) return "happy";
    if (std::strcmp(name, "motion_upside_down") == 0) return "confused";
    if (std::strcmp(name, "motion_brace") == 0) return "scared";
    return name;
}

inline const PersonaPalette &visual(const char *name) {
    return persona(reaction_mood(name));
}

inline lv_color_t background()    { return lv_color_hex(kBackground); }
inline lv_color_t mascot()        { return lv_color_hex(kMascot); }
inline lv_color_t text_primary()  { return lv_color_hex(kTextPrimary); }
inline lv_color_t text_secondary(){ return lv_color_hex(kTextSecondary); }
inline lv_color_t surface()       { return lv_color_hex(kSurface); }
inline lv_color_t border()        { return lv_color_hex(kBorder); }
inline lv_color_t accent()        { return lv_color_hex(kAccent); }
inline lv_color_t stop()          { return lv_color_hex(kStop); }
inline lv_color_t persona_background(const char *name) {
    return lv_color_hex(visual(name).background);
}

// The face canvas is a raw RGB565 buffer, so it needs the packed forms.
constexpr uint16_t rgb565(uint32_t hex) {
    return static_cast<uint16_t>((((hex >> 19) & 0x1F) << 11) |
                                 (((hex >> 10) & 0x3F) << 5) |
                                 ((hex >> 3) & 0x1F));
}
constexpr uint16_t kBackground565 = rgb565(kBackground);
constexpr uint16_t kMascot565     = rgb565(kMascot);
constexpr uint16_t kAccent565     = rgb565(kAccent);

}  // namespace theme
}  // namespace kiki
