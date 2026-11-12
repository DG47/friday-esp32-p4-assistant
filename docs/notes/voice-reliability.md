# Voice reliability controlled trial

Developed from published `5e467ce` in an isolated candidate, then installed as an
approved controlled trial. Backend cancellation fixes are running; experimental AEC
and reviewed metadata diagnostics through `656870a` are installed. The diagnostics
flash preserved current device settings/private CA and did not restart the backend.
No paid automated provider calls, credentials changes or new scopes were used.

## What changed

Pinned official `espressif/esp-sr` 2.5.5 supplies P4 standalone AEC. The existing
shared-bus ES7210 capture and ES8311 playback both remain 24kHz. An experimental
`FRIDAY_AEC_BARGE_IN` Kconfig option is off by default. Raw mono microphone and actual
successful speaker writes are resampled to the AEC's required 16kHz; cleaned samples
return through the existing 12kHz recognition stream. This uses bounded interpolation,
not a studio-quality resampler; acoustic quality and alias rejection require measurement.
No hand-written echo subtraction is used in production.

A 24-frame reference history retains at most 480 mono samples per frame. Epochs
invalidate cancelled/old-stream writes. Capture selects a reference within 6ms of
its configured software alignment (0–200ms, initial 60ms). Reference misses, lock
contention, and processing warm-up suppress capture rather than sending raw playback
as a wake candidate. A 200ms echo-tail holdoff covers normal finish/interruption;
missing tail reference stays suppressed. Capture rechecks the epoch under the bounded
reference lock after DSP and before admitting output; cancellation or lock contention
clears pending input/output and rejects the frame. AEC allocation/init failure falls back to
half duplex. No reference is retained on disk or logged. Four aligned 1344-sample
scratch buffers plus reference history use about34KiB, excluding the official AEC's
internal allocation, whose on-device memory/CPU budget remains unmeasured.

With valid cleaned frames, the existing VAD-gated local Whisper wake path continues
while speech plays. Saying Friday requests the existing authenticated activation;
that cancels local speech and backend synthesis, then starts a new audio turn.
This is wake-word-mediated interruption, not arbitrary speech interruption. Existing
3-second wake probes plus local recognition and network timing limit response latency;
it is not claimed to be instant or physically proven. Music/priority ownership,
protected confirmation/resume, touch controls and action permissions are unchanged.

Backend speech cancel and replacement operations now serialize per device. A cancelled
old task must finish before replacement starts. Final cleanup removes only its own
completion waiter, preventing stale cleanup from discarding a newer stream.

## Verification and remaining gates

Default-off and enabled P4 builds pass against pinned official headers/libraries.
307 backend regressions pass, including concurrent replacement and cancel/replacement,
stale completion, failure, disconnect and invalid PCM behavior. Actual production
reference logic passes timestamp/ownership/gap/tail/resampling tests and50 repeated
turns, plus30 cancellation-during-DSP/reset sequences, with RTOS/AEC test doubles. Those doubles test plumbing, not acoustic AEC quality.
Existing UI/music/memory ownership regressions remain required before installation.

The experimental trial is installed. Before accepting it for everyday use, measure
speaker DMA and microphone timestamp delay, calibrate reference alignment, test echo
only/near-end/double-talk in the real room, actual reference coverage, wake latency,
AEC heap/CPU/codec starvation, alert/confirmation/Music transitions, repeated spoken
and touch cancellation, WiFi loss/reconnect and prolonged operation. Failure must
retain half duplex. No actual captured room audio is part of synthetic tests.

Calendar authorization passed using the actual supervised Google settings. Only an
existing-grant token check was performed; no event fetch, credentials write, or new
consent. This establishes grant validity at check time, not future Google Testing expiry.

Official references:
- https://docs.espressif.com/projects/esp-sr/en/latest/esp32p4/audio_front_end/README.html
- https://github.com/espressif/esp-sr/blob/master/include/esp32p4/esp_aec.h

## Installed metadata diagnostics

Atomic32 metadata counters track capture/reference frames, matched/missing reference,
stale reference writes, lock/epoch rejection, warm-up, clean output, DSP calls and
cancellations. Processing elapsed time includes DSP/resampling/lock/reset work; it
is not whole-device CPU utilization. Counters include20ms work-budget overruns,
inter-capture gaps over40ms, and distance to the closest timestamped reference.
Alignment error describes the configured software reference timing, not a measured
acoustic delay or echo-suppression score.

A separate priority1 task reports interval counters every5seconds plus internal and
PSRAM free/minimum/largest-block headroom. The capture path only updates fixed-size
atomic counters; it performs no additional logging/allocation. Snapshot/reset fields
can straddle a frame boundary, so per-interval ratios are approximate. Console behavior
and physical capture cadence still require trial validation. No PCM, transcripts,
stream/track IDs, personal facts, credentials or raw timestamps are logged.

The installed trial includes these counters. Its app-only flash used current private
CA/device settings and preserved verified AEC-trial and half-duplex rollback images.
No backend restart or new authentication credential was required for metadata telemetry.

Alignment sum uses bounded32-bit saturation at4,294,967,295us and an explicit overflow/incomplete flag. A flagged interval is a lower bound and must not be used for an exact average; the next interval resets both fields. Prolonged missing-reference calibration cannot silently wrap.
