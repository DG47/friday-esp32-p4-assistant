#pragma once
#include <stdint.h>
extern int64_t fake_clock;
static inline int64_t esp_timer_get_time(void){fake_clock+=100;return fake_clock;}
