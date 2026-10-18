#include "friday_execution_stop.h"
#include <stdio.h>
#include <string.h>
static const char *str(const cJSON *p,const char *k){const cJSON *v=cJSON_GetObjectItemCaseSensitive(p,k);return cJSON_IsString(v)?v->valuestring:"";}
static bool id(const char *v){if(!v||!*v||strlen(v)>128)return false;for(;*v;v++)if(!((*v>='a'&&*v<='z')||(*v>='A'&&*v<='Z')||(*v>='0'&&*v<='9')||strchr("._:-",*v)))return false;return true;}
static bool inc(const char *v){if(!v||strlen(v)!=32)return false;for(;*v;v++)if(!((*v>='0'&&*v<='9')||(*v>='a'&&*v<='f')))return false;return true;}
static void copy(char *to,size_t n,const char *from){size_t count=strlen(from);if(count>=n)count=n-1;memmove(to,from,count);to[count]=0;}
bool friday_execution_blocked(const friday_execution_stop_t *c){return c->phase==FR_EXEC_PENDING||c->phase==FR_EXEC_UNCONFIRMED||c->phase==FR_EXEC_UNCERTAIN;}
bool friday_execution_capture(friday_execution_stop_t *c,const char *job,const char *execution){
    if(!id(job)||!inc(execution)||!strcmp(c->finished,execution)||friday_execution_blocked(c))return false;
    copy(c->job,sizeof(c->job),job);copy(c->incarnation,sizeof(c->incarnation),execution);c->action[0]=c->query[0]=0;c->phase=FR_EXEC_AVAILABLE;return true;
}
bool friday_execution_restore(friday_execution_stop_t *c,const cJSON *target){
    const char *job=str(target,"job_id"),*execution=str(target,"execution_id");
    if(!id(job)||!inc(execution)||(friday_execution_blocked(c)&&c->job[0]&&(strcmp(c->job,job)||strcmp(c->incarnation,execution))))return false;
    copy(c->job,sizeof(c->job),job);copy(c->incarnation,sizeof(c->incarnation),execution);c->phase=FR_EXEC_UNCERTAIN;return true;
}
cJSON *friday_execution_request(friday_execution_stop_t *c,const char *action,uint64_t now,bool status){
    if(!id(c->job)||!inc(c->incarnation)||!id(action))return NULL;
    cJSON *p=cJSON_CreateObject();if(!p)return NULL;
    const char *request=status?action:(c->action[0]?c->action:action);
    if(!cJSON_AddStringToObject(p,"type",status?"execution.status":"execution.stop")||
       !cJSON_AddStringToObject(p,"request_id",request)||!cJSON_AddStringToObject(p,"job_id",c->job)||
       !cJSON_AddStringToObject(p,"execution_id",c->incarnation)){cJSON_Delete(p);return NULL;}
    if(status)copy(c->query,sizeof(c->query),action);
    else{copy(c->action,sizeof(c->action),request);c->phase=FR_EXEC_PENDING;c->deadline_ms=now+1000;}
    return p;
}
void friday_execution_tick(friday_execution_stop_t *c,uint64_t now){if(c->phase==FR_EXEC_PENDING&&now>=c->deadline_ms)c->phase=FR_EXEC_UNCONFIRMED;}
void friday_execution_disconnected(friday_execution_stop_t *c){if(c->job[0])c->phase=FR_EXEC_UNCONFIRMED;}
static void settle(friday_execution_stop_t *c,friday_execution_phase_t phase){copy(c->finished,sizeof(c->finished),c->incarnation);c->phase=phase;c->job[0]=c->incarnation[0]=c->action[0]=c->query[0]=0;}
bool friday_execution_receive(friday_execution_stop_t *c,const cJSON *p){
    const char *type=str(p,"type"),*request=str(p,"request_id");
    bool status=!strcmp(type,"execution.stop.status");
    if((!status&&strcmp(type,"execution.stop.observed"))||!c->job[0]||strcmp(c->job,str(p,"job_id"))||strcmp(c->incarnation,str(p,"execution_id"))||
       !*request||strcmp(status?c->query:c->action,request))return false;
    const cJSON *rev=cJSON_GetObjectItemCaseSensitive(p,"authority_revoked");if(!cJSON_IsBool(rev))return false;
    if(!status){
        const cJSON *ack=cJSON_GetObjectItemCaseSensitive(p,"ack_confirmed"),*drain=cJSON_GetObjectItemCaseSensitive(p,"handler_drained");
        if(!cJSON_IsBool(ack)||!cJSON_IsBool(drain))return false;
        if(cJSON_IsFalse(ack)){if(cJSON_IsTrue(rev))return false;c->phase=FR_EXEC_UNCONFIRMED;return true;}
        const char *state=str(p,"state_at_ack");
        if(cJSON_IsTrue(rev)&&!strcmp(state,"stopped_before_admission"))settle(c,FR_EXEC_STOPPED);
        else if(cJSON_IsTrue(rev)&&!strcmp(state,"outcome_uncertain"))c->phase=FR_EXEC_UNCERTAIN;
        else if(cJSON_IsFalse(rev)&&!strcmp(state,"already_terminal"))settle(c,FR_EXEC_TERMINAL);
        else return false;
    }else{
        const cJSON *unknown=cJSON_GetObjectItemCaseSensitive(p,"outcome_uncertain"),*none=cJSON_GetObjectItemCaseSensitive(p,"no_execution_admitted");
        const char *state=str(p,"job_status");bool terminal=!strcmp(state,"completed")||!strcmp(state,"failed")||!strcmp(state,"expired")||!strcmp(state,"late");
        if(!cJSON_IsBool(unknown)||!cJSON_IsBool(none)||(!terminal&&strcmp(state,"queued")&&strcmp(state,"running")))return false;
        if(cJSON_IsTrue(none)&&(cJSON_IsTrue(unknown)||!cJSON_IsTrue(rev)||strcmp(state,"failed")))return false;
        if(cJSON_IsTrue(unknown)&&strcmp(state,"running"))return false;
        if(cJSON_IsTrue(none)&&cJSON_IsTrue(rev)&&!strcmp(state,"failed"))settle(c,FR_EXEC_STOPPED);
        else if(cJSON_IsTrue(unknown))c->phase=FR_EXEC_UNCERTAIN;
        else if(terminal)settle(c,FR_EXEC_TERMINAL);
        else c->phase=FR_EXEC_UNCONFIRMED;
    }return true;
}
void friday_execution_result(friday_execution_stop_t *c,const char *job,const char *execution){
    if(!inc(execution)||!id(job))return;
    copy(c->finished,sizeof(c->finished),execution);
    if(!friday_execution_blocked(c)&&!strcmp(c->job,job)&&!strcmp(c->incarnation,execution))settle(c,FR_EXEC_TERMINAL);
}
const char *friday_execution_notice(friday_execution_phase_t phase){switch(phase){
    case FR_EXEC_PENDING:return "Stop requested; acknowledgement pending.";
    case FR_EXEC_UNCONFIRMED:return "Stop not confirmed; work may continue.";
    case FR_EXEC_UNCERTAIN:return "Outcome uncertain. Review on the Mac; do not replay.";
    case FR_EXEC_STOPPED:return "Stopped before admission; no operation started.";
    case FR_EXEC_TERMINAL:return "Request finished; prior effects are not reversed.";
    default:return "";
}}
