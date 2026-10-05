#pragma once
#include <stddef.h>
typedef void *SemaphoreHandle_t;
extern int lock_allowed;
static inline SemaphoreHandle_t xSemaphoreCreateMutex(void){return (void*)1;}
static inline int xSemaphoreTake(SemaphoreHandle_t s,int t){(void)s;(void)t;return lock_allowed;}
static inline void xSemaphoreGive(SemaphoreHandle_t s){(void)s;}
