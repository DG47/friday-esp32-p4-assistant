#pragma once
#include <stddef.h>
#define pdPASS 1
static inline int xTaskCreate(void (*f)(void*),const char *n,int s,void *a,int p,void *h){(void)f;(void)n;(void)s;(void)a;(void)p;(void)h;return 1;}
static inline void vTaskDelay(int ticks){(void)ticks;}
