# Capture gap investigation — 2026-10-05

## Evidence and conclusion

Inspected original checkout HEAD `9ba1cc3626f18cec2501308a50ec62d6d9c159ee`.
Its pre-existing modified backend CA file and untracked outputs were left alone.
The older voice-candidate checkout remains at `656870a05ef8a1275644d039319892c3c475b978`;
it was not used as the candidate base. Work is in an isolated local clone.

Existing content-free evidence: `voice-diagnostics-install-result.json` and
`voice-diagnostics-device.json` in the 2026-10-02/task-2 workspace. Installed
app SHA256 is `e09fc6040ceb0f523e93bf45124108e69f4ee894f5e47b33933af636f55887ab`.
Four intervals report 238/241/238/244 frames, 7/9/8/10 gaps above 40 ms, and a
maximum gap of 211321 us. Playback, references and DSP calls are all zero;
maximum echo wrapper work is 11–15 us. No microphone read errors, crashes,
watchdogs or unhealthy memory were observed.

**The metric detects application capture-service stalls, not sample continuity.**
It timestamps `friday_echo_process()` after codec read and mono conversion.
The difference therefore includes the preceding frame's network/UI/log work,
task scheduling, the next blocking read and conversion. It is not an I2S hardware
timestamp or lost-sample count. AEC DSP cannot explain these intervals.

## Cadence and buffering

* 24 kHz, signed 16-bit, two selected TDM slots (FL+FR, slot0+slot2) in a four-slot
  bus. A 1920-byte read holds 480 samples/channel: 20 ms of audio.
* Codec reconfiguration applies the selected slot mask; ESP-IDF sets active slots
  to its popcount. Four bus slots do not double the application frame duration.
* Mono downsampling yields 240 samples at 12 kHz per frame. Wake batches contain
  25 frames/500 ms; active turns send one frame/20 ms.
* BSP uses `I2S_CHANNEL_DEFAULT_CONFIG`: six DMA descriptors, 240 sample times
  each (10 ms). I2S RX queue has `desc_num-1`, five entries. Physical ring span is
  60 ms; queued completion span is 50 ms, not a guarantee of 60 ms safe latency.
* On a full RX queue, the ISR removes the oldest entry and optionally invokes
  `on_recv_q_ovf`; this application registers no overflow callback. Later reads
  can still return success. Thus no read errors does not establish losslessness.
  The driver can also abandon a partially consumed buffer when the queue is
  nearly full to avoid a stale pointer.

A stall much longer than the DMA span while capture runs is an actual loss
mechanism, not harmless buffered cadence. The 211 ms observation is consistent
with that mechanism and ordinary 10/20 ms batching does not explain it. The
lower frame counts support delayed/lost service but cannot quantify exact lost
samples: the first interval includes initialization, intervals are reset from
another task, and no hardware sample/overflow counters were recorded. Exact
loss and event-by-event attribution remain unmeasured.

## Blocking paths and priorities

* Microphone: priority 6, pinned to core 1; speaker: priority 5; diagnostic task:
  priority 1. LVGL adapter defaults to priority 6 without affinity. lwIP TCP/IP
  priority is 18 without affinity; WebSocket default priority is 5. Raising mic
  priority cannot eliminate waits it explicitly performs.
* `friday_ui_set_microphone` executes every 12 frames (240 ms nominal) and waits
  up to 1000 ms on the recursive display lock. Scene acceptance, artwork decode,
  render updates and LVGL processing share that lock. This is a reproducible
  software capture-blocking path even with playback/AEC idle.
* Active binary send can wait 1000 ms for send_lock and another 1000 ms inside
  WebSocket send; wake batch uses 2000 ms for each. Wake/start/end text operations
  wait 250–1000 ms; opening the wake gate waits 500 ms. The loop has no dedicated
  capture queue/worker. Zero DSP/playback counters do not imply no wake sends.
* Codec read timeout is 1000 ms. Logging occurs every 50 frames and on VAD/send
  events. ESP-IDF logging/stdio locks and console writes can wait; console is
  UART 115200 with secondary USB Serial/JTAG. Diagnostics themselves log large
  metadata lines from another task every five seconds. No stage timing was
  recorded, so logging, scheduling and network contributions cannot be separated.

## Focused candidate and regression

The periodic meter/status refresh now requests the display lock with zero wait,
skipping the update if busy and resuming on a later refresh. Other state/scene
updates retain their existing behavior. `esp_lv_adapter_lock(0)` maps to
`xSemaphoreTakeRecursive(..., 0)`: zero really is a try operation in pinned 0.6.3.
The adapter emits an error log on failed acquisition; that log is a remaining
possible console wait, so this fix removes the display-lock timeout but does
not claim an entirely wait-free capture loop.

The native real-LVGL harness models a renderer holding the display mutex for
211 ms. It verifies the production setter requests zero wait, does not unlock
or mutate through a failed acquisition, then resumes after release. Compiling
this regression with the original UI source fails the zero-wait assertion;
the candidate and existing native UI replay pass. No real audio is involved.

Validation: 307 provider-free backend tests; 10 synthetic localhost callback
scenarios; native real-font, media ownership, recoverable error and ASan/UBSan
AEC reference/metadata regressions; native real-LVGL UI harness; Python compile
and whitespace checks. Both AEC-enabled and disabled firmware builds passed.
Native AEC uses a DSP stub and cannot validate physical echo cancellation.

During the initial investigation, no restart, flash, live config/credentials/permissions change, provider call,
raw audio/transcript collection, or push was performed. Device improvement and
precise stage attribution need a later authorized on-device test with bounded
read/work/UI/send timing and RX overflow counters; existing summaries alone
cannot identify which operation caused each gap.


## Authorized installation and timing comparison

The user approved app-only installation, timing comparison and publication on
2026-10-05. Built with the existing private CA, unchanged sdkconfig and current
AEC settings. App SHA256:
`e0c4aa3ae3033dea7757b04e3c5af4019567d63a21b3b38799cc51a49e17bd97`.
Flashed only the app at `0x10000` on the same ESP32-P4; esptool verified the data
hash and rebooted. The rollback app matches the prior installed SHA256
`e09fc6040ceb0f523e93bf45124108e69f4ee894f5e47b33933af636f55887ab`.
CA and configuration remained byte-identical. No backend restart.

Both serial collection sessions were bounded to 35 seconds and retained only
numeric diagnostics/heap fields and health flags, never raw serial logs, audio
or transcripts. Each session yielded five nominal five-second diagnostic
windows; startup/reconnect time is included in the collection duration, not all
35 seconds in the five counters. Opening the serial session observed boot/AEC
initialization, so these are startup-inclusive observations, not a continuous
steady-state experiment.

| Metric | Before | After |
| --- | ---: | ---: |
| Diagnostic windows | 5 | 5 |
| Capture frames | 1150 | 1226 |
| Gaps above 40 ms | 28 | 9 |
| Maximum capture gap | 1126.614 ms | 243.084 ms |
| Gaps excluding first window (4 windows each) | 21 | 7 |
| Frames excluding first window | 917 | 982 |
| Busy LVGL lock error logs | 0 | 76 |

Every diagnostic window had zero active playback/reference/DSP counts. Neither
session showed a panic, watchdog report or microphone read error. The 76 busy
lock logs show the new try operation actually skipped contended updates.
Residual gaps remain. This single before/after pair supports improved observed
service timing; it neither proves every original gap came from the UI lock nor
establishes sample continuity or lossless audio. Network/logging/scheduling
remain possible contributors, including the adapter's error log on a failed try.

The device received backend `ready` after reboot (the firmware logs only its
message type), confirming authenticated reconnect. Separate read-only HTTPS
checks passed normal CA verification, `/health=ok` and `/ready=ready`. The
existing local admin token returned 401 for `/api/diagnostics`; device-count and
worker fields from that endpoint could not be verified, and credentials were
not changed. Aggregate tests run directly beside the live .env hit private DB
sandbox write restrictions and an environment-dependent memory setting; the
same source in the isolated provider-free checkout passes all 307 tests.
