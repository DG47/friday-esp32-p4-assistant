#include "friday_media_overlay.h"
#include <string.h>
#include <math.h>
static const char *string(const cJSON *object,const char *key)
{const cJSON *v=cJSON_GetObjectItemCaseSensitive(object,key);return cJSON_IsString(v)?v->valuestring:"";}
void friday_media_overlay_clear(friday_media_overlay_t *overlay){memset(overlay,0,sizeof(*overlay));}
bool friday_media_overlay_suspend(friday_media_overlay_t *o,friday_media_guard_t *g,const cJSON *root,bool protected)
{
    const cJSON *m=cJSON_GetObjectItemCaseSensitive(root,"media_overlay");
    const char *request=string(m,"media_request_id"),*session=string(m,"media_session_id"),*id=string(m,"overlay_id");
    const cJSON *sequence=cJSON_GetObjectItemCaseSensitive(m,"media_sequence");
    size_t n=strlen(id);
    if(!cJSON_IsObject(m)||strcmp(string(m,"state"),"suspend")||!g->request[0]||!g->session[0]||
        strcmp(request,g->request)||strcmp(session,g->session)||!n||n>64||
        !cJSON_IsNumber(sequence)||!isfinite(sequence->valuedouble)||sequence->valuedouble<1||sequence->valuedouble>9007199254740991.0||
        floor(sequence->valuedouble)!=sequence->valuedouble||(uint64_t)sequence->valuedouble<g->sequence)return false;
    for(size_t i=0;i<n;i++)if((unsigned char)id[i]<33||(unsigned char)id[i]>126)return false;
    if(o->active&&!strcmp(o->id,id)&&o->sequence>=(uint64_t)sequence->valuedouble)return false;
    friday_media_overlay_clear(o);o->active=true;o->protected=protected;
    memcpy(o->request,request,strlen(request)+1);memcpy(o->session,session,25);memcpy(o->id,id,n+1);
    o->sequence=(uint64_t)sequence->valuedouble;g->sequence=o->sequence;return true;
}
cJSON *friday_media_overlay_resume(friday_media_overlay_t *o)
{
    if(!o->active||o->protected||o->resume_requested)return NULL;
    cJSON *root=cJSON_CreateObject();if(!root)return NULL;
    cJSON_AddStringToObject(root,"type","media.resume");cJSON_AddStringToObject(root,"media_request_id",o->request);
    cJSON_AddStringToObject(root,"media_session_id",o->session);cJSON_AddStringToObject(root,"overlay_id",o->id);
    o->resume_requested=true;return root;
}
bool friday_media_overlay_result(friday_media_overlay_t *o,const cJSON *root)
{
    if(!o->active||!o->resume_requested||strcmp(string(root,"type"),"media.resume.result")||
        strcmp(string(root,"media_request_id"),o->request)||strcmp(string(root,"media_session_id"),o->session)||strcmp(string(root,"overlay_id"),o->id))return false;
    const char *status=string(root,"status");
    if(!strcmp(status,"ready")){o->ready=true;return true;}
    if(!strcmp(status,"pending")||!strcmp(status,"ignored")){o->resume_requested=false;o->ready=false;return true;}
    return false;
}
