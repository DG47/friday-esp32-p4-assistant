#include "friday_audio.h"

#include <math.h>
#include <stdint.h>
#include <stdlib.h>

#include "bsp/esp-bsp.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "friday_backend.h"
#include "friday_ui.h"
#include "friday_vad.h"

#define FRIDAY_AUDIO_SAMPLE_RATE 24000
#define FRIDAY_AUDIO_FRAME_MS 20
#define FRIDAY_AUDIO_CHANNELS 2
#define FRIDAY_AUDIO_SAMPLES_PER_CHANNEL \
    (FRIDAY_AUDIO_SAMPLE_RATE * FRIDAY_AUDIO_FRAME_MS / 1000)
#define FRIDAY_AUDIO_FRAME_SAMPLES \
    (FRIDAY_AUDIO_SAMPLES_PER_CHANNEL * FRIDAY_AUDIO_CHANNELS)
#define FRIDAY_AUDIO_MAX_RECORDING_FRAMES (12000 / FRIDAY_AUDIO_FRAME_MS)

static const char *TAG = "friday_audio";
static esp_codec_dev_handle_t microphone;

static void microphone_task(void *argument)
{
    (void)argument;
    int16_t *samples = malloc(FRIDAY_AUDIO_FRAME_SAMPLES * sizeof(*samples));
    int16_t *mono = malloc(FRIDAY_AUDIO_SAMPLES_PER_CHANNEL * sizeof(*mono));
    if (samples == NULL || mono == NULL) {
        ESP_LOGE(TAG, "Could not allocate microphone frame");
        friday_ui_set_microphone("MIC ERROR", 0, 0);
        free(samples);
        free(mono);
        vTaskDelete(NULL);
        return;
    }

    uint32_t frames = 0;
    uint32_t recording_frames = 0;
    bool was_recording = false;
    friday_vad_t vad;
    friday_vad_init(&vad, FRIDAY_AUDIO_FRAME_MS);
    ESP_LOGI(TAG, "ES7210 capture active: %d Hz, FL+FR", FRIDAY_AUDIO_SAMPLE_RATE);
    while (true) {
        int result = esp_codec_dev_read(
            microphone, samples, FRIDAY_AUDIO_FRAME_SAMPLES * sizeof(*samples));
        if (result != ESP_CODEC_DEV_OK) {
            ESP_LOGE(TAG, "Microphone read failed: %d", result);
            friday_ui_set_microphone("MIC READ ERROR", 0, 0);
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        int32_t peak = 0;
        uint64_t sum_squares = 0;
        for (size_t index = 0; index < FRIDAY_AUDIO_FRAME_SAMPLES; ++index) {
            int32_t sample = samples[index];
            int32_t magnitude = sample < 0 ? -sample : sample;
            if (magnitude > peak) {
                peak = magnitude;
            }
            sum_squares += (uint64_t)(sample * sample);
        }

        uint64_t mono_sum_squares = 0;
        for (size_t index = 0; index < FRIDAY_AUDIO_SAMPLES_PER_CHANNEL; ++index) {
            int32_t left = samples[index * FRIDAY_AUDIO_CHANNELS];
            int32_t right = samples[index * FRIDAY_AUDIO_CHANNELS + 1];
            mono[index] = (int16_t)((left + right) / 2);
            int32_t sample = mono[index];
            mono_sum_squares += (uint64_t)(sample * sample);
        }
        int32_t mono_rms = (int32_t)sqrt(
            (double)mono_sum_squares / (double)FRIDAY_AUDIO_SAMPLES_PER_CHANNEL);

        bool recording = friday_backend_audio_active();
        if (recording) {
            if (!was_recording) {
                friday_vad_begin_turn(&vad);
                ESP_LOGI(TAG, "VAD armed: speech=%ld silence=%ld noise=%.1f",
                         (long)friday_vad_speech_threshold(&vad),
                         (long)friday_vad_silence_threshold(&vad),
                         (double)vad.noise_floor_rms);
            }
            esp_err_t send_result = friday_backend_send_audio(
                mono, FRIDAY_AUDIO_SAMPLES_PER_CHANNEL);
            if (send_result != ESP_OK) {
                ESP_LOGW(TAG, "Audio frame send failed: %s", esp_err_to_name(send_result));
            }
            recording_frames++;
            friday_vad_event_t vad_event = friday_vad_process(&vad, mono_rms);
            if (vad_event == FRIDAY_VAD_EVENT_SPEECH_STARTED) {
                ESP_LOGI(TAG, "VAD speech started: rms=%ld", (long)mono_rms);
            } else if (vad_event == FRIDAY_VAD_EVENT_SPEECH_ENDED) {
                ESP_LOGI(TAG, "VAD speech ended after %d ms silence",
                         CONFIG_FRIDAY_VAD_SILENCE_END_MS);
                friday_backend_end_audio();
                recording_frames = 0;
            } else if (recording_frames >= FRIDAY_AUDIO_MAX_RECORDING_FRAMES) {
                ESP_LOGI(TAG, "Ending audio turn at 12 second safety limit");
                friday_backend_end_audio();
                recording_frames = 0;
            }
        } else {
            recording_frames = 0;
            friday_vad_observe_idle(&vad, mono_rms);
        }
        was_recording = recording;
        int32_t rms = (int32_t)sqrt(
            (double)sum_squares / (double)FRIDAY_AUDIO_FRAME_SAMPLES);

        if ((frames % 12U) == 0U) {
            friday_ui_set_microphone("MICS ONLINE", peak, rms);
        }
        if ((frames % 50U) == 0U) {
            ESP_LOGI(TAG, "Microphone level peak=%ld rms=%ld",
                     (long)peak, (long)rms);
        }
        frames++;
    }
}

esp_err_t friday_audio_start(void)
{
    ESP_RETURN_ON_ERROR(bsp_audio_init_voice_24k(), TAG,
                        "Could not initialize 24 kHz voice bus");

    microphone = bsp_audio_codec_microphone_init();
    ESP_RETURN_ON_FALSE(microphone != NULL, ESP_FAIL, TAG,
                        "Could not initialize ES7210 microphones");

    esp_codec_dev_sample_info_t format = {
        .sample_rate = FRIDAY_AUDIO_SAMPLE_RATE,
        .channel = BSP_AUDIO_TDM_SLOT_COUNT,
        .bits_per_sample = 16,
        .channel_mask = BSP_AUDIO_TDM_SLOT_MASK_FL_FR,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(microphone, &format), TAG,
                        "Could not open ES7210 capture");
    ESP_RETURN_ON_ERROR(
        esp_codec_dev_set_in_channel_gain(
            microphone, BSP_AUDIO_ES7210_MIC_MASK_FL_FR, 30.0f),
        TAG, "Could not set microphone gain");

    BaseType_t created = xTaskCreatePinnedToCore(
        microphone_task, "friday_mics", 4096, NULL, 6, NULL, 1);
    ESP_RETURN_ON_FALSE(created == pdPASS, ESP_ERR_NO_MEM, TAG,
                        "Could not start microphone task");
    return ESP_OK;
}
