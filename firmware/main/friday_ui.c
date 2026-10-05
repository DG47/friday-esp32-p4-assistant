#include "friday_ui.h"

#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "lvgl.h"

#include "friday_state.h"
#include "friday_media_guard.h"
#include "friday_display_text.h"
#include "friday_presence.h"
#include "friday_artwork.h"
#include "friday_media_overlay.h"

static const char *TAG = "friday_ui";
static friday_media_guard_t media_guard;
static bool owned_media_render;
static friday_media_overlay_t overlay;
static bool overlay_action_pending;
static bool overlay_resolution_pending;
static bool overlay_memory_decision_pending;
static char last_media_line[2001];

#define FRIDAY_STATUS_BOTTOM_OFFSET -166
#define FRIDAY_MESSAGE_TOP 570
#define FRIDAY_MESSAGE_WIDTH 620
#define FRIDAY_MESSAGE_HEIGHT 96

typedef struct {
    lv_obj_t *presence;
    lv_obj_t *eyebrow;
    lv_obj_t *title;
    lv_obj_t *status;
    lv_obj_t *message;
    lv_obj_t *network;
    lv_obj_t *microphone;
    lv_obj_t *progress;
    lv_obj_t *cover;
    lv_obj_t *cover_image;
    lv_obj_t *cover_label;
    lv_obj_t *artist;
    lv_obj_t *media_state;
    lv_obj_t *media_time;
    lv_obj_t *attribution;
    lv_obj_t *lyric_caption;
    lv_obj_t *fact_panel;
    lv_obj_t *card_times[4];
    lv_image_dsc_t cover_dsc;
    friday_artwork_t artwork;
    lv_obj_t *cards[4];
    lv_obj_t *card_labels[4];
    lv_obj_t *buttons[4];
    lv_obj_t *button_labels[4];
    char card_actions[4][64];
    char button_actions[4][64];
    bool scene_active;
    char scene_kind[24];
    unsigned long scene_generation;
    bool long_pressed;
    lv_timer_t *expiry;
    lv_timer_t *countdown;
    int64_t countdown_deadline;
} friday_ui_t;

static friday_ui_t ui;
static friday_ui_activation_callback_t activation_callback;
static friday_ui_action_callback_t action_callback;
static void scene_layout(bool active);
static void scene_expire(lv_timer_t *timer);

static lv_color_t color_hex(uint32_t value)
{
    return lv_color_hex(value);
}

static void position_message(void)
{
    if(ui.scene_active&&!strcmp(ui.scene_kind,"media")) {lv_obj_set_size(ui.message,624,100);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,404);return;}
    lv_obj_set_size(ui.message, FRIDAY_MESSAGE_WIDTH, ui.scene_active ? 76 : FRIDAY_MESSAGE_HEIGHT);
    lv_obj_align(ui.message, LV_ALIGN_TOP_MID, 0, ui.scene_active ? 154 : 550);
}

static bool supports_glyph(uint32_t codepoint, void *context)
{
    const lv_font_t *font = context;
    lv_font_glyph_dsc_t glyph;
    return font != NULL && font->get_glyph_dsc != NULL &&
        font->get_glyph_dsc(font, &glyph, codepoint, 0);
}

static void display_label(lv_obj_t *label, const char *text)
{
    const char *source = text != NULL ? text : "";
    size_t length = strlen(source);
    if (length > (SIZE_MAX - 1) / 4) return;
    char *readable = malloc(length * 4 + 1);
    if (readable == NULL) {
        lv_label_set_text(label, "Display text unavailable");
        return;
    }
    friday_display_text(readable, length * 4 + 1, source, supports_glyph,
                        (void *)lv_obj_get_style_text_font(label, 0));
    lv_label_set_text(label, readable);
    free(readable);
}

/* Keep lyric controls fixed. Wrap first, then reduce only to the readable
 * 24px font; unusually long lines remain complete in a vertical swipe area. */
static void layout_lyric(bool reset_position)
{
    lv_label_set_long_mode(ui.message, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_style_text_letter_space(ui.message,0,0);
    lv_obj_set_style_text_line_space(ui.message,4,0);
    lv_obj_set_size(ui.message,624,100);
    const lv_font_t *font=&lv_font_montserrat_32;
    lv_point_t size;
    lv_text_get_size(&size,lv_label_get_text(ui.message),font,0,4,624,LV_TEXT_FLAG_NONE);
    if(size.y>100) {
        font=&lv_font_montserrat_24;
        lv_text_get_size(&size,lv_label_get_text(ui.message),font,0,4,624,LV_TEXT_FLAG_NONE);
    }
    lv_obj_set_style_text_font(ui.message,font,0);
    lv_obj_set_scroll_dir(ui.message,LV_DIR_VER);
    lv_obj_set_scrollbar_mode(ui.message,LV_SCROLLBAR_MODE_AUTO);
    if(size.y>100)lv_obj_add_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);
    else lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);
    display_label(ui.lyric_caption,size.y>100?"CURRENT LINE - swipe for more":"CURRENT LINE");
    if(reset_position)lv_obj_scroll_to_y(ui.message,0,LV_ANIM_OFF);
}

static void set_message_text(const char *text)
{
    display_label(ui.message, text);
    position_message();
}

static void apply_state(friday_state_t state)
{
    friday_state_set(state);
    if(overlay.active&&ui.scene_active&&!strcmp(ui.scene_kind,"media")&&
       (state==FRIDAY_STATE_LISTENING||state==FRIDAY_STATE_THINKING||state==FRIDAY_STATE_SPEAKING)) {
        for(int i=0;i<4;i++){lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);}
        scene_layout(false);lv_snprintf(ui.scene_kind,sizeof(ui.scene_kind),"ambient");display_label(ui.eyebrow,"FRIDAY");
    }
    friday_presence_set_state(ui.presence, state);
    const char *states[] = {"Here when you need me", "Listening carefully", "Connecting the dots", "Speaking", "Something needs your attention"};
    const char *headings[] = {"A little more space\nfor your day.", "I'm listening.", "Let me connect\nthe dots.", "Here's what I found.", "A moment of\nyour attention."};
    int index=state>=FRIDAY_STATE_IDLE&&state<=FRIDAY_STATE_ALERT?state:FRIDAY_STATE_IDLE;
    display_label(ui.status, states[index]);
    if (!ui.scene_active) {
        display_label(ui.title, headings[index]);
        if (state==FRIDAY_STATE_LISTENING) set_message_text("Go ahead. I'll keep up.");
        else if (state==FRIDAY_STATE_THINKING) set_message_text("A little thought before a useful answer.");
        else if (state==FRIDAY_STATE_ALERT) set_message_text("Let's take care of this.");
    }
    ESP_LOGI(TAG, "State changed to %s", friday_state_name(state));
}

static void log_ui_snapshot(const char *reason)
{
    lv_obj_update_layout(lv_screen_active());
    int cards = 0, buttons = 0, bounds_errors = 0, routed = 0;
    for (int i = 0; i < 4; i++) {
        lv_obj_t *objects[] = {ui.cards[i], ui.buttons[i]};
        for (int j = 0; j < 2; j++) {
            if (objects[j] != NULL) {
                lv_area_t diagnostic_area; lv_obj_get_coords(objects[j], &diagnostic_area);
                ESP_LOGI(TAG, "UI_OBJ type=%s index=%d hidden=%d visible=%d x1=%ld y1=%ld x2=%ld y2=%ld",
                    j == 0 ? "card" : "button", i, lv_obj_has_flag(objects[j], LV_OBJ_FLAG_HIDDEN),
                    lv_obj_is_visible(objects[j]), (long)diagnostic_area.x1, (long)diagnostic_area.y1,
                    (long)diagnostic_area.x2, (long)diagnostic_area.y2);
            }
            if (objects[j] != NULL && lv_obj_is_visible(objects[j])) {
                lv_area_t area; lv_obj_get_coords(objects[j], &area);
                if (j == 0) cards++; else buttons++;
                if (area.x1 < 0 || area.y1 < 0 || area.x2 >= 720 || area.y2 >= 720) bounds_errors++;
                if (lv_obj_has_flag(objects[j], LV_OBJ_FLAG_CLICKABLE)) routed++;
            }
        }
    }
    ESP_LOGI(TAG, "UI_DIAG reason=%s generation=%lu kind=%s active=%d state=%s cards=%d buttons=%d bounds_errors=%d clickable=%d screen_objects=%u",
        reason, ui.scene_generation, ui.scene_kind, ui.scene_active, friday_state_name(friday_state_get()),
        cards, buttons, bounds_errors, routed, (unsigned int)lv_obj_get_child_count(lv_screen_active()));
}

static void diagnostic_tick(lv_timer_t *timer)
{
    (void)timer;
    log_ui_snapshot("periodic");
}

static void presence_event_cb(lv_event_t *event)
{
    if (lv_event_get_code(event) == LV_EVENT_LONG_PRESSED) {
        ESP_LOGI(TAG, "UI_ACTION presence=long_press");
        ui.long_pressed = true;
        if (action_callback != NULL && !action_callback("status")) {
            set_message_text("Friday is offline. Try again after reconnecting.");
        }
        return;
    }
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) {
        return;
    }

    ESP_LOGI(TAG, "UI_ACTION presence=click suppressed=%d", ui.long_pressed);
    if (ui.long_pressed) {
        ui.long_pressed = false;
        return;
    }
    if (activation_callback != NULL) {
        friday_ui_activation_result_t result = activation_callback();
        if (result == FRIDAY_UI_ACTIVATION_ACKNOWLEDGED) {
            apply_state(FRIDAY_STATE_IDLE);
            set_message_text("Reminder acknowledged");
            return;
        }
        if (result == FRIDAY_UI_ACTIVATION_UNCHANGED) {
            return;
        }
        bool recording = result == FRIDAY_UI_ACTIVATION_RECORDING;
        apply_state(recording ? FRIDAY_STATE_LISTENING : FRIDAY_STATE_THINKING);
        if (recording) {
            set_message_text("Listening - pause when you are finished");
        }
    }
}

static lv_obj_t *make_label(lv_obj_t *parent, const char *text, const lv_font_t *font,
                            uint32_t color)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_obj_set_style_text_font(label, font, 0);
    display_label(label, text);
    lv_obj_set_style_text_color(label, color_hex(color), 0);
    lv_obj_set_style_text_letter_space(label, 0, 0);
    return label;
}

static void scene_action_event(lv_event_t *event)
{
    const char *action = lv_event_get_user_data(event);
    if (action == NULL || action[0] == '\0') {
        return;
    }
    char category[24];
    size_t length = strcspn(action, ":");
    if (length >= sizeof(category)) length = sizeof(category) - 1;
    memcpy(category, action, length); category[length] = '\0';
    const char *allowed[] = {"status", "reminders", "calendar", "dismiss", "confirm", "decline", "reminder", "snooze", "complete", "cancel"};
    const char *safe_category = "invalid";
    for (unsigned int i = 0; i < sizeof(allowed)/sizeof(allowed[0]); i++) if (strcmp(category, allowed[i]) == 0) safe_category = allowed[i];
    ESP_LOGI(TAG, "UI_ACTION action=%s", safe_category);
    if (strcmp(action,"dismiss")==0&&overlay.active) {
        if(!overlay.protected&&action_callback)action_callback("media.resume");
        return;
    }
    if (strcmp(action, "dismiss") == 0) {
        scene_expire(NULL);
        log_ui_snapshot("dismiss");
        if (action_callback != NULL) action_callback("dismiss");
    } else if (action_callback == NULL || !action_callback(action)) {
        set_message_text("Friday is offline. This action was not sent.");
    }
}

static void scene_layout(bool active)
{
    ui.scene_active=active;
    lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(ui.message,LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_style_text_line_space(ui.message,0,0);
    lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_DOTS);
    lv_obj_t *media[]={ui.cover,ui.artist,ui.media_state,ui.media_time,ui.attribution,ui.lyric_caption,ui.progress};
    for(unsigned i=0;i<sizeof(media)/sizeof(media[0]);i++) if(media[i])lv_obj_add_flag(media[i],LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_text_font(ui.title,active?&lv_font_montserrat_32:&lv_font_montserrat_48,0);
    lv_obj_set_size(ui.title,active?520:624,active?38:132);
    lv_obj_align(ui.title,LV_ALIGN_TOP_LEFT,48,active?84:138);
    lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_24,0);
    lv_obj_set_style_text_letter_space(ui.message,active?2:0,0);
    lv_obj_set_style_text_align(ui.message,active?LV_TEXT_ALIGN_CENTER:LV_TEXT_ALIGN_LEFT,0);
    position_message();
    lv_obj_set_size(ui.presence,active?96:648,active?48:232);
    lv_obj_align(ui.presence,LV_ALIGN_TOP_LEFT,active?576:36,active?28:300);
    friday_presence_set_compact(ui.presence,active);
    lv_obj_set_style_text_letter_space(ui.eyebrow,active?1:3,0);
    lv_obj_align(ui.eyebrow,LV_ALIGN_TOP_LEFT,48,active?38:40);
    if(active) {
        lv_obj_add_flag(ui.microphone,LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.network,LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.status,LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_remove_flag(ui.microphone,LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(ui.network,LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(ui.status,LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
    }
    for(int i=0;i<4;i++) {
        lv_obj_set_size(ui.cards[i],624,64);
        lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,48,238+i*76);
        lv_obj_set_style_bg_opa(ui.cards[i],LV_OPA_COVER,0);
        lv_obj_set_size(ui.card_labels[i],464,38);
        lv_obj_align(ui.card_labels[i],LV_ALIGN_LEFT_MID,136,0);
        lv_obj_set_style_text_font(ui.card_labels[i],&lv_font_montserrat_24,0);
        lv_obj_set_style_text_color(ui.card_labels[i],color_hex(0xEEECE7),0);
        lv_obj_set_style_text_align(ui.card_labels[i],LV_TEXT_ALIGN_LEFT,0);
        lv_obj_set_size(ui.buttons[i],300,58);
        lv_obj_align(ui.buttons[i],LV_ALIGN_TOP_LEFT,48+(i%2)*324,558+(i/2)*70);
        lv_obj_set_style_bg_color(ui.buttons[i],color_hex(0x23272F),0);
        lv_obj_set_style_text_font(ui.button_labels[i],&lv_font_montserrat_24,0);
        lv_obj_set_style_text_color(ui.button_labels[i],color_hex(0xEEECE7),0);
        lv_obj_set_size(ui.button_labels[i],276,32);
    }
}

static void scene_expire(lv_timer_t *timer)
{
    if(overlay.active){
        if(!overlay.protected&&action_callback)action_callback("media.resume");
        return;
    }
    friday_ui_media_clear();
    (void)timer;
    if (ui.countdown != NULL) {
        lv_timer_delete(ui.countdown);
        ui.countdown = NULL;
    }
    for (int i = 0; i < 4; i++) {
        lv_obj_add_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
    }
    scene_layout(false);
    lv_snprintf(ui.scene_kind, sizeof(ui.scene_kind), "%s", "ambient");
    ui.scene_generation++;
    display_label(ui.eyebrow, "Friday");
    display_label(ui.title, "A little more space\nfor your day.");
    set_message_text("Say Friday or touch the light.\nHold for local views.");
    apply_state(FRIDAY_STATE_IDLE);
}

void friday_ui_set_action_callback(friday_ui_action_callback_t callback)
{
    action_callback = callback;
}

static const char *scene_string(const cJSON *object, const char *name)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    return cJSON_IsString(value) ? value->valuestring : "";
}

static void countdown_tick(lv_timer_t *timer)
{
    (void)timer;
    int64_t remaining = (ui.countdown_deadline - esp_timer_get_time() + 999999) / 1000000;
    if (remaining < 0) remaining = 0;
    char text[64];
    lv_snprintf(text, sizeof(text), "%02ld:%02ld", (long)(remaining / 60), (long)(remaining % 60));
    display_label(ui.card_labels[0], text);
    if (remaining == 0) {
        lv_timer_pause(ui.countdown);
        set_message_text("Timer finished. Friday will send the reminder alert.");
    }
}

bool friday_ui_set_scene(const cJSON *surface)
{
    if (!friday_ui_set_surface(scene_string(surface, "kind"), scene_string(surface, "title"),
                               scene_string(surface, "subtitle"))) return false;
    if (!bsp_display_lock(1000)) {
        return false;
    }
    const cJSON *items = cJSON_GetObjectItemCaseSensitive(surface, "items");
    const cJSON *actions = cJSON_GetObjectItemCaseSensitive(surface, "actions");
    for (int i = 0; i < 4; i++) {
        const cJSON *item = cJSON_GetArrayItem(items, i);
        const cJSON *action = cJSON_GetArrayItem(actions, i);
        ui.card_actions[i][0] = '\0';
        ui.button_actions[i][0] = '\0';
        if (cJSON_IsObject(item)) {
            char time[64], label[240], text[308];
            friday_display_text(time, sizeof(time), scene_string(item, "time"), supports_glyph, (void *)&lv_font_montserrat_24);
            friday_display_text(label, sizeof(label), scene_string(item, "label"), supports_glyph, (void *)&lv_font_montserrat_24);
            lv_snprintf(text, sizeof(text), "%s", label);
            display_label(ui.card_times[i], time);
            lv_obj_set_style_text_font(ui.card_labels[i], &lv_font_montserrat_24, 0);
            display_label(ui.card_labels[i], text);
            lv_snprintf(ui.card_actions[i], sizeof(ui.card_actions[i]), "%s", scene_string(item, "action"));
            if (ui.card_actions[i][0]) lv_obj_add_flag(ui.cards[i], LV_OBJ_FLAG_CLICKABLE);
            else lv_obj_remove_flag(ui.cards[i], LV_OBJ_FLAG_CLICKABLE);
            lv_obj_remove_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        }
        if (cJSON_IsObject(action)) {
            const char *button_text = scene_string(action, "label");
            if (strcmp(scene_string(surface, "kind"), "media") == 0) {
                if (strcmp(button_text, "Previous") == 0) button_text = LV_SYMBOL_PREV "  Previous";
                else if (strcmp(button_text, "Next") == 0) button_text = LV_SYMBOL_NEXT "  Next";
                else if (strcmp(button_text, "Pause") == 0) button_text = LV_SYMBOL_PAUSE "  Pause";
                else if (strcmp(button_text, "Resume") == 0) button_text = LV_SYMBOL_PLAY "  Resume";
            }
            display_label(ui.button_labels[i], button_text);
            lv_snprintf(ui.button_actions[i], sizeof(ui.button_actions[i]), "%s", scene_string(action, "id"));
            if (ui.button_actions[i][0]&&!(overlay.active&&overlay.protected&&!strcmp(ui.button_actions[i],"dismiss"))) lv_obj_remove_state(ui.buttons[i], LV_STATE_DISABLED);
            else lv_obj_add_state(ui.buttons[i], LV_STATE_DISABLED);
            lv_obj_remove_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
    if (strcmp(scene_string(surface,"kind"),"media")==0) {
        lv_obj_add_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_t *media[]={ui.cover,ui.artist,ui.media_state,ui.media_time,ui.attribution,ui.lyric_caption};
        for(unsigned i=0;i<sizeof(media)/sizeof(media[0]);i++)lv_obj_remove_flag(media[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(ui.title,320,112);lv_obj_align(ui.title,LV_ALIGN_TOP_LEFT,352,96);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_32,0);
        lv_obj_set_style_text_letter_space(ui.message,0,0);
        lv_obj_set_style_text_align(ui.message,LV_TEXT_ALIGN_LEFT,0);
        if(!owned_media_render||strcmp(scene_string(surface,"subtitle"),last_media_line))display_label(ui.message,scene_string(surface,"subtitle"));
        lv_obj_set_size(ui.message,624,100);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,404);
        layout_lyric(!owned_media_render||strcmp(scene_string(surface,"subtitle"),last_media_line));
        const char *artist="",*state="Playback unavailable",*source="",*stamp="";
        for(int i=0;i<4;i++) {
            const cJSON *item=cJSON_GetArrayItem(items,i);const char *kind=scene_string(item,"time");
            if(!strcmp(kind,"Artist"))artist=scene_string(item,"label");
            else if(!strcmp(kind,"State"))state=scene_string(item,"label");
            else if(!strcmp(kind,"Source"))source=scene_string(item,"label");
            else if(!strcmp(kind,"Progress"))stamp=scene_string(item,"label");
        }
        display_label(ui.artist,artist);display_label(ui.media_state,state);
        display_label(ui.media_time,stamp);
        if(!strstr(source,"Spotify")&&!strstr(source,"synthetic")){char attribution[320];lv_snprintf(attribution,sizeof(attribution),"Spotify | %s",source);display_label(ui.attribution,attribution);}
        else display_label(ui.attribution,source);
        lv_obj_add_flag(ui.progress,LV_OBJ_FLAG_HIDDEN);
        int em,es,dm,ds;
        if(sscanf(stamp,"%4d:%2d / %4d:%2d",&em,&es,&dm,&ds)==4&&em>=0&&em<=1440&&es>=0&&es<60&&dm>=0&&dm<=1440&&ds>=0&&ds<60&&(dm*60+ds)>0) {
            lv_bar_set_value(ui.progress,(int)(((int64_t)em*60+es)*100/(dm*60+ds)),LV_ANIM_OFF);
            lv_obj_remove_flag(ui.progress,LV_OBJ_FLAG_HIDDEN);
        }
        for(int i=0;i<4;i++) {
            lv_obj_set_style_bg_color(ui.buttons[i],color_hex(0x23272F),0);lv_obj_set_style_text_color(ui.button_labels[i],color_hex(0xEEECE7),0);
            const char *label=scene_string(cJSON_GetArrayItem(actions,i),"label");
            int left=48+i*156,width=144;
            if(!strcmp(label,"Previous")) {left=48;width=120;display_label(ui.button_labels[i],LV_SYMBOL_PREV);}
            else if(!strcmp(label,"Pause")||!strcmp(label,"Resume")||!strcmp(label,"Play")) {
                left=180;width=208;display_label(ui.button_labels[i],!strcmp(label,"Pause")?LV_SYMBOL_PAUSE:LV_SYMBOL_PLAY);
                lv_obj_set_style_bg_color(ui.buttons[i],color_hex(0xE8E4DC),0);lv_obj_set_style_text_color(ui.button_labels[i],color_hex(0x15171D),0);
            } else if(!strcmp(label,"Next")) {left=400;width=120;display_label(ui.button_labels[i],LV_SYMBOL_NEXT);}
            else if(!strcmp(scene_string(cJSON_GetArrayItem(actions,i),"id"),"dismiss")) {left=532;width=140;display_label(ui.button_labels[i],LV_SYMBOL_CLOSE);}
            lv_obj_set_size(ui.buttons[i],width,72);lv_obj_align(ui.buttons[i],LV_ALIGN_TOP_LEFT,left,588);
            lv_obj_set_size(ui.button_labels[i],width-24,38);lv_obj_set_style_text_font(ui.button_labels[i],&lv_font_montserrat_32,0);lv_obj_center(ui.button_labels[i]);
        }
        /* Accepted media alone may update the retained cover. Invalid data is never exposed. */
        lv_obj_add_flag(ui.cover_image,LV_OBJ_FLAG_HIDDEN);lv_image_cache_drop(&ui.cover_dsc);
        bool cover_ready=owned_media_render&&friday_artwork_update(&ui.artwork,surface);
        if(cover_ready) {
            ui.cover_dsc.header.magic=LV_IMAGE_HEADER_MAGIC;ui.cover_dsc.header.cf=LV_COLOR_FORMAT_RGB565;
            ui.cover_dsc.header.w=192;ui.cover_dsc.header.h=192;ui.cover_dsc.header.stride=384;
            ui.cover_dsc.data_size=FRIDAY_ARTWORK_BYTES;ui.cover_dsc.data=ui.artwork.pixels;
            lv_image_set_src(ui.cover_image,&ui.cover_dsc);lv_obj_remove_flag(ui.cover_image,LV_OBJ_FLAG_HIDDEN);
        } else display_label(ui.cover_label,!strcmp(scene_string(cJSON_GetObjectItemCaseSensitive(surface,"artwork"),"status"),"loading")?"Cover loading":"No cover available");
    }
    const cJSON *countdown = cJSON_GetObjectItemCaseSensitive(surface, "countdown_ms");
    if (cJSON_IsNumber(countdown) && countdown->valuedouble >= 1000 && countdown->valuedouble <= 86400000) {
        ui.countdown_deadline = esp_timer_get_time() + (int64_t)countdown->valuedouble * 1000;
        lv_obj_set_style_text_font(ui.card_labels[0], &lv_font_montserrat_32, 0);
        ui.countdown = lv_timer_create(countdown_tick, 250, NULL);
        lv_obj_remove_flag(ui.cards[0], LV_OBJ_FLAG_HIDDEN);
        countdown_tick(ui.countdown);
    }
    if (ui.expiry != NULL) {
        lv_timer_delete(ui.expiry);
        ui.expiry = NULL;
    }
    const cJSON *expires = cJSON_GetObjectItemCaseSensitive(surface, "expires_ms");
    // Reminder alerts have their own acknowledgement-aware backend timer.
    if (cJSON_IsNumber(expires) && expires->valueint >= 1000 && expires->valueint <= 300000
        && strcmp(scene_string(surface, "kind"), "reminder") != 0
        && strcmp(scene_string(surface, "kind"), "media") != 0) {
        ui.expiry = lv_timer_create(scene_expire, (uint32_t)expires->valueint, NULL);
        lv_timer_set_repeat_count(ui.expiry, 1);
        lv_timer_set_auto_delete(ui.expiry, false);
    }
    log_ui_snapshot("scene");
    bsp_display_unlock();
    return true;
}

void friday_ui_create(void)
{
    lv_obj_t *screen=lv_screen_active();lv_obj_remove_style_all(screen);
    lv_obj_set_size(screen,720,720);lv_obj_set_style_bg_color(screen,color_hex(0x111319),0);
    lv_obj_set_style_bg_opa(screen,LV_OPA_COVER,0);lv_obj_remove_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
    ui.eyebrow=make_label(screen,"FRIDAY",&lv_font_montserrat_18,0xC9C6C1);
    ui.title=make_label(screen,"A little more space\nfor your day.",&lv_font_montserrat_48,0xEEECE7);
    lv_label_set_long_mode(ui.title,LV_LABEL_LONG_MODE_DOTS);
    ui.presence=friday_presence_create(screen);lv_obj_add_flag(ui.presence,LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(ui.presence,presence_event_cb,LV_EVENT_ALL,NULL);
    ui.status=make_label(screen,"Here when you need me",&lv_font_montserrat_18,0xAAA8A5);
    lv_obj_align(ui.status,LV_ALIGN_TOP_LEFT,48,648);
    ui.message=make_label(screen,"",&lv_font_montserrat_24,0xC9C6C1);
    lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_DOTS);
    ui.microphone=make_label(screen,"Microphone starting",&lv_font_montserrat_18,0x757B86);
    lv_obj_align(ui.microphone,LV_ALIGN_TOP_LEFT,48,676);
    ui.network=make_label(screen,"Connecting",&lv_font_montserrat_18,0x757B86);
    lv_obj_set_size(ui.network,360,28);lv_obj_set_style_text_align(ui.network,LV_TEXT_ALIGN_RIGHT,0);
    lv_label_set_long_mode(ui.network,LV_LABEL_LONG_MODE_DOTS);lv_obj_align(ui.network,LV_ALIGN_TOP_RIGHT,-48,40);
    ui.fact_panel=lv_obj_create(screen);lv_obj_remove_style_all(ui.fact_panel);
    lv_obj_set_size(ui.fact_panel,656,104);lv_obj_align(ui.fact_panel,LV_ALIGN_TOP_LEFT,32,138);
    lv_obj_set_style_bg_color(ui.fact_panel,color_hex(0x191C23),0);lv_obj_set_style_bg_opa(ui.fact_panel,LV_OPA_COVER,0);
    lv_obj_set_style_radius(ui.fact_panel,20,0);lv_obj_remove_flag(ui.fact_panel,LV_OBJ_FLAG_CLICKABLE);
    lv_obj_move_to_index(ui.fact_panel,0);
    for(int i=0;i<4;i++) {
        ui.cards[i]=lv_obj_create(screen);lv_obj_remove_style_all(ui.cards[i]);
        lv_obj_set_style_bg_color(ui.cards[i],color_hex(0x1D2028),0);lv_obj_set_style_bg_opa(ui.cards[i],LV_OPA_COVER,0);
        lv_obj_set_style_radius(ui.cards[i],20,0);lv_obj_remove_flag(ui.cards[i],LV_OBJ_FLAG_SCROLLABLE);
        ui.card_times[i]=make_label(ui.cards[i],"",&lv_font_montserrat_18,0x9399A5);
        lv_obj_set_size(ui.card_times[i],104,28);lv_obj_align(ui.card_times[i],LV_ALIGN_LEFT_MID,20,0);lv_label_set_long_mode(ui.card_times[i],LV_LABEL_LONG_MODE_DOTS);
        ui.card_labels[i]=make_label(ui.cards[i],"",&lv_font_montserrat_24,0xEEECE7);lv_label_set_long_mode(ui.card_labels[i],LV_LABEL_LONG_MODE_DOTS);
        lv_obj_add_event_cb(ui.cards[i],scene_action_event,LV_EVENT_CLICKED,ui.card_actions[i]);lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
        ui.buttons[i]=lv_button_create(screen);lv_obj_set_style_radius(ui.buttons[i],20,0);lv_obj_set_style_shadow_width(ui.buttons[i],0,0);
        lv_obj_set_style_border_width(ui.buttons[i],1,0);lv_obj_set_style_border_color(ui.buttons[i],color_hex(0x343943),0);
        lv_obj_set_style_pad_all(ui.buttons[i],0,0);ui.button_labels[i]=make_label(ui.buttons[i],"",&lv_font_montserrat_24,0xEEECE7);
        lv_obj_set_style_text_align(ui.button_labels[i],LV_TEXT_ALIGN_CENTER,0);lv_label_set_long_mode(ui.button_labels[i],LV_LABEL_LONG_MODE_DOTS);lv_obj_center(ui.button_labels[i]);
        lv_obj_add_event_cb(ui.buttons[i],scene_action_event,LV_EVENT_CLICKED,ui.button_actions[i]);lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
    }
    ui.cover=lv_obj_create(screen);lv_obj_remove_style_all(ui.cover);lv_obj_set_size(ui.cover,272,272);lv_obj_align(ui.cover,LV_ALIGN_TOP_LEFT,48,88);
    lv_obj_set_style_bg_color(ui.cover,color_hex(0x222730),0);lv_obj_set_style_bg_opa(ui.cover,LV_OPA_COVER,0);lv_obj_set_style_radius(ui.cover,24,0);lv_obj_set_style_clip_corner(ui.cover,true,0);
    lv_obj_remove_flag(ui.cover,LV_OBJ_FLAG_CLICKABLE);lv_obj_remove_flag(ui.cover,LV_OBJ_FLAG_SCROLLABLE);
    ui.cover_label=make_label(ui.cover,"No cover available",&lv_font_montserrat_18,0x929AA7);lv_obj_set_width(ui.cover_label,220);lv_obj_set_style_text_align(ui.cover_label,LV_TEXT_ALIGN_CENTER,0);lv_obj_center(ui.cover_label);
    ui.cover_image=lv_image_create(ui.cover);lv_image_set_pivot(ui.cover_image,0,0);lv_image_set_scale(ui.cover_image,342);lv_image_set_antialias(ui.cover_image,true);lv_obj_align(ui.cover_image,LV_ALIGN_TOP_LEFT,8,8);
    ui.artist=make_label(screen,"",&lv_font_montserrat_24,0xB7B4AF);lv_obj_set_size(ui.artist,320,64);lv_obj_align(ui.artist,LV_ALIGN_TOP_LEFT,352,216);lv_label_set_long_mode(ui.artist,LV_LABEL_LONG_MODE_DOTS);
    ui.media_state=make_label(screen,"",&lv_font_montserrat_18,0x9BCAA8);lv_obj_set_size(ui.media_state,320,48);lv_obj_align(ui.media_state,LV_ALIGN_TOP_LEFT,352,296);lv_label_set_long_mode(ui.media_state,LV_LABEL_LONG_MODE_DOTS);
    ui.lyric_caption=make_label(screen,"CURRENT LINE",&lv_font_montserrat_18,0x757B86);lv_obj_set_style_text_letter_space(ui.lyric_caption,2,0);lv_obj_align(ui.lyric_caption,LV_ALIGN_TOP_LEFT,48,372);
    ui.progress=lv_bar_create(screen);lv_obj_set_size(ui.progress,624,4);lv_obj_align(ui.progress,LV_ALIGN_TOP_LEFT,48,534);
    lv_obj_set_style_bg_color(ui.progress,color_hex(0x343943),LV_PART_MAIN);lv_obj_set_style_bg_color(ui.progress,color_hex(0xC9C6C1),LV_PART_INDICATOR);lv_obj_remove_flag(ui.progress,LV_OBJ_FLAG_CLICKABLE);
    ui.media_time=make_label(screen,"",&lv_font_montserrat_18,0x8D939E);lv_obj_set_size(ui.media_time,624,28);lv_obj_align(ui.media_time,LV_ALIGN_TOP_LEFT,48,548);lv_label_set_long_mode(ui.media_time,LV_LABEL_LONG_MODE_DOTS);
    ui.attribution=make_label(screen,"",&lv_font_montserrat_18,0x757B86);lv_obj_set_size(ui.attribution,624,26);lv_obj_align(ui.attribution,LV_ALIGN_TOP_LEFT,48,682);lv_label_set_long_mode(ui.attribution,LV_LABEL_LONG_MODE_DOTS);
    scene_layout(false);set_message_text("Say Friday or touch the light.\nHold for local views.");apply_state(FRIDAY_STATE_IDLE);
    lv_timer_create(diagnostic_tick,10000,NULL);
}

void friday_ui_set_activation_callback(friday_ui_activation_callback_t callback)
{
    activation_callback = callback;
}

void friday_ui_set_state(friday_state_t state)
{
    if (ui.status == NULL || !bsp_display_lock(1000)) {
        return;
    }
    apply_state(state);
    log_ui_snapshot("state");
    bsp_display_unlock();
}

bool friday_ui_set_surface(const char *kind, const char *title, const char *subtitle)
{
    if (ui.title == NULL || !bsp_display_lock(1000)) {
        return false;
    }
    if (!owned_media_render) {
        if(!overlay.active)friday_ui_media_clear();
        lv_label_set_long_mode(ui.message, LV_LABEL_LONG_MODE_DOTS);
    }
    if (ui.countdown != NULL) {
        lv_timer_delete(ui.countdown);
        ui.countdown = NULL;
    }
    if (ui.expiry != NULL) {
        lv_timer_delete(ui.expiry);
        ui.expiry = NULL;
    }
    for (int i = 0; i < 4; i++) {
        lv_obj_add_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
    }
    const char *known[] = {"ambient", "AMBIENT", "timeline", "message", "metric", "list", "reminder", "timer", "media", "ERROR", "PAIRING", "TOOL"};
    const char *safe_kind = "other";
    for (unsigned int i = 0; i < sizeof(known)/sizeof(known[0]); i++) if (kind != NULL && strcmp(kind, known[i]) == 0) safe_kind = known[i];
    bool media_continuation = owned_media_render && ui.scene_active &&
        strcmp(ui.scene_kind, "media") == 0 && strcmp(safe_kind, "media") == 0;
    lv_snprintf(ui.scene_kind, sizeof(ui.scene_kind), "%s", safe_kind);
    ui.scene_generation++;
    if (!media_continuation)
        scene_layout(kind != NULL && strcmp(kind, "ambient") != 0 && strcmp(kind, "AMBIENT") != 0);
    if (kind != NULL && kind[0] != '\0') {
        display_label(ui.eyebrow, strcmp(kind, "media") == 0 ? "Music" :
            strcmp(kind, "timeline") == 0 ? "Your day" :
            strcmp(kind, "metric") == 0 ? "Device status" :
            strcmp(kind, "message") == 0 ? "For your review" :
            strcmp(kind, "reminder") == 0 ? "A gentle nudge" :
            strcmp(kind, "timer") == 0 ? "Focus" : kind);
        lv_obj_align(ui.eyebrow, LV_ALIGN_TOP_LEFT, 48, 38);
    }
    if (title != NULL && title[0] != '\0') {
        display_label(ui.title, title);
        lv_obj_align(ui.title, LV_ALIGN_TOP_LEFT, 48, ui.scene_active ? 84 : 138);
    }
    if (subtitle != NULL && subtitle[0] != '\0' && (!owned_media_render || strcmp(subtitle,last_media_line) != 0)) {
        set_message_text(subtitle);
    }
    bsp_display_unlock();
    return true;
}

bool friday_ui_set_response(const char *text)
{
    // Keep the director's cards and touch actions when assistant.text follows surface.
    if (ui.message != NULL && bsp_display_lock(1000)) {
        set_message_text(text);
        bsp_display_unlock();
        return true;
    }
    return false;
}

void friday_ui_set_tool(const char *name, const char *status)
{
    char detail[96];
    lv_snprintf(detail, sizeof(detail), "%s  %s",
                name != NULL ? name : "tool", status != NULL ? status : "working");
    friday_ui_set_surface("TOOL", "At your service", detail);
}

void friday_ui_set_backend(const char *status)
{
    if (ui.network == NULL || !bsp_display_lock(1000)) {
        return;
    }
    display_label(ui.network, status != NULL ? status : "FRIDAY OFFLINE");
    lv_obj_align(ui.network, LV_ALIGN_TOP_RIGHT, -48, 40);
    bsp_display_unlock();
}

void friday_ui_set_network(const char *status, const char *detail)
{
    if (ui.network == NULL || !bsp_display_lock(1000)) {
        return;
    }

    display_label(ui.network, status);
    lv_obj_align(ui.network, LV_ALIGN_TOP_RIGHT, -48, 40);
    if (detail != NULL && detail[0] != '\0') {
        set_message_text(detail);
    }
    bsp_display_unlock();
}

void friday_ui_set_microphone(const char *status, int32_t peak, int32_t rms)
{
    /* Capture runs every 20 ms. Meter/status refresh is best effort: never
     * wait behind a scene render while the I2S receive queue fills. */
    if (ui.microphone == NULL || !bsp_display_lock(0)) {
        return;
    }

    (void)peak;
    friday_presence_set_level(ui.presence,rms);
    display_label(ui.microphone,status&&strstr(status,"READY")?"Microphone ready":status);
    lv_obj_align(ui.microphone,LV_ALIGN_TOP_LEFT,48,676);
    bsp_display_unlock();
}

/* BSP display lock is recursive (esp_lvgl_adapter 0.6.3). It serializes touch,
 * expiry and the complete acceptance/render operation, closing in-flight races. */
void friday_ui_media_begin(const char *request)
{
    if (!bsp_display_lock(-1)) return;
    last_media_line[0] = '\0';
    friday_media_overlay_clear(&overlay);overlay_action_pending=false;overlay_resolution_pending=false;overlay_memory_decision_pending=false;
    lv_obj_add_flag(ui.cover_image,LV_OBJ_FLAG_HIDDEN);lv_image_cache_drop(&ui.cover_dsc);friday_artwork_clear(&ui.artwork);
    friday_media_begin(&media_guard, request);
    bsp_display_unlock();
}
void friday_ui_media_clear(void)
{
    if(!bsp_display_lock(-1))return;
    friday_media_clear(&media_guard);friday_media_overlay_clear(&overlay);overlay_action_pending=false;overlay_resolution_pending=false;overlay_memory_decision_pending=false;
    last_media_line[0]=0;
    if(ui.cover_image){lv_obj_add_flag(ui.cover_image,LV_OBJ_FLAG_HIDDEN);lv_image_cache_drop(&ui.cover_dsc);}
    friday_artwork_clear(&ui.artwork);
    if(ui.scene_active&&!strcmp(ui.scene_kind,"media")) {
        display_label(ui.title,"Music");display_label(ui.cover_label,"No cover available");display_label(ui.artist,"");display_label(ui.media_state,"Waiting for a fresh playback update");
        display_label(ui.message,"Playback is unavailable right now.");display_label(ui.media_time,"");
        lv_obj_add_flag(ui.progress,LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<4;i++)if(!strncmp(ui.button_actions[i],"musicctl:",9))lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
        lv_snprintf(ui.button_actions[0],sizeof(ui.button_actions[0]),"music");display_label(ui.button_labels[0],LV_SYMBOL_REFRESH);
        lv_obj_set_size(ui.buttons[0],208,72);lv_obj_align(ui.buttons[0],LV_ALIGN_TOP_LEFT,180,588);
        lv_obj_set_size(ui.button_labels[0],184,38);lv_obj_center(ui.button_labels[0]);lv_obj_set_style_text_font(ui.button_labels[0],&lv_font_montserrat_32,0);
        lv_obj_set_style_bg_color(ui.buttons[0],color_hex(0xE8E4DC),0);lv_obj_set_style_text_color(ui.button_labels[0],color_hex(0x15171D),0);
        lv_obj_remove_state(ui.buttons[0],LV_STATE_DISABLED);lv_obj_remove_flag(ui.buttons[0],LV_OBJ_FLAG_HIDDEN);
    }
    bsp_display_unlock();
}
bool friday_ui_media_visible(void){if(!bsp_display_lock(-1))return false;bool visible=ui.scene_active&&!strcmp(ui.scene_kind,"media");bsp_display_unlock();return visible;}
bool friday_ui_media_suspended(void){if(!bsp_display_lock(-1))return false;bool active=overlay.active;bsp_display_unlock();return active;}
bool friday_ui_media_overlay_needs_resume(void){if(!bsp_display_lock(-1))return false;bool needed=overlay.active&&!overlay.protected&&overlay_resolution_pending;bsp_display_unlock();return needed;}
static bool protected_surface(const cJSON *surface)
{
    const cJSON *actions=cJSON_GetObjectItemCaseSensitive(surface,"actions");
    for(int i=0;i<4;i++) {
        const char *a=scene_string(cJSON_GetArrayItem(actions,i),"id");
        if(!strncmp(a,"memyes:",7)||!strncmp(a,"memno:",6)||!strncmp(a,"memreview:",10)||!strncmp(a,"confirm",7)||!strncmp(a,"decline",7))return true;
    }
    return !strcmp(scene_string(surface,"kind"),"reminder");
}
bool friday_ui_media_prepare_overlay(const cJSON *root)
{
    if(!bsp_display_lock(-1))return false;
    bool protected=!strcmp(scene_string(root,"type"),"reminder.alert")||protected_surface(cJSON_GetObjectItemCaseSensitive(root,"surface"));
    bool accepted=friday_media_overlay_suspend(&overlay,&media_guard,root,protected);
    if(accepted) {
        overlay_action_pending=false;overlay_resolution_pending=false;overlay_memory_decision_pending=false;last_media_line[0]=0;
        lv_obj_add_flag(ui.cover_image,LV_OBJ_FLAG_HIDDEN);lv_image_cache_drop(&ui.cover_dsc);friday_artwork_clear(&ui.artwork);
        if(ui.scene_active&&!strcmp(ui.scene_kind,"media")) {
            display_label(ui.message,"Music is paused while we take care of this.");display_label(ui.media_state,"Interaction in progress");lv_obj_add_flag(ui.progress,LV_OBJ_FLAG_HIDDEN);
            for(int i=0;i<4;i++)if(!strncmp(ui.button_actions[i],"musicctl:",9))lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
        }
    }
    bsp_display_unlock();return accepted;
}
void friday_ui_media_resume_result(const cJSON *root)
{if(!bsp_display_lock(-1))return;friday_media_overlay_result(&overlay,root);bsp_display_unlock();}
cJSON *friday_ui_media_resume_request(void)
{if(!bsp_display_lock(-1))return NULL;cJSON *root=friday_media_overlay_resume(&overlay);bsp_display_unlock();return root;}
void friday_ui_media_resume_sent(bool sent)
{if(!sent&&bsp_display_lock(-1)){overlay.resume_requested=false;overlay.ready=false;bsp_display_unlock();}}
void friday_ui_media_overlay_resolved(void)
{if(bsp_display_lock(-1)){if(overlay.active){overlay.protected=false;overlay_resolution_pending=true;}bsp_display_unlock();}}
void friday_ui_media_note_action(const char *action)
{
    if(!bsp_display_lock(-1))return;
    if(overlay.active&&action&&(!strncmp(action,"memyes:",7)||!strncmp(action,"memno:",6)||!strncmp(action,"confirm",7)||!strncmp(action,"decline",7)||!strncmp(action,"complete:",9)||!strncmp(action,"snooze:",7)||!strncmp(action,"cancel:",7))){overlay_action_pending=true;overlay_memory_decision_pending=!strncmp(action,"memyes:",7)||!strncmp(action,"memno:",6);}
    bsp_display_unlock();
}
void friday_ui_media_overlay_completed(const cJSON *root)
{
    if(!bsp_display_lock(-1))return;
    bool completed=!strcmp(scene_string(root,"status"),"completed")||(overlay_memory_decision_pending&&!strcmp(scene_string(root,"type"),"surface"));
    if(overlay.active&&overlay_action_pending&&completed&&!protected_surface(cJSON_GetObjectItemCaseSensitive(root,"surface"))) {
        overlay.protected=false;overlay_action_pending=false;overlay_memory_decision_pending=false;overlay_resolution_pending=true;
    }
    bsp_display_unlock();
}
bool friday_ui_set_media(const cJSON *root)
{
    if (!bsp_display_lock(1000)) return false;
    const cJSON *request = cJSON_GetObjectItemCaseSensitive(root,"media_request_id");
    const cJSON *session = cJSON_GetObjectItemCaseSensitive(root,"media_session_id");
    const cJSON *sequence = cJSON_GetObjectItemCaseSensitive(root,"media_sequence");
    const cJSON *surface = cJSON_GetObjectItemCaseSensitive(root,"surface");
    bool accepted = (!overlay.active||overlay.ready) && cJSON_IsObject(surface) && strcmp(scene_string(surface,"kind"),"media") == 0 &&
        cJSON_IsString(request) && cJSON_IsString(session) && cJSON_IsNumber(sequence) &&
        friday_media_accept(&media_guard,request->valuestring,session->valuestring,sequence->valuedouble);
    if (accepted) {
        if(overlay.active){friday_media_overlay_clear(&overlay);last_media_line[0]=0;}
        owned_media_render = true;
        accepted = friday_ui_set_scene(surface);
        owned_media_render = false;
        if (accepted && strcmp(scene_string(surface,"subtitle"),last_media_line) != 0) {
            lv_snprintf(last_media_line,sizeof(last_media_line),"%s",scene_string(surface,"subtitle"));
            /* layout_lyric already resets vertical position on a new timed line. */
        }
    }
    bsp_display_unlock();
    return accepted;
}
