# Continuous presence candidate

Isolated from verified published ccc8cacb0469163484cebe4593e52218e52c2f7e. No device, service, configuration, credentials, microphone collection or backend change.

## Design checkpoint

The existing experience keeps its ribbon moving while idle, changes activity intensity instantly, has no explicit ribbon touch response, and retains thinking copy when returning to idle. Its single persistent presence object already survives compact/full navigation; rebuilding it or animating whole content would introduce ownership and readability risks.

This candidate keeps that object and its phase. Real listening/thinking/speaking use the existing distinct ribbon rhythm and shapes; their intensity eases from the current intensity to the new target over 240ms. Idle and alert settle to stillness. Alert retains the existing amber palette. Touch-down expands the ribbon immediately and native controls tint on press; release or press-loss cancels feedback. Actual tool completion adds one decaying 320ms expansion, with no new text. A newer state, touch or compact/full view switch cancels that cue. Repeat state updates do not restart the easing. Idle clears only activity-generated copy, tracked by provenance at write time. Setup, pairing, responses and arbitrary user text—including text identical to the generated thinking line—survive repeated and idle state updates.

Compact/full layout still switches immediately to keep content and controls available. Continuity is the same phase, palette and state expression, not a flying object, content fade, new navigation delay or implicit media resume. Existing protected confirmation and media ownership rules are untouched. No consciousness, inferred feelings, simulated knowledge, proactive message, integration or scope is added.

## Implementation and bounds

Production LVGL 9.3 software-renderer source is used. One existing 33ms timer owns the controller; no lv_anim allocation, new object, framebuffer, raster asset or timer is added. Per-instance fixed fields only. Tick arithmetic uses LVGL wrap-safe elapsed time. No animation queue exists. Paused idle controller resumes on activity/touch/completion; deletion frees its timer and context. Drawing is confined to existing ribbon geometry (250 full / 60 compact line segments). Text, lyrics, artwork, hitboxes and layout never animate. Device physical rendering/input/heap limits remain unmeasured.

## Validation

Run `python3 scripts/native-ui/test_presence.py --lvgl LVGL_SOURCE` and the existing `scripts/native-ui/build.py --lvgl LVGL_SOURCE --cjson CJSON_SOURCE` with backend dependencies installed.

Controller: 1000 cycles cover repeat-state idempotency, midway reversal, completion interruption, press loss, compact/back phase continuity, 240ms settle, 320ms completion expiration, idle pause, zero heap growth and deletion. Existing native suite covers 480 routed browsing actions over 20 cycles, 528 recovery/zero-wait/protected notice cases across 11 scenes, ownership replay, stale media rejection, confirmation protection, lyric wrap/swipe and all-screen metadata audit. Native synthetic captures provide eleven 720x720 frames at 100ms intervals. Host CPU measurements describe software rendering only, not ESP32-P4 FPS, calibrated touch latency, AEC timing, PSRAM bandwidth or on-device fragmentation. Physical validation is a separate remaining gate; no flash, restart or push was performed.
