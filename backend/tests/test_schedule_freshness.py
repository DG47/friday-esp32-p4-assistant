import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.agent import FridayAgent
from app.tools.outcome_prose import NO_VERIFIED_OUTCOME
from app.config import Settings
from app.db import ReminderStore
from app.models import CalendarEvent
from app.providers.base import ProviderTurn
from app.tools.google_calendar import CalendarSnapshot
from app.tools.schedule_freshness import current_schedule_query

class ScheduleFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.agent=FridayAgent(Settings(_env_file=None,assistant_env='test',assistant_provider='mock',calendar_provider='sample',assistant_timezone='UTC',assistant_database_path=Path(self.temp.name)/'synthetic.db',tts_provider='none',stt_provider='none',weather_provider='none'), ReminderStore(Path(self.temp.name)/'synthetic.db'))
        self.now=datetime.now(timezone.utc)
        self.agent.calendar.clock=lambda:self.now
        self.events=[CalendarEvent(id='synthetic',title='Original fixture appointment',starts_at=self.now+timedelta(hours=1),ends_at=self.now+timedelta(hours=2))]
        self.calls=[];self.status='sample';self.error=None;self.fetched=self.now
        async def read(range='today',force=False):
            self.calls.append((range,force))
            if self.error: raise self.error
            return CalendarSnapshot(self.status,[event.model_copy(deep=True) for event in self.events],self.fetched)
        self.agent.calendar.snapshot=read
    async def asyncTearDown(self):
        await self.agent.calendar.close();self.temp.cleanup()
    async def ask(self,prompt='What are my next appointment start and end times and duration?'):
        return await self.agent.handle('fixture',prompt)
    async def test_external_move_duration_and_delete_between_turns(self):
        first=await self.ask(); self.assertIn('60 minutes',first.text)
        self.events[0].starts_at+=timedelta(hours=3)
        self.events[0].ends_at=self.events[0].starts_at+timedelta(minutes=90)
        second=await self.ask();self.assertIn('90 minutes',second.text);self.assertNotEqual(first.text,second.text)
        self.events=[];third=await self.ask();self.assertIn('No upcoming event',third.text)
        self.assertEqual(self.calls,[('next',True)]*3)
    async def test_old_history_beyond_cutoff_cannot_establish_current_facts(self):
        await self.ask()
        for n in range(12):self.agent.store.append_exchange('fixture',f'filler-{n}','Neutral synthetic exchange','Acknowledged')
        self.events[0].starts_at+=timedelta(hours=4);self.events[0].ends_at=self.events[0].starts_at+timedelta(minutes=25)
        reply=await self.ask();self.assertIn('25 minutes',reply.text);self.assertEqual(self.calls[-1],('next',True))
    async def test_timeout_failed_stale_or_old_timestamp_fail_closed(self):
        await self.ask()
        for status,error,fetched in [('sample',asyncio.TimeoutError(),self.now),('error',None,self.now),('stale',None,self.now),('ready',None,self.now-timedelta(minutes=2))]:
            self.status,self.error,self.fetched=status,error,fetched
            reply=await self.ask();self.assertIn('freshness is unavailable',reply.text);self.assertNotIn('Original fixture',reply.text);self.assertFalse(self.agent.working_context.state('fixture').events)
    async def test_historical_question_does_not_require_current_reread(self):
        class HistoricalProvider:
            name='original-synthetic-history'
            async def complete(self,*args):return ProviderTurn(text='Earlier you described a sample appointment; that is historical, not current confirmation.')
        with patch.object(self.agent.providers,'select',return_value=HistoricalProvider()):
            reply=await self.ask('What did I previously say about the appointment time?')
        self.assertEqual(reply.text,NO_VERIFIED_OUTCOME);self.assertEqual(self.calls,[])
    async def test_model_requested_failed_read_cannot_produce_confident_continuation(self):
        from app.providers.base import ToolCall
        class Provider:
            name='original-synthetic-request'
            calls=0
            async def complete(inner,*args):
                inner.calls+=1
                return ProviderTurn(tool_calls=[ToolCall('synthetic-current','get_calendar',{'range':'next'})])
        provider=Provider();self.error=asyncio.TimeoutError()
        with patch.object(self.agent.providers,'select',return_value=provider):
            reply=await self.ask('Read my calendar and check weather for its event')
        self.assertEqual(provider.calls,1);self.assertIn('freshness is unavailable',reply.text)
    def test_bounded_intent_scope(self):
        self.assertTrue(current_schedule_query('When does my meeting start?'))
        self.assertFalse(current_schedule_query('What time did my meeting previously start?'))
        self.assertFalse(current_schedule_query('What is the weather at the dinner?'))

    async def test_named_or_ordinal_referents_clarify_without_substitution(self):
        for prompt in ['When does my Friday meeting start?', 'What time is my second appointment?', 'When does my next meeting with a doctor start?', 'When does my next appointment on 2026-10-20 start?', 'When does my next dinner start?']:
            reply=await self.ask(prompt)
            self.assertIn('Which appointment',reply.text)
        self.assertEqual(self.calls,[])

    async def test_superseded_failed_read_preserves_new_owner_context(self):
        from copy import deepcopy
        await self.ask()
        events=deepcopy(self.agent.working_context.state('fixture').events)
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(range='today',force=False):
            entered.set();await release.wait();raise asyncio.TimeoutError()
        self.agent.calendar.snapshot=delayed
        task=asyncio.create_task(self.agent.handle('fixture','What are my next appointment times?',request_id='old-request'))
        await entered.wait()
        self.agent.working_context.begin('fixture','new-request')
        release.set();await task
        self.assertEqual(self.agent.working_context.state('fixture').owner,'new-request')
        self.assertEqual(self.agent.working_context.state('fixture').events,events)
