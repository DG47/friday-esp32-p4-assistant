import asyncio
from contextlib import closing
import threading
import unittest
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from app import main
from app.models import AgentReply,Surface
from app.execution_stop import CAPABILITY
import test_device_socket as fixture

class ExecutionStopSocket(unittest.TestCase):
    setUp=fixture.DeviceSocketTests.setUp
    tearDown=fixture.DeviceSocketTests.tearDown
    def packet(self,ws,kind):
        for _ in range(24):
            packet=ws.receive_json()
            if packet['type']==kind:return packet
        self.fail('Expected '+kind)
    def auth(self,ws,device):
        fixture.DeviceSocketTests.authenticate(self,ws,device,[CAPABILITY,'result_ack'])
    def test_scoped_queued_stop_status_and_readonly_navigation(self):
        job=self.job_store.enqueue('stop-device','Private original queued request','queued-socket')
        with patch.object(main.job_worker,'process_once',AsyncMock(return_value=False)):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/stop-device') as ws:
                self.auth(ws,'stop-device')
                ws.send_json({'type':'execution.stop','request_id':'stop-action','job_id':job.id,'execution_id':job.delivery_id})
                observed=self.packet(ws,'execution.stop.observed')
                self.assertTrue(observed['ack_confirmed']);self.assertTrue(observed['authority_revoked'])
                self.assertEqual(observed['state_at_ack'],'stopped_before_admission')
                ws.send_json({'type':'execution.status','request_id':'read-action','job_id':job.id,'execution_id':job.delivery_id})
                status=self.packet(ws,'execution.stop.status')
                self.assertTrue(status['no_execution_admitted']);self.assertNotIn('Private original',str(status))
                ws.send_json({'type':'scene.action','request_id':'read-reminders','action':'reminders'})
                self.assertIn('reminders',self.packet(ws,'surface')['surface']['title'].lower())
        self.assertEqual(self.job_store.get(job.id).attempts,0)
    def test_other_device_cannot_revoke_target(self):
        job=self.job_store.enqueue('private-device','Private original effect','private-job')
        with patch.object(main.job_worker,'process_once',AsyncMock(return_value=False)):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/other-device') as ws:
                self.auth(ws,'other-device')
                ws.send_json({'type':'execution.stop','request_id':'wrong-owner','job_id':job.id,'execution_id':job.delivery_id})
                result=self.packet(ws,'execution.stop.observed')
                self.assertFalse(result['ack_confirmed']);self.assertFalse(result['authority_revoked'])
        self.assertEqual(self.job_store.get(job.id).status,'queued')
    def test_accepted_incarnation_stop_and_late_result_do_not_claim_success(self):
        entered,release=threading.Event(),threading.Event()
        class Resistant:
            async def handle(self,*args,**kwargs):
                entered.set()
                while not release.is_set():
                    try:await asyncio.sleep(.005)
                    except asyncio.CancelledError:pass
                return AgentReply(text='Obsolete synthetic completion',surface=Surface(kind='message',title='Synthetic'))
        with patch.object(main.job_worker,'agent',Resistant()):
            try:
                with TestClient(main.app) as client,client.websocket_connect('/ws/device/running-stop') as ws:
                    self.auth(ws,'running-stop')
                    ws.send_json({'type':'utterance','request_id':'slow-request','text':'Original slow request'})
                    accepted=self.packet(ws,'job.accepted')
                    self.assertEqual(len(accepted['execution_id']),32);self.assertTrue(entered.wait(1))
                    ws.send_json({'type':'execution.stop','request_id':'stop-slow','job_id':accepted['job_id'],'execution_id':accepted['execution_id']})
                    result=self.packet(ws,'execution.stop.observed')
                    self.assertTrue(result['authority_revoked']);self.assertFalse(result['handler_drained'])
                    self.assertEqual(result['state_at_ack'],'outcome_uncertain')
                    release.set()
                    ws.send_json({'type':'execution.status','request_id':'read-slow','job_id':accepted['job_id'],'execution_id':accepted['execution_id']})
                    status=self.packet(ws,'execution.stop.status')
                    self.assertTrue(status['outcome_uncertain']);self.assertFalse(status['no_execution_admitted'])
            finally:release.set()

    def test_exact_recognized_stop_uses_explicit_captured_target_not_a_new_job(self):
        class StopTranscriber:
            name='synthetic-stop-recognizer'
            async def transcribe(self,*args):return 'Stop'
        job=self.job_store.enqueue('voice-stop','Original queued voice target','voice-target')
        with patch.object(main.job_worker,'process_once',AsyncMock(return_value=False)), patch.object(main,'transcriber',StopTranscriber()):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/voice-stop') as ws:
                self.auth(ws,'voice-stop')
                ws.send_json({'type':'activate','request_id':'voice-stop-action'})
                self.packet(ws,'state')
                ws.send_json({'type':'audio.start','format':'pcm_s16le','sample_rate':16000,'channels':1,'execution_target':{'job_id':job.id,'execution_id':job.delivery_id}})
                self.packet(ws,'state');ws.send_bytes(b'\0\0'*3200);ws.send_json({'type':'audio.end'})
                observed=self.packet(ws,'execution.stop.observed')
                self.assertEqual(observed['request_id'],'voice-stop-action')
                self.assertEqual(observed['job_id'],job.id)
                self.assertEqual(observed['state_at_ack'],'stopped_before_admission')
        with closing(self.job_store._connect()) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_jobs').fetchone()[0],1)

    def test_reconciliation_preview_is_admin_readonly_and_never_offers_replay(self):
        job=self.job_store.enqueue('preview-device','Private synthetic effect text','preview-job')
        self.job_store.mark_execution_uncertain(self.job_store.claim_next())
        with patch.object(main.settings,'assistant_admin_token','f'*64):
            with TestClient(main.app,client=('127.0.0.1',45000)) as client,client.websocket_connect('/ws/device/preview-device') as ws:
                self.auth(ws,'preview-device')
                url='/api/devices/preview-device/executions/'+job.id+'/'+job.delivery_id+'/reconciliation'
                self.assertEqual(client.get(url).status_code,401)
                response=client.get(url,headers={'x-friday-admin-token':'f'*64})
                self.assertEqual(response.status_code,200);preview=response.json()
                self.assertTrue(preview['needs_effect_evidence'])
                self.assertFalse(preview['automatic_resolution_available']);self.assertFalse(preview['replay_allowed'])
                self.assertNotIn('Private synthetic',str(preview))
                self.assertEqual(client.post(url,json={'resolve':True},headers={'x-friday-admin-token':'f'*64}).status_code,405)
        self.assertEqual(self.job_store.get(job.id).error,'interrupted_execution_outcome_uncertain')
