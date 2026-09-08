#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace kiki {

// Gravity-relative posture. The QMI8658 has no magnetometer, so this describes
// how the screen is being held, never an absolute compass heading.
enum class MotionPosture : uint8_t {
    Unknown = 0,
    FaceUp,
    FaceDown,
    Upright,
    UpsideDown,
    SideLeft,
    SideRight,
};

// Semantic physical situations consumed by the face/audio reaction layer.
// None is deliberately zero so a default-constructed decision is inert.
enum class MotionSituation : uint8_t {
    None = 0,
    PickedUp,
    UprightAlert,
    FaceDown,
    UpsideDown,
    Sideways,
    GentleWiggle,
    Shake,
    RepeatedShake,
    DizzyAfterShake,
    Rocking,
    Spin,
    Carried,
    Bump,
    Freefall,
    HardLanding,
    SetDown,
    Bored,
    VeryBored,
    Dozing,
    ChargingRest,
    MusicDance,
    LowBatteryTired,
    WakeFromSleep,
    Count,
};

struct MotionSample {
    // Accelerometer values in g and gyroscope values in degrees/second.
    float ax = 0.0F;
    float ay = 0.0F;
    float az = -1.0F;
    float gx = 0.0F;
    float gy = 0.0F;
    float gz = 0.0F;
    uint32_t timestamp_ms = 0;
};

struct MotionContext {
    bool idle = false;
    bool music = false;
    bool charging = false;
    bool low_battery = false;
};

struct MotionDecision {
    MotionSituation situation = MotionSituation::None;
    MotionPosture posture = MotionPosture::Unknown;
    float intensity = 0.0F;

    explicit operator bool() const { return situation != MotionSituation::None; }
};

struct MotionClassifierSnapshot {
    MotionPosture posture = MotionPosture::Unknown;
    MotionSituation last_situation = MotionSituation::None;
    float accel_g = 1.0F;
    float linear_g = 0.0F;
    float gyro_dps = 0.0F;
    float annoyance = 0.0F;
    uint32_t stationary_ms = 0;
    uint32_t event_count = 0;
};

class MotionClassifier {
public:
    MotionDecision update(const MotionSample &sample, const MotionContext &context);
    void note_interaction(uint32_t now_ms);
    const MotionClassifierSnapshot &snapshot() const { return snapshot_; }

private:
    MotionDecision emit(MotionSituation situation, float intensity, uint32_t now_ms,
                        uint32_t cooldown_ms = 0);
    MotionPosture raw_posture(float gx, float gy, float gz) const;
    void update_posture(MotionPosture raw, uint32_t now_ms);

    bool initialized_ = false;
    uint32_t previous_ms_ = 0;
    float gravity_x_ = 0.0F;
    float gravity_y_ = 0.0F;
    float gravity_z_ = -1.0F;
    float previous_accel_g_ = 1.0F;
    float motion_ema_ = 0.0F;
    float gyro_ema_ = 0.0F;

    MotionPosture posture_ = MotionPosture::Unknown;
    MotionPosture posture_candidate_ = MotionPosture::Unknown;
    MotionPosture previous_posture_ = MotionPosture::Unknown;
    uint32_t posture_candidate_since_ms_ = 0;
    bool posture_changed_ = false;

    uint32_t still_since_ms_ = 0;
    uint32_t moving_since_ms_ = 0;
    uint32_t last_motion_ms_ = 0;
    uint32_t idle_since_ms_ = 0;
    uint8_t boredom_stage_ = 0;
    bool pickup_armed_ = false;
    bool was_carried_ = false;
    bool sleeping_ = false;

    float shake_score_ = 0.0F;
    float annoyance_ = 0.0F;
    uint32_t wiggle_since_ms_ = 0;
    uint32_t shake_last_active_ms_ = 0;
    bool dizzy_pending_ = false;

    uint32_t high_gyro_since_ms_ = 0;
    uint32_t carried_since_ms_ = 0;
    int8_t rock_sign_ = 0;
    uint32_t last_rock_flip_ms_ = 0;
    uint32_t rock_window_started_ms_ = 0;
    uint8_t rock_reversals_ = 0;

    uint32_t freefall_since_ms_ = 0;
    uint32_t freefall_started_ms_ = 0;
    bool in_freefall_ = false;
    float bump_peak_ = 0.0F;
    uint32_t bump_started_ms_ = 0;

    std::array<uint32_t, static_cast<std::size_t>(MotionSituation::Count)> last_emitted_ms_{};
    MotionClassifierSnapshot snapshot_{};
};

const char *motion_posture_name(MotionPosture posture);
const char *motion_situation_name(MotionSituation situation);

}  // namespace kiki
