#include "friday_ui.h"

#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "lvgl.h"

#include "friday_state.h"
#include "friday_media_guard.h"
#include "friday_display_text.h"

static const char *TAG = "friday_ui";
static friday_media_guard_t media_guard;
static bool owned_media_render;
static char last_media_line[2001];

#define FRIDAY_STATUS_BOTTOM_OFFSET -166
#define FRIDAY_MESSAGE_TOP 570
#define FRIDAY_MESSAGE_WIDTH 620
#define FRIDAY_MESSAGE_HEIGHT 96

typedef struct {
    lv_obj_t *orb;
    lv_obj_t *core;
    lv_obj_t *inner_ring;
    lv_obj_t *eyebrow;
    lv_obj_t *title;
    lv_obj_t *status;
    lv_obj_t *message;
    lv_obj_t *network;
    lv_obj_t *microphone;
    lv_anim_t pulse;
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

static void set_circle_size(void *object, int32_t size)
{
    lv_obj_set_size((lv_obj_t *)object, size, size);
}

static void position_message(void)
{
    lv_obj_set_size(ui.message, FRIDAY_MESSAGE_WIDTH, ui.scene_active ? 76 : FRIDAY_MESSAGE_HEIGHT);
    lv_obj_align(ui.message, LV_ALIGN_TOP_MID, 0, ui.scene_active ? 118 : FRIDAY_MESSAGE_TOP);
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

static void set_message_text(const char *text)
{
    display_label(ui.message, text);
    position_message();
}

static void apply_state(friday_state_t state)
{
    uint32_t core_color = 0x79F5DD;
    uint32_t glow_color = 0x197D7B;
    int32_t pulse_min = 100;
    int32_t pulse_max = 126;
    uint32_t duration = 1500;
    const char *message = NULL;

    switch (state) {
    case FRIDAY_STATE_LISTENING:
        core_color = 0x63A8FF;
        glow_color = 0x285E9A;
        pulse_min = 104;
        pulse_max = 142;
        duration = 650;
        message = "Listening through the onboard microphone array";
        break;
    case FRIDAY_STATE_THINKING:
        core_color = 0xB595FF;
        glow_color = 0x59428C;
        pulse_min = 94;
        pulse_max = 132;
        duration = 900;
        message = "Thinking mode - glorified pondering, now in color";
        break;
    case FRIDAY_STATE_SPEAKING:
        core_color = 0x79F5DD;
        glow_color = 0x2CA491;
        pulse_min = 108;
        pulse_max = 140;
        duration = 320;
        break;
    case FRIDAY_STATE_ALERT:
        core_color = 0xFFCC75;
        glow_color = 0x9A6628;
        pulse_min = 106;
        pulse_max = 146;
        duration = 480;
        message = "Alert mode - tastefully alarming";
        break;
    case FRIDAY_STATE_IDLE:
    default:
        break;
    }

    friday_state_set(state);
    display_label(ui.status, friday_state_name(state));
    lv_obj_align(ui.status, LV_ALIGN_BOTTOM_MID, 0, ui.scene_active ? -8 : FRIDAY_STATUS_BOTTOM_OFFSET);
    if (message != NULL) {
        set_message_text(message);
    }
    lv_obj_set_style_bg_color(ui.core, color_hex(core_color), 0);
    lv_obj_set_style_shadow_color(ui.core, color_hex(glow_color), 0);
    lv_obj_set_style_border_color(ui.orb, color_hex(core_color), 0);

    lv_anim_delete(ui.core, set_circle_size);
    lv_anim_init(&ui.pulse);
    lv_anim_set_var(&ui.pulse, ui.core);
    lv_anim_set_exec_cb(&ui.pulse, set_circle_size);
    lv_anim_set_values(&ui.pulse, ui.scene_active ? 32 : pulse_min, ui.scene_active ? 44 : pulse_max);
    lv_anim_set_duration(&ui.pulse, duration);
    lv_anim_set_playback_duration(&ui.pulse, duration);
    lv_anim_set_repeat_count(&ui.pulse, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&ui.pulse, lv_anim_path_ease_in_out);
    lv_anim_start(&ui.pulse);

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

static void orb_event_cb(lv_event_t *event)
{
    if (lv_event_get_code(event) == LV_EVENT_LONG_PRESSED) {
        ESP_LOGI(TAG, "UI_ACTION orb=long_press");
        ui.long_pressed = true;
        if (action_callback != NULL && !action_callback("status")) {
            set_message_text("Friday is offline. Try again after reconnecting.");
        }
        return;
    }
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) {
        return;
    }

    ESP_LOGI(TAG, "UI_ACTION orb=click suppressed=%d", ui.long_pressed);
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
    lv_obj_set_style_text_letter_space(label, 2, 0);
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
    ui.scene_active = active;
    lv_obj_set_size(ui.inner_ring, active ? 64 : 196, active ? 64 : 196);
    lv_obj_center(ui.inner_ring);
    lv_obj_set_size(ui.orb, active ? 84 : 260, active ? 84 : 260);
    lv_obj_align(ui.orb, active ? LV_ALIGN_TOP_RIGHT : LV_ALIGN_CENTER,
                 active ? -22 : 0, active ? 46 : 4);
    lv_obj_align(ui.title, LV_ALIGN_TOP_MID, active ? -32 : 0, 88);
    position_message();
    if (active) {
        lv_obj_add_flag(ui.microphone, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_remove_flag(ui.microphone, LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_align(ui.status, LV_ALIGN_BOTTOM_MID, 0, active ? -8 : FRIDAY_STATUS_BOTTOM_OFFSET);
    lv_anim_set_values(&ui.pulse, active ? 32 : 100, active ? 44 : 126);
    lv_anim_delete(ui.core, set_circle_size);
    lv_anim_start(&ui.pulse);
}

static void scene_expire(lv_timer_t *timer)
{
    friday_media_clear(&media_guard);
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
    display_label(ui.eyebrow, "AMBIENT");
    display_label(ui.title, "FRIDAY");
    set_message_text("Say Friday or tap. Hold the orb for local views.");
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
            lv_snprintf(text, sizeof(text), "%s  %s", time, label);
            lv_obj_set_style_text_font(ui.card_labels[i], &lv_font_montserrat_24, 0);
            display_label(ui.card_labels[i], text);
            lv_snprintf(ui.card_actions[i], sizeof(ui.card_actions[i]), "%s", scene_string(item, "action"));
            lv_obj_remove_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        }
        if (cJSON_IsObject(action)) {
            display_label(ui.button_labels[i], scene_string(action, "label"));
            lv_snprintf(ui.button_actions[i], sizeof(ui.button_actions[i]), "%s", scene_string(action, "id"));
            lv_obj_remove_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
        }
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
        && strcmp(scene_string(surface, "kind"), "reminder") != 0) {
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
    lv_obj_t *screen = lv_screen_active();
    lv_obj_remove_style_all(screen);
    lv_obj_set_size(screen, 720, 720);
    lv_obj_set_style_bg_color(screen, color_hex(0x05090B), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_clear_flag(screen, LV_OBJ_FLAG_SCROLLABLE);

    ui.eyebrow = make_label(screen, "AMBIENT", &lv_font_montserrat_18, 0x7D9696);
    lv_obj_align(ui.eyebrow, LV_ALIGN_TOP_MID, 0, 54);

    ui.title = make_label(screen, "FRIDAY", &lv_font_montserrat_32, 0xEDF8F7);
    lv_obj_set_width(ui.title, 510);
    lv_obj_set_style_text_align(ui.title, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(ui.title, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_align(ui.title, LV_ALIGN_TOP_MID, 0, 88);

    ui.orb = lv_obj_create(screen);
    lv_obj_remove_style_all(ui.orb);
    lv_obj_set_size(ui.orb, 260, 260);
    lv_obj_set_style_radius(ui.orb, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(ui.orb, 2, 0);
    lv_obj_set_style_border_color(ui.orb, color_hex(0x79F5DD), 0);
    lv_obj_set_style_border_opa(ui.orb, LV_OPA_30, 0);
    lv_obj_set_style_bg_opa(ui.orb, LV_OPA_TRANSP, 0);
    lv_obj_align(ui.orb, LV_ALIGN_CENTER, 0, 4);
    lv_obj_add_flag(ui.orb, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(ui.orb, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(ui.orb, orb_event_cb, LV_EVENT_ALL, NULL);

    lv_obj_t *inner_ring = lv_obj_create(ui.orb);
    ui.inner_ring = inner_ring;
    lv_obj_remove_style_all(inner_ring);
    lv_obj_set_size(inner_ring, 196, 196);
    lv_obj_set_style_radius(inner_ring, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(inner_ring, 2, 0);
    lv_obj_set_style_border_color(inner_ring, color_hex(0x79F5DD), 0);
    lv_obj_set_style_border_opa(inner_ring, LV_OPA_20, 0);
    lv_obj_center(inner_ring);
    lv_obj_remove_flag(inner_ring, LV_OBJ_FLAG_CLICKABLE);

    ui.core = lv_obj_create(ui.orb);
    lv_obj_remove_style_all(ui.core);
    lv_obj_set_style_radius(ui.core, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(ui.core, LV_OPA_COVER, 0);
    lv_obj_set_style_shadow_width(ui.core, 70, 0);
    lv_obj_set_style_shadow_spread(ui.core, 14, 0);
    lv_obj_set_style_shadow_opa(ui.core, LV_OPA_40, 0);
    lv_obj_center(ui.core);
    lv_obj_remove_flag(ui.core, LV_OBJ_FLAG_CLICKABLE);

    ui.status = make_label(screen, "idle", &lv_font_montserrat_18, 0x79F5DD);
    lv_obj_set_style_text_letter_space(ui.status, 6, 0);
    lv_obj_align(ui.status, LV_ALIGN_BOTTOM_MID, 0, ui.scene_active ? -8 : FRIDAY_STATUS_BOTTOM_OFFSET);

    ui.message = make_label(screen, "", &lv_font_montserrat_24, 0xEDF8F7);
    lv_obj_set_style_text_align(ui.message, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(ui.message, LV_LABEL_LONG_MODE_DOTS);
    position_message();

    ui.microphone = make_label(screen, "MICS STARTING", &lv_font_montserrat_18, 0x36504F);
    lv_obj_align(ui.microphone, LV_ALIGN_BOTTOM_MID, 0, -20);

    ui.network = make_label(screen, "WI-FI STARTING", &lv_font_montserrat_18, 0x36504F);
    lv_obj_align(ui.network, LV_ALIGN_TOP_MID, 0, 24);

    for (int i = 0; i < 4; i++) {
        ui.cards[i] = lv_obj_create(screen);
        lv_obj_set_size(ui.cards[i], 620, 70);
        lv_obj_align(ui.cards[i], LV_ALIGN_TOP_MID, 0, 206 + i * 80);
        lv_obj_set_style_bg_color(ui.cards[i], color_hex(0x102124), 0);
        lv_obj_set_style_border_width(ui.cards[i], 0, 0);
        lv_obj_set_style_radius(ui.cards[i], 14, 0);
        lv_obj_remove_flag(ui.cards[i], LV_OBJ_FLAG_SCROLLABLE);
        ui.card_labels[i] = make_label(ui.cards[i], "", &lv_font_montserrat_24, 0xEDF8F7);
        lv_obj_set_size(ui.card_labels[i], 582, 38);
        lv_label_set_long_mode(ui.card_labels[i], LV_LABEL_LONG_MODE_DOTS);
        lv_obj_center(ui.card_labels[i]);
        lv_obj_add_event_cb(ui.cards[i], scene_action_event, LV_EVENT_CLICKED, ui.card_actions[i]);
        lv_obj_add_flag(ui.cards[i], LV_OBJ_FLAG_HIDDEN);
        ui.buttons[i] = lv_button_create(screen);
        lv_obj_set_size(ui.buttons[i], 294, 58);
        lv_obj_align(ui.buttons[i], LV_ALIGN_TOP_LEFT, 50 + (i % 2) * 326, 544 + (i / 2) * 70);
        lv_obj_set_style_bg_color(ui.buttons[i], color_hex(0x193A3A), 0);
        ui.button_labels[i] = make_label(ui.buttons[i], "", &lv_font_montserrat_24, 0x79F5DD);
        lv_obj_center(ui.button_labels[i]);
        lv_obj_add_event_cb(ui.buttons[i], scene_action_event, LV_EVENT_CLICKED, ui.button_actions[i]);
        lv_obj_add_flag(ui.buttons[i], LV_OBJ_FLAG_HIDDEN);
    }
    set_message_text("Tap to talk. Hold the orb for local views.");
    apply_state(FRIDAY_STATE_IDLE);
    lv_timer_create(diagnostic_tick, 10000, NULL);
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
    if (!owned_media_render) { friday_media_clear(&media_guard); lv_label_set_long_mode(ui.message, LV_LABEL_LONG_MODE_DOTS); }
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
    lv_snprintf(ui.scene_kind, sizeof(ui.scene_kind), "%s", safe_kind);
    ui.scene_generation++;
    scene_layout(kind != NULL && strcmp(kind, "ambient") != 0 && strcmp(kind, "AMBIENT") != 0);
    if (kind != NULL && kind[0] != '\0') {
        display_label(ui.eyebrow, kind);
        lv_obj_align(ui.eyebrow, LV_ALIGN_TOP_MID, 0, 54);
    }
    if (title != NULL && title[0] != '\0') {
        display_label(ui.title, title);
        lv_obj_align(ui.title, LV_ALIGN_TOP_MID, ui.scene_active ? -32 : 0, 88);
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
    friday_ui_set_surface("TOOL", "FRIDAY", detail);
}

void friday_ui_set_backend(const char *status)
{
    if (ui.network == NULL || !bsp_display_lock(1000)) {
        return;
    }
    display_label(ui.network, status != NULL ? status : "FRIDAY OFFLINE");
    lv_obj_align(ui.network, LV_ALIGN_TOP_MID, 0, 24);
    bsp_display_unlock();
}

void friday_ui_set_network(const char *status, const char *detail)
{
    if (ui.network == NULL || !bsp_display_lock(1000)) {
        return;
    }

    display_label(ui.network, status);
    lv_obj_align(ui.network, LV_ALIGN_TOP_MID, 0, 24);
    if (detail != NULL && detail[0] != '\0') {
        set_message_text(detail);
    }
    bsp_display_unlock();
}

void friday_ui_set_microphone(const char *status, int32_t peak, int32_t rms)
{
    if (ui.microphone == NULL || !bsp_display_lock(1000)) {
        return;
    }

    char text[80];
    if (peak > 0 || rms > 0) {
        lv_snprintf(text, sizeof(text), "%s  PEAK %ld  RMS %ld",
                    status, (long)peak, (long)rms);
    } else {
        lv_snprintf(text, sizeof(text), "%s", status);
    }
    display_label(ui.microphone, text);
    lv_obj_align(ui.microphone, LV_ALIGN_BOTTOM_MID, 0, -20);
    bsp_display_unlock();
}

/* BSP display lock is recursive (esp_lvgl_adapter 0.6.3). It serializes touch,
 * expiry and the complete acceptance/render operation, closing in-flight races. */
void friday_ui_media_begin(const char *request)
{
    if (!bsp_display_lock(-1)) return;
    last_media_line[0] = '\0';
    friday_media_begin(&media_guard, request);
    bsp_display_unlock();
}
void friday_ui_media_clear(void)
{
    if (!bsp_display_lock(-1)) return;
    friday_media_clear(&media_guard);
    bsp_display_unlock();
}
bool friday_ui_set_media(const cJSON *root)
{
    if (!bsp_display_lock(1000)) return false;
    const cJSON *request = cJSON_GetObjectItemCaseSensitive(root,"media_request_id");
    const cJSON *session = cJSON_GetObjectItemCaseSensitive(root,"media_session_id");
    const cJSON *sequence = cJSON_GetObjectItemCaseSensitive(root,"media_sequence");
    const cJSON *surface = cJSON_GetObjectItemCaseSensitive(root,"surface");
    bool accepted = cJSON_IsObject(surface) && strcmp(scene_string(surface,"kind"),"media") == 0 &&
        cJSON_IsString(request) && cJSON_IsString(session) && cJSON_IsNumber(sequence) &&
        friday_media_accept(&media_guard,request->valuestring,session->valuestring,sequence->valuedouble);
    if (accepted) {
        owned_media_render = true;
        accepted = friday_ui_set_scene(surface);
        owned_media_render = false;
        if (accepted && strcmp(scene_string(surface,"subtitle"),last_media_line) != 0) {
            lv_snprintf(last_media_line,sizeof(last_media_line),"%s",scene_string(surface,"subtitle"));
            /* A long current line stays readable, without truncating its text. */
            lv_label_set_long_mode(ui.message, LV_LABEL_LONG_SCROLL_CIRCULAR);
            lv_obj_set_style_anim_duration(ui.message, 10000, 0);
        }
    }
    bsp_display_unlock();
    return accepted;
}
