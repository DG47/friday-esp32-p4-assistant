#pragma once
#include "lvgl.h"
#include "friday_state.h"
/* Original procedural light ribbons; no raster assets, framebuffer, or fonts. */
lv_obj_t *friday_presence_create(lv_obj_t *parent);
void friday_presence_set_state(lv_obj_t *object, friday_state_t state);
void friday_presence_set_level(lv_obj_t *object, int32_t rms);
void friday_presence_set_compact(lv_obj_t *object, bool compact);

/* One bounded cue for an actual tool completion. */
void friday_presence_complete(lv_obj_t *object);

void friday_presence_set_material(lv_obj_t *object, bool material);
