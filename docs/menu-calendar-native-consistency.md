# Modern native local launcher and Calendar agenda

Verified source base: b32b57ce0027bf20e7e73a86fa7dc4e2ff0784df. The earlier 645a442 design was rejected visually and is superseded by this revision; the revision still requires user visual acceptance.

## Cause and source ownership

friday_ui.c presence_event_cb routes long press to status. backend/app/scenes.py local_scene returns the metric local menu; CalendarService.scene in backend/app/tools/google_calendar.py returns timeline, message and artifact surfaces. Native friday_ui_set_scene renders them. Original v2 specialized ambient and Music but left browsing on generic compact geometry. Earlier QA illustrated a simplified metric fixture, missing the actual menu. The first consistency proposal corrected dimensions but retained a boxed diagnostic dashboard, which the user rejected as retro.

The original checkout remains at the requested b32b57c. Its only tracked local modification is the private CA. Remote feature branch also points to b32b57c. Remote HEAD 677a1a443e91634291e76fdab5cf642b5ac14f2a is Dhruv Gupta's October 1 merge; its tree equals shared ancestor 632668450c31e9285b11ff987fb9ba6604b21f95, tree 1d530664b119a7ad97cab7959d31ce39c74cf949. The local source contains eight later implementation commits; there is no newer remote-only implementation to integrate. Original checkout, private config, session scratch and outputs were not overwritten.

## Modern native presentation

The exact local menu is a four-tile touch launcher: 300x184 targets, native 44px line icons, 28px rounded corners, quiet secondary hints, distinct soft slate/green/lavender surfaces, and a warm Calendar tile. The original status facts remain in a complete scrolling footer, instead of being the main navigation. All original Memory, Reminders, Calendar and Music action IDs remain intact.

Calendar is an open editorial agenda: Today and a timezone-correct day/date, subdued source/read-only/stale disclosure, generous time/title columns, one softly emphasized first row, quiet timeline separators, and a single bottom row of 64px pill controls. Details use the same agenda components. Empty/loading/disconnected/revoked/error states retain honest text and controls with a restrained native Calendar illustration. Long event titles retain complete label text and native circular scrolling.

Only backend behavior change is presentation: prefix the existing Calendar subtitle with a day/date derived from its existing configured timezone/clock. No schema, provider, OAuth, privacy, event selection or action change. Dates are not derived from the ESP uptime. No new feature is added. Native browsing dimensions remain stable when later assistant text updates arrive. All browse-specific styles and extra objects reset/hide on exit; memory pagination and Music layout stay unchanged.

## Final verification

308 aggregate backend tests PASS, including a new UTC-midnight/configured-zone date regression. Ten real localhost callback scenarios PASS. Repo-required native glyph/error/media guard and sanitized AEC-reference tests PASS, plus strict artwork, protected overlay, actual WebSocket assembler and exhaustive memory geometry/parity checks. Python compileall and git diff --check PASS.

Actual production LVGL 9.3 RGB565 renderer at 720x720 captures both base and candidate with identical synthetic production Calendar fixtures: exact menu, pages 1/2, details, stale, empty, revoked, unconnected, error, loading, long titles and a later scroll frame. Native bounds/minimum 48px clickable assertions, production long press/release suppression, 480 routed browsing actions across 20 repeated/interrupted flows (including exact declared button IDs), and existing 50 cancellation/reentry cycles PASS. New extra icon/date/footer objects remain hidden on protected and Music surfaces.

Actual backend publisher/ownership/cover conversion generated 44 operations, replayed through the production renderer: over 1000s persistent Music, reminder pending/ready, memory invalid-token/idle barrier, explicit cancellation/resume, late frames, dismissal/reentry and disconnect PASS. All fixture event names, artwork, facts and tokens are synthetic; no real Calendar content or provider credentials entered previews. Settled LVGL heap 26728 bytes; peak 85712 bytes. Final ESP32-P4 build PASS: app 0x1b2340 bytes, 79% of 8MiB application partition free. Current private backend CA and sdkconfig match the original byte-for-byte and remain excluded from commits.

## Installation scope and limits

Production patch scope is firmware/main/friday_ui.c plus the presentation-only date line in backend/app/tools/google_calendar.py. Other changes are regression fixtures/tests/docs. No live install, flash, restart, push, provider/security/settings change occurred. Applying this revision eventually requires both source changes; do not overwrite CA, sdkconfig, .env, data or user outputs.

Host captures prove native rendering and routing with ESP/BSP/transport stubs, not physical calibration or device animation/DMA/PSRAM performance. The synthetic callback accepts actions without live providers; backend tests separately cover privacy/action semantics. Physical testing requires separate approval.
