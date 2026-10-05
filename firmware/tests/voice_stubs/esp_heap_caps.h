#pragma once
#include <stdlib.h>
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
static inline void *heap_caps_aligned_alloc(size_t a,size_t n,int c){(void)a;(void)c;return malloc(n);}
#define MALLOC_CAP_INTERNAL 4
static inline size_t heap_caps_get_free_size(int c){(void)c;return 100000;}
static inline size_t heap_caps_get_minimum_free_size(int c){(void)c;return 90000;}
static inline size_t heap_caps_get_largest_free_block(int c){(void)c;return 80000;}
