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

## Persistent view and freshness

Weather stays open until Dismiss or a newer navigation/priority surface. It is
excluded from the generic scene-closing expiry timer. Current WMO conditions
lead the headline and each city card; the next full hour's probability remains
secondary and never controls the current condition or image.

The backend supplies a bounded data-valid duration: the earliest of the two
areas' remaining five-minute cache lifetime, observation-age limit, or the next
full-hour label changing. A single device-owned one-shot timer then clears
temperatures, current-condition claims, probabilities, wind and humidity without
closing Weather. It switches art to neutral and labels the old window as past.
This works while disconnected; no automatic provider polling is introduced.
Reopening Weather explicitly refreshes data under the existing cache rules.

Scene replacement cancels the timer, and its generation guard prevents an old
callback from replacing a newer Weather result or protected interaction.
Missing/invalid freshness metadata fails closed. Allocation failure clears data
instead of leaving current values unbounded. All-unavailable views contain no
current measurements and remain honest without a data timer.

At the exact hour, the selected complete interval starts now and remains valid:
its end, rather than its start, bounds the normal remaining cache/observation
lease. This retains the existing exact-hour selection while avoiding an
immediate blank. Requests just before the hour still expire at that boundary;
requests just after it select the following complete hour.

The absolute data deadline is retained with the surface, and its remaining lease
is recomputed inside each peer's send lock. Queued delivery or a stored-result
replay cannot regain the original five-minute lifetime; malformed/past deadlines
clear data immediately. Internal cache freshness fields are removed from tool
payloads.

This combined persistence/freshness change requires reviewed backend deployment
and an app-only firmware flash; the Fahrenheit conversion alone does not.
