#include "kiki_motion_classifier.hpp"

#include <algorithm>
#include <cmath>

namespace kiki {
namespace {

constexpr float kGravity = 1.0F;
constexpr uint32_t kPostureDwellMs = 350;

float magnitude(float x, float y, float z) {
    return std::sqrt(x * x + y * y + z * z);
}

float clamp01(float value) { return std::clamp(value, 0.0F, 1.0F); }

uint32_t elapsed(uint32_t now, uint32_t then) { return now - then; }

}  // namespace

MotionPosture MotionClassifier::raw_posture(float gx, float gy, float gz) const {
    const float mag = magnitude(gx, gy, gz);
    if (mag < 0.65F) return MotionPosture::Unknown;
    gx /= mag;
    gy /= mag;
    gz /= mag;

    // Waveshare's reference application maps accelerometer X to screen Y and
    // reports about -1 g on Z while face-up. Therefore +X is screen-down when
    // the portrait face is upright. These thresholds leave a wide neutral band
    // so merely tilting the board does not chatter between postures.
    if (gz < -0.78F) return MotionPosture::FaceUp;
    if (gz > 0.78F) return MotionPosture::FaceDown;
    if (gx > 0.68F) return MotionPosture::Upright;
    if (gx < -0.68F) return MotionPosture::UpsideDown;
    if (gy > 0.68F) return MotionPosture::SideLeft;
    if (gy < -0.68F) return MotionPosture::SideRight;
    return MotionPosture::Unknown;
}

void MotionClassifier::update_posture(MotionPosture raw, uint32_t now_ms) {
    posture_changed_ = false;
    if (raw != posture_candidate_) {
        posture_candidate_ = raw;
        posture_candidate_since_ms_ = now_ms;
        return;
    }
    if (raw == MotionPosture::Unknown || raw == posture_) return;
    if (elapsed(now_ms, posture_candidate_since_ms_) < kPostureDwellMs) return;
    previous_posture_ = posture_;
    posture_ = raw;
    posture_changed_ = true;
}

MotionDecision MotionClassifier::emit(MotionSituation situation, float intensity,
                                      uint32_t now_ms, uint32_t cooldown_ms) {
    const auto index = static_cast<size_t>(situation);
    if (index >= last_emitted_ms_.size()) return {};
    // Zero is also the initial timestamp, so a brand-new classifier may emit.
    if (last_emitted_ms_[index] != 0 &&
        elapsed(now_ms, last_emitted_ms_[index]) < cooldown_ms) {
        return {};
    }
    last_emitted_ms_[index] = now_ms;
    snapshot_.last_situation = situation;
    ++snapshot_.event_count;
    return {situation, posture_, clamp01(intensity)};
}

void MotionClassifier::note_interaction(uint32_t now_ms) {
    idle_since_ms_ = now_ms;
    boredom_stage_ = 0;
    sleeping_ = false;
}

MotionDecision MotionClassifier::update(const MotionSample &sample,
                                        const MotionContext &context) {
    const uint32_t now = sample.timestamp_ms;
    if (!initialized_) {
        initialized_ = true;
        previous_ms_ = now;
        gravity_x_ = sample.ax;
        gravity_y_ = sample.ay;
        gravity_z_ = sample.az;
        previous_accel_g_ = magnitude(sample.ax, sample.ay, sample.az);
        still_since_ms_ = now;
        last_motion_ms_ = now;
        idle_since_ms_ = now;
        posture_candidate_ = raw_posture(gravity_x_, gravity_y_, gravity_z_);
        posture_candidate_since_ms_ = now;
        snapshot_.posture = posture_;
        return {};
    }

    const float dt = std::clamp(elapsed(now, previous_ms_) / 1000.0F, 0.005F, 0.20F);
    previous_ms_ = now;

    const float accel_g = magnitude(sample.ax, sample.ay, sample.az);
    const float gyro_dps = magnitude(sample.gx, sample.gy, sample.gz);
    // About a 180 ms gravity time constant. Dynamic acceleration falls out of
    // the low-pass estimate and becomes the motion signal used below.
    const float gravity_alpha = std::exp(-dt / 0.18F);
    gravity_x_ = gravity_alpha * gravity_x_ + (1.0F - gravity_alpha) * sample.ax;
    gravity_y_ = gravity_alpha * gravity_y_ + (1.0F - gravity_alpha) * sample.ay;
    gravity_z_ = gravity_alpha * gravity_z_ + (1.0F - gravity_alpha) * sample.az;
    const float linear_g = magnitude(sample.ax - gravity_x_, sample.ay - gravity_y_,
                                     sample.az - gravity_z_);
    const float jerk_g_s = std::fabs(accel_g - previous_accel_g_) / dt;
    previous_accel_g_ = accel_g;
    motion_ema_ += (linear_g - motion_ema_) * std::min(1.0F, dt * 5.0F);
    gyro_ema_ += (gyro_dps - gyro_ema_) * std::min(1.0F, dt * 4.0F);

    const bool still = linear_g < 0.045F && gyro_dps < 9.0F &&
                       std::fabs(accel_g - kGravity) < 0.10F;
    const bool meaningful_motion = linear_g > 0.09F || gyro_dps > 18.0F;
    if (still) {
        if (still_since_ms_ == 0) still_since_ms_ = now;
        moving_since_ms_ = 0;
    } else {
        still_since_ms_ = 0;
        if (moving_since_ms_ == 0) moving_since_ms_ = now;
    }
    if (meaningful_motion) {
        last_motion_ms_ = now;
        idle_since_ms_ = now;
        boredom_stage_ = 0;
    }

    update_posture(raw_posture(gravity_x_, gravity_y_, gravity_z_), now);
    snapshot_.posture = posture_;
    snapshot_.accel_g = accel_g;
    snapshot_.linear_g = linear_g;
    snapshot_.gyro_dps = gyro_dps;
    snapshot_.annoyance = annoyance_;
    snapshot_.stationary_ms = still_since_ms_ ? elapsed(now, still_since_ms_) : 0;

    // Personality has memory, but forgives. Roughly a minute of calm erases a
    // serious shaking episode.
    annoyance_ = std::max(0.0F, annoyance_ - dt * 0.035F);

    // Freefall and landing are safety reactions and outrank every playful
    // interpretation. Require two samples at 50 Hz to reject an I2C glitch.
    if (accel_g < 0.28F) {
        if (freefall_since_ms_ == 0) freefall_since_ms_ = now;
        if (!in_freefall_ && elapsed(now, freefall_since_ms_) >= 35) {
            in_freefall_ = true;
            freefall_started_ms_ = now;
            return emit(MotionSituation::Freefall, 1.0F, now, 1500);
        }
    } else {
        freefall_since_ms_ = 0;
    }
    if (in_freefall_) {
        if (accel_g > 1.75F || linear_g > 1.20F) {
            in_freefall_ = false;
            annoyance_ = std::min(3.0F, annoyance_ + 0.8F);
            return emit(MotionSituation::HardLanding,
                        std::max(accel_g / 4.0F, linear_g / 2.5F), now, 1800);
        }
        if (elapsed(now, freefall_started_ms_) > 900) in_freefall_ = false;
    }

    if (sleeping_ && meaningful_motion) {
        sleeping_ = false;
        boredom_stage_ = 0;
        idle_since_ms_ = now;
        return emit(MotionSituation::WakeFromSleep,
                    std::max(linear_g / 0.6F, gyro_dps / 180.0F), now, 5000);
    }

    // A real spin is dominated by angular velocity for several consecutive
    // samples. It is kept separate from shaking because its visual punchline
    // is dizziness, not irritation.
    if (gyro_dps > 360.0F) {
        if (high_gyro_since_ms_ == 0) high_gyro_since_ms_ = now;
        if (elapsed(now, high_gyro_since_ms_) >= 280) {
            high_gyro_since_ms_ = now;
            dizzy_pending_ = true;
            return emit(context.music ? MotionSituation::MusicDance : MotionSituation::Spin,
                        gyro_dps / 900.0F, now, 2500);
        }
    } else if (gyro_dps < 220.0F) {
        high_gyro_since_ms_ = 0;
    }

    const float shake_input = (linear_g > 0.32F ? (linear_g - 0.32F) * 2.6F : 0.0F) +
                              (gyro_dps > 95.0F ? (gyro_dps - 95.0F) / 260.0F : 0.0F) +
                              (jerk_g_s > 5.0F ? std::min(1.0F, jerk_g_s / 18.0F) : 0.0F);
    shake_score_ = std::max(0.0F, shake_score_ - dt * 0.62F);
    if (shake_input > 0.0F) {
        shake_score_ = std::min(4.0F, shake_score_ + shake_input * dt);
        shake_last_active_ms_ = now;
        if (wiggle_since_ms_ == 0) wiggle_since_ms_ = now;
    } else if (linear_g < 0.12F && gyro_dps < 35.0F) {
        wiggle_since_ms_ = 0;
    }

    if (context.music && shake_score_ > 0.48F) {
        shake_score_ *= 0.55F;
        return emit(MotionSituation::MusicDance, std::max(linear_g, gyro_dps / 500.0F),
                    now, 3000);
    }
    if (shake_score_ > 1.18F) {
        shake_score_ *= 0.42F;
        const bool repeated = annoyance_ >= 1.25F;
        annoyance_ = std::min(3.0F, annoyance_ + (repeated ? 0.75F : 0.9F));
        dizzy_pending_ = true;
        return emit(repeated ? MotionSituation::RepeatedShake : MotionSituation::Shake,
                    std::max(linear_g / 1.4F, gyro_dps / 600.0F), now, 1300);
    }
    if (wiggle_since_ms_ != 0 && elapsed(now, wiggle_since_ms_) > 320 &&
        shake_score_ > 0.30F && shake_score_ < 0.85F) {
        wiggle_since_ms_ = now;
        shake_score_ *= 0.45F;
        return emit(MotionSituation::GentleWiggle,
                    std::max(linear_g / 0.65F, gyro_dps / 260.0F), now, 2200);
    }
    if (dizzy_pending_ && elapsed(now, shake_last_active_ms_) > 480 &&
        linear_g < 0.10F && gyro_dps < 28.0F) {
        dizzy_pending_ = false;
        return emit(MotionSituation::DizzyAfterShake, annoyance_ / 2.0F, now, 2200);
    }

    // Slow reversals are rocking/cradling. Count direction changes in the
    // strongest in-plane gyro axis rather than treating one smooth tilt as a
    // complete rocking gesture.
    const float rocking_axis = std::fabs(sample.gx) >= std::fabs(sample.gy)
                                   ? sample.gx : sample.gy;
    const int8_t sign = rocking_axis > 16.0F ? 1 : (rocking_axis < -16.0F ? -1 : 0);
    if (sign != 0 && rock_sign_ != 0 && sign != rock_sign_ &&
        elapsed(now, last_rock_flip_ms_) > 160) {
        if (rock_window_started_ms_ == 0 || elapsed(now, rock_window_started_ms_) > 2800) {
            rock_window_started_ms_ = now;
            rock_reversals_ = 0;
        }
        ++rock_reversals_;
        last_rock_flip_ms_ = now;
    } else if (sign != 0 && rock_sign_ == 0) {
        last_rock_flip_ms_ = now;
        rock_window_started_ms_ = now;
    }
    if (sign != 0) rock_sign_ = sign;
    if (rock_reversals_ >= 3 && linear_g < 0.30F && gyro_dps < 170.0F) {
        rock_reversals_ = 0;
        annoyance_ = std::max(0.0F, annoyance_ - 0.45F);
        return emit(context.music ? MotionSituation::MusicDance : MotionSituation::Rocking,
                    gyro_dps / 170.0F, now, 4000);
    }

    // Confirmed posture transitions are deliberately after dynamic gestures:
    // an energetic shake that crosses vertical should remain a shake.
    if (posture_changed_) {
        if (posture_ == MotionPosture::FaceDown) {
            annoyance_ = std::min(3.0F, annoyance_ + 0.30F);
            return emit(MotionSituation::FaceDown, 0.70F, now, 6000);
        }
        if (posture_ == MotionPosture::UpsideDown) {
            return emit(MotionSituation::UpsideDown, 0.80F, now, 5000);
        }
        if (posture_ == MotionPosture::SideLeft || posture_ == MotionPosture::SideRight) {
            return emit(MotionSituation::Sideways, 0.55F, now, 5000);
        }
        if (posture_ == MotionPosture::Upright) {
            was_carried_ = true;
            return emit(MotionSituation::UprightAlert,
                        std::max(0.45F, motion_ema_ / 0.30F), now, 4000);
        }
        if (posture_ == MotionPosture::FaceUp &&
            previous_posture_ != MotionPosture::Unknown && was_carried_) {
            was_carried_ = false;
            pickup_armed_ = false;
            return emit(MotionSituation::SetDown, 0.45F, now, 2500);
        }
    }

    if (posture_ == MotionPosture::FaceUp && still_since_ms_ != 0 &&
        elapsed(now, still_since_ms_) > 1100) {
        pickup_armed_ = true;
    }
    if (pickup_armed_ && meaningful_motion && posture_ == MotionPosture::FaceUp &&
        linear_g > 0.16F) {
        pickup_armed_ = false;
        was_carried_ = true;
        return emit(MotionSituation::PickedUp, linear_g / 0.65F, now, 3000);
    }

    const bool carried_motion = posture_ != MotionPosture::FaceUp &&
                                motion_ema_ > 0.035F && motion_ema_ < 0.35F &&
                                gyro_ema_ > 7.0F && gyro_ema_ < 145.0F;
    if (carried_motion) {
        if (carried_since_ms_ == 0) carried_since_ms_ = now;
        if (elapsed(now, carried_since_ms_) > 2400) {
            carried_since_ms_ = now;
            was_carried_ = true;
            return emit(MotionSituation::Carried, motion_ema_ / 0.35F, now, 20000);
        }
    } else {
        carried_since_ms_ = 0;
    }

    // A short impulse which settles before it accumulates shake energy is a
    // bump. Hold it pending briefly so vigorous shaking wins instead.
    if (!in_freefall_ && (linear_g > 0.58F || jerk_g_s > 8.0F)) {
        if (bump_started_ms_ == 0) bump_started_ms_ = now;
        bump_peak_ = std::max(bump_peak_, std::max(linear_g, std::fabs(accel_g - 1.0F)));
    }
    if (bump_started_ms_ != 0 && elapsed(now, bump_started_ms_) > 80 &&
        linear_g < 0.14F && gyro_dps < 45.0F) {
        const float peak = bump_peak_;
        bump_started_ms_ = 0;
        bump_peak_ = 0.0F;
        if (elapsed(now, shake_last_active_ms_) > 250) {
            return emit(MotionSituation::Bump, peak / 1.5F, now, 1800);
        }
    } else if (bump_started_ms_ != 0 && elapsed(now, bump_started_ms_) > 500) {
        bump_started_ms_ = 0;
        bump_peak_ = 0.0F;
    }

    // Boredom is a posture + runtime situation, not merely a lack of IMU
    // samples. Conversation, music, charging, or any physical interaction
    // resets this clock through the context and note_interaction().
    const bool table_idle = context.idle && posture_ == MotionPosture::FaceUp && still;
    if (!table_idle) {
        idle_since_ms_ = now;
        boredom_stage_ = 0;
    } else {
        if (idle_since_ms_ == 0) idle_since_ms_ = now;
        const uint32_t idle_ms = elapsed(now, idle_since_ms_);
        if (context.low_battery && boredom_stage_ < 1 && idle_ms >= 30000) {
            boredom_stage_ = 1;
            return emit(MotionSituation::LowBatteryTired, 0.65F, now, 120000);
        }
        if (context.charging && boredom_stage_ < 1 && idle_ms >= 45000) {
            boredom_stage_ = 1;
            return emit(MotionSituation::ChargingRest, 0.55F, now, 180000);
        }
        if (!context.charging && boredom_stage_ < 1 && idle_ms >= 60000) {
            boredom_stage_ = 1;
            return emit(MotionSituation::Bored, 0.40F, now, 120000);
        }
        if (!context.charging && boredom_stage_ < 2 && idle_ms >= 180000) {
            boredom_stage_ = 2;
            return emit(MotionSituation::VeryBored, 0.72F, now, 240000);
        }
        if (boredom_stage_ < 3 && idle_ms >= 600000) {
            boredom_stage_ = 3;
            sleeping_ = true;
            return emit(MotionSituation::Dozing, 0.85F, now, 600000);
        }
    }

    snapshot_.annoyance = annoyance_;
    return {};
}

const char *motion_posture_name(MotionPosture posture) {
    switch (posture) {
        case MotionPosture::FaceUp: return "face_up";
        case MotionPosture::FaceDown: return "face_down";
        case MotionPosture::Upright: return "upright";
        case MotionPosture::UpsideDown: return "upside_down";
        case MotionPosture::SideLeft: return "side_left";
        case MotionPosture::SideRight: return "side_right";
        case MotionPosture::Unknown:
        default: return "unknown";
    }
}

const char *motion_situation_name(MotionSituation situation) {
    switch (situation) {
        case MotionSituation::PickedUp: return "picked_up";
        case MotionSituation::UprightAlert: return "upright_alert";
        case MotionSituation::FaceDown: return "face_down";
        case MotionSituation::UpsideDown: return "upside_down";
        case MotionSituation::Sideways: return "sideways";
        case MotionSituation::GentleWiggle: return "gentle_wiggle";
        case MotionSituation::Shake: return "shake";
        case MotionSituation::RepeatedShake: return "repeated_shake";
        case MotionSituation::DizzyAfterShake: return "dizzy_after_shake";
        case MotionSituation::Rocking: return "rocking";
        case MotionSituation::Spin: return "spin";
        case MotionSituation::Carried: return "carried";
        case MotionSituation::Bump: return "bump";
        case MotionSituation::Freefall: return "freefall";
        case MotionSituation::HardLanding: return "hard_landing";
        case MotionSituation::SetDown: return "set_down";
        case MotionSituation::Bored: return "bored";
        case MotionSituation::VeryBored: return "very_bored";
        case MotionSituation::Dozing: return "dozing";
        case MotionSituation::ChargingRest: return "charging_rest";
        case MotionSituation::MusicDance: return "music_dance";
        case MotionSituation::LowBatteryTired: return "low_battery_tired";
        case MotionSituation::WakeFromSleep: return "wake_from_sleep";
        case MotionSituation::None:
        case MotionSituation::Count:
        default: return "none";
    }
}

}  // namespace kiki
