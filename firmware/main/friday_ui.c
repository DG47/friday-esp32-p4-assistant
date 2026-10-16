#include "friday_ui.h"

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>

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
#include "friday_weather_art.h"
#include "friday_media_overlay.h"
#include "friday_error_scene.h"

static const char *TAG = "friday_ui";
static friday_experience_guard_t experience_guard;
/* Allowlisted context only: no facts, lyrics, event titles, tokens or JSON. */
static struct {
    char city[2][64], temperature[2][48], condition[2][96], outlook[2][192], detail[2][192];
    uint32_t weather_until;
    bool weather_seen, review_pending, linked_weather;
    int recent_view; /* 1=Calendar checked, 2=Reminders checked, 3=Music viewed */
} ambient_context;
static friday_media_guard_t media_guard;
static bool owned_media_render;
static friday_media_overlay_t overlay;
static bool overlay_action_pending;
static bool overlay_resolution_pending;
static bool overlay_memory_decision_pending;
static char last_media_line[2001];
/* Capture may report a one-shot allocation/read failure while the display is
 * busy. Keep that state without waiting; the UI timer renders it when free. */
static atomic_bool microphone_unavailable;
static atomic_uint interaction_notice_until;
static atomic_bool interaction_notice_memory;
static atomic_bool music_send_notice;
static atomic_bool interaction_notice_weather;
static atomic_bool invalid_input_notice, rejected_input_notice;

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
    lv_obj_t *weather_details[2];
    bool weather_view;
    int weather_code[2],weather_day[2],weather_city[2];
    lv_timer_t *weather_data_expiry;
    unsigned long weather_data_generation;
    char weather_window[128];
    lv_image_dsc_t cover_dsc;
    friday_artwork_t artwork;
    lv_obj_t *cards[4];
    lv_obj_t *card_labels[4];
    lv_obj_t *buttons[5];
    lv_obj_t *button_labels[5];
    lv_obj_t *browse_icons[5];
    lv_obj_t *browse_hints[5];
    lv_obj_t *browse_date;
    lv_obj_t *browse_meta;
    int browse_message_y,browse_message_height;
    int browse_mode; /* 0 standard/protected, 1 launcher, 2 agenda */
    char card_actions[4][64];
    char button_actions[5][64];
    bool voice_notice;

    int recovery_notice_mask;
    bool hide_scene_summary;
    bool weather_completion_pending;
    bool scene_active;
    char scene_kind[24];
    unsigned long scene_generation;
    bool long_pressed;
    bool experience_preview;
    bool experience_building;
    bool experience_protected;
    bool experience_sensitive, experience_memory_page, explicit_ambient_items;
    lv_timer_t *experience_timer, *context_weather_expiry;
    lv_obj_t *weather_layer, *weather_chip[2], *weather_body[2], *weather_tile[2], *weather_support[2], *review_control, *context_back, *context_refresh;
    bool weather_focus, context_reflow;
    int weather_target;
    unsigned context_cards_mask, context_actions_mask;
    bool context_message_visible;
    lv_obj_t *calendar_layer, *calendar_rows[4], *calendar_times[4], *calendar_labels[4], *calendar_back, *status_update_panel;
    lv_timer_t *calendar_lease;
    char calendar_actions[4][64], calendar_title[256];
    bool calendar_retained, calendar_status, calendar_reply, preserve_calendar;
    bool linked_context;
    unsigned linked_view;
    lv_timer_t *linked_event_expiry, *linked_weather_expiry;
    char linked_event_ref[25], linked_weather_ref[25], linked_weather_summary[480];
    bool conversation_reply;
    bool playback_snapshot;
    lv_obj_t *experience_well;
    lv_obj_t *experience_detail;
    char experience_title[256];
    char context_label[80];
    char active_tool[64];
    bool tool_working;
    bool tool_awaiting_result;
    bool activity_message; /* Provenance, never inferred from user text. */
    lv_timer_t *expiry;
    lv_timer_t *countdown;
    int64_t countdown_deadline;
} friday_ui_t;

static friday_ui_t ui;
static friday_ui_activation_callback_t activation_callback;
static friday_ui_action_callback_t action_callback;
static void scene_layout(bool active);
static void retained_weather_clear(void);
static void experience_layout(void);
static void experience_transition_cancel(void);
static void experience_transition_exec(void *object,int32_t value);
static void weather_layer_motion(void *object,int32_t height);
static void ambient_tick(lv_timer_t *timer);
static void context_weather_expired(lv_timer_t *timer);
static void weather_context_layout(bool protected);
static bool protected_surface(const cJSON *surface);
static void scene_expire(lv_timer_t *timer);
static void calendar_context_clear(void);
static void linked_expired(lv_timer_t *timer);
static const char *scene_string(const cJSON *object,const char *name);

/* Shared palette used by ambient, Music, browsing and review surfaces. */
enum { UI_CANVAS=0x111319, UI_PANEL=0x191C23, UI_CARD=0x1D2028,
       UI_CONTROL=0x23272F, UI_EDGE=0x343943, UI_TEXT=0xEEECE7,
       UI_PRIMARY=0xE8E4DC, UI_PRIMARY_TEXT=0x15171D };

static lv_color_t color_hex(uint32_t value)
{
    return lv_color_hex(value);
}

static void position_message(void)
{
    if(ui.browse_mode) {
        lv_obj_set_size(ui.message,624,ui.browse_message_height);
        lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,ui.browse_message_y);return;
    }
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
    if(label==ui.message)ui.activity_message=false;
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
    const int height=ui.experience_preview&&!ui.experience_protected?112:100;
    lv_label_set_long_mode(ui.message, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_style_text_letter_space(ui.message,0,0);
    lv_obj_set_style_text_line_space(ui.message,4,0);
    lv_obj_set_size(ui.message,624,height);
    const lv_font_t *font=&lv_font_montserrat_32;
    lv_point_t size;
    lv_text_get_size(&size,lv_label_get_text(ui.message),font,0,4,624,LV_TEXT_FLAG_NONE);
    if(size.y>height) {
        font=&lv_font_montserrat_24;
        lv_text_get_size(&size,lv_label_get_text(ui.message),font,0,4,624,LV_TEXT_FLAG_NONE);
    }
    lv_obj_set_style_text_font(ui.message,font,0);
    lv_obj_set_scroll_dir(ui.message,LV_DIR_VER);
    lv_obj_set_scrollbar_mode(ui.message,LV_SCROLLBAR_MODE_AUTO);
    if(size.y>height)lv_obj_add_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);
    else lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);
    display_label(ui.lyric_caption,"Swipe for more");
    lv_obj_set_style_text_letter_space(ui.lyric_caption,0,0);
    if(size.y>height)lv_obj_remove_flag(ui.lyric_caption,LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(ui.lyric_caption,LV_OBJ_FLAG_HIDDEN);
    if(reset_position)lv_obj_scroll_to_y(ui.message,0,LV_ANIM_OFF);
}

/* Only generated UI copy is simplified. Arbitrary user/confirmation content
 * is never scanned for words such as provider, source, IDs or metadata. */
static const char *human_message(const char *text)
{
    if(!text)return "";
    const struct {const char *technical;const char *human;} copy[]={
        {"Playback clock stale; lyrics hidden.","Refresh Music to see the latest playback."},
        {"Recording metadata incomplete; lyrics hidden.","Lyrics aren't available for this song."},
        {"Recording mismatch; lyrics hidden.","Lyrics aren't available for this song."},
        {"Invalid lyric timing; text hidden.","Lyrics aren't available for this song."},
        {"Timed lyrics disabled.","Lyrics aren't available right now."},
        {"Timed lyrics unavailable.","Lyrics aren't available right now."},
        {"Lyrics rate limited.","Please try again shortly."},
        {"Lyrics retry is waiting.","Please try again shortly."},
        {"Timed view requires a client update.","Update Friday to use Music."},
        {"Requires separately approved deployment.","Memory isn't available yet."},
        {"Local touch controls work while voice is unavailable.","Touch controls work while voice is unavailable."},
        {"I did not hear a clear request. Tap the orb to retry, or use local controls.","I didn't hear a clear request. Touch the light to retry, or open a view."},
        {"remember: text · update memory #ID: text · forget memory #ID","Say ‘remember’ followed by what you’d like to save. Open Memory to review or forget it."},
        {"I found multiple matches. Say the reminder number you mean.","I found multiple reminders. Say which reminder you mean."},
        {"I can handle the calendar and reminders in mock mode. The broader intellect arrives when you add the API keys.","Ask me about your calendar or reminders."},
    };
    for(unsigned i=0;i<sizeof(copy)/sizeof(copy[0]);i++)if(!strcmp(text,copy[i].technical))return copy[i].human;
    return text;
}
static const char *human_error(const char *text)
{
    if(text&&(strstr(text,"authenticated hello")||strstr(text,"Send hello")||strstr(text,"token")))return "Reconnect Friday to continue.";
    if(text&&(strstr(text,"Action is unavailable")||strstr(text,"refresh the scene")))return "That action is no longer available. Open the view again.";
    if(text&&strstr(text,"Result acknowledgement"))return "Please open that request again.";
    if(text&&strstr(text,"silence"))return "I didn't hear a request. Please try again.";
    return "Friday couldn't complete that request. Please try again.";
}
static void set_message_text(const char *text)
{
    display_label(ui.message, human_message(text));
    position_message();
}

static void set_activity_message(const char *text)
{
    set_message_text(text);ui.activity_message=true;
}

/* The top 28px are reserved for actionable recovery notices on every scene.
 * Keep them separate from response text, protected facts, credits and targets. */
#if CONFIG_FRIDAY_EXECUTION_STOP || defined(FRIDAY_NATIVE_EXECUTION_STOP)
static atomic_int execution_phase;
static atomic_bool execution_online;
static lv_obj_t *execution_stop_button,*execution_status_button,*execution_stop_label;
static void execution_event(lv_event_t *event)
{
    const char *action=lv_event_get_user_data(event);
    if(action_callback)action_callback(action);
}
static void execution_controls_update(void)
{
    if(!execution_stop_button)return;
    int phase=atomic_load(&execution_phase);bool visible=phase>=FR_EXEC_AVAILABLE&&phase<=FR_EXEC_UNCERTAIN;
    lv_obj_t *controls[]={execution_stop_button,execution_status_button};
    for(int i=0;i<2;i++){
        if(visible){lv_obj_remove_flag(controls[i],LV_OBJ_FLAG_HIDDEN);lv_obj_move_foreground(controls[i]);}
        else lv_obj_add_flag(controls[i],LV_OBJ_FLAG_HIDDEN);
        if(!atomic_load(&execution_online)||(i==0&&(phase==FR_EXEC_PENDING||phase==FR_EXEC_UNCERTAIN)))lv_obj_add_state(controls[i],LV_STATE_DISABLED);
        else lv_obj_remove_state(controls[i],LV_STATE_DISABLED);
    }
    lv_label_set_text(execution_stop_label,phase==FR_EXEC_UNCONFIRMED?"Retry Stop":"Stop task");
}
void friday_ui_execution_state(friday_execution_phase_t phase,bool online)
{
    atomic_store(&execution_phase,phase);atomic_store(&execution_online,online);
}
#else
void friday_ui_execution_state(friday_execution_phase_t phase,bool online){(void)phase;(void)online;}
#endif

static void recovery_notice_update(void)
{
    int mask=(atomic_load_explicit(&microphone_unavailable,memory_order_relaxed)?1:0)|(ui.voice_notice?2:0);
    if(atomic_load(&interaction_notice_until) && (int32_t)(atomic_load(&interaction_notice_until)-lv_tick_get())>0)mask|=atomic_load(&interaction_notice_memory)?12:4;
    if((mask&4)&&atomic_load(&invalid_input_notice))mask|=64;
    if((mask&4)&&atomic_load(&rejected_input_notice))mask|=128;
    if((mask&4)&&atomic_load(&music_send_notice))mask|=16;
    if((mask&4)&&atomic_load(&interaction_notice_weather))mask|=32;
    if((mask&4)&&!(mask&48)&&overlay.active&&overlay.protected&&strcmp(media_guard.request,overlay.request)) {
        /* A denied Music request restores the suspended owner's frame guard. */
        lv_snprintf(media_guard.request,sizeof(media_guard.request),"%s",overlay.request);
        lv_snprintf(media_guard.session,sizeof(media_guard.session),"%s",overlay.session);
        media_guard.sequence=overlay.sequence;
    }
#if CONFIG_FRIDAY_EXECUTION_STOP || defined(FRIDAY_NATIVE_EXECUTION_STOP)
    execution_controls_update();
    int phase=atomic_load(&execution_phase);
    if(phase>=FR_EXEC_PENDING)mask|=256+(phase<<10);
#endif
    if(mask==ui.recovery_notice_mask)return;
    ui.recovery_notice_mask=mask;
    if(!mask){lv_obj_add_flag(ui.status,LV_OBJ_FLAG_HIDDEN);return;}
    const char *text=(mask&128)?"Finish or cancel the current request first.":(mask&64)?"I couldn't accept that input. Please try again.":(mask&32)?((mask&16)?"Could not open Weather. Check the connection and retry.":"Finish or cancel this request to open Weather."):(mask&16)?"Could not open Music. Check the connection and retry.":(mask&4)?((mask&3)?"Voice unavailable. Finish or cancel this request first.":
        atomic_load(&interaction_notice_memory)?"Finish or cancel this review to open Music.":"Finish or cancel this request to open Music."):mask==3?"Microphone and voice unavailable. Use touch controls.":
        mask==1?"Microphone unavailable. Use touch controls.":
        "Voice isn't available. You can read the response.";
#if CONFIG_FRIDAY_EXECUTION_STOP || defined(FRIDAY_NATIVE_EXECUTION_STOP)
    if(mask&256)text=friday_execution_notice((friday_execution_phase_t)phase);
#endif
    display_label(ui.status,text);
    lv_obj_set_size(ui.status,624,24);lv_obj_align(ui.status,LV_ALIGN_TOP_LEFT,48,4);
    lv_obj_set_style_text_color(ui.status,color_hex(UI_TEXT),0);
    lv_obj_remove_flag(ui.status,LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(ui.status);
}
static void queue_interaction_notice(bool memory,bool send_failed,bool weather)
{
    atomic_store(&invalid_input_notice,false);atomic_store(&rejected_input_notice,false);
    atomic_store(&interaction_notice_memory,memory);atomic_store(&music_send_notice,send_failed);atomic_store(&interaction_notice_weather,weather);
    atomic_store(&interaction_notice_until,lv_tick_get()+5000);
    if(bsp_display_lock(0)){ui.recovery_notice_mask=-1;recovery_notice_update();bsp_display_unlock();}
}

bool friday_ui_handle_notice(const cJSON *packet)
{
    const cJSON *type=cJSON_GetObjectItemCaseSensitive(packet,"type");
    const cJSON *code=cJSON_GetObjectItemCaseSensitive(packet,"code");
    if(!cJSON_IsString(type)||strcmp(type->valuestring,"error")||
       !cJSON_IsString(code)||strcmp(code->valuestring,"interaction_pending"))return false;
    const cJSON *message=cJSON_GetObjectItemCaseSensitive(packet,"message");
    queue_interaction_notice(cJSON_IsString(message)&&strstr(message->valuestring,"memory review")!=NULL,false,
        cJSON_IsString(message)&&strstr(message->valuestring,"Weather")!=NULL);
    return true;
}

static void recovery_notice_tick(lv_timer_t *timer)
{
    (void)timer;
    if(!bsp_display_lock(0))return;
    recovery_notice_update();bsp_display_unlock();
}

/* Keep activity separate from response/fact text. No user content is rewritten. */
static void context_update(void)
{
    friday_state_t state=friday_state_get();
    const char *activity=state==FRIDAY_STATE_LISTENING?"Listening":
        state==FRIDAY_STATE_SPEAKING?"Speaking":state==FRIDAY_STATE_ALERT?"Attention":
        ui.tool_awaiting_result?"Awaiting result":ui.tool_working?"Working":state==FRIDAY_STATE_THINKING?"Thinking":"";
    char text[112];
    lv_snprintf(text,sizeof(text),"%s%s%s",ui.context_label[0]?ui.context_label:"Friday",
        *activity?" / ":"",activity);
    display_label(ui.eyebrow,text);
    lv_obj_set_width(ui.eyebrow,500);
    lv_label_set_long_mode(ui.eyebrow,LV_LABEL_LONG_MODE_DOTS);
    friday_presence_set_state(ui.presence,(ui.tool_working||ui.tool_awaiting_result)&&state==FRIDAY_STATE_IDLE?FRIDAY_STATE_THINKING:state);
    experience_layout();
}
static void context_label(const char *text)
{
    lv_snprintf(ui.context_label,sizeof(ui.context_label),"%s",text);
    context_update();
}

static void apply_state(friday_state_t state)
{
    friday_state_t previous=friday_state_get();
    friday_state_set(state);
    if(state==FRIDAY_STATE_SPEAKING)ui.voice_notice=false;
    if(!ui.experience_preview&&overlay.active&&ui.scene_active&&!strcmp(ui.scene_kind,"media")&&
       (state==FRIDAY_STATE_LISTENING||state==FRIDAY_STATE_THINKING||state==FRIDAY_STATE_SPEAKING)) {
        for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<5;i++)lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
        scene_layout(false);lv_snprintf(ui.scene_kind,sizeof(ui.scene_kind),"ambient");context_label("FRIDAY");
    }
    context_update();
    const char *states[] = {"Here when you need me", "Listening carefully", "Connecting the dots", "Speaking", "Something needs your attention"};
    const char *headings[] = {"A little more space\nfor your day.", "I'm listening.", "Let me connect\nthe dots.", "Here's what I found.", "A moment of\nyour attention."};
    int index=state>=FRIDAY_STATE_IDLE&&state<=FRIDAY_STATE_ALERT?state:FRIDAY_STATE_IDLE;
    (void)states;
    recovery_notice_update();
    if (!ui.scene_active) {
        display_label(ui.title, headings[index]);
        if (state==FRIDAY_STATE_IDLE&&ui.activity_message) set_message_text("Touch the light to begin.\nHold for local views.");
        else if (state==FRIDAY_STATE_LISTENING&&previous!=state) set_activity_message("Go ahead. I'll keep up.");
        else if (state==FRIDAY_STATE_THINKING&&previous!=state) set_activity_message("A little thought before a useful answer.");
        else if (state==FRIDAY_STATE_ALERT&&previous!=state) set_activity_message("Let's take care of this.");
    }
    experience_layout();
    ESP_LOGI(TAG, "State changed to %s", friday_state_name(state));
}

static void log_ui_snapshot(const char *reason)
{
    lv_obj_update_layout(lv_screen_active());
    int cards = 0, buttons = 0, bounds_errors = 0, routed = 0;
    for (int i = 0; i < 5; i++) {
        lv_obj_t *objects[] = {i<4?ui.cards[i]:NULL, ui.buttons[i]};
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
            set_activity_message("Listening - pause when you are finished");
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
    if(lv_obj_has_state(lv_event_get_target(event),LV_STATE_DISABLED))return;
    const char *action = lv_event_get_user_data(event);
    if (action == NULL || action[0] == '\0') {
        return;
    }
    char category[24];
    size_t length = strcspn(action, ":");
    if (length >= sizeof(category)) length = sizeof(category) - 1;
    memcpy(category, action, length); category[length] = '\0';
    const char *allowed[] = {"context", "status", "weather", "reminders", "calendar", "dismiss", "confirm", "decline", "reminder", "snooze", "complete", "cancel"};
    const char *safe_category = "invalid";
    for (unsigned int i = 0; i < sizeof(allowed)/sizeof(allowed[0]); i++) if (strcmp(category, allowed[i]) == 0) safe_category = allowed[i];
    ESP_LOGI(TAG, "UI_ACTION action=%s", safe_category);
    if(!strcmp(action,"context.weather")) {
        if(ui.experience_protected||ui.experience_sensitive||(overlay.active&&overlay.protected)||!ambient_context.weather_seen)return;
        ui.weather_focus=!ui.weather_focus;
        if(ui.weather_focus)ui.context_message_visible=lv_obj_is_visible(ui.message);
        if(!ui.weather_focus){for(int i=0;i<4;i++)if(ui.context_cards_mask&(1U<<i))lv_obj_remove_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);for(int i=0;i<5;i++)if((ui.context_actions_mask&(1U<<i))&&ui.button_actions[i][0])lv_obj_remove_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);ui.context_cards_mask=ui.context_actions_mask=0;if(ui.context_message_visible)lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);}
        experience_layout();return;
    }
    if (strcmp(action,"dismiss")==0&&overlay.active) {
        if(!overlay.protected&&action_callback)action_callback("media.resume");
        return;
    }
    if (strcmp(action, "dismiss") == 0) {
        if(ui.experience_protected){queue_interaction_notice(true,false,false);return;}
        if(ambient_context.linked_weather)retained_weather_clear();
        scene_expire(NULL);
        log_ui_snapshot("dismiss");
        if (action_callback != NULL) action_callback("dismiss");
    } else if (action_callback == NULL || !action_callback(!strcmp(action,"weather")&&ambient_context.linked_weather?"weather_context":action)) {
        if(!strcmp(action,"music")||!strncmp(action,"musicctl:",9))queue_interaction_notice(false,true,false);
        else if(!strcmp(action,"weather"))queue_interaction_notice(false,true,true);
        else set_message_text("Friday is offline. This action was not sent.");
    }
}

static void scene_layout(bool active)
{
    experience_transition_cancel();
    if(ui.linked_event_expiry){lv_timer_delete(ui.linked_event_expiry);ui.linked_event_expiry=NULL;}
    if(ui.linked_weather_expiry){lv_timer_delete(ui.linked_weather_expiry);ui.linked_weather_expiry=NULL;}
    ui.linked_context=false;ui.linked_event_ref[0]=ui.linked_weather_ref[0]=ui.linked_weather_summary[0]=0;
    ui.conversation_reply=false;
    if(!ui.preserve_calendar)calendar_context_clear();
    ui.context_cards_mask=ui.context_actions_mask=0;
    ui.scene_active=active;
    ui.experience_protected=false;
    ui.tool_working=false;ui.tool_awaiting_result=false;ui.active_tool[0]='\0';
    lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
    ui.browse_mode=0;
    ui.weather_view=false;ui.weather_focus=false;
    if(ui.weather_data_expiry){lv_timer_delete(ui.weather_data_expiry);ui.weather_data_expiry=NULL;}
    for(int i=0;i<2;i++)if(ui.weather_details[i])lv_obj_add_flag(ui.weather_details[i],LV_OBJ_FLAG_HIDDEN);
    ui.hide_scene_summary=false;
    lv_obj_add_flag(ui.browse_date,LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(ui.browse_meta,LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_size(ui.fact_panel,656,104);lv_obj_align(ui.fact_panel,LV_ALIGN_TOP_LEFT,32,138);
    lv_obj_set_style_radius(ui.fact_panel,20,0);
    lv_obj_set_style_bg_color(ui.fact_panel,color_hex(UI_PANEL),0);
    lv_obj_set_style_text_color(ui.title,color_hex(UI_TEXT),0);
    lv_obj_set_style_text_color(ui.message,color_hex(0xC9C6C1),0);
    lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(ui.message,LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_style_text_line_space(ui.message,0,0);
    lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_DOTS);
    lv_obj_t *media[]={ui.cover,ui.artist,ui.media_state,ui.media_time,ui.attribution,ui.lyric_caption,ui.progress};
    for(unsigned i=0;i<sizeof(media)/sizeof(media[0]);i++) if(media[i])lv_obj_add_flag(media[i],LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_text_font(ui.title,active?&lv_font_montserrat_32:&lv_font_montserrat_48,0);
    lv_label_set_long_mode(ui.title,LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_size(ui.title,active?520:624,active?38:132);
    lv_obj_align(ui.title,LV_ALIGN_TOP_LEFT,48,active?84:138);
    lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_24,0);
    lv_obj_set_style_text_letter_space(ui.message,active?2:0,0);
    lv_obj_set_style_text_align(ui.message,active?LV_TEXT_ALIGN_CENTER:LV_TEXT_ALIGN_LEFT,0);
    position_message();
    lv_obj_set_size(ui.presence,active?96:648,active?48:232);
    lv_obj_align(ui.presence,LV_ALIGN_TOP_LEFT,active?576:36,active?28:300);
    friday_presence_set_compact(ui.presence,active);
    friday_presence_set_state(ui.presence,friday_state_get());
    lv_obj_set_style_text_letter_space(ui.eyebrow,active?1:3,0);
    lv_obj_align(ui.eyebrow,LV_ALIGN_TOP_LEFT,48,active?38:40);
    if(active) {
        lv_obj_add_flag(ui.microphone,LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.network,LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(ui.microphone,LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.network,LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
    }
    for(int i=0;i<4;i++) {

        lv_obj_set_style_bg_image_src(ui.cards[i],NULL,0);
        lv_obj_set_style_bg_color(ui.cards[i],color_hex(UI_CARD),0);
        lv_obj_set_style_radius(ui.cards[i],20,0);
        lv_obj_set_style_border_width(ui.cards[i],0,0);
        lv_obj_set_style_anim_duration(ui.card_labels[i],0,0);
        lv_label_set_long_mode(ui.card_labels[i],LV_LABEL_LONG_MODE_DOTS);
        lv_label_set_long_mode(ui.card_times[i],LV_LABEL_LONG_MODE_DOTS);
        lv_obj_set_style_text_font(ui.card_times[i],&lv_font_montserrat_18,0);
        lv_obj_set_style_text_color(ui.card_times[i],color_hex(0x9399A5),0);
        lv_obj_set_size(ui.card_times[i],104,28);
        lv_obj_align(ui.card_times[i],LV_ALIGN_LEFT_MID,20,0);
        lv_obj_set_size(ui.cards[i],624,64);
        lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,48,238+i*76);
        lv_obj_set_style_bg_opa(ui.cards[i],LV_OPA_COVER,0);
        lv_obj_set_size(ui.card_labels[i],464,38);
        lv_obj_align(ui.card_labels[i],LV_ALIGN_LEFT_MID,136,0);
        lv_obj_set_style_text_font(ui.card_labels[i],&lv_font_montserrat_24,0);
        lv_obj_set_style_text_color(ui.card_labels[i],color_hex(UI_TEXT),0);
        lv_obj_set_style_text_align(ui.card_labels[i],LV_TEXT_ALIGN_LEFT,0);
    }
    for(int i=0;i<5;i++) {
        lv_obj_add_flag(ui.browse_icons[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.browse_hints[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_radius(ui.buttons[i],20,0);
        lv_obj_set_style_border_width(ui.buttons[i],1,0);
        lv_obj_set_style_border_color(ui.buttons[i],color_hex(UI_EDGE),0);
        lv_obj_set_style_text_align(ui.button_labels[i],LV_TEXT_ALIGN_CENTER,0);
        lv_obj_center(ui.button_labels[i]);
        lv_obj_set_size(ui.buttons[i],300,58);
        lv_obj_align(ui.buttons[i],LV_ALIGN_TOP_LEFT,48+(i%2)*324,558+(i/2)*70);
        lv_obj_set_style_bg_color(ui.buttons[i],color_hex(UI_CONTROL),0);
        lv_obj_set_style_text_font(ui.button_labels[i],&lv_font_montserrat_24,0);
        lv_obj_set_style_text_color(ui.button_labels[i],color_hex(UI_TEXT),0);
        lv_obj_set_size(ui.button_labels[i],276,32);
    }
}

/* Native line/rounded-rect icons: no raster assets, vector extension or HTTP. */
static void icon_line(lv_layer_t *layer,int x,int y,int x1,int y1,int x2,int y2,uint32_t color)
{
    lv_draw_line_dsc_t d;lv_draw_line_dsc_init(&d);d.color=color_hex(color);
    d.width=2;d.round_start=1;d.round_end=1;
    d.p1.x=x+x1;d.p1.y=y+y1;d.p2.x=x+x2;d.p2.y=y+y2;lv_draw_line(layer,&d);
}
static void icon_box(lv_layer_t *layer,int x,int y,int w,int h,int radius,uint32_t color,bool solid)
{
    lv_draw_rect_dsc_t d;lv_draw_rect_dsc_init(&d);d.radius=radius;
    d.bg_color=color_hex(color);d.bg_opa=solid?LV_OPA_COVER:LV_OPA_TRANSP;
    d.border_color=color_hex(color);d.border_width=solid?0:2;
    lv_area_t area={x,y,x+w-1,y+h-1};lv_draw_rect(layer,&d,&area);
}
static void launcher_icon(lv_event_t *event)
{
    if(ui.browse_mode!=1)return;
    const char *action=lv_event_get_user_data(event);lv_obj_t *object=lv_event_get_target(event);
    lv_area_t area;lv_obj_get_coords(object,&area);int x=area.x1,y=area.y1;
    lv_layer_t *layer=lv_event_get_layer(event);
    uint32_t color=0xB8C6D8;
    if(!strcmp(action,"calendar")) {
        icon_box(layer,x+3,y+8,36,32,7,color,false);
        icon_line(layer,x,y,3,18,38,18,color);icon_line(layer,x,y,12,3,12,12,color);icon_line(layer,x,y,30,3,30,12,color);
        icon_box(layer,x+11,y+25,5,5,2,color,true);icon_box(layer,x+25,y+25,5,5,2,color,true);
    } else if(!strcmp(action,"music")) {
        icon_line(layer,x,y,17,33,17,8,color);icon_line(layer,x,y,17,8,34,4,color);
        icon_line(layer,x,y,34,4,34,29,color);icon_box(layer,x+6,y+30,12,8,4,color,false);icon_box(layer,x+23,y+26,12,8,4,color,false);
    } else if(!strcmp(action,"weather")) {
        icon_box(layer,x+14,y+14,16,16,8,color,false);
        const int rays[][4]={{22,2,22,8},{22,36,22,42},{2,22,8,22},{36,22,42,22},
            {8,8,12,12},{32,32,36,36},{8,36,12,32},{32,12,36,8}};
        for(unsigned i=0;i<8;i++)icon_line(layer,x,y,rays[i][0],rays[i][1],rays[i][2],rays[i][3],color);
    } else if(!strcmp(action,"reminders")) {
        const int points[][2]={{9,29},{11,25},{12,15},{14,11},{18,8},{24,8},{28,11},{30,15},{31,25},{33,29}};
        for(unsigned i=1;i<sizeof(points)/sizeof(points[0]);i++)icon_line(layer,x,y,points[i-1][0],points[i-1][1],points[i][0],points[i][1],color);
        icon_line(layer,x,y,7,31,35,31,color);icon_line(layer,x,y,18,37,24,37,color);
    } else {
        icon_box(layer,x+10,y+4,29,30,6,color,false);icon_box(layer,x+3,y+11,29,30,6,color,false);
        icon_line(layer,x,y,11,23,24,23,color);icon_line(layer,x,y,11,30,20,30,color);
    }
}
/* Readable overlay on immutable current-condition city illustrations. */
static void weather_city_art(lv_event_t *event)
{
    if(!ui.weather_view&&lv_event_get_target(event)!=ui.weather_tile[0]&&lv_event_get_target(event)!=ui.weather_tile[1])return;
    lv_area_t area;lv_obj_get_coords(lv_event_get_target(event),&area);
    lv_draw_rect_dsc_t d;lv_draw_rect_dsc_init(&d);
    d.radius=16;d.bg_color=color_hex(0x0C1420);d.bg_opa=LV_OPA_COVER;
    d.bg_grad.dir=LV_GRAD_DIR_HOR;d.bg_grad.stops_count=2;
    d.bg_grad.stops[0].color=color_hex(0x0C1420);d.bg_grad.stops[0].opa=210;d.bg_grad.stops[0].frac=0;
    d.bg_grad.stops[1].color=color_hex(0x0C1420);d.bg_grad.stops[1].opa=ui.experience_preview?170:18;d.bg_grad.stops[1].frac=255;
    lv_draw_rect(lv_event_get_layer(event),&d,&area);
}

static void weather_data_expired(lv_timer_t *timer)
{
    if(ui.weather_data_expiry!=timer)return;
    ui.weather_data_expiry=NULL; /* one-shot timer auto-deletes after callback */
    if(!ui.weather_view||ui.scene_generation!=ui.weather_data_generation||
       (overlay.active&&overlay.protected))return;
    ambient_context.weather_until=lv_tick_get();memset(ambient_context.temperature,0,sizeof(ambient_context.temperature));memset(ambient_context.condition,0,sizeof(ambient_context.condition));memset(ambient_context.outlook,0,sizeof(ambient_context.outlook));memset(ambient_context.detail,0,sizeof(ambient_context.detail));weather_context_layout(false);
    display_label(ui.title,"Conditions expired.");
    char message[160];lv_snprintf(message,sizeof(message),"Last forecast window\n%s",ui.weather_window);
    display_label(ui.message,message);
    for(int location=0;location<2;location++) {
        int i=location*2;ui.weather_code[location]=-1;ui.weather_day[location]=-1;
        lv_obj_set_style_bg_image_src(ui.cards[i],friday_weather_art(ui.weather_city[location],-1,-1),0);
        display_label(ui.card_labels[i],"—");
        display_label(ui.card_times[i+1],"Now · Conditions expired");
        display_label(ui.card_labels[i+1],"Forecast expired");
        display_label(ui.weather_details[location],"Reopen Weather for an updated forecast.");
    }
    log_ui_snapshot("weather-data-expired");
}

static void agenda_empty_mark(lv_event_t *event)
{
    if(ui.browse_mode!=2)return;
    lv_area_t area;lv_obj_get_coords(lv_event_get_target(event),&area);
    lv_layer_t *layer=lv_event_get_layer(event);int x=area.x1+34,y=area.y1+34;
    uint32_t color=0x98ACBE;
    icon_box(layer,x+3,y+8,36,32,7,color,false);
    icon_line(layer,x,y,3,18,38,18,color);
    icon_line(layer,x,y,12,3,12,12,color);icon_line(layer,x,y,30,3,30,12,color);
    icon_box(layer,x+11,y+25,5,5,2,color,true);icon_box(layer,x+25,y+25,5,5,2,color,true);
}
static void agenda_mark(lv_event_t *event)
{
    if(ui.browse_mode!=2)return;
    lv_obj_t *object=lv_event_get_target(event);lv_area_t area;lv_obj_get_coords(object,&area);
    lv_layer_t *layer=lv_event_get_layer(event);
    (void)layer;(void)area; /* Alignment and spacing carry the agenda; no ornamental separators. */
}

/* One browsing component system: launcher, editorial agenda and empty states.
 * Standard/protected review geometry and action ownership are untouched. */
static void browse_layout(const cJSON *surface)
{
    bool launcher=!strcmp(ui.scene_kind,"metric")&&
        !strcmp(ui.button_actions[0],"memories")&&!strcmp(ui.button_actions[3],"music");
    ui.browse_mode=launcher?1:2;
    context_label(launcher?"Friday":"Calendar");
    lv_obj_set_style_text_letter_space(ui.eyebrow,0,0);
    lv_obj_set_style_text_font(ui.title,&lv_font_montserrat_48,0);
    lv_obj_set_size(ui.title,624,60);lv_obj_align(ui.title,LV_ALIGN_TOP_LEFT,48,88);
    lv_label_set_long_mode(ui.title,LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);
    lv_obj_set_style_text_letter_space(ui.message,0,0);
    lv_obj_set_style_text_align(ui.message,LV_TEXT_ALIGN_LEFT,0);
    lv_obj_add_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
    if(launcher) {
        display_label(ui.title,"Your day");
        lv_obj_add_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
        ui.hide_scene_summary=true;
        ui.browse_message_y=158;ui.browse_message_height=48;position_message();
        const char *hints[]={"Saved with care","Small things, remembered","Your day at a glance","Pick up the rhythm","Current conditions"};
        bool five=!strcmp(ui.button_actions[4],"weather");
        for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<(five?5:4);i++) {
            lv_obj_set_size(ui.buttons[i],i==4?624:300,i==4?104:five?144:172);
            lv_obj_align(ui.buttons[i],LV_ALIGN_TOP_LEFT,i==4?48:48+(i%2)*324,i==4?524:(five?188:236)+(i/2)*(five?168:196));
            lv_obj_set_style_radius(ui.buttons[i],20,0);lv_obj_set_style_border_width(ui.buttons[i],0,0);
            lv_obj_set_style_bg_color(ui.buttons[i],color_hex(UI_CARD),0);
            lv_obj_set_size(ui.button_labels[i],i==4?500:252,42);lv_obj_align(ui.button_labels[i],LV_ALIGN_TOP_LEFT,i==4?88:24,i==4?34:five?88:112);
            lv_obj_align(ui.browse_icons[i],LV_ALIGN_TOP_LEFT,24,i==4?30:24);
            lv_obj_set_style_text_align(ui.button_labels[i],LV_TEXT_ALIGN_LEFT,0);
            lv_obj_set_style_text_font(ui.button_labels[i],&lv_font_montserrat_32,0);
            lv_obj_set_style_text_color(ui.button_labels[i],color_hex(UI_TEXT),0);
            lv_obj_set_style_text_color(ui.browse_hints[i],color_hex(i==2?0x5E666B:0xABB5BE),0);
            display_label(ui.browse_hints[i],hints[i]);
            lv_obj_remove_flag(ui.browse_icons[i],LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.browse_hints[i],LV_OBJ_FLAG_HIDDEN);
        }
        lv_obj_add_flag(ui.browse_meta,LV_OBJ_FLAG_HIDDEN);
        return;
    }
    ui.hide_scene_summary=true;
    const char *subtitle=scene_string(surface,"subtitle");const char *split=strchr(subtitle,'\n');
    if(split) {
        char date[128];size_t length=(size_t)(split-subtitle);if(length>=sizeof(date))length=sizeof(date)-1;
        memcpy(date,subtitle,length);date[length]=0;display_label(ui.browse_date,date);
        lv_obj_remove_flag(ui.browse_date,LV_OBJ_FLAG_HIDDEN);
        display_label(ui.message,split+1);
    }
    bool detail=!strcmp(scene_string(surface,"kind"),"artifact");
    int rows=0,buttons=0;
    for(int i=0;i<4;i++)if(!lv_obj_has_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN))rows++;
    for(int i=0;i<5;i++)if(!lv_obj_has_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN))buttons++;
    bool stale=strstr(subtitle,"Cached calendar")||strstr(subtitle,"Cached event");
    const char *heading=scene_string(surface,"title");
    if(!strcmp(heading,"Calendar access revoked")||!strcmp(heading,"Calendar not connected"))display_label(ui.message,"Connect Calendar on the Mac to continue.");
    else if(!strcmp(heading,"Calendar unavailable"))display_label(ui.message,"Refresh Calendar and try again.");
    if(stale)display_label(ui.message,"Refresh to see the latest events.");
    else if(rows) {
        ui.hide_scene_summary=true;lv_obj_add_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
    } else if(strstr(subtitle,"No events today"))display_label(ui.message,"No events today.");
    else if(strstr(subtitle,"No events retrieved"))display_label(ui.message,"No events found. Refresh to try again.");
    lv_obj_set_style_text_font(ui.message,rows?&lv_font_montserrat_18:&lv_font_montserrat_24,0);
    lv_obj_set_style_text_color(ui.message,color_hex(0x9EA8B5),0);
    ui.browse_message_height=rows?52:144;
    ui.browse_message_y=rows?(split?190:158):386;position_message();
    lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);
    if(!rows) {
        lv_obj_set_style_text_align(ui.message,LV_TEXT_ALIGN_CENTER,0);
        lv_obj_set_size(ui.fact_panel,112,112);lv_obj_align(ui.fact_panel,LV_ALIGN_TOP_LEFT,304,250);
        lv_obj_set_style_radius(ui.fact_panel,36,0);lv_obj_set_style_bg_color(ui.fact_panel,color_hex(0x1B242C),0);
        lv_obj_remove_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
    }
    for(int i=0;i<4;i++) {
        lv_obj_set_size(ui.cards[i],624,78);lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,48,254+i*84);
        lv_obj_set_style_radius(ui.cards[i],24,0);lv_obj_set_style_border_width(ui.cards[i],0,0);
        lv_obj_set_style_bg_color(ui.cards[i],color_hex(0x1B242C),0);
        lv_obj_set_style_bg_opa(ui.cards[i],LV_OPA_TRANSP,0);
        lv_obj_set_style_text_font(ui.card_times[i],&lv_font_montserrat_18,0);
        lv_obj_set_style_text_color(ui.card_times[i],color_hex(0x98A4B2),0);
        lv_obj_set_size(ui.card_times[i],88,28);lv_obj_align(ui.card_times[i],LV_ALIGN_LEFT_MID,14,0);
        lv_label_set_long_mode(ui.card_times[i],LV_LABEL_LONG_MODE_WRAP);
        lv_obj_set_style_text_font(ui.card_labels[i],detail?&lv_font_montserrat_24:&lv_font_montserrat_32,0);
        lv_obj_set_size(ui.card_labels[i],474,detail?38:48);
        lv_obj_set_style_anim_duration(ui.card_labels[i],9000,0);lv_obj_align(ui.card_labels[i],LV_ALIGN_LEFT_MID,134,0);
        lv_label_set_long_mode(ui.card_labels[i],LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);
    }
    int visible=0,width=buttons?(624-(buttons-1)*16)/buttons:624;
    for(int i=0;i<5;i++) {
        if(lv_obj_has_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN))continue;
        lv_obj_set_size(ui.buttons[i],width,64);lv_obj_align(ui.buttons[i],LV_ALIGN_TOP_LEFT,48+visible*(width+16),626);visible++;
        lv_obj_set_style_radius(ui.buttons[i],20,0);lv_obj_set_style_border_width(ui.buttons[i],0,0);
        bool primary=visible==1&&strcmp(ui.button_actions[i],"dismiss");
        lv_obj_set_style_bg_color(ui.buttons[i],color_hex(primary?UI_PRIMARY:UI_CONTROL),0);
        lv_obj_set_style_text_color(ui.button_labels[i],color_hex(primary?UI_PRIMARY_TEXT:UI_TEXT),0);
        lv_obj_set_size(ui.button_labels[i],width-16,32);lv_obj_center(ui.button_labels[i]);
    }
}

static void scene_expire(lv_timer_t *timer)
{
    if(overlay.active){
        if(!overlay.protected&&action_callback)action_callback("media.resume");
        return;
    }
    experience_transition_cancel();friday_experience_cancel(&experience_guard);ui.weather_focus=false;
    friday_ui_media_clear();
    (void)timer;
    if (ui.countdown != NULL) {
        lv_timer_delete(ui.countdown);
        ui.countdown = NULL;
    }
    for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
    for(int i=0;i<5;i++)lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
    scene_layout(false);
    lv_snprintf(ui.scene_kind, sizeof(ui.scene_kind), "%s", "ambient");
    ui.scene_generation++;
    ui.tool_working=false;ui.tool_awaiting_result=false;ui.active_tool[0]='\0';
    context_label( "Friday");
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

/* Weather uses the same open editorial hierarchy as the agenda. Each pair of
 * rows is one location, with a primary reading and quieter supporting facts. */
static void weather_layout(const cJSON *surface)
{
    context_label("Weather");
    lv_obj_set_style_text_letter_space(ui.eyebrow,0,0);
    const char *subtitle=scene_string(surface,"subtitle"), *split=strchr(subtitle,'\n');
    char summary[160];size_t length=split?(size_t)(split-subtitle):strlen(subtitle);
    if(length>=sizeof(summary))length=sizeof(summary)-1;
    memcpy(summary,subtitle,length);summary[length]=0;
    display_label(ui.title,summary[0]?summary:"Weather");
    lv_obj_set_style_text_font(ui.title,&lv_font_montserrat_48,0);
    lv_obj_set_size(ui.title,624,60);lv_obj_align(ui.title,LV_ALIGN_TOP_LEFT,48,88);
    ui.hide_scene_summary=true;
    display_label(ui.message,split?split+1:"");
    const char *window=split?strchr(split+1,'\n'):NULL;
    lv_snprintf(ui.weather_window,sizeof(ui.weather_window),"%s",window?window+1:"");
    lv_obj_set_style_text_letter_space(ui.message,0,0);
    lv_obj_set_style_text_align(ui.message,LV_TEXT_ALIGN_LEFT,0);
    lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_size(ui.message,624,64);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,158);
    lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
    ui.weather_view=true;
    const cJSON *items=cJSON_GetObjectItemCaseSensitive(surface,"items");
    bool measurements=false;
    for(int location=0;location<2;location++) {
        int i=location*2, y=226+location*200;
        const cJSON *primary=cJSON_GetArrayItem(items,i),*detail=cJSON_GetArrayItem(items,i+1);
        const cJSON *code=cJSON_GetObjectItemCaseSensitive(primary,"weather_code"),*day=cJSON_GetObjectItemCaseSensitive(primary,"is_day");
        ui.weather_code[location]=cJSON_IsNumber(code)&&code->valuedouble==code->valueint?code->valueint:-1;
        ui.weather_day[location]=cJSON_IsNumber(day)&&day->valuedouble==day->valueint?day->valueint:-1;
        const char *city=scene_string(primary,"time");
        ui.weather_city[location]=!strcmp(city,"Jersey City, NJ")?1:!strcmp(city,"New York, NY")?2:0;
        lv_obj_set_size(ui.cards[i],680,190);lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,20,y);
        lv_obj_set_style_radius(ui.cards[i],16,0);
        lv_obj_set_style_bg_image_src(ui.cards[i],friday_weather_art(ui.weather_city[location],ui.weather_code[location],ui.weather_day[location]),0);
        lv_obj_set_style_bg_opa(ui.cards[i],LV_OPA_COVER,0);
        lv_obj_set_size(ui.card_times[i],240,38);lv_obj_align(ui.card_times[i],LV_ALIGN_TOP_LEFT,28,16);
        lv_obj_set_style_text_font(ui.card_times[i],&lv_font_montserrat_24,0);
        lv_obj_set_style_text_color(ui.card_times[i],color_hex(UI_TEXT),0);
        lv_obj_set_size(ui.card_labels[i],220,42);lv_obj_align(ui.card_labels[i],LV_ALIGN_TOP_LEFT,268,12);
        lv_obj_set_style_text_font(ui.card_labels[i],&lv_font_montserrat_32,0);
        lv_obj_set_style_text_align(ui.card_labels[i],LV_TEXT_ALIGN_LEFT,0);
        bool unavailable=!strcmp(scene_string(primary,"label"),"Weather unavailable");
        if(cJSON_IsObject(primary)&&scene_string(primary,"label")[0]&&!unavailable)measurements=true;
        if(unavailable)display_label(ui.card_labels[i],"—");
        lv_obj_set_size(ui.cards[i+1],624,96);lv_obj_align(ui.cards[i+1],LV_ALIGN_TOP_LEFT,48,y+58);
        lv_obj_set_style_bg_opa(ui.cards[i+1],LV_OPA_TRANSP,0);
        lv_obj_set_size(ui.card_times[i+1],624,42);lv_obj_align(ui.card_times[i+1],LV_ALIGN_TOP_LEFT,0,0);
        lv_obj_set_style_text_font(ui.card_times[i+1],&lv_font_montserrat_32,0);
        lv_obj_set_style_text_color(ui.card_times[i+1],color_hex(UI_TEXT),0);
        char current[96];const char *condition=scene_string(primary,"condition");
        lv_snprintf(current,sizeof(current),"Now · %s",condition[0]?condition:"Conditions unknown");
        display_label(ui.card_times[i+1],unavailable?"Weather unavailable":current);
        const char *chance=scene_string(detail,"label"),*prefix="Rain / snow chance ";
        char forecast[96];
        if(!strncmp(chance,prefix,strlen(prefix)))lv_snprintf(forecast,sizeof(forecast),"Rain / snow: %s",chance+strlen(prefix));
        else lv_snprintf(forecast,sizeof(forecast),"%s",chance);
        display_label(ui.card_labels[i+1],forecast);
        lv_obj_set_size(ui.card_labels[i+1],624,34);lv_obj_align(ui.card_labels[i+1],LV_ALIGN_TOP_LEFT,0,45);
        lv_obj_set_style_text_font(ui.card_labels[i+1],&lv_font_montserrat_24,0);
        lv_obj_set_style_text_color(ui.card_labels[i+1],color_hex(UI_TEXT),0);
        lv_obj_set_style_text_align(ui.card_labels[i+1],LV_TEXT_ALIGN_LEFT,0);
        display_label(ui.weather_details[location],scene_string(detail,"detail"));
        lv_obj_set_size(ui.weather_details[location],624,28);lv_obj_align(ui.weather_details[location],LV_ALIGN_TOP_LEFT,48,y+146);
        lv_obj_set_style_text_color(ui.weather_details[location],color_hex(0xCCD5DF),0);
        if(cJSON_IsObject(primary))lv_obj_remove_flag(ui.weather_details[location],LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_set_size(ui.buttons[0],300,56);lv_obj_align(ui.buttons[0],LV_ALIGN_TOP_LEFT,48,626);
    lv_obj_set_style_border_width(ui.buttons[0],0,0);
    display_label(ui.attribution,"Weather: Open-Meteo");
    lv_obj_align(ui.attribution,LV_ALIGN_TOP_LEFT,48,694);
    lv_obj_remove_flag(ui.attribution,LV_OBJ_FLAG_HIDDEN);
    if(measurements) {
        const cJSON *valid=cJSON_GetObjectItemCaseSensitive(surface,"weather_valid_ms");
        /* Missing/malformed freshness must never leave a current reading
         * unbounded, including a disconnected display or an older backend. */
        uint32_t ms=cJSON_IsNumber(valid)&&valid->valuedouble==valid->valueint&&
            valid->valueint>=1&&valid->valueint<=300000?(uint32_t)valid->valueint:1;
        ui.weather_data_generation=ui.scene_generation;
        ui.weather_data_expiry=lv_timer_create(weather_data_expired,ms,NULL);
        if(ui.weather_data_expiry)lv_timer_set_repeat_count(ui.weather_data_expiry,1);
        else weather_data_expired(NULL); /* allocation failure clears data safely */
    }
}

static void calendar_context_clear(void)
{
    if(ui.calendar_lease){lv_timer_delete(ui.calendar_lease);ui.calendar_lease=NULL;}
    ui.calendar_retained=ui.calendar_status=ui.calendar_reply=false;ui.calendar_title[0]='\0';
    ui.conversation_reply=false;
    if(ui.calendar_layer)lv_obj_add_flag(ui.calendar_layer,LV_OBJ_FLAG_HIDDEN);
    if(ui.calendar_back)lv_obj_add_flag(ui.calendar_back,LV_OBJ_FLAG_HIDDEN);
    if(ui.status_update_panel){if(lv_obj_get_parent(ui.message)==ui.status_update_panel)lv_obj_set_parent(ui.message,lv_screen_active());lv_obj_add_flag(ui.status_update_panel,LV_OBJ_FLAG_HIDDEN);}
    for(int i=0;i<4;i++) {
        ui.calendar_actions[i][0]='\0';
        if(ui.calendar_labels[i])display_label(ui.calendar_labels[i],"");
        if(ui.calendar_times[i])display_label(ui.calendar_times[i],"");
    }
}
static void calendar_context_expired(lv_timer_t *timer)
{
    (void)timer;ui.calendar_lease=NULL;calendar_context_clear();experience_layout();
}
static void calendar_context_capture(const cJSON *surface)
{
    if(!ui.calendar_layer)return;
    const cJSON *lease=cJSON_GetObjectItemCaseSensitive(surface,"expires_ms");
    if(!cJSON_IsNumber(lease)||lease->valuedouble<1||lease->valuedouble>300000){calendar_context_clear();return;}
#ifdef FRIDAY_NATIVE_SENDER
    extern bool friday_native_fail_calendar_timer(void);
    if(!friday_native_fail_calendar_timer())
#endif
    ui.calendar_lease=lv_timer_create(calendar_context_expired,(uint32_t)lease->valueint,NULL);
    if(!ui.calendar_lease){calendar_context_clear();return;}
    lv_timer_set_repeat_count(ui.calendar_lease,1);
    ui.calendar_retained=true;
    snprintf(ui.calendar_title,sizeof(ui.calendar_title),"%s",ui.experience_title);
    for(int i=0;i<4;i++) {
        display_label(ui.calendar_times[i],lv_label_get_text(ui.card_times[i]));
        display_label(ui.calendar_labels[i],lv_label_get_text(ui.card_labels[i]));
        snprintf(ui.calendar_actions[i],sizeof(ui.calendar_actions[i]),"%s",ui.card_actions[i]);
        if(lv_obj_is_visible(ui.cards[i]))lv_obj_remove_flag(ui.calendar_rows[i],LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(ui.calendar_rows[i],LV_OBJ_FLAG_HIDDEN);
        if(ui.card_actions[i][0])lv_obj_add_flag(ui.calendar_rows[i],LV_OBJ_FLAG_CLICKABLE);else lv_obj_remove_flag(ui.calendar_rows[i],LV_OBJ_FLAG_CLICKABLE);
    }
}
static bool is_status_update(const cJSON *surface)
{
    if(strcmp(scene_string(surface,"kind"),"metric")||!strcmp(scene_string(surface,"title"),"Weather"))return false;
    if(!strcmp(scene_string(surface,"subtitle"),"Local backend status"))return true;
    const cJSON *items=cJSON_GetObjectItemCaseSensitive(surface,"items");
    for(int i=0;i<4;i++){const cJSON *item=cJSON_GetArrayItem(items,i);const char *name=scene_string(item,"time");if(!strcmp(name,"Network")||!strcmp(name,"Microphone"))return true;}
    return false;
}
/* Independent fact leases never dismiss the composed Weather object. */
static void linked_erase(bool event)
{
    if(!ui.linked_context)return;
    if(event) {
        lv_snprintf(ui.experience_title,sizeof(ui.experience_title),"Your plan");display_label(ui.title,"Your plan");
        if(ui.linked_view==1) {
            for(int i=0;i<4;i++){display_label(ui.card_times[i],"");display_label(ui.card_labels[i],"");ui.card_actions[i][0]=0;}
        }
        display_label(ui.card_labels[0],"Event details expired; read Calendar again.");
        display_label(ui.card_times[1],"");display_label(ui.card_labels[1],"");
        ui.card_actions[0][0]=ui.card_actions[1][0]=0;
        lv_obj_remove_flag(ui.cards[0],LV_OBJ_FLAG_CLICKABLE);lv_obj_remove_flag(ui.cards[1],LV_OBJ_FLAG_CLICKABLE);
        ui.linked_event_ref[0]=0;
        set_message_text("Weather context stays until dismissed. Event details expired.");
        for(int i=0;i<5;i++)if(!strcmp(lv_label_get_text(ui.button_labels[i]),"Event"))lv_obj_add_state(ui.buttons[i],LV_STATE_DISABLED);
    } else {
        lv_snprintf(ui.linked_weather_summary,sizeof(ui.linked_weather_summary),"Reading expired; refresh Weather.");
        if(ui.linked_view==1)display_label(ui.experience_detail,ui.linked_weather_summary);
        else {display_label(ui.card_labels[2],"Reading expired; refresh Weather.");display_label(ui.card_labels[3],"Forecast unavailable until refreshed.");}
    }
}

static void linked_expired(lv_timer_t *timer)
{
    if(timer==ui.linked_event_expiry){ui.linked_event_expiry=NULL;linked_erase(true);}
    else if(timer==ui.linked_weather_expiry){ui.linked_weather_expiry=NULL;linked_erase(false);}
}

bool friday_ui_set_scene(const cJSON *surface)
{
    if(!bsp_display_lock(1000))return false;
    if(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(surface,"clear_weather_context"))&&!protected_surface(surface))retained_weather_clear();
    /* Status is an update to the current Calendar context, not a new page.
     * Retained event objects are volatile and keep their original lease. */
    ui.preserve_calendar=ui.experience_preview&&ui.calendar_retained&&!protected_surface(surface)&&is_status_update(surface);
    bool calendar_update=ui.preserve_calendar;
    bool continuation=owned_media_render&&ui.scene_active&&!strcmp(ui.scene_kind,"media");
    bool retained_focus=ui.weather_focus;
    ui.experience_building=true;
    atomic_store(&interaction_notice_until,0);ui.recovery_notice_mask=-1;
    if (!friday_ui_set_surface(scene_string(surface, "kind"), scene_string(surface, "title"),
                               scene_string(surface, "subtitle"))) {ui.preserve_calendar=false;ui.experience_building=false;bsp_display_unlock();return false;}
    ui.preserve_calendar=false;
    if(continuation)ui.weather_focus=retained_focus;
    const cJSON *items = cJSON_GetObjectItemCaseSensitive(surface, "items");
    const cJSON *actions = cJSON_GetObjectItemCaseSensitive(surface, "actions");
    for (int i = 0; i < 4; i++) {
        const cJSON *item = cJSON_GetArrayItem(items, i);
        ui.card_actions[i][0] = '\0';
        if (cJSON_IsObject(item)) {
            char time[64], label[240], text[308];
            friday_display_text(time, sizeof(time), scene_string(item, "time"), supports_glyph, (void *)&lv_font_montserrat_24);
            friday_display_text(label, sizeof(label), scene_string(item, "label"), supports_glyph, (void *)&lv_font_montserrat_24);
            const char *content=!strcmp(time,"Series")&&!strcmp(label,"Recurring event instance")?"Recurring event":label;
            const char *item_action=scene_string(item,"action");
            if((!strncmp(item_action,"reminder:",9)||(!strcmp(scene_string(surface,"kind"),"reminder")&&!strcmp(scene_string(surface,"title"),"Reminders")))&&label[0]=='#') {
                const char *after=label+1;while(*after>='0'&&*after<='9')after++;
                if(*after==' ')content=after+1;
            }
            lv_snprintf(text, sizeof(text), "%s", content);
            if(!strncmp(item_action,"memory:",7)) {
                display_label(ui.card_times[i],"");
                lv_obj_set_size(ui.card_labels[i],584,38);lv_obj_align(ui.card_labels[i],LV_ALIGN_LEFT_MID,20,0);
            } else display_label(ui.card_times[i], time);
            lv_obj_set_style_text_font(ui.card_labels[i], &lv_font_montserrat_24, 0);
            display_label(ui.card_labels[i], text);
            lv_snprintf(ui.card_actions[i], sizeof(ui.card_actions[i]), "%s", scene_string(item, "action"));
            if (ui.card_actions[i][0]) lv_obj_add_flag(ui.cards[i], LV_OBJ_FLAG_CLICKABLE);
            else lv_obj_remove_flag(ui.cards[i], LV_OBJ_FLAG_CLICKABLE);
            lv_obj_remove_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            display_label(ui.card_times[i],"");display_label(ui.card_labels[i],"");
            lv_obj_add_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
    for(int i=0;i<5;i++) {
        const cJSON *action=cJSON_GetArrayItem(actions,i);
        ui.button_actions[i][0]='\0';
        if (cJSON_IsObject(action)) {
            const char *button_text = scene_string(action, "label");
            if (strcmp(scene_string(surface, "kind"), "media") == 0) {
                if (strcmp(button_text, "Previous") == 0) button_text = LV_SYMBOL_PREV "  Previous";
                else if (strcmp(button_text, "Next") == 0) button_text = LV_SYMBOL_NEXT "  Next";
                else if (strcmp(button_text, "Pause") == 0) button_text = LV_SYMBOL_PAUSE "  Pause";
                else if (strcmp(button_text, "Resume") == 0) button_text = LV_SYMBOL_PLAY "  Resume";
            }
            if(!strcmp(button_text,"Local status")&&!strcmp(scene_string(action,"id"),"status"))button_text="Open views";
            display_label(ui.button_labels[i], button_text);
            lv_snprintf(ui.button_actions[i], sizeof(ui.button_actions[i]), "%s", scene_string(action, "id"));
            if (ui.button_actions[i][0]&&!(overlay.active&&overlay.protected&&!strcmp(ui.button_actions[i],"dismiss"))) lv_obj_remove_state(ui.buttons[i], LV_STATE_DISABLED);
            else lv_obj_add_state(ui.buttons[i], LV_STATE_DISABLED);
            lv_obj_remove_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
    ui.playback_snapshot=false;
    if (strcmp(scene_string(surface,"kind"),"media")==0) {
        lv_obj_add_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_t *media[]={ui.cover,ui.artist,ui.media_state,ui.media_time,ui.attribution,ui.lyric_caption};
        for(unsigned i=0;i<sizeof(media)/sizeof(media[0]);i++)lv_obj_remove_flag(media[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(ui.title,320,112);lv_obj_align(ui.title,LV_ALIGN_TOP_LEFT,352,96);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_32,0);
        lv_obj_set_style_text_letter_space(ui.message,0,0);
        lv_obj_set_style_text_align(ui.message,LV_TEXT_ALIGN_LEFT,0);
        if(!owned_media_render||strcmp(scene_string(surface,"subtitle"),last_media_line))display_label(ui.message,human_message(scene_string(surface,"subtitle")));
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
        display_label(ui.artist,artist);(void)state;lv_obj_add_flag(ui.media_state,LV_OBJ_FLAG_HIDDEN);
        display_label(ui.media_time,stamp);
        /* Preserve attribution, not transport/record identifiers. */
        if(strstr(source,"snapshot")){ui.playback_snapshot=true;display_label(ui.attribution,"Spotify / playback snapshot");lv_obj_add_flag(ui.cover_image,LV_OBJ_FLAG_HIDDEN);}
        else if(strstr(source,"LRCLIB"))display_label(ui.attribution,"Spotify / Lyrics: LRCLIB / rights unverified");
        else if(strstr(source,"synthetic")||strstr(source,"Synthetic"))lv_obj_add_flag(ui.attribution,LV_OBJ_FLAG_HIDDEN);
        else if(!source[0]||strstr(source,"Spotify"))display_label(ui.attribution,"Spotify");
        else {char credit[320];lv_snprintf(credit,sizeof(credit),"Spotify / %s",source);display_label(ui.attribution,credit);}
        lv_obj_align(ui.attribution,LV_ALIGN_TOP_LEFT,48,694);
        lv_obj_add_flag(ui.progress,LV_OBJ_FLAG_HIDDEN);
        int em,es,dm,ds;
        if(sscanf(stamp,"%4d:%2d / %4d:%2d",&em,&es,&dm,&ds)==4&&em>=0&&em<=1440&&es>=0&&es<60&&dm>=0&&dm<=1440&&ds>=0&&ds<60&&(dm*60+ds)>0) {
            lv_bar_set_value(ui.progress,(int)(((int64_t)em*60+es)*100/(dm*60+ds)),LV_ANIM_OFF);
            lv_obj_remove_flag(ui.progress,LV_OBJ_FLAG_HIDDEN);
        }
        for(int i=0;i<4;i++) {
            lv_obj_set_style_bg_color(ui.buttons[i],color_hex(UI_CONTROL),0);lv_obj_set_style_text_color(ui.button_labels[i],color_hex(UI_TEXT),0);
            const char *label=scene_string(cJSON_GetArrayItem(actions,i),"label");
            int left=48+i*156,width=144;
            if(!strcmp(label,"Previous")) {left=48;width=120;display_label(ui.button_labels[i],LV_SYMBOL_PREV);}
            else if(!strcmp(label,"Pause")||!strcmp(label,"Resume")||!strcmp(label,"Play")) {
                left=180;width=208;display_label(ui.button_labels[i],!strcmp(label,"Pause")?LV_SYMBOL_PAUSE:LV_SYMBOL_PLAY);
                lv_obj_set_style_bg_color(ui.buttons[i],color_hex(UI_PRIMARY),0);lv_obj_set_style_text_color(ui.button_labels[i],color_hex(UI_PRIMARY_TEXT),0);
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
        display_label(ui.card_times[0],"Remaining");
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
        && strcmp(scene_string(surface, "kind"), "media") != 0
        && !(strcmp(scene_string(surface,"kind"),"metric")==0 &&
             strcmp(scene_string(surface,"title"),"Weather")==0)) {
        ui.expiry = lv_timer_create(scene_expire, (uint32_t)expires->valueint, NULL);
        lv_timer_set_repeat_count(ui.expiry, 1);
        lv_timer_set_auto_delete(ui.expiry, false);
    }
    bool memory_detail=!strncmp(scene_string(surface,"title"),"Memory #",8);
    bool memory_list=!strcmp(scene_string(surface,"title"),"What Friday remembers");
    bool reminders_list=!strcmp(scene_string(surface,"title"),"Your reminders");
    if(memory_detail)for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
    if(memory_list||(reminders_list&&cJSON_GetArraySize(items)>0)) {
        ui.hide_scene_summary=true;lv_obj_add_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
    }
    for(int i=0;i<4;i++) {
        const cJSON *item=cJSON_GetArrayItem(items,i);const char *field=scene_string(item,"label");
        if(!strcmp(ui.scene_kind,"metric")&&(!strcmp(field,"Provider")||!strcmp(field,"Network")||
            !strcmp(field,"Microphone")||!strcmp(field,"Runtime")||!strcmp(field,"Transport")||
            !strcmp(field,"Device speech playback")||!strcmp(field,"Music / smart home / computer")))lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
        if(!strcmp(scene_string(item,"label"),"Open-Meteo · UTC")) {
            lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);display_label(ui.attribution,"Weather: Open-Meteo");lv_obj_remove_flag(ui.attribution,LV_OBJ_FLAG_HIDDEN);
        }
    }
    bool browse=!strcmp(ui.scene_kind,"timeline")||(!strcmp(ui.scene_kind,"metric")&&
        !strcmp(ui.button_actions[0],"memories")&&!strcmp(ui.button_actions[3],"music"));
    for(int i=0;i<5;i++) {
        const char *id=ui.button_actions[i];
        if(!strncmp(id,"calendar",8)||!strncmp(id,"event:",6))browse=true;
    }
    if(browse)browse_layout(surface);
    if(!strcmp(ui.scene_kind,"metric")&&!strcmp(scene_string(surface,"title"),"Weather")) {
        weather_layout(surface);
        if(ui.weather_completion_pending){friday_presence_complete(ui.presence);ui.weather_completion_pending=false;}
    }
    recovery_notice_update();
    ui.experience_building=false;ui.experience_protected=protected_surface(surface);
    ui.experience_sensitive=ui.experience_protected||memory_detail||memory_list;
    ui.experience_memory_page=false;
    for(int i=0;i<5;i++)if(!strncmp(ui.button_actions[i],"memyes:",7)||!strncmp(ui.button_actions[i],"memreview:",10))ui.experience_memory_page=true;
    ui.explicit_ambient_items=!ui.scene_active&&cJSON_GetArraySize(items)>0;
    if(ui.experience_protected&&!ui.experience_memory_page) {
        size_t capacity=strlen(scene_string(surface,"subtitle"))+1;
        for(int i=0;i<4;i++) {const cJSON *item=cJSON_GetArrayItem(items,i);capacity+=strlen(scene_string(item,"time"))+strlen(scene_string(item,"label"))+4;}
        char *full=calloc(capacity,1);
        if(!full){for(int i=0;i<5;i++)lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);bsp_display_unlock();return false;}
        snprintf(full,capacity,"%s",scene_string(surface,"subtitle"));
        for(int i=0;i<4;i++) {const cJSON *item=cJSON_GetArrayItem(items,i);if(!item)continue;size_t used=strlen(full);snprintf(full+used,capacity-used,"\n%s%s%s",scene_string(item,"time"),scene_string(item,"time")[0]?" / ":"",scene_string(item,"label"));}
        display_label(ui.message,full);free(full);
        for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
    }
    if(ui.weather_view) {
        if(ui.context_weather_expiry){lv_timer_delete(ui.context_weather_expiry);ui.context_weather_expiry=NULL;}
        const cJSON *lease=cJSON_GetObjectItemCaseSensitive(surface,"weather_valid_ms");
        {
            ambient_context.linked_weather=false;ui.weather_focus=true;ambient_context.weather_seen=true;ambient_context.weather_until=cJSON_IsNumber(lease)&&lease->valuedouble>=1&&lease->valuedouble<=300000?lv_tick_get()+(uint32_t)lease->valuedouble:0;
            if(ambient_context.weather_until){ui.context_weather_expiry=lv_timer_create(context_weather_expired,(uint32_t)lease->valuedouble,NULL);if(ui.context_weather_expiry)lv_timer_set_repeat_count(ui.context_weather_expiry,1);else ambient_context.weather_until=lv_tick_get();}
            for(int i=0;i<2;i++) {
                const cJSON *item=cJSON_GetArrayItem(items,i*2);
                friday_display_text(ambient_context.city[i],sizeof(ambient_context.city[i]),scene_string(item,"time"),supports_glyph,(void*)&lv_font_montserrat_18);
                friday_display_text(ambient_context.temperature[i],sizeof(ambient_context.temperature[i]),scene_string(item,"label"),supports_glyph,(void*)&lv_font_montserrat_24);

                snprintf(ambient_context.condition[i],sizeof(ambient_context.condition[i]),"Now / %s",scene_string(item,"condition")[0]?scene_string(item,"condition"):"Weather unavailable");
                const cJSON *forecast=cJSON_GetArrayItem(items,i*2+1);const char *chance=scene_string(forecast,"label");
                if(!strncmp(chance,"Rain / snow chance ",strlen("Rain / snow chance ")))snprintf(ambient_context.outlook[i],sizeof(ambient_context.outlook[i]),"Rain / snow: %s",chance+strlen("Rain / snow chance "));else snprintf(ambient_context.outlook[i],sizeof(ambient_context.outlook[i]),"%s",chance);
                if(ui.weather_tile[i])lv_obj_set_style_bg_image_src(ui.weather_tile[i],friday_weather_art(ui.weather_city[i],ui.weather_code[i],ui.weather_day[i]),0);
                snprintf(ambient_context.detail[i],sizeof(ambient_context.detail[i]),"%s",lv_label_get_text(ui.weather_details[i]));
            }
        }
    } else if(!ui.experience_sensitive) {
        if(owned_media_render&&!continuation)ui.weather_focus=false;
        if(!strcmp(ui.scene_kind,"timeline"))ambient_context.recent_view=1;
        else if(!strcmp(scene_string(surface,"title"),"Your reminders"))ambient_context.recent_view=2;
        else if(owned_media_render)ambient_context.recent_view=3;
    }
    if(ui.experience_preview&&ui.weather_view) {
        const char *text=scene_string(surface,"subtitle");size_t n=strcspn(text,"\n");
        if(n>=sizeof(ui.experience_title))n=sizeof(ui.experience_title)-1;
        memcpy(ui.experience_title,text,n);ui.experience_title[n]=0;
    }
    if(ui.experience_preview&&!strcmp(ui.scene_kind,"timeline"))calendar_context_capture(surface);
    if(calendar_update) {
        ui.calendar_status=true;ui.calendar_reply=false;
        lv_obj_scroll_to_y(ui.status_update_panel,0,LV_ANIM_OFF);
        size_t size=strlen(scene_string(surface,"title"))+strlen(scene_string(surface,"subtitle"))+4;
        for(int i=0;i<4;i++){const cJSON *item=cJSON_GetArrayItem(items,i);size+=strlen(scene_string(item,"time"))+strlen(scene_string(item,"label"))+4;}
        char *fact=calloc(size,1);
        if(!fact){calendar_context_clear();bsp_display_unlock();return false;}
        for(int i=0;i<4;i++){const cJSON *item=cJSON_GetArrayItem(items,i);if(item){size_t n=strlen(fact);snprintf(fact+n,size-n,"%s%s / %s",n?"\n":"",scene_string(item,"time"),scene_string(item,"label"));}}
        size_t n=strlen(fact);snprintf(fact+n,size-n,"\n%s\n%s",scene_string(surface,"title"),scene_string(surface,"subtitle"));
        set_message_text(fact);free(fact);
    }
    const cJSON *context=cJSON_GetObjectItemCaseSensitive(surface,"context");
    if(cJSON_IsObject(context)&&!protected_surface(surface)) {
        ui.linked_context=true;
        friday_display_text(ui.linked_weather_summary,sizeof(ui.linked_weather_summary),scene_string(context,"weather_summary"),supports_glyph,(void *)&lv_font_montserrat_18);
        const char *view=scene_string(context,"view");ui.linked_view=!strcmp(view,"event")?1:!strcmp(view,"weather")?2:0;
        lv_snprintf(ui.linked_event_ref,sizeof(ui.linked_event_ref),"%s",scene_string(context,"event_ref"));
        lv_snprintf(ui.linked_weather_ref,sizeof(ui.linked_weather_ref),"%s",scene_string(context,"weather_ref"));
        const cJSON *event_ms=cJSON_GetObjectItemCaseSensitive(context,"event_valid_ms");
        const cJSON *weather_ms=cJSON_GetObjectItemCaseSensitive(context,"weather_valid_ms");
        const char *place=scene_string(context,"weather_location");
        if(ui.linked_weather_ref[0]&&strlen(place)==5&&strspn(place,"0123456789")==5) {
            /* Public Weather only. Never retain event rows, narration or actions.
             * This independent layer survives ordinary scene replacement. */
            retained_weather_clear();ambient_context.weather_seen=true;ambient_context.linked_weather=true;
            ui.weather_focus=false;
            lv_snprintf(ambient_context.city[0],sizeof(ambient_context.city[0]),"Weather / %s",place);
            bool valid=cJSON_IsNumber(weather_ms)&&weather_ms->valuedouble>=1&&weather_ms->valuedouble<=300000;
            ambient_context.weather_until=lv_tick_get()+(valid?(uint32_t)weather_ms->valuedouble:0);
            if(!ambient_context.weather_until)ambient_context.weather_until=1;
            if(valid) {
                friday_display_text(ambient_context.temperature[0],sizeof(ambient_context.temperature[0]),scene_string(context,"weather_temperature"),supports_glyph,(void *)&lv_font_montserrat_24);
                friday_display_text(ambient_context.condition[0],sizeof(ambient_context.condition[0]),scene_string(context,"weather_condition"),supports_glyph,(void *)&lv_font_montserrat_18);
                friday_display_text(ambient_context.outlook[0],sizeof(ambient_context.outlook[0]),scene_string(context,"weather_outlook"),supports_glyph,(void *)&lv_font_montserrat_18);
                ui.context_weather_expiry=lv_timer_create(context_weather_expired,(uint32_t)weather_ms->valuedouble,NULL);
                if(ui.context_weather_expiry)lv_timer_set_repeat_count(ui.context_weather_expiry,1);
                else {ambient_context.weather_until=lv_tick_get();memset(ambient_context.temperature,0,sizeof(ambient_context.temperature));memset(ambient_context.condition,0,sizeof(ambient_context.condition));memset(ambient_context.outlook,0,sizeof(ambient_context.outlook));}
            }
            if(ui.weather_tile[0])lv_obj_set_style_bg_image_src(ui.weather_tile[0],NULL,0);
        }
        /* Invalid/missing timers erase facts immediately; allocation fails closed. */
        if(cJSON_IsNumber(event_ms)&&event_ms->valuedouble>=1&&event_ms->valuedouble<=300000)
            ui.linked_event_expiry=lv_timer_create(linked_expired,(uint32_t)event_ms->valuedouble,NULL);
        if(ui.linked_event_expiry)lv_timer_set_repeat_count(ui.linked_event_expiry,1);
        else linked_erase(true);
        if(cJSON_IsNumber(weather_ms)&&weather_ms->valuedouble>=1&&weather_ms->valuedouble<=300000)
            ui.linked_weather_expiry=lv_timer_create(linked_expired,(uint32_t)weather_ms->valuedouble,NULL);
        if(ui.linked_weather_expiry)lv_timer_set_repeat_count(ui.linked_weather_expiry,1);
        else linked_erase(false);
    }
    experience_layout();
    if(ui.experience_preview&&!ui.experience_sensitive&&!continuation) {
        /* Native object motion only; no snapshots/private facts/framebuffers. */
        lv_obj_t *objects[]={ui.message,ui.cover,ui.artist,ui.experience_detail,ui.cards[0],ui.cards[1],ui.cards[2],ui.cards[3]};
        for(unsigned i=0;i<sizeof(objects)/sizeof(objects[0]);i++) {
            lv_obj_t *o=objects[i];
            if(!o||!lv_obj_is_visible(o)||lv_obj_has_flag(o,LV_OBJ_FLAG_CLICKABLE)||lv_obj_has_flag(o,LV_OBJ_FLAG_SCROLLABLE))continue;
            lv_anim_t animation;lv_anim_init(&animation);lv_anim_set_var(&animation,o);
            lv_anim_set_exec_cb(&animation,experience_transition_exec);lv_anim_set_values(&animation,160,255);lv_anim_set_duration(&animation,160);lv_anim_set_path_cb(&animation,lv_anim_path_ease_out);lv_anim_start(&animation);
        }
    }
    log_ui_snapshot("scene");
    bsp_display_unlock();
    return true;
}

/* Composition prototype: one conversation band, content well and action
 * dock. It repositions real native objects; no alternate HTML renderer. */
static void experience_layout(void)
{
    if(!ui.experience_preview||!ui.experience_well||ui.experience_building)return;
    bool protected=ui.experience_protected||(overlay.active&&overlay.protected);
    for(int i=0;i<5;i++)if(!strncmp(ui.button_actions[i],"memyes:",7)||!strncmp(ui.button_actions[i],"memno:",6)||!strncmp(ui.button_actions[i],"confirm",7)||!strncmp(ui.button_actions[i],"decline",7))protected=true;
    /* Exact complete-fact/consent geometry remains a separate acceptance gate. */
    lv_obj_remove_flag(ui.experience_well,LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_size(ui.presence,160,160);lv_obj_align(ui.presence,LV_ALIGN_TOP_LEFT,280,32);
    friday_presence_set_compact(ui.presence,false);friday_presence_set_material(ui.presence,true);
    const char *activity=friday_state_get()==FRIDAY_STATE_LISTENING?"Listening":friday_state_get()==FRIDAY_STATE_SPEAKING?"Speaking":friday_state_get()==FRIDAY_STATE_ALERT?"Attention":ui.tool_awaiting_result?"Awaiting result":ui.tool_working?"Working":friday_state_get()==FRIDAY_STATE_THINKING?"Thinking":ui.calendar_reply?"Reply":ui.calendar_status?"Status update":"";
    char header[112];lv_snprintf(header,sizeof(header),"Friday%s%s",*activity?"\n":"",activity);display_label(ui.eyebrow,header);
    lv_obj_set_style_text_letter_space(ui.eyebrow,0,0);lv_obj_set_size(ui.eyebrow,208,52);lv_obj_align(ui.eyebrow,LV_ALIGN_TOP_LEFT,48,40);
    lv_obj_set_style_text_font(ui.title,&lv_font_montserrat_24,0);lv_obj_set_size(ui.title,624,28);lv_obj_align(ui.title,LV_ALIGN_TOP_LEFT,48,194);lv_label_set_long_mode(ui.title,ui.linked_context&&ui.linked_view==1?LV_LABEL_LONG_MODE_SCROLL_CIRCULAR:LV_LABEL_LONG_MODE_WRAP);if(ui.linked_context&&ui.linked_view==1)lv_obj_set_style_anim_duration(ui.title,9000,0);
    weather_context_layout(protected||ui.linked_context);
    if(ui.context_back){bool focused=ui.weather_focus&&!ui.weather_view&&!protected&&!ui.experience_sensitive;if(focused){lv_obj_remove_flag(ui.context_back,LV_OBJ_FLAG_HIDDEN);lv_obj_remove_flag(ui.context_refresh,LV_OBJ_FLAG_HIDDEN);}else{lv_obj_add_flag(ui.context_back,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.context_refresh,LV_OBJ_FLAG_HIDDEN);}}
    if(ui.review_control){if(ambient_context.review_pending&&!ui.scene_active&&!protected)lv_obj_remove_flag(ui.review_control,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(ui.review_control,LV_OBJ_FLAG_HIDDEN);}
    if(!ui.scene_active&&!ui.weather_focus)lv_obj_add_flag(ui.experience_well,LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_height(ui.experience_well,ui.scene_active&&strcmp(ui.scene_kind,"media")&&!ui.weather_view&&!protected?368:352);
    if(ui.calendar_layer){if(ui.calendar_retained&&!ui.weather_focus&&!protected)lv_obj_remove_flag(ui.calendar_layer,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(ui.calendar_layer,LV_OBJ_FLAG_HIDDEN);}
    if(ui.calendar_back)lv_obj_add_flag(ui.calendar_back,LV_OBJ_FLAG_HIDDEN);
    if(ui.status_update_panel&&(!ui.conversation_reply||ui.weather_focus||protected))lv_obj_add_flag(ui.status_update_panel,LV_OBJ_FLAG_HIDDEN);
    if(protected) {
        lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
        display_label(ui.title,ui.experience_title);
        lv_obj_add_flag(ui.experience_detail,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(ui.message,620,ui.experience_memory_page?76:284);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,50,278);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_24,0);lv_obj_set_style_text_letter_space(ui.message,ui.experience_memory_page?2:0,0);lv_obj_set_style_text_line_space(ui.message,0,0);
        if(!ui.experience_memory_page) {
            lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);lv_obj_add_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_scroll_dir(ui.message,LV_DIR_VER);lv_obj_set_scrollbar_mode(ui.message,LV_SCROLLBAR_MODE_AUTO);
        }
        int n=0;for(int i=0;i<5;i++)if(lv_obj_is_visible(ui.buttons[i]))n++;
        if(n)for(int i=0,index=0;i<5;i++)if(lv_obj_is_visible(ui.buttons[i])) {
            int width=(624-(n-1)*16)/n;lv_obj_set_size(ui.buttons[i],width,64);lv_obj_align(ui.buttons[i],LV_ALIGN_TOP_LEFT,48+index++*(width+16),624);lv_obj_set_size(ui.button_labels[i],width-16,32);lv_obj_set_style_text_font(ui.button_labels[i],&lv_font_montserrat_24,0);lv_obj_center(ui.button_labels[i]);
        }
        return;
    }
    if(ui.calendar_retained&&!ui.weather_focus&&!protected) {
        display_label(ui.title,ui.calendar_reply?"Calendar / last read":ui.calendar_title);
        for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui.browse_date,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.browse_meta,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.experience_detail,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(ui.message,624,48);lv_obj_set_pos(ui.message,48,224);lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_18,0);lv_obj_set_style_text_letter_space(ui.message,0,0);lv_obj_set_style_text_align(ui.message,LV_TEXT_ALIGN_LEFT,0);lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);
        if(ui.calendar_status){
            lv_obj_set_pos(ui.status_update_panel,48,224);
            lv_obj_remove_flag(ui.status_update_panel,LV_OBJ_FLAG_HIDDEN);lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_parent(ui.message,ui.status_update_panel);lv_obj_set_pos(ui.message,0,0);lv_obj_set_size(ui.message,548,LV_SIZE_CONTENT);lv_obj_set_style_text_line_space(ui.message,0,0);lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);
        }
        bool return_action=false;int count=0;
        for(int i=0;i<5;i++)if(ui.button_actions[i][0]){lv_obj_remove_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);count++;if(!strcmp(ui.button_actions[i],"calendar"))return_action=true;}
        bool extra=ui.calendar_status&&!return_action;
        int total=count+(extra?1:0),width=total?(624-(total-1)*12)/total:624,index=0;
        for(int i=0;i<5;i++)if(lv_obj_is_visible(ui.buttons[i])) {
            lv_obj_set_size(ui.buttons[i],width,64);lv_obj_set_pos(ui.buttons[i],48+index++*(width+12),624);
            lv_obj_add_flag(ui.browse_icons[i],LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.browse_hints[i],LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_size(ui.button_labels[i],width-16,48);lv_obj_set_style_text_font(ui.button_labels[i],total>3?&lv_font_montserrat_18:&lv_font_montserrat_24,0);lv_obj_center(ui.button_labels[i]);
        }
        if(extra){lv_obj_set_size(ui.calendar_back,width,64);lv_obj_set_pos(ui.calendar_back,48+index*(width+12),624);lv_obj_remove_flag(ui.calendar_back,LV_OBJ_FLAG_HIDDEN);}
        return;
    }
    if((!ui.scene_active||!strcmp(ui.scene_kind,"TOOL"))&&!ui.explicit_ambient_items) {
        for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
        if(strcmp(lv_label_get_text(ui.message),"Right here.")&&strcmp(lv_label_get_text(ui.message),"Ready"))lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
    }
    bool media=ui.scene_active&&!strcmp(ui.scene_kind,"media");
    if(ui.weather_focus&&!ui.weather_view)display_label(ui.title,"Weather, still here.");
    else if(media)display_label(ui.title,ui.playback_snapshot?"Current playback.":"Here's a little soundtrack.");
    else if(!ui.scene_active)display_label(ui.title,"Right here, when you need me.");
    else if(ui.experience_title[0]&&ui.scene_active)display_label(ui.title,ui.experience_title);
    lv_obj_add_flag(ui.experience_detail,LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_text_letter_space(ui.message,0,0);lv_obj_set_style_text_align(ui.message,LV_TEXT_ALIGN_LEFT,0);
    if((ui.weather_view&&ambient_context.weather_seen)||ui.weather_focus) {
        if(ui.weather_view) {lv_obj_set_size(ui.message,624,44);lv_obj_set_pos(ui.message,48,224);lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_18,0);lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);}else lv_obj_add_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<4;i++){if(ui.weather_focus&&!ui.weather_view&&lv_obj_is_visible(ui.cards[i]))ui.context_cards_mask|=1U<<i;lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);}
        for(int i=0;i<2;i++)lv_obj_add_flag(ui.weather_details[i],LV_OBJ_FLAG_HIDDEN);
        if(ui.weather_focus&&!ui.weather_view){for(int i=0;i<5;i++){if(lv_obj_is_visible(ui.buttons[i]))ui.context_actions_mask|=1U<<i;lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);}}
        if(ui.weather_focus&&media) {
            lv_obj_add_flag(ui.cover,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.artist,LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(ui.progress,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.media_time,LV_OBJ_FLAG_HIDDEN);
            for(int i=0;i<5;i++)lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
        }
    } else if(media) {
        lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(ui.cover,LV_OBJ_FLAG_HIDDEN);lv_obj_remove_flag(ui.artist,LV_OBJ_FLAG_HIDDEN);lv_obj_remove_flag(ui.progress,LV_OBJ_FLAG_HIDDEN);lv_obj_remove_flag(ui.media_time,LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<5;i++)if(ui.button_actions[i][0])lv_obj_remove_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(ui.cover,160,160);lv_obj_align(ui.cover,LV_ALIGN_TOP_LEFT,48,300);lv_image_set_scale(ui.cover_image,192);
        display_label(ui.experience_detail,ui.experience_title);lv_obj_set_size(ui.experience_detail,400,56);lv_obj_align(ui.experience_detail,LV_ALIGN_TOP_LEFT,240,300);lv_obj_set_style_text_font(ui.experience_detail,&lv_font_montserrat_24,0);lv_obj_remove_flag(ui.experience_detail,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(ui.artist,400,56);lv_obj_align(ui.artist,LV_ALIGN_TOP_LEFT,240,368);
        lv_obj_set_size(ui.progress,400,4);lv_obj_align(ui.progress,LV_ALIGN_TOP_LEFT,240,432);
        lv_obj_set_size(ui.media_time,400,26);lv_obj_align(ui.media_time,LV_ALIGN_TOP_LEFT,240,444);
        lv_obj_set_size(ui.message,624,112);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,478);lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);
        layout_lyric(false);
        lv_obj_align(ui.lyric_caption,LV_ALIGN_TOP_LEFT,48,596);
    } else if(ui.browse_mode==1) {
        lv_obj_add_flag(ui.message,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.browse_date,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.browse_meta,LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<5;i++) {
            lv_obj_set_size(ui.buttons[i],624,52);lv_obj_align(ui.buttons[i],LV_ALIGN_TOP_LEFT,48,332+i*60);
            lv_obj_add_flag(ui.browse_icons[i],LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.browse_hints[i],LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_size(ui.button_labels[i],584,32);lv_obj_center(ui.button_labels[i]);lv_obj_set_style_text_font(ui.button_labels[i],&lv_font_montserrat_24,0);
        }
        return;
    } else if(ui.linked_context) {
        lv_obj_add_flag(ui.browse_date,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.browse_meta,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(ui.message,624,ui.linked_view==1?44:64);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,224);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_18,0);lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);
        lv_obj_add_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_scroll_dir(ui.message,LV_DIR_VER);lv_obj_set_scrollbar_mode(ui.message,LV_SCROLLBAR_MODE_AUTO);
        if(ui.linked_view==1) {
            display_label(ui.experience_detail,ui.linked_weather_summary);lv_obj_set_size(ui.experience_detail,624,44);lv_obj_align(ui.experience_detail,LV_ALIGN_TOP_LEFT,48,276);
            lv_obj_set_style_text_font(ui.experience_detail,&lv_font_montserrat_18,0);lv_label_set_long_mode(ui.experience_detail,LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);lv_obj_set_style_anim_duration(ui.experience_detail,9000,0);lv_obj_remove_flag(ui.experience_detail,LV_OBJ_FLAG_HIDDEN);
        }
        for(int i=0;i<4;i++)if(lv_obj_is_visible(ui.cards[i])) {
            lv_obj_set_size(ui.cards[i],624,ui.linked_view==1?64:68);lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,48,(ui.linked_view==1?332:316)+i*(ui.linked_view==1?72:74));
            lv_obj_set_size(ui.card_times[i],140,48);lv_obj_align(ui.card_times[i],LV_ALIGN_LEFT_MID,16,0);
            lv_obj_set_size(ui.card_labels[i],432,56);lv_obj_align(ui.card_labels[i],LV_ALIGN_LEFT_MID,176,0);
            lv_obj_set_style_text_font(ui.card_labels[i],&lv_font_montserrat_24,0);lv_obj_set_style_text_font(ui.card_times[i],&lv_font_montserrat_18,0);
            lv_label_set_long_mode(ui.card_labels[i],LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);
            lv_obj_set_style_anim_duration(ui.card_labels[i],9000,0);
        }
    } else if(ui.countdown) {
        lv_obj_set_size(ui.message,624,44);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,224);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_18,0);
        lv_obj_set_size(ui.cards[0],624,112);lv_obj_align(ui.cards[0],LV_ALIGN_TOP_LEFT,48,332);
        lv_obj_set_size(ui.card_times[0],584,28);lv_obj_align(ui.card_times[0],LV_ALIGN_TOP_LEFT,20,8);
        lv_obj_set_size(ui.card_labels[0],584,64);lv_obj_align(ui.card_labels[0],LV_ALIGN_TOP_LEFT,20,40);
        lv_obj_set_style_text_font(ui.card_labels[0],&lv_font_montserrat_48,0);
        for(int i=1;i<4;i++)if(lv_obj_is_visible(ui.cards[i])) {
            lv_obj_set_size(ui.cards[i],624,48);lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,48,452+(i-1)*56);
        }
    } else if(ui.scene_active&&lv_obj_is_visible(ui.cards[0])&&!strcmp(ui.scene_kind,"timeline")) {
        lv_obj_add_flag(ui.browse_date,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.browse_meta,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_size(ui.message,624,44);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,224);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_18,0);
        for(int i=0;i<4;i++)if(lv_obj_is_visible(ui.cards[i])) {
            lv_obj_set_size(ui.cards[i],624,64);lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,48,332+i*72);
            lv_obj_set_style_bg_color(ui.cards[i],color_hex(UI_CARD),0);lv_obj_set_style_bg_opa(ui.cards[i],LV_OPA_COVER,0);
            lv_obj_set_size(ui.card_times[i],104,32);lv_obj_align(ui.card_times[i],LV_ALIGN_LEFT_MID,16,0);
            lv_obj_set_size(ui.card_labels[i],464,56);lv_obj_align(ui.card_labels[i],LV_ALIGN_LEFT_MID,144,0);
            lv_obj_set_style_text_font(ui.card_labels[i],&lv_font_montserrat_24,0);lv_obj_set_style_text_font(ui.card_times[i],&lv_font_montserrat_18,0);
        }
    } else if(ui.scene_active&&(lv_obj_is_visible(ui.cards[0])||lv_obj_is_visible(ui.cards[1])||lv_obj_is_visible(ui.cards[2])||lv_obj_is_visible(ui.cards[3]))) {
        lv_obj_set_size(ui.message,624,44);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,224);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_18,0);
        int row=0;
        for(int i=0;i<4;i++)if(lv_obj_is_visible(ui.cards[i])) {
            lv_obj_set_size(ui.cards[i],624,64);lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,48,332+row++*72);
            bool prefix=lv_label_get_text(ui.card_times[i])[0]!=0;
            lv_obj_set_size(ui.card_times[i],176,32);lv_obj_align(ui.card_times[i],LV_ALIGN_LEFT_MID,16,0);
            lv_label_set_long_mode(ui.card_times[i],LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);
            lv_obj_set_style_anim_duration(ui.card_times[i],9000,0);
            lv_obj_set_size(ui.card_labels[i],prefix?384:592,56);lv_obj_align(ui.card_labels[i],LV_ALIGN_LEFT_MID,prefix?216:16,0);
            lv_obj_set_style_text_font(ui.card_labels[i],&lv_font_montserrat_24,0);
            lv_label_set_long_mode(ui.card_labels[i],LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);
            lv_obj_set_style_anim_duration(ui.card_labels[i],9000,0);
        }
    } else if(!ui.scene_active&&lv_obj_is_visible(ui.cards[0])) {
        lv_obj_set_size(ui.message,576,56);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,72,278);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_24,0);
        for(int i=0;i<4;i++)if(lv_obj_is_visible(ui.cards[i])) {
            lv_obj_set_size(ui.cards[i],624,72);lv_obj_align(ui.cards[i],LV_ALIGN_TOP_LEFT,48,340+i*80);
            lv_obj_set_style_bg_color(ui.cards[i],color_hex(UI_CARD),0);lv_obj_set_style_bg_opa(ui.cards[i],LV_OPA_COVER,0);
            lv_obj_set_size(ui.card_times[i],104,32);lv_obj_align(ui.card_times[i],LV_ALIGN_LEFT_MID,16,0);
            lv_obj_set_size(ui.card_labels[i],464,56);lv_obj_align(ui.card_labels[i],LV_ALIGN_LEFT_MID,144,0);
            lv_obj_set_style_text_font(ui.card_labels[i],&lv_font_montserrat_24,0);lv_obj_set_style_text_font(ui.card_times[i],&lv_font_montserrat_18,0);
        }
    } else if(!ui.scene_active) {
        lv_obj_set_size(ui.message,624,48);lv_obj_set_pos(ui.message,48,520);lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_18,0);lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);
    } else {
        lv_obj_set_size(ui.message,624,256);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,332);lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);lv_obj_add_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_scroll_dir(ui.message,LV_DIR_VER);lv_obj_set_scrollbar_mode(ui.message,LV_SCROLLBAR_MODE_AUTO);
        lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_24,0);
    }
    if(ui.conversation_reply&&!media&&!protected&&!ui.weather_focus) {
        lv_obj_set_pos(ui.status_update_panel,48,ui.weather_view?220:224);lv_obj_remove_flag(ui.status_update_panel,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_parent(ui.message,ui.status_update_panel);lv_obj_set_pos(ui.message,0,0);lv_obj_set_size(ui.message,548,LV_SIZE_CONTENT);lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_18,0);lv_obj_set_style_text_line_space(ui.message,0,0);lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_SCROLLABLE);lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);
    }
    if(media&&ui.weather_layer&&!ui.weather_focus&&ambient_context.weather_seen&&lv_obj_get_height(ui.weather_layer)>48) {
        ui.context_reflow=true;
        lv_obj_t *incoming[]={ui.cover,ui.artist,ui.experience_detail,ui.message,ui.media_time,ui.progress,ui.lyric_caption};
        for(unsigned i=0;i<sizeof(incoming)/sizeof(incoming[0]);i++)lv_obj_add_flag(incoming[i],LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<5;i++)lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
    }
    if(ui.experience_sensitive&&!protected){lv_obj_set_size(ui.message,620,76);lv_obj_set_pos(ui.message,50,240);lv_obj_set_style_text_font(ui.message,&lv_font_montserrat_24,0);lv_obj_set_style_text_letter_space(ui.message,2,0);}
    int n=0;for(int i=0;i<5;i++)if(lv_obj_is_visible(ui.buttons[i]))n++;
    if(n) {
        int width=(624-(n-1)*16)/n,index=0;
        for(int i=0;i<5;i++)if(lv_obj_is_visible(ui.buttons[i])) {
            lv_obj_set_size(ui.buttons[i],width,64);lv_obj_align(ui.buttons[i],LV_ALIGN_TOP_LEFT,48+index*(width+16),624);index++;
            bool primary=media&&(ui.playback_snapshot?!strcmp(ui.button_actions[i],"music"):(!strcmp(lv_label_get_text(ui.button_labels[i]),LV_SYMBOL_PLAY)||!strcmp(lv_label_get_text(ui.button_labels[i]),LV_SYMBOL_PAUSE)));
            primary=primary||(ui.linked_context&&((ui.linked_view==1&&!strcmp(lv_label_get_text(ui.button_labels[i]),"Event"))||(ui.linked_view==2&&!strcmp(lv_label_get_text(ui.button_labels[i]),"Weather"))));
            lv_obj_set_style_radius(ui.buttons[i],20,0);lv_obj_set_style_bg_color(ui.buttons[i],color_hex(primary?0xB2DCD2:UI_CONTROL),0);
            lv_obj_set_style_text_color(ui.button_labels[i],color_hex(primary?UI_PRIMARY_TEXT:UI_TEXT),0);
            if(media&&!strcmp(ui.button_actions[i],"music")){lv_obj_set_size(ui.button_labels[i],width-24,32);lv_obj_set_style_text_font(ui.button_labels[i],&lv_font_montserrat_24,0);}
            lv_obj_center(ui.button_labels[i]);
        }
    }
}
void friday_ui_experience_preview(bool enabled)
{
    if(!bsp_display_lock(1000))return;
    if(!enabled)calendar_context_clear();
    if(enabled&&!ui.experience_well) {
        ui.experience_well=lv_obj_create(lv_screen_active());lv_obj_remove_style_all(ui.experience_well);
        lv_obj_remove_flag(ui.experience_well,LV_OBJ_FLAG_CLICKABLE);lv_obj_remove_flag(ui.experience_well,LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_size(ui.experience_well,656,352);lv_obj_align(ui.experience_well,LV_ALIGN_TOP_LEFT,32,252);
        lv_obj_set_style_bg_color(ui.experience_well,color_hex(UI_PANEL),0);lv_obj_set_style_bg_opa(ui.experience_well,LV_OPA_COVER,0);lv_obj_set_style_radius(ui.experience_well,28,0);lv_obj_move_to_index(ui.experience_well,0);
        ui.experience_detail=make_label(lv_screen_active(),"",&lv_font_montserrat_24,UI_TEXT);
        ui.calendar_layer=lv_obj_create(lv_screen_active());lv_obj_remove_style_all(ui.calendar_layer);lv_obj_remove_flag(ui.calendar_layer,LV_OBJ_FLAG_SCROLLABLE);lv_obj_remove_flag(ui.calendar_layer,LV_OBJ_FLAG_CLICKABLE);lv_obj_set_pos(ui.calendar_layer,48,332);lv_obj_set_size(ui.calendar_layer,624,280);lv_obj_add_flag(ui.calendar_layer,LV_OBJ_FLAG_HIDDEN);
        for(int i=0;i<4;i++) {
            ui.calendar_rows[i]=lv_obj_create(ui.calendar_layer);lv_obj_remove_style_all(ui.calendar_rows[i]);lv_obj_set_pos(ui.calendar_rows[i],0,i*72);lv_obj_set_size(ui.calendar_rows[i],624,64);lv_obj_set_style_radius(ui.calendar_rows[i],24,0);lv_obj_set_style_bg_color(ui.calendar_rows[i],color_hex(UI_CARD),0);lv_obj_set_style_bg_opa(ui.calendar_rows[i],LV_OPA_COVER,0);lv_obj_remove_flag(ui.calendar_rows[i],LV_OBJ_FLAG_SCROLLABLE);lv_obj_add_event_cb(ui.calendar_rows[i],scene_action_event,LV_EVENT_CLICKED,ui.calendar_actions[i]);
            ui.calendar_times[i]=make_label(ui.calendar_rows[i],"",&lv_font_montserrat_18,0xAAB4C2);lv_obj_set_pos(ui.calendar_times[i],16,16);lv_obj_set_size(ui.calendar_times[i],104,32);
            ui.calendar_labels[i]=make_label(ui.calendar_rows[i],"",&lv_font_montserrat_24,UI_TEXT);lv_obj_set_pos(ui.calendar_labels[i],144,4);lv_obj_set_size(ui.calendar_labels[i],464,56);lv_label_set_long_mode(ui.calendar_labels[i],LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);lv_obj_set_style_anim_duration(ui.calendar_labels[i],9000,0);
        }
        ui.calendar_back=lv_button_create(lv_screen_active());lv_obj_set_style_radius(ui.calendar_back,20,0);lv_obj_set_style_bg_color(ui.calendar_back,color_hex(UI_CONTROL),0);lv_obj_t *calendar_caption=make_label(ui.calendar_back,"Calendar",&lv_font_montserrat_24,UI_TEXT);lv_obj_center(calendar_caption);lv_obj_add_event_cb(ui.calendar_back,scene_action_event,LV_EVENT_CLICKED,"calendar");lv_obj_add_flag(ui.calendar_back,LV_OBJ_FLAG_HIDDEN);
        ui.status_update_panel=lv_obj_create(lv_screen_active());lv_obj_remove_style_all(ui.status_update_panel);lv_obj_set_pos(ui.status_update_panel,48,224);lv_obj_set_size(ui.status_update_panel,624,48);lv_obj_set_scroll_dir(ui.status_update_panel,LV_DIR_VER);lv_obj_set_scrollbar_mode(ui.status_update_panel,LV_SCROLLBAR_MODE_AUTO);lv_obj_add_flag(ui.status_update_panel,LV_OBJ_FLAG_HIDDEN);
        lv_obj_t *status_hint=make_label(ui.status_update_panel,"Swipe",&lv_font_montserrat_18,0xAAB4C2);lv_obj_set_pos(status_hint,560,14);lv_obj_add_flag(status_hint,LV_OBJ_FLAG_FLOATING);
        ui.weather_layer=lv_obj_create(lv_screen_active());lv_obj_remove_style_all(ui.weather_layer);
        lv_obj_remove_flag(ui.weather_layer,LV_OBJ_FLAG_SCROLLABLE);lv_obj_remove_flag(ui.weather_layer,LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_bg_opa(ui.weather_layer,LV_OPA_COVER,0);lv_obj_set_style_bg_color(ui.weather_layer,color_hex(UI_PANEL),0);lv_obj_set_style_radius(ui.weather_layer,24,0);
        lv_obj_set_pos(ui.weather_layer,32,268);lv_obj_set_size(ui.weather_layer,656,320);
        for(int i=0;i<2;i++) {
            ui.weather_chip[i]=make_label(ui.weather_layer,"",&lv_font_montserrat_18,UI_TEXT);
            lv_obj_set_size(ui.weather_chip[i],312,48);lv_obj_set_pos(ui.weather_chip[i],8+i*328,0);
            lv_obj_add_flag(ui.weather_chip[i],LV_OBJ_FLAG_CLICKABLE);lv_obj_add_event_cb(ui.weather_chip[i],scene_action_event,LV_EVENT_CLICKED,"context.weather");
            ui.weather_tile[i]=lv_obj_create(ui.weather_layer);lv_obj_remove_style_all(ui.weather_tile[i]);lv_obj_remove_flag(ui.weather_tile[i],LV_OBJ_FLAG_SCROLLABLE);lv_obj_remove_flag(ui.weather_tile[i],LV_OBJ_FLAG_CLICKABLE);
            lv_obj_set_size(ui.weather_tile[i],624,144);lv_obj_set_pos(ui.weather_tile[i],16,8+i*156);lv_obj_set_style_radius(ui.weather_tile[i],20,0);lv_obj_set_style_bg_opa(ui.weather_tile[i],LV_OPA_COVER,0);lv_obj_set_style_bg_color(ui.weather_tile[i],color_hex(UI_CARD),0);lv_obj_add_event_cb(ui.weather_tile[i],weather_city_art,LV_EVENT_DRAW_MAIN_END,NULL);
            ui.weather_body[i]=make_label(ui.weather_tile[i],"",&lv_font_montserrat_24,UI_TEXT);lv_obj_set_size(ui.weather_body[i],592,56);lv_obj_set_pos(ui.weather_body[i],16,8);lv_label_set_long_mode(ui.weather_body[i],LV_LABEL_LONG_MODE_WRAP);
            ui.weather_support[i]=make_label(ui.weather_tile[i],"",&lv_font_montserrat_18,UI_TEXT);lv_obj_set_size(ui.weather_support[i],592,52);lv_obj_set_pos(ui.weather_support[i],16,76);lv_label_set_long_mode(ui.weather_support[i],LV_LABEL_LONG_MODE_WRAP);
        }
        ui.experience_timer=lv_timer_create(ambient_tick,1000,NULL);
        ui.context_back=lv_button_create(lv_screen_active());ui.context_refresh=lv_button_create(lv_screen_active());
        lv_obj_t *focus_controls[]={ui.context_refresh,ui.context_back};const char *focus_titles[]={"Refresh weather","Back"};const char *focus_actions[]={"weather","context.weather"};
        for(int i=0;i<2;i++){lv_obj_set_size(focus_controls[i],300,64);lv_obj_set_pos(focus_controls[i],48+i*324,624);lv_obj_set_style_radius(focus_controls[i],20,0);lv_obj_set_style_bg_color(focus_controls[i],color_hex(UI_CONTROL),0);lv_obj_t *label=make_label(focus_controls[i],focus_titles[i],&lv_font_montserrat_24,UI_TEXT);lv_obj_center(label);lv_obj_add_event_cb(focus_controls[i],scene_action_event,LV_EVENT_CLICKED,(void*)focus_actions[i]);lv_obj_add_flag(focus_controls[i],LV_OBJ_FLAG_HIDDEN);}
        ui.review_control=lv_button_create(lv_screen_active());lv_obj_set_size(ui.review_control,624,64);lv_obj_set_pos(ui.review_control,48,624);lv_obj_set_style_radius(ui.review_control,20,0);lv_obj_set_style_bg_color(ui.review_control,color_hex(UI_CONTROL),0);
        lv_obj_t *review_label=make_label(ui.review_control,"Review a pending request",&lv_font_montserrat_24,UI_TEXT);lv_obj_center(review_label);lv_obj_add_event_cb(ui.review_control,scene_action_event,LV_EVENT_CLICKED,"recover");
    }
    ui.experience_preview=enabled;experience_layout();bsp_display_unlock();
}

static void experience_transition_exec(void *object,int32_t value)
{
    lv_obj_set_style_opa(object,value,0);
    lv_obj_set_style_translate_y(object,(255-value)*12/95,0);
}
static void experience_transition_cancel(void)
{
    if(ui.context_reflow&&ui.weather_layer){ui.context_reflow=false;lv_anim_delete(ui.weather_layer,weather_layer_motion);weather_layer_motion(ui.weather_layer,48);}
    lv_obj_t *objects[]={ui.message,ui.cover,ui.artist,ui.experience_detail,ui.cards[0],ui.cards[1],ui.cards[2],ui.cards[3]};
    for(unsigned i=0;i<sizeof(objects)/sizeof(objects[0]);i++)if(objects[i]) {
        lv_anim_delete(objects[i],experience_transition_exec);experience_transition_exec(objects[i],255);
    }
}
static void weather_layer_motion(void *object,int32_t height)
{
    lv_obj_set_height(object,height);
    int base=ui.scene_active&&strcmp(ui.scene_kind,"media")&&!ui.weather_view&&!ui.weather_focus?272:238;
    lv_obj_set_y(object,base+(height-48)*(268-base)/272);
    if(height==48){lv_obj_update_layout(object);if(!ui.weather_focus)for(int i=0;i<2;i++)lv_obj_add_flag(ui.weather_tile[i],LV_OBJ_FLAG_HIDDEN);}
    if(height==48&&ui.context_reflow) {
        ui.context_reflow=false;experience_layout();
        lv_obj_t *incoming[]={ui.cover,ui.artist,ui.experience_detail,ui.message,ui.media_time,ui.progress};
        for(unsigned i=0;i<sizeof(incoming)/sizeof(incoming[0]);i++){lv_anim_t a;lv_anim_init(&a);lv_anim_set_var(&a,incoming[i]);lv_anim_set_exec_cb(&a,experience_transition_exec);lv_anim_set_values(&a,160,255);lv_anim_set_duration(&a,160);lv_anim_start(&a);}
    }
}
static void weather_context_layout(bool protected)
{
    if(!ui.weather_layer)return;
    if(protected||ui.experience_sensitive||!ambient_context.weather_seen) {lv_obj_add_flag(ui.weather_layer,LV_OBJ_FLAG_HIDDEN);lv_anim_delete(ui.weather_layer,weather_layer_motion);return;}
    bool valid=(int32_t)(ambient_context.weather_until-lv_tick_get())>0;
    for(int i=0;i<2;i++) {
        if(!ambient_context.city[i][0]){lv_obj_add_flag(ui.weather_tile[i],LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(ui.weather_chip[i],LV_OBJ_FLAG_HIDDEN);continue;}
        lv_obj_set_width(ui.weather_chip[i],ambient_context.city[1][0]?312:624);
        char chip[128],body[448],support[448];snprintf(chip,sizeof(chip),"%s / %s",ambient_context.city[i],valid||!ambient_context.weather_until?ambient_context.temperature[i]:"Expired");
        display_label(ui.weather_chip[i],chip);
        bool expired=ambient_context.weather_until&&!valid;
        snprintf(body,sizeof(body),"%s / %s\n%s",ambient_context.city[i],expired?"Expired":ambient_context.temperature[i],expired?"Conditions expired":ambient_context.condition[i]);display_label(ui.weather_body[i],body);
        snprintf(support,sizeof(support),"%s\n%s",expired?"Forecast expired. Reopen Weather.":ambient_context.outlook[i],expired?"Refresh for current conditions.":ambient_context.detail[i]);display_label(ui.weather_support[i],support);
        /* Keep the actual old content/art in the shrinking native clip. */
        if(ui.weather_focus||lv_obj_get_height(ui.weather_layer)>48)lv_obj_remove_flag(ui.weather_tile[i],LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(ui.weather_tile[i],LV_OBJ_FLAG_HIDDEN);
        if(ui.weather_focus)lv_obj_add_flag(ui.weather_chip[i],LV_OBJ_FLAG_HIDDEN);else lv_obj_remove_flag(ui.weather_chip[i],LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_remove_flag(ui.weather_layer,LV_OBJ_FLAG_HIDDEN);
    int goal=ui.weather_focus?320:48,old=lv_obj_get_height(ui.weather_layer);
    for(int i=0;i<2;i++)lv_obj_set_y(ui.weather_tile[i],ui.weather_focus?8+i*156:52+i*156);
    if(old==48&&!ui.weather_focus)weather_layer_motion(ui.weather_layer,48);
    if(ui.weather_target!=goal) {
        ui.weather_target=goal;lv_anim_delete(ui.weather_layer,weather_layer_motion);lv_anim_t a;lv_anim_init(&a);lv_anim_set_var(&a,ui.weather_layer);lv_anim_set_exec_cb(&a,weather_layer_motion);lv_anim_set_values(&a,old,goal);lv_anim_set_duration(&a,220);lv_anim_set_path_cb(&a,lv_anim_path_ease_in_out);lv_anim_start(&a);
    }
}
static void retained_weather_clear(void)
{
    if(ui.context_weather_expiry){lv_timer_delete(ui.context_weather_expiry);ui.context_weather_expiry=NULL;}
    memset(ambient_context.city,0,sizeof(ambient_context.city));memset(ambient_context.temperature,0,sizeof(ambient_context.temperature));
    memset(ambient_context.condition,0,sizeof(ambient_context.condition));memset(ambient_context.outlook,0,sizeof(ambient_context.outlook));memset(ambient_context.detail,0,sizeof(ambient_context.detail));
    ambient_context.weather_until=0;ambient_context.weather_seen=false;ambient_context.linked_weather=false;ui.weather_focus=false;
    if(ui.weather_layer)lv_obj_add_flag(ui.weather_layer,LV_OBJ_FLAG_HIDDEN);
}
static void context_weather_expired(lv_timer_t *timer)
{
    if(ui.context_weather_expiry!=timer)return;
    ui.context_weather_expiry=NULL;ambient_tick(NULL);
}
static void ambient_tick(lv_timer_t *timer)
{
    (void)timer;
    if(ambient_context.weather_seen&&ambient_context.weather_until&&(int32_t)(ambient_context.weather_until-lv_tick_get())<=0&&ambient_context.temperature[0][0]) {
        memset(ambient_context.temperature,0,sizeof(ambient_context.temperature));memset(ambient_context.condition,0,sizeof(ambient_context.condition));memset(ambient_context.outlook,0,sizeof(ambient_context.outlook));memset(ambient_context.detail,0,sizeof(ambient_context.detail));
        for(int i=0;i<2;i++)if(ui.weather_tile[i])lv_obj_set_style_bg_image_src(ui.weather_tile[i],ambient_context.linked_weather?NULL:friday_weather_art(ui.weather_city[i],-1,-1),0);
        experience_layout();
    }
}

void friday_ui_create(void)
{
    lv_obj_t *screen=lv_screen_active();lv_obj_remove_style_all(screen);
    lv_obj_set_size(screen,720,720);lv_obj_set_style_bg_color(screen,color_hex(UI_CANVAS),0);
    lv_obj_set_style_bg_opa(screen,LV_OPA_COVER,0);lv_obj_remove_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
    ui.eyebrow=make_label(screen,"FRIDAY",&lv_font_montserrat_18,0xC9C6C1);
    lv_snprintf(ui.context_label,sizeof(ui.context_label),"FRIDAY");
    ui.title=make_label(screen,"A little more space\nfor your day.",&lv_font_montserrat_48,0xEEECE7);
    lv_label_set_long_mode(ui.title,LV_LABEL_LONG_MODE_DOTS);
    ui.presence=friday_presence_create(screen);lv_obj_add_flag(ui.presence,LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(ui.presence,presence_event_cb,LV_EVENT_ALL,NULL);
    ui.status=make_label(screen,"Here when you need me",&lv_font_montserrat_18,0xAAA8A5);
    lv_obj_align(ui.status,LV_ALIGN_TOP_LEFT,48,4);
    ui.recovery_notice_mask=-1;lv_obj_add_flag(ui.status,LV_OBJ_FLAG_HIDDEN);
#if CONFIG_FRIDAY_EXECUTION_STOP || defined(FRIDAY_NATIVE_EXECUTION_STOP)
    lv_obj_t **execution_widgets[]={&execution_stop_button,&execution_status_button};
    const char *execution_titles[]={"Stop task","Task status"};
    const char *execution_actions[]={"execution.stop","execution.status"};
    for(int i=0;i<2;i++){
        lv_obj_t *button=lv_button_create(screen);*execution_widgets[i]=button;
        lv_obj_set_pos(button,504,76+i*56);lv_obj_set_size(button,168,48);
        lv_obj_set_style_radius(button,18,0);lv_obj_set_style_bg_color(button,color_hex(UI_CONTROL),0);
        lv_obj_t *label=make_label(button,execution_titles[i],&lv_font_montserrat_18,UI_TEXT);lv_obj_center(label);
        if(!i)execution_stop_label=label;
        lv_obj_add_event_cb(button,execution_event,LV_EVENT_CLICKED,(void*)execution_actions[i]);lv_obj_add_flag(button,LV_OBJ_FLAG_HIDDEN);
    }
#endif
    ui.message=make_label(screen,"",&lv_font_montserrat_24,0xC9C6C1);
    lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_DOTS);
    ui.microphone=make_label(screen,"Microphone starting",&lv_font_montserrat_18,0x757B86);
    lv_obj_align(ui.microphone,LV_ALIGN_TOP_LEFT,48,676);
    ui.network=make_label(screen,"Connecting",&lv_font_montserrat_18,0x757B86);
    lv_obj_set_size(ui.network,360,28);lv_obj_set_style_text_align(ui.network,LV_TEXT_ALIGN_RIGHT,0);
    lv_label_set_long_mode(ui.network,LV_LABEL_LONG_MODE_DOTS);lv_obj_align(ui.network,LV_ALIGN_TOP_RIGHT,-48,40);
    ui.fact_panel=lv_obj_create(screen);lv_obj_remove_style_all(ui.fact_panel);
    lv_obj_set_size(ui.fact_panel,656,104);lv_obj_align(ui.fact_panel,LV_ALIGN_TOP_LEFT,32,138);
    lv_obj_set_style_bg_color(ui.fact_panel,color_hex(UI_PANEL),0);lv_obj_set_style_bg_opa(ui.fact_panel,LV_OPA_COVER,0);
    lv_obj_set_style_radius(ui.fact_panel,20,0);lv_obj_remove_flag(ui.fact_panel,LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(ui.fact_panel,agenda_empty_mark,LV_EVENT_DRAW_MAIN,NULL);
    lv_obj_move_to_index(ui.fact_panel,0);
    for(int i=0;i<4;i++) {
        ui.cards[i]=lv_obj_create(screen);lv_obj_remove_style_all(ui.cards[i]);
        lv_obj_set_style_bg_image_src(ui.cards[i],NULL,0);
        lv_obj_set_style_bg_color(ui.cards[i],color_hex(UI_CARD),0);lv_obj_set_style_bg_opa(ui.cards[i],LV_OPA_COVER,0);
        lv_obj_set_style_radius(ui.cards[i],20,0);lv_obj_remove_flag(ui.cards[i],LV_OBJ_FLAG_SCROLLABLE);
        ui.card_times[i]=make_label(ui.cards[i],"",&lv_font_montserrat_18,0x9399A5);
        lv_obj_set_size(ui.card_times[i],104,28);lv_obj_align(ui.card_times[i],LV_ALIGN_LEFT_MID,20,0);lv_label_set_long_mode(ui.card_times[i],LV_LABEL_LONG_MODE_DOTS);
        ui.card_labels[i]=make_label(ui.cards[i],"",&lv_font_montserrat_24,0xEEECE7);lv_label_set_long_mode(ui.card_labels[i],LV_LABEL_LONG_MODE_DOTS);
        lv_obj_set_style_bg_color(ui.cards[i],color_hex(0x263B50),LV_STATE_PRESSED);
        lv_obj_set_style_border_color(ui.cards[i],color_hex(0x8FE2C7),LV_STATE_PRESSED);
        lv_obj_add_event_cb(ui.cards[i],agenda_mark,LV_EVENT_DRAW_MAIN,NULL);
        if(!(i%2))lv_obj_add_event_cb(ui.cards[i],weather_city_art,LV_EVENT_DRAW_MAIN,(void *)(intptr_t)(i/2));
        lv_obj_add_event_cb(ui.cards[i],scene_action_event,LV_EVENT_CLICKED,ui.card_actions[i]);lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
    }
    for(int i=0;i<5;i++) {
        ui.buttons[i]=lv_button_create(screen);lv_obj_set_style_radius(ui.buttons[i],20,0);lv_obj_set_style_shadow_width(ui.buttons[i],0,0);
        lv_obj_set_style_border_width(ui.buttons[i],1,0);lv_obj_set_style_border_color(ui.buttons[i],color_hex(UI_EDGE),0);
        lv_obj_set_style_pad_all(ui.buttons[i],0,0);ui.button_labels[i]=make_label(ui.buttons[i],"",&lv_font_montserrat_24,0xEEECE7);
        ui.browse_icons[i]=lv_obj_create(ui.buttons[i]);lv_obj_remove_style_all(ui.browse_icons[i]);
        lv_obj_set_size(ui.browse_icons[i],44,44);lv_obj_align(ui.browse_icons[i],LV_ALIGN_TOP_LEFT,24,24);
        lv_obj_remove_flag(ui.browse_icons[i],LV_OBJ_FLAG_CLICKABLE);lv_obj_remove_flag(ui.browse_icons[i],LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_event_cb(ui.browse_icons[i],launcher_icon,LV_EVENT_DRAW_MAIN,ui.button_actions[i]);
        ui.browse_hints[i]=make_label(ui.buttons[i],"",&lv_font_montserrat_18,0xABB5BE);
        lv_obj_set_size(ui.browse_hints[i],252,26);lv_obj_align(ui.browse_hints[i],LV_ALIGN_TOP_LEFT,24,140);
        lv_label_set_long_mode(ui.browse_hints[i],LV_LABEL_LONG_MODE_DOTS);
        lv_obj_set_style_text_align(ui.button_labels[i],LV_TEXT_ALIGN_CENTER,0);lv_label_set_long_mode(ui.button_labels[i],LV_LABEL_LONG_MODE_DOTS);lv_obj_center(ui.button_labels[i]);
        lv_obj_set_style_bg_color(ui.buttons[i],color_hex(0x263B50),LV_STATE_PRESSED);
        lv_obj_set_style_border_color(ui.buttons[i],color_hex(0x8FE2C7),LV_STATE_PRESSED);
        lv_obj_add_event_cb(ui.buttons[i],scene_action_event,LV_EVENT_CLICKED,ui.button_actions[i]);lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
    }
    ui.cover=lv_obj_create(screen);lv_obj_remove_style_all(ui.cover);lv_obj_set_size(ui.cover,272,272);lv_obj_align(ui.cover,LV_ALIGN_TOP_LEFT,48,88);
    lv_obj_set_style_bg_color(ui.cover,color_hex(0x222730),0);lv_obj_set_style_bg_opa(ui.cover,LV_OPA_COVER,0);lv_obj_set_style_radius(ui.cover,24,0);lv_obj_set_style_clip_corner(ui.cover,true,0);
    lv_obj_remove_flag(ui.cover,LV_OBJ_FLAG_CLICKABLE);lv_obj_remove_flag(ui.cover,LV_OBJ_FLAG_SCROLLABLE);
    ui.cover_label=make_label(ui.cover,"",&lv_font_montserrat_18,0x929AA7);lv_obj_add_flag(ui.cover_label,LV_OBJ_FLAG_HIDDEN);lv_obj_set_width(ui.cover_label,220);lv_obj_set_style_text_align(ui.cover_label,LV_TEXT_ALIGN_CENTER,0);lv_obj_center(ui.cover_label);
    ui.cover_image=lv_image_create(ui.cover);lv_image_set_pivot(ui.cover_image,0,0);lv_image_set_scale(ui.cover_image,342);lv_image_set_antialias(ui.cover_image,true);lv_obj_align(ui.cover_image,LV_ALIGN_TOP_LEFT,8,8);
    ui.artist=make_label(screen,"",&lv_font_montserrat_24,0xB7B4AF);lv_obj_set_size(ui.artist,320,64);lv_obj_align(ui.artist,LV_ALIGN_TOP_LEFT,352,216);lv_label_set_long_mode(ui.artist,LV_LABEL_LONG_MODE_DOTS);
    ui.media_state=make_label(screen,"",&lv_font_montserrat_18,0x9BCAA8);lv_obj_set_size(ui.media_state,320,48);lv_obj_align(ui.media_state,LV_ALIGN_TOP_LEFT,352,296);lv_label_set_long_mode(ui.media_state,LV_LABEL_LONG_MODE_DOTS);
    ui.lyric_caption=make_label(screen,"CURRENT LINE",&lv_font_montserrat_18,0x757B86);lv_obj_set_style_text_letter_space(ui.lyric_caption,2,0);lv_obj_align(ui.lyric_caption,LV_ALIGN_TOP_LEFT,48,372);
    ui.progress=lv_bar_create(screen);lv_obj_set_size(ui.progress,624,4);lv_obj_align(ui.progress,LV_ALIGN_TOP_LEFT,48,534);
    lv_obj_set_style_bg_color(ui.progress,color_hex(UI_EDGE),LV_PART_MAIN);lv_obj_set_style_bg_color(ui.progress,color_hex(0xC9C6C1),LV_PART_INDICATOR);lv_obj_remove_flag(ui.progress,LV_OBJ_FLAG_CLICKABLE);
    ui.media_time=make_label(screen,"",&lv_font_montserrat_18,0x8D939E);lv_obj_set_size(ui.media_time,624,28);lv_obj_align(ui.media_time,LV_ALIGN_TOP_LEFT,48,548);lv_label_set_long_mode(ui.media_time,LV_LABEL_LONG_MODE_DOTS);
    for(int i=0;i<2;i++){ui.weather_details[i]=make_label(screen,"",&lv_font_montserrat_18,0x98A4B2);lv_obj_add_flag(ui.weather_details[i],LV_OBJ_FLAG_HIDDEN);}
    ui.attribution=make_label(screen,"",&lv_font_montserrat_18,0x757B86);lv_obj_set_size(ui.attribution,624,26);lv_obj_align(ui.attribution,LV_ALIGN_TOP_LEFT,48,694);lv_label_set_long_mode(ui.attribution,LV_LABEL_LONG_MODE_DOTS);
    ui.browse_date=make_label(screen,"",&lv_font_montserrat_24,0xC8CED9);
    lv_obj_set_size(ui.browse_date,624,32);lv_obj_align(ui.browse_date,LV_ALIGN_TOP_LEFT,48,154);
    ui.browse_meta=make_label(screen,"",&lv_font_montserrat_18,0x7F8B98);
    scene_layout(false);set_message_text("Say Friday or touch the light.\nHold for local views.");apply_state(FRIDAY_STATE_IDLE);
    lv_timer_create(diagnostic_tick,10000,NULL);
    lv_timer_create(recovery_notice_tick,100,NULL);
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
    if(!(owned_media_render&&ui.scene_active&&!strcmp(ui.scene_kind,"media")&&kind&&!strcmp(kind,"media"))&&(!kind||strcmp(kind,"metric")||!title||strcmp(title,"Weather")))ui.weather_focus=false;
    ui.weather_completion_pending=kind&&!strcmp(kind,"metric")&&title&&!strcmp(title,"Weather")&&
        (ui.tool_working||ui.tool_awaiting_result)&&!strcmp(ui.active_tool,"get_weather");
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
    for(int i=0;i<4;i++)lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);
    for(int i=0;i<5;i++)lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
    const char *known[] = {"ambient", "AMBIENT", "timeline", "message", "metric", "list", "reminder", "timer", "media", "ERROR", "PAIRING", "TOOL"};
    const char *safe_kind = "other";
    for (unsigned int i = 0; i < sizeof(known)/sizeof(known[0]); i++) if (kind != NULL && strcmp(kind, known[i]) == 0) safe_kind = known[i];
    bool media_continuation = owned_media_render && ui.scene_active &&
        strcmp(ui.scene_kind, "media") == 0 && strcmp(safe_kind, "media") == 0;
    lv_snprintf(ui.scene_kind, sizeof(ui.scene_kind), "%s", safe_kind);
    ui.scene_generation++;
    if(!media_continuation){ui.tool_working=false;ui.tool_awaiting_result=false;ui.active_tool[0]='\0';}
    if (!media_continuation)
        scene_layout(kind != NULL && strcmp(kind, "ambient") != 0 && strcmp(kind, "AMBIENT") != 0);
    if (kind != NULL && kind[0] != '\0') {
        context_label( strcmp(kind, "media") == 0 ? "Music" :
            strcmp(kind, "timeline") == 0 ? "Your day" :
            strcmp(kind, "metric") == 0 ? "Friday" :
            strcmp(kind, "message") == 0 ? "For your review" :
            strcmp(kind, "reminder") == 0 ? "A gentle nudge" :
            strcmp(kind, "timer") == 0 ? "Focus" :
            strcmp(kind, "PAIRING") == 0 ? "Connect Friday" :
            strcmp(kind, "ERROR") == 0 ? "A little help" : "Friday");
        lv_obj_align(ui.eyebrow, LV_ALIGN_TOP_LEFT, 48, 38);
    }
    if (title != NULL && title[0] != '\0') {
        const char *heading=title;
        char memory_title[80];
        if(kind&&!strcmp(kind,"reminder")&&!strncmp(title,"Reminder #",10))heading="Reminder";
        if(!strncmp(title,"Memory #",8)) {
            const char *page=strchr(title+8,' ');
            lv_snprintf(memory_title,sizeof(memory_title),"Memory%s",page?page:"");heading=memory_title;
        }
        if(!strcmp(title,"Mock mode"))heading="Friday";
        lv_snprintf(ui.experience_title,sizeof(ui.experience_title),"%s",heading);
        display_label(ui.title, heading);
        lv_obj_align(ui.title, LV_ALIGN_TOP_LEFT, 48, ui.scene_active ? 84 : 138);
    }
    if (subtitle != NULL && subtitle[0] != '\0' && (!owned_media_render || strcmp(subtitle,last_media_line) != 0)) {
        set_message_text(kind&&!strcmp(kind,"ERROR")?human_error(subtitle):subtitle);
    }
    experience_layout();
    bsp_display_unlock();
    return true;
}

bool friday_ui_set_response(const char *text)
{
    // Keep the director's cards and touch actions when assistant.text follows surface.
    if (ui.message != NULL && bsp_display_lock(1000)) {
        if(ui.linked_context&&!ui.linked_event_ref[0])text="Event details expired; Weather remains until dismissed.";
        /* A protected scene owns the complete fact being reviewed. Narration
         * can arrive later or be empty; it must never replace that fact. */
        if(ui.experience_preview&&ui.calendar_retained&&!ui.experience_protected&&(!ui.calendar_status||ui.calendar_reply)&&text&&text[0]) {
            ui.calendar_status=true;ui.calendar_reply=true;ui.hide_scene_summary=false;
            lv_obj_scroll_to_y(ui.status_update_panel,0,LV_ANIM_OFF);set_message_text(text);
        } else if(!ui.hide_scene_summary&&!(ui.experience_preview&&(ui.experience_protected||ui.calendar_status)))set_message_text(text);
        experience_layout();
        bsp_display_unlock();
        return true;
    }
    return false;
}

void friday_ui_set_tool(const char *name,const char *status)
{
    /* No request IDs: a same-name completion could belong to an older call,
     * even after cancellation and reentry. Never derive success, failure or
     * an idle cue from it. Await an actual result surface or navigation. */
    if(!status)return;
    bool working=!strcmp(status,"working");
    bool complete=!strcmp(status,"complete");
    bool failed=!strcmp(status,"failed");
    if(!working&&!complete&&!failed)return;
    if(!bsp_display_lock(1000))return;
    if(complete||failed) {
        if((!ui.tool_working&&!ui.tool_awaiting_result)||!name||strcmp(name,ui.active_tool)) {bsp_display_unlock();return;}
        ui.tool_working=false;ui.tool_awaiting_result=true;context_update();
        /* Keep facts, lyrics, actions and any newer loading title unchanged.
         * Backend-owned result/error surfaces provide actionable outcome copy. */
        bsp_display_unlock();return;
    }
    bool keep_context=((ui.tool_working||ui.tool_awaiting_result)&&name&&!strcmp(name,ui.active_tool))||
        (ui.scene_active&&ui.browse_mode!=1&&strcmp(ui.scene_kind,"TOOL")&&strcmp(ui.scene_kind,"ERROR"));
    if(keep_context) {
        ui.tool_working=true;ui.tool_awaiting_result=false;lv_snprintf(ui.active_tool,sizeof(ui.active_tool),"%s",name?name:"");
        context_update();bsp_display_unlock();return;
    }
    bsp_display_unlock();
    bool weather=name&&!strcmp(name,"get_weather");
    if(weather&&status&&!strcmp(status,"working")) {
        if(!friday_ui_set_surface("TOOL","Checking the forecast","Temperature and rain / snow outlook."))return;
        if(!bsp_display_lock(1000))return;
        context_label("Friday");lv_obj_set_style_text_letter_space(ui.eyebrow,0,0);
        lv_obj_set_style_text_font(ui.title,&lv_font_montserrat_48,0);
        lv_obj_set_size(ui.title,624,120);lv_obj_align(ui.title,LV_ALIGN_TOP_LEFT,48,88);
        lv_label_set_long_mode(ui.title,LV_LABEL_LONG_MODE_WRAP);
        lv_obj_add_flag(ui.fact_panel,LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_text_letter_space(ui.message,0,0);
        lv_obj_set_style_text_align(ui.message,LV_TEXT_ALIGN_LEFT,0);
        lv_obj_set_size(ui.message,624,76);lv_obj_align(ui.message,LV_ALIGN_TOP_LEFT,48,220);
        lv_label_set_long_mode(ui.message,LV_LABEL_LONG_MODE_WRAP);
        friday_presence_set_compact(ui.presence,false);
        lv_obj_set_size(ui.presence,624,160);lv_obj_align(ui.presence,LV_ALIGN_TOP_LEFT,48,320);
        friday_presence_set_state(ui.presence,FRIDAY_STATE_THINKING);
        ui.tool_working=true;ui.tool_awaiting_result=false;lv_snprintf(ui.active_tool,sizeof(ui.active_tool),"%s",name);
        context_update();bsp_display_unlock();return;
    }
    friday_ui_set_surface("TOOL","One moment","I'll take care of that.");
    if(bsp_display_lock(1000)) {
        ui.tool_working=true;ui.tool_awaiting_result=false;lv_snprintf(ui.active_tool,sizeof(ui.active_tool),"%s",name?name:"");
        context_update();bsp_display_unlock();
    }
}

void friday_ui_set_backend(const char *status)
{
    if(ui.network==NULL||!bsp_display_lock(1000))return;
    lv_obj_add_flag(ui.network,LV_OBJ_FLAG_HIDDEN);
    if(status&&(strstr(status,"VOICE UNAVAILABLE")||!strcmp(status,"SPEAKER ERROR"))) {
        ui.voice_notice=true;recovery_notice_update();
    } else if(status&&!strcmp(status,"FRIDAY ONLINE")) {
        ui.voice_notice=false;recovery_notice_update();
    }
    bsp_display_unlock();
}

void friday_ui_set_network(const char *status,const char *detail)
{
    if(ui.network==NULL||!bsp_display_lock(1000))return;
    lv_obj_add_flag(ui.network,LV_OBJ_FLAG_HIDDEN);
    /* Setup instructions are actionable; routine IP/SSID/credential state isn't. */
    if(status&&strstr(status,"WI-FI SETUP")&&detail&&detail[0]&&!ui.scene_active){set_message_text(detail);lv_obj_remove_flag(ui.message,LV_OBJ_FLAG_HIDDEN);}
    else if(status&&!strcmp(status,"WI-FI SAVED"))set_message_text("Joining your network…");
    bsp_display_unlock();
}

void friday_ui_set_microphone(const char *status, int32_t peak, int32_t rms)
{
    /* Capture runs every 20 ms. Meter/status refresh is best effort: never
     * wait behind a scene render while the I2S receive queue fills. */
    if(status&&(!strcmp(status,"MIC ERROR")||!strcmp(status,"MIC READ ERROR")))
        atomic_store_explicit(&microphone_unavailable,true,memory_order_relaxed);
    else if(status&&(!strcmp(status,"MICS ONLINE")||!strcmp(status,"Ready")||!strcmp(status,"MIC READY")||!strcmp(status,"MICS READY")))
        atomic_store_explicit(&microphone_unavailable,false,memory_order_relaxed);
    if (ui.microphone == NULL || !bsp_display_lock(0)) {
        return;
    }

    (void)peak;
    friday_presence_set_level(ui.presence,rms);
    lv_obj_add_flag(ui.microphone,LV_OBJ_FLAG_HIDDEN);
    recovery_notice_update();
    lv_obj_align(ui.microphone,LV_ALIGN_TOP_LEFT,48,676);
    bsp_display_unlock();
}

/* BSP display lock is recursive (esp_lvgl_adapter 0.6.3). It serializes touch,
 * expiry and the complete acceptance/render operation, closing in-flight races. */
void friday_ui_media_begin(const char *request)
{
    if (!bsp_display_lock(-1)) return;
    atomic_store(&interaction_notice_until,0);ui.recovery_notice_mask=-1;recovery_notice_update();
    if(overlay.active&&overlay.protected) {
        /* Stage fresh ownership without destroying the protected view. Only an
         * authenticated frame for this fresh request may replace it. */
        friday_media_begin(&media_guard,request);bsp_display_unlock();return;
    }
    last_media_line[0] = '\0';
    friday_media_overlay_clear(&overlay);overlay_action_pending=false;overlay_resolution_pending=false;overlay_memory_decision_pending=false;
    lv_obj_add_flag(ui.cover_image,LV_OBJ_FLAG_HIDDEN);lv_image_cache_drop(&ui.cover_dsc);friday_artwork_clear(&ui.artwork);
    friday_media_begin(&media_guard, request);
    bsp_display_unlock();
}
void friday_ui_media_send_failed(const char *request)
{
    if(!bsp_display_lock(-1))return;
    if(request&&!strcmp(media_guard.request,request)) {
        if(overlay.active&&overlay.protected) {
            lv_snprintf(media_guard.request,sizeof(media_guard.request),"%s",overlay.request);
            lv_snprintf(media_guard.session,sizeof(media_guard.session),"%s",overlay.session);
            media_guard.sequence=overlay.sequence;
        } else friday_ui_media_clear();
    }
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
        for(int i=0;i<5;i++)if(!strncmp(ui.button_actions[i],"musicctl:",9))lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
        lv_snprintf(ui.button_actions[0],sizeof(ui.button_actions[0]),"music");display_label(ui.button_labels[0],LV_SYMBOL_REFRESH);
        lv_obj_set_size(ui.buttons[0],208,72);lv_obj_align(ui.buttons[0],LV_ALIGN_TOP_LEFT,180,588);
        lv_obj_set_size(ui.button_labels[0],184,38);lv_obj_center(ui.button_labels[0]);lv_obj_set_style_text_font(ui.button_labels[0],&lv_font_montserrat_32,0);
        lv_obj_set_style_bg_color(ui.buttons[0],color_hex(UI_PRIMARY),0);lv_obj_set_style_text_color(ui.button_labels[0],color_hex(UI_PRIMARY_TEXT),0);
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
    for(int i=0;i<5;i++) {
        const char *a=scene_string(cJSON_GetArrayItem(actions,i),"id");
        if(!strncmp(a,"reqcancel:",10)||!strncmp(a,"memyes:",7)||!strncmp(a,"memno:",6)||!strncmp(a,"memreview:",10)||!strncmp(a,"confirm",7)||!strncmp(a,"decline",7))return true;
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
            for(int i=0;i<5;i++)if(!strncmp(ui.button_actions[i],"musicctl:",9))lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
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
    if(overlay.active&&action&&(!strncmp(action,"reqcancel:",10)||!strncmp(action,"memyes:",7)||!strncmp(action,"memno:",6)||!strncmp(action,"confirm",7)||!strncmp(action,"decline",7)||!strncmp(action,"complete:",9)||!strncmp(action,"snooze:",7)||!strncmp(action,"cancel:",7))){overlay_action_pending=true;overlay_memory_decision_pending=!strncmp(action,"memyes:",7)||!strncmp(action,"memno:",6);}
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
    bool accepted = (!overlay.active||overlay.ready||(overlay.protected&&strcmp(media_guard.request,overlay.request))) && cJSON_IsObject(surface) && strcmp(scene_string(surface,"kind"),"media") == 0 &&
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

bool friday_ui_experience_enabled(void){return ui.experience_preview;}
void friday_ui_experience_session(const char *epoch)
{
    if(!bsp_display_lock(1000))return;
    experience_transition_cancel();calendar_context_clear();
    if(ui.linked_context) {
        scene_layout(false);
        for(int i=0;i<4;i++){display_label(ui.card_labels[i],"");display_label(ui.card_times[i],"");lv_obj_add_flag(ui.cards[i],LV_OBJ_FLAG_HIDDEN);}
        for(int i=0;i<5;i++)lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);
        display_label(ui.experience_detail,"");lv_obj_add_flag(ui.experience_detail,LV_OBJ_FLAG_HIDDEN);
        lv_snprintf(ui.experience_title,sizeof(ui.experience_title),"Friday");display_label(ui.title,"Friday");set_message_text("Ready");experience_layout();
    }
    friday_experience_session(&experience_guard,ui.experience_preview?epoch:NULL);
    if(!epoch) {
        if(ui.context_weather_expiry){lv_timer_delete(ui.context_weather_expiry);ui.context_weather_expiry=NULL;}
        memset(&ambient_context,0,sizeof(ambient_context));ui.weather_focus=false;
        if(ui.weather_layer)lv_obj_add_flag(ui.weather_layer,LV_OBJ_FLAG_HIDDEN);
    }
    bsp_display_unlock();
}
void friday_ui_experience_request(const char *request){if(bsp_display_lock(-1)){experience_transition_cancel();friday_experience_begin(&experience_guard,request);bsp_display_unlock();}}
void friday_ui_experience_send_failed(const char *request){if(bsp_display_lock(-1)){friday_experience_send_failed(&experience_guard,request);bsp_display_unlock();}}
friday_experience_accept_t friday_ui_experience_packet(const cJSON *packet){if(!bsp_display_lock(-1))return FRIDAY_EXPERIENCE_REJECT;friday_experience_accept_t result=friday_experience_accept(&experience_guard,packet);bsp_display_unlock();return result;}
void friday_ui_experience_finished(void){if(bsp_display_lock(-1)){ui.tool_working=false;ui.tool_awaiting_result=false;ui.active_tool[0]=0;context_update();bsp_display_unlock();}}

static char accepted_results[64][33];
static unsigned accepted_result_index;
static friday_state_t receive_state(const char *name)
{
    if(!strcmp(name,"listening"))return FRIDAY_STATE_LISTENING;
    if(!strcmp(name,"thinking"))return FRIDAY_STATE_THINKING;
    if(!strcmp(name,"speaking"))return FRIDAY_STATE_SPEAKING;
    if(!strcmp(name,"alert"))return FRIDAY_STATE_ALERT;
    return FRIDAY_STATE_IDLE;
}
static bool receive_retains_context(const cJSON *packet,const cJSON *surface)
{
    return ui.experience_preview&&cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(packet,"retain_context"))&&
        ui.scene_active&&!ui.experience_protected&&!ui.experience_sensitive&&!(overlay.active&&overlay.protected)&&
        !strcmp(scene_string(surface,"kind"),"message")&&
        cJSON_GetArraySize(cJSON_GetObjectItemCaseSensitive(surface,"items"))==0&&
        cJSON_GetArraySize(cJSON_GetObjectItemCaseSensitive(surface,"actions"))==0;
}
static bool receive_reply(const cJSON *packet,const cJSON *surface,const char *text)
{
    if(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(packet,"retain_context"))&&ui.scene_active&&(ui.experience_protected||ui.experience_sensitive||(overlay.active&&overlay.protected)))return friday_ui_set_response(text);
    bool retain=receive_retains_context(packet,surface);
    if(!retain&&!friday_ui_set_scene(surface))return false;
    if(retain){ui.conversation_reply=true;ui.hide_scene_summary=false;if(ui.calendar_retained)ui.calendar_reply=true;lv_obj_scroll_to_y(ui.status_update_panel,0,LV_ANIM_OFF);}
    return friday_ui_set_response(text);
}
friday_ui_receive_result_t friday_ui_receive(const cJSON *packet,bool recording,bool speech_playing)
{
    friday_ui_receive_result_t out={0};
    if(!bsp_display_lock(-1))return out;
    const char *type=scene_string(packet,"type");
    bool result=!strcmp(type,"agent.result");
    out.handled=true;
    if(result) {
        const char *delivery=scene_string(packet,"delivery_id"),*job=scene_string(packet,"job_id"),*status=scene_string(packet,"status");
        if(strlen(delivery)!=32||strspn(delivery,"0123456789abcdef")!=32||!job[0]||strlen(job)>128||!cJSON_IsObject(cJSON_GetObjectItemCaseSensitive(packet,"surface"))||!cJSON_IsString(cJSON_GetObjectItemCaseSensitive(packet,"text"))||(strcmp(status,"completed")&&strcmp(status,"failed")&&strcmp(status,"expired")&&strcmp(status,"late")))goto done;
    }
    friday_experience_accept_t acceptance=friday_experience_accept(&experience_guard,packet);
    out.handled=true;out.accepted=acceptance==FRIDAY_EXPERIENCE_ACCEPT;
    if(result) {
        const char *delivery=scene_string(packet,"delivery_id"),*job=scene_string(packet,"job_id"),*status=scene_string(packet,"status");
        const cJSON *surface=cJSON_GetObjectItemCaseSensitive(packet,"surface"),*text=cJSON_GetObjectItemCaseSensitive(packet,"text");
        if(strlen(delivery)!=32||strspn(delivery,"0123456789abcdef")!=32||!job[0]||strlen(job)>128||!cJSON_IsObject(surface)||!cJSON_IsString(text)||(strcmp(status,"completed")&&strcmp(status,"failed")&&strcmp(status,"expired")&&strcmp(status,"late"))) {out.accepted=false;goto done;}
        if(acceptance==FRIDAY_EXPERIENCE_REJECT)goto done;
        bool duplicate=false;for(int i=0;i<64;i++)if(!strcmp(accepted_results[i],delivery))duplicate=true;
        if(out.accepted&&!duplicate) {
            friday_ui_media_prepare_overlay(packet);
            if(!friday_ui_media_suspended()&&!receive_retains_context(packet,surface))friday_ui_media_clear();
            if(!receive_reply(packet,surface,text->valuestring)) {out.accepted=false;goto done;}
            memcpy(accepted_results[accepted_result_index++%64],delivery,33);
            friday_ui_media_overlay_completed(packet);out.completed=true;
        }
        cJSON *ack=cJSON_CreateObject();
        if(ack) {cJSON_AddStringToObject(ack,"type","result.ack");cJSON_AddStringToObject(ack,"job_id",job);cJSON_AddStringToObject(ack,"delivery_id",delivery);out.ack=cJSON_PrintUnformatted(ack);cJSON_Delete(ack);}
        goto done;
    }
    if(!out.accepted)goto done;
    if(!strcmp(type,"experience.input_error")){queue_interaction_notice(false,false,false);atomic_store(&invalid_input_notice,true);ui.recovery_notice_mask=-1;recovery_notice_update();goto done;}
    if(!strcmp(type,"experience.rejected")) {queue_interaction_notice(false,false,false);atomic_store(&rejected_input_notice,true);ui.recovery_notice_mask=-1;recovery_notice_update();goto done;}
    if(!strcmp(type,"experience.recovery.available")) {
        ambient_context.review_pending=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(packet,"pending"));experience_layout();goto done;
    }
    if(friday_ui_handle_notice(packet))goto done;
    if(!strcmp(type,"ready")) {
        friday_ui_experience_session(scene_string(packet,"experience_epoch"));ambient_context.recent_view=0;
        ambient_context.review_pending=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(packet,"review_pending"));
        friday_ui_set_backend("FRIDAY ONLINE");friday_ui_set_state(receive_state(scene_string(packet,"state")));
        const cJSON *surface=cJSON_GetObjectItemCaseSensitive(packet,"surface");if(cJSON_IsObject(surface))friday_ui_set_scene(surface);
        goto done;
    }
    if(!strncmp(type,"identity.",9)) {if(ui.linked_context)friday_ui_experience_session(NULL);memset(&ambient_context,0,sizeof(ambient_context));experience_transition_cancel();out.handled=false;goto done;}
    if(!strcmp(type,"media.resume.result")) {friday_ui_media_resume_result(packet);goto done;}
    if(!strcmp(type,"experience.finished")) {friday_ui_experience_finished();goto done;}
    if(!strcmp(type,"surface")) {
        if(cJSON_HasObjectItem(packet,"media_request_id")||cJSON_HasObjectItem(packet,"media_session_id")||cJSON_HasObjectItem(packet,"media_sequence"))out.accepted=friday_ui_set_media(packet);
        else {friday_ui_media_prepare_overlay(packet);const cJSON *surface=cJSON_GetObjectItemCaseSensitive(packet,"surface");out.accepted=cJSON_IsObject(surface)&&(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(packet,"retain_context"))?receive_reply(packet,surface,scene_string(surface,"subtitle")):friday_ui_set_scene(surface));if(out.accepted)friday_ui_media_overlay_completed(packet);}
        out.completed=out.accepted;goto done;
    }
    if(!strcmp(type,"reminder.alert")) {
        friday_ui_media_prepare_overlay(packet);const cJSON *surface=cJSON_GetObjectItemCaseSensitive(packet,"surface");
        if(cJSON_IsObject(surface)){friday_ui_set_scene(surface);}
        friday_ui_set_response(scene_string(packet,"text"));friday_ui_set_state(FRIDAY_STATE_ALERT);goto done;
    }
    if(!strcmp(type,"state")) {
        friday_ui_media_prepare_overlay(packet);friday_state_t state=receive_state(scene_string(packet,"state"));
        if(!recording&&(state!=FRIDAY_STATE_SPEAKING||speech_playing)){friday_ui_set_state(state);}
        goto done;
    }
    if(!strcmp(type,"tool.activity")) {friday_ui_media_prepare_overlay(packet);friday_ui_set_tool(scene_string(packet,"name"),scene_string(packet,"status"));goto done;}
    if(!strcmp(type,"assistant.text")) {friday_ui_set_response(scene_string(packet,"text"));out.completed=true;goto done;}
    if(!strcmp(type,"error")) {
        const char *code=scene_string(packet,"code");
        if(!strcmp(code,"pairing_required")) {out.handled=false;goto done;}
        if(!strcmp(code,"speech_unavailable")) {friday_ui_set_backend("VOICE UNAVAILABLE — TEXT READY");friday_ui_set_state(FRIDAY_STATE_IDLE);goto done;}
        bool weather_error=!strcmp(ui.active_tool,"get_weather");friday_ui_experience_finished();
        if(ui.experience_preview&&ui.scene_active) {
            /* Keep current facts; recovery owns only the dock, never protected consent. */
            queue_interaction_notice(false,true,weather_error);
            if(!ui.experience_protected) {
                cJSON *recovery=friday_error_scene(code);const cJSON *actions=cJSON_GetObjectItemCaseSensitive(recovery,"actions");
                if(cJSON_IsArray(actions))for(int i=0;i<5;i++) {
                    const cJSON *a=cJSON_GetArrayItem(actions,i);if(!a){lv_obj_add_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);continue;}
                    lv_snprintf(ui.button_actions[i],sizeof(ui.button_actions[i]),"%s",scene_string(a,"id"));display_label(ui.button_labels[i],scene_string(a,"label"));lv_obj_remove_flag(ui.buttons[i],LV_OBJ_FLAG_HIDDEN);lv_obj_remove_state(ui.buttons[i],LV_STATE_DISABLED);
                }
                cJSON_Delete(recovery);experience_layout();
            }
        } else {cJSON *recovery=friday_error_scene(code);if(recovery){friday_ui_set_scene(recovery);cJSON_Delete(recovery);}else friday_ui_set_surface("ERROR","Friday",scene_string(packet,"message"));}
        out.completed=true;goto done;
    }
    out.handled=false;
done:
    bsp_display_unlock();return out;
}
