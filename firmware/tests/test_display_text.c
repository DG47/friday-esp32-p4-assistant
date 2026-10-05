#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "lvgl.h"
#include "friday_display_text.h"

static bool supports(uint32_t code, void *context)
{
    const lv_font_t *font=context; lv_font_glyph_dsc_t glyph;
    return font->get_glyph_dsc(font,&glyph,code,0);
}
static void check(const lv_font_t *font, const char *source, const char *expected)
{
    char result[2048]; friday_display_text(result,sizeof(result),source,supports,(void *)font);
    assert(strcmp(result,expected)==0);
    /* Conversion is stable; no emitted marker contains unsupported glyphs. */
    char again[2048]; friday_display_text(again,sizeof(again),result,supports,(void *)font);
    assert(strcmp(result,again)==0);
}
int main(void)
{
    const lv_font_t *fonts[]={&lv_font_montserrat_18,&lv_font_montserrat_24,&lv_font_montserrat_32};
    for (unsigned f=0;f<3;f++) {
        const lv_font_t *font=fonts[f];
        for (uint32_t c=32;c<=126;c++) assert(supports(c,(void *)font));
        assert(!supports(0xb7,(void *)font));
        assert(!supports(0x2019,(void *)font));
        assert(!supports(0x2014,(void *)font));
        assert(!supports(0x2026,(void *)font));
        check(font,"Google Calendar · read only · 2 events","Google Calendar  -  read only  -  2 events");
        check(font,"“Friday’s” — 09:00–10:00…","\"Friday's\" -- 09:00-10:00...");
        check(font,"Today → details ≤ 4","Today -> details <= 4");
        check(font,"ASCII 0123456789: !? / # - _","ASCII 0123456789: !? / # - _");
        check(font,"20°C","20°C");
        check(font,"café 世界 😀","caf[U+00E9] [U+4E16][U+754C] [U+1F600]");
        check(font,"one\ntwo\tthree\rfour","one\ntwo threefour");
        check(font,"bad \xf0\x28\x8c\x28","bad ?(?(" );
        for (size_t capacity=1;capacity<40;capacity++) {
            char result[40],again[160];
            memset(result,0xa5,sizeof(result));
            friday_display_text(result,capacity,"α°😀…test",supports,(void *)font);
            assert(strlen(result)<capacity);
            friday_display_text(again,sizeof(again),result,supports,(void *)font);
            assert(strcmp(result,again)==0);
            assert((unsigned char)result[capacity]==0xa5);
        }
    }
    puts("Actual Montserrat 18/24/32 glyph coverage and display normalization: PASS");
}
#include <stdlib.h>
void lv_draw_buf_flush_cache(const lv_draw_buf_t *buffer,const lv_area_t *area){(void)buffer;(void)area;abort();}
uint32_t lv_draw_buf_width_to_stride(uint32_t width,lv_color_format_t format){(void)width;(void)format;abort();}
