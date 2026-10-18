"""Adversarial provider prose after actual synthetic Calendar/Weather reads."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.agent import FridayAgent
from app.db import ReminderStore
from app.providers.base import ProviderTurn
from test_conversation_scenario import settings_for, synthetic_calendar, SyntheticWeather, ScriptedProvider, call, NOW


class ObservedReadProse(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = settings_for(Path(self.temp.name)/'synthetic.db', assistant_timezone='America/New_York')
        self.agent = FridayAgent(self.settings, ReminderStore(self.settings.assistant_database_path))
        synthetic_calendar(self.agent)
        self.agent.weather = SyntheticWeather(self.settings.weather_locations)
    async def asyncTearDown(self):
        await self.agent.calendar.close()
        self.temp.cleanup()
    async def ask(self, turns):
        provider = ScriptedProvider(turns)
        with patch.object(self.agent.providers, 'select', return_value=provider), patch('app.agent.weather_now', lambda: NOW):
            reply = await self.agent.handle('synthetic-device', 'Read the plan and conditions for me', request_id='synthetic-read')
        return reply, provider
    async def test_wrong_timezone_and_units_cannot_replace_observed_read_facts(self):
        reply, provider = await self.ask([call('get_calendar', {'range':'next'}), call('get_weather', {'location':'10010'}), ProviderTurn(text='Dinner is at 1:55 PM EDT; it is 70.7 Celsius. I moved it for you.')])
        self.assertIn('18:30 EDT (UTC-04:00)', reply.text)
        self.assertIn('90 minutes', reply.text)
        self.assertIn('degrees Fahrenheit', reply.text)
        self.assertIn('Sample calendar', reply.text)
        self.assertNotIn('Celsius', reply.text)
        self.assertNotIn('moved it', reply.text)
        self.assertEqual(self.agent.store.conversation('synthetic-device')[-1]['content'], reply.text)
    async def test_disabled_sharing_cannot_be_presented_as_empty_calendar(self):
        self.settings.calendar_provider = 'google'
        self.settings.calendar_model_context = False
        reply, _ = await self.ask([call('get_calendar', {'range':'next'}), ProviderTurn(text='Your live calendar is empty.')])
        self.assertIn('not been authorized', reply.text)
        self.assertNotIn('empty', reply.text)
        self.assertNotIn('Dinner', reply.text)
    async def test_failed_weather_cannot_be_presented_as_observed_success(self):
        self.agent.weather = None
        reply, _ = await self.ask([call('get_weather'), ProviderTurn(text='It is sunny and 22 Celsius right now.')])
        self.assertIn('not connected', reply.text)
        self.assertNotIn('22', reply.text)

    async def test_exact_temporal_trace_repeats_requested_zip_without_rain_free_extrapolation(self):
        from app.agent import ToolOutcome
        from app.models import Surface
        payload=json.loads((Path(__file__).parent/'fixtures/observed_weather_temporal.json').read_text())
        observed=ToolOutcome(content=json.dumps(payload),surface=Surface(kind='metric',title='Weather'),success=True)
        provider=ScriptedProvider([call('get_weather',{'location':'context','event_ref':payload['context']['event_ref']}),ProviderTurn(text='ZIP 10010. Temperature is 70.7°F with no rain expected until later tonight.')])
        with patch.object(self.agent,'_execute_read_tool',return_value=observed):
            reply=await self.agent._handle_with_model('synthetic-trace','Check Weather for that same dinner again using my correction. Tell me which ZIP you used.',provider,'synthetic-trace-read')
        self.assertIn('ZIP 10010',reply.text)
        self.assertIn('70.7 degrees Fahrenheit',reply.text)
        self.assertIn('10%',reply.text)
        self.assertIn('2026-10-16 22:00 UTC',reply.text)
        self.assertIn('2026-10-16 23:00 UTC',reply.text)
        self.assertNotIn('until later tonight',reply.text)
        self.assertNotIn('no rain expected',reply.text)
