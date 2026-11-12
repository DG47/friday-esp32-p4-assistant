"""Acceptance gaps using actual controller paths and original synthetic facts."""
import asyncio
import unittest
import test_context_restart as context_fixture
import test_transcription_diagnostics as stt_fixture

class ContextCapacityAcceptance(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=context_fixture.ContextRestart.asyncSetUp
    asyncTearDown=context_fixture.ContextRestart.asyncTearDown
    fresh=context_fixture.ContextRestart.fresh
    read=context_fixture.ContextRestart.read

    async def test_eviction_does_not_recreate_authority_for_an_old_owner(self):
        context=self.agent.working_context
        context.begin('device','old-read')
        for i in range(32):context.begin('other-'+str(i),'request-'+str(i))
        self.assertNotIn('device',context.devices)
        self.assertFalse(context.current('device','old-read'))
        self.assertNotIn('device',context.devices)

    async def test_late_calendar_after_capacity_eviction_cannot_reattach_private_facts(self):
        agent=self.agent;context=agent.working_context
        entered,release=asyncio.Event(),asyncio.Event()
        original=agent.calendar.snapshot
        async def delayed(*args,**kwargs):
            result=await original(*args,**kwargs);entered.set();await release.wait();return result
        agent.calendar.snapshot=delayed
        task=asyncio.create_task(self.read(agent,'get_calendar',{'range':'today'},request='evicted-calendar'))
        try:
            await asyncio.wait_for(entered.wait(),1)
            for i in range(32):context.begin('other-'+str(i),'request-'+str(i))
            release.set();result=await asyncio.wait_for(task,1)
            self.assertFalse(result.success);self.assertIn('superseded',result.content)
            self.assertNotIn('device',context.devices)
        finally:release.set();await asyncio.wait_for(task,1)

    async def test_late_calendar_after_replacement_does_not_return_old_observed_facts(self):
        agent=self.agent;context=agent.working_context
        entered,release=asyncio.Event(),asyncio.Event();original=agent.calendar.snapshot
        async def delayed(*args,**kwargs):
            result=await original(*args,**kwargs);entered.set();await release.wait();return result
        agent.calendar.snapshot=delayed
        task=asyncio.create_task(self.read(agent,'get_calendar',{'range':'today'},request='old-calendar'))
        try:
            await asyncio.wait_for(entered.wait(),1);context.begin('device','replacement')
            release.set();result=await asyncio.wait_for(task,1)
            self.assertFalse(result.success);self.assertIn('superseded',result.content)
            self.assertNotIn('Dinner',result.content)
            self.assertEqual(context.state('device').owner,'replacement')
        finally:release.set();await asyncio.wait_for(task,1)

    async def test_same_request_id_cannot_restore_an_evicted_read_incarnation(self):
        agent=self.agent;context=agent.working_context
        entered,release=asyncio.Event(),asyncio.Event();original=agent.calendar.snapshot
        async def delayed(*args,**kwargs):
            result=await original(*args,**kwargs);entered.set();await release.wait();return result
        agent.calendar.snapshot=delayed
        task=asyncio.create_task(self.read(agent,'get_calendar',{'range':'today'},request='reused-read'))
        try:
            await asyncio.wait_for(entered.wait(),1)
            for i in range(32):context.begin('other-'+str(i),'request-'+str(i))
            context.begin('device','reused-read');replacement=context.devices['device']
            release.set();result=await asyncio.wait_for(task,1)
            self.assertFalse(result.success);self.assertIn('superseded',result.content)
            self.assertIs(context.devices['device'],replacement);self.assertFalse(replacement.events)
        finally:release.set();await asyncio.wait_for(task,1)

    async def test_capacity_eviction_discards_late_weather_without_recreating_context(self):
        agent=self.agent;context=agent.working_context
        entered,release=asyncio.Event(),asyncio.Event();original=agent.weather.current
        async def delayed(location='both'):
            entered.set();await release.wait();return await original(location)
        agent.weather.current=delayed
        task=asyncio.create_task(self.read(agent,'get_weather',{'location':'context'},request='evicted-weather'))
        try:
            await asyncio.wait_for(entered.wait(),1)
            for i in range(32):context.begin('other-'+str(i),'request-'+str(i))
            release.set();result=await asyncio.wait_for(task,1)
            self.assertFalse(result.success);self.assertIn('superseded',result.content)
            self.assertNotIn('device',context.devices)
        finally:release.set();await asyncio.wait_for(task,1)

class SpeechErrorPrivacyAcceptance(unittest.TestCase):
    setUp=stt_fixture.SocketDiagnosticsTests.setUp
    tearDown=stt_fixture.SocketDiagnosticsTests.tearDown
    authenticate=stt_fixture.SocketDiagnosticsTests.authenticate
    turn=stt_fixture.SocketDiagnosticsTests.turn
    failure_then_recovery=stt_fixture.SocketDiagnosticsTests.failure_then_recovery

    def test_untrusted_provider_exception_is_not_sent_to_device(self):
        from app.stt.base import TranscriptionError
        self.failure_then_recovery(TranscriptionError(stt_fixture.SECRET), 'stt_error',
                                   'Speech recognition failed. Please try again.')

class WakeErrorPrivacyAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_wake_load_and_decode_errors_are_fixed_copy_with_classified_reason(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock,patch
        from app.stt.base import AudioFormat,TranscriptionError
        from app.stt.diagnostics import FailureReason
        from app.stt.faster_whisper_provider import FasterWhisperTranscriber
        def failed_decode(*args,**kwargs):raise RuntimeError(stt_fixture.SECRET)
        for phase in ('load','decode'):
            p=FasterWhisperTranscriber('synthetic','cpu','int8','en')
            model=AsyncMock(side_effect=RuntimeError(stt_fixture.SECRET)) if phase=='load' else AsyncMock(return_value=SimpleNamespace(transcribe=failed_decode))
            try:
                with patch.object(p,'_get_model',model):
                    with self.assertRaises(TranscriptionError) as caught:
                        await p.transcribe_wake(b'\0\0'*2400,AudioFormat('pcm_s16le',12000,1))
                self.assertNotIn(stt_fixture.SECRET,str(caught.exception))
                self.assertEqual(caught.exception.reason,FailureReason.MODEL_LOAD if phase=='load' else FailureReason.DECODE)
            finally:self.assertTrue(await p.close())

class UnobservedOutcomeAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_exact_installed_no_tool_reminder_claim_is_not_an_operation_receipt(self):
        import json,tempfile
        from pathlib import Path
        from datetime import datetime,timedelta,timezone
        from unittest.mock import patch
        from app.agent import FridayAgent
        from app.db import ReminderStore
        from app.providers.base import ProviderTurn
        from test_conversation_scenario import settings_for,ScriptedProvider
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.db';store=ReminderStore(path)
            reminder=store.create('Synthetic reminder must remain active',datetime.now(timezone.utc)+timedelta(hours=1),'synthetic-owner')
            agent=FridayAgent(settings_for(path),store)
            claim='I marked your existing reminder complete. It is done.'
            provider=ScriptedProvider([ProviderTurn(text=claim)])
            try:
                with patch.object(agent.providers,'select',return_value=provider):
                    reply=await agent.handle('synthetic-owner','Please take care of my existing reminder as we discussed.',request_id='synthetic-no-tool-claim')
                self.assertIsNotNone(store.active_by_id(reminder.id,'synthetic-owner'))
                self.assertIsNone(reply.tool);self.assertNotIn(claim,reply.text)
                self.assertIn('No action was performed',reply.text);self.assertNotIn(claim,json.dumps(store.conversation('synthetic-owner')))
            finally:await agent.calendar.close()

class ObservedReceiptAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_actual_success_failure_and_partial_mutations_override_untrusted_final_claims(self):
        import json,tempfile
        from pathlib import Path
        from datetime import datetime,timedelta,timezone
        from unittest.mock import patch
        from app.agent import FridayAgent
        from app.db import ReminderStore
        from app.providers.base import ProviderTurn,ToolCall
        from test_conversation_scenario import settings_for,ScriptedProvider
        for mode in ('success','failure','partial'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'synthetic.db';store=ReminderStore(path);agent=FridayAgent(settings_for(path),store)
                due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
                calls=[ToolCall('synthetic-'+mode,'create_reminder',{'text':'Synthetic receipt fact','due_at':due if mode!='failure' else 'invalid'})]
                if mode=='partial':calls.append(ToolCall('synthetic-failed','create_reminder',{'text':'Synthetic rejected fact','due_at':'invalid'}))
                claim='All reminders are complete and I sent the report to your phone.'
                provider=ScriptedProvider([ProviderTurn(tool_calls=calls),ProviderTurn(text=claim)])
                try:
                    with patch.object(agent.providers,'select',return_value=provider):
                        reply=await agent.handle('receipt-device','Please handle the synthetic item.','receipt-'+mode)
                    self.assertNotIn(claim,reply.text);self.assertNotIn(claim,json.dumps(store.conversation('receipt-device')))
                    self.assertEqual(len(store.active('receipt-device')),0 if mode=='failure' else 1)
                    if mode!='failure':self.assertIn('The reminder is set.',reply.text)
                    if mode!='success':self.assertIn('No completion is confirmed.',reply.text)
                finally:await agent.calendar.close()

    async def test_proposed_tool_call_narrative_is_not_stored_as_completed_effect(self):
        import json,tempfile
        from pathlib import Path
        from datetime import datetime,timedelta,timezone
        from unittest.mock import patch
        from app.agent import FridayAgent
        from app.db import ReminderStore
        from app.providers.base import ProviderTurn,ToolCall
        from test_conversation_scenario import settings_for,ScriptedProvider
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.db';store=ReminderStore(path);agent=FridayAgent(settings_for(path),store)
            reminder=store.create('Synthetic consent fact',datetime.now(timezone.utc)+timedelta(hours=1),'receipt-device')
            claim='I completed the reminder before you confirmed.'
            provider=ScriptedProvider([ProviderTurn(text=claim,tool_calls=[ToolCall('proposal','complete_reminder',{'reminder_id':reminder.id})])])
            try:
                with patch.object(agent.providers,'select',return_value=provider):
                    reply=await agent.handle('receipt-device','Please handle the synthetic item.','receipt-proposal')
                self.assertIn('Confirm:',reply.text);self.assertIsNotNone(store.active_by_id(reminder.id,'receipt-device'))
                self.assertNotIn(claim,json.dumps(store.conversation('receipt-device')))
            finally:await agent.calendar.close()

    async def test_round_limit_reports_real_admitted_effects_instead_of_generic_failure(self):
        import tempfile
        from pathlib import Path
        from datetime import datetime,timedelta,timezone
        from unittest.mock import patch
        from app.agent import FridayAgent
        from app.db import ReminderStore
        from app.providers.base import ProviderTurn,ToolCall
        from test_conversation_scenario import settings_for,ScriptedProvider
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.db';store=ReminderStore(path);agent=FridayAgent(settings_for(path),store)
            due=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
            provider=ScriptedProvider([ProviderTurn(tool_calls=[ToolCall('round-'+str(i),'create_reminder',{'text':'Synthetic item '+str(i),'due_at':due})]) for i in range(4)])
            try:
                with patch.object(agent.providers,'select',return_value=provider):
                    reply=await agent.handle('receipt-device','Please handle the four synthetic items.','receipt-round-limit')
                self.assertEqual(len(store.active('receipt-device')),4)
                self.assertEqual(reply.text.count('The reminder is set.'),4)
            finally:await agent.calendar.close()

import test_device_socket as socket_fixture
class CalendarSocketCapacityAcceptance(unittest.TestCase):
    setUp=socket_fixture.DeviceSocketTests.setUp
    tearDown=socket_fixture.DeviceSocketTests.tearDown
    def test_native_calendar_navigation_drops_evicted_read_without_private_surface(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import main
        from app.experience import CAPABILITY
        from app.working_context import WorkingContext
        from app.tools.google_calendar import CalendarSnapshot
        from app.models import CalendarEvent
        from datetime import timedelta
        from test_conversation_scenario import settings_for,NOW
        context=WorkingContext(settings_for(self.reminder_store.path),lambda:NOW)
        snapshot=CalendarSnapshot('ready',[CalendarEvent(id='capacity-synthetic',title='CAPACITY_PRIVATE_SYNTHETIC_FACT',starts_at=NOW+timedelta(hours=1),ends_at=NOW+timedelta(hours=2))],NOW)
        async def evict_during_read(*args,**kwargs):
            for i in range(32):context.begin('other-'+str(i),'request-'+str(i))
            return snapshot
        with patch.object(main.agent,'working_context',context),patch.object(main.agent.calendar,'snapshot',side_effect=evict_during_read):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/calendar-capacity') as ws:
                self.assertEqual(ws.receive_json()['type'],'hello.required')
                token='a'*64;self.credential_store.open_pairing_window('calendar-capacity',token[:8])
                ws.send_json({'type':'hello','device_token':token,'capabilities':[CAPABILITY]})
                while ws.receive_json()['type']!='ready':pass
                ws.send_json({'type':'scene.action','action':'calendar','request_id':'capacity-calendar-read'})
                frames=[]
                for _ in range(12):
                    frame=ws.receive_json();frames.append(frame)
                    if frame['type']=='experience.finished' and frame.get('experience_id')=='capacity-calendar-read':break
                else:self.fail('Expected bound navigation finish')
                self.assertNotIn('CAPACITY_PRIVATE_SYNTHETIC_FACT',str(frames))
                self.assertNotIn('calendar-capacity',context.devices)
