/* Production controller tested with LVGL's deterministic clock. */
#include <assert.h>
#include <stdio.h>
#include "../../firmware/main/friday_presence.c"
static void advance(presence_t *p, unsigned ms) {lv_tick_inc(ms);tick(p->timer);}
int main(void) {
 lv_init();lv_display_t *display=lv_display_create(720,720);assert(display);
 lv_obj_t *object=friday_presence_create(lv_screen_active());assert(object);
 presence_t *p=context(object);assert(p);
 advance(p,240);assert(lv_timer_get_paused(p->timer)&&p->activity==0);
 size_t used;lv_mem_monitor_t heap;lv_mem_monitor(&heap);used=heap.total_size-heap.free_size;
 for(int i=0;i<1000;i++) {
  friday_presence_set_state(object,FRIDAY_STATE_LISTENING);advance(p,120);assert(p->activity==128);
  uint32_t start=p->changed_at;friday_presence_set_state(object,FRIDAY_STATE_LISTENING);assert(p->changed_at==start);
  friday_presence_set_state(object,FRIDAY_STATE_IDLE);advance(p,240);assert(!p->activity&&lv_timer_get_paused(p->timer));
  friday_presence_complete(object);assert(p->feedback);advance(p,160);assert(p->feedback);
  friday_presence_set_state(object,FRIDAY_STATE_THINKING);assert(!p->feedback);advance(p,240);
  int phase=p->phase;friday_presence_set_compact(object,true);assert(p->phase==phase);
  lv_obj_send_event(object,LV_EVENT_PRESSED,NULL);assert(p->pressed);
  lv_obj_send_event(object,LV_EVENT_PRESS_LOST,NULL);assert(!p->pressed&&!p->feedback);
  friday_presence_set_compact(object,false);friday_presence_set_state(object,FRIDAY_STATE_IDLE);advance(p,240);
  friday_presence_complete(object);advance(p,320);assert(!p->feedback&&lv_timer_get_paused(p->timer));
 }
 lv_mem_monitor(&heap);assert(heap.total_size-heap.free_size==used);
 lv_obj_delete(object);lv_display_delete(display);
 puts("PRESENCE 1000 interrupted/repeated/touch-cancel/compact-return/completion cycles: PASS; 240ms morph, 320ms cue; zero heap growth");
}
