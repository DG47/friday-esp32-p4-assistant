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


## Current refinement: no technical metadata on native screens

The request was no on-screen technical metadata, with the visual imbalance resolved without a reference design. This revision supersedes fc72d378's appearance; it still requires visual acceptance. Visual critique identified competing tile colors, an unexplained bright Calendar destination/first-event emphasis, redundant promotional captions, inconsistent control radii and pipe-like agenda separators. Tiles now share one quiet neutral surface and consistent line icons; the heading is short; captions/footer disappear. Browsing controls use the same 20px corner language as Music. Event titles are 32px with quiet 18px times, no ornamental markers or implied next-event selection. Details use quieter 18px field labels and 24px content. Empty-state icon/text form one group. Full title scrolling completes on a 9-second cycle within the scene lifetime.

| Native screen/path | Removed from display | Preserved |
|---|---|---|
| Ambient/listening/thinking/speaking/attention | Network/authentication/connection, microphone status, duplicate state labels, peak/RMS | Expressive ribbon and response/user instructions |
| Local launcher | Provider/runtime/status facts/footer, promotional captions | Four exact routed destinations |
| Calendar agenda/pages/details | Service/read-only/cache/zone/event-count implementation line | Today/date in configured zone, event names/times/dates/location/recurrence, navigation |
| Calendar stale/empty/loading/auth/error | Cached/provider diagnostics | Plain refresh/connect instructions and honest empty/loading states |
| Music and ownership transitions | Runtime/control/source-device details, record IDs/matching labels, default CURRENT LINE label, cover-load diagnostics, synthetic QA attribution | Artwork, title, artist, complete lyrics, elapsed/duration, controls, swipe hint, required real source/rights credits |
| Memory list/detail | Internal IDs, source/create/update/edit provenance rows and extraction implementation copy | Saved text, routed review/forget actions and complete pagination |
| Memory/protected confirmations | No safety content removed | Exact full review text, 620x76/Montserrat24/tracking2 geometry and page progress; Wi-Fi/tool completion cannot overwrite it |
| Reminders/list/timer | Internal reminder IDs/page summaries | User reminder text, due dates/times, timer, complete/snooze/cancel/dismiss |
| Weather/metric | Provider/network/runtime/transport rows and UTC diagnostic timestamp | Weather measurements and necessary Open-Meteo credit |
| Tool activity and generic comparison/person/email/map/progress/graph/camera/artifact | Tool names/status codes and raw renderer-kind headings | User content, calm progress and actionable failure copy |
| Error/voice/pairing/setup | Raw transport/JSON/request/capability/error internals and routine IP connection detail | Plain retry/reconnect instructions, voice fallback notice, pairing code and setup instructions when needed |

The policy is applied by semantic scene/field origin, not blanket word filtering: arbitrary saved/user text containing words such as provider, codes or metadata is preserved. All-scene rendering is protected by one outer recursive display lock so generated metadata cannot appear in an intermediate frame. Hidden summaries cannot be reintroduced by a later assistant.text update. New generated objects reset/hide across scene transitions, while essential content becomes visible again.

Necessary exceptions: Spotify and lyrics-source credits/rights notice, Open-Meteo attribution, confirmation/pagination safety text, actionable stale/error messages, pairing codes and Wi-Fi setup name/address instructions. Routine connected IP/SSID/credential/debug state stays hidden. Diagnostics remain in existing privacy-safe logs; no raw content logging was added. No real Calendar content entered previews.

Final verification: 308 backend tests, ten localhost scenarios, all-screen positive/negative native visibility audit, 480 routed actions/20 repeated browsing cycles, existing 50 cancellation cycles, 44 actual backend/native ownership operations, busy meter lock, glyph/error/media/overlay/artwork/sanitized echo/transport regressions and exhaustive memory geometry all PASS. Native audit deliberately injects provider/record/IP/ID/JSON/request sentinels and verifies they are absent while user content, lyrics, dates/times, credits, actionable errors and protected facts stay visible. Standard and protected confirmation fixture pixels remain byte-identical to b32b57c. Music changes are display-copy visibility only; approved artwork/lyrics/control geometry is preserved.

Final ESP32-P4 build PASS: app 0x1b3610 bytes, 79% free application space. LVGL settled heap 26552 bytes, peak 85520 bytes; retained artwork buffer unchanged. Python syntax/whitespace checks PASS. Original checkout/private CA/sdkconfig remain preserved; no install, restart, flash, push or provider/security/settings change. Physical touch calibration, device animation/DMA/PSRAM and live transport behavior remain separately unverified.


## Recovery regression closure after 5fcddcd

Independent review found microphone failures became invisible when raw codes were hidden, and the voice fallback at y666 overlapped Calendar controls at y626–689. A shared actionable notice now occupies the reserved y4–27 strip across every scene, preserving response text, credits and controls. An atomic microphone failure flag is recorded before the existing zero-wait display lock; a 100ms UI timer delivers one-shot failures after a busy render without blocking capture. Corresponding success clears the notice.

528 native checks across 11 scenes and 12 cycles cover both microphone error codes, unknown interim status, busy error/success delivery, combined voice/microphone failure, interrupted navigation and content retention. Notice text fits and overlaps no visible root object. All eight normal previews remain pixel-identical. Existing aggregate 308 tests, ten synthetic localhost scenarios, native transport/display/media/overlay/artwork/echo and memory geometry regressions pass. Firmware build passes: app 0x1b3790 bytes, 79% free. Host settled heap 26632 bytes, peak 85584 bytes. Private CA/sdkconfig are byte-identical to original.

Synthetic 720×720 recovery Library previews: microphone libfile_4a1ae604ea248191b3bb667610a320b8; voice libfile_a07b4e02b7e08191a47721727f2da1ad. No install/flash/restart/push or live provider call. Physical touch, animation/DMA/PSRAM and device recovery remain unverified.
