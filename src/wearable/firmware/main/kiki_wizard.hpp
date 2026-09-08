#pragma once

#include "cJSON.h"

namespace kiki {

// The boot wizard, ported from KikiFast's core/startup_config.py.
//
// On the Pi this runs before any service starts, on the 16x2 LCD. Here the
// answers live in config.json on the laptop, so the board cannot ask them
// alone: the gateway sends the questions and their current values once the
// session is up, and the board sends the choices back.
//
// Offered once per boot and auto-declined after 20 seconds, exactly like the Pi,
// so an unattended power-on never stalls waiting for someone to answer.
void wizard_offer(const cJSON *options);

bool wizard_active();

}  // namespace kiki
