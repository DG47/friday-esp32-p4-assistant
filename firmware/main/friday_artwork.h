#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "cJSON.h"
#define FRIDAY_ARTWORK_WIDTH 192
#define FRIDAY_ARTWORK_BYTES (FRIDAY_ARTWORK_WIDTH * FRIDAY_ARTWORK_WIDTH * 2)
#define FRIDAY_ARTWORK_BASE64_BYTES (FRIDAY_ARTWORK_BYTES / 3 * 4)
#define FRIDAY_MEDIA_MESSAGE_MAX 131072
/* One retained cover; only accepted source-owned frames may call update. */
typedef struct { uint8_t *pixels; char track_key[129]; bool ready; uint64_t generation; char sha256[65]; } friday_artwork_t;
void friday_artwork_clear(friday_artwork_t *cache);
bool friday_artwork_update(friday_artwork_t *cache, const cJSON *surface);
