#include "friday_error_scene.h"
#include <string.h>

/* Routine voice failures must leave touch controls available. Pairing/security
 * errors retain their separate, explicit handling in the protocol receiver. */
cJSON *friday_error_scene(const char *code)
{
    if (code == NULL || (strcmp(code, "transcription_failed") &&
        strcmp(code, "bad_audio") && strcmp(code, "wake_unavailable"))) return NULL;
    cJSON *scene = cJSON_Parse("{\"kind\":\"message\",\"title\":\"Try again\","
        "\"subtitle\":\"I did not hear a clear request. Tap the orb to retry, or use local controls.\","
        "\"actions\":[{\"id\":\"status\",\"label\":\"Local status\"},"
        "{\"id\":\"reminders\",\"label\":\"Reminders\"},{\"id\":\"dismiss\",\"label\":\"Dismiss\"}],"
        "\"expires_ms\":30000}");
    if (scene != NULL && strcmp(code, "wake_unavailable") == 0) {
        cJSON_ReplaceItemInObject(scene, "title", cJSON_CreateString("Voice unavailable"));
        cJSON_ReplaceItemInObject(scene, "subtitle", cJSON_CreateString("Local touch controls work while voice is unavailable."));
    }
    return scene;
}
