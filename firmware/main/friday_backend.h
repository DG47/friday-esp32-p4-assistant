#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t friday_backend_start(void);
bool friday_backend_begin_audio(const char *source);
bool friday_backend_toggle_audio(void);
bool friday_backend_audio_active(void);
bool friday_backend_take_wake_request(void);
esp_err_t friday_backend_send_audio(const int16_t *samples, size_t sample_count);
esp_err_t friday_backend_send_idle_audio(const int16_t *samples, size_t sample_count);
void friday_backend_end_audio(void);
