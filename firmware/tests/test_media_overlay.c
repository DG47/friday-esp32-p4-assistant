#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "friday_media_overlay.h"
static cJSON *parse(const char *text){cJSON *r=cJSON_Parse(text);assert(r);return r;}
int main(void)
{
    friday_media_guard_t guard={0};friday_media_overlay_t o={0};assert(friday_media_begin(&guard,"owner"));assert(friday_media_accept(&guard,"owner","0123456789abcdef01234567",1));
    cJSON *r=parse("{\"media_overlay\":{\"media_request_id\":\"owner\",\"media_session_id\":\"0123456789abcdef01234567\",\"overlay_id\":\"overlay-one\",\"media_sequence\":3,\"state\":\"suspend\"}}");
    assert(friday_media_overlay_suspend(&o,&guard,r,true)&&o.active&&o.protected&&guard.sequence==3);
    assert(!friday_media_overlay_resume(&o));assert(!friday_media_overlay_suspend(&o,&guard,r,false));
    o.protected=false;cJSON *request=friday_media_overlay_resume(&o);assert(request&&o.resume_requested);cJSON_Delete(request);assert(!friday_media_overlay_resume(&o));
    cJSON *result=parse("{\"type\":\"media.resume.result\",\"media_request_id\":\"wrong\",\"media_session_id\":\"0123456789abcdef01234567\",\"overlay_id\":\"overlay-one\",\"status\":\"ready\"}");
    assert(!friday_media_overlay_result(&o,result)&&!o.ready);cJSON_SetValuestring(cJSON_GetObjectItem(result,"media_request_id"),"owner");
    cJSON_SetValuestring(cJSON_GetObjectItem(result,"status"),"pending");assert(friday_media_overlay_result(&o,result)&&!o.ready&&!o.resume_requested);
    request=friday_media_overlay_resume(&o);assert(request);cJSON_Delete(request);cJSON_SetValuestring(cJSON_GetObjectItem(result,"status"),"ready");
    assert(friday_media_overlay_result(&o,result)&&o.ready);assert(!friday_media_accept(&guard,"owner","0123456789abcdef01234567",2));assert(friday_media_accept(&guard,"owner","0123456789abcdef01234567",4));
    friday_media_overlay_clear(&o);assert(!friday_media_overlay_result(&o,result));friday_media_clear(&guard);assert(!friday_media_overlay_suspend(&o,&guard,r,false));
    cJSON_Delete(r);cJSON_Delete(result);puts("Exact owner/overlay binding, protected resume, pending/ready, stale sequence: PASS");return 0;
}
