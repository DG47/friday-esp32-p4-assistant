import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.experience import CAPABILITY
import test_device_socket

class ExperienceSocketTests(unittest.TestCase):
    setUp=test_device_socket.DeviceSocketTests.setUp
    tearDown=test_device_socket.DeviceSocketTests.tearDown

    def packet(self,socket,kind):
        for _ in range(12):
            packet=socket.receive_json()
            if packet['type']==kind:return packet
            self.assertIn(packet['type'],{'experience.finished','experience.recovery.available'})
        self.fail('Expected '+kind)

    def auth(self,socket,device):
        self.assertEqual(socket.receive_json()['type'],'hello.required')
        token='a'*64;self.credential_store.open_pairing_window(device,token[:8])
        socket.send_json({'type':'hello','capabilities':[CAPABILITY],'device_token':token})
        return self.packet(socket,'ready')

    def test_recover_reject_dismiss_cancel_and_reconnect_without_private_replay(self):
        from app import main
        device='experience-recovery'
        with patch.object(main.agent.memory,'store',None):
            with TestClient(app) as client:
                with client.websocket_connect('/ws/device/'+device) as socket:
                    ready=self.auth(socket,device);epoch=ready['experience_epoch']
                    pending=self.reminder_store.request_confirmation('synthetic-confirm',device,'synthetic-request',[], 'Complete the original synthetic reminder?',datetime.now(timezone.utc)+timedelta(minutes=5))
                    socket.send_json({'type':'scene.action','action':'recover','request_id':'review'})
                    surface=self.packet(socket,'surface')
                    self.assertEqual(surface['experience_id'],'review');self.assertIn('synthetic reminder',surface['surface']['subtitle'])
                    for i in range(3):
                        socket.send_json({'type':'scene.action','action':'dismiss','request_id':'dismiss-'+str(i)})
                        denied=self.packet(socket,'experience.rejected')
                        self.assertEqual(denied['experience_previous'],'review');self.assertEqual(main.hub.experience.current[device].request,'review')
                        self.assertEqual(self.reminder_store.pending_confirmation(device).id,pending.id)
                with client.websocket_connect('/ws/device/'+device) as socket:
                    ready=self.auth(socket,device)
                    self.assertNotEqual(ready['experience_epoch'],epoch);self.assertTrue(ready['review_pending']);self.assertNotIn('synthetic reminder',str(ready))
                    socket.send_json({'type':'scene.action','action':'recover','request_id':'fresh-review'})
                    surface=self.packet(socket,'surface');self.assertEqual(surface['experience_id'],'fresh-review')
                    action='reqcancel:'+hashlib.sha256(pending.id.encode()).hexdigest()[:24]
                    socket.send_json({'type':'scene.action','action':action,'request_id':'explicit-cancel'})
                    self.packet(socket,'surface');self.assertIsNone(self.reminder_store.pending_confirmation(device))

    def test_empty_stt_clears_capture_busy_and_preserves_review(self):
        from app import main
        from unittest.mock import AsyncMock
        device='empty-stt-cleanup'
        fake=type('EmptySTT',(),{'name':'offline-empty','transcribe':AsyncMock(return_value='')})()
        with patch.object(main,'transcriber',fake),patch.object(main.agent.memory,'store',None):
            with TestClient(app) as client:
                with client.websocket_connect('/ws/device/'+device) as socket:
                    self.auth(socket,device)
                    pending=self.reminder_store.request_confirmation('synthetic-empty-review',device,'synthetic-request',[], 'Keep the original synthetic review.',datetime.now(timezone.utc)+timedelta(minutes=5))
                    for index in range(2):
                        socket.send_json({'type':'activate','request_id':'empty-turn-'+str(index)})
                        self.assertEqual(self.packet(socket,'state')['state'],'listening')
                        socket.send_json({'type':'audio.start','format':'pcm_s16le','sample_rate':12000,'channels':1})
                        self.assertEqual(self.packet(socket,'state')['state'],'listening')
                        socket.send_bytes(b'\0'*6000)
                        socket.send_json({'type':'audio.end'})
                        self.assertEqual(self.packet(socket,'state')['state'],'thinking')
                        self.assertEqual(self.packet(socket,'error')['code'],'transcription_failed')
                        self.assertEqual(self.packet(socket,'state')['state'],'idle')
                        self.assertNotIn(device,main.local_music.busy)
                        self.assertFalse(self.job_store.active_for(device))
                        self.assertEqual(self.reminder_store.pending_confirmation(device).id,pending.id)
                    socket.send_json({'type':'scene.action','action':'recover','request_id':'recover-after-empty'})
                    surface=self.packet(socket,'surface')
                    self.assertIn('original synthetic review',surface['surface']['subtitle'])
                    self.assertIsNotNone(self.reminder_store.pending_confirmation(device))
        self.assertEqual(fake.transcribe.await_count,2)
