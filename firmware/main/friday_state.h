#pragma once

typedef enum {
    FRIDAY_STATE_IDLE,
    FRIDAY_STATE_LISTENING,
    FRIDAY_STATE_THINKING,
    FRIDAY_STATE_SPEAKING,
    FRIDAY_STATE_ALERT,
} friday_state_t;

const char *friday_state_name(friday_state_t state);
void friday_state_set(friday_state_t state);
friday_state_t friday_state_get(void);

