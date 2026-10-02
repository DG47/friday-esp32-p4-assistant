#include "friday_display_text.h"
#include <stdio.h>
#include <string.h>

static size_t decode(const unsigned char *s, size_t remaining, uint32_t *code)
{
    unsigned count; uint32_t minimum;
    if (s[0] < 128) { *code=s[0]; return 1; }
    if (s[0]>=0xc2 && s[0]<=0xdf) { count=2; *code=s[0]&31; minimum=128; }
    else if (s[0]>=0xe0 && s[0]<=0xef) { count=3; *code=s[0]&15; minimum=2048; }
    else if (s[0]>=0xf0 && s[0]<=0xf4) { count=4; *code=s[0]&7; minimum=65536; }
    else { *code=0xfffd; return 1; }
    if (remaining<count) { *code=0xfffd; return 1; }
    for (unsigned i=1;i<count;i++) {
        if ((s[i]&0xc0)!=0x80) { *code=0xfffd; return 1; }
        *code=(*code<<6)|(s[i]&63);
    }
    if (*code<minimum || *code>0x10ffff || (*code>=0xd800 && *code<=0xdfff)) { *code=0xfffd; return 1; }
    return count;
}

static const char *equivalent(uint32_t code)
{
    switch (code) {
    case 0xa0: case 0x202f: case 0x2007: return " ";
    case 0xb7: case 0x2022: return " - ";
    case 0x2018: case 0x2019: case 0x2032: return "'";
    case 0x201c: case 0x201d: case 0x2033: return "\"";
    case 0x2010: case 0x2011: case 0x2012: case 0x2013: case 0x2212: return "-";
    case 0x2014: return "--";
    case 0x2026: return "...";
    case 0x2190: return "<-";
    case 0x2192: return "->";
    case 0x2264: return "<=";
    case 0x2265: return ">=";
    case 0x200b: case 0x200c: case 0x200d: case 0x2060: case 0xfeff: return "";
    default: return NULL;
    }
}

void friday_display_text(char *output, size_t capacity, const char *input,
                         friday_glyph_supported_t supported, void *context)
{
    if (!capacity) return;
    size_t used=0;
    const unsigned char *s=(const unsigned char *)(input?input:"");
    size_t remaining=strlen((const char *)s);
    while (remaining) {
        uint32_t code; size_t bytes=decode(s,remaining,&code);
        const char *replacement=NULL; char marker[16];
        if (code=='\n') replacement="\n";
        else if (code=='\r') replacement="";
        else if (code=='\t') replacement=" ";
        else if (code<32 || (code>=0x7f && code<=0x9f)) replacement="?";
        else replacement=equivalent(code);
        if (!replacement && !(supported && supported(code,context))) {
            if (code==0xfffd) replacement="?";
            else { snprintf(marker,sizeof(marker),"[U+%04lX]",(unsigned long)code); replacement=marker; }
        }
        size_t length=replacement?strlen(replacement):bytes;
        if (length>=capacity-used) break;
        memcpy(output+used,replacement?replacement:(const char *)s,length); used+=length;
        s+=bytes; remaining-=bytes;
    }
    output[used]='\0';
}
