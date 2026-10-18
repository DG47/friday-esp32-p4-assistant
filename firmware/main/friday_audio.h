#pragma once

#include "esp_err.h"

esp_err_t friday_audio_start(void);


#include <stdbool.h>
#include <stddef.h>
#include "cJSON.h"
bool friday_audio_playing(void);
bool friday_audio_speaker_ready(void);
void friday_audio_stop_speech(void);
void friday_audio_speech_message(const cJSON *root);

/* Latest numeric-only snapshot; zero-wait, stale snapshots omitted. */
bool friday_audio_copy_capture_diagnostics(char *destination, size_t capacity);
