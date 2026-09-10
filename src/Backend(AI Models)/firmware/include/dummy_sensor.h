// dummy_sensor.h -- on-device synthetic sensor generator.
// Mirrors data_generation/generate_dummy_data.py so the *same* activity/fall
// statistics can be exercised directly on ESP32 hardware, with no physical
// QMI8658/MAX30101 wired up. Swap this module out for real driver reads once
// hardware is attached -- everything downstream (motion gate, baselines,
// decision tree, CNN) consumes the same imu_sample_t / vitals_sample_t structs
// regardless of source.
#ifndef DUMMY_SENSOR_H
#define DUMMY_SENSOR_H

#include <math.h>
#include <stdlib.h>
#include <stdint.h>

#ifndef DUMMY_SENSOR_PI
#define DUMMY_SENSOR_PI 3.14159265358979323846f
#endif

typedef struct {
    float acc_x, acc_y, acc_z;
    float gyro_x, gyro_y, gyro_z;
} imu_sample_t;

typedef struct {
    float heart_rate;
    float spo2;
} vitals_sample_t;

typedef enum {
    SIM_RESTING, SIM_INACTIVE, SIM_WALKING, SIM_RUNNING, SIM_FALL
} sim_state_t;

static sim_state_t g_sim_state = SIM_RESTING;
static uint32_t g_sim_state_elapsed_ms = 0;
static uint32_t g_sim_state_duration_ms = 30000;
static uint32_t g_sim_fall_phase_ms = 0; // used only during SIM_FALL

static inline float randf(float lo, float hi) {
    return lo + (hi - lo) * (float)rand() / (float)RAND_MAX;
}

static inline void dummy_sensor_pick_next_state() {
    // 3% chance of a fall event when transitioning states, otherwise cycle activities.
    if (randf(0, 1) < 0.8f) {
        g_sim_state = SIM_FALL;
        g_sim_state_duration_ms = 4000;
        g_sim_fall_phase_ms = 0;
    } else {
        int r = rand() % 4;
        g_sim_state = (sim_state_t)r;
        g_sim_state_duration_ms = (uint32_t)randf(15000, 60000);
    }
    g_sim_state_elapsed_ms = 0;
}

// Call at IMU_FS_HZ (e.g. every 20ms). dt_ms = time since last call.
static inline imu_sample_t dummy_sensor_read_imu(uint32_t dt_ms) {
    g_sim_state_elapsed_ms += dt_ms;
    if (g_sim_state_elapsed_ms >= g_sim_state_duration_ms) {
        dummy_sensor_pick_next_state();
    }

    float acc_mag = 1.0f, gyro_mag = 1.0f;

    switch (g_sim_state) {
        case SIM_RESTING:  acc_mag = 1.0f + randf(-0.02f, 0.02f); gyro_mag = fabsf(1.0f + randf(-1.0f, 1.0f)); break;
        case SIM_INACTIVE: acc_mag = 1.0f + randf(-0.01f, 0.01f); gyro_mag = fabsf(0.3f + randf(-0.3f, 0.3f)); break;
        case SIM_WALKING: {
            float t = g_sim_state_elapsed_ms / 1000.0f;
            acc_mag = 1.25f + 0.12f * sinf(2 * DUMMY_SENSOR_PI * 1.8f * t) + randf(-0.1f, 0.1f);
            gyro_mag = fabsf(25.0f + 5.0f * sinf(2 * DUMMY_SENSOR_PI * 1.8f * t + 0.5f) + randf(-4.0f, 4.0f));
            break;
        }
        case SIM_RUNNING: {
            float t = g_sim_state_elapsed_ms / 1000.0f;
            acc_mag = 1.80f + 0.27f * sinf(2 * DUMMY_SENSOR_PI * 2.8f * t) + randf(-0.2f, 0.2f);
            gyro_mag = fabsf(70.0f + 12.0f * sinf(2 * DUMMY_SENSOR_PI * 2.8f * t + 0.5f) + randf(-10.0f, 10.0f));
            break;
        }
        case SIM_FALL: {
            // 3-phase signature: free-fall dip (0-350ms) -> impact spike (350-450ms) -> stillness (rest)
            g_sim_fall_phase_ms += dt_ms;
            if (g_sim_fall_phase_ms < 350) {
                acc_mag = 1.0f - (g_sim_fall_phase_ms / 350.0f) * 0.9f;
                gyro_mag = fabsf(randf(-1, 1));
            } else if (g_sim_fall_phase_ms < 450) {
                acc_mag = randf(3.0f, 8.0f);
                gyro_mag = randf(150.0f, 400.0f);
            } else {
                acc_mag = 1.0f + randf(-0.02f, 0.02f);
                gyro_mag = fabsf(randf(-2.0f, 2.0f));
            }
            break;
        }
    }

    // Random unit-vector direction so acc_x/y/z, gyro_x/y/z all carry signal (matches
    // the Python generator's mag_to_axes randomized-orientation approach).
    float vx = randf(-1, 1), vy = randf(-1, 1), vz = randf(-1, 1);
    float norm = sqrtf(vx * vx + vy * vy + vz * vz) + 1e-6f;
    vx /= norm; vy /= norm; vz /= norm;

    float gx = randf(-1, 1), gy = randf(-1, 1), gz = randf(-1, 1);
    float gnorm = sqrtf(gx * gx + gy * gy + gz * gz) + 1e-6f;
    gx /= gnorm; gy /= gnorm; gz /= gnorm;

    imu_sample_t s;
    s.acc_x = acc_mag * vx; s.acc_y = acc_mag * vy; s.acc_z = acc_mag * vz;
    s.gyro_x = gyro_mag * gx; s.gyro_y = gyro_mag * gy; s.gyro_z = gyro_mag * gz;
    return s;
}

// Call at VITALS_FS_HZ (e.g. every 1000ms). Vitals track the current sim state.
static inline vitals_sample_t dummy_sensor_read_vitals() {
    float hr_mean, spo2_mean;
    switch (g_sim_state) {
        case SIM_RESTING:  hr_mean = 68;  spo2_mean = 98.0f; break;
        case SIM_INACTIVE: hr_mean = 62;  spo2_mean = 97.5f; break;
        case SIM_WALKING:  hr_mean = 98;  spo2_mean = 97.0f; break;
        case SIM_RUNNING:  hr_mean = 142; spo2_mean = 96.0f; break;
        case SIM_FALL:     hr_mean = 85;  spo2_mean = 96.5f; break;
        default:           hr_mean = 70;  spo2_mean = 97.5f; break;
    }
    vitals_sample_t v;
    v.heart_rate = hr_mean + randf(-3, 3);
    v.spo2 = fminf(100.0f, fmaxf(85.0f, spo2_mean + randf(-0.4f, 0.4f)));
    return v;
}

#endif // DUMMY_SENSOR_H
