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
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "nvs.h"

#include "friday_state.h"
#include "friday_ui.h"
#include "friday_artwork.h"
#include "friday_audio.h"
#include "friday_error_scene.h"
#include "friday_wifi.h"

static const char *TAG = "friday_backend";
static bool scene_action_pending;
extern const char friday_backend_ca_pem_start[] asm("_binary_friday_backend_ca_pem_start");
#define FRIDAY_AUDIO_SEND_TIMEOUT_MS 1000
#define FRIDAY_IDLE_AUDIO_SEND_TIMEOUT_MS 2000
#define FRIDAY_DEVICE_ID "friday-p3a"
#define FRIDAY_BACKEND_URI_MAX 192
#define FRIDAY_DEVICE_TOKEN_BYTES 32
#define FRIDAY_DEVICE_TOKEN_LENGTH (FRIDAY_DEVICE_TOKEN_BYTES * 2)
#define FRIDAY_DISCOVERY_ATTEMPTS 3
/* One 500 ms, 12 kHz mono s16 idle-audio batch is 12,000 bytes. Keep the
 * WebSocket buffer large enough to send it as one frame instead of six. */
#define FRIDAY_WEBSOCKET_BUFFER_SIZE 12288

static esp_websocket_client_handle_t client;
static char *message_buffer;
static size_t message_size;
static size_t message_offset;
static SemaphoreHandle_t send_lock;
static volatile bool audio_active;
static volatile bool wake_requested;
static volatile bool backend_ready;
static char backend_uri[FRIDAY_BACKEND_URI_MAX];
static char device_token[FRIDAY_DEVICE_TOKEN_LENGTH + 1];
static bool persist_backend_uri;
static volatile unsigned int reconnect_failures;
static volatile bool rediscovery_pending;
static esp_timer_handle_t reminder_expiry_timer;
static volatile int pending_reminder_id = -1;

static void reminder_expired(void *argument)
{
    (void)argument;
    if(friday_ui_media_suspended()) {
        ESP_LOGI(TAG,"Protected reminder remains visible until acknowledgement");return;
    }
    if (pending_reminder_id < 0) {
        return;
    }
    pending_reminder_id = -1;
    friday_ui_set_surface("AMBIENT", "FRIDAY", "Ready when needed.");
    friday_ui_set_state(FRIDAY_STATE_IDLE);
    ESP_LOGI(TAG, "Reminder alert expired without acknowledgement");
}

static void restart_after_identity_change(void *argument)
{
    (void)argument;
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

static bool clear_device_token(void)
{
    nvs_handle_t handle;
    if (nvs_open("friday_auth", NVS_READWRITE, &handle) != ESP_OK) {
        return false;
    }
    esp_err_t result = nvs_erase_key(handle, "device_token");
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        result = ESP_OK;
    }
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result == ESP_OK;
}

static bool valid_backend_uri(const char *uri)
{
    return uri != NULL && strncmp(uri, "wss://", 6) == 0;
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

static void backend_rediscovery_task(void *argument)
{
    (void)argument;
    char discovered_uri[FRIDAY_BACKEND_URI_MAX] = {0};

    ESP_LOGW(TAG, "Saved backend remains unavailable; retrying discovery");
    friday_ui_set_backend("FRIDAY SEARCHING");
    if (!discover_backend(discovered_uri, sizeof(discovered_uri))) {
        ESP_LOGW(TAG, "Backend rediscovery did not find a trusted endpoint");
        reconnect_failures = 0;
        rediscovery_pending = false;
        vTaskDelete(NULL);
        return;
    }

    if (backend_ready) {
        ESP_LOGI(TAG, "Backend recovered while discovery was running");
        reconnect_failures = 0;
        rediscovery_pending = false;
        vTaskDelete(NULL);
        return;
    }

    esp_err_t stop_result = esp_websocket_client_stop(client);
    if (stop_result != ESP_OK) {
        ESP_LOGW(TAG, "Could not pause WebSocket for rediscovery: %s",
                 esp_err_to_name(stop_result));
        reconnect_failures = 0;
        rediscovery_pending = false;
        vTaskDelete(NULL);
        return;
    }

    esp_err_t uri_result = esp_websocket_client_set_uri(client, discovered_uri);
    if (uri_result != ESP_OK) {
        ESP_LOGE(TAG, "Could not apply discovered backend endpoint: %s",
                 esp_err_to_name(uri_result));
        esp_websocket_client_start(client);
        rediscovery_pending = false;
        vTaskDelete(NULL);
        return;
    }

    strlcpy(backend_uri, discovered_uri, sizeof(backend_uri));
    persist_backend_uri = true;
    reconnect_failures = 0;
    ESP_LOGI(TAG, "Retrying with discovered backend endpoint: %s", backend_uri);
    if (esp_websocket_client_start(client) != ESP_OK) {
        ESP_LOGE(TAG, "Could not restart WebSocket after rediscovery");
        friday_ui_set_backend("FRIDAY OFFLINE");
    }
    rediscovery_pending = false;
    vTaskDelete(NULL);
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

static bool resume_media(void)
{
    if(!backend_ready)return false;
    cJSON *root=friday_ui_media_resume_request();if(!root)return false;
    char *payload=cJSON_PrintUnformatted(root);bool sent=payload&&send_text(payload,pdMS_TO_TICKS(250))>=0;
    cJSON_free(payload);cJSON_Delete(root);friday_ui_media_resume_sent(sent);return sent;
}
bool friday_backend_scene_action(const char *action)
{
    if (!backend_ready || action == NULL || strlen(action) > 63) {
        return false;
    }
    if(!strcmp(action,"media.resume"))return resume_media();
    bool media_action = strcmp(action,"music") == 0 || strncmp(action,"musicctl:",9) == 0;
    /* Navigation pauses the source-owned view; an authenticated overlay binds it. */
    if(!strcmp(action,"dismiss"))friday_ui_media_clear();
    bool mutation = strncmp(action, "snooze:", 7) == 0 || strncmp(action, "complete:", 9) == 0 || strncmp(action, "cancel:", 7) == 0 || strncmp(action,"musicctl:",9) == 0;
    if (mutation && scene_action_pending) {
        return true;
    }
    cJSON *message = cJSON_CreateObject();
    if (message == NULL) {
        return false;
    }
    char request_id[40];
    snprintf(request_id, sizeof(request_id), "%08lx%08lx%08lx%08lx",
             (unsigned long)esp_random(), (unsigned long)esp_random(),
             (unsigned long)esp_random(), (unsigned long)esp_random());
    if (media_action) friday_ui_media_begin(request_id);
    cJSON_AddStringToObject(message, "type", "scene.action");
    cJSON_AddStringToObject(message, "action", action);
    cJSON_AddStringToObject(message, "request_id", request_id);
    char *encoded = cJSON_PrintUnformatted(message);
    bool sent = encoded != NULL && send_text(encoded, pdMS_TO_TICKS(250)) >= 0;
    cJSON_free(encoded);
    cJSON_Delete(message);
    if (!sent && media_action) friday_ui_media_clear();
    if(sent)friday_ui_media_note_action(action);
    if (sent && mutation) {
        scene_action_pending = true;
    }
    return sent;
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
    friday_ui_set_scene(surface);
}

static char accepted_results[64][33];
static unsigned int accepted_result_index;

static void handle_agent_result(const cJSON *root)
{
    const char *delivery_id = json_string(root, "delivery_id");
    const char *job_id = json_string(root, "job_id");
    const char *text = json_string(root, "text");
    const char *status = json_string(root, "status");
    const cJSON *surface = cJSON_GetObjectItemCaseSensitive(root, "surface");
    if (delivery_id == NULL || strlen(delivery_id) != 32 || strspn(delivery_id, "0123456789abcdef") != 32 ||
        job_id == NULL || job_id[0] == '\0' || strlen(job_id) > 128 || text == NULL || !cJSON_IsObject(surface) ||
        status == NULL || (strcmp(status, "completed") && strcmp(status, "failed") && strcmp(status, "expired") && strcmp(status, "late"))) return;
    bool duplicate = false;
    for (int index = 0; index < 64; index++) {
        if (strcmp(accepted_results[index], delivery_id) == 0) { duplicate = true; break; }
    }
    if (!duplicate) {
        if (!friday_ui_set_scene(surface) || !friday_ui_set_response(text)) return;
        memcpy(accepted_results[accepted_result_index++ % 64], delivery_id, 33);
        scene_action_pending = false;
    }
    cJSON *ack = cJSON_CreateObject();
    if (ack == NULL) return;
    cJSON_AddStringToObject(ack, "type", "result.ack");
    cJSON_AddStringToObject(ack, "job_id", job_id);
    cJSON_AddStringToObject(ack, "delivery_id", delivery_id);
    char *payload = cJSON_PrintUnformatted(ack);
    if (payload != NULL) { send_text(payload, pdMS_TO_TICKS(250)); free(payload); }
    cJSON_Delete(ack);
    if(!duplicate)friday_ui_media_overlay_completed(root);
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

    friday_ui_media_prepare_overlay(root);
    if(!strcmp(type,"media.resume.result")){friday_ui_media_resume_result(root);cJSON_Delete(root);return;}
    if (!friday_ui_media_suspended() && (strcmp(type,"agent.result") == 0 || strcmp(type,"reminder.alert") == 0 ||
        strcmp(type,"assistant.text") == 0 || strcmp(type,"error") == 0 ||
        (strcmp(type,"state") == 0 && json_string(root,"state") != NULL &&
         strcmp(json_string(root,"state"),"idle") != 0) ||
        (strcmp(type,"tool.activity") == 0 && json_string(root,"status") != NULL &&
         strcmp(json_string(root,"status"),"working") == 0))) friday_ui_media_clear();
    if (strcmp(type, "agent.result") == 0) {
        handle_agent_result(root);
    } else if (strncmp(type, "speech.", 7) == 0) {
        friday_audio_speech_message(root);
    } else if (strcmp(type, "ready") == 0) {
        backend_ready = true;
        scene_action_pending = false;
        reconnect_failures = 0;
        if (persist_backend_uri) {
            if (friday_wifi_set_backend_uri(backend_uri) == ESP_OK) {
                persist_backend_uri = false;
                ESP_LOGI(TAG, "Saved authenticated backend endpoint");
            } else {
                ESP_LOGW(TAG, "Could not save authenticated backend endpoint");
            }
        }
        friday_ui_set_backend("FRIDAY ONLINE");
        friday_ui_set_state(parse_state(json_string(root, "state")));
        if(friday_ui_media_visible())friday_ui_set_response("Connected. Refresh Music for current playback.");
        else {handle_surface(cJSON_GetObjectItemCaseSensitive(root,"surface"));friday_ui_set_response("Say Friday or touch the light. Hold for local views.");}
        if (send_text("{\"type\":\"wake.start\"}", pdMS_TO_TICKS(1000)) < 0) {
            ESP_LOGW(TAG, "Could not start Friday wake detection");
        }
    } else if (strcmp(type, "state") == 0) {
        friday_state_t next = parse_state(json_string(root, "state"));
        if (!(next == FRIDAY_STATE_SPEAKING && !friday_audio_playing()) && !audio_active) friday_ui_set_state(next);
        if(next==FRIDAY_STATE_IDLE&&friday_ui_media_overlay_needs_resume())resume_media();
    } else if (strcmp(type, "wake.detected") == 0) {
        wake_requested = true;
    } else if (strcmp(type, "reminder.alert") == 0) {
        const cJSON *reminder_id = cJSON_GetObjectItemCaseSensitive(
            root, "reminder_id");
        const cJSON *surface = cJSON_GetObjectItemCaseSensitive(root, "surface");
        handle_surface(surface);
        friday_ui_set_response(json_string(root, "text"));
        friday_ui_set_state(FRIDAY_STATE_ALERT);
        if (cJSON_IsNumber(reminder_id)) {
            pending_reminder_id = reminder_id->valueint;
            int expires_ms = 60000;
            const cJSON *expires = cJSON_IsObject(surface)
                                       ? cJSON_GetObjectItemCaseSensitive(surface, "expires_ms")
                                       : NULL;
            if (cJSON_IsNumber(expires) && expires->valueint >= 5000 &&
                expires->valueint <= 300000) {
                expires_ms = expires->valueint;
            }
            if (reminder_expiry_timer != NULL) {
                esp_timer_stop(reminder_expiry_timer);
                esp_timer_start_once(reminder_expiry_timer,
                                     (uint64_t)expires_ms * 1000U);
            }
        }
    } else if (strcmp(type, "identity.rotate") == 0 ||
               strcmp(type, "identity.revoked") == 0) {
        if (clear_device_token()) {
            friday_ui_set_backend(strcmp(type, "identity.rotate") == 0
                                      ? "FRIDAY ROTATING IDENTITY"
                                      : "FRIDAY IDENTITY REVOKED");
            xTaskCreate(restart_after_identity_change, "friday_identity_restart",
                        2048, NULL, 5, NULL);
        } else {
            ESP_LOGE(TAG, "Could not clear device identity");
        }
    } else if (strcmp(type, "surface") == 0) {
        if (cJSON_HasObjectItem(root,"media_request_id") || cJSON_HasObjectItem(root,"media_session_id") || cJSON_HasObjectItem(root,"media_sequence")) {
            if (friday_ui_set_media(root)) scene_action_pending = false;
        } else {
            handle_surface(cJSON_GetObjectItemCaseSensitive(root,"surface"));
            friday_ui_media_overlay_completed(root);
            if(friday_state_get()==FRIDAY_STATE_IDLE&&friday_ui_media_overlay_needs_resume())resume_media();
        }
    } else if (strcmp(type, "assistant.text") == 0) {
        scene_action_pending = false;
        const char *text = json_string(root, "text");
        ESP_LOGI(TAG, "Rendering assistant response (%u bytes)",
                 text != NULL ? (unsigned int)strlen(text) : 0);
        friday_ui_set_response(text);
    } else if (strcmp(type, "tool.activity") == 0) {
        friday_ui_set_tool(json_string(root, "name"), json_string(root, "status"));
    } else if (strcmp(type, "error") == 0) {
        scene_action_pending = false;
        const char *code = json_string(root, "code");
        if (code != NULL && strcmp(code, "speech_unavailable") == 0) {
            friday_ui_set_backend("VOICE UNAVAILABLE — TEXT READY");
            friday_ui_set_state(FRIDAY_STATE_IDLE);
            cJSON_Delete(root);
            return;
        }
        cJSON *recovery = friday_error_scene(code);
        if (recovery != NULL) {
            friday_ui_set_state(FRIDAY_STATE_IDLE);
            friday_ui_set_scene(recovery);
            cJSON_Delete(recovery);
            cJSON_Delete(root);
            return;
        }
        /* Alert state installs a generic message, so apply it before the
         * specific error surface rather than overwriting the useful detail. */
        friday_ui_set_state(FRIDAY_STATE_ALERT);
        if (code != NULL && strcmp(code, "pairing_required") == 0) {
            char pairing_message[32];
            snprintf(pairing_message, sizeof(pairing_message),
                     "Pairing code: %.4s-%.4s", device_token, device_token + 4);
            friday_ui_set_surface("PAIRING", "FRIDAY", pairing_message);
            ESP_LOGW(TAG, "Pairing required; code shown on the physical display");
        } else {
            friday_ui_set_surface("ERROR", "Friday", json_string(root, "message"));
        }
    }

    cJSON_Delete(root);
}

static void receive_fragment(const esp_websocket_event_data_t *event)
{
    if (event->payload_len < 0 || event->payload_len > FRIDAY_MEDIA_MESSAGE_MAX) {
        free(message_buffer); message_buffer = NULL; message_size = 0; message_offset = 0; return;
    }
    if (event->payload_offset == 0) {
        free(message_buffer);
        message_buffer = calloc((size_t)event->payload_len + 1, 1);
        message_size = (size_t)event->payload_len;message_offset=0;
    }

    if (message_buffer == NULL || event->payload_offset < 0 || event->data_len < 0 ||
        (size_t)event->payload_len != message_size || (size_t)event->payload_offset != message_offset ||
        (size_t)event->payload_offset + (size_t)event->data_len > message_size) {
        ESP_LOGW(TAG, "Dropping oversized backend message");
        free(message_buffer);
        message_buffer = NULL;
        message_size = 0;message_offset=0;
        return;
    }

    memcpy(message_buffer + event->payload_offset, event->data_ptr, event->data_len);message_offset+=(size_t)event->data_len;
    if ((size_t)event->payload_offset + (size_t)event->data_len == message_size) {
        handle_message(message_buffer);
        free(message_buffer);
        message_buffer = NULL;
        message_size = 0;message_offset=0;
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
        friday_ui_media_clear();
        backend_ready = false;
        char hello[512];
        int hello_length = snprintf(
            hello, sizeof(hello),
            "{\"type\":\"hello\",\"protocol\":1,"
            "\"device_token\":\"%s\","
            "\"capabilities\":[\"display\",\"touch\",\"microphone\","
            "\"reminder_ack\",\"identity_rotate\",\"result_ack\",\"media_updates_v1\",\"media_artwork_v1\",\"media_persistent_v1\"%s]}",
            device_token, friday_audio_speaker_ready() ? ",\"pcm_speech\"" : "");
        ESP_LOGI(TAG, "Connected to Friday backend");
        friday_ui_set_backend("FRIDAY AUTHENTICATING");
        if (hello_length <= 0 || (size_t)hello_length >= sizeof(hello) ||
            send_text(hello, pdMS_TO_TICKS(1000)) < 0) {
            ESP_LOGW(TAG, "Could not send authenticated hello");
        }
        break;
    }
    case WEBSOCKET_EVENT_DISCONNECTED:
        friday_ui_media_clear();
        backend_ready = false;
        audio_active = false;
        friday_audio_stop_speech();
        wake_requested = false;
        ESP_LOGW(TAG, "Backend disconnected; automatic reconnect pending");
        friday_ui_set_backend("FRIDAY RECONNECTING");
        if (!rediscovery_pending &&
            ++reconnect_failures >= CONFIG_FRIDAY_REDISCOVERY_FAILURES) {
            rediscovery_pending = true;
            if (xTaskCreate(backend_rediscovery_task, "friday_rediscovery",
                            6144, NULL, 5, NULL) != pdPASS) {
                rediscovery_pending = false;
                ESP_LOGE(TAG, "Could not start backend rediscovery task");
            }
        }
        break;
    case WEBSOCKET_EVENT_DATA:
        if (event->op_code == 0x1 || event->op_code == 0x0) {
            receive_fragment(event);
        }
        break;
    case WEBSOCKET_EVENT_ERROR:
        backend_ready = false;
        if (event->error_handle.error_type == WEBSOCKET_ERROR_TYPE_TCP_TRANSPORT) {
            ESP_LOGW(TAG,
                     "Backend transport error tls=%s stack=%d verify=%d "
                     "socket_errno=%d",
                     esp_err_to_name(event->error_handle.esp_tls_last_esp_err),
                     event->error_handle.esp_tls_stack_err,
                     event->error_handle.esp_tls_cert_verify_flags,
                     event->error_handle.esp_transport_sock_errno);
        } else if (event->error_handle.error_type ==
                   WEBSOCKET_ERROR_TYPE_HANDSHAKE) {
            ESP_LOGW(TAG, "Backend handshake failed with HTTP status %d",
                     event->error_handle.esp_ws_handshake_status_code);
        } else if (event->error_handle.error_type ==
                   WEBSOCKET_ERROR_TYPE_SERVER_CLOSE) {
            ESP_LOGW(TAG, "Backend closed WebSocket with status %d",
                     event->close_status_code);
        } else if (event->error_handle.error_type ==
                   WEBSOCKET_ERROR_TYPE_PONG_TIMEOUT) {
            ESP_LOGW(TAG, "Backend WebSocket pong timed out");
        } else {
            ESP_LOGW(TAG, "Backend connection error type=%d",
                     event->error_handle.error_type);
        }
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
        persist_backend_uri = true;
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
        .cert_pem = friday_backend_ca_pem_start,
        .buffer_size = FRIDAY_WEBSOCKET_BUFFER_SIZE,
        .task_stack = 12288,
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
    const esp_timer_create_args_t reminder_timer_args = {
        .callback = reminder_expired,
        .name = "friday_reminder_expiry",
    };
    ESP_RETURN_ON_ERROR(
        esp_timer_create(&reminder_timer_args, &reminder_expiry_timer),
        TAG, "Could not create reminder expiry timer");
    BaseType_t created = xTaskCreate(
        backend_start_task, "friday_backend_start", 6144, NULL, 6, NULL);
    return created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

friday_ui_activation_result_t friday_backend_toggle_audio(void)
{
    if (pending_reminder_id >= 0) {
        int reminder_id = pending_reminder_id;
        char acknowledgement[80];
        int length = snprintf(
            acknowledgement, sizeof(acknowledgement),
            "{\"type\":\"reminder.ack\",\"reminder_id\":%d}",
            reminder_id);
        if (length > 0 && (size_t)length < sizeof(acknowledgement) &&
            send_text(acknowledgement, pdMS_TO_TICKS(1000)) >= 0) {
            pending_reminder_id = -1;
            if (reminder_expiry_timer != NULL) {
                esp_timer_stop(reminder_expiry_timer);
            }
            friday_ui_media_overlay_resolved();resume_media();
            ESP_LOGI(TAG, "Reminder %d acknowledged by touch", reminder_id);
            return FRIDAY_UI_ACTIVATION_ACKNOWLEDGED;
        }
        ESP_LOGW(TAG, "Could not acknowledge reminder %d", reminder_id);
        return FRIDAY_UI_ACTIVATION_UNCHANGED;
    }
    if (audio_active) {
        friday_backend_end_audio();
        return FRIDAY_UI_ACTIVATION_IDLE;
    }

    return friday_backend_begin_audio("touch")
               ? FRIDAY_UI_ACTIVATION_RECORDING
               : FRIDAY_UI_ACTIVATION_IDLE;
}

bool friday_backend_begin_audio(const char *source)
{
    /* Voice activation may receive a bound priority overlay instead of dismissing music. */
    if (!backend_ready || client == NULL ||
        !esp_websocket_client_is_connected(client)) {
        ESP_LOGW(TAG, "Activation ignored while backend is offline");
        return false;
    }
    if (audio_active) {
        return false;
    }

    friday_audio_stop_speech();
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

static esp_err_t send_audio_binary(const int16_t *samples, size_t sample_count,
                                   int timeout_ms)
{
    if (client == NULL || !esp_websocket_client_is_connected(client)) {
        return ESP_ERR_INVALID_STATE;
    }
    if (send_lock == NULL ||
        xSemaphoreTake(send_lock, pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    int sent = esp_websocket_client_send_bin(
        client, (const char *)samples, sample_count * sizeof(*samples),
        pdMS_TO_TICKS(timeout_ms));
    xSemaphoreGive(send_lock);
    return sent < 0 ? ESP_FAIL : ESP_OK;
}

esp_err_t friday_backend_send_audio(const int16_t *samples, size_t sample_count)
{
    if (!audio_active) {
        return ESP_ERR_INVALID_STATE;
    }
    return send_audio_binary(samples, sample_count, FRIDAY_AUDIO_SEND_TIMEOUT_MS);
}

esp_err_t friday_backend_send_idle_audio(const int16_t *samples, size_t sample_count)
{
    if (audio_active || !backend_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    return send_audio_binary(samples, sample_count,
                             FRIDAY_IDLE_AUDIO_SEND_TIMEOUT_MS);
}

bool friday_backend_begin_wake_window(void)
{
    if (audio_active || !backend_ready) {
        return false;
    }
    return send_text("{\"type\":\"wake.start\"}", pdMS_TO_TICKS(500)) >= 0;
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

void friday_backend_speech_finished(const char *stream_id, bool success)
{
    if (stream_id == NULL || strlen(stream_id) != 32) return;
    char message[128];
    snprintf(message, sizeof(message), "{\"type\":\"speech.finished\",\"stream_id\":\"%.32s\",\"status\":\"%s\"}", stream_id, success ? "completed" : "failed");
    send_text(message, pdMS_TO_TICKS(250));
}
