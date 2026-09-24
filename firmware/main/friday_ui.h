#pragma once

#include <stdint.h>

void friday_ui_create(void);
void friday_ui_set_network(const char *status, const char *detail);
void friday_ui_set_microphone(const char *status, int32_t peak, int32_t rms);
