#include "kiki_wizard.hpp"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>

#include "bsp/esp-bsp.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gateway_client.hpp"
#include "kiki_theme.hpp"
#include "kiki_ui.hpp"
#include "lvgl.h"
#include "wifi_station.hpp"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_wizard";
constexpr int32_t kScreen = 466;
constexpr int32_t kPanelW = 320;
// startup_config.EDIT_PROMPT_TIMEOUT_S. An unattended boot must not stall.
constexpr uint32_t kOfferTimeoutMs = 20000;

// Eight was silently fewer than the modes that exist. The mode list comes from
// the gateway's config, which grows whenever a persona is added, and anything
// past this cap was dropped without a word -- so "Vaibhav" and "senior" had
// never once appeared on the panel, and neither did two new personas that were
// correctly loaded, correctly served by the gateway, and simply cut off the
// end of the array.
//
// Costs kMaxChoices * kMaxLabel bytes per question, in six questions of static
// internal RAM: 16 * 28 * 6 is about 2.7 KB against the ~67 KB free. Truncation
// is now logged, because the whole problem here was that it was not.
constexpr int kMaxChoices = 16;
constexpr int kMaxLabel = 28;

struct Question {
    char key[16];
    char label[32];
    char choices[kMaxChoices][kMaxLabel];
    int choice_count;
    int current;
    // Numeric questions (volume, gain, caption delay) step a value instead.
    bool numeric;
    float value;
    float step;
    float minimum;
    float maximum;
    const char *unit;
};

Question g_questions[6];
int g_question_count = 0;
int g_index = -1;

lv_obj_t *g_screen = nullptr;
lv_obj_t *g_title = nullptr;
lv_obj_t *g_value = nullptr;
lv_obj_t *g_list = nullptr;
lv_timer_t *g_timeout = nullptr;
std::atomic<bool> g_active{false};

void show_question(int index);

void finish() {
    g_active = false;
    if (g_timeout) { lv_timer_delete(g_timeout); g_timeout = nullptr; }
    gateway_client_send_event("config_commit");
    ui_show();
}

void send_answer(const Question &question, const char *value) {
    char extra[96];
    if (question.numeric) {
        std::snprintf(extra, sizeof(extra), "\"key\":\"%s\",\"value\":%s",
                      question.key, value);
    } else {
        std::snprintf(extra, sizeof(extra), "\"key\":\"%s\",\"value\":\"%s\"",
                      question.key, value);
    }
    gateway_client_send_event("config_set", extra);
}

void advance() {
    if (g_index + 1 >= g_question_count) {
        finish();
        return;
    }
    show_question(g_index + 1);
}

void choice_cb(lv_event_t *event) {
    const auto choice = reinterpret_cast<intptr_t>(lv_event_get_user_data(event));
    Question &question = g_questions[g_index];
    send_answer(question, question.choices[choice]);
    advance();
}

void numeric_cb(lv_event_t *event) {
    const auto delta = reinterpret_cast<intptr_t>(lv_event_get_user_data(event));
    Question &question = g_questions[g_index];
    if (delta == 0) {
        char rendered[24];
        std::snprintf(rendered, sizeof(rendered), "%.2f", question.value);
        send_answer(question, rendered);
        advance();
        return;
    }
    question.value = std::clamp(question.value + question.step * delta,
                                question.minimum, question.maximum);
    // Apply as it moves so volume and gain are audible while being set.
    char rendered[24];
    std::snprintf(rendered, sizeof(rendered), "%.2f", question.value);
    send_answer(question, rendered);
    char text[48];
    std::snprintf(text, sizeof(text), "%.*f%s",
                  question.step < 1.0f ? 1 : 0,
                  static_cast<double>(question.value), question.unit);
    lv_label_set_text(g_value, text);
}

lv_obj_t *add_row(const char *text, lv_event_cb_t handler, intptr_t data) {
    lv_obj_t *button = lv_list_add_button(g_list, nullptr, text);
    lv_obj_set_style_text_font(button, &lv_font_montserrat_24, 0);
    lv_obj_set_style_bg_color(button, theme::surface(), 0);
    lv_obj_set_style_text_color(button, theme::text_primary(), 0);
    lv_obj_set_style_border_width(button, 0, 0);
    lv_obj_set_style_margin_bottom(button, 5, 0);
    lv_obj_add_event_cb(button, handler, LV_EVENT_CLICKED,
                        reinterpret_cast<void *>(data));
    return button;
}

void show_question(int index) {
    g_index = index;
    const Question &question = g_questions[index];
    lv_label_set_text(g_title, question.label);
    lv_obj_clean(g_list);

    if (question.numeric) {
        char text[48];
        std::snprintf(text, sizeof(text), "%.*f%s",
                      question.step < 1.0f ? 1 : 0,
                      static_cast<double>(question.value), question.unit);
        lv_label_set_text(g_value, text);
        lv_obj_remove_flag(g_value, LV_OBJ_FLAG_HIDDEN);
        add_row(LV_SYMBOL_PLUS "   More", numeric_cb, 1);
        add_row(LV_SYMBOL_MINUS "   Less", numeric_cb, -1);
        add_row(LV_SYMBOL_OK "   Next", numeric_cb, 0);
        return;
    }

    lv_obj_add_flag(g_value, LV_OBJ_FLAG_HIDDEN);
    for (int i = 0; i < question.choice_count; ++i) {
        char text[40];
        std::snprintf(text, sizeof(text), "%s%s", question.choices[i],
                      i == question.current ? "   " LV_SYMBOL_OK : "");
        add_row(text, choice_cb, i);
    }
}

// "Edit config?" -- No first, like the Pi, so the timeout and the safe answer
// agree.
void offer_cb(lv_event_t *event) {
    const auto yes = reinterpret_cast<intptr_t>(lv_event_get_user_data(event));
    if (g_timeout) { lv_timer_delete(g_timeout); g_timeout = nullptr; }
    if (!yes) {
        g_active = false;
        ui_show();
        ESP_LOGI(kTag, "keeping startup defaults");
        return;
    }
    show_question(0);
}

void timeout_cb(lv_timer_t *) {
    g_timeout = nullptr;
    g_active = false;
    ui_show();
    ESP_LOGI(kTag, "no answer in %lus; keeping startup defaults",
             static_cast<unsigned long>(kOfferTimeoutMs / 1000));
}

void build_screen() {
    if (g_screen) return;
    g_screen = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(g_screen, theme::background(), 0);
    lv_obj_set_style_border_width(g_screen, 0, 0);
    lv_obj_remove_flag(g_screen, LV_OBJ_FLAG_SCROLLABLE);

    g_title = lv_label_create(g_screen);
    lv_label_set_long_mode(g_title, LV_LABEL_LONG_DOT);
    lv_obj_set_width(g_title, kPanelW);
    lv_obj_set_style_text_align(g_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(g_title, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(g_title, theme::mascot(), 0);
    lv_obj_set_pos(g_title, (kScreen - kPanelW) / 2, 64);

    g_value = lv_label_create(g_screen);
    lv_obj_set_width(g_value, kPanelW);
    lv_obj_set_style_text_align(g_value, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(g_value, &lv_font_montserrat_36, 0);
    lv_obj_set_style_text_color(g_value, theme::text_primary(), 0);
    lv_obj_set_pos(g_value, (kScreen - kPanelW) / 2, 108);
    lv_obj_add_flag(g_value, LV_OBJ_FLAG_HIDDEN);

    g_list = lv_list_create(g_screen);
    lv_obj_set_size(g_list, kPanelW, 254);
    lv_obj_set_pos(g_list, (kScreen - kPanelW) / 2, 156);
    lv_obj_set_style_bg_color(g_list, theme::background(), 0);
    lv_obj_set_style_border_width(g_list, 0, 0);
}

void add_choice_question(const cJSON *options, const char *key) {
    const cJSON *entry = cJSON_GetObjectItemCaseSensitive(options, key);
    if (!cJSON_IsObject(entry)) return;
    const cJSON *choices = cJSON_GetObjectItemCaseSensitive(entry, "choices");
    if (!cJSON_IsArray(choices) || cJSON_GetArraySize(choices) < 1) return;
    const cJSON *label = cJSON_GetObjectItemCaseSensitive(entry, "label");
    const cJSON *current = cJSON_GetObjectItemCaseSensitive(entry, "current");

    Question &question = g_questions[g_question_count];
    question = {};
    std::snprintf(question.key, sizeof(question.key), "%s", key);
    std::snprintf(question.label, sizeof(question.label), "%s",
                  cJSON_IsString(label) ? label->valuestring : key);
    const int offered = cJSON_GetArraySize(choices);
    const int count = std::min(offered, kMaxChoices);
    if (offered > kMaxChoices) {
        ESP_LOGW(kTag, "\"%s\" has %d choices; showing the first %d", key, offered,
                 kMaxChoices);
    }
    for (int i = 0; i < count; ++i) {
        const cJSON *choice = cJSON_GetArrayItem(choices, i);
        if (!cJSON_IsString(choice)) continue;
        std::snprintf(question.choices[question.choice_count],
                      kMaxLabel, "%s", choice->valuestring);
        if (cJSON_IsString(current) &&
            std::strcmp(choice->valuestring, current->valuestring) == 0) {
            question.current = question.choice_count;
        }
        ++question.choice_count;
    }
    if (question.choice_count > 0) ++g_question_count;
}

void add_numeric_question(const cJSON *options, const char *key, float step,
                          float minimum, float maximum, const char *unit) {
    const cJSON *entry = cJSON_GetObjectItemCaseSensitive(options, key);
    if (!cJSON_IsObject(entry)) return;
    const cJSON *label = cJSON_GetObjectItemCaseSensitive(entry, "label");
    const cJSON *current = cJSON_GetObjectItemCaseSensitive(entry, "current");

    Question &question = g_questions[g_question_count];
    question = {};
    std::snprintf(question.key, sizeof(question.key), "%s", key);
    std::snprintf(question.label, sizeof(question.label), "%s",
                  cJSON_IsString(label) ? label->valuestring : key);
    question.numeric = true;
    question.value = cJSON_IsNumber(current) ? static_cast<float>(current->valuedouble) : minimum;
    question.step = step;
    question.minimum = minimum;
    question.maximum = maximum;
    question.unit = unit;
    ++g_question_count;
}

}  // namespace

bool wizard_active() { return g_active.load(); }

void wizard_offer(const cJSON *options) {
    // Once per boot, not once per gateway session. The gateway sends
    // config_options on every connection and the board reconnects on its own,
    // so this used to throw "Edit config?" over whatever was on screen every
    // time the link blipped -- which reads exactly like the board restarting.
    static std::atomic<bool> g_offered{false};
    if (g_offered.exchange(true)) return;
    if (g_active.load() || !cJSON_IsObject(options)) return;

    g_question_count = 0;
    // Same order as startup_config.py, minus Wi-Fi -- the board owns the radio
    // and offers its own picker from Settings, so asking here would be a second
    // route to the same screen.
    add_choice_question(options, "provider");
    add_numeric_question(options, "volume", 10, 0, 100, "%");
    add_numeric_question(options, "gain", 0.2f, 1.0f, 5.0f, "x");
    add_choice_question(options, "language");
    add_choice_question(options, "mode");
    // Offered last for the same reason the Pi offers calibration last: every
    // choice above can change how the voice sounds and therefore how it lands.
    add_numeric_question(options, "sync", 40, -500, 2000, " ms");
    if (g_question_count == 0) return;

    if (bsp_display_lock(1000) != ESP_OK) return;
    g_active = true;
    build_screen();
    lv_label_set_text(g_title, "Edit config?");
    lv_obj_add_flag(g_value, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clean(g_list);
    add_row("No", offer_cb, 0);
    add_row("Yes", offer_cb, 1);
    g_timeout = lv_timer_create(timeout_cb, kOfferTimeoutMs, nullptr);
    lv_timer_set_repeat_count(g_timeout, 1);
    lv_screen_load(g_screen);
    bsp_display_unlock();
    ESP_LOGI(kTag, "offering startup config (%d questions)", g_question_count);
}

}  // namespace kiki
