#include "friday_presence.h"
#include <stdlib.h>

typedef struct {
    lv_obj_t *object;
    lv_timer_t *timer;
    friday_state_t state;
    int phase;
    int level;
    bool compact;
} presence_t;

static int wave(int angle) { return lv_trigo_sin(angle) / 256; }

static void draw(lv_event_t *event)
{
    presence_t *p=lv_event_get_user_data(event);
    lv_area_t box;lv_obj_get_coords(p->object,&box);
    const int width=lv_area_get_width(&box),height=lv_area_get_height(&box);
    const int segments=p->compact?20:50;
    const int ribbons=p->compact?3:5;
    const int padding=p->compact?6:22;
    const int extent=width-2*padding;
    lv_layer_t *layer=lv_event_get_layer(event);
    uint32_t palette[]={0x8594EF,0x80C8E4,0x8FE2C7,0xDAB99D,0xAF9CDD};
    if(p->state==FRIDAY_STATE_ALERT) {palette[0]=0xF0BD7F;palette[1]=0xF5D09B;palette[2]=0xD98D6A;}
    for(int ribbon=0;ribbon<ribbons;ribbon++) {
        lv_draw_line_dsc_t line;lv_draw_line_dsc_init(&line);
        line.color=lv_color_hex(palette[ribbon]);line.round_start=0;line.round_end=0;
        int last_x=0,last_y=0;
        for(int i=0;i<=segments;i++) {
            int t=i*360/segments;
            int envelope=wave(i*180/segments);
            int strength=p->compact?5:22;
            if(p->state==FRIDAY_STATE_LISTENING)strength+=p->level/12+(p->compact?1:10);
            if(p->state==FRIDAY_STATE_THINKING)strength+=p->compact?2:18;
            if(p->state==FRIDAY_STATE_SPEAKING)strength+=p->compact?3:14;
            int frequency=p->state==FRIDAY_STATE_THINKING?2:1;
            int bend=wave(t*frequency+p->phase+ribbon*38)*strength/128;
            bend+=wave(t*2-p->phase/2+ribbon*47)*(p->compact?2:12)/128;
            int spread=p->compact?3:14;
            int y=box.y1+height/2+(bend+(ribbon-ribbons/2)*spread)*envelope/128;
            int x=box.x1+padding+i*extent/segments;
            if(i>0) {
                line.p1.x=last_x;line.p1.y=last_y;line.p2.x=x;line.p2.y=y;
                /* Small translucent halo, then a crisp tapered strand. */
                line.width=p->compact?2:3;line.opa=LV_OPA_COVER;lv_draw_line(layer,&line);
            }
            last_x=x;last_y=y;
        }
    }
}

static void tick(lv_timer_t *timer)
{
    presence_t *p=lv_timer_get_user_data(timer);
    int speed=p->state==FRIDAY_STATE_THINKING?7:p->state==FRIDAY_STATE_SPEAKING?9:p->state==FRIDAY_STATE_LISTENING?5:2;
    p->phase=(p->phase+speed)%720;
    if(lv_obj_is_visible(p->object))lv_obj_invalidate(p->object);
}

static void deleted(lv_event_t *event)
{
    presence_t *p=lv_event_get_user_data(event);lv_timer_delete(p->timer);free(p);
}

lv_obj_t *friday_presence_create(lv_obj_t *parent)
{
    presence_t *p=calloc(1,sizeof(*p));if(!p)return NULL;
    p->object=lv_obj_create(parent);lv_obj_remove_style_all(p->object);
    lv_obj_remove_flag(p->object,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_size(p->object,648,232);
    lv_obj_add_event_cb(p->object,draw,LV_EVENT_DRAW_MAIN,p);
    p->timer=lv_timer_create(tick,100,p);
    lv_obj_add_event_cb(p->object,deleted,LV_EVENT_DELETE,p);
    return p->object;
}

static presence_t *context(lv_obj_t *object)
{
    for(uint32_t i=0;i<lv_obj_get_event_count(object);i++) {
        lv_event_dsc_t *d=lv_obj_get_event_dsc(object,i);
        if(lv_event_dsc_get_cb(d)==draw)return lv_event_dsc_get_user_data(d);
    }
    return NULL;
}
void friday_presence_set_state(lv_obj_t *object,friday_state_t state)
{
    presence_t *p=context(object);if(!p)return;p->state=state;
    lv_timer_set_period(p->timer,state==FRIDAY_STATE_IDLE?100:33);lv_obj_invalidate(object);
}
void friday_presence_set_level(lv_obj_t *object,int32_t rms)
{
    presence_t *p=context(object);if(!p)return;p->level=rms<0?0:rms>1000?255:rms*255/1000;
}
void friday_presence_set_compact(lv_obj_t *object,bool compact)
{
    presence_t *p=context(object);if(!p)return;p->compact=compact;lv_obj_invalidate(object);
}
