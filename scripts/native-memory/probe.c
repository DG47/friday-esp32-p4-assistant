/* Host-only probe of the actual pinned LVGL 9.3 sources and Montserrat 24 font. */
#include <stdio.h>
#include <string.h>
#include "lvgl.h"
#include "src/misc/lv_text_private.h"

int main(int argc, char **argv) {
    const lv_font_t *font=&lv_font_montserrat_24;
    if(argc>1 && strcmp(argv[1],"--metrics")==0) {
        printf("[");
        for(int c=32;c<=126;c++) {
            if(c!=32)printf(",");printf("[");
            printf("%d",lv_font_get_glyph_width(font,c,0));
            for(int next=32;next<=126;next++)printf(",%d",lv_font_get_glyph_width(font,c,next));
            printf("]");
        }
        printf("]\n");return 0;
    }
    char text[2048];
    while(fgets(text,sizeof(text),stdin)) {
        text[strcspn(text,"\n")]=0;
        if(argc>1 && strcmp(argv[1],"--hex")==0) {
            size_t length=strlen(text);
            if(length%2)return 3;
            for(size_t index=0;index<length;index+=2) {
                unsigned int value;
                if(sscanf(text+index,"%2x",&value)!=1)return 3;
                text[index/2]=(char)value;
            }
            text[length/2]=0;
        }
        lv_point_t size;
        lv_text_get_size(&size,text,font,2,0,620,LV_TEXT_FLAG_NONE);
        printf("{\"width\":%d,\"height\":%d,\"lines\":[",size.x,size.y);
        unsigned int offset=0;int first=1;
        while(text[offset]) {
            int used=0;
            unsigned int advance=lv_text_get_next_line(text+offset,LV_TEXT_LEN_MAX,font,2,620,&used,LV_TEXT_FLAG_NONE);
            if(!advance)return 2;
            if(!first)printf(",");first=0;
            printf("{\"end\":%u,\"used_width\":%d}",offset+advance,used);
            offset+=advance;
        }
        printf("]}\n");
    }
    return 0;
}
/* Layout does not render bitmaps. Abort if that assumption ever changes. */
#include <stdlib.h>
void lv_draw_buf_flush_cache(const lv_draw_buf_t *buffer,const lv_area_t *area){(void)buffer;(void)area;abort();}
uint32_t lv_draw_buf_width_to_stride(uint32_t width,lv_color_format_t format){(void)width;(void)format;abort();}
