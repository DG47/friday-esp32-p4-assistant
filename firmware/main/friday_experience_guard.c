#include "friday_experience_guard.h"
#include <string.h>
#include <stdio.h>
static const char *string(const cJSON *j,const char *key) {
    const cJSON *v=cJSON_GetObjectItemCaseSensitive(j,key);return cJSON_IsString(v)?v->valuestring:"";
}
bool friday_experience_session(friday_experience_guard_t *g,const char *epoch) {
    memset(g,0,sizeof(*g));
    if(!epoch||strlen(epoch)!=32||strspn(epoch,"0123456789abcdef")!=32)return false;
    memcpy(g->epoch,epoch,33);g->enabled=true;return true;
}
bool friday_experience_begin(friday_experience_guard_t *g,const char *request) {
    if(!g->enabled)return true;
    if(!request||!request[0]||strlen(request)>128||strspn(request,"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")!=strlen(request))return false;
    memcpy(g->previous,g->request,sizeof(g->request));g->previous_sequence=g->sequence;g->previous_finished=g->finished;
    snprintf(g->request,sizeof(g->request),"%s",request);g->sequence=0;g->finished=false;g->provisional=true;return true;
}
void friday_experience_send_failed(friday_experience_guard_t *g,const char *request) {
    if(!request||strcmp(request,g->request))return;
    memcpy(g->request,g->previous,sizeof(g->request));g->sequence=g->previous_sequence;g->finished=g->previous_finished;
    g->provisional=false;
}
void friday_experience_cancel(friday_experience_guard_t *g) {
    g->request[0]=g->previous[0]=0;g->sequence=g->previous_sequence=0;g->finished=false;g->provisional=false;
}
friday_experience_accept_t friday_experience_accept(friday_experience_guard_t *g,const cJSON *p) {
    if(!g->enabled)return FRIDAY_EXPERIENCE_ACCEPT;
    const char *type=string(p,"type");
    if(!strcmp(type,"reminder.alert")||!strncmp(type,"identity.",9)) {friday_experience_cancel(g);return FRIDAY_EXPERIENCE_ACCEPT;}
    if(!strcmp(type,"experience.input_error")||(!strcmp(type,"error")&&(!strcmp(string(p,"code"),"pairing_required")||!strcmp(string(p,"code"),"authentication_required"))))return FRIDAY_EXPERIENCE_ACCEPT;
    if(!strcmp(type,"ready")||!strcmp(type,"experience.recovery.available")||!strncmp(type,"speech.",7)||!strncmp(type,"media.",6)||!strcmp(type,"wake.detected")||(!strcmp(type,"surface")&&cJSON_HasObjectItem(p,"media_request_id")))return FRIDAY_EXPERIENCE_ACCEPT;
    bool result=!strcmp(type,"agent.result");
    const cJSON *sequence=cJSON_GetObjectItemCaseSensitive(p,"experience_sequence");
    bool historical=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(p,"experience_historical"));
    if(!historical&&result&&g->provisional&&!strcmp(string(p,"experience_epoch"),g->epoch)&&!strcmp(string(p,"experience_id"),g->previous))return FRIDAY_EXPERIENCE_REJECT; /* defer ACK until admission/rejection resolves */
    if(historical||!g->request[0]||strcmp(string(p,"experience_epoch"),g->epoch)||strcmp(string(p,"experience_id"),g->request)||!cJSON_IsNumber(sequence)||sequence->valuedouble<1||sequence->valuedouble>UINT32_MAX||sequence->valuedouble!=(uint32_t)sequence->valuedouble||sequence->valuedouble<=g->sequence)
        return result?FRIDAY_EXPERIENCE_DISCARD_RESULT:FRIDAY_EXPERIENCE_REJECT;
    if(!strcmp(type,"experience.rejected")) {
        if(!g->provisional||strcmp(string(p,"experience_previous"),g->previous))return FRIDAY_EXPERIENCE_REJECT;
        friday_experience_send_failed(g,g->request);return FRIDAY_EXPERIENCE_ACCEPT;
    }
    g->sequence=(uint32_t)sequence->valuedouble;
    g->provisional=false;
    if(!strcmp(type,"tool.activity")&&g->finished)return FRIDAY_EXPERIENCE_REJECT;
    if(!strcmp(type,"experience.finished"))g->finished=true;
    return FRIDAY_EXPERIENCE_ACCEPT;
}
