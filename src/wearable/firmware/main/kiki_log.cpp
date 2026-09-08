#include "kiki_log.hpp"

#include <atomic>
#include <cstdio>
#include <cstring>

#include "audio_pipeline.hpp"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "gateway_client.hpp"

namespace kiki {
namespace {

// 16 x 144 bytes is a little over 2 KB of internal RAM. Internal RAM is the
// scarce pool on this board -- the display's DMA buffers already fail to
// allocate under TLS pressure -- so this stays deliberately small. Losing old
// log lines is fine; losing a display flush is not.
constexpr int kLines = 16;
constexpr int kLineLength = 144;

QueueHandle_t g_queue = nullptr;
vprintf_like_t g_previous = nullptr;
// Reentrancy guard: a failing send inside the sender would log, which would
// queue, which would send. The hook must never run inside itself.
std::atomic<bool> g_sending{false};

int log_hook(const char *format, va_list args) {
    // The serial console keeps working exactly as before; this only adds a
    // copy. Losing the local log to gain a remote one would be a bad trade.
    const int written = g_previous ? g_previous(format, args) : vprintf(format, args);
    if (!g_queue || g_sending.load()) return written;

    char line[kLineLength];
    va_list copy;
    va_copy(copy, args);
    const int n = vsnprintf(line, sizeof(line), format, copy);
    va_end(copy);
    if (n <= 0) return written;

    // Trailing newlines and the colour escapes ESP_LOG adds are noise once the
    // line is a JSON string on the far end.
    size_t length = strnlen(line, sizeof(line) - 1);
    while (length && (line[length - 1] == '\n' || line[length - 1] == '\r')) {
        line[--length] = '\0';
    }
    if (length == 0) return written;

    // Never block a logging call. If the queue is full the oldest line goes,
    // because the newest one is the one describing what is happening now.
    if (xQueueSend(g_queue, line, 0) != pdTRUE) {
        char discard[kLineLength];
        xQueueReceive(g_queue, discard, 0);
        xQueueSend(g_queue, line, 0);
    }
    return written;
}

void sender_task(void *) {
    char line[kLineLength];
    char previous[kLineLength] = "";
    int repeats = 0;
    while (true) {
        if (xQueueReceive(g_queue, line, portMAX_DELAY) != pdTRUE) continue;
        // A driver failing in a loop -- the display's ESP_ERR_NO_MEM fires
        // every frame -- would otherwise be the only thing this channel ever
        // carries, at the rate limit, forever.
        if (strncmp(line, previous, sizeof(previous)) == 0) {
            ++repeats;
            continue;
        }
        if (repeats > 0) {
            char note[kLineLength];
            std::snprintf(note, sizeof(note), "(last line repeated %d times)", repeats);
            repeats = 0;
            std::snprintf(previous, sizeof(previous), "%s", line);
            std::snprintf(line, sizeof(line), "%s", note);
        } else {
            std::snprintf(previous, sizeof(previous), "%s", line);
        }
        if (!gateway_client_connected()) continue;
        // Audio owns the uplink. Anything queued while Kiki is speaking or
        // listening waits, and is dropped if it waits too long.
        if (audio_pipeline_is_playing()) continue;

        char escaped[kLineLength * 2];
        size_t out = 0;
        for (size_t i = 0; line[i] && out + 8 < sizeof(escaped); ++i) {
            const unsigned char c = static_cast<unsigned char>(line[i]);
            if (c == '"' || c == '\\') {
                escaped[out++] = '\\';
                escaped[out++] = static_cast<char>(c);
            } else if (c >= 0x20 && c < 0x7F) {
                escaped[out++] = static_cast<char>(c);
            }
            // Control bytes and the ANSI colour escapes are dropped outright.
        }
        escaped[out] = '\0';

        char payload[sizeof(escaped) + 16];
        std::snprintf(payload, sizeof(payload), "\"line\":\"%s\"", escaped);
        g_sending = true;
        gateway_client_send_event("device_log", payload);
        g_sending = false;

        // Rate limit. The remote link has a ~450 ms round trip and a full
        // websocket send holds the client mutex for its duration, which is
        // what starves the keepalive and drops the session.
        vTaskDelay(pdMS_TO_TICKS(gateway_client_is_remote() ? 500 : 100));
    }
}

}  // namespace

void log_remote_start() {
    if (g_queue) return;
    g_queue = xQueueCreate(kLines, kLineLength);
    if (!g_queue) return;
    g_previous = esp_log_set_vprintf(log_hook);
    xTaskCreate(sender_task, "kiki_log_tx", 4096, nullptr, 2, nullptr);
    ESP_LOGW("kiki_log", "remote logging on; the panel no longer needs a cable");
}

}  // namespace kiki
