import asyncio
import logging
import struct
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.stt.base import AudioFormat
from app.stt.diagnostics import TranscriptionDiagnostics, FailureReason, log_failure, observe
from app.stt.faster_whisper_provider import FasterWhisperTranscriber

FORMAT = AudioFormat('pcm_s16le', 12000, 1)

class InputMetadataTests(unittest.TestCase):
    def test_normal_quiet_silent_empty_malformed_clipped(self):
        for samples, peak, rms, zeros, clips in [([1000,-1000],1000,1000,0,0),
                ([1,-1],1,1,0,0),([0,0],0,0,1000000,0),
                ([-32768,32767],32768,32767,0,1000000)]:
            with self.subTest(samples=samples):
                pcm=struct.pack('<'+'h'*len(samples),*samples)
                d=TranscriptionDiagnostics(len(pcm),12000,1);d.input_level(pcm,'pcm_s16le')
                m=d.snapshot()
                self.assertEqual([m[k] for k in ['pcm_peak','pcm_rms','pcm_zero_ppm','pcm_clip_ppm']], [peak,rms,zeros,clips])
                self.assertEqual(m['pcm_status'],'valid')
        d=TranscriptionDiagnostics(0,12000,1);d.input_level(b'','pcm_s16le')
        self.assertEqual(d.snapshot()['pcm_status'],'empty')
        for pcm,rate,channels,encoding in [(b'x',12000,1,'pcm_s16le'),(b'xx',12000,2,'pcm_s16le'),(b'xx',0,1,'pcm_s16le'),(b'xx',12000,1,'private')]:
            d=TranscriptionDiagnostics(len(pcm),rate,channels);d.input_level(pcm,encoding)
            self.assertEqual(d.snapshot()['pcm_status'],'malformed')

    def test_vad_outcomes_validate_metadata_without_guessing(self):
        for before,after,outcome in [(2,0,'none_retained'),(2,1,'reduced'),(2,2,'all_retained'),
                (None,None,'unknown'),(float('nan'),1,'unknown'),(2,3,'unknown'),(0,0,'unknown'),('private',1,'unknown'),(10**1000,1,'unknown')]:
            d=TranscriptionDiagnostics(4,12000,1)
            d.voice_detection(True,SimpleNamespace(duration=before,duration_after_vad=after))
            self.assertEqual(d.snapshot()['vad_outcome'],outcome)
        d.voice_detection(False,SimpleNamespace(duration=2,duration_after_vad=0))
        self.assertEqual(d.snapshot()['vad_outcome'],'disabled')

    def test_scan_bound_and_hostile_optional_metadata(self):
        d=TranscriptionDiagnostics(2400000,24000,1)
        d.input_level(b'\0'*2400000,'pcm_s16le')
        m=d.snapshot()
        self.assertEqual(m['pcm_samples'],1200000)
        self.assertLessEqual(m['pcm_analyzed_samples'],12000)
        self.assertEqual(m['pcm_stride'],100)
        class Hostile:
            @property
            def duration(self):raise RuntimeError('PRIVATE_EXCEPTION')
        d.voice_detection(True,Hostile())
        self.assertEqual(d.snapshot()['vad_outcome'],'unknown')

    def test_bounded_content_free_failure_log(self):
        d=TranscriptionDiagnostics(4,12000,1);d.input_level(b'\0'*4,'pcm_s16le')
        d.voice_detection(True,SimpleNamespace(duration='PRIVATE_AUDIO',duration_after_vad='PRIVATE_WORDS'))
        with self.assertLogs('metadata-test',level='WARNING') as captured:
            log_failure(logging.getLogger('metadata-test'),d,FailureReason.EMPTY)
        self.assertEqual(len(captured.output),1)
        self.assertNotIn('PRIVATE',captured.output[0])
        self.assertLess(len(captured.output[0]),800)

class ProviderMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_decoder_empty_does_not_imply_vad_rejection(self):
        p=FasterWhisperTranscriber('fake','cpu','int8','en')
        for retained,outcome in [(0,'none_retained'),(1,'reduced'),(2,'all_retained')]:
            d=TranscriptionDiagnostics(4,12000,1)
            model=SimpleNamespace(transcribe=lambda *a,**k:(iter([]),SimpleNamespace(duration=2,duration_after_vad=retained)))
            with observe(d),patch.object(p,'_get_model',AsyncMock(return_value=model)):
                self.assertEqual(await p.transcribe(b'\0'*4,FORMAT),'')
            self.assertEqual(d.snapshot()['vad_outcome'],outcome)
            self.assertEqual(d.snapshot()['decoder_segments'],0)

    async def test_invalid_optional_metadata_preserves_recognition(self):
        class Hostile:
            @property
            def duration(self):raise RuntimeError('PRIVATE_EXCEPTION')
        p=FasterWhisperTranscriber('fake','cpu','int8','en')
        for info in [None,Hostile(),SimpleNamespace(duration=10**1000,duration_after_vad=1)]:
            d=TranscriptionDiagnostics(4,12000,1)
            model=SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text='fixture')]),info))
            with observe(d),patch.object(p,'_get_model',AsyncMock(return_value=model)):
                self.assertEqual(await p.transcribe(b'\0'*4,FORMAT),'fixture')
            self.assertEqual(d.snapshot()['vad_outcome'],'unknown')

    async def test_late_metadata_stays_with_cancelled_turn_and_logs_once(self):
        p=FasterWhisperTranscriber('fake','cpu','int8','en')
        entered,release=threading.Event(),threading.Event()
        def transcribe(*args,**kwargs):
            entered.set();release.wait(2)
            return iter([SimpleNamespace(text='PRIVATE_TRANSCRIPT')]),SimpleNamespace(duration=2,duration_after_vad=1)
        old=TranscriptionDiagnostics(4,12000,1);new=TranscriptionDiagnostics(4,12000,1)
        with observe(old),patch.object(p,'_get_model',AsyncMock(return_value=SimpleNamespace(transcribe=transcribe))):
            first=asyncio.create_task(p.transcribe(b'\0'*4,FORMAT))
            self.assertTrue(await asyncio.to_thread(entered.wait,2))
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            with self.assertLogs('late-metadata',level='WARNING') as captured:
                log_failure(logging.getLogger('late-metadata'),old,FailureReason.TIMEOUT)
            logged=captured.output.copy()
            try:
                with observe(new):
                    new.input_level(struct.pack('<hh',100,-100),'pcm_s16le')
            finally:
                release.set();await asyncio.wait_for(asyncio.gather(*p._workers),2)
        self.assertEqual(new.snapshot()['vad_outcome'],'unknown')
        self.assertEqual(new.snapshot()['decoder_segments'],0)
        self.assertEqual(old.snapshot()['decoder_segments'],1)
        self.assertEqual(captured.output,logged)
        self.assertNotIn('PRIVATE_TRANSCRIPT',''.join(logged))
