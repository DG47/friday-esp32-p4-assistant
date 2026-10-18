#include "friday_presence.h"
#include <stdlib.h>

typedef struct {
    lv_obj_t *object;
    lv_timer_t *timer;
    friday_state_t state;
    int phase;
    int level;
    bool compact;
    bool material;
    int activity, from_activity, target_activity;
    uint32_t changed_at, feedback_at;
    bool feedback;
    bool pressed;
} presence_t;

static int wave(int angle) { return lv_trigo_sin(angle) / 256; }

static void draw(lv_event_t *event)
{
    presence_t *p=lv_event_get_user_data(event);
    lv_area_t box;lv_obj_get_coords(p->object,&box);
    const int width=lv_area_get_width(&box),height=lv_area_get_height(&box);
    const int segments=p->compact?24:64;
    const int strands=p->compact?3:5;
    lv_layer_t *layer=lv_event_get_layer(event);
    const uint32_t colors[]={0x94A5F6,0x8AD4EC,0x93E1CD,0xDAC5B4,0xB7A7E8};
    int radius=p->compact?17:88;
    radius+=p->activity*(p->compact?1:7)/256;
    if(p->state==FRIDAY_STATE_LISTENING)radius+=p->level*(p->compact?1:9)/255;
    if(p->pressed)radius+=p->compact?1:5;
    if(p->feedback)radius+=(p->compact?2:8)*(320-(int)lv_tick_elaps(p->feedback_at))/320;
    int limit=(width<height?width:height)/2-(p->compact?4:20);
    if(radius>limit)radius=limit;
    if(!p->compact) {
        /* A quiet translucent volume grounds the moving contours; these are
         * inexpensive gradient rectangles with circular corners, not blur. */
        for(int n=0;n<4;n++) {
            int r=radius+10-n*14;
            lv_area_t volume={box.x1+width/2-r,box.y1+height/2-r,
                              box.x1+width/2+r,box.y1+height/2+r};
            lv_draw_rect_dsc_t fill;lv_draw_rect_dsc_init(&fill);
            fill.radius=LV_RADIUS_CIRCLE;fill.bg_opa=24+n*5;
            fill.bg_color=lv_color_hex(p->state==FRIDAY_STATE_ALERT?0x75543B:0x354D80);
            fill.bg_grad.dir=LV_GRAD_DIR_VER;fill.bg_grad.stops_count=2;
            fill.bg_grad.stops[0].color=fill.bg_color;fill.bg_grad.stops[0].opa=255;fill.bg_grad.stops[0].frac=0;
            fill.bg_grad.stops[1].color=lv_color_hex(0x233B43);fill.bg_grad.stops[1].opa=50;fill.bg_grad.stops[1].frac=255;
            lv_draw_rect(layer,&fill,&volume);
        }
    }
    if(p->material) {
        /* A soft luminous volume, not a technical wireframe. Layered gradient
         * discs use the native renderer and follow the same bounded activity. */
        for(int lobe=0;lobe<3;lobe++)for(int n=0;n<8;n++) {
            int drift=p->state==FRIDAY_STATE_IDLE?0:p->phase;
            int shift=radius/3;
            int cx=box.x1+width/2+wave(drift/2+lobe*120)*shift/128;
            int cy=box.y1+height/2+wave(drift/3+lobe*120+90)*shift/128;
            int r=radius*2/3-n*radius/24;
            lv_area_t area={cx-r,cy-r,cx+r,cy+r};
            lv_draw_rect_dsc_t fill;lv_draw_rect_dsc_init(&fill);
            fill.radius=LV_RADIUS_CIRCLE;fill.bg_opa=20+n*4;
            uint32_t tint=p->state==FRIDAY_STATE_ALERT?0xDCAB73:lobe==0?0x8E8FE4:lobe==1?0x69BAD0:0xA3D4C8;
            fill.bg_color=lv_color_hex(tint);fill.bg_grad.dir=LV_GRAD_DIR_VER;fill.bg_grad.stops_count=2;
            fill.bg_grad.stops[0].color=fill.bg_color;fill.bg_grad.stops[0].opa=255;fill.bg_grad.stops[0].frac=0;
            fill.bg_grad.stops[1].color=lv_color_hex(0x263447);fill.bg_grad.stops[1].opa=35;fill.bg_grad.stops[1].frac=255;
            lv_draw_rect(layer,&fill,&area);
        }
        return;
    }
    /* Woven luminous contours at both scales. No bitmap, shadow filter,
     * framebuffer or allocation in the draw/animation path. */
    for(int strand=0;strand<strands;strand++) {
        lv_draw_line_dsc_t line;lv_draw_line_dsc_init(&line);
        line.color=lv_color_hex(p->state==FRIDAY_STATE_ALERT?0xEBC095:colors[strand]);
        line.round_start=1;line.round_end=1;
        int last_x=0,last_y=0;
        for(int i=0;i<=segments;i++) {
            int t=i*360/segments;
            int twist=p->phase+strand*37;
            int r=radius+wave(t*3+twist)*(p->compact?1:7)/128;
            int horizontal=wave(t+90)*r/128;
            int vertical=wave(t)*r*(35+strand*10+wave(twist)/10)/128/100;
            int angle=strand*36+p->phase/4;
            int x=box.x1+width/2+(horizontal*wave(angle+90)-vertical*wave(angle))/128;
            int y=box.y1+height/2+(horizontal*wave(angle)+vertical*wave(angle+90))/128;
            y+=wave(t*2-twist)*(p->compact?1:4)/128;
            if(i>0) {
                line.p1.x=last_x;line.p1.y=last_y;line.p2.x=x;line.p2.y=y;
                int depth=wave(t+twist);
                if(!p->compact) {line.width=7;line.opa=18;lv_draw_line(layer,&line);}
                line.width=p->compact?1:3;line.opa=145+depth/3;
                lv_draw_line(layer,&line);
            }
            last_x=x;last_y=y;
        }
    }
}

static void tick(lv_timer_t *timer)
{
    presence_t *p=lv_timer_get_user_data(timer);
    uint32_t elapsed=lv_tick_elaps(p->changed_at);
    if(elapsed>240)elapsed=240;
    p->activity=p->from_activity+(p->target_activity-p->from_activity)*(int)elapsed/240;
    if(p->feedback&&lv_tick_elaps(p->feedback_at)>=320)p->feedback=false;
    /* No idle animation while reading. Preserve phase across every view. */
    int speed=p->state==FRIDAY_STATE_THINKING?7:p->state==FRIDAY_STATE_SPEAKING?9:p->state==FRIDAY_STATE_LISTENING?5:0;
    p->phase=(p->phase+speed)%720;
    if(lv_obj_is_visible(p->object))lv_obj_invalidate(p->object);
    if(!speed&&elapsed==240&&!p->feedback&&!p->pressed)lv_timer_pause(p->timer);
}

static void touch(lv_event_t *event)
{
    presence_t *p=lv_event_get_user_data(event);
    lv_event_code_t code=lv_event_get_code(event);
    if(code==LV_EVENT_PRESSED)p->pressed=true;
    else if(code==LV_EVENT_RELEASED||code==LV_EVENT_PRESS_LOST)p->pressed=false;
    else return;
    p->feedback=false; /* Interrupted input cannot leave a delayed cue. */
    lv_timer_resume(p->timer);lv_timer_ready(p->timer);lv_obj_invalidate(p->object);
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
    p->timer=lv_timer_create(tick,33,p);
    lv_obj_add_event_cb(p->object,touch,LV_EVENT_ALL,p);
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
    presence_t *p=context(object);if(!p||p->state==state)return;
    p->from_activity=p->activity;p->target_activity=state==FRIDAY_STATE_IDLE?0:256;
    p->changed_at=lv_tick_get();p->state=state;p->feedback=false;
    lv_timer_resume(p->timer);lv_timer_ready(p->timer);lv_obj_invalidate(object);
}
void friday_presence_set_level(lv_obj_t *object,int32_t rms)
{
    presence_t *p=context(object);if(!p)return;p->level=rms<0?0:rms>1000?255:rms*255/1000;
}
void friday_presence_set_compact(lv_obj_t *object,bool compact)
{
    presence_t *p=context(object);if(!p||p->compact==compact)return;
    p->compact=compact;p->pressed=false;p->feedback=false;lv_obj_invalidate(object);
}

void friday_presence_complete(lv_obj_t *object)
{
    presence_t *p=context(object);if(!p)return;
    p->feedback=true;p->feedback_at=lv_tick_get();
    lv_timer_resume(p->timer);lv_timer_ready(p->timer);lv_obj_invalidate(object);
}

void friday_presence_set_material(lv_obj_t *object,bool material)
{
    presence_t *p=context(object);if(!p)return;p->material=material;lv_obj_invalidate(object);
}
