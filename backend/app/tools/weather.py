"""Read-only, opt-in weather adapter with a fixed endpoint and bounded response."""
import asyncio
import json
import time
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field


def weather_display_name(label: str) -> str:
    """Display-only names for the approved areas; routing retains the exact ZIP."""
    return {"07306": "Jersey City, NJ", "10010": "New York, NY"}.get(label, label)


class CurrentWeather(BaseModel):
    time: str = Field(max_length=32)
    temperature_2m: float = Field(ge=-100, le=100)
    relative_humidity_2m: int = Field(ge=0, le=100)
    wind_speed_10m: float = Field(ge=0, le=500)


class WeatherUnavailable(Exception):
    pass


class WeatherService:
    def __init__(self, latitude, longitude, transport=None):
        self.latitude, self.longitude, self.transport = latitude, longitude, transport
        self._cache = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def current(self):
        async with self._lock:
            if self._cache is not None and time.monotonic() - self._cached_at < 300:
                observed = datetime.fromisoformat(self._cache['time'])
                observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
                if -3600 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 7200:
                    return dict(self._cache)
            # Expired data is never returned as current if a refresh fails.
            self._cache = None
            result = await self._fetch()
            self._cache, self._cached_at = result, time.monotonic()
            return dict(result)

    async def _fetch(self):
        try:
            async with httpx.AsyncClient(timeout=8, transport=self.transport, follow_redirects=False) as client:
                async with client.stream('GET', 'https://api.open-meteo.com/v1/forecast', params={
                    'latitude': self.latitude, 'longitude': self.longitude,
                    'current': 'temperature_2m,relative_humidity_2m,wind_speed_10m',
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
            current = CurrentWeather.model_validate(data['current'])
            observed = datetime.fromisoformat(current.time)
            observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
            age = (datetime.now(timezone.utc) - observed).total_seconds()
            if age < -3600 or age > 7200:
                raise ValueError('Weather observation is stale')
            return {'source': 'Open-Meteo', 'units': {'temperature': '°C', 'wind': 'km/h'}, **current.model_dump()}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise WeatherUnavailable('Weather is unavailable; try again later.') from error


class WeatherLocations:
    """Independent bounded caches and failures; no runtime geocoding."""
    def __init__(self, locations, transport=None):
        self.services = {location.label: WeatherService(location.latitude, location.longitude, transport)
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
