#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "friday_capture_metrics.h"
int main(void)
{
    friday_capture_metrics_t m = {0};
    assert(!friday_capture_corr_valid(&m) && friday_capture_corr_ppm(&m) == 0);
    for(int i=0;i<24000;i++) friday_capture_raw(&m, 1000, 1000);
    assert(m.samples == 24000 && friday_capture_corr_ppm(&m) == 1000000);
    assert(friday_capture_rms(m.mono_energy,m.samples)==1000);
    memset(&m,0,sizeof(m));
    for(int i=0;i<24000;i++) friday_capture_raw(&m, 1000,-1000);
    assert(friday_capture_corr_ppm(&m)==-1000000 && m.mono_energy==0);
    assert(friday_capture_rms(m.left_energy,m.samples)==1000);
    memset(&m,0,sizeof(m));
    for(int i=0;i<24000;i++) friday_capture_raw(&m, 1000,0);
    assert(!friday_capture_corr_valid(&m));
    assert(friday_capture_rms(m.mono_energy,m.samples)==500);
    memset(&m,0,sizeof(m));
    for(int i=0;i<24000;i++) friday_capture_raw(&m, -32768,32767);
    assert(m.left_clips==24000 && m.right_clips==24000);
    assert(m.left_energy==UINT64_C(25769803776000));
    assert(friday_capture_rms(m.left_energy,m.samples)==32768);
    assert(m.mono_energy==0); /* C truncates -1/2 toward zero. */
    int16_t pcm[480];
    for(int i=0;i<480;i++)pcm[i]=i%2?-32768:32767;
    for(int frame=0;frame<50;frame++) {
        friday_capture_stage(&m,pcm,480,0);
        friday_capture_stage(&m,pcm,480,1);
        friday_capture_stage(&m,pcm,240,2);
    }
    assert(m.gain_clips==24000 && m.stream_clips==12000 && m.stream_samples==12000);
    assert(friday_capture_rms(m.aec_energy,24000)==32767);
    m=(friday_capture_metrics_t){0};
    assert(!m.samples&&!m.gain_clips&&!m.cross&&!m.stream_energy);
    for(int i=0;i<24000;i++)friday_capture_raw(&m,32767,-32768);
    m.frames=m.clean_frames=m.playback_frames=m.recording_frames=50;
    m.stream_samples=12000;
    char payload[640];
    int written=friday_capture_format_json(payload,sizeof(payload),&m,60000,UINT32_MAX);
    assert(written>0 && (size_t)written<sizeof(payload));
    char tiny[16];assert(friday_capture_format_json(tiny,sizeof(tiny),&m,60000,UINT32_MAX)>=(int)sizeof(tiny));
    puts(payload);
    puts("Capture stage metadata: silence/in-phase/opposite/one-channel/clipping/50-frame/reset PASS");
}
