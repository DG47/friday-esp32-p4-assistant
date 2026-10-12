#pragma once
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <stdio.h>

/* One fixed-size metadata window; never retains PCM. Raw channels precede
 * averaging; later stages preserve existing capture/AEC/gain/downsampling. */
typedef struct {
    uint64_t left_energy, right_energy, mono_energy, aec_energy, gain_energy, stream_energy;
    int64_t cross;
    uint32_t samples, stream_samples, frames, clean_frames, playback_frames, recording_frames;
    uint32_t left_clips, right_clips, gain_clips, stream_clips;
} friday_capture_metrics_t;

static inline uint64_t friday_capture_square(int32_t value)
{ return (uint64_t)((int64_t)value * value); }

static inline void friday_capture_raw(friday_capture_metrics_t *m, int32_t left, int32_t right)
{
    m->left_energy += friday_capture_square(left);
    m->right_energy += friday_capture_square(right);
    m->cross += (int64_t)left * right;
    m->mono_energy += friday_capture_square((left + right) / 2);
    m->left_clips += left == -32768 || left == 32767;
    m->right_clips += right == -32768 || right == 32767;
    m->samples++;
}

static inline void friday_capture_stage(friday_capture_metrics_t *m, const int16_t *pcm,
                                       size_t count, int stage)
{
    uint64_t energy = 0;
    uint32_t clips = 0;
    for (size_t i = 0; i < count; ++i) {
        energy += friday_capture_square(pcm[i]);
        clips += pcm[i] == -32768 || pcm[i] == 32767;
    }
    if (stage == 0) m->aec_energy += energy;
    else if (stage == 1) { m->gain_energy += energy; m->gain_clips += clips; }
    else if (stage == 2) {
        m->stream_energy += energy; m->stream_clips += clips; m->stream_samples += count;
    }
}

static inline uint32_t friday_capture_rms(uint64_t energy, uint32_t count)
{ return count ? (uint32_t)sqrt((double)energy / count) : 0; }

/* Undefined correlation (a silent channel) stays explicit, never interpreted
 * as positive or negative correlation. Both values are bounded numeric fields. */
static inline int friday_capture_corr_valid(const friday_capture_metrics_t *m)
{ return m->left_energy != 0 && m->right_energy != 0; }
static inline int32_t friday_capture_corr_ppm(const friday_capture_metrics_t *m)
{
    if (!friday_capture_corr_valid(m)) return 0;
    double value = 1000000.0 * m->cross /
                   sqrt((double)m->left_energy * (double)m->right_energy);
    return value > 1000000 ? 1000000 : value < -1000000 ? -1000000 : (int32_t)value;
}

static inline int friday_capture_format_json(char *destination, size_t capacity,
        const friday_capture_metrics_t *m, uint32_t wall_ms, uint32_t sequence)
{
    return snprintf(destination, capacity, "{\"seq\":%lu,\"frames\":%lu,\"samples\":%lu,\"stream_samples\":%lu,\"wall_ms\":%lu,\"clean\":%lu,\"playback\":%lu,\"recording\":%lu,\"l_rms\":%lu,\"r_rms\":%lu,\"mono_rms\":%lu,\"post_aec_rms\":%lu,\"gain_rms\":%lu,\"stream_rms\":%lu,\"corr_valid\":%d,\"corr_ppm\":%ld,\"l_clips\":%lu,\"r_clips\":%lu,\"gain_clips\":%lu,\"stream_clips\":%lu}",
                (unsigned long)sequence, (unsigned long)m->frames, (unsigned long)m->samples,
                (unsigned long)m->stream_samples,
                (unsigned long)(wall_ms),
                (unsigned long)m->clean_frames, (unsigned long)m->playback_frames,
                (unsigned long)m->recording_frames,
                (unsigned long)friday_capture_rms(m->left_energy, m->samples),
                (unsigned long)friday_capture_rms(m->right_energy, m->samples),
                (unsigned long)friday_capture_rms(m->mono_energy, m->samples),
                (unsigned long)friday_capture_rms(m->aec_energy, m->samples),
                (unsigned long)friday_capture_rms(m->gain_energy, m->samples),
                (unsigned long)friday_capture_rms(m->stream_energy, m->stream_samples),
                friday_capture_corr_valid(m), (long)friday_capture_corr_ppm(m),
                (unsigned long)m->left_clips, (unsigned long)m->right_clips,
                (unsigned long)m->gain_clips, (unsigned long)m->stream_clips);
}
