#pragma once
#include <cmath>
#include <cstdint>

namespace kiki {
// Raw 50 Hz IMU ladder, independent of animation cooldowns and network queues.
// Heuristic candidate only; it is not a clinically validated fall detector.
class FallDetector {
 public:
    bool update(uint32_t ms, float g, bool worn) {
        if (worn) { last_worn_ = ms; have_worn_ = true; }
        if (!std::isfinite(g)) return false;
        if (!have_worn_ || ms - last_worn_ > 30000) {
            low_ = armed_ = false;
            return false;
        }
        if (cooldown_ && ms - triggered_ < 60000) return false;
        cooldown_ = false;
        // A wrist fall can have only one 20 ms low-g sample; requiring 80 ms
        // missed short, real transitions. The user-facing check-in is the
        // false-positive guard, so favour detecting and asking over silence.
        if (g < 0.70F) {
            if (!low_) { low_ = true; low_since_ = ms; }
            armed_ = true;
            armed_at_ = ms;
        } else {
            low_ = false;
        }
        if (armed_ && ms - armed_at_ > 1200) armed_ = false;
        // A very hard impact while recently worn also merits a check-in even
        // if the 50 Hz sampler missed the preceding low-g instant.
        if ((armed_ && g >= 1.8F) || g >= 3.0F) {
            armed_ = false;
            cooldown_ = true;
            triggered_ = ms;
            return true;
        }
        return false;
    }
 private:
    bool low_ = false, armed_ = false, have_worn_ = false, cooldown_ = false;
    uint32_t low_since_ = 0, armed_at_ = 0, last_worn_ = 0, triggered_ = 0;
};
} // namespace kiki
