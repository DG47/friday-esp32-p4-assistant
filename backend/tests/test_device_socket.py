import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agent_jobs import AgentJob, AgentJobStore
from app.db import ReminderStore
from app.main import app


class FakeTranscriber:
    name = "test-stt"

    async def transcribe(self, pcm, audio_format):
        self.pcm = pcm
        self.audio_format = audio_format
        return "What's next?"


class FakeWakeTranscriber(FakeTranscriber):
    async def transcribe(self, pcm, audio_format):
        self.pcm = pcm
        self.audio_format = audio_format
        return "Friday"


class DeviceSocketTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "friday.db"
        self.reminder_store = ReminderStore(database_path)
        self.job_store = AgentJobStore(database_path)
        self.patchers = [
            patch("app.main.store", self.reminder_store),
            patch("app.main.agent.store", self.reminder_store),
            patch("app.main.reminder_scheduler.store", self.reminder_store),
            patch("app.main.job_store", self.job_store),
            patch("app.main.job_worker.store", self.job_store),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_utterance_is_accepted_without_waiting_for_the_agent(self):
        queued = AgentJob(
            id="request-fast",
            device_id="queue-test",
            text="Take your time",
            status="queued",
            attempts=0,
        )
        with patch("app.main.job_store.enqueue", return_value=queued):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/device/queue-test") as socket:
                    self.assertEqual(socket.receive_json()["type"], "ready")
                    socket.send_json(
                        {
                            "type": "utterance",
                            "request_id": "request-fast",
                            "text": "Take your time",
                        }
                    )
                    self.assertEqual(
                        socket.receive_json(), {"type": "state", "state": "thinking"}
                    )
                    self.assertEqual(
                        socket.receive_json(),
                        {"type": "job.accepted", "job_id": "request-fast"},
                    )

    def test_reminder_acknowledgement_is_recorded(self):
        with patch("app.main.store.mark_delivered") as mark_delivered:
            with TestClient(app) as client:
                with client.websocket_connect("/ws/device/ack-test") as socket:
                    self.assertEqual(socket.receive_json()["type"], "ready")
                    socket.send_json(
                        {"type": "hello", "capabilities": ["reminder_ack"]}
                    )
                    self.assertEqual(socket.receive_json()["type"], "state")
                    socket.send_json({"type": "reminder.ack", "reminder_id": 42})
                    socket.send_json({"type": "unsupported"})
                    self.assertEqual(socket.receive_json()["type"], "error")

        self.assertEqual(mark_delivered.call_args.args[:2], (42, "ack-test"))

    def test_idle_audio_can_trigger_friday(self):
        transcriber = FakeWakeTranscriber()
        with patch("app.main.transcriber", transcriber):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/device/wake-test") as socket:
                    self.assertEqual(socket.receive_json()["type"], "ready")
                    socket.send_json({"type": "wake.start"})
                    sample = (5000).to_bytes(2, "little", signed=True)
                    socket.send_bytes(sample * 36000)

                    self.assertEqual(
                        socket.receive_json(),
                        {"type": "wake.detected", "wake_word": "Friday"},
                    )

        self.assertEqual(transcriber.audio_format.sample_rate, 12000)

    def test_audio_turn_is_transcribed_and_sent_to_agent(self):
        transcriber = FakeTranscriber()
        with patch("app.main.transcriber", transcriber), patch(
            "app.main.agent.providers.select", return_value=None
        ):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/device/voice-test") as socket:
                    self.assertEqual(socket.receive_json()["type"], "ready")
                    socket.send_json(
                        {
                            "type": "audio.start",
                            "format": "pcm_s16le",
                            "sample_rate": 16000,
                            "channels": 1,
                        }
                    )
                    self.assertEqual(
                        socket.receive_json(), {"type": "state", "state": "listening"}
                    )
                    socket.send_bytes(b"\x00\x00" * 3200)
                    socket.send_json({"type": "audio.end"})

                    messages = []
                    while True:
                        message = socket.receive_json()
                        messages.append(message)
                        if message == {"type": "state", "state": "idle"}:
                            break

        transcript = next(item for item in messages if item["type"] == "transcript")
        reply = next(item for item in messages if item["type"] == "assistant.text")
        self.assertEqual(transcript["text"], "What's next?")
        self.assertIn("next event", reply["text"])
        self.assertEqual(transcriber.pcm, b"\x00\x00" * 3200)


if __name__ == "__main__":
    unittest.main()
