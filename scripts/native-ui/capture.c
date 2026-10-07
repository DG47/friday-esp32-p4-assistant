#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <assert.h>
#include <time.h>
#include "lvgl.h"
#include "friday_ui.h"
#include "friday_presence.h"
static uint32_t pixels[720*720];
static uint32_t tick;
static int actions;
static char last_action[64];
#ifdef FRIDAY_NATIVE_SENDER
extern bool friday_backend_scene_action(const char *action);
static int native_sender_result=-1,native_sender_calls;
static bool native_sender_supersede;
static char native_sender_request[129],native_sender_action[64];
extern void native_sender_set_ready(bool ready);
int native_sender_send_text(const char *payload,int timeout)
{
    assert(timeout==250);native_sender_calls++;
    cJSON *packet=cJSON_Parse(payload);assert(packet);
    cJSON *request=cJSON_GetObjectItem(packet,"request_id");assert(cJSON_IsString(request));
    snprintf(native_sender_request,sizeof(native_sender_request),"%s",request->valuestring);
    cJSON *action=cJSON_GetObjectItem(packet,"action");assert(cJSON_IsString(action));
    snprintf(native_sender_action,sizeof(native_sender_action),"%s",action->valuestring);cJSON_Delete(packet);
    if(native_sender_supersede)friday_ui_media_begin("sender-newer-owner");
    return native_sender_result;
}
#endif

/* Model a renderer holding the recursive display lock for 211 ms. No sleeps,
 * device access, or audio content: exercise the production meter call itself. */
static int display_hold_ms, display_requested_ms, display_unlocks;
bool bsp_display_lock(int ms) {
    display_requested_ms=ms;
    return display_hold_ms==0 || ms<0 || ms>=display_hold_ms;
}
void bsp_display_unlock(void) {display_unlocks++;}
#ifndef FRIDAY_BASELINE
static void capture_lock_regression(void) {
    display_hold_ms=211;
    int unlocks=display_unlocks;
    friday_ui_set_microphone("MICS ONLINE",0,100);
    assert(display_requested_ms==0);
    assert(display_unlocks==unlocks); /* Busy: no LVGL mutation/unlock. */
    display_hold_ms=0;
    friday_ui_set_microphone("MICS ONLINE",0,200);
    assert(display_requested_ms==0 && display_unlocks==unlocks+1);
    puts("Capture meter skips busy display lock and resumes: PASS");
}
#endif
static bool action(const char *id) {assert(id && *id);snprintf(last_action,sizeof(last_action),"%s",id);actions++;return true;}
int64_t esp_timer_get_time(void) {return (int64_t)tick*1000;}
static void flush(lv_display_t *d,const lv_area_t *a,uint8_t *p) {
    uint16_t *src=(uint16_t *)p;
    for(int y=a->y1;y<=a->y2;y++) for(int x=a->x1;x<=a->x2;x++) {uint16_t v=*src++;pixels[y*720+x]=(((v>>11)*255/31)<<16)|((((v>>5)&63)*255/63)<<8)|((v&31)*255/31);}
    lv_display_flush_ready(d);
}
static void capture(const char *dir,const char *name) {
    clock_t start=clock();tick+=100;lv_tick_inc(100);lv_timer_handler();lv_refr_now(NULL);
    printf("HOST_RENDER scene=%s cpu_ms=%.2f\n",name,1000.0*(clock()-start)/CLOCKS_PER_SEC);
    lv_obj_t *screen=lv_screen_active();
    for(uint32_t i=0;i<lv_obj_get_child_count(screen);i++) {
        lv_obj_t *o=lv_obj_get_child(screen,i);if(!lv_obj_is_visible(o)) continue;
        lv_area_t a;lv_obj_get_coords(o,&a);
        if(!(a.x1>=0 && a.y1>=0 && a.x2<720 && a.y2<720)) fprintf(stderr,"BOUNDS %s child=%u [%d,%d,%d,%d]\n",name,i,a.x1,a.y1,a.x2,a.y2);
#ifndef FRIDAY_BASELINE
        assert(a.x1>=0 && a.y1>=0 && a.x2<720 && a.y2<720);
#endif
        if(lv_obj_has_flag(o,LV_OBJ_FLAG_CLICKABLE)) assert(a.x2-a.x1+1>=48 && a.y2-a.y1+1>=48);
    }
    char path[1024];snprintf(path,sizeof(path),"%s/%s.ppm",dir,name);
    FILE *f=fopen(path,"wb");assert(f);fprintf(f,"P6\n720 720\n255\n");
    for(int i=0;i<720*720;i++) {uint8_t rgb[]={pixels[i]>>16,pixels[i]>>8,pixels[i]};fwrite(rgb,1,3,f);}fclose(f);
}
static void scene(const char *json) {cJSON *s=cJSON_Parse(json);assert(s);assert(friday_ui_set_scene(s));cJSON_Delete(s);}
#ifndef FRIDAY_COMPARE_BASE
static bool visible_text(lv_obj_t *object,const char *needle)
{
    if(!lv_obj_is_visible(object))return false;
    if(lv_obj_check_type(object,&lv_label_class)&&strstr(lv_label_get_text(object),needle))return true;
    for(uint32_t i=0;i<lv_obj_get_child_count(object);i++)if(visible_text(lv_obj_get_child(object,i),needle))return true;
    return false;
}
static void weather_text_fits(lv_obj_t *object) {
    if(lv_obj_has_flag(object,LV_OBJ_FLAG_HIDDEN))return;
    if(lv_obj_check_type(object,&lv_label_class)) {
        const char *text=lv_label_get_text(object);
        if(text[0]) {lv_point_t size;lv_text_get_size(&size,text,lv_obj_get_style_text_font(object,0),0,0,lv_obj_get_width(object),LV_TEXT_FLAG_NONE);assert(size.y<=lv_obj_get_height(object));}
    }
    for(unsigned i=0;i<lv_obj_get_child_count(object);i++)weather_text_fits(lv_obj_get_child(object,i));
}
static void shown(const char *text){if(!visible_text(lv_screen_active(),text))fprintf(stderr,"MISSING_TEXT %s\n",text);assert(visible_text(lv_screen_active(),text));}
static void hidden_text(const char *text){assert(!visible_text(lv_screen_active(),text));}
static void metadata_audit(const char *directory)
{
    friday_ui_media_clear();
    scene("{\"kind\":\"ambient\",\"title\":\"Friday\",\"subtitle\":\"Ready\"}");
    friday_ui_set_network("WI-FI ONLINE","Connected at 203.0.113.10");friday_ui_set_backend("FRIDAY AUTHENTICATING");friday_ui_set_microphone("MICS ONLINE",9,9);
    hidden_text("203.0.113.10");hidden_text("AUTHENTICATING");hidden_text("MICS ONLINE");capture(directory,"audit-ambient");
    friday_ui_set_tool("get_calendar","working");hidden_text("get_calendar");shown("One moment");capture(directory,"audit-tool");
    scene("{\"kind\":\"message\",\"title\":\"Remember 1/1\",\"subtitle\":\"My note about provider codes and metadata is user content.\",\"actions\":[{\"id\":\"confirm:test\",\"label\":\"Remember this\"},{\"id\":\"cancel\",\"label\":\"Cancel\"}]}");
    shown("My note about provider codes and metadata is user content.");friday_ui_set_tool("get_calendar","complete");
    friday_ui_set_network("WI-FI SETUP","Join Demo-Setup and open 192.0.2.1.");hidden_text("Demo-Setup");shown("My note about provider codes and metadata is user content.");capture(directory,"audit-confirmation");
    scene("{\"kind\":\"message\",\"title\":\"Memory #42 1/1\",\"subtitle\":\"Keep mornings free for focused work.\",\"items\":[{\"time\":\"Source\",\"label\":\"Explicit user request\"},{\"time\":\"Created\",\"label\":\"2026-10-01T12:00:00\"},{\"time\":\"Updated\",\"label\":\"2026-10-02T12:00:00\"},{\"time\":\"Edit\",\"label\":\"update memory #42: new text\"}],\"actions\":[{\"id\":\"memforget:42\",\"label\":\"Forget\"},{\"id\":\"memories\",\"label\":\"Back\"}]}");
    hidden_text("#42");hidden_text("Explicit user request");hidden_text("2026-10");shown("Keep mornings free for focused work.");shown("Memory 1/1");capture(directory,"audit-memory-detail");
    scene("{\"kind\":\"list\",\"title\":\"What Friday remembers\",\"subtitle\":\"Local only; extraction disabled\",\"items\":[{\"time\":\"#42\",\"label\":\"Keep mornings free for focused work.\",\"action\":\"memory:42\"}],\"actions\":[{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}");
    hidden_text("#42");hidden_text("Local only");shown("Keep mornings free for focused work.");capture(directory,"audit-memory-list");
    scene("{\"kind\":\"list\",\"title\":\"Your reminders\",\"subtitle\":\"1 active; page 1\",\"items\":[{\"time\":\"15:30\",\"label\":\"#12 Stretch\",\"action\":\"reminder:12\"}],\"actions\":[{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}");
    hidden_text("#12");hidden_text("page 1");shown("Stretch");shown("15:30");capture(directory,"audit-reminders");
    scene("{\"kind\":\"reminder\",\"title\":\"Reminder #12\",\"subtitle\":\"Stretch\",\"items\":[{\"time\":\"Due\",\"label\":\"15:30\"}],\"actions\":[{\"id\":\"complete:12\",\"label\":\"Complete\"},{\"id\":\"snooze:12\",\"label\":\"Snooze\"}]}");
    hidden_text("#12");shown("Stretch");shown("15:30");capture(directory,"audit-reminder");
    scene("{\"kind\":\"timeline\",\"title\":\"Today\",\"subtitle\":\"Thursday, October 1\\nGoogle Calendar; read only; 1 event; America/New_York\",\"items\":[{\"time\":\"09:00\",\"label\":\"Synthetic planning\",\"action\":\"event:fixture\"}],\"actions\":[{\"id\":\"calendar_refresh\",\"label\":\"Refresh\"},{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}");
    friday_ui_set_response("Google Calendar SOURCE_SENTINEL; read only; America/New_York");hidden_text("SOURCE_SENTINEL");hidden_text("Google Calendar");hidden_text("America/New_York");shown("09:00");shown("Synthetic planning");capture(directory,"audit-calendar");
    scene("{\"kind\":\"timeline\",\"title\":\"Today\",\"subtitle\":\"Thursday, October 1\\nGoogle Calendar; No events today\",\"actions\":[{\"id\":\"calendar_refresh\",\"label\":\"Refresh\"},{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}");
    hidden_text("Google Calendar");shown("No events today.");capture(directory,"audit-calendar-empty");
    scene("{\"kind\":\"timeline\",\"title\":\"Today\",\"subtitle\":\"Cached calendar; stale\",\"items\":[{\"time\":\"09:00\",\"label\":\"Synthetic planning\"}],\"actions\":[{\"id\":\"calendar_refresh\",\"label\":\"Refresh\"}]}");
    hidden_text("Cached calendar");shown("Refresh to see the latest events.");
    scene("{\"kind\":\"media\",\"title\":\"Synthetic song\",\"subtitle\":\"A complete synthetic lyric line\",\"items\":[{\"time\":\"Artist\",\"label\":\"Synthetic artist\"},{\"time\":\"Progress\",\"label\":\"1:24 / 3:42\"},{\"time\":\"Source\",\"label\":\"LRCLIB record 999; metadata matched; rights unverified\"},{\"time\":\"State\",\"label\":\"Stale; controls hidden\"}],\"actions\":[{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}");
    shown("Synthetic artist");shown("A complete synthetic lyric line");shown("1:24 / 3:42");shown("LRCLIB");shown("rights unverified");hidden_text("record 999");hidden_text("metadata matched");hidden_text("controls hidden");hidden_text("CURRENT LINE");capture(directory,"audit-music-credit");
    scene("{\"kind\":\"metric\",\"title\":\"22 C\",\"subtitle\":\"A warm afternoon\",\"items\":[{\"time\":\"50%\",\"label\":\"Humidity\"},{\"time\":\"12 km/h\",\"label\":\"Wind\"},{\"time\":\"2026-10-01T12:00:00\",\"label\":\"Open-Meteo · UTC\"}],\"actions\":[{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}");
    shown("Humidity");shown("Wind");shown("Weather: Open-Meteo");hidden_text("UTC");hidden_text("2026-10-01T12:00:00");capture(directory,"audit-weather");
    const char *weather_names[]={"weather-both","weather-07306","weather-10010","weather-partial","weather-unavailable","weather-extremes","weather-forecast-unknown","weather-dst","weather-low","weather-current-rain","weather-night","weather-condition-unknown"};
    for(unsigned i=0;i<12;i++) {
        char path[1024],data[8192]={0};snprintf(path,sizeof(path),"%s/../%s.json",directory,weather_names[i]);
        FILE *file=fopen(path,"rb");assert(file);assert(fread(data,1,sizeof(data)-1,file)>0);fclose(file);
        scene(data);hidden_text("UTC");shown("Weather: Open-Meteo");hidden_text("latitude");hidden_text("longitude");
        if(i!=2)shown("Jersey City, NJ");if(i!=1)shown("New York, NY");
        if(i==3||i==4)shown("Weather unavailable");
        if(i<4||i>5)shown("Humidity 65%");
        if(i==6)shown("Rain / snow: unknown");if(i==7){shown("1 am EDT");shown("1 am EST");}if(i==8)shown("Low rain / snow chance");
        hidden_text("07306");hidden_text("10010");shown("Next full hour");shown("Rain / snow:");
        if(i==5){shown("-100 °C");shown("100 °C");shown("Wind 500 km/h");shown("Humidity 100%");}
        lv_obj_update_layout(lv_screen_active());weather_text_fits(lv_screen_active());capture(directory,weather_names[i]);
    }
    puts("WEATHER_PRESENTATION_AUDIT cases=12 city-state/ZIP-hidden/source-credit/full-text-fit/both/either/partial/unavailable/extremes PASS");
    const char *kinds[]={"comparison","person","email","map","progress","graph","camera","artifact","timer"};
    for(unsigned i=0;i<sizeof(kinds)/sizeof(kinds[0]);i++){friday_ui_set_surface(kinds[i],"User content","My note about provider codes and metadata is user content.");shown("My note about provider codes and metadata is user content.");hidden_text(kinds[i]);}
    friday_ui_set_surface("ERROR","Friday","Expected a JSON object; request_id=TRACE_SENTINEL");hidden_text("JSON");hidden_text("request_id");hidden_text("TRACE_SENTINEL");shown("Please try again.");capture(directory,"audit-error");
    friday_ui_set_backend("VOICE UNAVAILABLE — TEXT READY");friday_ui_set_state(FRIDAY_STATE_IDLE);shown("Voice isn't available");hidden_text("TEXT READY");capture(directory,"audit-voice-error");friday_ui_set_state(FRIDAY_STATE_SPEAKING);
    friday_ui_set_surface("PAIRING","Friday","Pairing code: ABCD-EFGH");shown("ABCD-EFGH");capture(directory,"audit-pairing");
    friday_ui_set_surface("ambient","Friday","Ready");friday_ui_set_network("WI-FI SETUP","Join Demo-Setup and open 192.0.2.1.");shown("Demo-Setup");shown("192.0.2.1");capture(directory,"audit-setup");
    puts("ALL_SCREEN_METADATA_AUDIT generated diagnostics hidden, user content/credits/errors/protected text retained: PASS");
}
static void activity_copy_audit(const char *directory)
{
    friday_ui_media_clear();friday_ui_set_surface("ambient","Friday","Touch the light to begin.");
    friday_ui_set_state(FRIDAY_STATE_IDLE);friday_ui_set_state(FRIDAY_STATE_THINKING);
    shown("A little thought before a useful answer.");friday_ui_set_state(FRIDAY_STATE_IDLE);
    hidden_text("A little thought before a useful answer.");shown("Touch the light to begin.");
    friday_ui_set_state(FRIDAY_STATE_THINKING);
    friday_ui_set_network("WI-FI SETUP","Join Synthetic-Setup and open 192.0.2.10.");
    for(int i=0;i<10;i++) {friday_ui_set_state(FRIDAY_STATE_THINKING);shown("Synthetic-Setup");}
    for(int i=0;i<10;i++) {friday_ui_set_state(FRIDAY_STATE_IDLE);shown("Synthetic-Setup");}
    capture(directory,"presence-preserved-setup");
    const char *explicit_copy[]={"A little thought before a useful answer.","My own note about thinking and listening.","Pairing code: DEMO-CODE"};
    for(unsigned i=0;i<3;i++) {
        friday_ui_set_state(FRIDAY_STATE_THINKING);friday_ui_set_response(explicit_copy[i]);
        for(int j=0;j<10;j++)friday_ui_set_state(FRIDAY_STATE_THINKING);
        shown(explicit_copy[i]);friday_ui_set_state(FRIDAY_STATE_IDLE);shown(explicit_copy[i]);
        friday_ui_set_state(FRIDAY_STATE_IDLE);shown(explicit_copy[i]);
    }
    friday_ui_set_surface("PAIRING","Friday","Pairing code: DEMO-CODE");
    for(int i=0;i<10;i++){friday_ui_set_state(FRIDAY_STATE_IDLE);shown("DEMO-CODE");}
    puts("ACTIVITY_COPY provenance: generated thinking cleared; setup/pairing/explicit and identical user copy retained through repeated/idle states PASS");
}
static lv_obj_t *notice_label(void)
{
    lv_obj_t *screen=lv_screen_active();
    for(uint32_t i=0;i<lv_obj_get_child_count(screen);i++) {
        lv_obj_t *object=lv_obj_get_child(screen,i);
        if(lv_obj_is_visible(object)&&lv_obj_check_type(object,&lv_label_class)) {
            const char *text=lv_label_get_text(object);
            if(!strcmp(text,"Microphone unavailable. Use touch controls.")||
                !strcmp(text,"Voice isn't available. You can read the response.")||
                !strcmp(text,"Microphone and voice unavailable. Use touch controls.")||
                strstr(text,"Finish or cancel")||strstr(text,"finish or cancel")||(strstr(text,"Could not open Music")||strstr(text,"Could not open Weather")))return object;
        }
    }
    return NULL;
}
static void notice_geometry(void)
{
    lv_obj_t *screen=lv_screen_active();lv_obj_update_layout(screen);
    lv_obj_t *notice=notice_label();assert(notice);lv_area_t area;lv_obj_get_coords(notice,&area);
    assert(area.x1>=0&&area.x2<720&&area.y1>=0&&area.y2<28);
    lv_point_t size;lv_text_get_size(&size,lv_label_get_text(notice),lv_obj_get_style_text_font(notice,0),0,0,lv_obj_get_width(notice),LV_TEXT_FLAG_NONE);
    assert(size.y<=lv_obj_get_height(notice));
    for(uint32_t i=0;i<lv_obj_get_child_count(screen);i++) {
        lv_obj_t *object=lv_obj_get_child(screen,i);if(object==notice||!lv_obj_is_visible(object))continue;
        lv_area_t other;lv_obj_get_coords(object,&other);
        /* Not merely outside buttons: no overlap with content, credits or ribbon. */
        assert(area.x2<other.x1||other.x2<area.x1||area.y2<other.y1||other.y2<area.y1);
    }
}
static void deferred_notice_tick(void){tick+=120;lv_tick_inc(120);lv_timer_handler();}
static void recovery_audit(const char *directory)
{
    const char *surfaces[]={
        "{\"kind\":\"timeline\",\"title\":\"Today\",\"subtitle\":\"Thursday, October 1\\nGoogle Calendar; read only\",\"items\":[{\"time\":\"09:00\",\"label\":\"Studio planning\"},{\"time\":\"12:00\",\"label\":\"Lunch\"},{\"time\":\"15:00\",\"label\":\"Project review\"},{\"time\":\"17:00\",\"label\":\"Wrap up\"}],\"actions\":[{\"id\":\"calendar:0\",\"label\":\"Previous\"},{\"id\":\"calendar:2\",\"label\":\"Next\"},{\"id\":\"calendar_refresh\",\"label\":\"Refresh\"},{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}",
        "{\"kind\":\"message\",\"title\":\"Remember 1/1\",\"subtitle\":\"My provider and metadata note stays complete.\",\"actions\":[{\"id\":\"confirm:test\",\"label\":\"Remember this\"},{\"id\":\"cancel\",\"label\":\"Cancel\"}]}",
        "{\"kind\":\"media\",\"title\":\"Quiet Satellites\",\"subtitle\":\"A complete original lyric line\",\"items\":[{\"time\":\"Artist\",\"label\":\"The Paper Observatory\"},{\"time\":\"Source\",\"label\":\"Spotify\"},{\"time\":\"Progress\",\"label\":\"1:24 / 3:42\"}],\"actions\":[{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}",
        "{\"kind\":\"list\",\"title\":\"Your reminders\",\"items\":[{\"time\":\"15:30\",\"label\":\"#12 Stretch\",\"action\":\"reminder:12\"}],\"actions\":[{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}",
        "{\"kind\":\"timer\",\"title\":\"Timer running\",\"subtitle\":\"Your timer is set.\",\"countdown_ms\":120000,\"actions\":[{\"id\":\"cancel:12\",\"label\":\"Cancel\"}]}",
        "{\"kind\":\"metric\",\"title\":\"At your service\",\"actions\":[{\"id\":\"memories\",\"label\":\"Memory\"},{\"id\":\"reminders\",\"label\":\"Reminders\"},{\"id\":\"calendar\",\"label\":\"Calendar\"},{\"id\":\"music\",\"label\":\"Music\"}]}",
        "{\"kind\":\"timeline\",\"title\":\"Today\",\"subtitle\":\"Thursday, October 1\\nNo events today\",\"actions\":[{\"id\":\"calendar_refresh\",\"label\":\"Refresh\"},{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}",
        "{\"kind\":\"artifact\",\"title\":\"Design catch-up\",\"items\":[{\"time\":\"When\",\"label\":\"Thu Oct 01 - 11:00\"},{\"time\":\"Where\",\"label\":\"The studio\"}],\"actions\":[{\"id\":\"calendar\",\"label\":\"Today\"},{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}",
        "{\"kind\":\"ambient\",\"title\":\"Friday\",\"subtitle\":\"Say Friday or touch the light.\"}",
        "{\"kind\":\"PAIRING\",\"title\":\"Friday\",\"subtitle\":\"Pairing code: ABCD-EFGH\"}",
        "{\"kind\":\"ERROR\",\"title\":\"Friday\",\"subtitle\":\"Expected a JSON object\"}"
    };
    const char *content[]={"Studio planning","My provider and metadata note stays complete.","A complete original lyric line","Stretch","Your timer is set.","Memory","No events today.","The studio","Say Friday or touch the light.","ABCD-EFGH","Please try again."};
    friday_ui_set_backend("FRIDAY ONLINE");friday_ui_set_microphone("MICS ONLINE",0,0);
    int checks=0;
    for(int cycle=0;cycle<12;cycle++)for(unsigned sample=0;sample<sizeof(surfaces)/sizeof(surfaces[0]);sample++) {
        scene(surfaces[sample]);shown(content[sample]);assert(!notice_label());
        friday_ui_set_microphone("MIC ERROR",0,0);shown("Microphone unavailable");notice_geometry();shown(content[sample]);hidden_text("MIC ERROR");checks++;
        friday_ui_set_microphone("MICS NOT READY",0,0);shown("Microphone unavailable");
        friday_ui_set_microphone("MICS ONLINE",10,4);assert(!notice_label());shown(content[sample]);
        /* The read-error path must remain visible even if capture's one-shot
         * notification arrives while the renderer owns its recursive lock. */
        display_hold_ms=211;int unlocks=display_unlocks;
        friday_ui_set_microphone("MIC READ ERROR",0,0);assert(display_requested_ms==0&&display_unlocks==unlocks);
        assert(!notice_label());display_hold_ms=0;deferred_notice_tick();shown("Microphone unavailable");notice_geometry();shown(content[sample]);checks++;
        if(cycle==0&&sample==0)capture(directory,"recovery-calendar-mic");
        friday_ui_set_backend("VOICE UNAVAILABLE — TEXT READY");shown("Microphone and voice unavailable");notice_geometry();shown(content[sample]);checks++;
        if(cycle==0&&sample==1)capture(directory,"recovery-protected-both");
        friday_ui_set_microphone("MICS ONLINE",8,4);shown("Voice isn't available");notice_geometry();shown(content[sample]);
        if(sample==2)shown("Spotify");
        if(cycle==0&&sample==0)capture(directory,"recovery-calendar-voice");
        if(cycle==0&&sample==2)capture(directory,"recovery-music-voice");
        friday_ui_set_backend("FRIDAY ONLINE");assert(!notice_label());shown(content[sample]);
        friday_ui_set_backend("SPEAKER ERROR");shown("Voice isn't available");notice_geometry();shown(content[sample]);checks++;
        /* A scene change must neither hide a pending failure nor rewrite user
         * content. This includes Calendar->protected review and back. */
        scene(surfaces[(sample+1)%(sizeof(surfaces)/sizeof(surfaces[0]))]);notice_geometry();
        scene(surfaces[sample]);shown(content[sample]);shown("Voice isn't available");
        friday_ui_set_backend("FRIDAY ONLINE");assert(!notice_label());shown(content[sample]);
        friday_ui_set_microphone("MIC READ ERROR",0,0);notice_geometry();
        display_hold_ms=211;unlocks=display_unlocks;friday_ui_set_microphone("MICS ONLINE",0,0);
        assert(display_requested_ms==0&&display_unlocks==unlocks);assert(notice_label());
        display_hold_ms=0;deferred_notice_tick();assert(!notice_label());shown(content[sample]);
        if(cycle==0&&sample==0)capture(directory,"recovery-calendar-restored");
    }
    printf("RECOVERY_NOTICE_AUDIT cases=%d cycles=12 scenes=11 busy-zero-wait/pending/clear/no-overlap/no-content-loss PASS\n",checks);
}
static const char *field(const cJSON *o,const char *key);
#ifdef FRIDAY_NATIVE_SENDER
static void sender_failure_audit(const char *directory)
{
    friday_ui_media_clear();friday_ui_media_begin("sender-original");
    cJSON *frame=cJSON_Parse("{\"type\":\"surface\",\"media_request_id\":\"sender-original\",\"media_session_id\":\"0123456789abcdef01234567\",\"media_sequence\":1,\"surface\":{\"kind\":\"media\",\"title\":\"Original music\"}}");assert(frame&&friday_ui_set_media(frame));
    cJSON *binding=cJSON_Parse("{\"type\":\"surface\",\"media_overlay\":{\"state\":\"suspend\",\"media_request_id\":\"sender-original\",\"media_session_id\":\"0123456789abcdef01234567\",\"overlay_id\":\"sender-protected\",\"media_sequence\":1},\"surface\":{\"kind\":\"message\",\"actions\":[{\"id\":\"memno:ffffffffffffffffffffffff\",\"label\":\"Cancel review\"}]}}");
    assert(binding&&friday_ui_media_prepare_overlay(binding));cJSON_Delete(binding);
    scene("{\"kind\":\"message\",\"title\":\"Protected review\",\"subtitle\":\"Keep these protected facts unchanged.\",\"actions\":[{\"id\":\"music\",\"label\":\"Music\"},{\"id\":\"memno:ffffffffffffffffffffffff\",\"label\":\"Cancel review\"}]}");
    uint32_t objects=lv_obj_get_child_count(lv_screen_active());
    native_sender_result=-1;
    for(int i=0;i<25;i++){
        assert(!friday_backend_scene_action("music"));assert(friday_ui_media_suspended());assert(friday_ui_media_resume_request()==NULL);
        shown("Keep these protected facts unchanged.");assert(lv_obj_get_child_count(lv_screen_active())==objects);
        cJSON_ReplaceItemInObject(frame,"media_request_id",cJSON_CreateString(native_sender_request));assert(!friday_ui_set_media(frame));
    }
    /* Exercise the real touch wrapper too: failed send must not replace facts. */
    friday_ui_set_action_callback(friday_backend_scene_action);int calls=native_sender_calls;
    lv_obj_t *screen=lv_screen_active();
    for(uint32_t j=0;j<lv_obj_get_child_count(screen);j++){
        lv_obj_t *object=lv_obj_get_child(screen,j);
        if(lv_obj_is_visible(object)&&lv_obj_check_type(object,&lv_button_class)&&visible_text(object,"Music"))lv_obj_send_event(object,LV_EVENT_CLICKED,NULL);
    }
    assert(native_sender_calls==calls+1);shown("Keep these protected facts unchanged.");shown("Could not open Music");notice_geometry();
    assert(friday_ui_media_suspended());assert(friday_ui_media_resume_request()==NULL);capture(directory,"music-failed-send-protected");
    /* A newer staged owner during transport failure must survive old rollback. */
    native_sender_supersede=true;calls=native_sender_calls;
    for(uint32_t j=0;j<lv_obj_get_child_count(screen);j++){
        lv_obj_t *object=lv_obj_get_child(screen,j);
        if(lv_obj_is_visible(object)&&lv_obj_check_type(object,&lv_button_class)&&visible_text(object,"Music"))lv_obj_send_event(object,LV_EVENT_CLICKED,NULL);
    }
    assert(native_sender_calls==calls+1);native_sender_supersede=false;
    shown("Keep these protected facts unchanged.");
    assert(friday_ui_media_suspended());
    cJSON_ReplaceItemInObject(frame,"media_request_id",cJSON_CreateString("sender-newer-owner"));assert(friday_ui_set_media(frame));assert(!friday_ui_media_suspended());
    friday_ui_media_begin("retry-original");cJSON_ReplaceItemInObject(frame,"media_request_id",cJSON_CreateString("retry-original"));assert(friday_ui_set_media(frame));
    native_sender_result=1;assert(friday_backend_scene_action("music"));
    cJSON_ReplaceItemInObject(frame,"media_request_id",cJSON_CreateString(native_sender_request));assert(friday_ui_set_media(frame));
    cJSON_Delete(frame);friday_ui_set_action_callback(action);friday_ui_media_clear();
    printf("ACTUAL_SENDER_FAILURE_AUDIT failures=27 protected-owner/facts/controls/no-objects/stale-frame-rejection/newer-owner/retry PASS\n");
}
#endif
static void music_recovery_route_audit(const char *directory)
{
    char path[1024];snprintf(path,sizeof(path),"%s/../music-recovery-fixture.json",directory);
    FILE *file=fopen(path,"rb");assert(file);char data[16384]={0};assert(fread(data,1,sizeof(data)-1,file)>0);fclose(file);
    cJSON *packets=cJSON_Parse(data);assert(packets);cJSON *packet=NULL;int checks=0;
    friday_ui_set_microphone("MIC READ ERROR",0,0);
    cJSON_ArrayForEach(packet,packets){
        const cJSON *surface=cJSON_GetObjectItem(packet,"surface");
        if(surface){
            assert(friday_ui_set_scene(surface));
            const char *title=field(surface,"title");shown(title);shown("Microphone unavailable");notice_geometry();
            const cJSON *first=cJSON_GetArrayItem(cJSON_GetObjectItem(surface,"actions"),0);
            const char *expected=field(first,"id");int prior=actions;
            lv_obj_t *screen=lv_screen_active();
            for(uint32_t j=0;j<lv_obj_get_child_count(screen);j++){
                lv_obj_t *object=lv_obj_get_child(screen,j);
                if(lv_obj_is_visible(object)&&lv_obj_check_type(object,&lv_button_class)){
                    lv_obj_send_event(object,LV_EVENT_CLICKED,NULL);assert(!strcmp(last_action,expected));
                }
            }
            assert(actions==prior+1);checks++;
            capture(directory,!strcmp(title,"Request cancelled")?"music-request-cancelled":"music-touch-confirmation");
        }else{
            friday_ui_media_clear();friday_ui_media_begin("notice-original");
            cJSON *original=cJSON_Parse("{\"type\":\"surface\",\"media_request_id\":\"notice-original\",\"media_session_id\":\"0123456789abcdef01234567\",\"media_sequence\":1,\"surface\":{\"kind\":\"media\",\"title\":\"Synthetic music\"}}");
            assert(original&&friday_ui_set_media(original));
            cJSON *binding=cJSON_Parse("{\"type\":\"surface\",\"media_overlay\":{\"state\":\"suspend\",\"media_request_id\":\"notice-original\",\"media_session_id\":\"0123456789abcdef01234567\",\"overlay_id\":\"notice-owned\",\"media_sequence\":1},\"surface\":{\"kind\":\"reminder\"}}");
            assert(binding&&friday_ui_media_prepare_overlay(binding));cJSON_Delete(binding);
            bool alert=!strcmp(field(packet,"fixture_owner"),"reminder");
            if(alert)scene("{\"kind\":\"reminder\",\"title\":\"Reminder alert\",\"subtitle\":\"Keep this protected review visible.\",\"actions\":[{\"id\":\"snooze:12\",\"label\":\"Snooze\"}]}");
            else scene("{\"kind\":\"message\",\"title\":\"Review request\",\"subtitle\":\"Keep this protected review visible.\",\"actions\":[{\"id\":\"memno:ffffffffffffffffffffffff\",\"label\":\"Cancel review\"}]}");
            friday_ui_media_begin("notice-denied-music");assert(friday_ui_media_suspended());
            assert(friday_ui_media_resume_request()==NULL);
            display_hold_ms=211;int unlocks=display_unlocks;
            assert(friday_ui_handle_notice(packet));assert(display_requested_ms==0&&display_unlocks==unlocks);
            shown("Keep this protected review visible.");display_hold_ms=0;deferred_notice_tick();
            assert(friday_ui_media_suspended());assert(friday_ui_media_resume_request()==NULL);
            cJSON_ReplaceItemInObject(original,"media_request_id",cJSON_CreateString("notice-denied-music"));
            assert(!friday_ui_set_media(original));cJSON_Delete(original);
            shown("Keep this protected review visible.");shown("Finish or cancel");shown("Voice unavailable");notice_geometry();
            int prior=actions;lv_obj_t *screen=lv_screen_active();
            for(uint32_t j=0;j<lv_obj_get_child_count(screen);j++){lv_obj_t *object=lv_obj_get_child(screen,j);if(lv_obj_is_visible(object)&&lv_obj_check_type(object,&lv_button_class))lv_obj_send_event(object,LV_EVENT_CLICKED,NULL);}
            assert(actions==prior+1&&!strcmp(last_action,alert?"snooze:12":"memno:ffffffffffffffffffffffff"));
            for(int repeat=0;repeat<10;repeat++) {
                friday_ui_media_begin("notice-denied-music");assert(friday_ui_media_suspended());
                assert(friday_ui_handle_notice(packet));assert(friday_ui_media_resume_request()==NULL);
                shown("Keep this protected review visible.");notice_geometry();
            }
            capture(directory,alert?"music-alert-notice":"music-protected-notice");checks++;
            tick+=5200;lv_tick_inc(5200);deferred_notice_tick();shown("Microphone unavailable");
            assert(friday_ui_media_suspended());assert(friday_ui_media_resume_request()==NULL);
            friday_ui_media_begin("notice-allowed-music");
            cJSON *allowed=cJSON_Parse("{\"type\":\"surface\",\"media_request_id\":\"notice-allowed-music\",\"media_session_id\":\"abcdef0123456789abcdef01\",\"media_sequence\":1,\"surface\":{\"kind\":\"media\",\"title\":\"Allowed synthetic music\"}}");
            assert(allowed&&friday_ui_set_media(allowed));assert(!friday_ui_media_suspended());cJSON_Delete(allowed);

        }
    }
    cJSON_Delete(packets);friday_ui_set_microphone("MICS ONLINE",0,0);scene("{\"kind\":\"ambient\"}");
    printf("MUSIC_RECOVERY_ROUTE_AUDIT backend-packets=%d microphone-unavailable/native-bound-actions/protected-scene/busy-deferred PASS\n",checks);
}
#endif
#ifndef FRIDAY_BASELINE
static bool artwork_visible(void)
{
    lv_obj_t *screen=lv_screen_active();
    for(uint32_t i=0;i<lv_obj_get_child_count(screen);i++) {
        lv_obj_t *o=lv_obj_get_child(screen,i);
        for(uint32_t j=0;j<lv_obj_get_child_count(o);j++){lv_obj_t *child=lv_obj_get_child(o,j);if(lv_obj_check_type(child,&lv_image_class)&&lv_obj_is_visible(child))return true;}
    }
    return false;
}
static const char *field(const cJSON *o,const char *key){cJSON *v=cJSON_GetObjectItem(o,key);return cJSON_IsString(v)?v->valuestring:"";}
static void backend_replay(const char *directory)
{
    char path[1024];snprintf(path,sizeof(path),"%s/../backend-stream.jsonl",directory);FILE *f=fopen(path,"rb");if(!f)return;
    char *line=malloc(131074);assert(line);int operations=0;
    while(fgets(line,131074,f)) {
        cJSON *operation=cJSON_Parse(line);assert(operation);operations++;
        const char *op=field(operation,"op");cJSON *frame=cJSON_GetObjectItem(operation,"frame");
        if(!strcmp(op,"begin"))friday_ui_media_begin(field(operation,"request"));
        else if(!strcmp(op,"clear"))friday_ui_media_clear();
        else if(!strcmp(op,"wait")){uint32_t delta=(uint32_t)cJSON_GetNumberValue(cJSON_GetObjectItem(operation,"milliseconds"));tick+=delta;lv_tick_inc(delta);lv_timer_handler();}
        else if(!strcmp(op,"media")){bool accepted=friday_ui_set_media(frame);assert(accepted==cJSON_IsTrue(cJSON_GetObjectItem(operation,"accepted")));}
        else if(!strcmp(op,"overlay")) {
            friday_ui_media_prepare_overlay(frame);cJSON *surface=cJSON_GetObjectItem(frame,"surface");if(cJSON_IsObject(surface))assert(friday_ui_set_scene(surface));
            if(!strcmp(field(frame,"type"),"reminder.alert"))friday_ui_set_state(FRIDAY_STATE_ALERT);
        } else if(!strcmp(op,"result"))friday_ui_media_resume_result(frame);
        else if(!strcmp(op,"state"))friday_ui_set_state(!strcmp(field(frame,"state"),"idle")?FRIDAY_STATE_IDLE:FRIDAY_STATE_THINKING);
        else if(!strcmp(op,"resolved")){friday_ui_media_overlay_resolved();friday_ui_set_state(FRIDAY_STATE_IDLE);}
        else if(!strcmp(op,"action"))friday_ui_media_note_action(field(operation,"action"));
        else if(!strcmp(op,"completed"))friday_ui_media_overlay_completed(frame);
        else if(!strcmp(op,"request")) {
            cJSON *request=friday_ui_media_resume_request(),*expected=cJSON_GetObjectItem(operation,"expected");
            if(cJSON_IsNull(expected))assert(request==NULL);
            else {assert(request);const char *keys[]={"media_request_id","media_session_id","overlay_id"};for(int i=0;i<3;i++)assert(!strcmp(field(request,keys[i]),field(expected,keys[i])));}
            cJSON_Delete(request);
        } else if(!strcmp(op,"check")) {
            cJSON *art=cJSON_GetObjectItem(operation,"artwork"),*suspended=cJSON_GetObjectItem(operation,"suspended");
            if(cJSON_IsBool(art))assert(artwork_visible()==cJSON_IsTrue(art));
            if(cJSON_IsBool(suspended))assert(friday_ui_media_suspended()==cJSON_IsTrue(suspended));
            if(*field(operation,"capture"))capture(directory,field(operation,"capture"));
        }
        cJSON_Delete(operation);
    }
    free(line);fclose(f);printf("ACTUAL_BACKEND_NATIVE_REPLAY operations=%d PASS\n",operations);
}
#endif
#ifdef FRIDAY_NATIVE_SENDER
static cJSON *weather_fixture(const char *directory,const char *name)
{
    char path[1024],data[32768]={0};snprintf(path,sizeof(path),"%s/../%s",directory,name);
    FILE *file=fopen(path,"rb");assert(file);assert(fread(data,1,sizeof(data)-1,file)>0);fclose(file);
    cJSON *json=cJSON_Parse(data);assert(json);return json;
}
static void tap_caption(const char *caption)
{
    lv_obj_t *screen=lv_screen_active();int targets=0;
    for(uint32_t j=0;j<lv_obj_get_child_count(screen);j++) {
        lv_obj_t *object=lv_obj_get_child(screen,j);
        if(lv_obj_is_visible(object)&&lv_obj_check_type(object,&lv_button_class)&&visible_text(object,caption)) {
            lv_obj_send_event(object,LV_EVENT_CLICKED,NULL);targets++;
        }
    }
    assert(targets==1);
}
static void weather_touch_audit(const char *directory)
{
    cJSON *browse=weather_fixture(directory,"browse-fixtures.json");
    cJSON *menu=cJSON_GetObjectItem(browse,"local-menu");assert(menu);
    cJSON *weather=weather_fixture(directory,"weather-both.json");
    friday_ui_media_clear();friday_ui_set_backend("FRIDAY ONLINE");friday_ui_set_microphone("MICS ONLINE",0,0);
    friday_ui_set_state(FRIDAY_STATE_IDLE);
    friday_ui_set_action_callback(friday_backend_scene_action);native_sender_set_ready(true);native_sender_result=1;
    int checks=0;
    for(int cycle=0;cycle<25;cycle++) {
        assert(friday_ui_set_scene(menu));shown("Memory");shown("Reminders");shown("Calendar");shown("Music");shown("Weather");
        lv_obj_update_layout(lv_screen_active());int count=0;lv_area_t targets[5];
        lv_obj_t *screen=lv_screen_active();
        for(uint32_t j=0;j<lv_obj_get_child_count(screen);j++) {
            lv_obj_t *object=lv_obj_get_child(screen,j);
            if(lv_obj_is_visible(object)&&lv_obj_check_type(object,&lv_button_class)) {
                assert(count<5);lv_obj_get_coords(object,&targets[count]);
                assert(targets[count].x1>=48&&targets[count].x2<=671&&targets[count].y1>=188&&targets[count].y2<=627);
                assert(targets[count].x2-targets[count].x1+1>=48&&targets[count].y2-targets[count].y1+1>=48);count++;
            }
        }
        assert(count==5);
        for(int i=0;i<5;i++)for(int j=i+1;j<5;j++)assert(targets[i].x2<targets[j].x1||targets[j].x2<targets[i].x1||targets[i].y2<targets[j].y1||targets[j].y2<targets[i].y1);
        int calls=native_sender_calls;tap_caption("Weather");tap_caption("Weather");
        assert(native_sender_calls==calls+2&&!strcmp(native_sender_action,"weather"));checks+=2;
        assert(friday_ui_set_scene(weather));shown("Jersey City, NJ");shown("New York, NY");shown("Weather: Open-Meteo");hidden_text("UTC");
        if(cycle==0) {
            capture(directory,"weather-touch-result");
            assert(friday_ui_set_scene(menu));capture(directory,"weather-motion-00-opening");
            friday_ui_set_tool("get_weather","working");shown("Checking the forecast");hidden_text("An umbrella");
            for(int n=1;n<=5;n++){char name[80];snprintf(name,sizeof(name),"weather-motion-%02d-loading",n);capture(directory,name);}
            friday_ui_set_tool("get_weather","complete");assert(friday_ui_set_scene(weather));shown("An umbrella may help");
            for(int n=6;n<=10;n++){char name[80];snprintf(name,sizeof(name),"weather-motion-%02d-result",n);capture(directory,name);}
            friday_ui_set_tool("get_weather","working");assert(friday_ui_set_scene(menu));
            for(int n=0;n<5;n++){char name[80];snprintf(name,sizeof(name),"weather-cancel-%02d-menu",n);capture(directory,name);}
            assert(friday_ui_set_scene(weather));
        }
        tap_caption("Dismiss");assert(!strcmp(native_sender_action,"dismiss"));hidden_text("Weather");checks++;
    }
    assert(friday_ui_set_scene(menu));native_sender_result=-1;int calls=native_sender_calls;
    tap_caption("Weather");assert(native_sender_calls==calls+1);shown("Could not open Weather");shown("Memory");shown("Calendar");shown("Music");notice_geometry();capture(directory,"weather-touch-disconnected");checks++;
    native_sender_set_ready(false);calls=native_sender_calls;tap_caption("Weather");assert(native_sender_calls==calls);shown("Weather");checks++;
    native_sender_set_ready(true);native_sender_result=1;tap_caption("Weather");assert(!strcmp(native_sender_action,"weather"));
    assert(friday_ui_set_scene(weather));hidden_text("Could not open Weather");capture(directory,"weather-touch-recovered");checks++;
    /* A failed send cannot overwrite protected facts, even if a Weather action
     * is present on an older or concurrently owned review surface. */
    scene("{\"kind\":\"message\",\"title\":\"Review request\",\"subtitle\":\"Keep the pending facts intact.\",\"actions\":[{\"id\":\"weather\",\"label\":\"Weather\"},{\"id\":\"confirm\",\"label\":\"Confirm\"}]}");
    native_sender_result=-1;tap_caption("Weather");shown("Keep the pending facts intact.");shown("Confirm");shown("Could not open Weather");notice_geometry();checks++;
    cJSON *packets=weather_fixture(directory,"weather-touch-fixture.json"),*packet=NULL;
    cJSON_ArrayForEach(packet,packets) {
        cJSON *surface=cJSON_GetObjectItem(packet,"surface");
        if(cJSON_IsObject(surface)) {
            assert(friday_ui_set_scene(surface));
            if(!strcmp(field(surface,"title"),"Confirmation required")) {shown("Cancel request");shown("Complete the synthetic reminder?");hidden_text("Jersey City, NJ");capture(directory,"weather-touch-confirmation");}
            else {shown("Jersey City, NJ");shown("New York, NY");}
        } else {
            scene("{\"kind\":\"message\",\"title\":\"Review request\",\"subtitle\":\"Keep the pending facts intact.\",\"actions\":[{\"id\":\"confirm\",\"label\":\"Confirm\"}]}");
            display_hold_ms=211;assert(friday_ui_handle_notice(packet));display_hold_ms=0;deferred_notice_tick();
            shown("Keep the pending facts intact.");shown("Confirm");shown("open Weather");notice_geometry();capture(directory,"weather-touch-protected-notice");
        }
        checks++;
    }
    cJSON_Delete(packets);cJSON_Delete(weather);cJSON_Delete(browse);
    native_sender_result=-1;friday_ui_set_action_callback(action);scene("{\"kind\":\"ambient\"}");
    printf("WEATHER_TOUCH_AUDIT checks=%d fifth-targets/no-overlap/native-sender/repeated/dismiss/offline/recovery/protected/backend-packets PASS\n",checks);
}
#endif

int main(int argc,char **argv) {
    assert(argc==2);lv_init();lv_display_t *d=lv_display_create(720,720);
    lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);
    static uint16_t buffer[720*720];lv_display_set_buffers(d,buffer,NULL,sizeof(buffer),LV_DISPLAY_RENDER_MODE_FULL);lv_display_set_flush_cb(d,flush);
    friday_ui_create();
#ifndef FRIDAY_BASELINE
    capture_lock_regression();
#endif
    friday_ui_set_action_callback(action);friday_ui_set_network("Connected",NULL);friday_ui_set_microphone("Ready",0,0);capture(argv[1],"ambient");
    lv_obj_t *rootscreen=lv_screen_active();
    for(uint32_t child=0;child<lv_obj_get_child_count(rootscreen);child++) {
        lv_obj_t *object=lv_obj_get_child(rootscreen,child);
        if(lv_obj_is_visible(object)&&lv_obj_has_flag(object,LV_OBJ_FLAG_CLICKABLE)) {
            int prior=actions;lv_obj_send_event(object,LV_EVENT_LONG_PRESSED,NULL);
            assert(actions==prior+1);lv_obj_send_event(object,LV_EVENT_CLICKED,NULL);assert(actions==prior+1);
        }
    }
    actions=0;
    const char *names[]={"listening","reasoning","speaking","attention"};
    for(int i=1;i<=4;i++){friday_ui_set_state((friday_state_t)i);capture(argv[1],names[i-1]);}
    friday_ui_set_state(FRIDAY_STATE_IDLE);
    char browsepath[1024];snprintf(browsepath,sizeof(browsepath),"%s/../browse-fixtures.json",argv[1]);
    FILE *browsefile=fopen(browsepath,"rb");assert(browsefile);
    char browsejson[32768]={0};assert(fread(browsejson,1,sizeof(browsejson)-1,browsefile)>0);fclose(browsefile);
    cJSON *browse=cJSON_Parse(browsejson);assert(browse);
    cJSON *browsefixture=NULL;cJSON_ArrayForEach(browsefixture,browse){
        assert(friday_ui_set_scene(browsefixture));capture(argv[1],browsefixture->string);
        if(!strcmp(browsefixture->string,"calendar-long")) {
#ifndef FRIDAY_COMPARE_BASE
            lv_obj_t *screen=lv_screen_active();bool found=false;
            const char *title=field(cJSON_GetArrayItem(cJSON_GetObjectItem(browsefixture,"items"),0),"label");
            for(uint32_t i=0;i<lv_obj_get_child_count(screen);i++) {
                lv_obj_t *o=lv_obj_get_child(screen,i);
                if(lv_obj_is_visible(o)&&lv_obj_get_child_count(o)==2) {
                    lv_obj_t *label=lv_obj_get_child(o,1);
                    if(lv_obj_check_type(label,&lv_label_class)&&!strcmp(lv_label_get_text(label),title)) {
#ifndef FRIDAY_COMPARE_BASE
                        assert(lv_label_get_long_mode(label)==LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);
#endif
                        found=true;
                    }
                }
            }
            assert(found);
#endif
            tick+=4000;lv_tick_inc(4000);capture(argv[1],"calendar-long-scrolled");
        }
    }
    for(int cycle=0;cycle<20;cycle++) {
        cJSON_ArrayForEach(browsefixture,browse){
            assert(friday_ui_set_scene(browsefixture));lv_obj_update_layout(lv_screen_active());
            lv_obj_t *screen=lv_screen_active();
            for(uint32_t child=0;child<lv_obj_get_child_count(screen);child++) {
                lv_obj_t *object=lv_obj_get_child(screen,child);
                if(lv_obj_get_width(object)>=200&&lv_obj_is_visible(object)&&lv_obj_has_flag(object,LV_OBJ_FLAG_CLICKABLE)) {
                    int prior=actions;lv_obj_send_event(object,LV_EVENT_CLICKED,NULL);assert(actions==prior+1);
                    if(lv_obj_check_type(object,&lv_button_class)) {
                        const char *caption=lv_label_get_text(lv_obj_get_child(object,0));
                        cJSON *declared=NULL;bool bound=false;
                        cJSON_ArrayForEach(declared,cJSON_GetObjectItem(browsefixture,"actions")) {
                            if(!strcmp(field(declared,"label"),caption)){assert(!strcmp(field(declared,"id"),last_action));bound=true;}
                        }
                        assert(bound);
                    }
                }
            }
        }
        scene("{\"kind\":\"ambient\",\"title\":\"Friday\",\"subtitle\":\"Ready\"}");
    }
    printf("BROWSE_ROUTED actions=%d cycles=20 PASS\n",actions);
    actions=0;
    cJSON_Delete(browse);
    scene("{\"kind\":\"timeline\",\"title\":\"A little breathing room\",\"subtitle\":\"Your next meeting starts in 20 minutes.\",\"items\":[{\"time\":\"10:30\",\"label\":\"Design review\"},{\"time\":\"12:00\",\"label\":\"Lunch with the team\"},{\"time\":\"14:00\",\"label\":\"Project catch-up\"}],\"actions\":[{\"id\":\"dismiss\",\"label\":\"Done\"},{\"id\":\"calendar\",\"label\":\"Refresh\"}]}");capture(argv[1],"calendar");
    scene("{\"kind\":\"message\",\"title\":\"Remember 1/1\",\"subtitle\":\"I prefer a quiet workspace in the morning.\\nPlease keep everyday replies concise.\",\"actions\":[{\"id\":\"confirm:test\",\"label\":\"Remember this\"},{\"id\":\"cancel\",\"label\":\"Cancel\"}]}");capture(argv[1],"memory");
    scene("{\"kind\":\"reminder\",\"title\":\"Time for a small break\",\"subtitle\":\"Stretch, drink some water, and look outside.\",\"items\":[{\"time\":\"Now\",\"label\":\"Afternoon reset\"}],\"actions\":[{\"id\":\"complete:x\",\"label\":\"All done\"},{\"id\":\"snooze:x\",\"label\":\"Snooze\"},{\"id\":\"dismiss\",\"label\":\"Dismiss\"}]}");capture(argv[1],"reminder");
    scene("{\"kind\":\"media\",\"title\":\"Quiet Satellites\",\"subtitle\":\"We make a little room for the morning\",\"items\":[{\"time\":\"Artist\",\"label\":\"The Paper Observatory\"},{\"time\":\"Progress\",\"label\":\"1:24 / 3:42\"},{\"time\":\"Source\",\"label\":\"Original synthetic timing fixture\"},{\"time\":\"State\",\"label\":\"Playing\"}],\"actions\":[{\"id\":\"musicctl:p\",\"label\":\"Previous\"},{\"id\":\"musicctl:q\",\"label\":\"Pause\"},{\"id\":\"musicctl:n\",\"label\":\"Next\"},{\"id\":\"dismiss\",\"label\":\"Done\"}]}");capture(argv[1],"music");
#ifndef FRIDAY_BASELINE
    char fixturepath[1024];snprintf(fixturepath,sizeof(fixturepath),"%s/../artwork-fixture.json",argv[1]);
    FILE *fixture=fopen(fixturepath,"rb");assert(fixture);char *payload=calloc(131073,1);assert(payload);assert(fread(payload,1,131072,fixture)>0);fclose(fixture);
    cJSON *artframe=cJSON_Parse(payload);free(payload);assert(artframe);friday_ui_media_begin("art-fixture");assert(friday_ui_set_media(artframe));capture(argv[1],"music-artwork");cJSON_Delete(artframe);
#endif
    scene("{\"kind\":\"media\",\"title\":\"Spotify unavailable\",\"subtitle\":\"Playback clock stale; lyrics hidden.\",\"items\":[{\"time\":\"Artist\",\"label\":\"Not available\"},{\"time\":\"Progress\",\"label\":\"Unavailable\"},{\"time\":\"Source\",\"label\":\"Spotify\"},{\"time\":\"State\",\"label\":\"Stale; controls hidden\"}],\"actions\":[{\"id\":\"dismiss\",\"label\":\"Done\"}]}");capture(argv[1],"disabled");
    friday_ui_media_begin("fixture");
    cJSON *frame=cJSON_Parse("{\"media_request_id\":\"fixture\",\"media_session_id\":\"0123456789abcdef01234567\",\"media_sequence\":1,\"surface\":{\"kind\":\"media\",\"title\":\"Original long-line fixture\",\"subtitle\":\"We leave a little light beside the window and a little space for all the things the morning might bring\",\"actions\":[{\"id\":\"dismiss\",\"label\":\"Done\"}]}}");
    assert(friday_ui_set_media(frame));assert(!friday_ui_set_media(frame));
    cJSON_SetNumberValue(cJSON_GetObjectItem(frame,"media_sequence"),2);assert(friday_ui_set_media(frame));
#ifndef FRIDAY_BASELINE
    lv_obj_t *line=NULL;
    for(uint32_t i=0;i<lv_obj_get_child_count(lv_screen_active());i++){lv_obj_t *o=lv_obj_get_child(lv_screen_active(),i);if(lv_obj_check_type(o,&lv_label_class)&&strstr(lv_label_get_text(o),"We leave a little light"))line=o;}
    assert(line);
    assert(lv_label_get_long_mode(line)==LV_LABEL_LONG_MODE_WRAP);
    assert(lv_obj_get_style_text_font(line,0)==&lv_font_montserrat_32 || lv_obj_get_style_text_font(line,0)==&lv_font_montserrat_24);
#endif
    capture(argv[1],"music-long-line");
#ifndef FRIDAY_BASELINE
    const char *cases[]={"A small original line", "Morning light\nacross the room", "A cafe, an em dash — and a quiet thought 漢字 🌙", NULL};
    char huge[1901];memset(huge,'W',1900);huge[1900]=0;
    for(int i=0;i<4;i++) {
        cJSON_SetValuestring(cJSON_GetObjectItem(cJSON_GetObjectItem(frame,"surface"),"subtitle"),i==3?huge:cases[i]);
        cJSON_SetNumberValue(cJSON_GetObjectItem(frame,"media_sequence"),3+i);assert(friday_ui_set_media(frame));
        assert(lv_label_get_long_mode(line)==LV_LABEL_LONG_MODE_WRAP);lv_obj_update_layout(line);
        assert(lv_obj_get_width(line)==624&&lv_obj_get_height(line)==100);
        if(i==3){assert(lv_obj_has_flag(line,LV_OBJ_FLAG_SCROLLABLE));assert(lv_obj_get_scroll_bottom(line)>0);assert(strlen(lv_label_get_text(line))==1900);lv_obj_scroll_to_y(line,120,LV_ANIM_OFF);assert(lv_obj_get_scroll_y(line)>0);capture(argv[1],"music-unbroken-scrolled");cJSON_SetNumberValue(cJSON_GetObjectItem(frame,"media_sequence"),7);assert(friday_ui_set_media(frame));lv_obj_update_layout(line);assert(lv_obj_get_scroll_y(line)==120);}
        else {assert(lv_obj_get_scroll_y(line)==0);char name[48];snprintf(name,sizeof(name),"music-wrap-case-%d",i);capture(argv[1],name);}
    }
    for(int i=0;i<60;i++) {
        const char *text=i%2?"Short again":"We leave a little light beside the window and a little space for all the things the morning might bring";
        cJSON_SetValuestring(cJSON_GetObjectItem(cJSON_GetObjectItem(frame,"surface"),"subtitle"),text);
        cJSON_SetNumberValue(cJSON_GetObjectItem(frame,"media_sequence"),8+i);assert(friday_ui_set_media(frame));lv_obj_update_layout(line);assert(lv_obj_get_scroll_y(line)==0);assert(!strcmp(lv_label_get_text(line),text));assert(lv_label_get_long_mode(line)==LV_LABEL_LONG_MODE_WRAP);
    }
    puts("Native wrapped lyric / Unicode fallback / 1900-character vertical swipe / 60 rapid changes: PASS");
#endif

    friday_ui_set_surface("message","A new thought","Music has yielded to this context.");
    cJSON_SetNumberValue(cJSON_GetObjectItem(frame,"media_sequence"),3);assert(!friday_ui_set_media(frame));cJSON_Delete(frame);
    scene("{\"kind\":\"metric\",\"title\":\"Everything in good order\",\"subtitle\":\"Connected. Your private memory stays local.\",\"items\":[{\"time\":\"Network\",\"label\":\"Online\"},{\"time\":\"Microphone\",\"label\":\"Ready\"}],\"actions\":[{\"id\":\"reminders\",\"label\":\"Reminders\"},{\"id\":\"calendar\",\"label\":\"Calendar\"},{\"id\":\"dismiss\",\"label\":\"Done\"}]}");capture(argv[1],"status");
    scene("{\"kind\":\"timer\",\"title\":\"A moment of focus\",\"subtitle\":\"I will keep an eye on the clock.\",\"countdown_ms\":120000,\"actions\":[{\"id\":\"cancel\",\"label\":\"Cancel timer\"}]}");capture(argv[1],"timer");
    char longtext[1800];memset(longtext,'W',sizeof(longtext)-1);longtext[sizeof(longtext)-1]=0;
    friday_ui_set_surface("message",longtext,longtext);capture(argv[1],"long-text");
    for(int i=0;i<50;i++) {scene("{\"kind\":\"message\",\"title\":\"Cancelled\",\"subtitle\":\"Nothing saved.\",\"actions\":[{\"id\":\"cancel\",\"label\":\"Cancel\"}]}");lv_obj_t *screen=lv_screen_active();
        for(uint32_t j=0;j<lv_obj_get_child_count(screen);j++) {lv_obj_t *o=lv_obj_get_child(screen,j);if(lv_obj_is_visible(o)&&lv_obj_check_type(o,&lv_button_class))lv_obj_send_event(o,LV_EVENT_CLICKED,NULL);}
        friday_ui_set_surface("ambient","At your service","Tap to talk.");}
    assert(actions==50);
    capture(argv[1],"reentry");
#ifndef FRIDAY_BASELINE
    backend_replay(argv[1]);
#endif
#ifndef FRIDAY_COMPARE_BASE
    metadata_audit(argv[1]);
    recovery_audit(argv[1]);
    music_recovery_route_audit(argv[1]);
#ifdef FRIDAY_NATIVE_SENDER
    sender_failure_audit(argv[1]);
    weather_touch_audit(argv[1]);
#endif
    activity_copy_audit(argv[1]);
#endif
    /* Native short sequence, synthetic labels only; no content animation. */
    friday_ui_media_clear();friday_ui_set_surface("ambient","Friday","Touch the light to begin.");
    friday_ui_set_state(FRIDAY_STATE_IDLE);capture(argv[1],"presence-00-rest");
    lv_obj_t *presence=NULL;
    for(uint32_t j=0;j<lv_obj_get_child_count(lv_screen_active());j++) {
        lv_obj_t *o=lv_obj_get_child(lv_screen_active(),j);
        if(lv_obj_get_width(o)==648&&lv_obj_get_height(o)==232)presence=o;
    }
    assert(presence);lv_obj_send_event(presence,LV_EVENT_PRESSED,NULL);capture(argv[1],"presence-01-touch");
    lv_obj_send_event(presence,LV_EVENT_PRESS_LOST,NULL);
    friday_ui_set_state(FRIDAY_STATE_LISTENING);capture(argv[1],"presence-02-listening");
    capture(argv[1],"presence-03-listening");friday_ui_set_state(FRIDAY_STATE_THINKING);capture(argv[1],"presence-04-thinking");
    capture(argv[1],"presence-05-thinking");friday_ui_set_state(FRIDAY_STATE_SPEAKING);capture(argv[1],"presence-06-speaking");
    friday_ui_set_state(FRIDAY_STATE_IDLE);friday_ui_set_tool("synthetic","complete");capture(argv[1],"presence-07-complete");
    capture(argv[1],"presence-08-settle");capture(argv[1],"presence-09-settle");capture(argv[1],"presence-10-rest");
    lv_mem_monitor_t m;lv_mem_monitor(&m);printf("HOST_HEAP used=%zu max=%zu\n",m.total_size-m.free_size,m.max_used);return 0;
}
