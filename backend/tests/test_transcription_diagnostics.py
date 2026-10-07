import asyncio
import logging
from contextlib import closing
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main
from app.audio_session import AudioSession
from app.stt.base import AudioFormat, TranscriptionError
from app.stt.diagnostics import (FailureReason, Phase, TranscriptionDiagnostics,
                                 log_failure, measure, observe)
from app.stt.faster_whisper_provider import FasterWhisperTranscriber
import test_device_socket as socket_fixture

SECRET = "private-token /private/model/path PRIVATE_TRANSCRIPT"


class FakeTranscriber:
    name = "fixture"

    def __init__(self, outcome):
        self.outcome = outcome
        self.cancelled = False

    async def transcribe(self, pcm, audio_format):
        if self.outcome == "block":
            try:
                with measure(Phase.DECODE):
                    await asyncio.Event().wait()
            finally:
                self.cancelled = True
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class SocketDiagnosticsTests(unittest.TestCase):
    setUp = socket_fixture.DeviceSocketTests.setUp
    tearDown = socket_fixture.DeviceSocketTests.tearDown
    authenticate = socket_fixture.DeviceSocketTests.authenticate

    def turn(self, socket, pcm=b"\0\0" * 2400, start=True):
        if start:
            socket.send_json({"type": "audio.start", "format": "pcm_s16le",
                              "sample_rate": 12000, "channels": 1})
            self.assertEqual(socket.receive_json(), {"type": "state", "state": "listening"})
            if pcm:
                socket.send_bytes(pcm)
        socket.send_json({"type": "audio.end"})
        packets = []
        while True:
            packet = socket.receive_json()
            packets.append(packet)
            if packet == {"type": "state", "state": "idle"}:
                return packets

    def failure_then_recovery(self, outcome, expected, message, pcm=b"\0\0" * 2400,
                              start=True, timeout=60):
        fake = FakeTranscriber(outcome)
        async def complete(device_id, text):
            await main.hub.broadcast(device_id, {"type": "state", "state": "idle"})
        enqueue = AsyncMock(side_effect=complete)
        with patch("app.main.transcriber", fake), patch("app.main.handle_utterance", enqueue), \
             patch("app.main.AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS", timeout):
            with TestClient(main.app) as client:
                with client.websocket_connect("/ws/device/stt-diagnostic-fixture") as socket:
                    self.authenticate(socket, "stt-diagnostic-fixture")
                    with self.assertLogs("uvicorn.error", level="WARNING") as captured:
                        packets = self.turn(socket, pcm=pcm, start=start)
                    error = next(p for p in packets if p["type"] == "error")
                    self.assertEqual(error, {"type": "error", "code": "transcription_failed", "message": message})
                    enqueue.assert_not_awaited()
                    with closing(self.job_store._connect()) as database:
                        self.assertEqual(database.execute("SELECT count(*) FROM agent_jobs").fetchone()[0], 0)
                    detailed = [x for x in captured.output if "transcription_diag " in x]
                    self.assertEqual(len(detailed), 1)
                    self.assertIn("reason=" + expected, detailed[0])
                    self.assertNotIn(SECRET, "\n".join(captured.output))
                    self.assertNotIn("PRIVATE_TRANSCRIPT", "\n".join(captured.output))
                    for key in ["input_bytes", "sample_rate", "channels", "audio_ms", "total_us", "model_load_us", "input_prepare_us", "decode_us"]:
                        self.assertRegex(detailed[0], key + r"=\d+")
                    if outcome == "block":
                        self.assertTrue(fake.cancelled)
                        self.assertIn("phase=decode", detailed[0])
                    fake.outcome = "synthetic recognition"
                    # A success produces no diagnostic record, and the same socket recovers.
                    with patch("app.main.log_failure") as unexpected:
                        packets = self.turn(socket)
                        unexpected.assert_not_called()
                    self.assertEqual(next(p for p in packets if p["type"] == "transcript")["text"], "synthetic recognition")
                    enqueue.assert_awaited_once()
        return detailed[0]

    def test_missing_turn_and_recovery(self):
        self.failure_then_recovery("unused", "capture_missing", "audio.end arrived without audio.start", start=False)

    def test_short_input_and_recovery(self):
        record = self.failure_then_recovery("unused", "input_short", "Audio turn was too short", pcm=b"\0\0" * 240)
        self.assertIn("input_bytes=480", record)
        self.assertIn("audio_ms=20", record)

    def test_malformed_buffer_and_recovery(self):
        original = AudioSession.finish
        first = True
        def corrupt_once(session):
            nonlocal first
            if first:
                first = False
                session.data.append(0)
            return original(session)
        with patch.object(AudioSession, "finish", corrupt_once):
            self.failure_then_recovery("unused", "capture_malformed", "PCM audio must contain complete 16-bit samples")

    def test_classified_provider_errors_and_recovery(self):
        for reason in [FailureReason.MODEL_LOAD, FailureReason.INPUT_PREPARE, FailureReason.DECODE]:
            with self.subTest(reason=reason):
                self.failure_then_recovery(TranscriptionError(SECRET, reason=reason), reason.value, SECRET)

    def test_unclassified_error_cannot_inject_log_reason(self):
        self.failure_then_recovery(TranscriptionError(SECRET, reason=SECRET), "stt_error", SECRET)

    def test_empty_result_and_recovery(self):
        self.failure_then_recovery("  ", "empty", "No speech was recognized")

    def test_real_deadline_cancellation_and_recovery(self):
        self.failure_then_recovery("block", "timeout", "Speech transcription timed out", timeout=.01)

    def test_disabled_transcriber_and_recovery(self):
        async def complete(device_id, text):
            await main.hub.broadcast(device_id, {"type": "state", "state": "idle"})
        enqueue = AsyncMock(side_effect=complete)
        with patch("app.main.transcriber", None), patch("app.main.handle_utterance", enqueue):
            with TestClient(main.app) as client:
                with client.websocket_connect("/ws/device/stt-disabled") as socket:
                    self.authenticate(socket, "stt-disabled")
                    with self.assertLogs("uvicorn.error", level="WARNING") as captured:
                        packets = self.turn(socket)
                    self.assertIn("reason=stt_disabled", "\n".join(captured.output))
                    self.assertEqual(next(p for p in packets if p["type"] == "error")["message"], "Speech transcription is disabled")
                    enqueue.assert_not_awaited()
                    with patch("app.main.transcriber", FakeTranscriber("fixture")):
                        self.turn(socket)
                    enqueue.assert_awaited_once()


class ProviderDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def provider(self):
        return FasterWhisperTranscriber("fixture", "cpu", "int8", "en")

    async def test_model_load_failure_preserves_message_and_reason(self):
        p = self.provider()
        d = TranscriptionDiagnostics(4800, 12000, 1)
        with observe(d), patch.object(p, "_get_model", AsyncMock(side_effect=RuntimeError(SECRET))):
            with self.assertRaises(TranscriptionError) as caught:
                await p.transcribe(b"\0\0" * 2400, AudioFormat("pcm_s16le", 12000, 1))
        self.assertEqual(str(caught.exception), "Could not load the local Whisper model: " + SECRET)
        self.assertEqual(caught.exception.reason, FailureReason.MODEL_LOAD)
        self.assertEqual(d.snapshot()["phase"], "model_load")

    async def test_lazy_segment_failure_is_decode_not_model_load(self):
        def lazy_segments():
            yield SimpleNamespace(text="PRIVATE_TRANSCRIPT")
            raise RuntimeError(SECRET)
        model = SimpleNamespace(transcribe=lambda *args, **kwargs: (lazy_segments(), None))
        p = self.provider()
        d = TranscriptionDiagnostics(4800, 12000, 1)
        with observe(d), patch.object(p, "_get_model", AsyncMock(return_value=model)):
            with self.assertRaises(TranscriptionError) as caught:
                await p.transcribe(b"\0\0" * 2400, AudioFormat("pcm_s16le", 12000, 1))
        self.assertEqual(caught.exception.reason, FailureReason.DECODE)
        self.assertEqual(str(caught.exception), "Local transcription failed: " + SECRET)
        self.assertEqual(d.snapshot()["phase"], "decode")
        with self.assertLogs("diagnostic-fixture", level="WARNING") as captured:
            log_failure(logging.getLogger("diagnostic-fixture"), d, caught.exception.reason)
        self.assertNotIn("PRIVATE_TRANSCRIPT", "\n".join(captured.output))
        self.assertNotIn("/private", "\n".join(captured.output))

    async def test_wav_preparation_failure_is_not_decode(self):
        p = self.provider()
        d = TranscriptionDiagnostics(4800, 12000, 1)
        with observe(d), patch.object(p, "_get_model", AsyncMock(return_value=object())), \
             patch("app.stt.faster_whisper_provider.tempfile.NamedTemporaryFile", side_effect=OSError(SECRET)):
            with self.assertRaises(TranscriptionError) as caught:
                await p.transcribe(b"\0\0" * 2400, AudioFormat("pcm_s16le", 12000, 1))
        self.assertEqual(caught.exception.reason, FailureReason.INPUT_PREPARE)
        self.assertEqual(d.snapshot()["decode_us"], 0)

    async def test_concurrent_contexts_and_late_worker_are_isolated(self):
        entered = threading.Event()
        release = threading.Event()
        old = TranscriptionDiagnostics(4800, 12000, 1)
        new = TranscriptionDiagnostics(9600, 24000, 1)
        def worker():
            with measure(Phase.DECODE):
                entered.set()
                release.wait(2)
        with observe(old):
            task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            await asyncio.to_thread(entered.wait, 2)
            self.assertTrue(entered.is_set())
            snapshot = old.snapshot()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with observe(new):
                with measure(Phase.MODEL_LOAD):
                    pass
            self.assertEqual(new.snapshot()["decode_us"], 0)
            self.assertEqual(new.snapshot()["phase"], "model_load")
            self.assertEqual(snapshot["phase"], "decode")
        finally:
            release.set()


class TimingTests(unittest.TestCase):
    def test_active_phase_snapshot_counts_partial_time_once(self):
        with patch("app.stt.diagnostics.time.monotonic_ns", side_effect=[1000, 2000, 5000, 6000, 7000]):
            d = TranscriptionDiagnostics(4800, 12000, 1)
            with d.measure(Phase.DECODE):
                active = d.snapshot()
            finished = d.snapshot()
        self.assertEqual(active["decode_us"], 3)
        self.assertEqual(finished["decode_us"], 4)
        self.assertEqual(active["audio_ms"], 200)
        self.assertEqual(finished["total_us"], 6)

    def test_runtime_error_argument_compatibility(self):
        for args in [(), ("fixture",), ("fixture", "detail")]:
            self.assertEqual(str(TranscriptionError(*args)), str(RuntimeError(*args)))
