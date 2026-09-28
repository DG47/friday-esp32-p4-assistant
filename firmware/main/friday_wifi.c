#include "friday_wifi.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_check.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs.h"

#include "friday_ui.h"

#define FRIDAY_SETUP_SSID "Friday-Setup"
#define FRIDAY_SETUP_PASSWORD "fridaysetup"
#define FRIDAY_MAX_RETRIES 5
#define FRIDAY_WIFI_CONNECTED_BIT BIT0

static const char *TAG = "friday_wifi";
static httpd_handle_t setup_server;
static bool has_credentials;
static int retry_count;
static EventGroupHandle_t wifi_events;

static esp_err_t load_credentials(char *ssid, size_t ssid_size,
                                  char *password, size_t password_size)
{
    nvs_handle_t handle;
    esp_err_t result = nvs_open("friday_wifi", NVS_READONLY, &handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_ERR_NOT_FOUND;
    }
    ESP_RETURN_ON_ERROR(result, TAG, "Could not open Wi-Fi settings");

    size_t stored_ssid_size = ssid_size;
    size_t stored_password_size = password_size;
    result = nvs_get_str(handle, "ssid", ssid, &stored_ssid_size);
    if (result == ESP_OK) {
        result = nvs_get_str(handle, "password", password, &stored_password_size);
    }
    nvs_close(handle);
    return result;
}

static esp_err_t save_credentials(const char *ssid, const char *password,
                                  const char *backend_uri)
{
    nvs_handle_t handle;
    ESP_RETURN_ON_ERROR(nvs_open("friday_wifi", NVS_READWRITE, &handle), TAG,
                        "Could not open Wi-Fi settings");
    esp_err_t result = nvs_set_str(handle, "ssid", ssid);
    if (result == ESP_OK) {
        result = nvs_set_str(handle, "password", password);
    }
    if (result == ESP_OK) {
        result = backend_uri != NULL && backend_uri[0] != '\0'
                     ? nvs_set_str(handle, "backend_uri", backend_uri)
                     : nvs_erase_key(handle, "backend_uri");
        if (result == ESP_ERR_NVS_NOT_FOUND) {
            result = ESP_OK;
        }
    }
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result;
}

static void clear_credentials(void)
{
    nvs_handle_t handle;
    if (nvs_open("friday_wifi", NVS_READWRITE, &handle) == ESP_OK) {
        nvs_erase_all(handle);
        nvs_commit(handle);
        nvs_close(handle);
    }
}

static void restart_task(void *argument)
{
    (void)argument;
    vTaskDelay(pdMS_TO_TICKS(1800));
    esp_restart();
}

static const char setup_page[] =
    "<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Friday setup</title><style>body{font:16px system-ui;background:#05090b;color:#edf8f7;"
    "max-width:420px;margin:48px auto;padding:24px}h1{font-weight:400}label{display:block;margin:18px 0 6px}"
    "input{box-sizing:border-box;width:100%;padding:12px;border:1px solid #31504f;border-radius:8px;"
    "background:#0b1215;color:#edf8f7}button{margin-top:22px;width:100%;padding:13px;border:0;border-radius:8px;"
    "background:#79f5dd;color:#06201d;font-weight:700}</style></head><body><h1>Friday</h1>"
    "<p>Connect this device to your Wi-Fi. Credentials stay on the device.</p>"
    "<form method=post action=/configure><label>Wi-Fi name</label><input name=ssid maxlength=32 required>"
    "<label>Password</label><input name=password type=password maxlength=63>"
    "<label>Backend WebSocket URL (optional)</label>"
    "<input name=backend maxlength=191 placeholder='Auto-discover on local network'>"
    "<button>Connect Friday</button></form></body></html>";

static void url_decode(char *destination, size_t destination_size, const char *source)
{
    size_t output = 0;
    while (*source != '\0' && output + 1 < destination_size) {
        if (*source == '+' ) {
            destination[output++] = ' ';
            source++;
        } else if (*source == '%' && isxdigit((unsigned char)source[1]) &&
                   isxdigit((unsigned char)source[2])) {
            unsigned int value = 0;
            sscanf(source + 1, "%2x", &value);
            destination[output++] = (char)value;
            source += 3;
        } else {
            destination[output++] = *source++;
        }
    }
    destination[output] = '\0';
}

static bool form_value(const char *body, const char *key, char *value, size_t value_size)
{
    char pattern[24];
    snprintf(pattern, sizeof(pattern), "%s=", key);
    const char *start = strstr(body, pattern);
    if (start == NULL) {
        return false;
    }
    start += strlen(pattern);
    const char *end = strchr(start, '&');
    size_t length = end == NULL ? strlen(start) : (size_t)(end - start);
    char encoded[256];
    if (length >= sizeof(encoded)) {
        return false;
    }
    memcpy(encoded, start, length);
    encoded[length] = '\0';
    url_decode(value, value_size, encoded);
    return true;
}

static esp_err_t root_handler(httpd_req_t *request)
{
    httpd_resp_set_type(request, "text/html");
    return httpd_resp_send(request, setup_page, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t configure_handler(httpd_req_t *request)
{
    if (request->content_len <= 0 || request->content_len >= 512) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid setup request");
        return ESP_FAIL;
    }

    char body[512] = {0};
    int received = httpd_req_recv(request, body, request->content_len);
    if (received <= 0) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Could not read setup request");
        return ESP_FAIL;
    }
    body[received] = '\0';

    char ssid[33] = {0};
    char password[64] = {0};
    char backend_uri[192] = {0};
    if (!form_value(body, "ssid", ssid, sizeof(ssid)) || ssid[0] == '\0' ||
        !form_value(body, "password", password, sizeof(password)) ||
        !form_value(body, "backend", backend_uri, sizeof(backend_uri))) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Wi-Fi name is required");
        return ESP_FAIL;
    }
    if (backend_uri[0] != '\0' && strncmp(backend_uri, "ws://", 5) != 0 &&
        strncmp(backend_uri, "wss://", 6) != 0) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST,
                            "Backend URL must begin with ws:// or wss://");
        return ESP_FAIL;
    }

    ESP_RETURN_ON_ERROR(save_credentials(ssid, password, backend_uri), TAG,
                        "Could not save Wi-Fi credentials");

    friday_ui_set_network("WI-FI SAVED", "Restarting to join your network...");
    httpd_resp_set_type(request, "text/html");
    esp_err_t result = httpd_resp_sendstr(request,
        "<!doctype html><meta name=viewport content='width=device-width'><body style='font:18px system-ui;"
        "background:#05090b;color:#edf8f7;padding:40px'><h1>Friday is connecting.</h1>"
        "<p>You may return to your normal Wi-Fi network.</p></body>");
    xTaskCreate(restart_task, "friday_restart", 2048, NULL, 5, NULL);
    return result;
}

static esp_err_t start_setup_server(void)
{
    if (setup_server != NULL) {
        return ESP_OK;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    ESP_RETURN_ON_ERROR(httpd_start(&setup_server, &config), TAG, "Setup server failed");

    const httpd_uri_t root = {
        .uri = "/",
        .method = HTTP_GET,
        .handler = root_handler,
    };
    const httpd_uri_t configure = {
        .uri = "/configure",
        .method = HTTP_POST,
        .handler = configure_handler,
    };
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(setup_server, &root), TAG,
                        "Could not register setup page");
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(setup_server, &configure), TAG,
                        "Could not register setup form");
    return ESP_OK;
}

static void reset_credentials_task(void *argument)
{
    (void)argument;
    friday_ui_set_network("WI-FI SETUP", "Saved network unavailable; restarting setup...");
    clear_credentials();
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

static void wifi_event_handler(void *argument, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    (void)argument;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START && has_credentials) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED &&
               has_credentials) {
        if (wifi_events != NULL) {
            xEventGroupClearBits(wifi_events, FRIDAY_WIFI_CONNECTED_BIT);
        }
        if (retry_count++ < FRIDAY_MAX_RETRIES) {
            ESP_LOGW(TAG, "Wi-Fi disconnected; retry %d/%d", retry_count, FRIDAY_MAX_RETRIES);
            esp_wifi_connect();
        } else {
            has_credentials = false;
            xTaskCreate(reset_credentials_task, "friday_wifi_reset", 3072, NULL, 5, NULL);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        char detail[64];
        snprintf(detail, sizeof(detail), "Connected at " IPSTR, IP2STR(&event->ip_info.ip));
        retry_count = 0;
        if (wifi_events != NULL) {
            xEventGroupSetBits(wifi_events, FRIDAY_WIFI_CONNECTED_BIT);
        }
        ESP_LOGI(TAG, "%s", detail);
        friday_ui_set_network("WI-FI ONLINE", detail);
    }
}

esp_err_t friday_wifi_start(void)
{
    char ssid[33] = {0};
    char password[64] = {0};
    esp_err_t saved_result = load_credentials(ssid, sizeof(ssid), password, sizeof(password));
    has_credentials = saved_result == ESP_OK && ssid[0] != '\0';
    if (saved_result != ESP_OK && saved_result != ESP_ERR_NOT_FOUND &&
        saved_result != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGW(TAG, "Ignoring unreadable Wi-Fi settings: %s", esp_err_to_name(saved_result));
    }

    wifi_events = xEventGroupCreate();
    ESP_RETURN_ON_FALSE(wifi_events != NULL, ESP_ERR_NO_MEM, TAG,
                        "Could not create Wi-Fi event group");
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "Network stack init failed");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "Event loop init failed");
    if (has_credentials) {
        esp_netif_create_default_wifi_sta();
    } else {
        esp_netif_create_default_wifi_ap();
    }

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init), TAG, "Wi-Fi init failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_FLASH), TAG,
                        "Wi-Fi storage setup failed");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                    wifi_event_handler, NULL), TAG,
                        "Wi-Fi event handler failed");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                    wifi_event_handler, NULL), TAG,
                        "IP event handler failed");

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(has_credentials ? WIFI_MODE_STA : WIFI_MODE_AP),
                        TAG, "Could not set Wi-Fi mode");
    if (has_credentials) {
        wifi_config_t station = {0};
        strlcpy((char *)station.sta.ssid, ssid, sizeof(station.sta.ssid));
        strlcpy((char *)station.sta.password, password, sizeof(station.sta.password));
        station.sta.threshold.authmode = password[0] == '\0' ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
        station.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
        ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &station), TAG,
                            "Could not configure Wi-Fi station");
    } else {
        wifi_config_t access_point = {0};
        strlcpy((char *)access_point.ap.ssid, FRIDAY_SETUP_SSID, sizeof(access_point.ap.ssid));
        strlcpy((char *)access_point.ap.password, FRIDAY_SETUP_PASSWORD,
                sizeof(access_point.ap.password));
        access_point.ap.ssid_len = strlen(FRIDAY_SETUP_SSID);
        access_point.ap.channel = 1;
        access_point.ap.max_connection = 2;
        access_point.ap.authmode = WIFI_AUTH_WPA2_PSK;
        ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_AP, &access_point), TAG,
                            "Could not configure setup AP");
    }
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "Could not start Wi-Fi");

    if (has_credentials) {
        friday_ui_set_network("WI-FI CONNECTING", "Using saved credentials");
    } else {
        ESP_RETURN_ON_ERROR(start_setup_server(), TAG, "Could not start setup page");
        ESP_LOGI(TAG, "Provisioning available: SSID=%s URL=http://192.168.4.1",
                 FRIDAY_SETUP_SSID);
        friday_ui_set_network("WI-FI SETUP",
                              "Join Friday-Setup (password: fridaysetup), then open 192.168.4.1");
    }
    return ESP_OK;
}

bool friday_wifi_wait_connected(TickType_t timeout)
{
    if (wifi_events == NULL) {
        return false;
    }
    EventBits_t bits = xEventGroupWaitBits(
        wifi_events, FRIDAY_WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, timeout);
    return (bits & FRIDAY_WIFI_CONNECTED_BIT) != 0;
}

esp_err_t friday_wifi_get_backend_uri(char *uri, size_t uri_size)
{
    if (uri == NULL || uri_size == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    uri[0] = '\0';
    nvs_handle_t handle;
    esp_err_t result = nvs_open("friday_wifi", NVS_READONLY, &handle);
    if (result != ESP_OK) {
        return result;
    }
    size_t stored_size = uri_size;
    result = nvs_get_str(handle, "backend_uri", uri, &stored_size);
    nvs_close(handle);
    return result;
}
