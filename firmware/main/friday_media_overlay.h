#pragma once
#include "friday_media_guard.h"
#include "cJSON.h"
/* A priority overlay can suspend exactly one existing media owner. */
typedef struct {
    bool active,protected,resume_requested,ready;
    char request[129],session[25],id[65];
    uint64_t sequence;
} friday_media_overlay_t;
void friday_media_overlay_clear(friday_media_overlay_t *overlay);
bool friday_media_overlay_suspend(friday_media_overlay_t *overlay,friday_media_guard_t *guard,const cJSON *root,bool protected);
bool friday_media_overlay_result(friday_media_overlay_t *overlay,const cJSON *root);
cJSON *friday_media_overlay_resume(friday_media_overlay_t *overlay);
