"""Fresh-source restart resume, synthetic state only; no private facts checkpointed."""
import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from app.agent import FridayAgent
from app.db import ReminderStore
from app.models import CalendarEvent
from test_conversation_scenario import settings_for, synthetic_calendar, SyntheticWeather, NOW


class ContextRestart(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'synthetic.db'
        self.settings = settings_for(self.path)
        self.agents = []
        self.agent, _ = self.fresh()
        self.agent.working_context.begin('device', 'before')
        result = await self.read(self.agent, 'get_calendar', {'range':'today'}, 'before')
        self.ref = json.loads(result.content)['context_refs'][0]['event_ref']
        self.corrected = await self.read(self.agent, 'get_weather', {'event_ref':self.ref,'location':'10010'}, 'before')
    async def asyncTearDown(self):
        for agent in self.agents:
            await agent.calendar.close()
        self.temp.cleanup()
    def fresh(self, place='Jersey City, NJ (07306)'):
        agent = FridayAgent(self.settings, ReminderStore(self.path))
        snapshot = synthetic_calendar(agent, place)
        agent.weather = SyntheticWeather(self.settings.weather_locations)
        self.agents.append(agent)
        return agent, snapshot
    async def read(self, agent, name, args, request='after', device='device'):
        agent.working_context.begin(device, request)
        return await agent._execute_read_tool(name, args, device, request, 0, for_model=True)
    async def test_restart_rereads_sources_and_preserves_exact_zip_correction(self):
        agent, snapshot = self.fresh()
        reads = []
        async def current(range='today',force=False):
            reads.append((range,force))
            result = deepcopy(snapshot);result.fetched_at=agent.calendar.clock()
            return result
        agent.calendar.snapshot = current
        self.assertEqual(agent.working_context.state('device').events, {})
        self.assertIsNone(agent.working_context.state('device').weather)
        result = await self.read(agent,'get_weather',{'location':'context'})
        self.assertTrue(result.success)
        self.assertEqual(reads,[('today',True)])
        self.assertEqual(agent.weather.calls,['10010'])
        self.assertEqual(result.surface.context['event_ref'],self.ref)
        self.assertEqual(agent.working_context.state('device').events[self.ref].location,'Jersey City, NJ (07306)')
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT * FROM context_checkpoints').fetchone()
        self.assertNotIn('Dinner',str(row));self.assertNotIn('Jersey City',str(row))
        self.assertNotIn('70.7',str(row))
        old_action = self.corrected.surface.actions[1].id
        with self.assertRaises(ValueError):agent.working_context.touch('device',old_action)
    async def test_restart_never_uses_correction_for_changed_or_missing_source(self):
        agent,_ = self.fresh('Somewhere ambiguous')
        result = await self.read(agent,'get_weather',{'location':'context'})
        self.assertFalse(result.success);self.assertEqual(agent.weather.calls,[])
        self.assertIn('Which configured ZIP',result.content)
    async def test_removed_event_cannot_silently_select_a_different_event(self):
        agent,snapshot = self.fresh()
        snapshot.events[0].id='different-instance'
        result = await self.read(agent,'get_weather',{'location':'context'})
        self.assertFalse(result.success);self.assertEqual(agent.weather.calls,[])
        self.assertIn('Which event',result.content)
    async def test_dismissal_expiry_and_other_identity_do_not_resume(self):
        other,_ = self.fresh()
        result = await self.read(other,'get_weather',{'location':'context'},device='other-device')
        self.assertFalse(result.success);self.assertEqual(other.weather.calls,[])
        expired,_ = self.fresh();expired.calendar.clock=lambda:NOW+timedelta(hours=25)
        result = await self.read(expired,'get_weather',{'location':'context'})
        self.assertFalse(result.success);self.assertEqual(expired.weather.calls,[])
        # Recreate a current checkpoint, then explicit dismissal must delete it.
        await self.read(self.agent,'get_weather',{'location':'context'},request='before')
        self.agent.working_context.dismiss('device')
        dismissed,_ = self.fresh()
        result = await self.read(dismissed,'get_weather',{'location':'context'})
        self.assertFalse(result.success);self.assertEqual(dismissed.weather.calls,[])
    async def test_failed_fresh_read_cannot_revive_checkpoint_facts(self):
        agent,_ = self.fresh()
        async def failure(*args,**kwargs):raise OSError('synthetic offline')
        agent.calendar.snapshot=failure
        result = await self.read(agent,'get_weather',{'location':'context'})
        self.assertFalse(result.success);self.assertEqual(agent.weather.calls,[])
        self.assertIn('freshness is unavailable',result.content)
        self.assertFalse(agent.working_context.state('device').events)

    async def test_superseded_resume_does_not_read_weather_or_replace_new_owner(self):
        agent,snapshot = self.fresh()
        entered,release=asyncio.Event(),asyncio.Event()
        async def delayed(*args,**kwargs):
            entered.set();await release.wait();return deepcopy(snapshot)
        agent.calendar.snapshot=delayed
        task=asyncio.create_task(self.read(agent,'get_weather',{'location':'context'}))
        try:
            await asyncio.wait_for(entered.wait(),1)
            agent.working_context.begin('device','new-owner')
            release.set();result=await asyncio.wait_for(task,1)
            self.assertFalse(result.success);self.assertIn('superseded',result.content)
            self.assertEqual(agent.weather.calls,[])
            self.assertEqual(agent.working_context.state('device').owner,'new-owner')
        finally:
            release.set();await asyncio.wait_for(task,1)

    async def test_private_calendar_model_gate_precedes_resume_io(self):
        agent,_=self.fresh()
        self.settings.calendar_provider='google';self.settings.calendar_model_context=False
        async def forbidden(*args,**kwargs):raise AssertionError('Sharing gate must precede resume I/O')
        agent.calendar.snapshot=forbidden
        result=await self.read(agent,'get_weather',{'location':'context'})
        self.assertFalse(result.success);self.assertIn('sharing_disabled',result.content)
        self.assertEqual(agent.weather.calls,[])

    async def test_checkpoint_preserves_next_range_without_claiming_facts_survived(self):
        await self.read(self.agent,'get_calendar',{'range':'next'},request='before')
        agent,snapshot=self.fresh();reads=[]
        async def fresh(range='today',force=False):
            reads.append((range,force));return deepcopy(snapshot)
        agent.calendar.snapshot=fresh
        result=await self.read(agent,'get_weather',{'location':'context'})
        self.assertTrue(result.success);self.assertEqual(reads,[('next',True)])

    async def test_checkpoint_rows_are_bounded_and_load_does_not_renew(self):
        context=self.agent.working_context
        for index in range(40):
            device='synthetic-device-'+str(index)
            state=context.state(device);state.focus=f'{index:024x}'
            context.checkpoints.save(device,state)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM context_checkpoints').fetchone()[0],32)
            before=db.execute('SELECT expires_at FROM context_checkpoints WHERE device_id=?',(device,)).fetchone()[0]
        context.checkpoints.load(device)
        with closing(sqlite3.connect(self.path)) as db:
            after=db.execute('SELECT expires_at FROM context_checkpoints WHERE device_id=?',(device,)).fetchone()[0]
        self.assertEqual(before,after)

    async def test_disconnect_erases_private_facts_but_preserves_fresh_resume_intent(self):
        context=self.agent.working_context
        old_action=self.corrected.surface.actions[1].id
        context.disconnect('device')
        self.assertNotIn('device',context.devices)
        result=await self.read(self.agent,'get_weather',{'location':'context'})
        self.assertTrue(result.success)
        self.assertEqual(self.agent.weather.calls,['10010','10010'])
        with self.assertRaises(ValueError):context.touch('device',old_action)
