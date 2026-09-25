#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    FRIDAY_VAD_EVENT_NONE = 0,
    FRIDAY_VAD_EVENT_SPEECH_STARTED,
    FRIDAY_VAD_EVENT_SPEECH_ENDED,
} friday_vad_event_t;

typedef struct {
    float noise_floor_rms;
    uint32_t speech_frames;
    uint32_t silence_frames;
    uint32_t frame_ms;
    bool speech_started;
} friday_vad_t;

void friday_vad_init(friday_vad_t *vad, uint32_t frame_ms);
void friday_vad_observe_idle(friday_vad_t *vad, int32_t rms);
void friday_vad_begin_turn(friday_vad_t *vad);
friday_vad_event_t friday_vad_process(friday_vad_t *vad, int32_t rms);
int32_t friday_vad_speech_threshold(const friday_vad_t *vad);
int32_t friday_vad_silence_threshold(const friday_vad_t *vad);
