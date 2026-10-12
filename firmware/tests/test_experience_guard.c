#include <assert.h>
#include <stdio.h>
#include "friday_experience_guard.h"
int main(void)
{
    friday_experience_guard_t guard;
    const char *epoch="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    assert(friday_experience_session(&guard,epoch));
    assert(friday_experience_begin(&guard,"synthetic-report"));
    cJSON *result=cJSON_Parse("{\"type\":\"agent.result\",\"experience_epoch\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"experience_id\":\"synthetic-report\",\"experience_sequence\":1,\"job_id\":\"synthetic-task\",\"delivery_id\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"status\":\"completed\"}");
    assert(result);
    assert(friday_experience_accept(&guard,result)==FRIDAY_EXPERIENCE_ACCEPT);
    assert(friday_experience_accept(&guard,result)==FRIDAY_EXPERIENCE_DISCARD_RESULT);
    assert(friday_experience_begin(&guard,"synthetic-recover"));
    cJSON_ReplaceItemInObject(result,"experience_id",cJSON_CreateString("synthetic-recover"));
    assert(friday_experience_accept(&guard,result)==FRIDAY_EXPERIENCE_ACCEPT);
    cJSON_AddTrueToObject(result,"experience_historical");
    assert(friday_experience_accept(&guard,result)==FRIDAY_EXPERIENCE_DISCARD_RESULT);
    cJSON_DeleteItemFromObject(result,"experience_historical");
    cJSON_ReplaceItemInObject(result,"experience_epoch",cJSON_CreateString("cccccccccccccccccccccccccccccccc"));
    assert(friday_experience_accept(&guard,result)==FRIDAY_EXPERIENCE_DISCARD_RESULT);
    cJSON_Delete(result);
    assert(!friday_experience_session(&guard,"invalid"));
    puts("Native experience result/recovery/duplicate/history guards: PASS");
    return 0;
}
