#pragma once

#include <cstddef>
#include <cstdint>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace kiki {

esp_err_t gateway_client_start();
bool gateway_client_connected();

// Websocket sessions lost since boot. A link that has already dropped one is a
// link worth being careful with -- see the uplink gating in app_main.cpp.
uint32_t gateway_client_socket_drops();

// True when the live connection is a public one rather than the LAN. The
// remote link has a ~450 ms round trip, so anything bandwidth-hungry has to
// behave differently on it.
bool gateway_client_is_remote();

// "lan" | "cloud" | "backup" -- which address is in use. Sent in `hello` so the
// gateway log shows the board moving between them.
const char *gateway_client_link_name();
esp_err_t gateway_client_send_mic(const int16_t *samples, size_t count,
                                  uint32_t sample_rate, uint8_t flags,
                                  uint32_t sequence, int64_t timestamp_us);
// Queues the event and returns immediately -- it never touches the socket, so
// it is safe from an LVGL touch callback, the motion task or the log tap. The
// tx task drains urgent control, then ordinary control, then bulk, then one
// microphone frame, so a button press overtakes a backlog of audio.
esp_err_t gateway_client_send_event(const char *type, const char *extra_json = nullptr);

// Sends at most one queued control event, highest priority first. The tx task
// calls this until it returns false before it sends any audio.
bool gateway_client_flush_events();

// Who to wake when an event is queued.
void gateway_client_set_tx_task(TaskHandle_t task);

// The slowest of the recent sends. While a send is in flight the client mutex
// is held, so nothing is read from the socket and no keepalive can go out --
// which makes this the board's only early warning that the uplink is failing.
uint32_t gateway_client_worst_send_ms();

uint32_t gateway_client_tx_dropped();
uint32_t gateway_client_rx_dropped();

// Smallest stack headroom kiki_net_rx has ever had, in bytes. Reported in
// device_stats because the Opus decoder lives on that stack.
size_t gateway_client_rx_stack_free();

// Opus decode health. See decode_opus_to_playback().
uint32_t gateway_client_opus_decode_us_max();
uint32_t gateway_client_opus_frames();
uint32_t gateway_client_opus_pcm_per_frame();

}  // namespace kiki
