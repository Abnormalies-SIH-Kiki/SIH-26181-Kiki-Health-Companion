#pragma once

// Which network should this watch actually join, and is the band the problem?
//
// Deliberately free of ESP-IDF: everything here is arithmetic over surveyed
// access points, so `firmware/tests/test_wifi_score.cpp` can check it on the
// host. The board-side file only gathers the survey and draws the answer.
//
// One hardware fact shapes all of it. The ESP32-S3 has a 2.4 GHz radio and no
// 5 GHz radio, so a survey physically cannot contain the 5 GHz APs a phone
// reports. Measured on 2026-09-07: a phone sat on NSUT_WIFI channel 153 at good
// signal while this board, on the *same SSID*, was associated at -81 dBm on
// 2.4 GHz. They are different radios on different bands wearing one name. The
// analyser therefore never claims to survey a band it cannot hear.

#include <cstdint>

namespace kiki {
namespace wifiscore {

// 2.4 GHz channels sit 5 MHz apart and are about 22 MHz wide, so a transmitter
// interferes with everything within four channels of it. This is why 1, 6 and
// 11 are the only mutually non-overlapping choices.
constexpr int kOverlapChannels = 4;
constexpr int kMinChannel = 1;
constexpr int kMaxChannel = 13;

// The window the quality scale spans. -40 dBm and better is as good as it gets
// in practice; below -90 nothing usable is left.
constexpr int kStrongDbm = -40;
constexpr int kUnusableDbm = -90;

// One access point, reduced to what the maths needs.
struct ApView {
    int8_t rssi;
    uint8_t channel;
    bool open;    // no passphrase
    bool known;   // this board already holds credentials for this SSID
};

// 0.0 at -90 dBm or worse, 1.0 at -40 dBm or better, linear between.
//
// Linear in dBm rather than in milliwatts on purpose: this drives a judgement
// about how well a link will behave, and perceived link quality tracks the
// logarithmic scale far better than raw power, which would make everything
// below -60 look identically dead.
inline float quality(int rssi) {
    if (rssi >= kStrongDbm) return 1.0f;
    if (rssi <= kUnusableDbm) return 0.0f;
    return static_cast<float>(rssi - kUnusableDbm) /
           static_cast<float>(kStrongDbm - kUnusableDbm);
}

// How much an AP `distance` channels away spills into this one: full weight on
// the same channel, tapering to nothing at the edge of the overlap window.
inline float overlap_weight(int distance) {
    if (distance < 0) distance = -distance;
    if (distance > kOverlapChannels) return 0.0f;
    return 1.0f - (static_cast<float>(distance) /
                   static_cast<float>(kOverlapChannels + 1));
}

// Congestion on `channel`, weighting each AP by how strong it is and how much
// it overlaps. Unbounded above; only ever compared against other channels.
//
// Weighted by signal, not just counted: twenty distant APs interfere less than
// one loud neighbour, and a plain count says the opposite.
inline float channel_congestion(const ApView *aps, int count, int channel) {
    if (!aps || count <= 0) return 0.0f;
    float total = 0.0f;
    for (int i = 0; i < count; ++i) {
        const int distance = static_cast<int>(aps[i].channel) - channel;
        const float weight = overlap_weight(distance);
        if (weight <= 0.0f) continue;
        total += weight * quality(aps[i].rssi);
    }
    return total;
}

// The quietest 2.4 GHz channel. Informational: you cannot retune somebody
// else's access point, but it answers "is this band hopeless here, or is the
// watch simply on the wrong AP?"
inline int best_channel(const ApView *aps, int count) {
    int best = kMinChannel;
    float best_score = -1.0f;
    for (int channel = kMinChannel; channel <= kMaxChannel; ++channel) {
        const float congestion = channel_congestion(aps, count, channel);
        // Prefer 1 / 6 / 11 when the difference is marginal: they are the only
        // channels that do not overlap each other, so a tie broken towards them
        // stays true once somebody else also picks a channel.
        const bool canonical = channel == 1 || channel == 6 || channel == 11;
        const float score = -congestion + (canonical ? 0.15f : 0.0f);
        if (score > best_score) {
            best_score = score;
            best = channel;
        }
    }
    return best;
}

// Can this board actually get onto it? An AP needing a passphrase nobody has
// is not a recommendation, however strong it is.
inline bool joinable(const ApView &ap) { return ap.known || ap.open; }

// How good a home this AP would be. Signal is the dominant term; congestion is
// a real but secondary penalty, because a strong link on a busy channel still
// beats a weak link on an empty one.
inline float join_score(const ApView &ap, const ApView *aps, int count) {
    const float congestion = channel_congestion(aps, count, ap.channel);
    // Normalised against a thoroughly busy channel so the penalty stays in the
    // same units as quality() and cannot swamp it.
    constexpr float kBusyChannel = 6.0f;
    float penalty = congestion / kBusyChannel;
    if (penalty > 1.0f) penalty = 1.0f;
    // A network already known beats an open stranger at equal signal: campus
    // guest APs are usually captive portals, which look joined and carry no
    // traffic.
    const float familiarity = ap.known ? 0.10f : 0.0f;
    return quality(ap.rssi) - 0.30f * penalty + familiarity;
}

// Index of the AP to recommend, or -1 when the survey is empty.
//
// Joinable APs always win over unjoinable ones regardless of signal. When
// nothing is joinable the strongest AP is returned anyway, so the screen can
// say "strongest here, but you will need its password" rather than nothing.
inline int recommend(const ApView *aps, int count) {
    if (!aps || count <= 0) return -1;
    int best = -1;
    float best_score = 0.0f;
    for (int i = 0; i < count; ++i) {
        if (!joinable(aps[i])) continue;
        const float score = join_score(aps[i], aps, count);
        if (best < 0 || score > best_score) {
            best = i;
            best_score = score;
        }
    }
    if (best >= 0) return best;
    for (int i = 0; i < count; ++i) {
        if (best < 0 || aps[i].rssi > aps[best].rssi) best = i;
    }
    return best;
}

// A word for a signal level, so the screen never shows a bare negative number
// to somebody who does not read dBm.
inline const char *strength_word(int rssi) {
    if (rssi >= -55) return "excellent";
    if (rssi >= -67) return "good";
    if (rssi >= -75) return "weak";
    return "very weak";
}

}  // namespace wifiscore
}  // namespace kiki
