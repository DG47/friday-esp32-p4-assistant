#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "friday_state.h"

typedef bool (*friday_ui_activation_callback_t)(void);

void friday_ui_create(void);
void friday_ui_set_activation_callback(friday_ui_activation_callback_t callback);
void friday_ui_set_state(friday_state_t state);
void friday_ui_set_surface(const char *kind, const char *title, const char *subtitle);
void friday_ui_set_response(const char *text);
void friday_ui_set_tool(const char *name, const char *status);
void friday_ui_set_backend(const char *status);
void friday_ui_set_network(const char *status, const char *detail);
void friday_ui_set_microphone(const char *status, int32_t peak, int32_t rms);
