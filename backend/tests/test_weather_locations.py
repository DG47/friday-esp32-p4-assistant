import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError
from app.agent import FridayAgent
from app.config import Settings, WeatherLocation
from app.db import ReminderStore
from app.tools.weather import WeatherLocations
from tests.test_weather import payload

LOCATIONS = [dict(label='07306', latitude=40.736294, longitude=-74.071128),
             dict(label='10010', latitude=40.739045, longitude=-73.982616)]


class WeatherLocationTests(unittest.IsolatedAsyncioTestCase):
    def agent(self, directory, response):
        settings = Settings(_env_file=None, assistant_provider='mock', weather_provider='open-meteo', weather_locations=LOCATIONS)
        agent = FridayAgent(settings, ReminderStore(Path(directory)/'test.db'))
        agent.weather = WeatherLocations(settings.weather_locations, httpx.MockTransport(response))
        return agent

    async def test_routes_default_either_both_and_preserves_zip_zero(self):
        for query, labels in [('show weather', ['07306', '10010']), ('weather in 07306', ['07306']),
                              ('show weather for 10010', ['10010']), ('weather for both', ['07306','10010']),
                              ('weather 10010 and 07306', ['07306','10010'])]:
            seen=[]
            with TemporaryDirectory() as directory:
                agent=self.agent(directory, lambda req: (seen.append(req),httpx.Response(200,json=payload()))[1])
                result=await agent.handle('test',query)
                self.assertEqual([item['time'] for item in result.surface.items if item['time']],[{'07306':'Jersey City, NJ','10010':'New York, NY'}[label] for label in labels])
                self.assertEqual(len(seen),len(labels))
                self.assertEqual(result.provider,'local')
                self.assertIn('["07306", "10010"]',agent._instructions())
                self.assertNotIn('UTC',result.surface.model_dump_json())
                self.assertNotIn('Open-Meteo',result.surface.model_dump_json())
                self.assertNotIn('home',result.text.lower())
                self.assertNotIn('work',result.text.lower())

    async def test_independent_failure_cache_and_recovery(self):
        seen=[]; fail=True
        def response(req):
            seen.append(req.url.params['latitude'])
            return httpx.Response(503) if fail and req.url.params['latitude']=='40.739045' else httpx.Response(200,json=payload())
        service=WeatherLocations([WeatherLocation(**item) for item in LOCATIONS],httpx.MockTransport(response))
        readings=await service.current()
        self.assertEqual([r['ok'] for r in readings],[True,False])
        fail=False
        readings=await service.current()
        self.assertEqual([r['ok'] for r in readings],[True,True])
        self.assertEqual(seen,['40.736294','40.739045','40.739045'])
        # Expired cache must refresh and a failed refresh must not present old data.
        service.services['07306']._cached_at-=301
        service.services['07306'].transport=httpx.MockTransport(lambda req:httpx.Response(503))
        readings=await service.current()
        self.assertEqual([r['ok'] for r in readings],[False,True])

    async def test_partial_and_total_failure_remain_location_specific(self):
        for fail_all in (False,True):
            with TemporaryDirectory() as directory:
                agent=self.agent(directory,lambda req:httpx.Response(503) if fail_all or req.url.params['latitude']=='40.739045' else httpx.Response(200,json=payload()))
                outcome=await agent._execute_read_tool('get_weather',{},'test','req',0,for_model=True)
                self.assertEqual(outcome.success,not fail_all)
                self.assertEqual([r['ok'] for r in json.loads(outcome.content)['locations']],[not fail_all,False])
                self.assertEqual([r['location'] for r in json.loads(outcome.content)['locations']],['07306','10010'])
                self.assertNotIn('latitude',outcome.content)
                self.assertNotIn('longitude',outcome.content)
                self.assertIn('New York, NY: weather unavailable.',outcome.surface.subtitle)

    async def test_concurrent_reads_share_each_location_cache(self):
        import asyncio
        seen=[]
        service=WeatherLocations([WeatherLocation(**item) for item in LOCATIONS],
            httpx.MockTransport(lambda req:(seen.append(req),httpx.Response(200,json=payload()))[1]))
        readings=await asyncio.gather(service.current(),service.current())
        self.assertEqual(len(seen),2)
        self.assertTrue(all(reading['ok'] for group in readings for reading in group))

    async def test_unknown_location_cannot_send_network_request(self):
        with TemporaryDirectory() as directory:
            agent=self.agent(directory,lambda req:self.fail('Unexpected provider request'))
            for query in ('weather 90210','weather 07306 and 90210'):
                result=await agent.handle('test',query)
                self.assertIn('Choose a configured',result.text)
            for value in (123,None,[],{},'90210'):
                outcome=await agent._execute_read_tool('get_weather',{'location':value},'test','req',0)
                self.assertFalse(outcome.success)

    async def test_observation_expiry_overrides_cache_ttl(self):
        service=WeatherLocations([WeatherLocation(**LOCATIONS[0])],httpx.MockTransport(lambda req:httpx.Response(200,json=payload())))
        await service.current()
        cache=service.services['07306']
        cache._cache['time']=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()
        cache.transport=httpx.MockTransport(lambda req:httpx.Response(503))
        self.assertFalse((await service.current())[0]['ok'])

    def test_config_rejects_ambiguous_duplicate_and_excess_locations(self):
        for fields in ({'weather_locations':[LOCATIONS[0],LOCATIONS[0]]},
                       {'weather_locations':LOCATIONS,'weather_latitude':1,'weather_longitude':2},
                       {'weather_locations':LOCATIONS+[dict(label='third',latitude=1,longitude=2)]},
                       {'weather_locations':[dict(label='07306',latitude=999,longitude=2)]},
                       {'weather_locations':[dict(label='both',latitude=1,longitude=2)]}):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None,weather_provider='open-meteo',**fields)
        settings=Settings(_env_file=None,weather_provider='open-meteo',weather_latitude=1,weather_longitude=2)
        self.assertEqual(settings.weather_locations,[])

    def test_json_environment_config(self):
        with patch.dict('os.environ',{'WEATHER_LOCATIONS':json.dumps(LOCATIONS),'WEATHER_PROVIDER':'open-meteo'}):
            settings=Settings(_env_file=None)
            self.assertEqual([item.label for item in settings.weather_locations],['07306','10010'])
