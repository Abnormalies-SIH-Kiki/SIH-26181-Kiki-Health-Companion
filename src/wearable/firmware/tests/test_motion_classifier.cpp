#include "kiki_motion_classifier.hpp"

#include <cassert>
#include <cstdio>
#include <initializer_list>

using kiki::MotionClassifier;
using kiki::MotionContext;
using kiki::MotionDecision;
using kiki::MotionPosture;
using kiki::MotionSample;
using kiki::MotionSituation;

namespace {

MotionDecision feed(MotionClassifier &classifier, uint32_t &now,
                    std::initializer_list<MotionSample> pattern,
                    uint32_t duration_ms, const MotionContext &context = {}) {
    MotionDecision last{};
    const auto *samples = pattern.begin();
    const size_t count = pattern.size();
    for (uint32_t spent = 0, i = 0; spent < duration_ms; spent += 20, ++i) {
        MotionSample sample = samples[i % count];
        sample.timestamp_ms = now;
        if (auto decision = classifier.update(sample, context)) last = decision;
        now += 20;
    }
    return last;
}

void test_postures() {
    MotionClassifier classifier;
    uint32_t now = 100;
    feed(classifier, now, {{0, 0, -1, 0, 0, 0}}, 700);
    assert(classifier.snapshot().posture == MotionPosture::FaceUp);
    const auto upright = feed(classifier, now, {{1, 0, 0, 0, 0, 0}}, 700);
    assert(classifier.snapshot().posture == MotionPosture::Upright);
    assert(upright.situation == MotionSituation::UprightAlert);
    const auto inverted = feed(classifier, now, {{-1, 0, 0, 0, 0, 0}}, 900);
    assert(classifier.snapshot().posture == MotionPosture::UpsideDown);
    assert(inverted.situation == MotionSituation::UpsideDown);
}

void test_shake_escalates() {
    MotionClassifier classifier;
    uint32_t now = 100;
    feed(classifier, now, {{0, 0, -1, 0, 0, 0}}, 1400);
    MotionDecision first{};
    for (int burst = 0; burst < 7 && !first; ++burst) {
        const auto decision = feed(
            classifier, now,
            {{0.9F, 0, -1, 260, 0, 0}, {-0.9F, 0, -1, -260, 0, 0}}, 700);
        if (decision.situation == MotionSituation::Shake ||
            decision.situation == MotionSituation::RepeatedShake) {
            first = decision;
        }
    }
    assert(first.situation == MotionSituation::Shake ||
           first.situation == MotionSituation::RepeatedShake);
    feed(classifier, now, {{0, 0, -1, 0, 0, 0}}, 1600);
    MotionDecision second{};
    for (int burst = 0; burst < 5 && second.situation != MotionSituation::RepeatedShake;
         ++burst) {
        const auto decision = feed(
            classifier, now,
            {{1.1F, 0, -1, 310, 0, 0}, {-1.1F, 0, -1, -310, 0, 0}}, 800);
        if (decision.situation == MotionSituation::RepeatedShake) second = decision;
    }
    assert(second.situation == MotionSituation::RepeatedShake);
}

void test_freefall_and_landing() {
    MotionClassifier classifier;
    uint32_t now = 100;
    feed(classifier, now, {{0, 0, -1, 0, 0, 0}}, 700);
    const auto fall = feed(classifier, now, {{0, 0, 0.05F, 0, 0, 0}}, 80);
    assert(fall.situation == MotionSituation::Freefall);
    const auto landing = feed(classifier, now, {{0, 0, -2.8F, 0, 0, 0}}, 40);
    assert(landing.situation == MotionSituation::HardLanding);
}

void test_boredom() {
    MotionClassifier classifier;
    uint32_t now = 100;
    MotionContext idle{};
    idle.idle = true;
    const auto decision = feed(classifier, now, {{0, 0, -1, 0, 0, 0}}, 62000, idle);
    assert(decision.situation == MotionSituation::Bored);
}

void test_face_down_and_music_dance() {
    MotionClassifier classifier;
    uint32_t now = 100;
    feed(classifier, now, {{0, 0, -1, 0, 0, 0}}, 700);
    const auto face_down = feed(classifier, now, {{0, 0, 1, 0, 0, 0}}, 900);
    assert(face_down.situation == MotionSituation::FaceDown);

    MotionContext music{};
    music.music = true;
    MotionDecision dance{};
    for (int i = 0; i < 250 && !dance; ++i) {
        MotionSample sample = i % 2 == 0
                                  ? MotionSample{0.7F, 0, -1, 220, 0, 0}
                                  : MotionSample{-0.7F, 0, -1, -220, 0, 0};
        sample.timestamp_ms = now;
        const auto decision = classifier.update(sample, music);
        if (decision.situation == MotionSituation::MusicDance) dance = decision;
        now += 20;
    }
    assert(dance.situation == MotionSituation::MusicDance);
}

void test_charging_rest_replaces_boredom() {
    MotionClassifier classifier;
    uint32_t now = 100;
    MotionContext charging{};
    charging.idle = true;
    charging.charging = true;
    const auto decision = feed(classifier, now, {{0, 0, -1, 0, 0, 0}}, 47000, charging);
    assert(decision.situation == MotionSituation::ChargingRest);
}

}  // namespace

int main() {
    test_postures();
    test_shake_escalates();
    test_freefall_and_landing();
    test_boredom();
    test_face_down_and_music_dance();
    test_charging_rest_replaces_boredom();
    std::puts("motion classifier tests passed");
    return 0;
}
