#include "kiki_wifi_score.hpp"

#include <cstdio>
#include <string>
#include <vector>

// What the analyser recommends, and why.
//
// The screen exists because a single number -- "-81 dBm" -- was, for most of a
// day, all anybody knew about a link that was dropping every few minutes. The
// recommendation is the part that has to be right: a person standing in a hall
// before a demo will do what it says, so it must never send them to a network
// they cannot join, and never call a strong link weak.
//
// Build and run on the host:
//   g++ -std=c++17 -I../main -o /tmp/test_wifi_score test_wifi_score.cpp
//   /tmp/test_wifi_score

using kiki::wifiscore::ApView;

namespace {

int failures = 0;

void check(bool ok, const char *what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

ApView ap(int rssi, int channel, bool open = false, bool known = false) {
    ApView view{};
    view.rssi = static_cast<int8_t>(rssi);
    view.channel = static_cast<uint8_t>(channel);
    view.open = open;
    view.known = known;
    return view;
}

// -------------------------------------------------------------- quality ----

void test_quality_spans_the_usable_range() {
    using kiki::wifiscore::quality;
    check(quality(-30) == 1.0f, "anything above -40 dBm is as good as it gets");
    check(quality(-40) == 1.0f, "-40 dBm is full quality");
    check(quality(-95) == 0.0f, "below -90 dBm nothing usable is left");
    check(quality(-90) == 0.0f, "-90 dBm is the floor");
    check(quality(-65) > 0.4f && quality(-65) < 0.6f, "-65 dBm sits mid-scale");
    check(quality(-55) > quality(-75), "stronger is always better");
}

void test_strength_words_match_what_people_expect() {
    using kiki::wifiscore::strength_word;
    // The board measured -41 on a hotspot and -81 on the campus network on
    // 2026-09-07; those two must not be described the same way.
    check(std::string(strength_word(-41)) == "excellent", "-41 dBm is excellent");
    check(std::string(strength_word(-60)) == "good", "-60 dBm is good");
    check(std::string(strength_word(-72)) == "weak", "-72 dBm is weak");
    check(std::string(strength_word(-81)) == "very weak", "-81 dBm is very weak");
}

// ------------------------------------------------------------- overlap -----

void test_overlap_tapers_and_stops() {
    using kiki::wifiscore::overlap_weight;
    check(overlap_weight(0) == 1.0f, "an AP on this channel interferes fully");
    check(overlap_weight(5) == 0.0f, "five channels away is clear of the 22 MHz mask");
    check(overlap_weight(-3) == overlap_weight(3), "overlap is symmetric");
    check(overlap_weight(1) > overlap_weight(3), "interference falls off with distance");
}

void test_congestion_weights_by_signal_not_by_count() {
    // The reason a plain count is wrong: twenty faint APs are a quieter channel
    // than one loud neighbour, and a phone-style "20 networks" label says the
    // opposite.
    std::vector<ApView> many(20, ap(-88, 6));
    std::vector<ApView> one{ap(-40, 6)};
    check(kiki::wifiscore::channel_congestion(one.data(), 1, 6) >
              kiki::wifiscore::channel_congestion(many.data(), (int)many.size(), 6),
          "one strong AP beats twenty faint ones for congestion");
}

void test_congestion_reaches_across_overlapping_channels() {
    std::vector<ApView> aps{ap(-45, 6)};
    const float on = kiki::wifiscore::channel_congestion(aps.data(), 1, 6);
    const float near = kiki::wifiscore::channel_congestion(aps.data(), 1, 8);
    const float away = kiki::wifiscore::channel_congestion(aps.data(), 1, 12);
    check(on > near && near > 0.0f, "channel 8 still hears a transmitter on 6");
    check(away == 0.0f, "channel 12 is clear of it");
}

// -------------------------------------------------------- best channel -----

void test_best_channel_finds_the_quiet_end_of_the_band() {
    std::vector<ApView> aps{ap(-40, 1), ap(-42, 1), ap(-45, 2), ap(-50, 3)};
    const int best = kiki::wifiscore::best_channel(aps.data(), (int)aps.size());
    check(best >= 9, "with the low channels crowded, the answer is up the band");
}

void test_ties_break_towards_the_non_overlapping_channels() {
    // An empty band: every channel scores identically on congestion, so the
    // tiebreak is the only thing choosing, and it must choose 1, 6 or 11.
    const int best = kiki::wifiscore::best_channel(nullptr, 0);
    check(best == 1 || best == 6 || best == 11,
          "an empty band resolves to a non-overlapping channel");
}

// -------------------------------------------------------- recommendation ---

void test_a_network_we_cannot_join_is_never_recommended() {
    // The live shape of this: NSUT_WIFI is reachable and saved but weak, while
    // a strong neighbouring AP needs a password nobody has. Sending someone to
    // the strong one would be useless advice.
    std::vector<ApView> aps{
        ap(-45, 6, /*open=*/false, /*known=*/false),  // strong, locked, unknown
        ap(-78, 11, /*open=*/false, /*known=*/true),  // weak, but ours
    };
    const int pick = kiki::wifiscore::recommend(aps.data(), (int)aps.size());
    check(pick == 1, "a joinable weak network beats an unjoinable strong one");
}

void test_among_joinable_networks_the_strongest_wins() {
    std::vector<ApView> aps{
        ap(-80, 6, false, true),
        ap(-48, 6, false, true),
        ap(-70, 6, false, true),
    };
    check(kiki::wifiscore::recommend(aps.data(), 3) == 1, "-48 dBm is the pick");
}

void test_congestion_breaks_a_tie_between_similar_signals() {
    // Same signal, different neighbourhoods: channel 11 is empty, channel 1 is
    // stacked. The quiet one should win.
    std::vector<ApView> aps{
        ap(-60, 1, false, true),
        ap(-60, 11, false, true),
        ap(-45, 1), ap(-46, 1), ap(-47, 2), ap(-44, 3),
    };
    const int pick = kiki::wifiscore::recommend(aps.data(), (int)aps.size());
    check(pick == 1, "at equal signal the quieter channel wins");
}

void test_signal_still_outranks_congestion() {
    // The penalty is secondary on purpose: a strong link on a busy channel is
    // genuinely better than a faint one on an empty channel, and a scoring
    // function that got this backwards would walk someone away from a good AP.
    std::vector<ApView> aps{
        ap(-42, 1, false, true),   // strong, crowded
        ap(-78, 11, false, true),  // faint, empty
        ap(-40, 1), ap(-41, 1), ap(-43, 2), ap(-44, 3), ap(-45, 2),
    };
    check(kiki::wifiscore::recommend(aps.data(), (int)aps.size()) == 0,
          "a strong crowded AP beats a faint empty one");
}

void test_a_known_network_edges_out_an_open_stranger() {
    // Campus guest APs are usually captive portals: they associate, look
    // joined, and carry nothing.
    std::vector<ApView> aps{
        ap(-60, 6, /*open=*/true, /*known=*/false),
        ap(-60, 6, /*open=*/false, /*known=*/true),
    };
    check(kiki::wifiscore::recommend(aps.data(), 2) == 1,
          "at equal signal, the network we have actually joined before wins");
}

void test_nothing_joinable_still_names_the_strongest() {
    // So the screen can say "strongest here, but you will need its password"
    // instead of going blank, which reads as a broken scan.
    std::vector<ApView> aps{ap(-70, 1), ap(-52, 6), ap(-83, 11)};
    check(kiki::wifiscore::recommend(aps.data(), 3) == 1,
          "with nothing joinable, the strongest AP is still named");
}

void test_an_empty_survey_recommends_nothing() {
    check(kiki::wifiscore::recommend(nullptr, 0) == -1, "no APs, no recommendation");
    std::vector<ApView> none;
    check(kiki::wifiscore::recommend(none.data(), 0) == -1, "an empty list is not a pick");
}

}  // namespace

int main() {
    test_quality_spans_the_usable_range();
    test_strength_words_match_what_people_expect();
    test_overlap_tapers_and_stops();
    test_congestion_weights_by_signal_not_by_count();
    test_congestion_reaches_across_overlapping_channels();
    test_best_channel_finds_the_quiet_end_of_the_band();
    test_ties_break_towards_the_non_overlapping_channels();
    test_a_network_we_cannot_join_is_never_recommended();
    test_among_joinable_networks_the_strongest_wins();
    test_congestion_breaks_a_tie_between_similar_signals();
    test_signal_still_outranks_congestion();
    test_a_known_network_edges_out_an_open_stranger();
    test_nothing_joinable_still_names_the_strongest();
    test_an_empty_survey_recommends_nothing();

    if (failures == 0) std::printf("test_wifi_score: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
