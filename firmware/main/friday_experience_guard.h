#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"
typedef struct {
    char epoch[33], request[129], previous[129];
    uint32_t sequence, previous_sequence;
    bool enabled, finished, previous_finished, provisional;
} friday_experience_guard_t;
typedef enum {FRIDAY_EXPERIENCE_ACCEPT, FRIDAY_EXPERIENCE_REJECT, FRIDAY_EXPERIENCE_DISCARD_RESULT} friday_experience_accept_t;
bool friday_experience_session(friday_experience_guard_t *guard, const char *epoch);
bool friday_experience_begin(friday_experience_guard_t *guard, const char *request);
void friday_experience_send_failed(friday_experience_guard_t *guard, const char *request);
void friday_experience_cancel(friday_experience_guard_t *guard);
friday_experience_accept_t friday_experience_accept(friday_experience_guard_t *guard, const cJSON *packet);
