#pragma once
#include "cJSON.h"
namespace kiki {
// WebSocket commands only. Stop/disconnect also terminates the local visual.
void instructor_command(const cJSON *root);
void instructor_stop();
bool instructor_active();
}
