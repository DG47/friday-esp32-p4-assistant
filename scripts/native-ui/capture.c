#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <assert.h>
#include <time.h>
#include "lvgl.h"
#include "friday_ui.h"
static uint32_t pixels[720*720];
static uint32_t tick;
static int actions;
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
static bool action(const char *id) {assert(id && *id);actions++;return true;}
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
static const char *field(cJSON *o,const char *key){cJSON *v=cJSON_GetObjectItem(o,key);return cJSON_IsString(v)?v->valuestring:"";}
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
    cJSON *browsefixture=NULL;cJSON_ArrayForEach(browsefixture,browse){assert(friday_ui_set_scene(browsefixture));capture(argv[1],browsefixture->string);}
    for(int cycle=0;cycle<20;cycle++) {
        cJSON_ArrayForEach(browsefixture,browse){
            assert(friday_ui_set_scene(browsefixture));lv_obj_update_layout(lv_screen_active());
            lv_obj_t *screen=lv_screen_active();
            for(uint32_t child=0;child<lv_obj_get_child_count(screen);child++) {
                lv_obj_t *object=lv_obj_get_child(screen,child);
                if(lv_obj_get_width(object)>=200&&lv_obj_is_visible(object)&&lv_obj_has_flag(object,LV_OBJ_FLAG_CLICKABLE)) {
                    int prior=actions;lv_obj_send_event(object,LV_EVENT_CLICKED,NULL);assert(actions==prior+1);
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
    lv_mem_monitor_t m;lv_mem_monitor(&m);printf("HOST_HEAP used=%zu max=%zu\n",m.total_size-m.free_size,m.max_used);return 0;
}
