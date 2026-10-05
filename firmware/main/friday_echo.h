#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define FRIDAY_ECHO_SAMPLES 480
bool friday_echo_init(void);
void friday_echo_invalidate(void);
uint32_t friday_echo_generation(void);
void friday_echo_reference(const int16_t *stereo,size_t frames,int64_t started_us,uint32_t generation);
/* False means unsafe/unready: caller must suppress capture rather than use raw echo. */
bool friday_echo_process(int16_t *mono,int64_t captured_us,bool playback);
/* Metadata only: interval counts/timing, no PCM, IDs, text or timestamps. */
typedef struct {
    uint32_t capture_frames, active_frames, reference_frames, stale_reference_frames;
    uint32_t matched_frames, missing_frames, lock_drops, epoch_drops, warmup_frames;
    uint32_t clean_frames, cancellations, dsp_calls, processing_us, max_processing_us;
    uint32_t deadline_misses, alignment_abs_us, max_alignment_abs_us;
    uint32_t capture_gaps, max_capture_gap_us;
    uint32_t alignment_sum_overflow;
} friday_echo_stats_t;
friday_echo_stats_t friday_echo_stats(bool reset);
