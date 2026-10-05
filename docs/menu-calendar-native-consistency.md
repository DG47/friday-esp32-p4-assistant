# Local menu and Calendar native consistency candidate

Base: b32b57ce0027bf20e7e73a86fa7dc4e2ff0784df. Remote HEAD read-only lookup on 2026-10-05: 677a1a443e91634291e76fdab5cf642b5ac14f2a. No remote changes were imported. Isolated candidate clone; original checkout untouched. Its modified backend CA and sdkconfig were copied for the local build, excluded from the candidate commit.

## Cause and native paths

firmware/main/friday_ui.c presence_event_cb routes long press to status; backend/app/scenes.py local_scene returns the metric local menu. backend/app/tools/google_calendar.py CalendarService.scene returns timeline lists and message/artifact details or unavailable states. friday_ui_set_scene and scene_layout render those surfaces on the actual LVGL display. V2 specialized ambient and Music, but browsing retained generic 32px headings, centered tracking-2 subtitles, 64px rows and 58px controls. The status value was confined to a 104px time column. Native captures had illustrated a simplified status fixture rather than the exact local menu, missing the truncation.

## Change

Shared palette tokens and browse_layout apply the Music/ambient palette, layered rounded surfaces, 48px headings, left-aligned untracked copy, 72px outlined rows and 64px controls with 48px outer spacing. Status labels and values get separate generous columns with wrapping. Calendar times remain quiet; long headings and event titles use native circular scrolling. Calendar actions select the same component styling for details and unavailable states, while memory review/message pagination remains unchanged. Calendar privacy, action IDs, paging, dismissal/expiry, ownership handshakes and provider behavior are unchanged. Generic layout resets all browse-specific row/font/label styles before reuse.

## Verification

307 aggregate backend tests PASS. Ten real localhost Spotify callback scenarios PASS, no credential exchange. Repo CI native recoverable-error, Montserrat glyph/media guard and sanitized echo regressions PASS. Additional native protected overlay and strict artwork regressions PASS; existing artwork test has an unused-variable warning and was compiled without -Werror. Full memory geometry/parity verification PASS. Python compileall and git diff --check PASS.

Both base and candidate were compiled against installed LVGL 9.3 and actual production C renderer, RGB565 at 720x720, with the identical synthetic production Calendar fixtures. Includes exact local menu, first/second page, detail, stale, empty, revoked, unconnected, loading, error and long title. Bounds and minimum 48px clickable target assertions PASS. Production long press and release-click suppression PASS. 740 routed native actions across 20 repeated/interrupted browse/reentry cycles PASS, followed by 50 existing cancellation/reentry cycles. Existing Music ownership/protected resume and capture meter busy-lock checks remain enabled and PASS. Memory and artwork-Music fixture pixels are byte-identical before/after. Renderer heap settled 22952 bytes; peak 81968 bytes. Screens visually inspected locally; all preview data synthetic.

Final ESP32-P4 firmware build PASS: app 0x1b1380 bytes, 79% of 8MiB app partition free. Private current backend CA/config preserved in local build and excluded from commit. No flash, restart, push, provider or security change.

## Limits

Host captures use production native rendering with ESP/BSP/transport stubs; physical touch calibration, on-device animation smoothness, DMA/PSRAM pressure and live transport interruption require a separately approved device test. The callback fixture accepts actions without contacting a backend. Backend action/privacy tests cover those semantics separately. No real Calendar event data or credentials were included in preview artifacts.
