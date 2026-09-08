#include "kiki_instructor.hpp"
#include "kiki_instructor_model.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "audio_pipeline.hpp"
#include "bsp/esp-bsp.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "gateway_client.hpp"
#include "kiki_dance.hpp"
#include "kiki_ui.hpp"
#include "kiki_wearable.hpp"
#include "lvgl.h"

namespace kiki {
namespace {
lv_obj_t *g_screen=nullptr, *g_canvas=nullptr, *g_status=nullptr, *g_title=nullptr;
lv_timer_t *g_timer=nullptr;
uint16_t *g_pixels=nullptr;
float *g_depth=nullptr;
instructor::Command g_command;
std::atomic<bool> g_stop{false};
std::atomic<bool> g_active{false};
bool g_dirty=false;
bool g_running=false;
int64_t g_started=0, g_expires=0;
int g_remaining=-1;
char g_demo_id[64]{};

void close() {
    if(g_timer) {lv_timer_delete(g_timer);g_timer=nullptr;}
    if(g_screen) {
        ui_show();
        lv_obj_delete(g_screen);
    }
    g_screen=g_canvas=g_status=g_title=nullptr;
    heap_caps_free(g_pixels);heap_caps_free(g_depth);
    g_pixels=nullptr;g_depth=nullptr;
    g_running=false;g_demo_id[0]='\0';
    g_active.store(false);
}

void stop_cb(lv_event_t *) {
    // Local audio and animation stop immediately; the gateway closes the care
    // session as well, so late model output cannot resume a cancelled hold.
    instructor_stop();
    audio_pipeline_stop_playback();
    gateway_client_send_event("cancel_turn"); // urgent queue: overtake microphone backlog
    gateway_client_send_event("care_action","\"action\":\"end\"");
    close();
}

void tick(lv_timer_t *) {
    const int64_t now=esp_timer_get_time();
    if(g_stop.load() || now>=g_expires || wearable_fall_check_pending()) {close();return;}
    if(!g_running && !g_dirty) return;
    const int64_t started=esp_timer_get_time();
    instructor::render(g_pixels,g_depth,g_command,g_running?(now-g_started)/1000000.f:-1.f);
    g_dirty=false;
    lv_obj_invalidate(g_canvas);
    const int left=std::max(0,int((g_expires-now+999999)/1000000));
    if(g_running && left!=g_remaining) {
        char label[64];
        const char *side=g_command.side==instructor::Side::Left?"LEFT":
                         g_command.side==instructor::Side::Right?"RIGHT":"BOTH SIDES";
        std::snprintf(label,sizeof(label),"%s  /  %s  /  %ds",side,g_command.hold?"HOLD":"SLOW",left);
        lv_label_set_text(g_status,label);g_remaining=left;
    }
    // Yield more time to voice when a frame is expensive. No rendering task
    // competes with I2S or Wi-Fi; this runs under the existing LVGL lock.
    const uint32_t cost=uint32_t((esp_timer_get_time()-started)/1000);
    lv_timer_set_period(g_timer,std::clamp<uint32_t>(cost+40,100,250));
}

lv_obj_t *label(const char *text,int y,const lv_font_t *font,uint32_t colour) {
    lv_obj_t *obj=lv_label_create(g_screen);
    lv_label_set_text(obj,text);
    lv_obj_set_style_text_font(obj,font,0);
    lv_obj_set_style_text_color(obj,lv_color_hex(colour),0);
    lv_obj_align(obj,LV_ALIGN_TOP_MID,0,y);
    return obj;
}

bool open() {
    if(g_screen) return true;
    constexpr size_t n=instructor::kWidth*instructor::kHeight;
    g_pixels=static_cast<uint16_t *>(heap_caps_malloc(n*sizeof(uint16_t),MALLOC_CAP_SPIRAM));
    g_depth=static_cast<float *>(heap_caps_malloc(n*sizeof(float),MALLOC_CAP_SPIRAM));
    if(!g_pixels || !g_depth) {
        heap_caps_free(g_pixels);heap_caps_free(g_depth);g_pixels=nullptr;g_depth=nullptr;
        ESP_LOGE("instructor","no PSRAM for 3D instructor; voice remains available");
        return false;
    }
    g_screen=lv_obj_create(nullptr);
    std::fill(g_pixels,g_pixels+n,uint16_t(0x10E5));
    lv_obj_set_style_bg_color(g_screen,lv_color_hex(0x111D2B),0);
    lv_obj_remove_flag(g_screen,LV_OBJ_FLAG_SCROLLABLE);
    label("KIKI  /  MOVE",23,&lv_font_montserrat_18,0x65DAC5);
    g_title=label("Your instructor",49,&lv_font_montserrat_24,0xF1F5F4);
    g_canvas=lv_canvas_create(g_screen);
    lv_canvas_set_buffer(g_canvas,g_pixels,instructor::kWidth,instructor::kHeight,LV_COLOR_FORMAT_RGB565);
    lv_obj_set_pos(g_canvas,73,78);
    lv_obj_remove_flag(g_canvas,LV_OBJ_FLAG_CLICKABLE);
    g_status=label("Listen, then follow",370,&lv_font_montserrat_18,0xA9C2CC);
    lv_obj_t *button=lv_button_create(g_screen);
    lv_obj_set_size(button,180,47);
    lv_obj_align(button,LV_ALIGN_TOP_MID,0,399);
    lv_obj_set_style_radius(button,24,0);
    lv_obj_set_style_bg_color(button,lv_color_hex(0x733B4A),0);
    lv_obj_add_event_cb(button,stop_cb,LV_EVENT_CLICKED,nullptr);
    lv_obj_t *text=lv_label_create(button);
    lv_label_set_text(text,LV_SYMBOL_STOP "  End session");
    lv_obj_set_style_text_font(text,&lv_font_montserrat_18,0);
    lv_obj_center(text);
    g_timer=lv_timer_create(tick,100,nullptr);
    lv_screen_load(g_screen);
    g_active.store(true);
    return true;
}

const char *str(const cJSON *root,const char *key) {
    const cJSON *v=cJSON_GetObjectItemCaseSensitive(root,key);
    return cJSON_IsString(v)?v->valuestring:"";
}
} // namespace

void instructor_stop() {g_stop.store(true);}
bool instructor_active() {return g_active.load();}

void instructor_command(const cJSON *root) {
    const char *action=str(root,"action");
    if(std::strcmp(action,"stop")==0) {
        const char *id=str(root,"demo_id");
        if(!id[0]) instructor_stop();
        else if(bsp_display_lock(200)==ESP_OK) {
            if(std::strcmp(id,g_demo_id)==0) instructor_stop();
            bsp_display_unlock();
        }
        return;
    }
    const bool prepare=std::strcmp(action,"prepare")==0;
    if(!prepare && std::strcmp(action,"start")!=0) return;
    instructor::Command cmd;
    if(!instructor::parse_move(str(root,"move"),cmd.move)) return;
    const char *side=str(root,"side"), *pattern=str(root,"pattern");
    if(std::strcmp(side,"left")==0) cmd.side=instructor::Side::Left;
    else if(std::strcmp(side,"right")==0) cmd.side=instructor::Side::Right;
    else if(std::strcmp(side,"both")!=0) return;
    if(std::strcmp(pattern,"hold")==0) cmd.hold=true;
    else if(std::strcmp(pattern,"repeat")!=0) return;
    if(cmd.hold && cmd.side==instructor::Side::Both &&
       (cmd.move==instructor::Move::SeatedMarch || cmd.move==instructor::Move::TorsoTwist)) return;
    if(cmd.hold && cmd.move==instructor::Move::ShoulderRoll) return;
    const cJSON *period=cJSON_GetObjectItemCaseSensitive(root,"period_seconds");
    const cJSON *duration=cJSON_GetObjectItemCaseSensitive(root,"seconds");
    if(!cJSON_IsNumber(period) || !std::isfinite(period->valuedouble) ||
       period->valuedouble<4 || period->valuedouble>12) return;
    cmd.period=float(period->valuedouble);
    if(!prepare && (!cJSON_IsNumber(duration) || !std::isfinite(duration->valuedouble) ||
                   duration->valuedouble<=0 || duration->valuedouble>120)) return;
    const char *id=str(root,"demo_id");
    if(!id[0] || std::strlen(id)>=sizeof(g_demo_id)) return;
    if(dance_is_active()) return; // another exclusive screen already owns the panel
    if(bsp_display_lock(200)!=ESP_OK) return;
    // A start must belong to the still-open preparation. In particular a late
    // start arriving after a local End cannot resurrect the instructor.
    if(!prepare && (!g_screen || g_stop.load() || std::strcmp(id,g_demo_id)!=0)) {
        bsp_display_unlock();return;
    }
    if(prepare) g_stop.store(false);
    if(open()) {
        g_command=cmd;g_running=!prepare;g_started=esp_timer_get_time();
        g_expires=g_started+int64_t((prepare?60:duration->valuedouble)*1000000);
        g_remaining=-1;
        std::snprintf(g_demo_id,sizeof(g_demo_id),"%s",id);
        lv_label_set_text(g_title,instructor::move_title(cmd.move));
        lv_label_set_text(g_status,prepare?"Listen, then follow":"Follow at your own comfort");
        g_dirty=true;
        ESP_LOGI("instructor","%s %s / %s",action,str(root,"move"),side);
    }
    bsp_display_unlock();
}
} // namespace kiki
