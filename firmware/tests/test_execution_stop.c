#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "friday_execution_stop.h"
static const char *execution="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static cJSON *ack(const char *type,const char *request){cJSON *p=cJSON_CreateObject();cJSON_AddStringToObject(p,"type",type);cJSON_AddStringToObject(p,"request_id",request);cJSON_AddStringToObject(p,"job_id","synthetic-job");cJSON_AddStringToObject(p,"execution_id",execution);return p;}
int main(void){
    friday_execution_stop_t c={0};assert(!friday_execution_capture(&c,"bad/target",execution));assert(!friday_execution_capture(&c,"job","invalid"));
    assert(friday_execution_capture(&c,"synthetic-job",execution));cJSON *p=friday_execution_request(&c,"stop-1",100,false);assert(p);cJSON_Delete(p);
    friday_execution_tick(&c,1099);assert(c.phase==FR_EXEC_PENDING);friday_execution_tick(&c,1100);assert(c.phase==FR_EXEC_UNCONFIRMED&&friday_execution_blocked(&c));
    friday_execution_result(&c,"synthetic-job",execution);assert(c.phase==FR_EXEC_UNCONFIRMED);
    friday_execution_disconnected(&c);assert(c.phase==FR_EXEC_UNCONFIRMED);
    assert(!friday_execution_capture(&c,"replacement","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    p=friday_execution_request(&c,"new-action",1200,false);assert(!strcmp(cJSON_GetObjectItem(p,"request_id")->valuestring,"stop-1"));cJSON_Delete(p);assert(!strcmp(c.action,"stop-1"));
    p=ack("execution.stop.observed","stale");cJSON_AddBoolToObject(p,"ack_confirmed",true);cJSON_AddBoolToObject(p,"authority_revoked",true);cJSON_AddBoolToObject(p,"handler_drained",true);cJSON_AddStringToObject(p,"state_at_ack","outcome_uncertain");
    assert(!friday_execution_receive(&c,p));cJSON_SetValuestring(cJSON_GetObjectItem(p,"request_id"),"stop-1");
    cJSON_SetValuestring(cJSON_GetObjectItem(p,"execution_id"),"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");assert(!friday_execution_receive(&c,p));cJSON_SetValuestring(cJSON_GetObjectItem(p,"execution_id"),execution);
    assert(friday_execution_receive(&c,p));assert(c.phase==FR_EXEC_UNCERTAIN&&friday_execution_blocked(&c));cJSON_Delete(p);
    p=friday_execution_request(&c,"read-1",1300,true);cJSON_Delete(p);
    p=ack("execution.stop.status","read-1");cJSON_AddStringToObject(p,"job_status","running");cJSON_AddBoolToObject(p,"authority_revoked",true);cJSON_AddBoolToObject(p,"outcome_uncertain",true);cJSON_AddBoolToObject(p,"no_execution_admitted",false);
    assert(friday_execution_receive(&c,p));assert(c.phase==FR_EXEC_UNCERTAIN);cJSON_Delete(p);
    p=ack("execution.stop.observed","stop-1");cJSON_AddBoolToObject(p,"ack_confirmed",true);cJSON_AddBoolToObject(p,"authority_revoked",true);cJSON_AddBoolToObject(p,"handler_drained",false);cJSON_AddStringToObject(p,"state_at_ack","stopped_before_admission");
    assert(friday_execution_receive(&c,p));assert(c.phase==FR_EXEC_STOPPED&&!friday_execution_blocked(&c));assert(!friday_execution_receive(&c,p));cJSON_Delete(p);
    assert(!friday_execution_capture(&c,"synthetic-job",execution));
    friday_execution_stop_t restored={0};p=ack("unused","unused");assert(friday_execution_restore(&restored,p));assert(restored.phase==FR_EXEC_UNCERTAIN&&friday_execution_blocked(&restored));
    cJSON_SetValuestring(cJSON_GetObjectItem(p,"job_id"),"another-job");assert(!friday_execution_restore(&restored,p));cJSON_Delete(p);
    p=friday_execution_request(&restored,"strict-read",0,true);cJSON_Delete(p);
    p=ack("execution.stop.status","strict-read");cJSON_AddStringToObject(p,"job_status","failed");cJSON_AddBoolToObject(p,"authority_revoked",true);cJSON_AddBoolToObject(p,"outcome_uncertain",true);cJSON_AddBoolToObject(p,"no_execution_admitted",true);assert(!friday_execution_receive(&restored,p));assert(restored.phase==FR_EXEC_UNCERTAIN);cJSON_Delete(p);
    assert(sizeof(c)<640);puts("Native execution Stop: deadline, stale/scope, lost ACK, retained retry, terminal ordering, restart and no false drain-success PASS");
}
