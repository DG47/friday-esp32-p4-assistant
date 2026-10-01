#include <assert.h>
#include <string.h>
#include "friday_error_scene.h"
int main(void) {
    const char *codes[] = {"transcription_failed", "bad_audio", "wake_unavailable"};
    for (int i = 0; i < 3; i++) {
        cJSON *scene = friday_error_scene(codes[i]);
        assert(scene != NULL);
        assert(strcmp(cJSON_GetObjectItem(scene, "kind")->valuestring, "message") == 0);
        assert(strcmp(cJSON_GetObjectItem(scene, "title")->valuestring, "ERROR") != 0);
        assert(cJSON_GetObjectItem(scene, "expires_ms")->valueint == 30000);
        cJSON *actions = cJSON_GetObjectItem(scene, "actions");
        assert(cJSON_GetArraySize(actions) == 3);
        assert(strcmp(cJSON_GetObjectItem(cJSON_GetArrayItem(actions, 0), "id")->valuestring, "status") == 0);
        assert(strcmp(cJSON_GetObjectItem(cJSON_GetArrayItem(actions, 1), "id")->valuestring, "reminders") == 0);
        assert(strcmp(cJSON_GetObjectItem(cJSON_GetArrayItem(actions, 2), "id")->valuestring, "dismiss") == 0);
        cJSON_Delete(scene);
    }
    assert(friday_error_scene(NULL) == NULL);
    assert(friday_error_scene("pairing_required") == NULL);
    assert(friday_error_scene("unknown") == NULL);
    return 0;
}
