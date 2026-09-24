#include "friday_state.h"

static friday_state_t current_state = FRIDAY_STATE_IDLE;

const char *friday_state_name(friday_state_t state)
{
    switch (state) {
    case FRIDAY_STATE_IDLE: return "idle";
    case FRIDAY_STATE_LISTENING: return "listening";
    case FRIDAY_STATE_THINKING: return "thinking";
    case FRIDAY_STATE_SPEAKING: return "speaking";
    case FRIDAY_STATE_ALERT: return "alert";
    default: return "unknown";
    }
}

void friday_state_set(friday_state_t state)
{
    current_state = state;
}

friday_state_t friday_state_get(void)
{
    return current_state;
}

