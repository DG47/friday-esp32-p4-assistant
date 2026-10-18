#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "friday_state.h"
#include "cJSON.h"

typedef enum {
    FRIDAY_UI_ACTIVATION_IDLE = 0,
    FRIDAY_UI_ACTIVATION_RECORDING = 1,
    FRIDAY_UI_ACTIVATION_ACKNOWLEDGED = 2,
    FRIDAY_UI_ACTIVATION_UNCHANGED = 3,
} friday_ui_activation_result_t;

typedef friday_ui_activation_result_t (*friday_ui_activation_callback_t)(void);
typedef bool (*friday_ui_action_callback_t)(const char *action);

void friday_ui_create(void);
/* Authenticated recovery notice; preserves the current scene and activity. */
bool friday_ui_handle_notice(const cJSON *packet);
void friday_ui_set_activation_callback(friday_ui_activation_callback_t callback);
void friday_ui_set_action_callback(friday_ui_action_callback_t callback);
bool friday_ui_set_scene(const cJSON *surface);
void friday_ui_set_state(friday_state_t state);
bool friday_ui_set_surface(const char *kind, const char *title, const char *subtitle);
bool friday_ui_set_response(const char *text);
void friday_ui_set_tool(const char *name, const char *status);
void friday_ui_set_backend(const char *status);
void friday_ui_set_network(const char *status, const char *detail);
void friday_ui_set_microphone(const char *status, int32_t peak, int32_t rms);

void friday_ui_media_begin(const char *request);
/* Roll back only the matching staged send; leave a newer owner intact. */
void friday_ui_media_send_failed(const char *request);
void friday_ui_media_clear(void);
bool friday_ui_set_media(const cJSON *message);

/* Authenticated priority overlay lifecycle; result binding is checked natively. */
bool friday_ui_media_suspended(void);
bool friday_ui_media_prepare_overlay(const cJSON *root);
void friday_ui_media_resume_result(const cJSON *root);
cJSON *friday_ui_media_resume_request(void);
void friday_ui_media_resume_sent(bool sent);
void friday_ui_media_overlay_resolved(void);
void friday_ui_media_note_action(const char *action);
void friday_ui_media_overlay_completed(const cJSON *root);
bool friday_ui_media_overlay_needs_resume(void);
bool friday_ui_media_visible(void);

/* Opt-in continuous-context candidate; CONFIG_FRIDAY_EXPERIENCE_CONTEXT defaults off. */
void friday_ui_experience_preview(bool enabled);

#include "friday_experience_guard.h"
bool friday_ui_experience_enabled(void);
void friday_ui_experience_session(const char *epoch);
void friday_ui_experience_request(const char *request);
void friday_ui_experience_send_failed(const char *request);
friday_experience_accept_t friday_ui_experience_packet(const cJSON *packet);
void friday_ui_experience_finished(void);
typedef struct {bool handled, accepted, completed;char *ack;} friday_ui_receive_result_t;
/* One atomic freshness + render operation. Caller sends/frees ACK after unlock. */
friday_ui_receive_result_t friday_ui_receive(const cJSON *packet,bool recording,bool speech_playing);

#include "friday_execution_stop.h"
/* Atomic notice/control state; applied by LVGL timer, separate from protected facts. */
void friday_ui_execution_state(friday_execution_phase_t phase,bool online);
