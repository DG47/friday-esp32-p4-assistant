#include "friday_vad.h"

#include <stddef.h>

#include "sdkconfig.h"

static int32_t threshold_from_noise(float noise_floor, int ratio_percent,
                                    int minimum)
{
    int32_t adaptive = (int32_t)(noise_floor * (float)ratio_percent / 100.0f);
    return adaptive > minimum ? adaptive : minimum;
}

void friday_vad_init(friday_vad_t *vad, uint32_t frame_ms)
{
    if (vad == NULL) {
        return;
    }
    *vad = (friday_vad_t) {
        .noise_floor_rms = CONFIG_FRIDAY_VAD_INITIAL_NOISE_RMS,
        .frame_ms = frame_ms,
    };
}

void friday_vad_observe_idle(friday_vad_t *vad, int32_t rms)
{
    if (vad == NULL || rms <= 0) {
        return;
    }

    float ceiling = vad->noise_floor_rms * 3.0f + 20.0f;
    if ((float)rms <= ceiling) {
        vad->noise_floor_rms = vad->noise_floor_rms * 0.98f + (float)rms * 0.02f;
    }
}

void friday_vad_begin_turn(friday_vad_t *vad)
{
    if (vad == NULL) {
        return;
    }
    vad->speech_frames = 0;
    vad->silence_frames = 0;
    vad->speech_started = false;
}

int32_t friday_vad_speech_threshold(const friday_vad_t *vad)
{
    return threshold_from_noise(
        vad->noise_floor_rms,
        CONFIG_FRIDAY_VAD_SPEECH_RATIO_PERCENT,
        CONFIG_FRIDAY_VAD_MIN_SPEECH_RMS);
}

int32_t friday_vad_silence_threshold(const friday_vad_t *vad)
{
    return threshold_from_noise(
        vad->noise_floor_rms,
        CONFIG_FRIDAY_VAD_SILENCE_RATIO_PERCENT,
        CONFIG_FRIDAY_VAD_MIN_SILENCE_RMS);
}

friday_vad_event_t friday_vad_process(friday_vad_t *vad, int32_t rms)
{
    if (vad == NULL || vad->frame_ms == 0) {
        return FRIDAY_VAD_EVENT_NONE;
    }

    if (!vad->speech_started) {
        if (rms >= friday_vad_speech_threshold(vad)) {
            vad->speech_frames++;
        } else {
            vad->speech_frames = 0;
        }

        uint32_t required_frames =
            (CONFIG_FRIDAY_VAD_SPEECH_START_MS + vad->frame_ms - 1) / vad->frame_ms;
        if (vad->speech_frames >= required_frames) {
            vad->speech_started = true;
            vad->silence_frames = 0;
            return FRIDAY_VAD_EVENT_SPEECH_STARTED;
        }
        return FRIDAY_VAD_EVENT_NONE;
    }

    if (rms <= friday_vad_silence_threshold(vad)) {
        vad->silence_frames++;
    } else {
        vad->silence_frames = 0;
    }

    uint32_t required_frames =
        (CONFIG_FRIDAY_VAD_SILENCE_END_MS + vad->frame_ms - 1) / vad->frame_ms;
    if (vad->silence_frames >= required_frames) {
        return FRIDAY_VAD_EVENT_SPEECH_ENDED;
    }
    return FRIDAY_VAD_EVENT_NONE;
}
