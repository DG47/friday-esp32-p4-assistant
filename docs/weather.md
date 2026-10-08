# Opt-in weather for two ZIP areas

Weather stays disabled until explicitly configured. The named-location path shows
both configured locations by default, accepts either ZIP through the `get_weather`
`location` argument, and never treats either one as home or work. There is no
runtime geocoder or arbitrary user-supplied provider URL.

## Approved area points and provenance

The user selected ZIP labels **07306** and **10010** and approved sending their
approximate area coordinates to Open-Meteo, including returned weather results
flowing to the configured Claude provider for requested spoken weather use.
This is not an exact address or live location.

The [U.S. Census Bureau Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html)
provides representative latitude/longitude points for geographic areas. On
2026-10-07 the [2026 national ZCTA file](https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_zcta_national.zip)
contained these rows (`INTPTLAT`, `INTPTLONG`):

| ZIP label | Latitude | Longitude |
| --- | --- | --- |
| 07306 | 40.736294 | -74.071128 |
| 10010 | 40.739045 | -73.982616 |

ZCTAs approximate ZIP areas; their representative points are not personal
locations. Keep these ZIP labels, including the leading zero.

## Configuration and startup

For a later approved activation, set the following JSON in the existing private
`.env`, with legacy `WEATHER_LATITUDE` and `WEATHER_LONGITUDE` unset:

```dotenv
WEATHER_LOCATIONS=[{"label":"07306","latitude":40.736294,"longitude":-74.071128},{"label":"10010","latitude":40.739045,"longitude":-73.982616}]
```

The installed LaunchAgent currently pins `WEATHER_PROVIDER=none`. Its generator
now supports an explicit `--weather-provider open-meteo` alongside the existing
`--tts-provider macos`. Generating the candidate definition, installing it, and
restarting are separate actions; no definition is installed by the generator.
`run_backend.py` preserves an explicit environment provider and defaults to none.
For direct backend startup, `WEATHER_PROVIDER=open-meteo` may be set in `.env`.

Configuration accepts at most two unique named locations and rejects a mixture
with legacy coordinates. Existing single-coordinate configurations still work.
Keep ZIP labels as routing identities. The approved display labels are Jersey City, NJ
for 07306 and New York, NY for 10010; no home/work role is inferred.

## Requests and failure behavior

The fixed HTTPS destination is `https://api.open-meteo.com/v1/forecast`. Each
selected location sends its latitude/longitude, requested temperature/humidity/
wind measurements, Celsius/kmh unit preferences and UTC timezone. Open-Meteo
also receives normal connection metadata. No credentials, account identifiers,
exact address or device location are sent by this adapter.

Each location has its own five-minute memory cache and concurrent-request lock.
A failed location retries on the next request; the other cache stays usable.
Expired or stale data are never passed off as current after a failed refresh.
Both location results are bounded and validated separately. When all locations
fail, the tool fails honestly; when one succeeds, its reading remains visible
beside the other's unavailable state. No background polling is added.

`show weather`, `weather for both`, `weather in 07306`, `weather for 10010`, and
`weather 07306 and 10010` route locally without reasoning-provider inference.
Other natural-language weather requests can use the model tool. Its response
includes selected ZIP labels, observations, source and units; it does not include
coordinates. The screen shows city/state names, useful measurements/failure text, and a discreet
Open-Meteo credit. It does not show ZIP routing codes, timestamps or operational
diagnostics. Weather data:
[Open-Meteo](https://open-meteo.com/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Review and rollout

1. Review the commit and synthetic native `weather-both`, either-ZIP,
   `weather-partial` and `weather-unavailable` previews.
2. After rollout approval, apply the backend/scripts commit and the exact named
   location JSON, preserving secrets, CA, DB, local speech and other opt-ins.
3. Generate and inspect a candidate LaunchAgent selecting `open-meteo` and the
   existing local speech option. Replace/restart only after separate approval.
4. Verify the weather route through the running device connection. Firmware
   production source is unchanged, so weather needs no flash. Physical voice
   testing remains deferred unless requested.
5. Rollback by restoring `WEATHER_PROVIDER=none` and restarting after approval;
   neither credential changes nor database migration are needed.

Candidate touch-only hold-menu access and firmware rollout gate: [weather-touch.md](weather-touch.md).

## City-label and native presentation refinement

The display-only mapping uses Jersey City, NJ (07306) and New York, NY (10010).
The Census representative points above remain unchanged. Their postal city/state
association is corroborated by the primary location pages of
[RWJBarnabas Health](https://www.rwjbh.org/our-locations/specialty-centers/the-jersey-city-family-health-center/)
and [Baruch College, CUNY](https://www.baruch.cuny.edu/about-baruch/). These are
public institutional examples, not personal addresses; no runtime geocoding or
location request is added. The raw ZIP remains in model tool payloads and caches.

The named-location screen has the agenda's 48px left margin, 48px headline,
compact ribbon and open spacing. Each location has its name and large Fahrenheit
reading on one line, followed by quieter wind in km/h and humidity in percent.
The redundant summary panel is hidden; partial/total failures retain each city
name and a clear unavailable/retry message. Dismiss remains. Weather stays open, while a bounded device timer clears expired data without closing the view.
A small bottom credit uses the existing attribution component. No conditions
icons, forecast, extra controls or provider change are added.

## Precipitation probability and grounded presence

The [official hourly definitions](https://open-meteo.com/en/docs) describe
`precipitation_probability` as probability of more than 0.1 mm precipitation in
the preceding hour, including rain/showers/snow. It is an ensemble estimate at
roughly 27 km resolution, not a rain-only probability or exact personal-location
forecast. The approved endpoint and approximate area points remain the same.
The request additionally asks for this hourly field and six forecast hours;
current observations, Celsius/km/h preferences and UTC request timezone remain.

At 13:23 local time the selected complete interval is 14:00–15:00, using the
provider's 15:00 endpoint. At exactly 14:00, 14:00–15:00 is selected. The UI says
**Next full hour**, then its date and local endpoints. UTC interval selection and
`America/New_York` conversion avoid ambiguous provider local timestamps. Both
EDT/EST labels are retained when a repeated/skipped DST hour changes offset;
midnight includes the next endpoint's date. Both approved cities share the same
interval. If gathering/publishing crosses the hourly boundary, any reading for
the old interval becomes unknown rather than being mislabeled.

Each five-minute area cache retains hourly data and selects the interval again
on every read. A missing/null/out-of-bounds probability, invalid units, mismatched
arrays, nonhourly/unsorted/gapped timestamps or missing selected endpoint yields
**Rain / snow chance unknown**; current observations can still be shown. An
expired cache whose refresh fails remains unavailable. No interpolation, daily
aggregation, probability summation, invented next-hour value or default-zero
fallback is used.

Summary rules are deterministic: any known selected probability >=60% gives
The headline comes from current WMO conditions, never future probability.
Matching known conditions use their actual label (for example **Clear.** or
**Rain.**); differing city conditions use **Conditions vary.** Each city retains
its own prominent current-condition label. Missing condition fields remain
**Conditions unknown.** or **Conditions incomplete.** Rain/snow chance remains
secondary information for the explicitly labelled future window, including 0%.
No generic no-precipitation headline is substituted for current conditions.

On admitted `get_weather` working activity, the existing Friday ribbon expands
and uses its thinking motion beside **Checking the forecast**. Completion returns
it to the actual assistant state and gives its existing 320 ms cue after the
result geometry is installed. Idle results settle; other navigation cancels the
weather activity cue/state. The protected ownership and bounded send paths remain
unchanged. This is task feedback; neither static pixels nor this motion establish
sentience or awareness. Native playback uses synthetic responses and the actual
production UI/presence code. Physical touch/voice verification remains deferred.

## Native city artwork revision

The art revision supersedes the text-only mock. Two 224×148 procedural native
illustrations sit beside the readings: Jersey City has a waterfront clock and
modern tower; New York has stepped/spired Manhattan towers. Both use the existing
muted blue/teal, warm light, rounded cards and Friday ribbon direction. These are
stylized city illustrations, not photoreal maps or live views.

The current-variable request now includes `weather_code` and `is_day`. Their
[official definitions](https://open-meteo.com/en/docs) supply current WMO
conditions and daylight/night. Missing or invalid values are independently
normalized to unknown without discarding valid temperature/humidity/wind.
Supported WMO descriptions include 97 (heavy thunderstorm) in the current docs.
The illustration reads only these current fields: clear/partly cloudy,
clouds/rain/snow/fog/lightning and reported day/night select restrained marks.
Thunderstorm alone does not imply rain. Unknown current conditions use neutral
art with no sun/moon/rain marks. The screen labels conditions **Now**; its separate
rain/snow probability retains the explicitly dated next-full-hour interval.

There are no raster downloads, canvas buffers, new graphics dependencies or
continuous artwork animation. Two supporting text labels and two draw callbacks
are added; native peak LVGL heap is 88,464 bytes (816 bytes above the preceding
87,648-byte harness peak). This is host-native evidence, not device RAM/FPS QA.
`preview_weather_art.py` compares actual artwork pixels across future high/low/
unknown chance, current rain, and day/night, and verifies distinct city scenes.
Existing navigation/recovery/protected text audits remain enabled. All supplied
art mock readings and conditions are explicitly synthetic.

## Temperature presentation

Both configured cities display Fahrenheit. The adapter keeps validated
Open-Meteo measurements and its private cache in Celsius, then converts each
published reading with `°F = °C × 9/5 + 32`, rounded to one decimal place.
Tool/model payloads, display labels and spoken temperature wording all use
Fahrenheit. Cache hits do not reconvert previously converted values. Wind
remains in km/h. This requires a backend deployment/restart, with no provider,
credentials, private configuration or firmware change.
