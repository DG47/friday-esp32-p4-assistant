from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import httpx
from app.config import WeatherLocation
from app.tools.weather import (WeatherService, WeatherLocations, WeatherUnavailable,
    precipitation_window, weather_window_label, weather_rain_summary)

NOW=datetime(2026,10,7,17,23,tzinfo=timezone.utc)
UNITS={'precipitation_probability':'%'}
def hourly(now=NOW,values=None):
    start=now.replace(minute=0,second=0,microsecond=0)
    return {'time':[(start+timedelta(hours=i)).replace(tzinfo=None).isoformat(timespec='minutes') for i in range(6)],
            'precipitation_probability':values or [1,10,70,40,20,0]}
def response_data(now=NOW,values=None):
    return {'current_units':{'temperature_2m':'°C','relative_humidity_2m':'%','wind_speed_10m':'km/h'},
            'current':{'time':now.isoformat(),'temperature_2m':21.5,'relative_humidity_2m':65,'wind_speed_10m':8.5},
            'hourly_units':UNITS,'hourly':hourly(now,values)}

class ForecastTests(unittest.IsolatedAsyncioTestCase):
    def test_preceding_hour_endpoint_and_explicit_complete_future_window(self):
        result=precipitation_window(hourly(),UNITS,NOW,'America/New_York')
        self.assertEqual(result['probability'],70) # 19:00 describes 18:00-19:00, not 19:00-20:00
        self.assertEqual(result['starts_at'],'2026-10-07T18:00:00+00:00')
        self.assertEqual(result['ends_at'],'2026-10-07T19:00:00+00:00')
        self.assertEqual(result['local_window'],'Wed, Oct 7 · 2 pm–3 pm EDT')
    def test_exact_hour_boundary_includes_the_complete_hour_starting_now(self):
        self.assertEqual(precipitation_window(hourly(),UNITS,NOW.replace(minute=0))['probability'],10)
    def test_probability_is_not_aggregated_or_replaced_by_current_precipitation(self):
        data=hourly();data['precipitation_probability'][2]=0;data['precipitation']= [99]*6
        self.assertEqual(precipitation_window(data,UNITS,NOW)['probability'],0)
    def test_missing_null_bad_bounds_units_lengths_order_and_hour_alignment_are_unknown(self):
        for probability in [None,-1,101,True,'70',float('nan')]:
            data=hourly();data['precipitation_probability'][2]=probability
            self.assertIsNone(precipitation_window(data,UNITS,NOW))
        for data,units in [({},UNITS),(hourly(),{}),(hourly(),{'precipitation_probability':'mm'}),
                           ({**hourly(),'time':hourly()['time'][:-1]},UNITS),
                           ({**hourly(),'time':list(reversed(hourly()['time']))},UNITS),
                           ({**hourly(),'time':[value.replace(':00',':30') for value in hourly()['time']]},UNITS)]:
            self.assertIsNone(precipitation_window(data,units,NOW))
    def test_missing_window_and_expired_forecast_do_not_imply_zero(self):
        self.assertIsNone(precipitation_window(hourly(),UNITS,NOW+timedelta(days=1)))
        data=hourly();data['time'].pop(2);data['precipitation_probability'].pop(2)
        self.assertIsNone(precipitation_window(data,UNITS,NOW))
    def test_dst_fall_back_uses_distinct_real_hour_endpoints(self):
        now=datetime(2026,11,1,4,23,tzinfo=timezone.utc)
        result=precipitation_window(hourly(now),UNITS,now,'America/New_York')
        self.assertEqual(result['local_window'],'Sun, Nov 1 · 1 am EDT–1 am EST')
        self.assertEqual(datetime.fromisoformat(result['ends_at'])-datetime.fromisoformat(result['starts_at']),timedelta(hours=1))
    def test_dst_spring_forward_and_midnight_keep_local_time_truthful(self):
        now=datetime(2026,3,8,5,23,tzinfo=timezone.utc)
        self.assertEqual(weather_window_label(now),'Sun, Mar 8 · 1 am EST–3 am EDT')
        now=datetime(2026,10,8,2,23,tzinfo=timezone.utc)
        self.assertIn('Wed, Oct 7',weather_window_label(now))
        self.assertIn('Thu, Oct 8',weather_window_label(now))
    def test_summary_thresholds_and_missing_data_do_not_claim_dry(self):
        def reading(value):return {'ok':True,'rain_forecast':None if value is None else {'probability':value}}
        for value,text in [(0,'Low rain / snow chance.'),(29,'Low rain / snow chance.'),(30,'Wet weather possible.'),
                           (59,'Wet weather possible.'),(60,'An umbrella may help.'),(100,'An umbrella may help.')]:
            self.assertEqual(weather_rain_summary([reading(value)]),text)
        self.assertEqual(weather_rain_summary([reading(0),reading(None)]),'Forecast incomplete.')
        self.assertEqual(weather_rain_summary([{'ok':False}]),'Weather unavailable.')
        self.assertEqual(weather_rain_summary([reading(70),reading(None)]),'An umbrella may help.')
    async def test_request_contract_same_endpoint_and_cache_reselects_after_hour_boundary(self):
        seen=[]
        service=WeatherService(40.736294,-74.071128,httpx.MockTransport(lambda req:(seen.append(req),httpx.Response(200,json=response_data()))[1]),'America/New_York')
        with patch('app.tools.weather.weather_now',return_value=NOW.replace(minute=58)):first=await service.current()
        with patch('app.tools.weather.weather_now',return_value=NOW.replace(hour=18,minute=1)):second=await service.current()
        self.assertEqual(first['rain_forecast']['probability'],70)
        self.assertEqual(second['rain_forecast']['probability'],40)
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0].url.host,'api.open-meteo.com')
        self.assertEqual(seen[0].url.params['hourly'],'precipitation_probability')
        self.assertEqual(seen[0].url.params['forecast_hours'],'6')
        self.assertEqual(seen[0].url.params['timezone'],'UTC')
        self.assertNotIn('forecast_hourly',first)
    async def test_missing_forecast_preserves_current_and_retry_never_reuses_expired_data(self):
        data=response_data();data.pop('hourly')
        service=WeatherService(1,2,httpx.MockTransport(lambda req:httpx.Response(200,json=data)))
        with patch('app.tools.weather.weather_now',return_value=NOW):
            reading=await service.current();self.assertIsNone(reading['rain_forecast']);self.assertEqual(reading['temperature_2m'],21.5)
            service._cached_at-=301;service.transport=httpx.MockTransport(lambda req:httpx.Response(503))
            with self.assertRaises(WeatherUnavailable):await service.current()
    async def test_independent_unknown_forecast_and_area_failure_recovery(self):
        fail=False
        def respond(req):
            data=response_data()
            if req.url.params['latitude']=='40.739045':
                if fail:return httpx.Response(503)
                data['hourly']['precipitation_probability'][2]=None
            return httpx.Response(200,json=data)
        locations=[WeatherLocation(label='07306',latitude=40.736294,longitude=-74.071128),WeatherLocation(label='10010',latitude=40.739045,longitude=-73.982616)]
        service=WeatherLocations(locations,httpx.MockTransport(respond))
        with patch('app.tools.weather.weather_now',return_value=NOW):
            readings=await service.current();self.assertEqual(readings[0]['rain_forecast']['probability'],70);self.assertIsNone(readings[1]['rain_forecast'])
            service.services['10010']._cached_at-=301;fail=True
            readings=await service.current();self.assertTrue(readings[0]['ok']);self.assertFalse(readings[1]['ok'])
            fail=False;readings=await service.current();self.assertTrue(readings[1]['ok']);self.assertIsNone(readings[1]['rain_forecast'])

    async def test_publication_crossing_hour_boundary_marks_the_old_window_unknown(self):
        from app.agent import FridayAgent
        from app.config import Settings
        from app.db import ReminderStore
        from unittest.mock import AsyncMock
        with TemporaryDirectory() as directory:
            settings=Settings(_env_file=None,assistant_provider='mock',weather_provider='open-meteo',weather_locations=[{'label':'07306','latitude':40.736294,'longitude':-74.071128}])
            agent=FridayAgent(settings,ReminderStore(Path(directory)/'test.db'))
            reading={'location':'07306','ok':True,'temperature_2m':21.5,'wind_speed_10m':8.5,'relative_humidity_2m':65,
                     'rain_forecast':precipitation_window(hourly(),UNITS,NOW)}
            with patch.object(agent.weather,'current',AsyncMock(return_value=[reading])),patch('app.agent.weather_now',return_value=NOW.replace(hour=18,minute=1)):
                outcome=await agent._execute_read_tool('get_weather',{},'test','req',0)
            self.assertIn('3 pm–4 pm EDT',outcome.surface.subtitle)
            self.assertEqual(outcome.surface.items[1]['label'],'Rain / snow chance unknown')
            self.assertIn('Forecast incomplete',outcome.surface.subtitle)

    async def test_current_conditions_are_independent_of_future_probability(self):
        from app.tools.weather import weather_condition
        data=response_data();data['current'].update(weather_code=0,is_day=1)
        with patch('app.tools.weather.weather_now',return_value=NOW):
            service=WeatherService(1,2,httpx.MockTransport(lambda req:httpx.Response(200,json=data)))
            reading=await service.current()
        self.assertEqual(reading['rain_forecast']['probability'],70)
        self.assertEqual(weather_condition(reading['weather_code']),'Clear')
        self.assertEqual(reading['is_day'],1)
    async def test_invalid_or_missing_current_conditions_remain_neutral_without_losing_temperature(self):
        from app.tools.weather import weather_condition
        for code,day in [(None,None),(999,1),(4,2),(True,False),('rain','day')]:
            data=response_data();data['current'].update(weather_code=code,is_day=day)
            with patch('app.tools.weather.weather_now',return_value=NOW):
                reading=await WeatherService(1,2,httpx.MockTransport(lambda req:httpx.Response(200,json=data))).current()
            self.assertEqual(reading['temperature_2m'],21.5)
            self.assertEqual(weather_condition(reading['weather_code']),'Conditions unknown')
            if type(day) is not int or day not in (0,1):self.assertIsNone(reading['is_day'])
