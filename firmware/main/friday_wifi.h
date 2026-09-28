#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

esp_err_t friday_wifi_start(void);
bool friday_wifi_wait_connected(TickType_t timeout);
esp_err_t friday_wifi_get_backend_uri(char *uri, size_t uri_size);
