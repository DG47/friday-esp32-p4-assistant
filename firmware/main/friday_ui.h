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
void friday_ui_media_clear(void);
bool friday_ui_set_media(const cJSON *message);
