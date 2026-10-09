import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.task_slice import TaskStore, TaskWorker, FIXTURES, digest, execute_fixture
from app.task_demo import create_demo_app
from fastapi.testclient import TestClient


class StoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.path = Path(self.temp.name)/'tasks.db'
        self.s = TaskStore(self.path, lambda:self.now[0])
        self.owner = 'owner-a'

    def tearDown(self): self.temp.cleanup()

    def proposed(self, fixture='range-total', action='start'):
        return self.s.create(self.owner, action, 'Prepare a reviewable fixture fix', fixture)

    def approved(self):
        t=self.proposed()
        return self.s.approve(self.owner,t['id'],'approve',t['revision'],t['scope_digest'],t['nonce'])

    async def test_end_to_end_check_patch_verify_same_context(self):
        original=digest(FIXTURES)
        t=self.approved();w=TaskWorker(self.s,self.owner)
        await w.step(t['id']);await w.step(t['id'])
        answer=self.s.conversation(self.owner,t['id'],'phone-turn','Phone','What failed?')
        self.assertIn('expected 10, got 6',answer)
        await w.run(t['id']);done=self.s.get(self.owner,t['id'])
        self.assertEqual(done['state'],'ready_for_review');self.assertEqual(len(done['evidence']),5)
        self.assertEqual(done['evidence'][-1]['output']['checked_digest'],done['artifact']['after_digest'])
        self.assertTrue(done['evidence'][-1]['output']['passed']);self.assertEqual(digest(FIXTURES),original)
        answer2=self.s.conversation(self.owner,t['id'],'device-turn','Device text harness','What changed?')
        self.assertIn('include the final range',answer2);self.assertEqual(len(self.s.turns(self.owner,t['id'])),2)
        self.assertEqual(self.s.conversation(self.owner,t['id'],'phone-turn','Mac','What failed?'),answer)
        self.assertEqual(len(self.s.turns(self.owner,t['id'])),2)

    async def test_exact_approval_duplicates_expiry_and_stop_race(self):
        t=self.proposed()
        with self.assertRaises(ValueError):self.s.approve(self.owner,t['id'],'bad-revision',99,t['scope_digest'],t['nonce'])
        with self.assertRaises(ValueError):self.s.approve(self.owner,t['id'],'bad-digest',t['revision'],'x'*64,t['nonce'])
        self.s.stop(self.owner,t['id'],'stop')
        with self.assertRaises(ValueError):self.s.approve(self.owner,t['id'],'late',t['revision'],t['scope_digest'],t['nonce'])
        self.assertFalse(await TaskWorker(self.s,self.owner).step(t['id']))
        fresh=self.proposed(action='fresh');self.now[0]+=301
        with self.assertRaises(ValueError):self.s.approve(self.owner,fresh['id'],'expired',fresh['revision'],fresh['scope_digest'],fresh['nonce'])

    async def test_refresh_expired_review_invalidates_old_nonce(self):
        old=self.proposed();self.now[0]+=301
        fresh=self.s.review(self.owner,old['id'],'refresh',old['revision'])
        with self.assertRaises(ValueError):self.s.approve(self.owner,old['id'],'old',old['revision'],old['scope_digest'],old['nonce'])
        self.s.approve(self.owner,fresh['id'],'fresh',fresh['revision'],fresh['scope_digest'],fresh['nonce'])
        await TaskWorker(self.s,self.owner).run(old['id'])
        self.assertEqual(self.s.get(self.owner,old['id'])['state'],'ready_for_review')

    async def test_duplicate_start_approval_and_conflicting_ids(self):
        t=self.proposed();self.assertEqual(self.proposed()['id'],t['id'])
        with self.assertRaises(ValueError):self.s.create(self.owner,'start','different goal','range-total')
        self.s.approve(self.owner,t['id'],'approval',t['revision'],t['scope_digest'],t['nonce'])
        replay=self.s.approve(self.owner,t['id'],'approval',t['revision'],t['scope_digest'],t['nonce'])
        self.assertEqual(replay['state'],'queued')
        with self.assertRaises(ValueError):self.s.approve(self.owner,t['id'],'other-client-approval',t['revision'],t['scope_digest'],t['nonce'])
        a=TaskWorker(self.s,self.owner);b=TaskWorker(self.s,self.owner)
        await asyncio.gather(a.run(t['id']),b.run(t['id']))
        self.assertEqual(len(self.s.get(self.owner,t['id'])['evidence']),5)

    async def test_owner_artifact_turn_and_action_isolation(self):
        t=self.approved();await TaskWorker(self.s,self.owner).run(t['id'])
        for op in [lambda:self.s.get('owner-b',t['id']),lambda:self.s.stop('owner-b',t['id'],'stop'),lambda:self.s.turns('owner-b',t['id']),lambda:self.s.conversation('owner-b',t['id'],'turn','Phone','status')]:
            with self.assertRaises(ValueError):op()
        self.assertEqual(self.s.list('owner-b'),[])

    async def test_stop_running_waits_for_drain_fences_evidence(self):
        t=self.approved();entered=asyncio.Event();release=asyncio.Event()
        async def block(task):entered.set();await release.wait()
        w=TaskWorker(self.s,self.owner,block);running=asyncio.create_task(w.step(t['id']));await entered.wait()
        stopped=self.s.stop(self.owner,t['id'],'stop');self.assertEqual(stopped['state'],'stopping')
        self.assertEqual(self.s.stop(self.owner,t['id'],'stop')['state'],'stopping')
        self.assertEqual(self.s.stop(self.owner,t['id'],'other-client-stop')['state'],'stopping')
        release.set();await running;done=self.s.get(self.owner,t['id'])
        self.assertEqual(done['state'],'stopped');self.assertEqual(done['evidence'],[])
        self.assertFalse(await w.step(t['id']))

    async def test_crash_restart_expired_lease_fences_old_worker(self):
        t=self.approved();claim=self.s.claim(self.owner,t['id'],'old-worker',10)
        restarted=TaskStore(self.path,lambda:self.now[0]);restarted.recover(self.owner)
        self.assertEqual(restarted.get(self.owner,t['id'])['state'],'running')
        self.now[0]+=11;restarted.recover(self.owner)
        new=restarted.claim(self.owner,t['id'],'new-worker')
        self.assertFalse(self.s.commit(self.owner,t['id'],claim['fence'],claim['generation'],execute_fixture(claim)))
        self.assertTrue(restarted.commit(self.owner,t['id'],new['fence'],new['generation'],execute_fixture(new)))
        await TaskWorker(restarted,self.owner).run(t['id'])
        self.assertEqual(restarted.get(self.owner,t['id'])['state'],'ready_for_review')
        self.assertEqual(len(restarted.get(self.owner,t['id'])['evidence']),5)

    async def test_cancelled_worker_requires_recovery_not_fake_completion(self):
        t=self.approved();claim=self.s.claim(self.owner,t['id'],'crashed',10)
        self.s.stop(self.owner,t['id'],'stop');self.now[0]+=11;self.s.recover(self.owner)
        self.assertEqual(self.s.get(self.owner,t['id'])['state'],'stopping')
        self.s.recover(self.owner,drained_tasks=[t['id']])
        self.assertEqual(self.s.get(self.owner,t['id'])['state'],'stopped')
        self.assertFalse(self.s.commit(self.owner,t['id'],claim['fence'],claim['generation'],execute_fixture(claim)))

    async def test_clarification_and_expired_execution_need_fresh_approval(self):
        t=self.proposed(fixture=None)
        with self.assertRaises(ValueError):self.s.clarify(self.owner,t['id'],'bad',t['revision'],'/Users/private')
        t=self.s.clarify(self.owner,t['id'],'clarify',t['revision'],'range-total-two')
        with self.assertRaises(ValueError):self.s.clarify(self.owner,t['id'],'stale',1,'range-total')
        t=self.s.approve(self.owner,t['id'],'approve',t['revision'],t['scope_digest'],t['nonce'])
        self.now[0]+=301;self.assertFalse(await TaskWorker(self.s,self.owner).step(t['id']))
        t=self.s.get(self.owner,t['id']);self.assertEqual(t['state'],'awaiting_approval')
        self.assertEqual(t['evidence'],[])
        t=self.s.approve(self.owner,t['id'],'fresh-approve',t['revision'],t['scope_digest'],t['nonce'])
        await TaskWorker(self.s,self.owner).run(t['id']);self.assertEqual(self.s.get(self.owner,t['id'])['evidence'][-1]['output']['actual'],14)

    async def test_fixture_or_executor_drift_requires_new_scope(self):
        t=self.approved()
        with patch.dict(FIXTURES, {'range-total':{**FIXTURES['range-total'],'stop':5,'expected':15}}):
            self.assertIsNone(self.s.claim(self.owner,t['id'],'worker'))
            changed=self.s.get(self.owner,t['id'])
            self.assertEqual(changed['state'],'awaiting_approval');self.assertNotEqual(changed['scope_digest'],t['scope_digest'])
            self.assertEqual(changed['evidence'],[])
            with self.assertRaises(ValueError):self.s.approve(self.owner,t['id'],'old',t['revision'],t['scope_digest'],t['nonce'] or 'x')
        with patch('app.task_slice.EXECUTOR_DIGEST','new-code'):
            t2=self.proposed(action='second')
            self.s.approve(self.owner,t2['id'],'second-approval',t2['revision'],t2['scope_digest'],t2['nonce'])
        self.assertIsNone(self.s.claim(self.owner,t2['id'],'changed-worker'))
        self.assertEqual(self.s.get(self.owner,t2['id'])['state'],'awaiting_approval')

    async def test_expired_worker_exception_cannot_finish_task(self):
        t=self.approved();claim=self.s.claim(self.owner,t['id'],'old',10)
        self.now[0]+=11
        self.s.failed(self.owner,t['id'],claim['fence'],claim['generation'],'LateError')
        self.assertEqual(self.s.get(self.owner,t['id'])['state'],'running')
        self.s.recover(self.owner);await TaskWorker(self.s,self.owner).run(t['id'])
        self.assertEqual(self.s.get(self.owner,t['id'])['state'],'ready_for_review')

    async def test_operation_exception_is_terminal_without_private_error_text(self):
        t=self.approved()
        with patch('app.task_slice.execute_fixture',side_effect=RuntimeError('PRIVATE_PAYLOAD')):
            await TaskWorker(self.s,self.owner).run(t['id'])
        t=self.s.get(self.owner,t['id']);self.assertEqual(t['state'],'failed')
        self.assertNotIn('PRIVATE_PAYLOAD',str(t));self.assertEqual(t['evidence'],[])


class ApiTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'demo.db'
    def tearDown(self):self.temp.cleanup()
    def client(self, app, host='127.0.0.1'):return TestClient(app,client=(host,50000))

    def test_demo_disabled_remote_origin_owner_and_commands_fail_closed(self):
        app=create_demo_app(self.path)
        with self.client(app) as c:self.assertEqual(c.get('/api/tasks').status_code,503)
        app=create_demo_app(self.path,enabled=True)
        with self.client(app,'203.0.113.1') as c:self.assertEqual(c.get('/api/tasks').status_code,403)
        with self.client(app) as c:
            payload={'action_id':'start','goal':'fixture','fixture':'range-total'}
            self.assertEqual(c.post('/api/tasks',json=payload).status_code,403)
            headers={'X-Friday-Fixture':'1','Origin':'https://untrusted.example'}
            self.assertEqual(c.post('/api/tasks',json=payload,headers=headers).status_code,403)
            headers={'X-Friday-Fixture':'1'}
            self.assertEqual(c.post('/api/tasks',json={**payload,'owner':'other'},headers=headers).status_code,422)
            self.assertEqual(c.post('/api/tasks',json={**payload,'command':'rm -rf'},headers=headers).status_code,422)
            self.assertEqual(c.get('/api/tasks',headers={'Host':'untrusted.example'}).status_code,403)

    def test_only_one_supervisor_can_assert_fixture_drain(self):
        one=create_demo_app(self.path,enabled=True);two=create_demo_app(self.path,enabled=True)
        with self.client(one):
            with self.assertRaisesRegex(RuntimeError,'one fixture supervisor'):
                with self.client(two):pass
        with self.client(two) as restarted:self.assertEqual(restarted.get('/api/tasks').status_code,200)

    def test_two_client_progress_dialogue_and_restart_same_task(self):
        app=create_demo_app(self.path,enabled=True);headers={'X-Friday-Fixture':'1'}
        with self.client(app) as phone:
            t=phone.post('/api/tasks',headers=headers,json={'action_id':'start','goal':'fixture','fixture':'range-total'}).json()
            a=phone.post(f"/api/tasks/{t['id']}/approve",headers=headers,json={'action_id':'approve','revision':t['revision'],'scope_digest':t['scope_digest'],'nonce':t['nonce']});self.assertEqual(a.status_code,200)
            # Clients disconnect without cancelling the durable task. Deterministic
            # worker proves completion here; browser rendering is a separate check.
            import time
            for _ in range(100):
                task=phone.get(f"/api/tasks/{t['id']}").json()
                if task['state']=='ready_for_review':break
                time.sleep(.01)
            self.assertEqual(task['state'],'ready_for_review')
            phone.post(f"/api/tasks/{t['id']}/conversation",headers=headers,json={'action_id':'phone-turn','client':'Phone','text':'What failed?'})
        restarted=create_demo_app(self.path,enabled=True)
        with self.client(restarted) as device:
            same=device.get(f"/api/tasks/{t['id']}").json();self.assertEqual(len(same['evidence']),5)
            answer=device.post(f"/api/tasks/{t['id']}/conversation",headers=headers,json={'action_id':'device-turn','client':'Device text harness','text':'What changed?'}).json()
            self.assertIn('include the final',answer['answer'])
            same=device.get(f"/api/tasks/{t['id']}").json();self.assertEqual(len(same['conversation']),2)
            self.assertEqual(device.get('/').status_code,200)
