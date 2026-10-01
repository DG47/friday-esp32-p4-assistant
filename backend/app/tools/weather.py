"""Read-only, opt-in weather adapter with a fixed endpoint and bounded response."""
import json
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field


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

    async def current(self):
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
