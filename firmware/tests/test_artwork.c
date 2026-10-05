#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "friday_artwork.h"
static cJSON *load(const char *path){FILE *f=fopen(path,"rb");assert(f);char *text=calloc(FRIDAY_MEDIA_MESSAGE_MAX+1,1);assert(text);size_t n=fread(text,1,FRIDAY_MEDIA_MESSAGE_MAX,f);fclose(f);assert(n>0&&n<FRIDAY_MEDIA_MESSAGE_MAX);cJSON *root=cJSON_Parse(text);free(text);assert(root);return root;}
int main(int argc,char **argv)
{
    assert(argc==2);cJSON *root=load(argv[1]);cJSON *s=cJSON_GetObjectItem(root,"surface"),*a=cJSON_GetObjectItem(s,"artwork");friday_artwork_t cache={0};
    assert(friday_artwork_update(&cache,s)&&cache.pixels&&cache.ready&&cache.generation==1);uint8_t *p=cache.pixels;
    cJSON *same=cJSON_Duplicate(s,true);cJSON_DeleteItemFromObject(cJSON_GetObjectItem(same,"artwork"),"data_base64");
    assert(friday_artwork_update(&cache,same)&&cache.pixels==p);cJSON_SetNumberValue(cJSON_GetObjectItem(same,"artwork_generation"),2);
    assert(!friday_artwork_update(&cache,same)&&!cache.pixels);cJSON_Delete(same);
    for(int mode=0;mode<11;mode++) {
        assert(friday_artwork_update(&cache,s));cJSON *bad=cJSON_Duplicate(s,true),*art=cJSON_GetObjectItem(bad,"artwork");
        if(mode==0)cJSON_SetValuestring(cJSON_GetObjectItem(art,"sha256"),"0000000000000000000000000000000000000000000000000000000000000000");
        if(mode==1)cJSON_GetObjectItem(art,"data_base64")->valuestring[0]='!';
        if(mode==2)cJSON_GetObjectItem(art,"data_base64")->valuestring[0]='=';
        if(mode==3)cJSON_SetValuestring(cJSON_GetObjectItem(art,"data_base64"),"AAAA");
        if(mode==4)cJSON_SetNumberValue(cJSON_GetObjectItem(art,"width"),320);
        if(mode==5)cJSON_SetValuestring(cJSON_GetObjectItem(art,"track_key"),"wrong-track");
        if(mode==6)cJSON_SetValuestring(cJSON_GetObjectItem(art,"status"),"loading");
        if(mode==7)cJSON_SetNumberValue(cJSON_GetObjectItem(bad,"artwork_generation"),NAN);
        if(mode==8)cJSON_SetNumberValue(cJSON_GetObjectItem(bad,"artwork_generation"),.5);
        if(mode==9)cJSON_SetNumberValue(cJSON_GetObjectItem(bad,"artwork_generation"),1e99);
        if(mode==10)cJSON_SetNumberValue(cJSON_GetObjectItem(bad,"artwork_generation"),0);
        assert(!friday_artwork_update(&cache,bad)&&!cache.ready&&!cache.pixels);cJSON_Delete(bad);
    }
    assert(friday_artwork_update(&cache,s));cJSON *next=cJSON_Duplicate(s,true),*nextart=cJSON_GetObjectItem(next,"artwork");
    cJSON_SetValuestring(cJSON_GetObjectItem(next,"track_key"),"synthetic:next");cJSON_SetValuestring(cJSON_GetObjectItem(nextart,"track_key"),"synthetic:next");
    cJSON_DeleteItemFromObject(nextart,"data_base64");assert(!friday_artwork_update(&cache,next)&&!cache.pixels);
    cJSON_Delete(next);friday_artwork_clear(&cache);cJSON_Delete(root);puts("Artwork strict decode/hash/size/generation/track/cache: PASS");return 0;
}
