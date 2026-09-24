#include "friday_ui.h"

#include "esp_log.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "lvgl.h"

#include "friday_state.h"

static const char *TAG = "friday_ui";

typedef struct {
    lv_obj_t *orb;
    lv_obj_t *core;
    lv_obj_t *status;
    lv_obj_t *message;
    lv_obj_t *network;
    lv_obj_t *microphone;
    lv_anim_t pulse;
} friday_ui_t;

static friday_ui_t ui;

static lv_color_t color_hex(uint32_t value)
{
    return lv_color_hex(value);
}

static void set_circle_size(void *object, int32_t size)
{
    lv_obj_set_size((lv_obj_t *)object, size, size);
}

static void apply_state(friday_state_t state)
{
    uint32_t core_color = 0x79F5DD;
    uint32_t glow_color = 0x197D7B;
    int32_t pulse_min = 100;
    int32_t pulse_max = 126;
    uint32_t duration = 1500;
    const char *message = "Touch the orb to test the interface";

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
        message = "Speaking mode - awaiting the external speaker";
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
    lv_label_set_text(ui.message, message);
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

    friday_state_t next = (friday_state_get() + 1) % (FRIDAY_STATE_ALERT + 1);
    apply_state(next);
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

    lv_obj_t *eyebrow = make_label(screen, "PERSONAL INTELLIGENCE", &lv_font_montserrat_18,
                                   0x7D9696);
    lv_obj_align(eyebrow, LV_ALIGN_TOP_MID, 0, 54);

    lv_obj_t *title = make_label(screen, "FRIDAY", &lv_font_montserrat_32, 0xEDF8F7);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 88);

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
    lv_obj_align(ui.status, LV_ALIGN_BOTTOM_MID, 0, -116);

    ui.message = make_label(screen, "", &lv_font_montserrat_18, 0x7D9696);
    lv_obj_set_width(ui.message, 600);
    lv_obj_set_style_text_align(ui.message, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(ui.message, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_align(ui.message, LV_ALIGN_BOTTOM_MID, 0, -62);

    ui.microphone = make_label(screen, "MICS STARTING", &lv_font_montserrat_18, 0x36504F);
    lv_obj_align(ui.microphone, LV_ALIGN_BOTTOM_MID, 0, -20);

    ui.network = make_label(screen, "WI-FI STARTING", &lv_font_montserrat_18, 0x36504F);
    lv_obj_align(ui.network, LV_ALIGN_TOP_MID, 0, 24);

    apply_state(FRIDAY_STATE_IDLE);
}

void friday_ui_set_network(const char *status, const char *detail)
{
    if (ui.network == NULL || !bsp_display_lock(1000)) {
        return;
    }

    lv_label_set_text(ui.network, status);
    if (detail != NULL && detail[0] != '\0') {
        lv_label_set_text(ui.message, detail);
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
    bsp_display_unlock();
}
