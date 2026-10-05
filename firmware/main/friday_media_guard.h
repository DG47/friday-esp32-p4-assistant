#pragma once
#include <stdbool.h>
#include <stdint.h>
/* Caller serializes under the display lock, including acceptance and render. */
typedef struct { char request[129]; char session[25]; uint64_t sequence; } friday_media_guard_t;
void friday_media_clear(friday_media_guard_t *guard);
bool friday_media_begin(friday_media_guard_t *guard, const char *request);
bool friday_media_accept(friday_media_guard_t *guard, const char *request, const char *session, double sequence);
