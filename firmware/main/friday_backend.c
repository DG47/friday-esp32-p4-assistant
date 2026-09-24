#include "friday_backend.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"

#include "friday_state.h"
#include "friday_ui.h"

static const char *TAG = "friday_backend";
static esp_websocket_client_handle_t client;
static char *message_buffer;
static size_t message_size;

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

    if (strcmp(type, "ready") == 0) {
        friday_ui_set_backend("FRIDAY ONLINE");
        friday_ui_set_state(parse_state(json_string(root, "state")));
        handle_surface(cJSON_GetObjectItemCaseSensitive(root, "surface"));
    } else if (strcmp(type, "state") == 0) {
        friday_ui_set_state(parse_state(json_string(root, "state")));
    } else if (strcmp(type, "surface") == 0) {
        handle_surface(cJSON_GetObjectItemCaseSensitive(root, "surface"));
    } else if (strcmp(type, "assistant.text") == 0) {
        friday_ui_set_response(json_string(root, "text"));
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
        const char hello[] =
            "{\"type\":\"hello\",\"protocol\":1,"
            "\"capabilities\":[\"display\",\"touch\",\"microphone\"]}";
        ESP_LOGI(TAG, "Connected to Friday backend");
        friday_ui_set_backend("FRIDAY ONLINE");
        esp_websocket_client_send_text(client, hello, strlen(hello), pdMS_TO_TICKS(1000));
        break;
    }
    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "Backend disconnected; automatic reconnect pending");
        friday_ui_set_backend("FRIDAY RECONNECTING");
        break;
    case WEBSOCKET_EVENT_DATA:
        if (event->op_code == 0x1 || event->op_code == 0x0) {
            receive_fragment(event);
        }
        break;
    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGW(TAG, "Backend connection error");
        friday_ui_set_backend("FRIDAY OFFLINE");
        break;
    default:
        break;
    }
}

esp_err_t friday_backend_start(void)
{
    const esp_websocket_client_config_t config = {
        .uri = CONFIG_FRIDAY_BACKEND_WS_URI,
        .buffer_size = 2048,
        .reconnect_timeout_ms = 3000,
        .network_timeout_ms = 5000,
        .ping_interval_sec = 15,
        .keep_alive_enable = true,
    };

    ESP_LOGI(TAG, "Backend endpoint: %s", CONFIG_FRIDAY_BACKEND_WS_URI);
    client = esp_websocket_client_init(&config);
    ESP_RETURN_ON_FALSE(client != NULL, ESP_ERR_NO_MEM, TAG,
                        "Could not create WebSocket client");
    ESP_RETURN_ON_ERROR(
        esp_websocket_register_events(client, WEBSOCKET_EVENT_ANY,
                                      websocket_event_handler, NULL),
        TAG, "Could not register WebSocket events");
    return esp_websocket_client_start(client);
}

void friday_backend_activate(void)
{
    if (client == NULL || !esp_websocket_client_is_connected(client)) {
        ESP_LOGW(TAG, "Touch activation ignored while backend is offline");
        return;
    }

    const char activation[] = "{\"type\":\"activate\",\"source\":\"touch\"}";
    int sent = esp_websocket_client_send_text(
        client, activation, strlen(activation), pdMS_TO_TICKS(250));
    if (sent < 0) {
        ESP_LOGW(TAG, "Could not send touch activation");
    }
}
