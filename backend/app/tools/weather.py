"""Read-only, opt-in weather adapter with a fixed endpoint and bounded response."""
import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field


def weather_display_name(label: str) -> str:
    """Display-only names for the approved areas; routing retains the exact ZIP."""
    return {"07306": "Jersey City, NJ", "10010": "New York, NY"}.get(label, label)


WEATHER_CONDITIONS = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Rain showers", 81: "Rain showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm", 96: "Storm with hail",
    97: "Heavy thunderstorm", 99: "Storm with hail",
}


def weather_condition(code):
    return WEATHER_CONDITIONS.get(code, "Conditions unknown")


def weather_current_summary(readings):
    """A current-condition headline; future probabilities never enter it."""
    available = [reading for reading in readings if reading.get('ok')]
    if not available:
        return 'Weather unavailable.'
    known = [weather_condition(reading.get('weather_code')) for reading in available
             if type(reading.get('weather_code')) is int and reading['weather_code'] in WEATHER_CONDITIONS]
    if not known:
        return 'Conditions unknown.'
    if len(known) != len(readings):
        return 'Conditions incomplete.'
    return known[0] + '.' if len(set(known)) == 1 else 'Conditions vary.'


def weather_now():
    return datetime.now(timezone.utc)


def next_forecast_window(now):
    now = now.astimezone(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    if start < now:
        start += timedelta(hours=1)
    return start, start + timedelta(hours=1)


def weather_window_label(now, zone="America/New_York"):
    start, end = next_forecast_window(now)
    start, end = start.astimezone(ZoneInfo(zone)), end.astimezone(ZoneInfo(zone))
    def clock(value):
        return value.strftime("%I %p").lstrip("0").lower()
    # Both abbreviations are retained across a DST transition/repeated hour.
    a = clock(start) + (" " + start.tzname() if start.tzname() != end.tzname() else "")
    b = (end.strftime("%a, %b") + f" {end.day} " if start.date() != end.date() else "") + clock(end)
    return f"{start.strftime('%a, %b')} {start.day} · {a}–{b} {end.tzname()}"


def precipitation_window(hourly, units, now, zone="America/New_York"):
    """Hourly probabilities describe (timestamp-1h, timestamp], not the next hour.

    Select the next COMPLETE future UTC hour and convert its endpoints for display.
    Never extrapolate a missing hour or interpret missing/null data as zero.
    """
    try:
        if units.get("precipitation_probability") != "%":
            return None
        times, probabilities = hourly["time"], hourly["precipitation_probability"]
        if not isinstance(times, list) or not isinstance(probabilities, list) or not 1 <= len(times) <= 24 or len(times) != len(probabilities):
            return None
        parsed = []
        for value in times:
            stamp = datetime.fromisoformat(value)
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
            if stamp.minute or stamp.second or stamp.microsecond:
                return None
            parsed.append(stamp)
        if any(b-a != timedelta(hours=1) for a,b in zip(parsed,parsed[1:])):
            return None
        start, end = next_forecast_window(now)
        probability = probabilities[parsed.index(end)]
        if isinstance(probability, bool) or not isinstance(probability, (int,float)) or not 0 <= probability <= 100:
            return None
        return {"probability": probability, "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                "local_window": weather_window_label(now,zone)}
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def weather_rain_summary(readings):
    forecasts = [reading.get("rain_forecast") for reading in readings if reading.get("ok")]
    known = [forecast["probability"] for forecast in forecasts if forecast is not None]
    if not any(reading.get("ok") for reading in readings):
        return "Weather unavailable."
    if known and max(known) >= 60:
        return "An umbrella may help."
    if len(known) != len(readings):
        return "Forecast incomplete."
    if max(known) >= 30:
        return "Wet weather possible."
    return "Low rain / snow chance."


class CurrentWeather(BaseModel):
    weather_code: int | None = None
    is_day: int | None = None
    time: str = Field(max_length=32)
    temperature_2m: float = Field(ge=-100, le=100)
    relative_humidity_2m: int = Field(ge=0, le=100)
    wind_speed_10m: float = Field(ge=0, le=500)


class WeatherUnavailable(Exception):
    pass


class WeatherService:
    def __init__(self, latitude, longitude, transport=None, forecast_zone="UTC"):
        self.forecast_zone = forecast_zone
        self.latitude, self.longitude, self.transport = latitude, longitude, transport
        self._cache = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def current(self):
        async with self._lock:
            if self._cache is not None and time.monotonic() - self._cached_at < 300:
                observed = datetime.fromisoformat(self._cache['time'])
                observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
                if -3600 <= (weather_now() - observed).total_seconds() <= 7200:
                    return self._reading(self._cache)
            # Expired data is never returned as current if a refresh fails.
            self._cache = None
            result = await self._fetch()
            self._cache, self._cached_at = result, time.monotonic()
            return self._reading(result)

    def _reading(self, result):
        reading = dict(result)
        # Keep the validated provider cache in Celsius; publish Fahrenheit
        # consistently to both the model/tool payload and the display. Never
        # mutate cached measurements or reconvert a cache hit.
        reading['temperature_2m'] = round(result['temperature_2m'] * 9 / 5 + 32, 1)
        reading['units'] = {**result['units'], 'temperature': '°F'}
        now = weather_now()
        observed = datetime.fromisoformat(result['time'])
        observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
        start, _ = next_forecast_window(now)
        # A long-lived view must clear data by cache expiry, observation expiry,
        # or the next-full-hour label changing, whichever happens first.
        remaining = min(300 - (time.monotonic() - self._cached_at),
                        7200 - (now - observed).total_seconds(),
                        (start - now).total_seconds())
        reading['_fresh_for_ms'] = max(1, min(300000, int(remaining * 1000)))
        reading['_fresh_until'] = (now + timedelta(milliseconds=reading['_fresh_for_ms'])).isoformat()
        forecast = reading.pop("forecast_hourly", {})
        reading["rain_forecast"] = precipitation_window(forecast.get("hourly", {}), forecast.get("units", {}), now, self.forecast_zone)
        return reading

    async def _fetch(self):
        try:
            async with httpx.AsyncClient(timeout=8, transport=self.transport, follow_redirects=False) as client:
                async with client.stream('GET', 'https://api.open-meteo.com/v1/forecast', params={
                    'latitude': self.latitude, 'longitude': self.longitude,
                    'current': 'temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code,is_day',
                    'hourly': 'precipitation_probability', 'forecast_hours': 6,
                    'timezone': 'UTC', 'temperature_unit': 'celsius', 'wind_speed_unit': 'kmh',
                }) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 65536:
                            raise ValueError('Weather response too large')
            data = json.loads(content)
            units = data['current_units']
            if not isinstance(units, dict):
                raise ValueError('Invalid weather units')
            if units.get('temperature_2m') != '°C' or units.get('wind_speed_10m') != 'km/h' or units.get('relative_humidity_2m') != '%':
                raise ValueError('Unexpected weather units')
            current_values = dict(data['current'])
            code, day = current_values.get('weather_code'), current_values.get('is_day')
            current_values['weather_code'] = code if type(code) is int and code in WEATHER_CONDITIONS else None
            current_values['is_day'] = day if type(day) is int and day in (0, 1) else None
            current = CurrentWeather.model_validate(current_values)
            observed = datetime.fromisoformat(current.time)
            observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
            age = (weather_now() - observed).total_seconds()
            if age < -3600 or age > 7200:
                raise ValueError('Weather observation is stale')
            return {'forecast_hourly': {'hourly': data.get('hourly', {}), 'units': data.get('hourly_units', {})},
                    'source': 'Open-Meteo', 'units': {'temperature': '°C', 'wind': 'km/h'}, **current.model_dump()}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise WeatherUnavailable('Weather is unavailable; try again later.') from error


class WeatherLocations:
    """Independent bounded caches and failures; no runtime geocoding."""
    def __init__(self, locations, transport=None):
        self.services = {location.label: WeatherService(location.latitude, location.longitude, transport,
                              "America/New_York" if location.label in {"07306", "10010"} else "UTC")
                         for location in locations}

    async def current(self, location="both"):
        if not isinstance(location, str) or location not in {"both", *self.services}:
            raise WeatherUnavailable("Choose a configured ZIP location or both.")
        selected = list(self.services) if location == "both" else [location]
        async def read(label):
            try:
                return {"location": label, "ok": True, **await self.services[label].current()}
            except WeatherUnavailable:
                return {"location": label, "ok": False, "error": "Weather unavailable. Try again shortly."}
        return await asyncio.gather(*(read(label) for label in selected))
