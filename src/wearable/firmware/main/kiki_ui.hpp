#pragma once

#include <cstdint>

#include "audio_pipeline.hpp"
namespace kiki {

void ui_start();

// Re-loads the face screen after a setup flow has borrowed the display.
void ui_show();

// The runtime settings menu, reached by holding the centre of the panel.
// This is ir_controls.py's both-sensor settings gesture.
void ui_open_settings();

// Runtime state name (idle / listening / thinking / speaking / tool / ...).
// Drives the crab face exactly as oled_display.set_state does on the Pi.
void ui_set_state(const char *state);
void ui_set_state_detail(const char *state, const char *detail);

// One inline <oled:...> mood selected by the speaking model.
void ui_set_expression(const char *name);

// Temporary offline physical reaction. The gateway-owned runtime state remains
// the base state and is restored after duration_ms. Ordinary motion only
// overlays idle/music/disconnected; critical drop/impact reactions may briefly
// overlay an active state, but their sound still refuses to mix with speech.
bool ui_play_motion_reaction(const char *state, const char *detail,
                             LocalEffect effect, uint32_t duration_ms,
                             bool critical = false);

// The two mirrored 16x2 LCD rows, rendered large. Everything the Pi's
// lcd_display.py would have committed arrives here.
void ui_set_lcd(const char *line1, const char *line2);

// The full sentence Kiki is speaking right now, timed by the gateway to land
// with the audio. Rendered across the whole caption area at a font chosen to
// fit it, rather than trimmed to a row.
void ui_set_speech(const char *text);

// Your speech (passive=false) or ambient capture (passive=true), already
// romanized by the gateway.
void ui_set_transcript(const char *text, bool passive);

// Transcript / streaming reply text, when the gateway has no LCD row to give.
void ui_set_text(const char *text);
// Display-only cached environment; never affects voice state or model context.
void ui_set_dashboard_environment(const char *text);
void ui_set_dashboard_details(const char *weather, const char *alerts, const char *whatsapp,
                              int alert_count, int whatsapp_count);

// `detail` shows under "Reconnecting" -- which address is being tried, and
// whether the clock is set. On a board with no serial console this second row
// is the only diagnostic there is, and "Reconnecting" on its own says nothing
// about which of half a dozen causes is the live one.
void ui_set_connected(bool connected, const char *detail = "");
// True while the Hold-to-talk button is down.
bool ui_talk_held();

void ui_set_volume_percent(int percent);
void ui_set_gain(float gain);
void ui_set_latency(int milliseconds);

// Battery gauge in the top row. `percent` is -1 when no cell is attached, in
// which case only the USB plug shows. `on_usb` is VBUS present -- the
// difference between "running off the cable" and "running off the cell" --
// and `charging` is the charger actually moving current in.
void ui_set_battery(int percent, bool on_usb, bool charging);

// Panel brightness, 5..100%. Applied immediately and remembered across
// restarts; `ui_brightness()` is what the settings menu adjusts from.
void ui_set_brightness(int percent);
int ui_brightness();

// Show/hide the transport row and set the play/pause glyph.
void ui_set_media(bool loaded, bool paused, const char *title = nullptr);

// Dance mode. The panel stops being a status screen and becomes a stage: every
// row, button and the resting face are hidden, and kiki_dance.cpp owns the
// pixels until ui_exit_dance(). Called by the dance itself, not by the
// gateway, so the two can never disagree about what is on screen.
void ui_enter_dance(const char *mood);
void ui_exit_dance();

}  // namespace kiki
