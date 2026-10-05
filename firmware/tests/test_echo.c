/* Tests actual production reference/resampling/ownership, not acoustic quality.
 * P4-only AEC and RTOS calls are deterministic test doubles. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "friday_echo.h"
#include "esp_aec.h"
int lock_allowed=1,calls;
int64_t fake_clock;
static int invalidate_during_processing;
static int delayed_processing;
static int stub_chunk=256;
static aec_handle_t instance;
aec_handle_t *aec_create(int rate,int filters,int channels,int mode){assert(rate==16000&&filters==4&&channels==1&&mode==0);return &instance;}
void aec_destroy(aec_handle_t *a){assert(a==&instance);}
int aec_get_chunksize(aec_handle_t *a){assert(a==&instance);return stub_chunk;}
void aec_process(aec_handle_t *a,int16_t *mic,int16_t *ref,int16_t *out){assert(a==&instance);calls++;if(delayed_processing){fake_clock+=25000;delayed_processing=0;}for(int i=0;i<stub_chunk;i++)out[i]=mic[i]-ref[i];if(invalidate_during_processing){invalidate_during_processing=0;friday_echo_invalidate();}}
static void fill(int16_t *a,int value,int n){for(int i=0;i<n;i++)a[i]=value;}
int main(void)
{
 assert(friday_echo_init());int16_t stereo[960],mic[480];fill(stereo,1000,960);fill(mic,1000,480);
 assert(!friday_echo_process(mic,100000,true));assert(!calls);
 uint32_t owner=friday_echo_generation();
 friday_echo_reference(stereo,480,100000,owner);fill(mic,1000,480);assert(!friday_echo_process(mic,180000,true));assert(calls==1);
 friday_echo_reference(stereo,480,120000,owner);fill(mic,1000,480);assert(friday_echo_process(mic,200000,true));for(int i=0;i<480;i++)assert(!mic[i]);
 /* New owner invalidates queued/late reference, including writes finishing after cancel. */
 friday_echo_invalidate();friday_echo_reference(stereo,480,140000,owner);fill(mic,1000,480);assert(!friday_echo_process(mic,220000,true));
 owner=friday_echo_generation();friday_echo_reference(stereo,481,140000,owner);assert(!friday_echo_process(mic,220000,true));
 friday_echo_reference(stereo,480,140000,owner);assert(!friday_echo_process(mic,240000,true)); /* stale timestamp */
 lock_allowed=0;assert(!friday_echo_process(mic,260000,true));lock_allowed=1;
 assert(!friday_echo_process(mic,280000,false)); /* echo-tail holdoff */
 fill(mic,123,480);assert(friday_echo_process(mic,600001,false));for(int i=0;i<480;i++)assert(mic[i]==123);
 /* Repeated playback starts, reference wrap, and no state growth. */
 for(int turn=0;turn<50;turn++) {friday_echo_invalidate();owner=friday_echo_generation();for(int frame=0;frame<30;frame++) {int64_t t=1000000LL+turn*1000000LL+frame*20000;friday_echo_reference(stereo,480,t,owner);fill(mic,1000,480);bool ready=friday_echo_process(mic,t+80000,true);if(frame)assert(ready);}}
 /* Reproduce cancellation inside DSP after old-epoch buffers contain output.
  * Reject stale audio, zero the returned frame and reset before accepting a new turn. */
 for(int turn=0;turn<30;turn++) {
    friday_echo_invalidate();owner=friday_echo_generation();
    int64_t t=60000000LL+turn*1000000LL;
    friday_echo_reference(stereo,480,t-40000,owner);fill(mic,2000,480);
    invalidate_during_processing=1;
    assert(!friday_echo_process(mic,t+40000,true)); /* cancelled during warm-up */
    for(int i=0;i<480;i++)assert(mic[i]==0);
    owner=friday_echo_generation();
    friday_echo_reference(stereo,480,t,owner);fill(mic,1000,480);
    assert(!friday_echo_process(mic,t+80000,true));
    friday_echo_reference(stereo,480,t+20000,owner);fill(mic,2000,480);
    invalidate_during_processing=1;
    assert(!friday_echo_process(mic,t+100000,true));
    assert(friday_echo_generation()!=owner);
    for(int i=0;i<480;i++)assert(mic[i]==0);
    friday_echo_reference(stereo,480,t+40000,owner);fill(mic,2000,480);
    assert(!friday_echo_process(mic,t+120000,true)); /* late old-epoch write */
    owner=friday_echo_generation();
    friday_echo_reference(stereo,480,t+60000,owner);fill(mic,1000,480);
    assert(!friday_echo_process(mic,t+140000,true)); /* fresh DSP warm-up */
    friday_echo_reference(stereo,480,t+80000,owner);fill(mic,1000,480);
    assert(friday_echo_process(mic,t+160000,true));
    for(int i=0;i<480;i++)assert(mic[i]==0); /* no stale residual output */
 }
 friday_echo_stats_t stats=friday_echo_stats(false);
 assert(stats.epoch_drops==60&&stats.cancellations>=110);
 assert(stats.matched_frames>0&&stats.missing_frames>0&&stats.stale_reference_frames>0);
 assert(stats.lock_drops==1&&stats.clean_frames>0&&stats.warmup_frames>0);
 assert(stats.dsp_calls==(uint32_t)calls&&stats.capture_frames>stats.active_frames);
 assert(stats.processing_us==stats.capture_frames*100&&stats.max_processing_us==100);
 assert(stats.deadline_misses==0);
 friday_echo_stats_t reset=friday_echo_stats(true);assert(reset.capture_frames==stats.capture_frames);
 stats=friday_echo_stats(false);assert(!stats.capture_frames&&!stats.epoch_drops&&!stats.dsp_calls);
 assert(stats.max_capture_gap_us==0&&!stats.capture_gaps);
 owner=friday_echo_generation();
 friday_echo_reference(stereo,480,100000000,owner);fill(mic,1000,480);delayed_processing=1;
 friday_echo_process(mic,100080000,true);
 stats=friday_echo_stats(true);
 assert(stats.deadline_misses==1&&stats.max_processing_us==25100);
 assert(stats.capture_gaps==1&&stats.max_capture_gap_us>40000);
 /* Hardware reports512-sample chunks; verify actual FIFO cadence as well. */
 stub_chunk=512;friday_echo_invalidate();owner=friday_echo_generation();
 for(int i=0;i<30;i++) {
    int64_t t=200000000LL+i*20000;
    friday_echo_reference(stereo,480,t,owner);fill(mic,1000,480);
    bool accepted=friday_echo_process(mic,t+80000,true);
    assert(accepted==(i!=0&&i!=2));
    if(accepted)for(int j=0;j<480;j++)assert(!mic[j]);
 }
 stats=friday_echo_stats(true);assert(stats.warmup_frames==2&&stats.clean_frames==28);
 assert(stats.matched_frames==30&&stats.dsp_calls==18);
 /* Prolonged unmatched reference: sum exceeds32 bits but must never wrap.
  * Exactly250 frames give5,622,500,000us; audio remains suppressed. */
 friday_echo_invalidate();owner=friday_echo_generation();
 friday_echo_reference(stereo,480,300000000,owner);friday_echo_stats(true);
 for(int i=0;i<250;i++) {
    fill(mic,1000,480);
    assert(!friday_echo_process(mic,320080000LL+i*20000,true));
 }
 stats=friday_echo_stats(true);
 assert(stats.missing_frames==250&&stats.clean_frames==0);
 assert(stats.alignment_abs_us==UINT32_MAX&&stats.alignment_sum_overflow==1);
 assert(stats.max_alignment_abs_us==24980000);
 stats=friday_echo_stats(false);
 assert(!stats.alignment_abs_us&&!stats.alignment_sum_overflow&&!stats.missing_frames);
 /* A new interval resumes ordinary exact counting after reset. */
 fill(mic,1000,480);assert(!friday_echo_process(mic,325080000,true));
 stats=friday_echo_stats(true);assert(stats.alignment_abs_us==25000000&&!stats.alignment_sum_overflow);
 fill(mic,1000,480);assert(!friday_echo_process(mic,5300080000LL,true));
 stats=friday_echo_stats(true);
 assert(stats.alignment_abs_us==UINT32_MAX&&stats.max_alignment_abs_us==UINT32_MAX&&stats.alignment_sum_overflow==1);
 puts("250 unmatched frames: alignment sum saturates, overflow flagged, reset restores exact counting PASS");
 puts("Privacy-safe metadata counts/timing and interval reset PASS");
 puts("Cancellation during DSP: stale capture rejected and fresh epoch reset, 30 turns PASS");
 puts("Production echo reference: ownership/stale-gap/tail/resampling/50 repeated turns PASS (AEC itself stubbed)");
}
