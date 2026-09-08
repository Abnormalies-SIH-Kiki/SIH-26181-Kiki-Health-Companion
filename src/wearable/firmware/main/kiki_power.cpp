#include "kiki_power.hpp"

#include <cstdio>

#include "bsp/esp-bsp.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_power";

// --- AXP2101 access -------------------------------------------------------
// Handle kept so telemetry can read the PMIC live. Only registers in the
// charger/ADC domain (0x30, 0x34-0x3B, 0x60s) are ever touched; the rail
// voltages live at 0x80+ and are never written, because a wrong value there
// over-volts the ESP32.
i2c_master_dev_handle_t g_pmic = nullptr;

// PMU_STATUS1. Bit 5 is VBUS good; bit 3 is battery present. The empty-board
// dump read 0x20 -- VBUS good, no cell -- which is consistent with both.
constexpr uint8_t kStatus1VbusGood = 1 << 5;
constexpr uint8_t kStatus1BatteryPresent = 1 << 3;

// CHARGE_GAUGE_WDT_CTRL bit 1 enables the main cell-battery charger. Keep a
// five-point hysteresis below the configured ceiling: the AXP2101 gauge moves
// by whole percentages and can otherwise chatter between enabled and disabled
// at the boundary. The PMIC retains this bit across an ESP32 reset, so a board
// that was switched off at the ceiling remains protected on its next boot and
// is re-enabled once the cell has genuinely fallen below the resume point.
constexpr uint8_t kChargeControlReg = 0x18;
constexpr uint8_t kCellChargeEnable = 1 << 1;
constexpr int kChargeStopPercent = CONFIG_KIKI_CHARGE_LIMIT_PERCENT;
constexpr int kChargeResumePercent = CONFIG_KIKI_CHARGE_LIMIT_PERCENT - 5;

// AXP2101 register 0x62, constant-current charge limit. The steps are 25 mA up
// to 200 mA and 100 mA above it; the board's power-on default is code 8, which
// is the 200 mA reading the empty-board dump showed in the handoff.
constexpr uint16_t kChargeCurrentMa[] = {
    0, 25, 50, 75, 100, 125, 150, 175, 200, 300, 400, 500, 600, 700, 800, 900, 1000,
};

const char *const kChargerPhase[] = {
    "trickle", "pre-charge", "constant-current", "constant-voltage",
    "done", "not-charging", "?", "?",
};

// Serialises telemetry, settings and shutdown access. Two tasks interleaving a
// transmit and a transmit_receive on the same device would hand one of them the
// other's byte.
SemaphoreHandle_t g_pmic_lock = nullptr;

struct PmicLock {
    PmicLock() {
        if (g_pmic_lock) xSemaphoreTake(g_pmic_lock, portMAX_DELAY);
    }
    ~PmicLock() {
        if (g_pmic_lock) xSemaphoreGive(g_pmic_lock);
    }
};

bool pmic_read(uint8_t reg, uint8_t *out) {
    PmicLock lock;
    return g_pmic && i2c_master_transmit_receive(g_pmic, &reg, 1, out, 1, 100) == ESP_OK;
}

bool pmic_write(uint8_t reg, uint8_t value) {
    PmicLock lock;
    const uint8_t frame[2] = {reg, value};
    return g_pmic && i2c_master_transmit(g_pmic, frame, 2, 100) == ESP_OK;
}

// The ADC results are 14-bit, high byte first, already in millivolts. The two
// bytes are read under one lock: sampled separately, a rotation between them
// yields a value that was never on the pin.
uint16_t pmic_mv(uint8_t reg) {
    PmicLock lock;
    if (!g_pmic) return 0;
    uint8_t hi = 0, lo = 0;
    const uint8_t lo_reg = static_cast<uint8_t>(reg + 1);
    if (i2c_master_transmit_receive(g_pmic, &reg, 1, &hi, 1, 100) != ESP_OK) return 0;
    if (i2c_master_transmit_receive(g_pmic, &lo_reg, 1, &lo, 1, 100) != ESP_OK) return 0;
    return static_cast<uint16_t>(((hi & 0x3F) << 8) | lo);
}

// Fallback for the short window in which the AXP2101 fuel gauge has not
// converged after a cell is attached. A voltage curve is only approximate under
// load, so 0xA4 remains authoritative whenever it returns a sane percentage.
int percent_from_voltage(uint16_t mv) {
    static const struct {
        uint16_t mv;
        uint8_t percent;
    } kCurve[] = {
        {4200, 100}, {4060, 90}, {3980, 80}, {3920, 70}, {3870, 60}, {3820, 50},
        {3790, 40},  {3770, 30}, {3740, 20}, {3680, 10}, {3450, 5},  {3000, 0},
    };
    if (mv >= kCurve[0].mv) return 100;
    for (size_t i = 1; i < sizeof(kCurve) / sizeof(kCurve[0]); ++i) {
        if (mv >= kCurve[i].mv) {
            // Linear between the two bracketing points.
            const int span_mv = kCurve[i - 1].mv - kCurve[i].mv;
            const int span_pc = kCurve[i - 1].percent - kCurve[i].percent;
            return kCurve[i].percent + (mv - kCurve[i].mv) * span_pc / span_mv;
        }
    }
    return 0;
}


// Rounds DOWN to a step the chip can express, so a configured value is a
// ceiling and never an under-estimate of what the cell will actually see.
void set_charge_current(int wanted_ma) {
    constexpr size_t kCodes = sizeof(kChargeCurrentMa) / sizeof(kChargeCurrentMa[0]);
    size_t code = 0;
    for (size_t i = 0; i < kCodes; ++i) {
        if (kChargeCurrentMa[i] <= wanted_ma) code = i;
    }
    uint8_t reg = 0;
    if (!pmic_read(0x62, &reg)) return;
    const size_t was = reg & 0x1F;
    const uint8_t updated = static_cast<uint8_t>((reg & ~0x1F) | code);
    if (updated == reg) {
        ESP_LOGI(kTag, "pmic: charge current already %umA", kChargeCurrentMa[code]);
        return;
    }
    if (pmic_write(0x62, updated)) {
        // `was` can only exceed the table if the read was garbage, which is
        // worth seeing rather than indexing off the end of the array for.
        ESP_LOGW(kTag, "pmic: charge current %dmA -> %umA (0x62 %02x -> %02x)",
                 was < kCodes ? kChargeCurrentMa[was] : -1, kChargeCurrentMa[code],
                 reg, updated);
    }
}

void apply_charge_ceiling(PowerState *state) {
    if (!state || !state->present || state->percent < 0) return;

    uint8_t control = 0;
    if (!pmic_read(kChargeControlReg, &control)) return;
    bool enabled = (control & kCellChargeEnable) != 0;

    if (state->percent >= kChargeStopPercent && enabled) {
        const uint8_t updated = static_cast<uint8_t>(control & ~kCellChargeEnable);
        if (pmic_write(kChargeControlReg, updated)) {
            enabled = false;
            ESP_LOGW(kTag,
                     "pmic: charge ceiling reached at %d%%; charger disabled "
                     "(0x18 %02x -> %02x)",
                     state->percent, control, updated);
        }
    } else if (state->percent <= kChargeResumePercent && !enabled) {
        const uint8_t updated = static_cast<uint8_t>(control | kCellChargeEnable);
        if (pmic_write(kChargeControlReg, updated)) {
            enabled = true;
            ESP_LOGW(kTag,
                     "pmic: battery at %d%%; charger re-enabled "
                     "(0x18 %02x -> %02x)",
                     state->percent, control, updated);
        }
    }

    if (!enabled) {
        state->charge_limited = true;
        state->charging = false;
        state->charger = "charge-limited";
    }
}

}  // namespace

bool power_read(PowerState *out) {
    if (!out) return false;
    *out = PowerState{};
    uint8_t s0 = 0, s1 = 0;
    if (!pmic_read(0x00, &s0) || !pmic_read(0x01, &s1)) return false;

    out->pmic = true;
    out->usb = (s0 & kStatus1VbusGood) != 0;
    out->present = (s0 & kStatus1BatteryPresent) != 0;
    const uint8_t phase = s1 & 0x07;
    out->charger = kChargerPhase[phase];
    out->charger_phase = phase;
    // Phases 0-3 are the four charging phases; 4 is "done" and 5 "not
    // charging", neither of which is putting current in.
    out->charging = phase <= 3;
    out->vbat_mv = pmic_mv(0x34);
    out->vbus_mv = pmic_mv(0x38);
    out->vsys_mv = pmic_mv(0x3A);

    if (out->present) {
        // With normal battery detection enabled, 0xA4 tracks the cell across
        // both USB charging and battery operation.
        uint8_t gauge = 0;
        if (pmic_read(0xA4, &gauge) && gauge <= 100) {
            out->percent = gauge;
        } else if (out->vbat_mv > 2500) {
            out->percent = percent_from_voltage(out->vbat_mv);
        }
    }
    apply_charge_ceiling(out);
    return true;
}

bool power_shutdown() {
    // 0x10 is PMU common config; bit 0 is the soft power-off. It is in the
    // control domain, not the rail-voltage domain at 0x80+ that can over-volt
    // the ESP32, so this is the same safe register class as the TS fix.
    uint8_t cfg = 0;
    if (!pmic_read(0x10, &cfg)) {
        ESP_LOGE(kTag, "pmic: cannot read 0x10; not shutting down");
        return false;
    }
    ESP_LOGW(kTag, "pmic: soft power off (0x10 %02x -> %02x)", cfg, cfg | 0x01);
    return pmic_write(0x10, static_cast<uint8_t>(cfg | 0x01));
}

void power_log(const char *when) {
    PowerState state{};
    if (!power_read(&state)) return;
    ESP_LOGW(kTag,
             "pmic %s: vbat=%umV vbus=%umV vsys=%umV charger=%s vbus_good=%d "
             "battery=%d level=%d%%",
             when, state.vbat_mv, state.vbus_mv, state.vsys_mv, state.charger,
             state.usb, state.present, state.percent);
}

// Scans the I2C bus and dumps the AXP2101's registers so the
// charger's real state can be read off the chip instead of inferred from
// documentation. Nothing here writes: the AXP2101 also sets the board's rail
// voltages, so a careless write can over-volt the ESP32 and destroy it. Any
// configuration change goes in only after this dump has been read against the
// datasheet.
void power_init() {
    if (bsp_i2c_init() != ESP_OK) {
        ESP_LOGW(kTag, "pmic probe: no I2C");
        return;
    }
    i2c_master_bus_handle_t bus = bsp_i2c_get_handle();
    if (!bus) {
        ESP_LOGW(kTag, "pmic probe: no bus handle");
        return;
    }

    char found[128];
    int n = 0;
    for (uint8_t addr = 0x08; addr < 0x78 && n < 100; ++addr) {
        if (i2c_master_probe(bus, addr, 50) == ESP_OK) {
            n += std::snprintf(found + n, sizeof(found) - n, "0x%02x ", addr);
        }
    }
    found[n] = '\0';
    ESP_LOGW(kTag, "i2c scan: %s", n ? found : "(nothing found)");

    i2c_device_config_t cfg = {};
    cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    cfg.device_address = 0x34;  // AXP2101
    cfg.scl_speed_hz = 100000;
    i2c_master_dev_handle_t dev = nullptr;
    if (i2c_master_bus_add_device(bus, &cfg, &dev) != ESP_OK || !dev) {
        ESP_LOGW(kTag, "pmic probe: could not address 0x34");
        return;
    }
    // 0xA0 covers the gauge registers as well as the 0x00-0x7F block the
    // original probe dumped, so 0xA4's behaviour is on the record too.
    for (uint16_t base = 0x00; base < 0xB0; base += 16) {
        char line[64];
        int p = 0;
        for (int i = 0; i < 16; ++i) {
            uint8_t reg = static_cast<uint8_t>(base + i);
            uint8_t value = 0;
            if (i2c_master_transmit_receive(dev, &reg, 1, &value, 1, 100) != ESP_OK) {
                value = 0xEE;  // read failed, not a real register value
            }
            p += std::snprintf(line + p, sizeof(line) - p, "%02x ", value);
        }
        ESP_LOGW(kTag, "axp2101 %02x: %s", base, line);
    }

    // Before g_pmic, so no read can reach the bus unserialised.
    if (!g_pmic_lock) g_pmic_lock = xSemaphoreCreateMutex();
    g_pmic = dev;
    power_log("before");

    // 0x30 is ADC_CHANNEL_CTRL. Bit 1 enables measurement of the TS
    // (battery-temperature) pin, and this board has no thermistor fitted, so
    // with it set the charger reads an out-of-range temperature and refuses to
    // charge -- which is why the BAT pads sat at 0.3-0.8 V from new.
    // Waveshare's own code calls disableTSPinMeasure() for exactly this.
    // Bits 0/2/3 turn on the VBAT, VBUS and VSYS ADCs so the readings above
    // are real rather than stale.
    if (pmic_write(0x30, 0x0D)) {
        ESP_LOGW(kTag, "pmic: TS measurement off, VBAT/VBUS/VSYS ADC on (0x30=0d)");
    }

    // Re-asserted every boot on purpose. The PMIC holds its registers across an
    // ESP32 reset, but a full power loss returns 0x62 to the 200 mA default --
    // and 200 mA into a 1050 mAh cell is 0.19C, which is a trickle, not a
    // charge. Charge current is the one setting that has to track the cell,
    // because the chip cannot detect capacity.
    set_charge_current(CONFIG_KIKI_CHARGE_CURRENT_MA);

    // 0x68 bit 0 enables normal cell detection. The PMIC keeps this register
    // across an ESP32 or OTA reboot, so assert the production state every boot
    // in case a diagnostic image previously disabled it.
    uint8_t det = 0;
    if (pmic_read(0x68, &det)) {
        const uint8_t wanted = static_cast<uint8_t>(det | 0x01);
        if (wanted != det && pmic_write(0x68, wanted)) {
            ESP_LOGW(kTag, "pmic: battery detection enabled (0x68 %02x -> %02x)",
                     det, wanted);
        }
    }

    vTaskDelay(pdMS_TO_TICKS(500));
    power_log("after");
}

}  // namespace kiki
