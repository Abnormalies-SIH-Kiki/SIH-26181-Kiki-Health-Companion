#pragma once
#include <cstdint>

namespace kiki {

// A snapshot of the AXP2101's view of the power system.
struct PowerState {
    bool pmic = false;      // the PMIC answered at all
    bool present = false;   // a cell is attached to BAT
    bool usb = false;       // VBUS is good, i.e. running from the cable
    bool charging = false;  // the charger is actively moving current in
    bool charge_limited = false;  // firmware disabled charging at the SOC ceiling
    int percent = -1;       // 0..100, or -1 when there is no cell to measure
    uint16_t vbat_mv = 0;
    uint16_t vbus_mv = 0;
    uint16_t vsys_mv = 0;
    const char *charger = "?";  // human-readable charger state machine phase
    // The same phase as a number: 0 trickle, 1 pre-charge, 2 constant-current,
    // 3 constant-voltage, 4 done, 5 not charging. While on USB this is what the
    // percentage is derived from, because the voltage on BAT is not the cell's.
    uint8_t charger_phase = 5;
};

// Finds the AXP2101, dumps its registers once, and applies the TS-pin fix that
// makes charging work at all on this board. Safe to call before Wi-Fi.
void power_init();

// Fills `out` from the chip. False when the PMIC was never found, in which case
// `out` is left as a default PowerState.
bool power_read(PowerState *out);

// One telemetry line, for the serial log.
void power_log(const char *when);

// Tells the AXP2101 to cut every rail (soft power-off, register 0x10 bit 0).
// This is a real off -- ESP32, panel, codecs and all -- not a sleep, so it does
// not return. False means the PMIC refused the write and Kiki is still running.
//
// Coming back needs VBUS or the PWR button: the board's own PWRON_STATUS
// (0x20 = 0x04, "VBUS insert") is proof the USB path works.
bool power_shutdown();

}  // namespace kiki
