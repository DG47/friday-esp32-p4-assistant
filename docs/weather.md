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
Do not silently replace a ZIP label with a city or infer home/work.

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
coordinates. The screen includes only ZIPs and useful measurements/failure text,
without provider names, timestamps or operational diagnostics. Weather data:
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
