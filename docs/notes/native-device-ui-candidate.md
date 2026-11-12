# Friday native device UI v2

This supersedes the rejected `daf2310` visual candidate. The verified design has an original flowing light signature with state-specific motion and microphone-level response, a real-cover-led Music scene, a separate 32px current line, 72px transport controls, quiet timing/attribution, and shared calendar/status/confirmation styling. Generic confirmations retain the exact Montserrat24,620x76,tracking2 memory pagination contract. No app grid, breathing-dot identity, fabricated album art, or third-party raster assets.

## Combined implementation

Backend patches through `6359e7601e2331568197d769a0aa74fd0b64e434` are integrated with the native implementation on `friday/native-device-ui-v2`, based on live baseline `647b3da`. Music has no display TTL and persists until explicit dismissal. Priority overlays preserve source ownership, block in-flight media, and require the exact pending/ready resume handshake followed by a newer owned frame. Protected facts remain displayed; close or timeout is not confirmation. Owner-bound explicit cancellation can resolve an expired memory confirmation; expired acceptance, invalid tokens and wrong owners remain rejected. Disconnect invalidates ownership and stale pixels while retaining an honest Music refresh screen.

Artwork is opt-in through MUSIC_ARTWORK_ENABLED and media_artwork_v1. Backend secure fetch/verified decode/aspect-preserving full-composition fit produces192x192 RGB565 little-endian. Native validates frame owner/sequence, opaque track_key, safe-integer artwork_generation,192x192 geometry, exact73728 decoded bytes/98304base64 chars, and SHA256. One retained cover buffer; invalid/loading/unavailable/changed ownership or priority suspension removes old pixels. Source imagery is not cropped or covered by controls/text. A131072-byte assembled WebSocket ceiling and contiguous-fragment checks bound transport. media_persistent_v1 controls retained overlay ownership. See media-v2-protocol.md for exact fields.

## Verification

- Final isolated ESP32-P4 firmware build passes: app0x19f190 bytes;80% of8MiB application partition free.
- Combined305 backend tests and10 real localhost mock-loopback scenarios pass.
- Native artwork strict base64/hash/size/generation/track/cache tests, exact overlay protected/pending/ready/stale binding tests, and actual production WebSocket assembler ceiling/order/reset tests pass.
- Actual backend publisher, cover conversion and ownership logic generated44 operations replayed through actual production native UI/LVGL RGB565. Covers1000s persistence, protected reminder pending/ack/resume, memory idle/invalid-token protection, explicit cancellation/resume, dismissal/reentry/disconnect and stale-frame rejection.
-23 native720x720 captures cover ambient/listening/reasoning/speaking/attention, artwork/music/current line/disabled source, calendar/status/reminder/timer, full memory confirmation, long text, and real-backend protocol transitions. Native bounds/minimum48px touch assertions and50 routed cancellation/reentry cycles pass.
- Existing native glyph and exhaustive memory geometry parity checks pass:9120 glyph measurements,3877 line-break cases,14296 pages; maximum620px wide and54px high within76px.
- Host capture CPU max2.33ms; LVGL arena peak82064 bytes, settled22888. The separate retained artwork buffer is73728 bytes. Largest actual fixture frame99291 bytes, below131072. The receive buffer+cJSON text+one image+observed renderer scratch remain within a conservative512KiB cover-transaction budget for the bounded protocol. No artwork double buffer or native image HTTP client.

Host timing is not on-device FPS. The physical device must confirm animation cadence, PSRAM/DMA contention and touch calibration. Capture.c uses actual pinned LVGL/software RGB565/Montserrat, production renderer/artwork/overlay/guard/normalizer; only ESP/BSP/transport fixtures are stubbed. No browser screenshot is used as native proof. All illustrated covers, lyric lines, names, calendars and facts are original synthetic fixtures; no personal Spotify content was fetched for mock QA.

## Assets and charset

The ribbon and synthetic cover are original procedural graphics created for this project. Existing LVGL9.3 bundled Montserrat is used at18/24/32 and newly enabled48px; no downloaded artwork/fonts. Existing font-aware glyph sanitizer stays authoritative; this does not add CJK, emoji or broad-script shaping. Memory remains conservative display-only normalization and complete sequential review.

## Installation

User explicitly approved installing v2, Pillow, backend restart and firmware flash on2026-10-02 at20:20:49UTC. Installation must back up source/private configuration/data and current firmware, preserve current backend CA/runtime configuration and NVS/Wi-Fi provisioning, rebuild locally, and flash the application only at verified0x10000. No Git push or merge is authorized. Deployment evidence, rollback location and remaining physical verification are recorded separately after installation.
