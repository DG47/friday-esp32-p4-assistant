#pragma once

/* Waveshare ESP32-P4-WIFI6-Touch-LCD-4B / P3A pin map. */
#define P3A_LCD_H_RES          720
#define P3A_LCD_V_RES          720
#define P3A_LCD_RESET_GPIO     27
#define P3A_LCD_BACKLIGHT_GPIO 26

#define P3A_I2C_SDA_GPIO       7
#define P3A_I2C_SCL_GPIO       8
#define P3A_TOUCH_RESET_GPIO   -1

#define P3A_I2S_DOUT_GPIO      9
#define P3A_I2S_LRCK_GPIO      10
#define P3A_I2S_DSIN_GPIO      11
#define P3A_I2S_SCLK_GPIO      12
#define P3A_I2S_MCLK_GPIO      13
#define P3A_SPEAKER_PA_GPIO    53

#define P3A_ES8311_I2C_ADDRESS 0x18
#define P3A_ES7210_I2C_ADDRESS 0x40
