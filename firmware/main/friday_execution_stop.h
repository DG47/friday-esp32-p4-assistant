#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"
/* Fixed storage, no credentials or facts; callers serialize access. */
typedef enum {FR_EXEC_IDLE,FR_EXEC_AVAILABLE,FR_EXEC_PENDING,FR_EXEC_UNCONFIRMED,
    FR_EXEC_UNCERTAIN,FR_EXEC_STOPPED,FR_EXEC_TERMINAL} friday_execution_phase_t;
typedef struct {
    char job[129],incarnation[33],action[129],query[129],finished[33];
    uint64_t deadline_ms;
    friday_execution_phase_t phase;
} friday_execution_stop_t;
bool friday_execution_blocked(const friday_execution_stop_t *c);
bool friday_execution_capture(friday_execution_stop_t *c,const char *job,const char *incarnation);
bool friday_execution_restore(friday_execution_stop_t *c,const cJSON *target);
/* Retry keeps the action ID and target. NULL means unavailable/invalid. */
cJSON *friday_execution_request(friday_execution_stop_t *c,const char *action,uint64_t now_ms,bool status);
void friday_execution_tick(friday_execution_stop_t *c,uint64_t now_ms);
void friday_execution_disconnected(friday_execution_stop_t *c);
bool friday_execution_receive(friday_execution_stop_t *c,const cJSON *packet);
void friday_execution_result(friday_execution_stop_t *c,const char *job,const char *incarnation);
const char *friday_execution_notice(friday_execution_phase_t phase);
