# Preserve Wi-Fi settings through outages

The installed October 12 firmware source (`db75ebd` provenance) erased the `friday_wifi` NVS namespace after six disconnect events, then restarted into provisioning. That namespace includes saved network settings and the backend URI. A deterministic harness compiling the actual baseline event functions observed one credential erase and one restart after six synthetic disconnects. This is a production blocker even though the current physical device's display/state has not yet been observed.

The repair retains credentials and station mode through any number of disconnects. A single one-shot ESP timer posts to the existing event loop, which attempts connection after delays of 1, 2, 4, 8, 16, then 30 seconds. Immediate connection errors also schedule retries. GOT_IP resets backoff and stops the timer; queued retry events do not reconnect an already healthy link. Event-queue saturation rearms a bounded retry. No new task, provisioning mode, credentials, or NVS writes are introduced during recovery. Existing first-time provisioning remains available when no settings are saved.

The regression compiles the production recovery/event functions verbatim with fake ESP side effects. It covers 100 outages, the capped delay, successful recovery, queued stale retry, immediate connect failure, and event-queue saturation. All 504 backend/native-harness tests pass. Existing native error, glyph, media ownership, capture metadata, and ASan/UBSan echo tests also pass. The ESP-IDF 5.5 build passes using the existing pinned toolchain and dependencies. Source-only review found no blocking credential-preservation defect.

The harness is sequential: it does not prove all timer/event-loop interleavings or strict retry spacing. A queued retry processed after a later disconnect can attempt connection early. Timer fallback rearming errors and blocking UI operations remain timing limits; no universal recovery deadline is claimed.

## Device candidate versus source defaults

The hardware build uses the installed public CA, installed Kconfig definition for experience context, and the existing installed sdkconfig (including experience context and AEC). All `firmware/main` files match the installed source provenance except `friday_wifi.c`. These local build overlays are restored to repository defaults after compilation; no private key is present in the firmware image or committed source. Exact image and overlay hashes are recorded separately in the runtime owner's `oct16-production-evidence/wifi-device-image.json`. The first repository-default build was compile evidence only and is not the hardware candidate.

The prepared hardware image has not been flashed. Do not erase NVS, flash a bootloader/partition table/C6 image, change the trust root, or assume this can recover credentials the old firmware already erased. Confirm the actual device state, preserve its current NVS and application before any app-only update, verify image/readback hashes, and obtain real authentication plus physical acceptance. If already in provisioning, owner interaction with the existing local setup flow is required; do not request a Wi-Fi password in chat.


## Explicit provisioning/reset boundary

The saved-credentials/first-time-provisioning branch, setup HTTP handlers and save/restart
flow are unchanged. Recovery events do not attempt station connection when no credentials
exist, covered by the native harness. There was no on-screen factory-reset action in the
baseline; this patch does not remove one or add automatic credential deletion. An explicit
owner-directed NVS reset through the maintenance tooling remains possible, and the next
boot with no saved settings enters the existing setup flow. Such a reset is destructive,
is not an outage-recovery step, and has not been performed here. The native test extracts
production event functions; it does not exercise the real setup HTTP server or hardware.

Publication is integrated onto GitHub main rather than replacing it with the older installed
source tree. Original 504-test/device-build results describe the runtime owner's candidate;
the merged-tree suite and CI are recorded independently in the publication PR. No flash,
NVS write, live configuration or runtime change is included in this source publication.
