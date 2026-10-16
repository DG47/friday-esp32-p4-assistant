import asyncio
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from app.agent import FridayAgent
from app.agent_jobs import AgentJobStore,AgentJobWorker,UNCERTAIN_CLAIM_ERROR
from app.config import Settings
from app.db import ReminderStore
from app.device_hub import DeviceHub
from app.execution_stop import ExecutionStop
from app.models import AgentReply,Surface
from app.stop_observer import StopObserver

class ExecutionStops(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'synthetic.db'
        self.reminders=ReminderStore(self.path);self.store=AgentJobStore(self.path);self.hub=DeviceHub()
        self.settings=Settings(_env_file=None,assistant_env='test',assistant_provider='mock',assistant_database_path=self.path,tts_provider='none',stt_provider='none',weather_provider='none')
        self.agent=FridayAgent(self.settings,self.reminders)
        self.worker=AgentJobWorker(self.store,self.agent,self.hub)
        self.stop=ExecutionStop(self.store,self.worker);self.stop.observer=StopObserver(ack_budget=.03,drain_grace=.01)
        self.release=asyncio.Event();self.running=None
    async def asyncTearDown(self):
        self.release.set()
        if self.running:await asyncio.wait_for(asyncio.shield(self.running),2)
        await self.stop.observer.close();await self.agent.calendar.close();self.temp.cleanup()
    async def test_queued_never_admitted_stop_is_durable_idempotent_and_never_runs(self):
        job=self.store.enqueue('device','set a timer for 2 minutes','queued')
        result=await self.stop.request('device',job.id,job.delivery_id,'stop-queued')
        self.assertTrue(result['ack_confirmed']);self.assertTrue(result['authority_revoked'])
        self.assertEqual(result['state_at_ack'],'stopped_before_admission')
        self.assertFalse(await self.worker.process_once());self.assertEqual(self.reminders.active('device'),[])
        duplicate=AgentJobStore(self.path).request_execution_stop('device',job.id,job.delivery_id,'retry-stop')
        self.assertEqual(duplicate['state'],'stopped_before_admission')
        self.assertEqual(self.store.get(job.id).attempts,0)
    async def test_resistant_handler_cannot_admit_late_reminder_after_revocation(self):
        entered=asyncio.Event();outer=self
        class Resistant:
            async def handle(self,*args,**kwargs):
                entered.set()
                while not outer.release.is_set():
                    try:await outer.release.wait()
                    except asyncio.CancelledError:pass
                return outer.agent._execute_tool('create_reminder',{'text':'Original late synthetic effect','due_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()},'device',kwargs['request_id'],0)
        self.worker.agent=Resistant()
        job=self.store.enqueue('device','Original synthetic slow request','running')
        self.running=asyncio.create_task(self.worker.process_once());await entered.wait()
        result=await self.stop.request('device',job.id,job.delivery_id,'stop-running')
        self.assertTrue(result['authority_revoked']);self.assertFalse(result['handler_drained'])
        self.assertEqual(result['state_at_ack'],'outcome_uncertain')
        self.release.set();await self.running
        self.assertEqual(self.reminders.active('device'),[])
        saved=self.store.get(job.id);self.assertEqual((saved.status,saved.error),('running',UNCERTAIN_CLAIM_ERROR))
        self.assertEqual(AgentJobStore(self.path).recover_interrupted(datetime.now(timezone.utc)+timedelta(days=1)),0)
    async def test_already_admitted_effect_is_not_undone_or_called_stopped(self):
        entered=asyncio.Event();outer=self
        class Admitted:
            async def handle(self,*args,**kwargs):
                outer.agent._execute_tool('create_reminder',{'text':'Original admitted synthetic effect','due_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()},'device',kwargs['request_id'],0)
                entered.set()
                while not outer.release.is_set():
                    try:await outer.release.wait()
                    except asyncio.CancelledError:pass
                return AgentReply(text='Old unconfirmed result',surface=Surface(kind='message',title='Synthetic'))
        self.worker.agent=Admitted();job=self.store.enqueue('device','Original already admitted request','admitted')
        self.running=asyncio.create_task(self.worker.process_once());await entered.wait()
        result=await self.stop.request('device',job.id,job.delivery_id,'stop-admitted')
        self.assertEqual(result['state_at_ack'],'outcome_uncertain');self.assertEqual(len(self.reminders.active('device')),1)
        self.release.set();await self.running
        self.assertIsNone(self.store.get(job.id).reply_json)
        self.assertEqual(len(self.reminders.active('device')),1)
    async def test_owner_and_incarnation_mismatch_cannot_revoke_or_disclose(self):
        job=self.store.enqueue('device','Private synthetic request','owned')
        for device,identity in [('other',job.delivery_id),('device','0'*32)]:
            with self.assertRaises(ValueError):self.store.request_execution_stop(device,job.id,identity,'wrong-scope')
        self.assertEqual(self.store.get(job.id).status,'queued')
    async def test_retry_with_prior_attempt_is_uncertain_not_never_admitted(self):
        job=self.store.enqueue('device','Original retried synthetic request','retried')
        claim=self.store.claim_next();self.store.fail(claim,'synthetic failure')
        result=await self.stop.request('device',job.id,job.delivery_id,'stop-retry')
        self.assertEqual(result['state_at_ack'],'outcome_uncertain')
        self.assertFalse((await self.stop.status('device',job.id,job.delivery_id,'read-status'))['no_execution_admitted'])

    async def test_lost_ack_status_and_recovery_are_scope_bound_after_restart(self):
        job=self.store.enqueue('device','Original synthetic stopped job','lost-ack')
        self.store.claim_next()
        self.store.request_execution_stop('device',job.id,job.delivery_id,'lost-stop')
        restored=AgentJobStore(self.path)
        self.assertEqual(restored.execution_recovery('device'),{'job_id':job.id,'execution_id':job.delivery_id})
        self.assertIsNone(restored.execution_recovery('other'))
        status=restored.execution_status('device',job.id,job.delivery_id)
        self.assertTrue(status['authority_revoked']);self.assertTrue(status['outcome_uncertain'])
        self.assertFalse(status['no_execution_admitted'])
        with self.assertRaises(ValueError):restored.execution_status('other',job.id,job.delivery_id)

    async def test_persistence_deadline_is_unconfirmed_and_late_commit_is_retained(self):
        import threading
        from unittest.mock import patch
        permit=threading.Event();job=self.store.enqueue('device','Original delayed Stop','delayed')
        original=self.store.request_execution_stop
        def delayed(*args):
            if not permit.wait(2):raise OSError('Synthetic storage safety bound')
            return original(*args)
        try:
            with patch.object(self.store,'request_execution_stop',side_effect=delayed):
                result=await self.stop.request('device',job.id,job.delivery_id,'delayed-stop')
                self.assertFalse(result['ack_confirmed']);self.assertFalse(result['authority_revoked'])
                self.assertEqual(self.store.get(job.id).status,'queued')
                permit.set();await self.stop.observer.close()
            status=await self.stop.status('device',job.id,job.delivery_id,'after-late-commit')
            self.assertTrue(status['no_execution_admitted']);self.assertTrue(status['authority_revoked'])
        finally:permit.set()
    async def test_failed_persistence_never_claims_revocation(self):
        from unittest.mock import patch
        job=self.store.enqueue('device','Original unavailable storage','failed-persistence')
        with patch.object(self.store,'request_execution_stop',side_effect=OSError('Private synthetic storage error')):
            result=await self.stop.request('device',job.id,job.delivery_id,'failed-stop')
        self.assertFalse(result['ack_confirmed']);self.assertFalse(result['authority_revoked'])
        self.assertNotIn('Private synthetic',str(result));self.assertEqual(self.store.get(job.id).status,'queued')
    async def test_cancelled_request_does_not_forget_admitted_stop_write(self):
        import threading
        from unittest.mock import patch
        entered,permit=threading.Event(),threading.Event();job=self.store.enqueue('device','Original cancelled observer','cancelled-observer')
        original=self.store.request_execution_stop
        def delayed(*args):
            entered.set()
            if not permit.wait(2):raise OSError('Synthetic storage safety bound')
            return original(*args)
        try:
            with patch.object(self.store,'request_execution_stop',side_effect=delayed):
                request=asyncio.create_task(self.stop.request('device',job.id,job.delivery_id,'cancel-observer'))
                self.assertTrue(await asyncio.to_thread(entered.wait,1));request.cancel()
                with self.assertRaises(asyncio.CancelledError):await request
                self.assertTrue(self.stop.observer.attempts);permit.set();await self.stop.observer.close()
            self.assertEqual(self.store.get(job.id).error,'user_stop_before_admission')
        finally:permit.set()

    async def test_lost_ack_delivery_cannot_keep_old_speech_or_presentation_authority(self):
        from unittest.mock import patch
        from app import main
        from app.speech import SpeechDirector
        job=self.store.enqueue('device','Synthetic queued request','lost-delivery')
        speech=SpeechDirector(self.hub);speech.latest['device']=job.id;speech.generations['device']=7
        with self.hub.experience.scope('device',job.id):self.hub.experience.bind_job('device',job.id)
        class LostAck:
            async def send_json(inner,response):
                self.assertTrue(response['authority_revoked'])
                self.assertIsNone(speech.latest['device'])
                self.assertGreater(speech.generations['device'],7)
                self.assertEqual(self.hub.experience.current['device'].request,'stop-lost-delivery')
                raise OSError('Synthetic lost acknowledgement')
        with patch.object(main,'execution_stop',self.stop),patch.object(main,'hub',self.hub),patch.object(main,'speech',speech):
            with self.hub.experience.scope('device','stop-lost-delivery'):
                with self.assertRaises(OSError):
                    await main.execution_control_reply('device',LostAck(),job.id,job.delivery_id,'stop-lost-delivery',True)
        self.assertEqual(self.store.get(job.id).error,'user_stop_before_admission')
