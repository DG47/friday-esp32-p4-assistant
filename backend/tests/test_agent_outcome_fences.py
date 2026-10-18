import asyncio
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from app.agent_jobs import AgentJobStore,AgentJobWorker,UNCERTAIN_CLAIM_ERROR
from app.db import ReminderStore
from app.device_hub import DeviceHub
from app.models import AgentReply,Surface
from app.providers.local_policy import LocalModelUnavailable
from test_agent_jobs import FakeWebSocket

class OutcomeFences(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'jobs.db'
        ReminderStore(self.path);self.store=AgentJobStore(self.path);self.hub=DeviceHub()
        self.peer=FakeWebSocket();await self.hub.connect('synthetic-device',self.peer)
    async def asyncTearDown(self):self.temp.cleanup()
    def reply(self,text):return AgentReply(text=text,surface=Surface(kind='message',title='Synthetic'),provider='test')
    async def blocked(self,kind,identity):
        entered,release=asyncio.Event(),asyncio.Event();outer=self;finished=[]
        class Old:
            async def handle(self,*args,**kwargs):
                entered.set();await release.wait()
                if kind=='failure':raise RuntimeError('Original synthetic obsolete failure')
                if kind=='timeout':raise TimeoutError('Original synthetic obsolete timeout')
                if kind=='local_unavailable':raise LocalModelUnavailable('local_server_unavailable')
                return outer.reply('Obsolete synthetic result')
        job=self.store.enqueue('synthetic-device','Original synthetic request',request_id=identity)
        worker=AgentJobWorker(self.store,Old(),self.hub)
        async def on_finished(device):finished.append(device)
        worker.on_finished=on_finished
        task=asyncio.create_task(worker.process_once());await asyncio.wait_for(entered.wait(),1)
        return job,worker,task,release,finished
    async def test_obsolete_outcomes_cannot_replace_or_present_newer_claim(self):
        for kind in ('success','failure','timeout','local_unavailable'):
            with self.subTest(kind=kind):
                job,worker,task,release,finished=await self.blocked(kind,'synthetic-'+kind)
                try:
                    with closing(self.store._connect()) as db, db:db.execute("UPDATE agent_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(job.id,))
                    self.assertEqual(AgentJobStore(self.path).recover_interrupted(),1)
                    outer=self
                    class New:
                        async def handle(self,*args,**kwargs):return outer.reply('Newer confirmed synthetic result')
                    self.assertTrue(await AgentJobWorker(self.store,New(),self.hub).process_once())
                    saved=self.store.get(job.id);self.assertEqual(saved.attempts,2)
                    messages=list(self.peer.messages)
                    release.set();await asyncio.wait_for(task,1)
                    self.assertEqual(self.store.get(job.id),saved)
                    self.assertEqual(self.peer.messages,messages)
                    self.assertEqual(finished,[])
                finally:
                    release.set();await asyncio.wait_for(task,1)
                    with closing(self.store._connect()) as db, db:db.execute('DELETE FROM agent_jobs')
    async def test_late_success_cannot_clear_persisted_uncertainty(self):
        job,worker,task,release,finished=await self.blocked('success','synthetic-uncertain')
        try:
            claimed=self.store.get(job.id);self.store.mark_execution_uncertain(claimed)
            release.set();await asyncio.wait_for(task,1)
            saved=self.store.get(job.id)
            self.assertEqual((saved.status,saved.error),('running',UNCERTAIN_CLAIM_ERROR))
            self.assertIsNone(saved.reply_json);self.assertEqual(finished,[])
            self.assertFalse(any(p['type']=='agent.result' for p in self.peer.messages))
        finally:release.set();await asyncio.wait_for(task,1)
    async def test_recreated_request_id_does_not_reuse_old_outcome_authority(self):
        job,worker,task,release,finished=await self.blocked('success','synthetic-recreated')
        try:
            old=self.store.get(job.id)
            with closing(self.store._connect()) as db, db:db.execute('DELETE FROM agent_jobs WHERE id=?',(job.id,))
            self.store.enqueue('synthetic-device','Original synthetic replacement',request_id=job.id)
            new=self.store.claim_next();self.assertEqual(new.attempts,old.attempts)
            self.assertNotEqual(new.delivery_id,old.delivery_id)
            self.assertFalse(self.store.mark_execution_uncertain(old))
            self.assertIsNone(self.store.fail(old,'Original obsolete failure',max_attempts=1))
            release.set();await asyncio.wait_for(task,1)
            self.assertEqual(self.store.get(job.id),new);self.assertEqual(finished,[])
        finally:release.set();await asyncio.wait_for(task,1)
