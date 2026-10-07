"""Actual backend weather surfaces with synthetic provider responses only."""
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root/'backend'))
from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore
from app.tools.weather import WeatherLocations

LOCATIONS = [dict(label='07306', latitude=40.736294, longitude=-74.071128),
             dict(label='10010', latitude=40.739045, longitude=-73.982616)]

async def main():
    out = root/'outputs/native-ui'
    out.mkdir(parents=True, exist_ok=True)
    for name, location, failures in [('weather-both','both',set()), ('weather-07306','07306',set()),
                                     ('weather-10010','10010',set()), ('weather-partial','both',{'10010'}),
                                     ('weather-unavailable','both',{'07306','10010'}), ('weather-extremes','both',set()), ('weather-forecast-unknown','both',set()), ('weather-dst','both',set()), ('weather-low','both',set())]:
        now=datetime(2026,11,1,4,23,tzinfo=timezone.utc) if name=="weather-dst" else datetime(2026,10,7,17,23,tzinfo=timezone.utc)
        def response(req):
            zip = '07306' if req.url.params['latitude']=='40.736294' else '10010'
            if zip in failures:
                return httpx.Response(503)
            return httpx.Response(200,json={'current_units':{'temperature_2m':'°C','relative_humidity_2m':'%','wind_speed_10m':'km/h'},
                'hourly_units':{'precipitation_probability':'%'},
                'hourly':{'time':[(now.replace(minute=0)+timedelta(hours=i)).replace(tzinfo=None).isoformat(timespec='minutes') for i in range(6)],
                          'precipitation_probability':[None if name=='weather-forecast-unknown' and zip=='10010' else 10 if name=='weather-low' else 70 if zip=='07306' else 40]*6},
                'current':{'time':now.isoformat(),
                           'temperature_2m':(-100 if zip=='07306' else 100) if name=='weather-extremes' else (21.5 if zip=='07306' else 22),
                           'relative_humidity_2m':100 if name=='weather-extremes' else 65,'wind_speed_10m':500 if name=='weather-extremes' else 8.5}})
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings(_env_file=None,assistant_provider='mock',weather_provider='open-meteo',weather_locations=LOCATIONS)
            agent=FridayAgent(settings,ReminderStore(Path(directory)/'test.db'))
            agent.weather=WeatherLocations(settings.weather_locations,httpx.MockTransport(response))
            with patch('app.tools.weather.weather_now',return_value=now),patch('app.agent.weather_now',return_value=now):
                outcome=await agent._execute_read_tool('get_weather',{'location':location},'synthetic','fixture',0)
            (out/(name+'.json')).write_text(outcome.surface.model_dump_json())

if __name__=='__main__':
    asyncio.run(main())
