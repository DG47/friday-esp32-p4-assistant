"""Shared domain integration: fake Telegram/provider, actual trusted fixed fixture."""
import sys
import tempfile
import unittest
from pathlib import Path
from app.task_local import LocalTaskStore,LocalTaskWorker
from app.task_planner import TaskPlanner
from app.task_telegram import TelegramFixtureAdapter
from app.task_telegram_transport import TelegramTransport
from app.providers.base import ProviderTurn,ToolCall
from app.task_slice import STEPS


class FixtureHTTP:
    offline_fixture=True
    def __init__(self):self.updates=[];self.calls=[]
    async def call(self,method,payload):
        self.calls.append((method,payload))
        if method=='getUpdates':return {'ok':True,'result':[u for u in self.updates if u['update_id']>=payload['offset']]}
        if method=='answerCallbackQuery':return {'ok':True,'result':True}
        return {'ok':True,'result':{'message_id':len(self.calls)}}


class InjectedProvider:
    name='injected-integration-fixture'
    async def complete(self,messages,instructions,tools):
        return ProviderTurn(tool_calls=[ToolCall('proposal','propose_registered_plan',
            {'repair':'inclusive-range-v1','operations':list(STEPS),'evidence_refs':[]})])


def update(number,text=None,token=None):
    message={'from':{'id':7,'is_bot':False},'chat':{'id':7,'type':'private'},'text':text or ''}
    return {'update_id':number, **({'callback_query':{'id':'fixture-callback-'+str(number),'from':message['from'],'message':message,'data':token}}
                                 if token else {'message':message})}


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').exists(), 'Trusted local execution integration requires macOS sandbox-exec')
class SharedIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_injected_plan_refresh_then_fake_delivery_real_fixture_execution(self):
        with tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[2]/'outputs')) as directory:
            root=Path(directory);store=LocalTaskStore(root/'tasks.db',root/'projects')
            adapter=TelegramFixtureAdapter(store);http=FixtureHTTP();service=TelegramTransport(adapter,http,enabled=True)
            await service.bind_verified(7,7,'owner')
            http.updates=[update(1,'/fixture range-total')];await service.poll_once()
            task=store.list('owner')[0]
            old_digest=task['scope_digest']
            plan=await TaskPlanner(store,provider=InjectedProvider(),enabled=True).propose('owner',task['id'],'plan')
            self.assertEqual(plan['state'],'ready_for_approval')
            self.assertNotEqual(store.get('owner',task['id'])['scope_digest'],old_digest)
            # First queued keyboard is stale and cannot grant authority.
            while await service.deliver_once():pass
            refreshed=[p for m,p in http.calls if m=='sendMessage' and 'reply_markup' in p]
            self.assertTrue(refreshed)
            self.assertTrue(any('injected-integration-fixture' in p['text'] for m,p in http.calls if m=='sendMessage'))
            http.updates.append(update(2,'/status '+task['id']));await service.poll_once()
            while await service.deliver_once():pass
            keyboard=[p for m,p in http.calls if m=='sendMessage' and 'reply_markup' in p][-1]
            token=keyboard['reply_markup']['inline_keyboard'][0][0]['callback_data']
            http.updates.append(update(3,token=token));await service.poll_once()
            await LocalTaskWorker(store,'owner').run(task['id'])
            done=store.get('owner',task['id'])
            self.assertEqual(done['state'],'ready_for_review')
            self.assertEqual(done['evidence'][1]['output']['exit_code'],1)
            self.assertEqual(done['evidence'][-1]['output']['exit_code'],0)
            self.assertEqual(len(store.list('owner')),1)
            # Duplicate delivery does not repeat task actions or inference.
            self.assertEqual(await service.poll_once(),0)
