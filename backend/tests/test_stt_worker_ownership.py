import asyncio
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.stt.base import AudioFormat, TranscriptionError
from app.stt.diagnostics import TranscriptionDiagnostics, observe
from app.stt.faster_whisper_provider import FasterWhisperTranscriber

FORMAT = AudioFormat("pcm_s16le", 12000, 1)


class WorkerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def provider(self):
        return FasterWhisperTranscriber("fixture", "cpu", "int8", "en")

    async def entered(self, event):
        self.assertTrue(await asyncio.to_thread(event.wait, 2))

    async def test_blocked_native_worker_retains_ownership_after_caller_timeout(self):
        p = self.provider()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def work():
            calls.append(1)
            entered.set()
            release.wait(2)
        first = asyncio.create_task(p._run_worker(work))
        await self.entered(entered)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        try:
            for _ in range(5):
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(p._run_worker(work), .01)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(p._workers), 1)
            self.assertTrue(p._worker_lock.locked())
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*p._workers), 2)

    async def test_direct_owner_cancellation_characterizes_shutdown_limit(self):
        # Global event-loop cancellation differs from canceling a public caller:
        # it releases the asyncio permit while the native thread still runs.
        p = self.provider()
        entered, release, next_entered = threading.Event(), threading.Event(), threading.Event()
        def work():
            entered.set()
            release.wait(2)
        first = asyncio.create_task(p._run_worker(work))
        await self.entered(entered)
        owner = next(iter(p._workers))
        owner.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await first
            await asyncio.wait_for(p._run_worker(next_entered.set), 2)
            self.assertTrue(next_entered.is_set())
            self.assertFalse(release.is_set())
        finally:
            release.set()

    async def test_cancelled_wake_cannot_overlap_next_decode(self):
        p = self.provider()
        first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def decode(*args):
            calls.append(args)
            if len(calls) == 1:
                first_entered.set()
                release.wait(2)
                raise RuntimeError("synthetic late decoder failure")
            second_entered.set()
            return "fixture result"
        with patch.object(p, "_get_model", AsyncMock(return_value=object())), patch.object(p, "_transcribe_sync", decode):
            first = asyncio.create_task(p.transcribe_wake(b"\0\0", FORMAT))
            await self.entered(first_entered)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(p.transcribe(b"\0\0", FORMAT))
            try:
                self.assertFalse(await asyncio.to_thread(second_entered.wait, .1), "next decode overlapped canceled wake worker")
            finally:
                release.set()
                result = await asyncio.wait_for(second, 2)
            self.assertEqual(result, "fixture result")
            self.assertEqual(len(calls), 2)

    async def test_timeout_drops_queued_decode_and_next_turn_recovers(self):
        p = self.provider()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def decode(*args):
            calls.append(args)
            if len(calls) == 1:
                entered.set()
                release.wait(2)
            return "fixture result"
        with patch.object(p, "_get_model", AsyncMock(return_value=object())), patch.object(p, "_transcribe_sync", decode):
            first = asyncio.create_task(p.transcribe(b"\0\0", FORMAT))
            await self.entered(entered)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            queued = TranscriptionDiagnostics(4800, 12000, 1)
            try:
                with observe(queued):
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(p.transcribe(b"\0\0", FORMAT), .02)
                self.assertEqual(queued.snapshot()["phase"], "worker_wait")
            finally:
                release.set()
            self.assertEqual(await asyncio.wait_for(p.transcribe(b"\0\0", FORMAT), 2), "fixture result")
            self.assertEqual(len(calls), 2, "expired queued work must never start")

    async def test_cancelled_cold_load_is_reused_not_duplicated(self):
        p = self.provider()
        first_entered, duplicate, release = threading.Event(), threading.Event(), threading.Event()
        models = []
        def constructor(*args, **kwargs):
            model = object()
            models.append(model)
            if len(models) == 1:
                first_entered.set()
                release.wait(2)
            else:
                duplicate.set()
            return model
        with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=constructor)}):
            first = asyncio.create_task(p._get_model())
            await self.entered(first_entered)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(p._get_model())
            try:
                self.assertFalse(await asyncio.to_thread(duplicate.wait, .1), "cancellation allowed duplicate cold model construction")
            finally:
                release.set()
                model = await asyncio.wait_for(second, 2)
            self.assertIs(model, models[0])
            self.assertIs(await p._get_model(), model)
            self.assertEqual(len(models), 1)

    async def test_failed_cold_load_allows_retry(self):
        p = self.provider()
        model = object()
        calls = []
        def constructor(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("synthetic model failure")
            return model
        with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=constructor)}):
            with self.assertRaises(RuntimeError):
                await p._get_model()
            self.assertIs(await p._get_model(), model)
            self.assertEqual(len(calls), 2)


    async def test_repeated_queue_timeouts_leave_one_owned_worker(self):
        p = self.provider()
        entered, release = threading.Event(), threading.Event()
        calls = []
        unhandled = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: unhandled.append(context))
        def decode(*args):
            calls.append(1)
            entered.set()
            release.wait(2)
            raise RuntimeError("synthetic private late failure")
        try:
            with patch.object(p, "_get_model", AsyncMock(return_value=object())), patch.object(p, "_transcribe_sync", decode):
                first = asyncio.create_task(p.transcribe(b"\0\0", FORMAT))
                await self.entered(entered)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                for _ in range(20):
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(p.transcribe(b"\0\0", FORMAT), .002)
                await asyncio.sleep(0)
                self.assertEqual(len(p._workers), 1)
                self.assertEqual(len(calls), 1)
                release.set()
                await asyncio.gather(*p._workers, return_exceptions=True)
                await asyncio.sleep(0)
                self.assertEqual(len(p._workers), 0)
                self.assertEqual(unhandled, [])
        finally:
            release.set()
            loop.set_exception_handler(previous_handler)


    async def test_abandoned_failed_cold_load_is_private_and_retryable(self):
        p = self.provider()
        entered, release = threading.Event(), threading.Event()
        calls = []
        unhandled = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: unhandled.append(context))
        model = object()
        def constructor(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                release.wait(2)
                raise RuntimeError("synthetic private model path")
            return model
        try:
            with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=constructor)}):
                first = asyncio.create_task(p._get_model())
                await self.entered(entered)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                release.set()
                done, pending = await asyncio.wait({p._model_task}, timeout=2)
                self.assertFalse(pending)
                self.assertIs(await p._get_model(), model)
                await asyncio.sleep(0)
                self.assertEqual(len(calls), 2)
                self.assertEqual(unhandled, [])
        finally:
            release.set()
            loop.set_exception_handler(previous_handler)


class SocketWorkerRecoveryTests(unittest.TestCase):
    # Reuse only the isolated stores/auth helper, not the fixture's test methods.
    import test_device_socket as _fixture
    setUp = _fixture.DeviceSocketTests.setUp
    tearDown = _fixture.DeviceSocketTests.tearDown
    authenticate = _fixture.DeviceSocketTests.authenticate

    def test_real_provider_owner_timeout_has_no_stale_enqueue(self):
        from fastapi.testclient import TestClient
        from app import main
        p = FasterWhisperTranscriber("fixture", "cpu", "int8", "en")
        release = threading.Event()
        calls = []
        def decode(*args):
            calls.append(1)
            if len(calls) == 1:
                release.wait(2)
                return "synthetic stale recognition"
            return "synthetic fresh recognition"
        async def complete(device_id, text):
            await main.hub.broadcast(device_id, {"type": "state", "state": "idle"})
        enqueue = AsyncMock(side_effect=complete)
        def start(socket):
            socket.send_json({"type": "audio.start", "format": "pcm_s16le", "sample_rate": 12000, "channels": 1})
            self.assertEqual(socket.receive_json()["state"], "listening")
            socket.send_bytes(b"\0\0" * 2400)
            socket.send_json({"type": "audio.end"})
            self.assertEqual(socket.receive_json()["state"], "thinking")
        try:
            with patch("app.main.transcriber", p), patch.object(p, "_get_model", AsyncMock(return_value=object())), \
                 patch.object(p, "_transcribe_sync", decode), patch("app.main.handle_utterance", enqueue):
                with TestClient(main.app) as client:
                    with client.websocket_connect("/ws/device/worker-recovery") as socket:
                        self.authenticate(socket, "worker-recovery")
                        with patch("app.main.AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS", .01), self.assertLogs("uvicorn.error", level="WARNING") as logs:
                            start(socket)
                            error = socket.receive_json()
                            self.assertEqual(error["code"], "transcription_failed")
                            self.assertEqual(error["message"], "Speech transcription timed out")
                            self.assertEqual(socket.receive_json()["state"], "idle")
                        enqueue.assert_not_awaited()
                        self.assertIn("reason=timeout", "\n".join(logs.output))
                        start(socket)
                        release.set()
                        packet = socket.receive_json()
                        self.assertEqual(packet["type"], "transcript")
                        self.assertEqual(packet["text"], "synthetic fresh recognition")
                        self.assertEqual(socket.receive_json()["state"], "idle")
                        enqueue.assert_awaited_once_with("worker-recovery", "synthetic fresh recognition")
                        self.assertNotIn("recognition", "\n".join(logs.output))
        finally:
            release.set()
