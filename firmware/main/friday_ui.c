#include "friday_ui.h"

#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "lvgl.h"

#include "friday_state.h"

static const char *TAG = "friday_ui";

#define FRIDAY_STATUS_BOTTOM_OFFSET -166
#define FRIDAY_MESSAGE_TOP 570
#define FRIDAY_MESSAGE_WIDTH 620
#define FRIDAY_MESSAGE_HEIGHT 96

typedef struct {
    lv_obj_t *orb;
    lv_obj_t *core;
    lv_obj_t *eyebrow;
    lv_obj_t *title;
    lv_obj_t *status;
    lv_obj_t *message;
    lv_obj_t *network;
    lv_obj_t *microphone;
    lv_anim_t pulse;
} friday_ui_t;

static friday_ui_t ui;
static friday_ui_activation_callback_t activation_callback;

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
    lv_obj_set_size(ui.message, FRIDAY_MESSAGE_WIDTH, FRIDAY_MESSAGE_HEIGHT);
    lv_obj_align(ui.message, LV_ALIGN_TOP_MID, 0, FRIDAY_MESSAGE_TOP);
}

static void set_message_text(const char *text)
{
    if (text == NULL) {
        lv_label_set_text(ui.message, "");
        position_message();
        return;
    }

    size_t length = strlen(text);
    char *normalized = malloc(length + 1);
    if (normalized == NULL) {
        lv_label_set_text(ui.message, text);
        position_message();
        return;
    }

    size_t input = 0;
    size_t output = 0;
    while (input < length) {
        const unsigned char *current = (const unsigned char *)&text[input];
        if (input + 2 < length && current[0] == 0xE2 && current[1] == 0x80 &&
            (current[2] == 0x98 || current[2] == 0x99)) {
            normalized[output++] = '\'';
            input += 3;
        } else {
            normalized[output++] = text[input++];
        }
    }
    normalized[output] = '\0';

    lv_label_set_text(ui.message, normalized);
    free(normalized);
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
    lv_label_set_text(ui.status, friday_state_name(state));
    lv_obj_align(ui.status, LV_ALIGN_BOTTOM_MID, 0, FRIDAY_STATUS_BOTTOM_OFFSET);
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
    lv_anim_set_values(&ui.pulse, pulse_min, pulse_max);
    lv_anim_set_duration(&ui.pulse, duration);
    lv_anim_set_playback_duration(&ui.pulse, duration);
    lv_anim_set_repeat_count(&ui.pulse, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&ui.pulse, lv_anim_path_ease_in_out);
    lv_anim_start(&ui.pulse);

    ESP_LOGI(TAG, "State changed to %s", friday_state_name(state));
}

static void orb_event_cb(lv_event_t *event)
{
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) {
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
    lv_label_set_text(label, text);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, color_hex(color), 0);
    lv_obj_set_style_text_letter_space(label, 2, 0);
    return label;
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
    lv_obj_add_event_cb(ui.orb, orb_event_cb, LV_EVENT_CLICKED, NULL);

    lv_obj_t *inner_ring = lv_obj_create(ui.orb);
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
    lv_obj_align(ui.status, LV_ALIGN_BOTTOM_MID, 0, FRIDAY_STATUS_BOTTOM_OFFSET);

    ui.message = make_label(screen, "", &lv_font_montserrat_24, 0xEDF8F7);
    lv_obj_set_style_text_align(ui.message, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(ui.message, LV_LABEL_LONG_MODE_DOTS);
    position_message();

    ui.microphone = make_label(screen, "MICS STARTING", &lv_font_montserrat_18, 0x36504F);
    lv_obj_align(ui.microphone, LV_ALIGN_BOTTOM_MID, 0, -20);

    ui.network = make_label(screen, "WI-FI STARTING", &lv_font_montserrat_18, 0x36504F);
    lv_obj_align(ui.network, LV_ALIGN_TOP_MID, 0, 24);

    set_message_text("Tap the orb to talk to Friday");
    apply_state(FRIDAY_STATE_IDLE);
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
    bsp_display_unlock();
}

void friday_ui_set_surface(const char *kind, const char *title, const char *subtitle)
{
    if (ui.title == NULL || !bsp_display_lock(1000)) {
        return;
    }
    if (kind != NULL && kind[0] != '\0') {
        lv_label_set_text(ui.eyebrow, kind);
        lv_obj_align(ui.eyebrow, LV_ALIGN_TOP_MID, 0, 54);
    }
    if (title != NULL && title[0] != '\0') {
        lv_label_set_text(ui.title, title);
        lv_obj_align(ui.title, LV_ALIGN_TOP_MID, 0, 88);
    }
    if (subtitle != NULL && subtitle[0] != '\0') {
        set_message_text(subtitle);
    }
    bsp_display_unlock();
}

void friday_ui_set_response(const char *text)
{
    friday_ui_set_surface("RESPONSE", "FRIDAY", text);
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
    lv_label_set_text(ui.network, status != NULL ? status : "FRIDAY OFFLINE");
    lv_obj_align(ui.network, LV_ALIGN_TOP_MID, 0, 24);
    bsp_display_unlock();
}

void friday_ui_set_network(const char *status, const char *detail)
{
    if (ui.network == NULL || !bsp_display_lock(1000)) {
        return;
    }

    lv_label_set_text(ui.network, status);
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
    lv_label_set_text(ui.microphone, text);
    lv_obj_align(ui.microphone, LV_ALIGN_BOTTOM_MID, 0, -20);
    bsp_display_unlock();
}
