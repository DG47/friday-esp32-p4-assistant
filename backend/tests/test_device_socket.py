import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.agent_jobs import AgentJob, AgentJobStore
from app.db import ReminderStore
from app.device_auth import DeviceCredentialStore
from app.main import app, settings


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
        self.credential_store = DeviceCredentialStore(database_path)
        self.patchers = [
            patch("app.main.store", self.reminder_store),
            patch("app.main.agent.store", self.reminder_store),
            patch("app.main.reminder_scheduler.store", self.reminder_store),
            patch("app.main.job_store", self.job_store),
            patch("app.main.job_worker.store", self.job_store),
            patch("app.main.credential_store", self.credential_store),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def authenticate(self, socket, device_id, capabilities=None, token=None):
        self.assertEqual(socket.receive_json()["type"], "hello.required")
        token = token or "a" * 64
        self.credential_store.open_pairing_window(device_id, token[:8])
        message = {"type": "hello", "capabilities": capabilities or []}
        message["device_token"] = token
        socket.send_json(message)
        ready = socket.receive_json()
        self.assertEqual(ready["type"], "ready")
        self.assertEqual(ready["surface"]["subtitle"], "Paired and ready.")
        self.assertEqual(
            socket.receive_json(), {"type": "state", "state": "idle"}
        )

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
                    self.authenticate(socket, "queue-test")
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
                    self.authenticate(socket, "ack-test", ["reminder_ack"])
                    socket.send_json({"type": "reminder.ack", "reminder_id": 42})
                    socket.send_json({"type": "unsupported"})
                    self.assertEqual(socket.receive_json()["type"], "error")

        self.assertEqual(mark_delivered.call_args.args[:2], (42, "ack-test"))

    def test_idle_audio_can_trigger_friday(self):
        transcriber = FakeWakeTranscriber()
        with patch("app.main.transcriber", transcriber):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/device/wake-test") as socket:
                    self.authenticate(socket, "wake-test")
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
                    self.authenticate(socket, "voice-test")
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

    def test_physical_device_requires_pairing_then_reuses_its_identity(self):
        token = "e" * 64
        self.credential_store.open_pairing_window("friday-p3a", token[:8])
        with TestClient(app) as client:
            with client.websocket_connect("/ws/device/friday-p3a") as socket:
                self.authenticate(socket, "friday-p3a", ["display"], token)

            with client.websocket_connect("/ws/device/friday-p3a") as socket:
                self.authenticate(socket, "friday-p3a", ["display"], token)

            with client.websocket_connect("/ws/device/friday-p3a") as socket:
                self.assertEqual(socket.receive_json()["type"], "hello.required")
                socket.send_json(
                    {
                        "type": "hello",
                        "capabilities": ["display"],
                        "device_token": "f" * 64,
                    }
                )
                self.assertEqual(socket.receive_json()["code"], "pairing_required")

    def test_loopback_websocket_still_requires_a_credential(self):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/device/local-test") as socket:
                self.assertEqual(socket.receive_json()["type"], "hello.required")
                socket.send_json({"type": "hello", "capabilities": []})
                self.assertEqual(socket.receive_json()["code"], "pairing_required")

    def test_explicit_simulator_credential_is_accepted(self):
        with patch("app.main.is_local_host", return_value=True), patch.object(
            settings, "assistant_simulator_token", "b" * 64
        ), patch.object(settings, "assistant_simulator_device_id", "friday-p3a"):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/device/friday-p3a") as socket:
                    self.assertEqual(socket.receive_json()["type"], "hello.required")
                    socket.send_json(
                        {
                            "type": "hello",
                            "device_token": "b" * 64,
                            "capabilities": ["display"],
                        }
                    )
                    self.assertEqual(socket.receive_json()["type"], "ready")

    def test_simulator_credential_rejects_remote_or_wrong_device(self):
        with patch.object(settings, "assistant_simulator_token", "b" * 64), patch.object(
            settings, "assistant_simulator_device_id", "friday-p3a"
        ):
            with patch("app.main.is_local_host", return_value=False):
                with TestClient(app) as client:
                    with client.websocket_connect("/ws/device/friday-p3a") as socket:
                        self.assertEqual(socket.receive_json()["type"], "hello.required")
                        socket.send_json(
                            {"type": "hello", "device_token": "b" * 64, "capabilities": []}
                        )
                        self.assertEqual(socket.receive_json()["code"], "pairing_required")

            with patch("app.main.is_local_host", return_value=True):
                with TestClient(app) as client:
                    with client.websocket_connect("/ws/device/other-device") as socket:
                        self.assertEqual(socket.receive_json()["type"], "hello.required")
                        socket.send_json(
                            {"type": "hello", "device_token": "b" * 64, "capabilities": []}
                        )
                        self.assertEqual(socket.receive_json()["code"], "pairing_required")

    def test_private_read_apis_require_loopback_token(self):
        self.reminder_store.create(
            "visible reminder",
            datetime.now(timezone.utc) + timedelta(hours=1),
            device_id="friday-p3a",
        )
        self.reminder_store.create(
            "other device reminder",
            datetime.now(timezone.utc) + timedelta(hours=1),
            device_id="other-device",
        )
        with patch.object(settings, "assistant_simulator_token", "b" * 64), patch.object(
            settings, "assistant_simulator_device_id", "friday-p3a"
        ):
            with patch("app.main.is_local_host", return_value=False):
                with TestClient(app) as client:
                    response = client.get(
                        "/api/calendar/today",
                        headers={"X-Friday-Simulator-Token": "b" * 64},
                    )
                self.assertEqual(response.status_code, 403)

            with patch("app.main.is_local_host", return_value=True):
                with TestClient(app) as client:
                    self.assertEqual(client.get("/api/reminders").status_code, 401)
                    response = client.get(
                        "/api/calendar/today",
                        headers={"X-Friday-Simulator-Token": "b" * 64},
                    )
                self.assertEqual(response.status_code, 200)
                with TestClient(app) as client:
                    response = client.get(
                        "/api/reminders",
                        headers={"X-Friday-Simulator-Token": "b" * 64},
                    )
                self.assertEqual(
                    [item["text"] for item in response.json()], ["visible reminder"]
                )

    def test_pairing_window_requires_local_confirmation_header(self):
        with patch("app.main.is_local_host", return_value=True), patch.object(
            settings, "assistant_admin_token", "9" * 64
        ):
            with TestClient(app) as client:
                self.assertEqual(client.post("/api/devices/pairing-window").status_code, 401)
                missing_code = client.post(
                    "/api/devices/pairing-window",
                    headers={
                        "X-Friday-Pairing": "open",
                        "X-Friday-Device-ID": "friday-p3a",
                        "X-Friday-Admin-Token": "9" * 64,
                    },
                )
                response = client.post(
                    "/api/devices/pairing-window",
                    headers={
                        "X-Friday-Pairing": "open",
                        "X-Friday-Device-ID": "friday-p3a",
                        "X-Friday-Pairing-Code": "abcd-1234",
                        "X-Friday-Admin-Token": "9" * 64,
                    },
                )

        self.assertEqual(missing_code.status_code, 400)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pairing")
        self.assertEqual(response.json()["device_id"], "friday-p3a")

    def test_pairing_window_rejects_non_loopback_clients(self):
        with patch("app.main.is_local_host", return_value=False), patch.object(
            settings, "assistant_admin_token", "9" * 64
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/devices/pairing-window",
                    headers={
                        "X-Friday-Pairing": "open",
                        "X-Friday-Device-ID": "friday-p3a",
                        "X-Friday-Pairing-Code": "abcd-1234",
                        "X-Friday-Admin-Token": "9" * 64,
                    },
                )
        self.assertEqual(response.status_code, 403)

    def test_online_device_identity_can_be_rotated_locally(self):
        self.credential_store.open_pairing_window("friday-p3a", "cccccccc")
        self.credential_store.authorize("friday-p3a", "c" * 64)
        with patch("app.main.is_local_host", return_value=True), patch.object(
            settings, "assistant_admin_token", "9" * 64
        ), patch(
            "app.main.hub.broadcast", new=AsyncMock(return_value=1)
        ) as broadcast:
            with TestClient(app) as client:
                response = client.post(
                    "/api/devices/friday-p3a/rotate",
                    headers={
                        "X-Friday-Device-Action": "rotate",
                        "X-Friday-Admin-Token": "9" * 64,
                    },
                )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.credential_store.is_paired("friday-p3a"))
        self.assertFalse(self.credential_store.pairing_open("friday-p3a"))
        self.assertEqual(response.json()["status"], "awaiting_pairing_code")
        self.assertEqual(broadcast.await_args.kwargs["required_capability"], "identity_rotate")

    def test_device_identity_can_be_revoked_locally(self):
        self.credential_store.open_pairing_window("friday-p3a", "dddddddd")
        self.credential_store.authorize("friday-p3a", "d" * 64)
        with patch("app.main.is_local_host", return_value=True), patch.object(
            settings, "assistant_admin_token", "9" * 64
        ), patch(
            "app.main.hub.broadcast", new=AsyncMock(return_value=1)
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/devices/friday-p3a/revoke",
                    headers={
                        "X-Friday-Device-Action": "revoke",
                        "X-Friday-Admin-Token": "9" * 64,
                    },
                )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.credential_store.is_paired("friday-p3a"))


if __name__ == "__main__":
    unittest.main()
