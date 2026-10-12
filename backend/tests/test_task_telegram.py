import asyncio
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from app.task_slice import TaskStore, TaskWorker
from app.task_telegram import TelegramFixtureAdapter


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000]
        self.store = TaskStore(Path(self.temp.name)/'tasks.db', lambda:self.now[0])
        self.adapter = TelegramFixtureAdapter(self.store)
        self.adapter.bind_verified(7,7,'owner')

    def tearDown(self): self.temp.cleanup()

    def update(self, number, text=None, token=None, user=7):
        message = {'chat': {'id':user,'type':'private'},'from':{'id':user,'is_bot':False}}
        if token is not None:
            return {'update_id':number,'callback_query':{'from':message['from'],'message':message,'data':token}}
        message['text'] = text
        return {'update_id':number,'message':message}

    def test_operational_help_tasks_and_unambiguous_bare_stop(self):
        self.assertIn('/tasks',self.adapter.handle(self.update(10,'/help'))['text'])
        self.assertIn('No tasks',self.adapter.handle(self.update(11,'/tasks'))['text'])
        task,_ = self.propose()
        self.assertIn(task['id'],self.adapter.handle(self.update(12,'/tasks'))['text'])
        self.assertIn('stopped',self.adapter.handle(self.update(13,'/stop'))['text'])

    def test_bare_stop_asks_selection_when_ambiguous(self):
        self.propose()
        self.adapter.handle(self.update(2,'/fixture range-total-two'))
        response = self.adapter.handle(self.update(3,'/stop'))
        self.assertIn('/stop ',response['text'])
        self.assertTrue(all(t['state']=='awaiting_approval' for t in self.store.list('owner')))

    def test_plain_scope_and_default_evidence_redaction(self):
        task, _ = self.propose()
        response = self.adapter.handle(self.update(2,'/status '+task['id']))
        self.assertIn('Network access: no',response['text'])
        self.assertNotIn('{"',response['text'])
        with self.store.db() as db:
            current=self.store._get(db,'owner',task['id'])
            current['evidence']=[{'step':'check','output':{'output':'SYNTHETIC_PRIVATE_RAW_OUTPUT','exit_code':1}}]
            self.store._save(db,'owner',current,'synthetic fixture evidence')
        response=self.adapter.handle(self.update(3,'/status '+task['id']))
        self.assertNotIn('SYNTHETIC_PRIVATE_RAW_OUTPUT',response['text'])

    def test_safe_error_response_preserves_binding(self):
        response=self.adapter.respond(self.update(15,'/unknown'))
        self.assertIn('/help',response['text'])
        self.assertEqual(response['delivery_binding']['owner'],'owner')

    def propose(self):
        response = self.adapter.handle(self.update(1,'/fixture range-total'))
        return self.store.list('owner')[0],response['buttons'][0]['callback_data']

    def test_shared_context_approval_duplicate_and_stop(self):
        task,token = self.propose()
        update = self.update(2,token=token)
        result = self.adapter.handle(update)
        self.assertIn('queued',result['text'])
        self.assertEqual(self.adapter.handle(update),result)
        self.assertEqual(TelegramFixtureAdapter(self.store).handle(update),result)
        self.assertEqual(self.store.get('owner',task['id'])['state'],'queued')
        answer = self.adapter.handle(self.update(3,'/ask '+task['id']+' What failed?'))
        self.assertTrue(answer['text'])
        self.assertEqual(self.store.turns('owner',task['id'])[0]['client'],'Telegram')
        self.assertIn('stopped',self.adapter.handle(self.update(4,'/stop '+task['id']))['text'])

    def test_owner_chat_and_private_binding(self):
        task,token = self.propose()
        with self.assertRaises(ValueError): self.adapter.handle(self.update(2,token=token,user=8))
        self.adapter.bind_verified(8,8,'other')
        with self.assertRaises(ValueError): self.adapter.handle(self.update(2,token=token,user=8))
        with self.assertRaises(ValueError): self.adapter.handle(self.update(2,'/status '+task['id'],user=8))
        group = self.update(3,'/fixture range-total')
        group['message']['chat']['type']='group'
        with self.assertRaises(ValueError): self.adapter.handle(group)
        with self.assertRaises(ValueError): self.adapter.bind_verified(7,-9,'owner')

    def test_expiry_and_changed_scope(self):
        task,token = self.propose()
        self.now[0] += 300
        with self.assertRaises(ValueError): self.adapter.handle(self.update(2,token=token))
        response = self.adapter.handle(self.update(3,'/review '+task['id']))
        fresh = response['buttons'][0]['callback_data']
        with self.assertRaises(ValueError): self.adapter.handle(self.update(4,token=token))
        self.assertIn('queued',self.adapter.handle(self.update(5,token=fresh))['text'])

    def test_revocation_including_duplicate_delivery(self):
        self.propose()
        self.adapter.revoke(7)
        with self.assertRaises(ValueError): self.adapter.handle(self.update(1,'/fixture range-total'))
        self.adapter.bind_verified(7,7,'different-owner')
        # Rebinding must not reveal a previously cached owner's response.
        with self.assertRaises(ValueError): self.adapter.handle(self.update(1,'/fixture range-total'))

    def test_conflicting_updates_and_unsupported_actions(self):
        task,token = self.propose()
        with self.assertRaises(ValueError): self.adapter.handle(self.update(1,'/fixture range-total-two'))
        with self.assertRaises(ValueError): self.adapter.handle(self.update(2,'/run rm -rf /'))
        self.assertIn(task['scope_digest'],self.adapter.handle(self.update(3,'/status '+task['id']))['text'])
        self.assertNotIn('token',self.adapter.handle(self.update(4,'/status '+task['id']))['text'])

    def test_stop_running_worker_blocks_artifact_commit(self):
        task,token = self.propose()
        self.adapter.handle(self.update(2,token=token))
        async def cancel_during_work(snapshot):
            response = self.adapter.handle(self.update(3,'/stop '+task['id']))
            self.assertIn('stopping',response['text'])
        asyncio.run(TaskWorker(self.store,'owner',cancel_during_work).run(task['id']))
        done = self.store.get('owner',task['id'])
        self.assertEqual(done['state'],'stopped')
        self.assertEqual(done['evidence'],[])
        self.assertIsNone(done['artifact'])

    def test_rebind_during_read_and_stale_delivery_denied(self):
        task,token = self.propose()
        other = TelegramFixtureAdapter(self.store)
        original = self.store.get
        def rebind(owner,task_id):
            result = original(owner,task_id)
            other.bind_verified(7,7,'other')
            return result
        with patch.object(self.store,'get',side_effect=rebind):
            with self.assertRaises(ValueError): self.adapter.handle(self.update(2,'/status '+task['id']))
        response = self.adapter.handle(self.update(3,'/fixture range-total'))
        fence = response['delivery_binding']
        other.revoke(7)
        with self.assertRaises(ValueError): self.adapter.authorize_delivery(**fence)

    def test_revocation_serialized_with_approval_across_instances(self):
        import threading
        task,token = self.propose()
        other = TelegramFixtureAdapter(self.store)
        admitted, release, revoked = threading.Event(),threading.Event(),threading.Event()
        original = self.store.approve
        def blocked(*args):
            admitted.set()
            self.assertTrue(release.wait(2))
            return original(*args)
        responses=[]
        with patch.object(self.store,'approve',side_effect=blocked):
            control=threading.Thread(target=lambda:responses.append(self.adapter.handle(self.update(2,token=token))))
            control.start()
            self.assertTrue(admitted.wait(2))
            revoke=threading.Thread(target=lambda:(other.revoke(7),revoked.set()))
            revoke.start()
            self.assertFalse(revoked.wait(.05))
            release.set()
            control.join(2); revoke.join(2)
        self.assertTrue(revoked.is_set())
        self.assertEqual(len(responses),1)
        with self.assertRaises(ValueError): self.adapter.authorize_delivery(**responses[0]['delivery_binding'])
        with self.assertRaises(ValueError): other.handle(self.update(2,token=token))
