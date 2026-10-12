import asyncio
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from app.agent import FridayAgent
from app.agent_jobs import AgentJobStore
from app.config import Settings
from app.db import ReminderStore
from app.device_auth import DeviceCredentialStore
from app.device_hub import DeviceHub
from app.experience import CAPABILITY
from app.providers.base import ProviderTurn,ToolCall
from app.report_runtime import create_report_runtime
from app.speech import SpeechDirector
from test_task_telegram_transport import FakeHTTP

class FixtureLocal:
    local_only=True;name='original-integrated-local-fixture'
    def __init__(self,*args,**kwargs):self.calls=0
    async def complete(self,*args):
        self.calls+=1
        return ProviderTurn(tool_calls=[ToolCall('original-integration','propose_project_report',{'capability':'project-report-v1','sections':['inventory','readme','source_statistics']})])
    async def ensure_available(self,*args,**kwargs):pass

class IntegratedReports(unittest.TestCase):
    provider_type=FixtureLocal
    model_name='original-fixture:local'
    review_polls=100
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        source=self.root/'source';source.mkdir();(source/'README.md').write_text('Original integrated synthetic project.\n')
        artifacts=self.root/'artifacts';artifacts.mkdir(mode=0o700)
        enrollment={'version':1,'state_path':str(self.root/'reports.db'),'artifact_root':str(artifacts),'device_owners':{'synthetic-p4':'original-owner','other-p4':'other-owner'},'projects':[{'owner':'original-owner','alias':'demo','root':str(source),'files':['README.md']},{'owner':'other-owner','alias':'other','root':str(source),'files':['README.md']}]}
        self.enrollment=self.root/'enrollment.json';self.enrollment.write_text(json.dumps(enrollment));self.enrollment.chmod(0o600)
        self.settings=Settings(_env_file=None,assistant_env='test',assistant_provider='local-only',ollama_model=self.model_name,assistant_reports_enabled=True,assistant_reports_enrollment_path=self.enrollment,assistant_reports_validation_root=self.root,assistant_database_path=self.root/'backend.db',assistant_admin_token='f'*64,stt_provider='none',tts_provider='none',calendar_provider='none')
        self.configure_settings()
        self.store=ReminderStore(self.settings.assistant_database_path);self.credentials=DeviceCredentialStore(self.settings.assistant_database_path);self.jobs=AgentJobStore(self.settings.assistant_database_path);self.hub=DeviceHub();self.agent=FridayAgent(self.settings,self.store);self.http=FakeHTTP()
        async def factory(*args):
            runtime=await create_report_runtime(*args)
            if runtime and not self.settings.assistant_report_telegram_enabled:await runtime.attach_mock_telegram(self.http,[(100,100,'original-owner')])
            return runtime
        self.stack=ExitStack()
        for target,value in [('settings',self.settings),('store',self.store),('credential_store',self.credentials),('job_store',self.jobs),('hub',self.hub),('agent',self.agent),('transcriber',None),('speech',SpeechDirector(self.hub,None)),('create_report_runtime',factory)]:self.stack.enter_context(patch('app.main.'+target,value))
        self.stack.enter_context(patch('app.report_runtime.OllamaProvider',self.provider_type))
        for target,value in [('store',self.jobs),('hub',self.hub),('agent',self.agent)]:self.stack.enter_context(patch.object(main.job_worker,target,value))
        self.stack.enter_context(patch.object(main.reminder_scheduler,'store',self.store));self.stack.enter_context(patch.object(main.reminder_scheduler,'hub',self.hub))
        main.scene_navigation.clear();main.local_music.protected.clear();main.local_music.reminder_devices.clear();main.local_music.busy.clear()
        self.client=self.stack.enter_context(TestClient(main.app,client=('127.0.0.1',45000)))
    def configure_settings(self):pass
    def tearDown(self):self.stack.close();self.temp.cleanup()
    def authenticate(self,ws,device='synthetic-p4',caps=None):
        self.assertEqual(ws.receive_json()['type'],'hello.required')
        token=('a' if device=='synthetic-p4' else 'b')*64
        if not self.credentials.is_paired(device):self.credentials.open_pairing_window(device,token[:8])
        ws.send_json({'type':'hello','device_token':token,'capabilities':caps if caps is not None else [CAPABILITY,'result_ack']})
        ready=ws.receive_json();self.assertEqual(ready['type'],'ready')
        if caps==[]:ws.receive_json()
        return ready
    def until(self,ws,predicate):
        for _ in range(30):
            packet=ws.receive_json()
            if predicate(packet):return packet
            if packet['type'] in {'error','experience.rejected','experience.input_error'}:self.fail('Unexpected protocol rejection: '+str(packet))
        self.fail('Expected protocol packet not observed')
    def review(self,task):
        for _ in range(self.review_polls):
            response=self.client.get('/api/devices/synthetic-p4/report-tasks/'+task,headers={'x-friday-admin-token':'f'*64})
            if response.status_code==200 and response.json()['state']=='awaiting_approval':return response.json()
            self.client.portal.call(asyncio.sleep,.01)
        self.fail('No exact-scope review')
    def approve(self,task,review):
        response=self.client.post('/api/devices/synthetic-p4/report-tasks/'+task+'/approve',headers={'x-friday-admin-token':'f'*64,'x-friday-report-review':'approve'},json={'revision':review['revision'],'scope_digest':review['scope_digest'],'nonce':review['nonce'],'action_id':'original-integrated-approval'})
        self.assertEqual(response.status_code,200);return response.json()
    def create(self,ws):
        ws.send_json({'type':'utterance','request_id':'original-report-start','text':'report demo Prepare a read-only project report'})
        accepted=self.until(ws,lambda p:p['type']=='job.accepted');return accepted['job_id']
    def test_actual_dispatcher_result_ack_reconnect_recovery_no_repeat(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws);task=self.create(ws);review=self.review(task)
            with self.jobs._connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_jobs').fetchone()[0],0)
            self.approve(task,review);result=self.until(ws,lambda p:p['type']=='agent.result')
            self.assertEqual(result['job_id'],task);self.assertEqual(result['status'],'completed')
            self.assertEqual(result['experience_id'],'original-report-start')
            artifact=main.report_runtime.store.get('original-owner',task)['artifact'];self.assertEqual(hashlib.sha256((self.root/'artifacts'/artifact['name']).read_bytes()).hexdigest(),artifact['sha256'])
            self.saved_result=result
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            ready=self.authenticate(ws);self.assertTrue(ready['review_pending'])
            ws.send_json({'type':'scene.action','request_id':'original-recover','action':'recover'})
            replay=self.until(ws,lambda p:p['type']=='agent.result')
            self.assertEqual(replay['delivery_id'],result['delivery_id']);self.assertEqual(replay['job_id'],task)
            ws.send_json({'type':'result.ack','job_id':task,'delivery_id':replay['delivery_id']})
            ws.send_json({'type':'scene.action','request_id':'original-recover-acked','action':'recover'})
            restored=self.until(ws,lambda p:p['type']=='surface' and p.get('job_id')==task)
            self.assertNotIn('delivery_id',restored)
            self.assertEqual(len(list((self.root/'artifacts').iterdir())),1)
    def test_device_stop_actual_action_schema_same_task(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws);task=self.create(ws);self.review(task)
            surface=self.until(ws,lambda p:p['type']=='surface' and p['surface']['subtitle'].startswith('State: awaiting_approval'))
            stop=next(action['id'] for action in surface['surface']['actions'] if action['label']=='Stop')
            ws.send_json({'type':'scene.action','request_id':'original-native-stop','action':stop})
            result=self.until(ws,lambda p:p['type']=='agent.result')
            self.assertEqual(result['job_id'],task);self.assertEqual(result['report_state'],'stopped');self.assertEqual(result['status'],'failed')
            self.assertEqual(list((self.root/'artifacts').iterdir()),[])
    def test_mock_telegram_and_authenticated_device_share_job(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws);task=self.create(ws);review=self.review(task)
            self.client.portal.call(main.report_runtime.transport.process_update,{'update_id':1,'message':{'from':{'id':100,'is_bot':False},'chat':{'id':100,'type':'private'},'text':'/status '+task}})
            self.assertEqual(len(main.report_runtime.store.list('original-owner')),1)
            self.approve(task,review);result=self.until(ws,lambda p:p['type']=='agent.result')
            self.assertEqual(result['job_id'],task)
            self.client.portal.call(main.report_runtime.transport.process_update,{'update_id':2,'message':{'from':{'id':100,'is_bot':False},'chat':{'id':100,'type':'private'},'text':'/ask '+task+' status'}})
            turns=main.report_runtime.store.turns('original-owner',task);self.assertIn('ready_for_review',turns[-1]['answer'])
    def test_unpaired_crossowner_and_missing_protocol_denied(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            ws.receive_json();ws.send_json({'type':'utterance','text':'report demo Prepare report','request_id':'denied'})
            self.assertEqual(ws.receive_json()['code'],'authentication_required')
            ws.send_json({'type':'hello','device_token':'a'*64,'capabilities':[CAPABILITY,'result_ack']})
            self.assertIn(ws.receive_json()['type'],{'error','experience.input_error'})
        with self.client.websocket_connect('/ws/device/other-p4') as ws:
            self.authenticate(ws,'other-p4');ws.send_json({'type':'utterance','request_id':'wrong-owner','text':'report demo Prepare report'})
            denied=self.until(ws,lambda p:p['type'] in {'error','experience.rejected','experience.input_error'});self.assertEqual(denied['code'],'request_conflict')
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws,caps=[]);ws.send_json({'type':'utterance','request_id':'wrong-protocol','text':'report demo Prepare report'})
            self.assertEqual(self.until(ws,lambda p:p['type']=='error')['code'],'request_conflict')
        self.assertEqual(main.report_runtime.store.list('original-owner'),[])
    def test_local_admin_fullscope_auth_and_stale_nonce(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws);task=self.create(ws);review=self.review(task)
            self.assertEqual(self.client.get('/api/devices/synthetic-p4/report-tasks/'+task).status_code,401)
            bad={k:review[k] for k in ['revision','scope_digest','nonce']};bad.update(action_id='original-stale',nonce='0'*32)
            self.assertEqual(self.client.post('/api/devices/synthetic-p4/report-tasks/'+task+'/approve',headers={'x-friday-admin-token':'f'*64,'x-friday-report-review':'approve'},json=bad).status_code,409)
            self.assertEqual(list((self.root/'artifacts').iterdir()),[])
    def test_default_real_project_enrollment_is_disabled(self):
        self.assertFalse(Settings(_env_file=None,stt_provider='none').assistant_reports_enabled)
        self.assertFalse(Settings(_env_file=None,stt_provider='none').assistant_reports_allow_real_projects)

    def test_repeated_recovery_reuses_stop_control(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws);task=self.create(ws);self.review(task)
            controls=[main.report_runtime.bridge.scene('synthetic-p4',task).actions[0].id for _ in range(100)]
            self.assertEqual(len(set(controls)),1)
            self.assertEqual(len(main.report_runtime.bridge.tokens),1)
            self.assertTrue(main.report_runtime.ready)
    def test_expired_admin_review_refreshes_nonce(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws);task=self.create(ws);first=self.review(task)
            with patch.object(main.report_runtime.store,'clock',return_value=first['expires_at']+1):
                response=self.client.get('/api/devices/synthetic-p4/report-tasks/'+task,headers={'x-friday-admin-token':'f'*64})
                self.assertEqual(response.status_code,200);fresh=response.json()
            self.assertNotEqual(first['nonce'],fresh['nonce'])
            self.assertGreater(fresh['revision'],first['revision'])
            self.assertEqual(fresh['scope']['manifest'],first['scope']['manifest'])
    def test_unavailable_supervisor_rejects_admission(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws)
            with patch.object(main.report_runtime,'ready',False):
                ws.send_json({'type':'utterance','request_id':'unavailable','text':'report demo Prepare report'})
                denied=self.until(ws,lambda p:p['type']=='error')
                self.assertEqual(denied['code'],'request_conflict')
            self.assertEqual(main.report_runtime.store.list('original-owner'),[])
    def test_state_symlink_and_hardlink_rejected(self):
        from app.project_report import ReportStore,ProjectGrant
        other=self.root/'outside.db';other.write_text('original untouched');other.chmod(0o600)
        linked=self.root/'linked.db';linked.symlink_to(other)
        grant=ProjectGrant('original-owner','demo',self.root/'source',('README.md',))
        with self.assertRaises(ValueError):ReportStore(linked,[grant],self.root/'artifacts')
        linked.unlink();linked.hardlink_to(other)
        with self.assertRaises(ValueError):ReportStore(linked,[grant],self.root/'artifacts')
        self.assertEqual(other.read_text(),'original untouched')

    def test_admin_expiry_refresh_races_other_surface_safely(self):
        with self.client.websocket_connect('/ws/device/synthetic-p4') as ws:
            self.authenticate(ws);task=self.create(ws);first=self.review(task)
            store=main.report_runtime.store;original=store.review;admitted=[]
            def race(owner,task_id,action,revision):
                if action.startswith('admin-review:') and not admitted:
                    admitted.append(True);original(owner,task_id,'original-concurrent-review',revision)
                return original(owner,task_id,action,revision)
            with patch.object(store,'clock',return_value=first['expires_at']+1),patch.object(store,'review',side_effect=race):
                fresh=main.report_runtime.review('original-owner',task)
            self.assertNotEqual(fresh['nonce'],first['nonce'])
            self.assertGreater(fresh['revision'],first['revision'])
            self.assertEqual(fresh['scope']['manifest'],first['scope']['manifest'])
