#include "esp_log.h"
#include "nvs_flash.h"

#include "bsp/esp-bsp.h"
#include "bsp/display.h"

#include "board_p3a.h"
#include "friday_audio.h"
#include "friday_backend.h"
#include "friday_state.h"
#include "friday_ui.h"
#include "friday_wifi.h"

static const char *TAG = "friday";

static void log_hardware_plan(void)
{
    ESP_LOGI(TAG, "P3A display: %dx%d, reset=%d, backlight=%d",
             P3A_LCD_H_RES, P3A_LCD_V_RES, P3A_LCD_RESET_GPIO,
             P3A_LCD_BACKLIGHT_GPIO);
    ESP_LOGI(TAG, "P3A audio: ES7210=0x%02x, ES8311=0x%02x, PA=%d",
             P3A_ES7210_I2C_ADDRESS, P3A_ES8311_I2C_ADDRESS,
             P3A_SPEAKER_PA_GPIO);
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    friday_state_set(FRIDAY_STATE_IDLE);
    ESP_LOGI(TAG, "Friday firmware scaffold booting in %s state",
             friday_state_name(friday_state_get()));
    log_hardware_plan();

    ESP_LOGI(TAG, "Starting Waveshare display and GT911 touch");
    ESP_ERROR_CHECK(bsp_display_start() != NULL ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(bsp_display_backlight_on());
    ESP_ERROR_CHECK(bsp_display_brightness_set(62));

    ESP_ERROR_CHECK(bsp_display_lock(-1) ? ESP_OK : ESP_ERR_TIMEOUT);
    friday_ui_create();
#if CONFIG_FRIDAY_EXPERIENCE_CONTEXT
    friday_ui_experience_preview(true);
#endif
    bsp_display_unlock();

    ESP_LOGI(TAG, "Friday display online; touch the orb to activate the backend");
    ESP_ERROR_CHECK(friday_wifi_start());
    friday_ui_set_activation_callback(friday_backend_toggle_audio);
    friday_ui_set_action_callback(friday_backend_scene_action);

    esp_err_t audio_result = friday_audio_start();
    if (audio_result != ESP_OK) {
        ESP_LOGE(TAG, "Microphone startup failed: %s", esp_err_to_name(audio_result));
        friday_ui_set_microphone("MICS UNAVAILABLE", 0, 0);
    }
    ESP_ERROR_CHECK(friday_backend_start());
}
