# Actual native LVGL UI capture

Run `python3 scripts/native-ui/build.py --lvgl <pinned LVGL 9.3 source directory> --cjson <ESP-IDF components/json/cJSON directory>`. Add `--before` to compile Git baseline `647b3da` with the same fixtures. No display service, browser, network, or real device is required. The output uses the native RGB565 software renderer and production UI source.

The test uses static host display buffers and a 16 MiB allocator arena so desktop assertions cannot hang while rendering; it reports actual LVGL heap use rather than treating arena size as used memory. This is not a firmware heap configuration change. Candidate assertions check bounds and 48-pixel clickable targets; synthetic actions and ESP/BSP functions are stubbed. Device FPS, transport effects, and calibrated physical touches require a separately approved device test.

See `docs/native-device-ui-candidate.md` for validation and deployment limits. PNG conversion can use macOS `sips -s format png file.ppm --out file.png` or any lossless PPM converter.
