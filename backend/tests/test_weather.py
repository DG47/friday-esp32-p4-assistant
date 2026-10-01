from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import httpx
from pydantic import ValidationError

from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore
from app.tools.weather import WeatherService, WeatherUnavailable


def payload():
    return {'current_units': {'temperature_2m': '°C', 'relative_humidity_2m': '%', 'wind_speed_10m': 'km/h'}, 'current': {'time': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='minutes'), 'temperature_2m': 21.5, 'relative_humidity_2m': 65, 'wind_speed_10m': 8.5}}


class WeatherTests(unittest.IsolatedAsyncioTestCase):
    async def test_contract_and_real_scene_with_mock_transport(self):
        seen = []
        def response(request):
            seen.append(request)
            return httpx.Response(200, json=payload())
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(_env_file=None, assistant_provider='mock')
            agent = FridayAgent(settings, ReminderStore(Path(directory) / 'test.db'))
            agent.weather = WeatherService(1, 2, httpx.MockTransport(response))
            result = await agent.handle('a', 'show weather')
            self.assertEqual(result.surface.kind, 'metric')
            self.assertEqual(result.surface.title, '21.5 °C')
            self.assertIn('Open-Meteo', result.surface.items[-1]['label'])
            self.assertEqual(seen[0].url.host, 'api.open-meteo.com')
            self.assertEqual(seen[0].url.params['latitude'], '1')
            self.assertEqual(seen[0].url.params['timezone'], 'UTC')

    async def test_disabled_weather_is_honest_and_does_not_call_network(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = FridayAgent(Settings(_env_file=None, assistant_provider='mock'), ReminderStore(Path(directory) / 'test.db'))
            result = await agent.handle('a', 'show weather')
            self.assertEqual(result.surface.title, 'Weather unavailable')
            self.assertIn('not connected', result.text)

    async def test_failures_and_stale_or_invalid_data_are_unavailable(self):
        for item in (None, {'error': True}, {**payload(), 'current': {**payload()['current'], 'time': '2000-01-01T00:00'}}, {**payload(), 'current_units': {}}, {**payload(), 'current': {**payload()['current'], 'temperature_2m': 999}}):
            service = WeatherService(1, 2, httpx.MockTransport(lambda request: httpx.Response(503) if item is None else httpx.Response(200, json=item)))
            with self.assertRaises(WeatherUnavailable):
                await service.current()

    def test_opt_in_requires_valid_explicit_coordinates(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, weather_provider='open-meteo')
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, weather_provider='open-meteo', weather_latitude=200, weather_longitude=1)
