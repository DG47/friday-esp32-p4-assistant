#include "friday_echo.h"
#include "sdkconfig.h"
#if CONFIG_FRIDAY_AEC_BARGE_IN
#include <string.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <limits.h>
#include "esp_timer.h"
#include "esp_aec.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#define HISTORY 24
#define CAPACITY 1344
#define BLOCK16 320
static struct {int16_t samples[480];int64_t timestamp;bool valid;} reference[HISTORY];
static SemaphoreHandle_t lock;
static uint32_t generation,seen;
static unsigned next;
static int64_t last_playback;
static aec_handle_t *aec;
static int16_t *mic,*ref,*clean,*output;
static size_t used,available;
static int chunk;
#define METRICS(X) X(capture_frames) X(active_frames) X(reference_frames) X(stale_reference_frames) X(matched_frames) X(missing_frames) X(lock_drops) X(epoch_drops) X(warmup_frames) X(clean_frames) X(cancellations) X(dsp_calls) X(processing_us) X(max_processing_us) X(deadline_misses) X(alignment_abs_us) X(max_alignment_abs_us) X(capture_gaps) X(max_capture_gap_us) X(alignment_sum_overflow)
#define DECLARE(name) static _Atomic uint32_t stat_##name;
METRICS(DECLARE)
#undef DECLARE
#define COUNT(name) atomic_fetch_add_explicit(&stat_##name,1,memory_order_relaxed)
static void maximum(_Atomic uint32_t *counter,uint32_t value)
{
    uint32_t previous=atomic_load_explicit(counter,memory_order_relaxed);
    /* Each maximum has one capture-task writer; diagnostics only resets it. */
    for(unsigned retry=0;retry<2&&value>previous;retry++) {
        if(atomic_compare_exchange_strong_explicit(counter,&previous,value,memory_order_relaxed,memory_order_relaxed))break;
    }
}
/* One capture writer and one periodic resetter: at most one reset races this
 * update. Saturation reports a lower bound rather than wrapping calibration data. */
static void add_alignment(uint32_t value)
{
    uint32_t previous=atomic_load_explicit(&stat_alignment_abs_us,memory_order_relaxed);
    for(unsigned retry=0;retry<2;retry++) {
        bool overflow=value>UINT32_MAX-previous;
        uint32_t next=overflow?UINT32_MAX:previous+value;
        if(atomic_compare_exchange_strong_explicit(&stat_alignment_abs_us,&previous,next,memory_order_relaxed,memory_order_relaxed)) {
            if(overflow)atomic_store_explicit(&stat_alignment_sum_overflow,1,memory_order_relaxed);
            return;
        }
    }
    /* Should a reset race both bounded attempts, mark the interval incomplete. */
    atomic_store_explicit(&stat_alignment_sum_overflow,1,memory_order_relaxed);
}
friday_echo_stats_t friday_echo_stats(bool reset)
{
    friday_echo_stats_t result;
#define READ(name) result.name=reset?atomic_exchange_explicit(&stat_##name,0,memory_order_relaxed):atomic_load_explicit(&stat_##name,memory_order_relaxed);
    METRICS(READ)
#undef READ
    return result;
}
static void diagnostics_task(void *unused)
{
    (void)unused;
    for(;;) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        friday_echo_stats_t s=friday_echo_stats(true);
        ESP_LOGI("friday_echo", "AEC_DIAG frames=%lu active=%lu ref=%lu stale_ref=%lu matched=%lu missing=%lu locks=%lu epochs=%lu warmup=%lu clean=%lu cancel=%lu dsp=%lu work_us=%lu max_us=%lu over20ms=%lu align_sum_us=%lu align_overflow=%lu align_max_us=%lu gaps40ms=%lu gap_max_us=%lu",
            (unsigned long)s.capture_frames,(unsigned long)s.active_frames,(unsigned long)s.reference_frames,(unsigned long)s.stale_reference_frames,
            (unsigned long)s.matched_frames,(unsigned long)s.missing_frames,(unsigned long)s.lock_drops,(unsigned long)s.epoch_drops,
            (unsigned long)s.warmup_frames,(unsigned long)s.clean_frames,(unsigned long)s.cancellations,(unsigned long)s.dsp_calls,
            (unsigned long)s.processing_us,(unsigned long)s.max_processing_us,(unsigned long)s.deadline_misses,
            (unsigned long)s.alignment_abs_us,(unsigned long)s.alignment_sum_overflow,(unsigned long)s.max_alignment_abs_us,(unsigned long)s.capture_gaps,(unsigned long)s.max_capture_gap_us);
        ESP_LOGI("friday_echo", "AEC_HEAP internal_free=%u internal_min=%u internal_largest=%u psram_free=%u psram_min=%u psram_largest=%u",
            (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
            (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
            (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
            (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT),
            (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT),
            (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    }
}

static bool reset_processor(void)
{
    if(aec)aec_destroy(aec);
    aec=aec_create(16000,4,1,AEC_MODE_SR_LOW_COST);used=available=0;
    chunk=aec?aec_get_chunksize(aec):0;
    return chunk>0&&chunk<=1024;
}
bool friday_echo_init(void)
{
    lock=xSemaphoreCreateMutex();
    mic=heap_caps_aligned_alloc(16,CAPACITY*2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    ref=heap_caps_aligned_alloc(16,CAPACITY*2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    clean=heap_caps_aligned_alloc(16,CAPACITY*2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    output=heap_caps_aligned_alloc(16,CAPACITY*2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    bool ready=lock&&mic&&ref&&clean&&output&&reset_processor();
    ESP_LOGI("friday_echo","Experimental AEC ready=%d chunk=%d delay_ms=%d",ready,chunk,CONFIG_FRIDAY_AEC_REFERENCE_DELAY_MS);
    if(!ready) {
        if(aec) { aec_destroy(aec); }
        aec=NULL;
        free(mic);free(ref);free(clean);free(output);mic=ref=clean=output=NULL;
    }
    if(ready&&xTaskCreate(diagnostics_task,"friday_aec_diag",3072,NULL,1,NULL)!=pdPASS)
        ESP_LOGI("friday_echo","AEC diagnostics task unavailable");
    return ready;
}
uint32_t friday_echo_generation(void)
{
    if(!lock) { return 0; }
    xSemaphoreTake(lock,portMAX_DELAY);uint32_t result=generation;xSemaphoreGive(lock);return result;
}
void friday_echo_invalidate(void)
{
    if(!lock) { return; }
    xSemaphoreTake(lock,portMAX_DELAY);generation++;COUNT(cancellations);memset(reference,0,sizeof(reference));xSemaphoreGive(lock);
}
void friday_echo_reference(const int16_t *stereo,size_t frames,int64_t started,uint32_t owner)
{
    if(!lock||!stereo||frames>480||xSemaphoreTake(lock,pdMS_TO_TICKS(2))!=pdTRUE)return;
    if(owner==generation){for(size_t i=0;i<480;i++)reference[next].samples[i]=i<frames?stereo[i*2]:0;
        reference[next].timestamp=started;reference[next].valid=true;next=(next+1)%HISTORY;COUNT(reference_frames);}
    else COUNT(stale_reference_frames);
    xSemaphoreGive(lock);
}
static bool process_capture(int16_t *mono,int64_t captured,bool playback)
{
    if(!aec||!lock||!mic||!ref||!clean||!output||chunk<1||chunk>1024)return !playback;
    if(playback)last_playback=captured;
    bool tail=last_playback&&captured-last_playback<200000;
    if(!playback&&!tail)return true;
    COUNT(active_frames);
    int16_t frame[480];bool found=false;
    if(xSemaphoreTake(lock,pdMS_TO_TICKS(2))!=pdTRUE){COUNT(lock_drops);return false;}
    uint32_t owner=generation;int64_t target=captured-20000-CONFIG_FRIDAY_AEC_REFERENCE_DELAY_MS*1000;
    int64_t nearest=INT64_MAX;int best=-1;
    for(unsigned i=0;i<HISTORY;i++)if(reference[i].valid){int64_t delta=llabs(reference[i].timestamp-target);if(delta<nearest){nearest=delta;best=(int)i;}}
    found=best>=0&&nearest<=6000;
    if(found)memcpy(frame,reference[best].samples,sizeof(frame));
    if(best>=0) {
        uint32_t error=nearest>UINT32_MAX?UINT32_MAX:(uint32_t)nearest;
        if(nearest>UINT32_MAX)atomic_store_explicit(&stat_alignment_sum_overflow,1,memory_order_relaxed);
        add_alignment(error);
        maximum(&stat_max_alignment_abs_us,error);
    }
    xSemaphoreGive(lock);
    if(owner!=seen){seen=owner;if(!reset_processor())return false;}
    if(!found){COUNT(missing_frames);used=available=0;return false;}
    COUNT(matched_frames);
    if(used+BLOCK16>CAPACITY){used=available=0;return false;}
    for(int i=0,j=0;i<480;i+=3,j+=2){mic[used+j]=(2*(int32_t)mono[i]+mono[i+1])/3;mic[used+j+1]=(mono[i+1]+2*(int32_t)mono[i+2])/3;
        ref[used+j]=(2*(int32_t)frame[i]+frame[i+1])/3;ref[used+j+1]=(frame[i+1]+2*(int32_t)frame[i+2])/3;}
    used+=BLOCK16;
    while(used>=(size_t)chunk){if(available+(size_t)chunk>CAPACITY){used=available=0;return false;}COUNT(dsp_calls);aec_process(aec,mic,ref,clean);memcpy(output+available,clean,chunk*2);available+=chunk;used-=chunk;memmove(mic,mic+chunk,used*2);memmove(ref,ref+chunk,used*2);}
    bool ready=available>=BLOCK16;
    if(ready) {
        for(int i=0,j=0;i<320;i+=2,j+=3){mono[j]=output[i];mono[j+1]=((int32_t)output[i]+output[i+1])/2;mono[j+2]=output[i+1];}
        available-=BLOCK16;memmove(output,output+BLOCK16,available*2);
    }
    /* Cancellation can invalidate the reference while DSP runs outside the lock.
     * Do not admit that old epoch's audio or carry its output into the next turn. */
    if(xSemaphoreTake(lock,pdMS_TO_TICKS(2))!=pdTRUE) {
        COUNT(lock_drops);
        used=available=0;memset(mono,0,480*sizeof(*mono));return false;
    }
    bool current=owner==generation;
    xSemaphoreGive(lock);
    if(!current) {
        COUNT(epoch_drops);
        used=available=0;memset(mono,0,480*sizeof(*mono));return false;
    }
    if(ready)COUNT(clean_frames);else COUNT(warmup_frames);
    return ready;
}
bool friday_echo_process(int16_t *mono,int64_t captured,bool playback)
{
    static int64_t previous_capture;
    int64_t started=esp_timer_get_time();COUNT(capture_frames);
    if(previous_capture&&captured>previous_capture) {
        int64_t gap=captured-previous_capture;
        maximum(&stat_max_capture_gap_us,gap>UINT32_MAX?UINT32_MAX:(uint32_t)gap);
        if(gap>40000)COUNT(capture_gaps);
    }
    previous_capture=captured;
    bool result=process_capture(mono,captured,playback);
    int64_t elapsed=esp_timer_get_time()-started;
    uint32_t duration=elapsed>UINT32_MAX?UINT32_MAX:elapsed>0?(uint32_t)elapsed:0;
    atomic_fetch_add_explicit(&stat_processing_us,duration,memory_order_relaxed);
    maximum(&stat_max_processing_us,duration);
    if(duration>20000)COUNT(deadline_misses);
    return result;
}
#else
bool friday_echo_init(void){return false;}
void friday_echo_invalidate(void){}
uint32_t friday_echo_generation(void){return 0;}
void friday_echo_reference(const int16_t *s,size_t n,int64_t t,uint32_t g){(void)s;(void)n;(void)t;(void)g;}
bool friday_echo_process(int16_t *s,int64_t t,bool playback){(void)s;(void)t;return !playback;}
#endif

#if !CONFIG_FRIDAY_AEC_BARGE_IN
friday_echo_stats_t friday_echo_stats(bool reset){(void)reset;return (friday_echo_stats_t){0};}
#endif
