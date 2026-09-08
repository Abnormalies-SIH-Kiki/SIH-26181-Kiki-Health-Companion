#include "kiki_care_ui.hpp"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>

#include "bsp/esp-bsp.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gateway_client.hpp"
#include "kiki_theme.hpp"
#include "kiki_ui.hpp"

namespace kiki {
namespace {

constexpr char kTag[] = "kiki_care_ui";
constexpr int32_t kScreen = 466;
constexpr int32_t kPanelW = 360;

// A care plan longer than this is not a plan a person reads on a watch. The
// gateway sends the whole thing every time and the board keeps the first
// kMaxItems; the count in the header says when there are more.
constexpr int kMaxItems = 16;

struct CareItem {
    char id[20];
    char title[64];
    char when[28];
    char category[16];
    char brief[224];
    bool enabled;
};

// PSRAM, for the reason kiki_exercise.cpp spells out: internal DRAM is what the
// Wi-Fi driver and the DMA descriptors come out of, and a list nobody is
// looking at should not be holding 5 kB of it.
CareItem *g_items = nullptr;
int g_count = 0;      // items actually held
int g_total = 0;      // items the gateway says exist
bool g_session_active = false;
char g_session_title[64] = "";

lv_obj_t *g_screen = nullptr;
lv_obj_t *g_detail = nullptr;
lv_obj_t *g_return_screen = nullptr;
lv_obj_t *g_list = nullptr;
lv_obj_t *g_subtitle = nullptr;
lv_obj_t *g_empty = nullptr;
lv_obj_t *g_session_row = nullptr;
lv_obj_t *g_session_label = nullptr;
lv_obj_t *g_delete_label = nullptr;
lv_timer_t *g_delete_timer = nullptr;
std::atomic<bool> g_active{false};
std::atomic<bool> g_dirty{false};
int g_selected = -1;
// Deleting a routine cannot be undone from here, so it takes two taps and the
// arming lapses -- the same shape the Shut down control uses.
bool g_delete_armed = false;

lv_color_t category_colour(const char *category) {
    // Four families, so a glance at the list separates a medicine from a walk
    // without reading a word of it.
    if (std::strcmp(category, "medicine") == 0) return lv_color_hex(0xC77B7B);
    if (std::strcmp(category, "exercise") == 0) return lv_color_hex(0x7BC79B);
    if (std::strcmp(category, "hydration") == 0 ||
        std::strcmp(category, "meal") == 0) return lv_color_hex(0x7BA8C7);
    return lv_color_hex(0xC7B07B);
}

void send_action(const char *action, const char *id) {
    char extra[96];
    std::snprintf(extra, sizeof(extra), "\"action\":\"%s\",\"id\":\"%s\"",
                  action, id ? id : "");
    gateway_client_send_event("care_action", extra);
}

void close_detail();

void disarm_delete(lv_timer_t *timer) {
    g_delete_armed = false;
    if (g_delete_label) lv_label_set_text(g_delete_label, LV_SYMBOL_TRASH "  Delete");
    if (g_delete_label) lv_obj_set_style_text_color(g_delete_label, theme::stop(), 0);
    if (timer) lv_timer_delete(timer);
    g_delete_timer = nullptr;
}

void detail_action_cb(lv_event_t *event) {
    const auto action = reinterpret_cast<intptr_t>(lv_event_get_user_data(event));
    if (g_selected < 0 || g_selected >= g_count) return;
    const char *id = g_items[g_selected].id;
    switch (action) {
        case 0:  // start now
            send_action("start", id);
            close_detail();
            care_ui_open(g_return_screen);
            ui_set_state("thinking");
            break;
        case 1:  // enable / disable
            send_action(g_items[g_selected].enabled ? "disable" : "enable", id);
            close_detail();
            break;
        case 2:  // delete, twice
            if (!g_delete_armed) {
                g_delete_armed = true;
                if (g_delete_label) {
                    lv_label_set_text(g_delete_label,
                                      LV_SYMBOL_WARNING "  Tap again to delete");
                    lv_obj_set_style_text_color(g_delete_label, theme::mascot(), 0);
                }
                if (g_delete_timer) lv_timer_delete(g_delete_timer);
                g_delete_timer = lv_timer_create(disarm_delete, 4000, nullptr);
                lv_timer_set_repeat_count(g_delete_timer, 1);
                return;
            }
            disarm_delete(g_delete_timer);
            send_action("delete", id);
            close_detail();
            break;
        default:
            close_detail();
            break;
    }
}

lv_obj_t *detail_button(lv_obj_t *parent, const char *text, lv_color_t colour,
                        int32_t y, intptr_t action) {
    lv_obj_t *button = lv_button_create(parent);
    lv_obj_set_size(button, kPanelW, 56);
    lv_obj_align(button, LV_ALIGN_TOP_MID, 0, y);
    lv_obj_set_style_bg_color(button, theme::surface(), 0);
    lv_obj_set_style_border_width(button, 0, 0);
    lv_obj_set_style_radius(button, 14, 0);
    lv_obj_add_event_cb(button, detail_action_cb, LV_EVENT_CLICKED,
                        reinterpret_cast<void *>(action));
    lv_obj_t *label = lv_label_create(button);
    lv_label_set_text(label, text);
    lv_obj_set_style_text_font(label, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(label, colour, 0);
    lv_obj_center(label);
    return label;
}

void close_detail() {
    disarm_delete(g_delete_timer);
    g_selected = -1;
    if (g_screen) lv_screen_load(g_screen);
    if (g_detail) {
        lv_obj_delete(g_detail);
        g_detail = nullptr;
    }
    g_delete_label = nullptr;
}

void open_detail(int index) {
    if (index < 0 || index >= g_count) return;
    g_selected = index;
    const CareItem &item = g_items[index];

    if (g_detail) lv_obj_delete(g_detail);
    g_detail = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(g_detail, theme::background(), 0);
    lv_obj_set_style_border_width(g_detail, 0, 0);
    lv_obj_remove_flag(g_detail, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *when = lv_label_create(g_detail);
    lv_label_set_text(when, item.when);
    lv_obj_set_style_text_font(when, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(when, category_colour(item.category), 0);
    lv_obj_align(when, LV_ALIGN_TOP_MID, 0, 54);

    lv_obj_t *title = lv_label_create(g_detail);
    lv_label_set_text(title, item.title);
    lv_obj_set_width(title, kPanelW);
    lv_label_set_long_mode(title, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(title, theme::text_primary(), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 96);

    // The brief is the thing worth reading: it is what Kiki was actually told
    // to do, in the words it was saved with.
    lv_obj_t *brief = lv_label_create(g_detail);
    lv_label_set_text(brief, item.brief[0] ? item.brief : "No details saved.");
    lv_obj_set_width(brief, kPanelW);
    lv_label_set_long_mode(brief, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(brief, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(brief, theme::text_secondary(), 0);
    lv_obj_align(brief, LV_ALIGN_TOP_MID, 0, 140);
    lv_obj_set_height(brief, 92);

    detail_button(g_detail, LV_SYMBOL_PLAY "  Start now", theme::accent(), 244, 0);
    detail_button(g_detail,
                  item.enabled ? LV_SYMBOL_PAUSE "  Turn off"
                               : LV_SYMBOL_OK "  Turn on",
                  theme::text_primary(), 306, 1);
    g_delete_label = detail_button(g_detail, LV_SYMBOL_TRASH "  Delete",
                                   theme::stop(), 368, 2);
    lv_obj_t *back = lv_button_create(g_detail);
    lv_obj_set_size(back, 140, 44);
    lv_obj_align(back, LV_ALIGN_BOTTOM_MID, 0, -6);
    lv_obj_set_style_bg_color(back, theme::background(), 0);
    lv_obj_set_style_border_width(back, 0, 0);
    lv_obj_add_event_cb(back, detail_action_cb, LV_EVENT_CLICKED,
                        reinterpret_cast<void *>(3));
    lv_obj_t *back_label = lv_label_create(back);
    lv_label_set_text(back_label, LV_SYMBOL_LEFT "  Back");
    lv_obj_set_style_text_color(back_label, theme::text_secondary(), 0);
    lv_obj_center(back_label);

    lv_screen_load(g_detail);
}

void row_cb(lv_event_t *event) {
    open_detail(static_cast<int>(
        reinterpret_cast<intptr_t>(lv_event_get_user_data(event))));
}

void footer_cb(lv_event_t *event) {
    const auto action = reinterpret_cast<intptr_t>(lv_event_get_user_data(event));
    if (action == 0) {
        send_action("refresh", "");
        return;
    }
    if (action == 2) {
        // Ending a session is not destructive -- the transcript is archived,
        // and it can be started again -- so unlike Delete it takes one tap.
        // Someone reaching for this usually wants it to stop now.
        send_action("end", "");
        return;
    }
    g_active = false;
    if (g_return_screen) lv_screen_load(g_return_screen);
}

void rebuild_list() {
    if (!g_list) return;
    lv_obj_clean(g_list);

    if (g_count == 0) {
        if (g_empty) lv_obj_remove_flag(g_empty, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    if (g_empty) lv_obj_add_flag(g_empty, LV_OBJ_FLAG_HIDDEN);

    for (int i = 0; i < g_count; ++i) {
        const CareItem &item = g_items[i];
        lv_obj_t *row = lv_obj_create(g_list);
        lv_obj_set_size(row, kPanelW - 8, 66);
        lv_obj_set_style_bg_color(row, theme::surface(), 0);
        lv_obj_set_style_border_width(row, 0, 0);
        lv_obj_set_style_radius(row, 14, 0);
        lv_obj_set_style_pad_all(row, 8, 0);
        lv_obj_set_style_margin_bottom(row, 6, 0);
        lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(row, row_cb, LV_EVENT_CLICKED,
                            reinterpret_cast<void *>(static_cast<intptr_t>(i)));

        // A coloured spine rather than a coloured row: the category has to be
        // readable at a glance without making the text fight the background.
        lv_obj_t *spine = lv_obj_create(row);
        lv_obj_set_size(spine, 5, 44);
        lv_obj_align(spine, LV_ALIGN_LEFT_MID, 0, 0);
        lv_obj_set_style_bg_color(spine, category_colour(item.category), 0);
        lv_obj_set_style_border_width(spine, 0, 0);
        lv_obj_set_style_radius(spine, 3, 0);

        lv_obj_t *when = lv_label_create(row);
        lv_label_set_text(when, item.when);
        lv_obj_set_style_text_font(when, &lv_font_montserrat_24, 0);
        lv_obj_set_style_text_color(
            when, item.enabled ? theme::text_primary() : theme::text_secondary(), 0);
        lv_obj_align(when, LV_ALIGN_LEFT_MID, 16, -11);

        lv_obj_t *title = lv_label_create(row);
        lv_label_set_text(title, item.title);
        lv_obj_set_width(title, kPanelW - 40);
        lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
        lv_obj_set_style_text_color(title, theme::text_secondary(), 0);
        lv_obj_align(title, LV_ALIGN_LEFT_MID, 16, 15);

        if (!item.enabled) {
            // Off, not gone. A routine someone paused must not look identical
            // to one that is going to speak in ten minutes.
            lv_obj_t *off = lv_label_create(row);
            lv_label_set_text(off, LV_SYMBOL_PAUSE);
            lv_obj_set_style_text_color(off, theme::text_secondary(), 0);
            lv_obj_align(off, LV_ALIGN_RIGHT_MID, -4, 0);
            lv_obj_set_style_opa(row, LV_OPA_60, 0);
        }
    }
}

void refresh_session_banner() {
    if (!g_session_row || !g_list) return;
    if (g_session_active) {
        if (g_session_label) {
            char text[96];
            std::snprintf(text, sizeof(text), LV_SYMBOL_STOP "  End %s",
                          g_session_title[0] ? g_session_title : "session");
            lv_label_set_text(g_session_label, text);
        }
        lv_obj_remove_flag(g_session_row, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_pos(g_list, (kScreen - kPanelW) / 2, 170);
        lv_obj_set_height(g_list, 210);
    } else {
        lv_obj_add_flag(g_session_row, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_pos(g_list, (kScreen - kPanelW) / 2, 112);
        lv_obj_set_height(g_list, 268);
    }
}

void refresh_header() {
    if (!g_subtitle) return;
    char text[64];
    if (g_total == 0) {
        std::snprintf(text, sizeof(text), "nothing scheduled");
    } else if (g_total > g_count) {
        std::snprintf(text, sizeof(text), "%d of %d routines", g_count, g_total);
    } else {
        std::snprintf(text, sizeof(text), "%d routine%s", g_total,
                      g_total == 1 ? "" : "s");
    }
    lv_label_set_text(g_subtitle, text);
}

void build() {
    if (g_screen) return;
    g_screen = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(g_screen, theme::background(), 0);
    lv_obj_set_style_border_width(g_screen, 0, 0);
    lv_obj_remove_flag(g_screen, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *title = lv_label_create(g_screen);
    lv_label_set_text(title, "Care Plan");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(title, theme::mascot(), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 44);

    g_subtitle = lv_label_create(g_screen);
    lv_label_set_text(g_subtitle, "");
    lv_obj_set_style_text_color(g_subtitle, theme::text_secondary(), 0);
    lv_obj_align(g_subtitle, LV_ALIGN_TOP_MID, 0, 84);

    g_list = lv_obj_create(g_screen);
    lv_obj_set_size(g_list, kPanelW, 268);
    lv_obj_set_pos(g_list, (kScreen - kPanelW) / 2, 112);
    lv_obj_set_style_bg_color(g_list, theme::background(), 0);
    lv_obj_set_style_border_width(g_list, 0, 0);
    lv_obj_set_style_pad_all(g_list, 2, 0);
    lv_obj_set_flex_flow(g_list, LV_FLEX_FLOW_COLUMN);

    g_empty = lv_label_create(g_screen);
    lv_label_set_text(g_empty,
                      "Nothing scheduled yet.\n\n"
                      "Ask Kiki: \"add a shoulder\nstretch at 9 in the morning\"");
    lv_obj_set_style_text_align(g_empty, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(g_empty, theme::text_secondary(), 0);
    lv_obj_align(g_empty, LV_ALIGN_CENTER, 0, 10);
    lv_obj_add_flag(g_empty, LV_OBJ_FLAG_HIDDEN);

    // A running session outranks the list. It is the one thing on this screen
    // that is happening RIGHT NOW, and the one thing a person may urgently want
    // to stop -- so it sits at the top and is the only red control here.
    g_session_row = lv_button_create(g_screen);
    lv_obj_set_size(g_session_row, kPanelW, 52);
    lv_obj_align(g_session_row, LV_ALIGN_TOP_MID, 0, 108);
    lv_obj_set_style_bg_color(g_session_row, theme::stop(), 0);
    lv_obj_set_style_border_width(g_session_row, 0, 0);
    lv_obj_set_style_radius(g_session_row, 14, 0);
    lv_obj_add_event_cb(g_session_row, footer_cb, LV_EVENT_CLICKED,
                        reinterpret_cast<void *>(2));
    g_session_label = lv_label_create(g_session_row);
    lv_label_set_text(g_session_label, LV_SYMBOL_STOP "  End session");
    lv_obj_set_style_text_font(g_session_label, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(g_session_label, theme::text_primary(), 0);
    lv_obj_center(g_session_label);
    lv_obj_add_flag(g_session_row, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *refresh = lv_button_create(g_screen);
    lv_obj_set_size(refresh, 150, 48);
    lv_obj_align(refresh, LV_ALIGN_BOTTOM_MID, -80, -18);
    lv_obj_set_style_bg_color(refresh, theme::surface(), 0);
    lv_obj_set_style_border_width(refresh, 0, 0);
    lv_obj_set_style_radius(refresh, 14, 0);
    lv_obj_add_event_cb(refresh, footer_cb, LV_EVENT_CLICKED,
                        reinterpret_cast<void *>(0));
    lv_obj_t *refresh_label = lv_label_create(refresh);
    lv_label_set_text(refresh_label, LV_SYMBOL_REFRESH "  Refresh");
    lv_obj_set_style_text_color(refresh_label, theme::text_primary(), 0);
    lv_obj_center(refresh_label);

    lv_obj_t *close = lv_button_create(g_screen);
    lv_obj_set_size(close, 150, 48);
    lv_obj_align(close, LV_ALIGN_BOTTOM_MID, 80, -18);
    lv_obj_set_style_bg_color(close, theme::surface(), 0);
    lv_obj_set_style_border_width(close, 0, 0);
    lv_obj_set_style_radius(close, 14, 0);
    lv_obj_add_event_cb(close, footer_cb, LV_EVENT_CLICKED,
                        reinterpret_cast<void *>(1));
    lv_obj_t *close_label = lv_label_create(close);
    lv_label_set_text(close_label, LV_SYMBOL_CLOSE "  Close");
    lv_obj_set_style_text_color(close_label, theme::text_primary(), 0);
    lv_obj_center(close_label);
}

const char *field(const cJSON *item, const char *name) {
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(item, name);
    return cJSON_IsString(value) ? value->valuestring : "";
}

}  // namespace

void care_ui_set_plan(const cJSON *root) {
    if (g_items == nullptr) {
        g_items = static_cast<CareItem *>(
            heap_caps_malloc(sizeof(CareItem) * kMaxItems, MALLOC_CAP_SPIRAM));
        if (g_items == nullptr) {
            ESP_LOGE(kTag, "no PSRAM for the care plan view");
            return;
        }
    }
    const cJSON *items = cJSON_GetObjectItemCaseSensitive(root, "items");
    const cJSON *total = cJSON_GetObjectItemCaseSensitive(root, "total");
    const cJSON *session = cJSON_GetObjectItemCaseSensitive(root, "session");
    g_session_active = cJSON_IsObject(session) &&
                       cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(session, "active"));
    const cJSON *session_title = cJSON_IsObject(session)
        ? cJSON_GetObjectItemCaseSensitive(session, "title") : nullptr;
    std::snprintf(g_session_title, sizeof(g_session_title), "%s",
                  cJSON_IsString(session_title) ? session_title->valuestring : "");

    int count = 0;
    if (cJSON_IsArray(items)) {
        const cJSON *entry = nullptr;
        cJSON_ArrayForEach(entry, items) {
            if (count >= kMaxItems) break;
            CareItem &slot = g_items[count];
            std::snprintf(slot.id, sizeof(slot.id), "%s", field(entry, "id"));
            std::snprintf(slot.title, sizeof(slot.title), "%s", field(entry, "title"));
            std::snprintf(slot.when, sizeof(slot.when), "%s", field(entry, "when"));
            std::snprintf(slot.category, sizeof(slot.category), "%s",
                          field(entry, "category"));
            std::snprintf(slot.brief, sizeof(slot.brief), "%s", field(entry, "brief"));
            const cJSON *enabled = cJSON_GetObjectItemCaseSensitive(entry, "enabled");
            slot.enabled = !cJSON_IsBool(enabled) || cJSON_IsTrue(enabled);
            ++count;
        }
    }
    g_count = count;
    g_total = cJSON_IsNumber(total) ? total->valueint : count;
    ESP_LOGI(kTag, "care plan: %d of %d routine(s)", g_count, g_total);

    // Only redraw when this screen is the one on the display. Rebuilding a
    // list behind the face costs LVGL work nobody can see, and the plan is
    // pushed on every change.
    if (!g_active.load()) {
        g_dirty = true;
        return;
    }
    if (bsp_display_lock(200) == ESP_OK) {
        refresh_header();
        refresh_session_banner();
        rebuild_list();
        bsp_display_unlock();
    } else {
        g_dirty = true;
    }
}

bool care_ui_active() { return g_active.load(); }

void care_ui_open(lv_obj_t *return_screen) {
    g_return_screen = return_screen;
    build();
    refresh_header();
    refresh_session_banner();
    rebuild_list();
    g_dirty = false;
    g_active = true;
    lv_screen_load(g_screen);
    // Ask for a fresh copy on open. The plan is small and the person is
    // looking straight at it, so showing a stale schedule is the one thing
    // this screen must not do.
    send_action("refresh", "");
}

}  // namespace kiki
