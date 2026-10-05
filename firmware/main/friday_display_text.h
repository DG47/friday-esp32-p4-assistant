#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef bool (*friday_glyph_supported_t)(uint32_t codepoint, void *context);
/* Display-only conversion. Never emits partial UTF-8 or unsupported glyphs. */
void friday_display_text(char *output, size_t capacity, const char *input,
                         friday_glyph_supported_t supported, void *context);
