"""Provider-free condition, freshness, persistence and payload regression tests."""
import json
from pathlib import Path
import unittest
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
import httpx

from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore
from app.device_hub import weather_packet
from app.tools.weather import WeatherLocations, WeatherService, weather_current_summary
from tests.test_weather_locations import LOCATIONS
from tests.test_weather_forecast import NOW, response_data


class WeatherPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def test_delayed_and_replayed_weather_cannot_regain_freshness(self):
        packet = {'type': 'agent.result', 'surface': {'kind': 'metric', 'title': 'Weather',
                  'weather_valid_ms': 300000, 'deadline_at': (NOW + timedelta(seconds=60)).isoformat()}}
        self.assertEqual(weather_packet(packet, NOW)['surface']['weather_valid_ms'], 60000)
        self.assertEqual(weather_packet(packet, NOW + timedelta(seconds=59))['surface']['weather_valid_ms'], 1000)
        self.assertEqual(weather_packet(packet, NOW + timedelta(hours=1))['surface']['weather_valid_ms'], 1)
        self.assertEqual(packet['surface']['weather_valid_ms'], 300000)
        for value in (None, 'bad timestamp', NOW.replace(tzinfo=None).isoformat()):
            invalid = {**packet, 'surface': {**packet['surface'], 'deadline_at': value}}
            self.assertEqual(weather_packet(invalid, NOW)['surface']['weather_valid_ms'], 1)
        other = {'type': 'surface', 'surface': {'kind': 'message', 'title': 'Confirmation required'}}
        self.assertIs(weather_packet(other, NOW), other)

    def test_current_summary_never_uses_future_probability(self):
        for probability in (0, 10, 70, 100, None):
            def reading(code):
                return {'ok': True, 'weather_code': code,
                        'rain_forecast': None if probability is None else {'probability': probability}}
            self.assertEqual(weather_current_summary([reading(0), reading(0)]), 'Clear.')
            self.assertEqual(weather_current_summary([reading(63), reading(63)]), 'Rain.')
            self.assertEqual(weather_current_summary([reading(0), reading(63)]), 'Conditions vary.')
            self.assertEqual(weather_current_summary([reading(None), reading(None)]), 'Conditions unknown.')
            self.assertEqual(weather_current_summary([reading(0), reading(None)]), 'Conditions incomplete.')
            self.assertEqual(weather_current_summary([reading(0), {'ok': False}]), 'Conditions incomplete.')
        self.assertEqual(weather_current_summary([{'ok': False}, {'ok': False}]), 'Weather unavailable.')
        self.assertEqual(weather_current_summary([{'ok': True, 'weather_code': True}]), 'Conditions unknown.')

    async def test_data_lease_is_bounded_by_cache_window_and_observation(self):
        data = response_data()
        service = WeatherService(1, 2, httpx.MockTransport(lambda request: httpx.Response(200, json=data)))
        with patch('app.tools.weather.weather_now', return_value=NOW):
            first = await service.current()
            self.assertGreater(first['_fresh_for_ms'], 299000)
            self.assertLessEqual(first['_fresh_for_ms'], 300000)
            service._cached_at -= 290
            cached = await service.current()
            self.assertGreater(cached['_fresh_for_ms'], 9000)
            self.assertLessEqual(cached['_fresh_for_ms'], 10000)
        with patch('app.tools.weather.weather_now', return_value=NOW.replace(minute=59, second=58)):
            boundary = await service.current()
            self.assertEqual(boundary['_fresh_for_ms'], 2000)
        with patch('app.tools.weather.weather_now', return_value=NOW.replace(hour=18, minute=0)):
            exact = await service.current()
            self.assertGreater(exact['_fresh_for_ms'], 9000)
            self.assertLessEqual(exact['_fresh_for_ms'], 10000)
        data['current']['time'] = (NOW - timedelta(seconds=7199)).isoformat()
        service = WeatherService(1, 2, httpx.MockTransport(lambda request: httpx.Response(200, json=data)))
        with patch('app.tools.weather.weather_now', return_value=NOW):
            old_observation = await service.current()
            self.assertEqual(old_observation['_fresh_for_ms'], 1000)

    async def test_fresh_exact_hour_before_and_after_preserve_window_and_expiry(self):
        boundary = NOW.replace(hour=18, minute=0)
        for instant, end_hour, probability in [(boundary - timedelta(microseconds=1), 19, 70),
                (boundary, 19, 70), (boundary + timedelta(microseconds=1), 20, 40)]:
            data = response_data()
            data['current']['time'] = instant.isoformat()
            service = WeatherService(1, 2, httpx.MockTransport(lambda request: httpx.Response(200, json=data)))
            with patch('app.tools.weather.weather_now', return_value=instant):
                reading = await service.current()
            self.assertEqual(reading['rain_forecast']['ends_at'], boundary.replace(hour=end_hour).isoformat())
            self.assertEqual(reading['rain_forecast']['probability'], probability)
            if instant < boundary:
                self.assertEqual(reading['_fresh_for_ms'], 1)
            else:
                self.assertGreater(reading['_fresh_for_ms'], 299000)
                self.assertLessEqual(reading['_fresh_for_ms'], 300000)
                deadline = reading['_fresh_until']
                packet = {'type': 'surface', 'surface': {'kind': 'metric', 'title': 'Weather',
                    'weather_valid_ms': reading['_fresh_for_ms'], 'deadline_at': deadline}}
                self.assertGreater(weather_packet(packet, instant)['surface']['weather_valid_ms'], 299000)
                self.assertEqual(weather_packet(packet, instant + timedelta(minutes=5))['surface']['weather_valid_ms'], 1)

    async def test_persistent_surface_zero_unknown_mixed_conditions_and_failed_refresh(self):
        for codes, probabilities, title in [((0, 0), (0, 0), 'Clear.'),
                ((0, 63), (0, 70), 'Conditions vary.'),
                ((63, 63), (0, 0), 'Rain.'),
                ((None, None), (0, 0), 'Conditions unknown.'),
                ((0, 0), (0, None), 'Clear.')]:
            failed = False
            def response(request):
                if failed:
                    return httpx.Response(503)
                index = 0 if request.url.params['latitude'] == '40.736294' else 1
                data = response_data()
                data['current'].update(weather_code=codes[index], is_day=1)
                data['hourly']['precipitation_probability'] = [probabilities[index]] * 6
                return httpx.Response(200, json=data)
            with TemporaryDirectory() as directory:
                settings = Settings(_env_file=None, assistant_provider='mock',
                    weather_provider='open-meteo', weather_locations=LOCATIONS)
                agent = FridayAgent(settings, ReminderStore(Path(directory) / 'test.db'))
                agent.weather = WeatherLocations(settings.weather_locations, httpx.MockTransport(response))
                with patch('app.tools.weather.weather_now', return_value=NOW), patch('app.agent.weather_now', return_value=NOW):
                    outcome = await agent._execute_read_tool('get_weather', {}, 'test', 'persistent', 0)
                    self.assertEqual(outcome.surface.subtitle.splitlines()[0], title)
                    self.assertIsNone(outcome.surface.expires_ms)
                    self.assertGreater(outcome.surface.weather_valid_ms, 299000)
                    self.assertLessEqual(outcome.surface.weather_valid_ms, 300000)
                    self.assertEqual([a.id for a in outcome.surface.actions], ['dismiss'])
                    self.assertNotIn('_fresh_for_ms', outcome.content)
                    self.assertNotIn('_fresh_until', outcome.content)
                    self.assertIsNotNone(outcome.surface.deadline_at)
                    self.assertNotIn('No precipitation', outcome.surface.subtitle)
                    readings = json.loads(outcome.content)['locations']
                    self.assertEqual([x['temperature_2m'] for x in readings], [70.7, 70.7])
                    self.assertTrue(all(x['units']['temperature'] == '°F' for x in readings))
                    self.assertEqual(outcome.surface.items[1]['label'], 'Rain / snow chance 0%')
                    if probabilities[1] is None:
                        self.assertEqual(outcome.surface.items[3]['label'], 'Rain / snow chance unknown')
                    failed = True
                    for service in agent.weather.services.values():
                        service._cached_at -= 301
                    unavailable = await agent._execute_read_tool('get_weather', {}, 'test', 'failed-refresh', 0)
                    self.assertEqual(unavailable.surface.subtitle.splitlines()[0], 'Weather unavailable.')
                    self.assertIsNone(unavailable.surface.expires_ms)
                    self.assertIsNone(unavailable.surface.weather_valid_ms)
                    self.assertNotIn('70.7', unavailable.surface.model_dump_json())
                    self.assertTrue(all(x['label'] == 'Rain / snow chance unknown' for x in unavailable.surface.items[1::2]))
