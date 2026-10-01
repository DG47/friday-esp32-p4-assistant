#include "friday_audio.h"

#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "bsp/esp-bsp.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/queue.h"
#include "esp_heap_caps.h"
#include "mbedtls/base64.h"

#include "friday_backend.h"
#include "friday_ui.h"
#include "friday_vad.h"

#define FRIDAY_AUDIO_SAMPLE_RATE 24000
#define FRIDAY_AUDIO_STREAM_SAMPLE_RATE 12000
#define FRIDAY_AUDIO_FRAME_MS 20
#define FRIDAY_AUDIO_CHANNELS 2
#define FRIDAY_AUDIO_SAMPLES_PER_CHANNEL \
    (FRIDAY_AUDIO_SAMPLE_RATE * FRIDAY_AUDIO_FRAME_MS / 1000)
#define FRIDAY_AUDIO_FRAME_SAMPLES \
    (FRIDAY_AUDIO_SAMPLES_PER_CHANNEL * FRIDAY_AUDIO_CHANNELS)
#define FRIDAY_AUDIO_STREAM_SAMPLES \
    (FRIDAY_AUDIO_STREAM_SAMPLE_RATE * FRIDAY_AUDIO_FRAME_MS / 1000)
#define FRIDAY_WAKE_STREAM_FRAMES 25
#define FRIDAY_WAKE_STREAM_SAMPLES \
    (FRIDAY_AUDIO_STREAM_SAMPLES * FRIDAY_WAKE_STREAM_FRAMES)
#define FRIDAY_WAKE_VAD_GUARD_FRAMES (300 / FRIDAY_AUDIO_FRAME_MS)
#define FRIDAY_AUDIO_MAX_RECORDING_FRAMES (12000 / FRIDAY_AUDIO_FRAME_MS)

static const char *TAG = "friday_audio";
static esp_codec_dev_handle_t microphone;

/* Bounded PCM playback. The WebSocket callback collects one utterance; a
 * separate task owns each completed buffer. Epoch cancellation never frees a
 * buffer while the codec writer uses it. Cancellation is checked before each
 * 20ms codec write; physical interruption latency still requires testing. */
#define PLAYBACK_MAX_BYTES (24000 * 2 * 30)
static esp_codec_dev_handle_t speaker;
static SemaphoreHandle_t playback_lock;
static QueueHandle_t playback_queue;
static volatile uint32_t playback_epoch;
static volatile bool playback_busy;
static uint8_t *collecting;
static size_t collecting_size, collecting_used;
static int collecting_sequence;
static char collecting_id[33];
typedef struct { uint8_t *pcm; size_t size; uint32_t epoch; char id[33]; } playback_job_t;

bool friday_audio_playing(void) { return playback_busy; }
bool friday_audio_speaker_ready(void) { return speaker != NULL && playback_queue != NULL; }

void friday_audio_stop_speech(void)
{
    if (playback_lock == NULL) return;
    xSemaphoreTake(playback_lock, portMAX_DELAY);
    playback_epoch++;
    playback_busy = false;
    free(collecting);
    collecting = NULL;
    collecting_size = collecting_used = 0;
    playback_job_t pending;
    while (playback_queue != NULL && xQueueReceive(playback_queue, &pending, 0) == pdTRUE) free(pending.pcm);
    xSemaphoreGive(playback_lock);
}

static void playback_task(void *argument)
{
    (void)argument;
    playback_job_t job;
    int16_t stereo[960];
    while (true) {
        if (xQueueReceive(playback_queue, &job, portMAX_DELAY) != pdTRUE) continue;
        bool success = true;
        for (size_t offset = 0; offset < job.size && job.epoch == playback_epoch; offset += 960) {
            size_t samples = (job.size - offset) / 2;
            if (samples > 480) samples = 480;
            for (size_t index = 0; index < samples; index++) {
                int16_t mono = (int16_t)((uint16_t)job.pcm[offset + index * 2] |
                                        ((uint16_t)job.pcm[offset + index * 2 + 1] << 8));
                stereo[index * 2] = stereo[index * 2 + 1] = mono;
            }
            if (esp_codec_dev_write(speaker, stereo, (int)(samples * 4)) != ESP_OK) {
                success = false;
                break;
            }
        }
        free(job.pcm);
        xSemaphoreTake(playback_lock, portMAX_DELAY);
        bool current = job.epoch == playback_epoch;
        if (current) playback_busy = false;
        xSemaphoreGive(playback_lock);
        if (current) {
            friday_ui_set_state(FRIDAY_STATE_IDLE);
            friday_backend_speech_finished(job.id, success);
            if (!success) friday_ui_set_backend("SPEAKER ERROR");
        }
    }
}

void friday_audio_speech_message(const cJSON *root)
{
    if (!friday_audio_speaker_ready()) return;
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "type");
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(root, "stream_id");
    if (!cJSON_IsString(type)) return;
    if (strcmp(type->valuestring, "speech.cancel") == 0) { friday_audio_stop_speech(); return; }
    if (!cJSON_IsString(id) || strlen(id->valuestring) != 32) return;
    if (strcmp(type->valuestring, "speech.start") == 0) {
        if (friday_backend_audio_active()) return;
        const cJSON *bytes = cJSON_GetObjectItemCaseSensitive(root, "bytes");
        const cJSON *rate = cJSON_GetObjectItemCaseSensitive(root, "sample_rate");
        const cJSON *channels = cJSON_GetObjectItemCaseSensitive(root, "channels");
        const cJSON *format = cJSON_GetObjectItemCaseSensitive(root, "format");
        if (!cJSON_IsNumber(bytes) || bytes->valueint <= 0 || bytes->valueint > PLAYBACK_MAX_BYTES || bytes->valueint % 2 ||
            !cJSON_IsNumber(rate) || rate->valueint != 24000 || !cJSON_IsNumber(channels) || channels->valueint != 1 ||
            !cJSON_IsString(format) || strcmp(format->valuestring, "pcm_s16le")) return;
        friday_audio_stop_speech();
        xSemaphoreTake(playback_lock, portMAX_DELAY);
        collecting = heap_caps_malloc(bytes->valueint, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (collecting != NULL) {
            collecting_size = bytes->valueint;
            collecting_used = 0;
            collecting_sequence = 0;
            memcpy(collecting_id, id->valuestring, 33);
            playback_busy = true;
        }
        bool allocated = collecting != NULL;
        xSemaphoreGive(playback_lock);
        if (!allocated) friday_backend_speech_finished(id->valuestring, false);
        return;
    }
    xSemaphoreTake(playback_lock, portMAX_DELAY);
    if (collecting == NULL || strcmp(id->valuestring, collecting_id)) {
        xSemaphoreGive(playback_lock); return;
    }
    bool invalid = false;
    if (strcmp(type->valuestring, "speech.chunk") == 0) {
        const cJSON *pcm = cJSON_GetObjectItemCaseSensitive(root, "pcm");
        const cJSON *sequence = cJSON_GetObjectItemCaseSensitive(root, "sequence");
        size_t length = 0;
        uint8_t decoded[4800];
        invalid = !cJSON_IsString(pcm) || strlen(pcm->valuestring) > 6400 ||
                  !cJSON_IsNumber(sequence) || sequence->valueint != collecting_sequence;
        if (!invalid) {
            invalid = mbedtls_base64_decode(decoded, sizeof(decoded), &length,
                (const unsigned char *)pcm->valuestring, strlen(pcm->valuestring)) != 0 ||
                length == 0 || length % 2 || length > collecting_size - collecting_used;
        }
        if (!invalid) {
            memcpy(collecting + collecting_used, decoded, length);
            collecting_used += length;
            collecting_sequence++;
        }
    } else if (strcmp(type->valuestring, "speech.end") == 0) {
        invalid = collecting_used != collecting_size;
        if (!invalid) {
            playback_job_t job = { .pcm = collecting, .size = collecting_size, .epoch = playback_epoch };
            memcpy(job.id, collecting_id, 33);
            if (xQueueSend(playback_queue, &job, 0) == pdTRUE) {
                collecting = NULL;
                friday_ui_set_state(FRIDAY_STATE_SPEAKING);
            } else invalid = true;
        }
    }
    if (invalid) {
        free(collecting); collecting = NULL;
        playback_busy = false;
        playback_epoch++;
    }
    xSemaphoreGive(playback_lock);
    if (invalid) friday_backend_speech_finished(id->valuestring, false);
}

static int16_t amplify_sample(int32_t sample)
{
    int32_t amplified = sample * CONFIG_FRIDAY_AUDIO_DIGITAL_GAIN;
    if (amplified > INT16_MAX) {
        return INT16_MAX;
    }
    if (amplified < INT16_MIN) {
        return INT16_MIN;
    }
    return (int16_t)amplified;
}

static void microphone_task(void *argument)
{
    (void)argument;
    int16_t *samples = malloc(FRIDAY_AUDIO_FRAME_SAMPLES * sizeof(*samples));
    int16_t *mono = malloc(FRIDAY_AUDIO_SAMPLES_PER_CHANNEL * sizeof(*mono));
    int16_t *idle_audio = malloc(FRIDAY_WAKE_STREAM_SAMPLES * sizeof(*idle_audio));
    if (samples == NULL || mono == NULL || idle_audio == NULL) {
        ESP_LOGE(TAG, "Could not allocate microphone frame");
        friday_ui_set_microphone("MIC ERROR", 0, 0);
        free(samples);
        free(mono);
        free(idle_audio);
        vTaskDelete(NULL);
        return;
    }

    friday_ui_set_surface("AMBIENT", "FRIDAY", "Say \"Friday\" or tap the orb");

    uint32_t frames = 0;
    uint32_t recording_frames = 0;
    uint32_t wake_vad_guard_frames = 0;
    size_t idle_audio_used = 0;
    int32_t interval_peak = 0;
    bool was_recording = false;
    bool wake_window = false;
    uint32_t wake_frames = 0;
    friday_vad_t wake_vad;
    friday_vad_init(&wake_vad, FRIDAY_AUDIO_FRAME_MS);
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
            int32_t raw_mono = (left + right) / 2;
            mono_sum_squares += (uint64_t)(raw_mono * raw_mono);

            mono[index] = amplify_sample(raw_mono);
        }
        int32_t mono_rms = (int32_t)sqrt(
            (double)mono_sum_squares / (double)FRIDAY_AUDIO_SAMPLES_PER_CHANNEL);

        for (size_t index = 0; index < FRIDAY_AUDIO_STREAM_SAMPLES; ++index) {
            int32_t filtered =
                ((int32_t)mono[index * 2] + (int32_t)mono[index * 2 + 1]) / 2;
            mono[index] = (int16_t)filtered;
        }

        if (friday_audio_playing()) {
            idle_audio_used = 0;
            wake_window = false;
            wake_frames = 0;
            was_recording = false;
            continue; /* Half duplex: never treat Friday playback as a wake word. */
        }
        bool recording = friday_backend_audio_active();
        if (recording) {
            idle_audio_used = 0;
            wake_window = false;
            wake_frames = 0;
            friday_vad_begin_turn(&wake_vad);
            if (!was_recording) {
                friday_vad_begin_turn(&vad);
                ESP_LOGI(TAG, "VAD armed: speech=%ld silence=%ld noise=%.1f",
                         (long)friday_vad_speech_threshold(&vad),
                         (long)friday_vad_silence_threshold(&vad),
                         (double)vad.noise_floor_rms);
            }
            esp_err_t send_result = friday_backend_send_audio(
                mono, FRIDAY_AUDIO_STREAM_SAMPLES);
            if (send_result != ESP_OK) {
                ESP_LOGW(TAG, "Audio frame send failed: %s", esp_err_to_name(send_result));
            }
            recording_frames++;
            friday_vad_event_t vad_event = FRIDAY_VAD_EVENT_NONE;
            if (wake_vad_guard_frames > 0) {
                wake_vad_guard_frames--;
            } else {
                vad_event = friday_vad_process(&vad, mono_rms);
            }
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
            wake_vad_guard_frames = 0;
            friday_vad_observe_idle(&vad, mono_rms);
            if (friday_backend_take_wake_request()) {
                idle_audio_used = 0;
                wake_window = false;
                friday_vad_begin_turn(&wake_vad);
                if (friday_backend_begin_audio("wake_word")) {
                    friday_ui_set_state(FRIDAY_STATE_LISTENING);
                    wake_vad_guard_frames = FRIDAY_WAKE_VAD_GUARD_FRAMES;
                }
            } else {
                if (!wake_window) {
                    /* Keep a chronological 500 ms pre-roll, including onset. */
                    if (idle_audio_used == FRIDAY_WAKE_STREAM_SAMPLES) {
                        memmove(idle_audio, idle_audio + FRIDAY_AUDIO_STREAM_SAMPLES,
                                (idle_audio_used - FRIDAY_AUDIO_STREAM_SAMPLES) * sizeof(*idle_audio));
                        idle_audio_used -= FRIDAY_AUDIO_STREAM_SAMPLES;
                    }
                    friday_vad_observe_idle(&wake_vad, mono_rms);
                }
                memcpy(idle_audio + idle_audio_used, mono,
                       FRIDAY_AUDIO_STREAM_SAMPLES * sizeof(*mono));
                idle_audio_used += FRIDAY_AUDIO_STREAM_SAMPLES;
                friday_vad_event_t wake_event = friday_vad_process(&wake_vad, mono_rms);
                if (!wake_window && wake_event == FRIDAY_VAD_EVENT_SPEECH_STARTED) {
                    wake_window = friday_backend_begin_wake_window();
                    wake_frames = 0;
                    if (wake_window) {
                        ESP_LOGI(TAG, "Wake audio gate opened: speech detected");
                    } else {
                        friday_vad_begin_turn(&wake_vad);
                    }
                }
                if (wake_window) {
                    wake_frames++;
                }
                if (wake_window && idle_audio_used == FRIDAY_WAKE_STREAM_SAMPLES) {
                    esp_err_t wake_send_result = friday_backend_send_idle_audio(
                        idle_audio, idle_audio_used);
                    idle_audio_used = 0;
                    if (wake_send_result != ESP_OK &&
                        wake_send_result != ESP_ERR_INVALID_STATE) {
                        ESP_LOGW(TAG, "Wake audio send failed: %s",
                                 esp_err_to_name(wake_send_result));
                    }
                    /* Three seconds minimum feeds one complete Whisper probe;
                     * a twelve-second cap bounds sustained noise/speech. Close
                     * only on batch boundaries so a final probe is not lost. */
                    if (wake_send_result != ESP_OK ||
                        (wake_frames >= 3000 / FRIDAY_AUDIO_FRAME_MS &&
                         wake_event == FRIDAY_VAD_EVENT_SPEECH_ENDED) ||
                        wake_frames >= FRIDAY_AUDIO_MAX_RECORDING_FRAMES) {
                        wake_window = false;
                        friday_vad_begin_turn(&wake_vad);
                        ESP_LOGI(TAG, "Wake audio gate closed after %lu ms",
                                 (unsigned long)(wake_frames * FRIDAY_AUDIO_FRAME_MS));
                    }
                }
            }
        }
        was_recording = recording;
        int32_t rms = (int32_t)sqrt(
            (double)sum_squares / (double)FRIDAY_AUDIO_FRAME_SAMPLES);
        if (peak > interval_peak) {
            interval_peak = peak;
        }

        if ((frames % 12U) == 0U) {
            friday_ui_set_microphone("MICS ONLINE", peak, rms);
        }
        if ((frames % 50U) == 0U) {
            ESP_LOGI(TAG, "Microphone 1s max=%ld current_rms=%ld",
                     (long)interval_peak, (long)rms);
            interval_peak = 0;
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
            microphone, BSP_AUDIO_ES7210_MIC_MASK_FL_FR, 37.5f),
        TAG, "Could not set microphone gain");

    playback_lock = xSemaphoreCreateMutex();
    playback_queue = xQueueCreate(1, sizeof(playback_job_t));
    speaker = bsp_audio_codec_speaker_init();
    esp_codec_dev_sample_info_t output = { .sample_rate = 24000, .channel = 2, .bits_per_sample = 16 };
    if (playback_lock != NULL && playback_queue != NULL && speaker != NULL &&
        esp_codec_dev_open(speaker, &output) == ESP_OK &&
        esp_codec_dev_set_out_vol(speaker, 40) == ESP_OK) {
        if (xTaskCreate(playback_task, "friday_speaker", 8192, NULL, 5, NULL) != pdPASS) speaker = NULL;
    } else {
        speaker = NULL;
        ESP_LOGW(TAG, "Speaker unavailable; microphone and display remain usable");
    }
    BaseType_t created = xTaskCreatePinnedToCore(
        microphone_task, "friday_mics", 8192, NULL, 6, NULL, 1);
    ESP_RETURN_ON_FALSE(created == pdPASS, ESP_ERR_NO_MEM, TAG,
                        "Could not start microphone task");
    return ESP_OK;
}
