import logging
import unittest
from unittest.mock import patch
from app.stt.capture_metadata import FIELDS, CaptureMetadataLog, validate_capture_metadata


def fixture():
    p={k:0 for k in FIELDS}
    p.update(seq=1,frames=50,samples=24000,stream_samples=12000,wall_ms=1000,clean=50,
             l_rms=1000,r_rms=1000,mono_rms=1000,post_aec_rms=1000,gain_rms=8000,
             stream_rms=8000,corr_valid=1,corr_ppm=1000000,age_ms=20)
    return p


class CaptureMetadataTests(unittest.TestCase):
    def test_valid_and_negative_correlation(self):
        p=fixture();self.assertEqual(validate_capture_metadata(p),p)
        p['corr_ppm']=-1000000;self.assertEqual(validate_capture_metadata(p),p)

    def test_unknown_content_and_invalid_numeric_values_rejected(self):
        for key,value in [('l_rms','PRIVATE_AUDIO'),('corr_ppm',1000001),('age_ms',3001),
                          ('frames',49),('samples',24001),('wall_ms',0),('clean',51),
                          ('r_rms',32769),('gain_clips',24001),('stream_clips',12001),
                          ('seq',True),('seq',10**1000),('seq',-1)]:
            with self.subTest(key=key,value=value):
                p=fixture();p[key]=value;self.assertIsNone(validate_capture_metadata(p))
        p=fixture();p['text']='PRIVATE_TRANSCRIPT';self.assertIsNone(validate_capture_metadata(p))
        p=fixture();del p['age_ms'];self.assertIsNone(validate_capture_metadata(p))
        for payload in [None,[], 'PRIVATE_TEXT', {'audio':b'private'}]:
            self.assertIsNone(validate_capture_metadata(payload))

    def test_fixed_schema_snapshot_copy_and_global_rate_bound(self):
        p=fixture();saved=validate_capture_metadata(p);p['seq']=99
        self.assertEqual(saved['seq'],1)
        recorder=CaptureMetadataLog()
        with patch('app.stt.capture_metadata.time.monotonic',side_effect=[0,1,4.99,5]),self.assertLogs('capture-fixture',level='INFO') as captured:
            for expected in [True,False,False,True]:
                self.assertEqual(recorder.record(logging.getLogger('capture-fixture'),saved),expected)
        self.assertEqual(len(captured.output),2)
        self.assertTrue(all(len(line)<768 and 'PRIVATE' not in line for line in captured.output))

    def test_invalid_metadata_cannot_consume_logging_slot(self):
        recorder=CaptureMetadataLog()
        with patch('app.stt.capture_metadata.time.monotonic',return_value=0),self.assertLogs('capture-fixture',level='INFO') as captured:
            self.assertFalse(recorder.record(logging.getLogger('capture-fixture'),{'text':'PRIVATE_TRANSCRIPT'}))
            self.assertTrue(recorder.record(logging.getLogger('capture-fixture'),fixture()))
        self.assertEqual(len(captured.output),1)


class CaptureSocketTests(unittest.TestCase):
    import test_device_socket as socket_fixture
    setUp = socket_fixture.DeviceSocketTests.setUp
    tearDown = socket_fixture.DeviceSocketTests.tearDown
    authenticate = socket_fixture.DeviceSocketTests.authenticate

    def test_unauthenticated_metadata_is_not_consumed(self):
        from app import main
        from fastapi.testclient import TestClient
        with patch.object(main.capture_metadata_log,'record') as record,TestClient(main.app) as client:
            with client.websocket_connect('/ws/device/capture-fixture') as socket:
                socket.receive_json()
                socket.send_json({'type':'audio.end','capture_diag':fixture()})
                self.assertEqual(socket.receive_json()['code'],'authentication_required')
                record.assert_not_called()

    def test_authenticated_active_turn_logs_only_numeric_fields(self):
        from app import main
        from fastapi.testclient import TestClient
        from test_transcription_diagnostics import FakeTranscriber
        with patch('app.main.transcriber',FakeTranscriber('')),patch('app.main.capture_metadata_log',CaptureMetadataLog()),TestClient(main.app) as client:
            with client.websocket_connect('/ws/device/capture-fixture') as socket:
                self.authenticate(socket,'capture-fixture')
                socket.send_json({'type':'audio.start','format':'pcm_s16le','sample_rate':12000,'channels':1})
                self.assertEqual(socket.receive_json()['state'],'listening')
                socket.send_bytes(b'\0\0'*2400)
                with self.assertLogs('uvicorn.error',level='INFO') as captured:
                    socket.send_json({'type':'audio.end','capture_diag':fixture()})
                    while socket.receive_json().get('state')!='idle':pass
                records=[line for line in captured.output if 'capture_diag ' in line]
                self.assertEqual(len(records),1)
                self.assertIn('l_rms=1000',records[0])
                self.assertNotIn('capture-fixture',records[0])
                self.assertNotIn('PRIVATE',records[0])

    def test_recognition_only_mode_never_enqueues_text_work(self):
        from app import main
        from fastapi.testclient import TestClient
        from test_transcription_diagnostics import FakeTranscriber
        from unittest.mock import AsyncMock
        enqueue=AsyncMock()
        with patch('app.main.transcriber',FakeTranscriber('synthetic recognition')),patch.object(main.settings,'assistant_voice_diagnostic_only',True),patch('app.main.handle_utterance',enqueue),TestClient(main.app) as client:
            with client.websocket_connect('/ws/device/capture-fixture') as socket:
                self.authenticate(socket,'capture-fixture')
                socket.send_json({'type':'audio.start','format':'pcm_s16le','sample_rate':12000,'channels':1})
                self.assertEqual(socket.receive_json()['state'],'listening')
                socket.send_bytes(b'\0\0'*2400)
                socket.send_json({'type':'audio.end'})
                packets=[]
                while True:
                    packet=socket.receive_json();packets.append(packet)
                    if packet.get('state')=='idle':break
                self.assertEqual(next(p for p in packets if p['type']=='transcript')['text'],'synthetic recognition')
                enqueue.assert_not_awaited()

    def test_recognition_only_mode_is_opt_in(self):
        from app.config import Settings
        s=Settings(_env_file=None)
        self.assertFalse(s.assistant_voice_diagnostic_only)
        s=Settings(_env_file=None,assistant_voice_diagnostic_only=True,assistant_provider='anthropic')
        self.assertTrue(s.assistant_voice_diagnostic_only)
        self.assertEqual(s.assistant_provider,'anthropic')
