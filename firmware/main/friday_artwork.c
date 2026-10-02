#include "friday_artwork.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "mbedtls/sha256.h"

static const char *string(const cJSON *object,const char *key)
{const cJSON *v=cJSON_GetObjectItemCaseSensitive(object,key);return cJSON_IsString(v)?v->valuestring:"";}
static bool identity(const char *key)
{
    size_t n=strlen(key);if(!n||n>128)return false;
    for(size_t i=0;i<n;i++)if((unsigned char)key[i]<33||(unsigned char)key[i]>126)return false;
    return true;
}
static int digit(unsigned char c)
{
    if(c>='A'&&c<='Z')return c-'A';
    if(c>='a'&&c<='z')return c-'a'+26;
    if(c>='0'&&c<='9')return c-'0'+52;
    if(c=='+')return 62;
    if(c=='/')return 63;
    return -1;
}
void friday_artwork_clear(friday_artwork_t *cache)
{
    if(cache->pixels){memset(cache->pixels,0,FRIDAY_ARTWORK_BYTES);free(cache->pixels);}
    memset(cache,0,sizeof(*cache));
}
bool friday_artwork_update(friday_artwork_t *cache,const cJSON *surface)
{
    const char *key=string(surface,"track_key");
    const cJSON *generation=cJSON_GetObjectItemCaseSensitive(surface,"artwork_generation");
    const cJSON *art=cJSON_GetObjectItemCaseSensitive(surface,"artwork");
    const cJSON *w=cJSON_GetObjectItemCaseSensitive(art,"width"),*h=cJSON_GetObjectItemCaseSensitive(art,"height");
    const char *hash=string(art,"sha256");
    bool hash_valid=strlen(hash)==64;
    for(size_t i=0;hash_valid&&i<64;i++)hash_valid=(hash[i]>='0'&&hash[i]<='9')||(hash[i]>='a'&&hash[i]<='f');
    if(!cJSON_IsNumber(generation)||!isfinite(generation->valuedouble)||generation->valuedouble<0||generation->valuedouble>9007199254740991.0||
        generation->valuedouble!=(double)(uint64_t)generation->valuedouble||!hash_valid||!identity(key)||strcmp(key,string(art,"track_key"))||strcmp(string(art,"status"),"ready")||
        !cJSON_IsNumber(w)||w->valuedouble!=192||!cJSON_IsNumber(h)||h->valuedouble!=192||
        strcmp(string(art,"format"),"rgb565_le")) {friday_artwork_clear(cache);return false;}
    uint64_t incoming=(uint64_t)generation->valuedouble;
    if(cache->ready&&!strcmp(cache->track_key,key)&&incoming<cache->generation){friday_artwork_clear(cache);return false;}
    const char *data=string(art,"data_base64");
    if(!*data) {
        if(cache->ready&&cache->pixels&&!strcmp(cache->track_key,key)&&incoming==cache->generation&&!strcmp(cache->sha256,hash))return true;
        friday_artwork_clear(cache);return false;
    }
    if(strlen(data)!=FRIDAY_ARTWORK_BASE64_BYTES){friday_artwork_clear(cache);return false;}
    /* Validate the complete alphabet before touching the single retained buffer. */
    for(size_t i=0;i<FRIDAY_ARTWORK_BASE64_BYTES;i++)if(digit((unsigned char)data[i])<0){friday_artwork_clear(cache);return false;}
    if(!cache->pixels)cache->pixels=malloc(FRIDAY_ARTWORK_BYTES);
    if(!cache->pixels){friday_artwork_clear(cache);return false;}
    for(size_t i=0,j=0;i<FRIDAY_ARTWORK_BASE64_BYTES;i+=4,j+=3) {
        uint32_t value=(uint32_t)digit(data[i])<<18|(uint32_t)digit(data[i+1])<<12|
            (uint32_t)digit(data[i+2])<<6|(uint32_t)digit(data[i+3]);
        cache->pixels[j]=value>>16;cache->pixels[j+1]=value>>8;cache->pixels[j+2]=value;
    }
    unsigned char digest[32];char hex[65];
    if(mbedtls_sha256(cache->pixels,FRIDAY_ARTWORK_BYTES,digest,0)!=0){friday_artwork_clear(cache);return false;}
    const char digits[]="0123456789abcdef";
    for(size_t i=0;i<32;i++){hex[i*2]=digits[digest[i]>>4];hex[i*2+1]=digits[digest[i]&15];}hex[64]=0;
    if(strcmp(hex,hash)){friday_artwork_clear(cache);return false;}
    memcpy(cache->track_key,key,strlen(key)+1);memcpy(cache->sha256,hash,65);
    cache->generation=incoming;cache->ready=true;return true;
}
