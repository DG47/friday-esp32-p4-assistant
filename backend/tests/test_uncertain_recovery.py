"""Restarted uncertain jobs are visible, never accepted as new executable work."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.agent_jobs import AgentJobStore,UncertainExecutionPending
from app.db import ReminderStore
from app.experience import CAPABILITY
from app.main import app
import test_experience_socket as experience_fixture
import test_device_socket as device_fixture


class UncertainStore(unittest.TestCase):
    def test_restart_rejects_same_device_admission_without_replaying_or_disclosing_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.db';ReminderStore(path);store=AgentJobStore(path)
            job=store.enqueue('device','Private original synthetic action',request_id='original')
            claim=store.claim_next();store.mark_execution_uncertain(claim)
            reopened=AgentJobStore(path)
            for identity in ('new','original'):
                with self.assertRaises(UncertainExecutionPending) as error:
                    reopened.enqueue('device','Private original synthetic action',request_id=identity)
                self.assertNotIn('Private original',str(error.exception))
            self.assertTrue(reopened.uncertain_for('device'))
            self.assertFalse(reopened.uncertain_for('other'))
            self.assertIsNone(reopened.get('new'));self.assertEqual(reopened.get(job.id),store.get(job.id))
            self.assertEqual(reopened.enqueue('other','Another synthetic task').device_id,'other')


class UncertainSocket(unittest.TestCase):
    setUp=experience_fixture.ExperienceSocketTests.setUp
    tearDown=experience_fixture.ExperienceSocketTests.tearDown
    packet=experience_fixture.ExperienceSocketTests.packet
    auth=experience_fixture.ExperienceSocketTests.auth
    def test_reconnect_recovery_and_admission_preserve_uncertain_row_without_private_replay(self):
        from app import main
        job=self.job_store.enqueue('uncertain-device','Private original synthetic action',request_id='original')
        self.job_store.mark_execution_uncertain(self.job_store.claim_next())
        with patch.object(main.agent.memory,'store',None):
            with TestClient(app) as client:
                with client.websocket_connect('/ws/device/uncertain-device') as socket:
                    ready=self.auth(socket,'uncertain-device')
                    self.assertTrue(ready['review_pending']);self.assertNotIn('Private original',str(ready))
                    socket.send_json({'type':'scene.action','action':'recover','request_id':'recover-uncertain'})
                    packet=self.packet(socket,'surface');surface=packet['surface']
                    self.assertEqual(surface['title'],'Outcome unconfirmed')
                    self.assertIn('not replayed',surface['subtitle'])
                    self.assertNotIn('Private original',str(surface));self.assertEqual(surface['actions'],[])
                    socket.send_json({'type':'utterance','text':'Another synthetic task','request_id':'new-task'})
                    rejected=self.packet(socket,'experience.rejected')
                    self.assertEqual(rejected['code'],'execution_outcome_uncertain')
                    self.assertIsNone(self.job_store.get('new-task'))
                    self.assertEqual(self.job_store.get(job.id).status,'running')

    def test_voice_rejection_keeps_socket_open_and_does_not_enqueue(self):
        from app import main
        self.job_store.enqueue('uncertain-voice','Private synthetic effect',request_id='original-voice')
        self.job_store.mark_execution_uncertain(self.job_store.claim_next())
        with patch.object(main.agent.memory,'store',None), patch.object(main,'transcriber',device_fixture.FakeTranscriber()), patch.object(main.settings,'assistant_voice_diagnostic_only',False):
            with TestClient(app) as client:
                with client.websocket_connect('/ws/device/uncertain-voice') as socket:
                    device_fixture.DeviceSocketTests.authenticate(self,socket,'uncertain-voice')
                    socket.send_json({'type':'audio.start','format':'pcm_s16le','sample_rate':16000,'channels':1})
                    self.assertEqual(socket.receive_json()['state'],'listening')
                    socket.send_bytes(b'\x00\x00'*3200);socket.send_json({'type':'audio.end'})
                    packets=[]
                    for _ in range(8):
                        packet=socket.receive_json();packets.append(packet)
                        if packet.get('code')=='execution_outcome_uncertain':break
                    self.assertEqual(packets[-1].get('code'),'execution_outcome_uncertain')
                    self.assertFalse(any(p['type']=='job.accepted' for p in packets))
                    socket.send_json({'type':'unsupported'})
                    self.assertEqual(socket.receive_json().get('code'),'bad_message')
                    with closing(self.job_store._connect()) as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_jobs').fetchone()[0],1)
