# Weather city illustrations

Weather uses generated full-color waterfront illustrations for Jersey City and
Manhattan. They are artistic city representations, not live camera images or
verified architectural records. The source illustrations were inspected before
mechanically splitting/resizing and converting them to RGB565 little-endian.
The checked-in manifest records every asset's dimensions, byte count and digest.

Ten immutable 680×190 assets (five per city) total 2,584,000 bytes. Both firmware
and the native renderer compile exactly these bytes with `weather_assets_c.py`.
No HTTP artwork fetch, dynamic decoding, canvas buffer, runtime pixel allocation
or unbounded cache is introduced. The existing firmware configuration maps
read-only data into PSRAM; this static footprint is separate from LVGL heap
measurements and must be included in device memory evaluation.

Current WMO condition and explicit `is_day` alone select the imagery. Missing or
invalid fields use neutral art. Recognized day conditions select the day, rain,
snow or neutral overcast treatment. Night uses neutral night art without
asserting precipitation; the current-condition text remains authoritative.
Unknown cities have no substituted city image. Future precipitation probability
never enters the asset selector and cannot make the current city appear rainy.

Scene reset clears image references before other layouts reuse the cards. The
assets and descriptors have static lifetime and require no cleanup allocation.
The fixed Spotify 192×192 artwork codec and 128KiB transport bound are unchanged.

Native captures validate production rendering and synthetic navigation. They
are not proof of calibrated physical touch, physical display pixels, device FPS
or successful voice interaction; physical voice testing remains deferred.
