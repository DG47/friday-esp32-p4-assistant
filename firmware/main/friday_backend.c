#include "friday_backend.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include "cJSON.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "nvs.h"

#include "friday_state.h"
#include "friday_ui.h"
#include "friday_wifi.h"

static const char *TAG = "friday_backend";
#define FRIDAY_AUDIO_SEND_TIMEOUT_MS 1000
#define FRIDAY_DEVICE_ID "friday-p3a"
#define FRIDAY_BACKEND_URI_MAX 192
#define FRIDAY_DEVICE_TOKEN_BYTES 32
#define FRIDAY_DEVICE_TOKEN_LENGTH (FRIDAY_DEVICE_TOKEN_BYTES * 2)
#define FRIDAY_DISCOVERY_ATTEMPTS 3

static esp_websocket_client_handle_t client;
static char *message_buffer;
static size_t message_size;
static SemaphoreHandle_t send_lock;
static volatile bool audio_active;
static volatile bool wake_requested;
static volatile bool backend_ready;
static char backend_uri[FRIDAY_BACKEND_URI_MAX];
static char device_token[FRIDAY_DEVICE_TOKEN_LENGTH + 1];

static bool valid_backend_uri(const char *uri)
{
    return uri != NULL &&
           (strncmp(uri, "ws://", 5) == 0 || strncmp(uri, "wss://", 6) == 0);
}

static esp_err_t load_or_create_device_token(void)
{
    nvs_handle_t handle;
    ESP_RETURN_ON_ERROR(nvs_open("friday_auth", NVS_READWRITE, &handle), TAG,
                        "Could not open device identity");
    size_t token_size = sizeof(device_token);
    esp_err_t result = nvs_get_str(handle, "device_token", device_token, &token_size);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        uint8_t random_bytes[FRIDAY_DEVICE_TOKEN_BYTES];
        static const char hex[] = "0123456789abcdef";
        esp_fill_random(random_bytes, sizeof(random_bytes));
        for (size_t index = 0; index < sizeof(random_bytes); index++) {
            device_token[index * 2] = hex[random_bytes[index] >> 4];
            device_token[index * 2 + 1] = hex[random_bytes[index] & 0x0f];
        }
        device_token[FRIDAY_DEVICE_TOKEN_LENGTH] = '\0';
        result = nvs_set_str(handle, "device_token", device_token);
        if (result == ESP_OK) {
            result = nvs_commit(handle);
        }
    }
    nvs_close(handle);
    return result;
}

static bool discover_backend(char *uri, size_t uri_size)
{
    int discovery_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (discovery_socket < 0) {
        return false;
    }

    int enabled = 1;
    struct timeval timeout = {.tv_sec = 2, .tv_usec = 0};
    setsockopt(discovery_socket, SOL_SOCKET, SO_BROADCAST, &enabled, sizeof(enabled));
    setsockopt(discovery_socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    struct sockaddr_in destination = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_FRIDAY_DISCOVERY_PORT),
        .sin_addr.s_addr = htonl(INADDR_BROADCAST),
    };
    esp_netif_t *station = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip_info;
    if (station != NULL && esp_netif_get_ip_info(station, &ip_info) == ESP_OK) {
        destination.sin_addr.s_addr = ip_info.ip.addr | ~ip_info.netmask.addr;
    }

    char request[96];
    snprintf(request, sizeof(request), "FRIDAY_DISCOVER_V1 %s", FRIDAY_DEVICE_ID);
    bool found = false;
    for (int attempt = 0; attempt < FRIDAY_DISCOVERY_ATTEMPTS && !found; attempt++) {
        if (sendto(discovery_socket, request, strlen(request), 0,
                   (struct sockaddr *)&destination, sizeof(destination)) < 0) {
            continue;
        }
        char response[256] = {0};
        int received = recvfrom(discovery_socket, response, sizeof(response) - 1,
                                0, NULL, NULL);
        if (received <= 0) {
            continue;
        }
        response[received] = '\0';
        cJSON *root = cJSON_Parse(response);
        const cJSON *url = root != NULL
                               ? cJSON_GetObjectItemCaseSensitive(root, "ws_url")
                               : NULL;
        if (cJSON_IsString(url) && valid_backend_uri(url->valuestring) &&
            strlen(url->valuestring) < uri_size) {
            strlcpy(uri, url->valuestring, uri_size);
            found = true;
        }
        cJSON_Delete(root);
    }
    close(discovery_socket);
    return found;
}

static int send_text(const char *message, TickType_t timeout)
{
    if (client == NULL || !esp_websocket_client_is_connected(client) ||
        send_lock == NULL || xSemaphoreTake(send_lock, timeout) != pdTRUE) {
        return -1;
    }
    int sent = esp_websocket_client_send_text(client, message, strlen(message), timeout);
    xSemaphoreGive(send_lock);
    return sent;
}

static friday_state_t parse_state(const char *name)
{
    if (name == NULL) {
        return FRIDAY_STATE_IDLE;
    }
    if (strcmp(name, "listening") == 0) {
        return FRIDAY_STATE_LISTENING;
    }
    if (strcmp(name, "thinking") == 0) {
        return FRIDAY_STATE_THINKING;
    }
    if (strcmp(name, "speaking") == 0) {
        return FRIDAY_STATE_SPEAKING;
    }
    if (strcmp(name, "alert") == 0) {
        return FRIDAY_STATE_ALERT;
    }
    return FRIDAY_STATE_IDLE;
}

static const char *json_string(const cJSON *object, const char *name)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    return cJSON_IsString(value) ? value->valuestring : NULL;
}

static void handle_surface(const cJSON *surface)
{
    if (!cJSON_IsObject(surface)) {
        return;
    }
    friday_ui_set_surface(
        json_string(surface, "kind"),
        json_string(surface, "title"),
        json_string(surface, "subtitle"));
}

static void handle_message(const char *payload)
{
    cJSON *root = cJSON_Parse(payload);
    if (root == NULL) {
        ESP_LOGW(TAG, "Ignoring malformed backend message");
        return;
    }

    const char *type = json_string(root, "type");
    if (type == NULL) {
        cJSON_Delete(root);
        return;
    }
    ESP_LOGI(TAG, "Received backend message: %s", type);

    if (strcmp(type, "ready") == 0) {
        backend_ready = true;
        friday_ui_set_backend("FRIDAY ONLINE");
        friday_ui_set_state(parse_state(json_string(root, "state")));
        handle_surface(cJSON_GetObjectItemCaseSensitive(root, "surface"));
        if (send_text("{\"type\":\"wake.start\"}", pdMS_TO_TICKS(1000)) < 0) {
            ESP_LOGW(TAG, "Could not start Friday wake detection");
        }
    } else if (strcmp(type, "state") == 0) {
        friday_ui_set_state(parse_state(json_string(root, "state")));
    } else if (strcmp(type, "wake.detected") == 0) {
        wake_requested = true;
    } else if (strcmp(type, "reminder.alert") == 0) {
        const cJSON *reminder_id = cJSON_GetObjectItemCaseSensitive(
            root, "reminder_id");
        handle_surface(cJSON_GetObjectItemCaseSensitive(root, "surface"));
        friday_ui_set_response(json_string(root, "text"));
        friday_ui_set_state(FRIDAY_STATE_ALERT);
        if (cJSON_IsNumber(reminder_id)) {
            char acknowledgement[80];
            int length = snprintf(
                acknowledgement, sizeof(acknowledgement),
                "{\"type\":\"reminder.ack\",\"reminder_id\":%d}",
                reminder_id->valueint);
            if (length <= 0 || (size_t)length >= sizeof(acknowledgement) ||
                send_text(acknowledgement, pdMS_TO_TICKS(1000)) < 0) {
                ESP_LOGW(TAG, "Could not acknowledge reminder");
            }
        }
    } else if (strcmp(type, "surface") == 0) {
        handle_surface(cJSON_GetObjectItemCaseSensitive(root, "surface"));
    } else if (strcmp(type, "assistant.text") == 0) {
        const char *text = json_string(root, "text");
        ESP_LOGI(TAG, "Rendering assistant response (%u bytes)",
                 text != NULL ? (unsigned int)strlen(text) : 0);
        friday_ui_set_response(text);
    } else if (strcmp(type, "tool.activity") == 0) {
        friday_ui_set_tool(json_string(root, "name"), json_string(root, "status"));
    } else if (strcmp(type, "error") == 0) {
        friday_ui_set_surface("ERROR", "Friday", json_string(root, "message"));
        friday_ui_set_state(FRIDAY_STATE_ALERT);
    }

    cJSON_Delete(root);
}

static void receive_fragment(const esp_websocket_event_data_t *event)
{
    if (event->payload_offset == 0) {
        free(message_buffer);
        message_buffer = calloc((size_t)event->payload_len + 1, 1);
        message_size = (size_t)event->payload_len;
    }

    if (message_buffer == NULL || event->payload_offset < 0 || event->data_len < 0 ||
        (size_t)event->payload_offset + (size_t)event->data_len > message_size) {
        ESP_LOGW(TAG, "Dropping oversized backend message");
        free(message_buffer);
        message_buffer = NULL;
        message_size = 0;
        return;
    }

    memcpy(message_buffer + event->payload_offset, event->data_ptr, event->data_len);
    if ((size_t)event->payload_offset + (size_t)event->data_len == message_size) {
        handle_message(message_buffer);
        free(message_buffer);
        message_buffer = NULL;
        message_size = 0;
    }
}

static void websocket_event_handler(void *handler_args, esp_event_base_t base,
                                    int32_t event_id, void *event_data)
{
    (void)handler_args;
    (void)base;
    esp_websocket_event_data_t *event = event_data;

    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED: {
        backend_ready = false;
        char hello[256];
        int hello_length = snprintf(
            hello, sizeof(hello),
            "{\"type\":\"hello\",\"protocol\":1,"
            "\"device_token\":\"%s\","
            "\"capabilities\":[\"display\",\"touch\",\"microphone\","
            "\"reminder_ack\"]}",
            device_token);
        ESP_LOGI(TAG, "Connected to Friday backend");
        friday_ui_set_backend("FRIDAY AUTHENTICATING");
        if (hello_length <= 0 || (size_t)hello_length >= sizeof(hello) ||
            send_text(hello, pdMS_TO_TICKS(1000)) < 0) {
            ESP_LOGW(TAG, "Could not send authenticated hello");
        }
        break;
    }
    case WEBSOCKET_EVENT_DISCONNECTED:
        backend_ready = false;
        audio_active = false;
        wake_requested = false;
        ESP_LOGW(TAG, "Backend disconnected; automatic reconnect pending");
        friday_ui_set_backend("FRIDAY RECONNECTING");
        break;
    case WEBSOCKET_EVENT_DATA:
        if (event->op_code == 0x1 || event->op_code == 0x0) {
            receive_fragment(event);
        }
        break;
    case WEBSOCKET_EVENT_ERROR:
        backend_ready = false;
        ESP_LOGW(TAG, "Backend connection error");
        friday_ui_set_backend("FRIDAY OFFLINE");
        break;
    default:
        break;
    }
}

static void backend_start_task(void *argument)
{
    (void)argument;
    if (!friday_wifi_wait_connected(portMAX_DELAY)) {
        vTaskDelete(NULL);
        return;
    }

    esp_err_t saved_uri = friday_wifi_get_backend_uri(
        backend_uri, sizeof(backend_uri));
    if (saved_uri == ESP_OK && valid_backend_uri(backend_uri)) {
        ESP_LOGI(TAG, "Using configured backend endpoint");
    } else if (discover_backend(backend_uri, sizeof(backend_uri))) {
        ESP_LOGI(TAG, "Discovered Friday backend on the local network");
    } else {
        strlcpy(backend_uri, CONFIG_FRIDAY_BACKEND_WS_URI, sizeof(backend_uri));
        ESP_LOGW(TAG, "Discovery unavailable; using fallback backend endpoint");
    }

    esp_err_t token_result = load_or_create_device_token();
    if (token_result != ESP_OK) {
        ESP_LOGE(TAG, "Device identity unavailable: %s",
                 esp_err_to_name(token_result));
        friday_ui_set_backend("PAIRING ERROR");
        vTaskDelete(NULL);
        return;
    }

    const esp_websocket_client_config_t config = {
        .uri = backend_uri,
        .buffer_size = 2048,
        .enable_close_reconnect = true,
        .reconnect_timeout_ms = 3000,
        .network_timeout_ms = 5000,
        .ping_interval_sec = 15,
        .keep_alive_enable = true,
    };

    ESP_LOGI(TAG, "Backend endpoint: %s", backend_uri);
    client = esp_websocket_client_init(&config);
    if (client == NULL ||
        esp_websocket_register_events(client, WEBSOCKET_EVENT_ANY,
                                      websocket_event_handler, NULL) != ESP_OK ||
        esp_websocket_client_start(client) != ESP_OK) {
        ESP_LOGE(TAG, "Could not start WebSocket client");
        friday_ui_set_backend("FRIDAY OFFLINE");
    }
    vTaskDelete(NULL);
}

esp_err_t friday_backend_start(void)
{
    send_lock = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(send_lock != NULL, ESP_ERR_NO_MEM, TAG,
                        "Could not create WebSocket send lock");
    BaseType_t created = xTaskCreate(
        backend_start_task, "friday_backend_start", 6144, NULL, 6, NULL);
    return created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

bool friday_backend_toggle_audio(void)
{
    if (audio_active) {
        friday_backend_end_audio();
        return false;
    }

    return friday_backend_begin_audio("touch");
}

bool friday_backend_begin_audio(const char *source)
{
    if (!backend_ready || client == NULL ||
        !esp_websocket_client_is_connected(client)) {
        ESP_LOGW(TAG, "Activation ignored while backend is offline");
        return false;
    }
    if (audio_active) {
        return false;
    }

    const char *activation =
        source != NULL && strcmp(source, "wake_word") == 0
            ? "{\"type\":\"activate\",\"source\":\"wake_word\"}"
            : "{\"type\":\"activate\",\"source\":\"touch\"}";
    const char audio_start[] =
        "{\"type\":\"audio.start\",\"format\":\"pcm_s16le\","
        "\"sample_rate\":12000,\"channels\":1}";
    if (send_text(activation, pdMS_TO_TICKS(250)) < 0 ||
        send_text(audio_start, pdMS_TO_TICKS(250)) < 0) {
        ESP_LOGW(TAG, "Could not send %s activation",
                 source != NULL ? source : "touch");
        return false;
    }
    audio_active = true;
    ESP_LOGI(TAG, "Audio turn started by %s", source != NULL ? source : "touch");
    return true;
}

bool friday_backend_audio_active(void)
{
    return audio_active;
}

bool friday_backend_take_wake_request(void)
{
    bool requested = wake_requested;
    wake_requested = false;
    return requested;
}

static esp_err_t send_audio_binary(const int16_t *samples, size_t sample_count)
{
    if (client == NULL || !esp_websocket_client_is_connected(client)) {
        return ESP_ERR_INVALID_STATE;
    }
    if (send_lock == NULL ||
        xSemaphoreTake(send_lock, pdMS_TO_TICKS(FRIDAY_AUDIO_SEND_TIMEOUT_MS)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    int sent = esp_websocket_client_send_bin(
        client, (const char *)samples, sample_count * sizeof(*samples),
        pdMS_TO_TICKS(FRIDAY_AUDIO_SEND_TIMEOUT_MS));
    xSemaphoreGive(send_lock);
    return sent < 0 ? ESP_FAIL : ESP_OK;
}

esp_err_t friday_backend_send_audio(const int16_t *samples, size_t sample_count)
{
    if (!audio_active) {
        return ESP_ERR_INVALID_STATE;
    }
    return send_audio_binary(samples, sample_count);
}

esp_err_t friday_backend_send_idle_audio(const int16_t *samples, size_t sample_count)
{
    if (audio_active || !backend_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    return send_audio_binary(samples, sample_count);
}

void friday_backend_end_audio(void)
{
    if (!audio_active) {
        return;
    }
    audio_active = false;
    const char audio_end[] = "{\"type\":\"audio.end\"}";
    if (send_text(audio_end, pdMS_TO_TICKS(500)) < 0) {
        ESP_LOGW(TAG, "Could not finish audio turn");
        return;
    }
    if (send_text("{\"type\":\"wake.start\"}", pdMS_TO_TICKS(500)) < 0) {
        ESP_LOGW(TAG, "Could not resume Friday wake detection");
    }
    ESP_LOGI(TAG, "Audio turn finished");
}
