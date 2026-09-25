import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class FakeTranscriber:
    name = "test-stt"

    async def transcribe(self, pcm, audio_format):
        self.pcm = pcm
        self.audio_format = audio_format
        return "What's next?"


class DeviceSocketTests(unittest.TestCase):
    def test_audio_turn_is_transcribed_and_sent_to_agent(self):
        transcriber = FakeTranscriber()
        with patch("app.main.transcriber", transcriber):
            with TestClient(app).websocket_connect("/ws/device/voice-test") as socket:
                self.assertEqual(socket.receive_json()["type"], "ready")
                socket.send_json(
                    {
                        "type": "audio.start",
                        "format": "pcm_s16le",
                        "sample_rate": 16000,
                        "channels": 1,
                    }
                )
                self.assertEqual(socket.receive_json(), {"type": "state", "state": "listening"})
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
