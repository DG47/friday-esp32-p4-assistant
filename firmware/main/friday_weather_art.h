#pragma once
#include "lvgl.h"

/* Immutable bundled RGB565 illustrations, 680x190. No runtime allocation.
 * Only current WMO/day selects art. No future forecast enters this API. */
static inline bool friday_weather_code_known(int code)
{
    switch(code) {
    case 0: case 1: case 2: case 3: case 45: case 48:
    case 51: case 53: case 55: case 56: case 57:
    case 61: case 63: case 65: case 66: case 67:
    case 71: case 73: case 75: case 77:
    case 80: case 81: case 82: case 85: case 86:
    case 95: case 96: case 97: case 99: return true;
    default: return false;
    }
}
const lv_image_dsc_t *friday_weather_art(int city, int code, int day);
