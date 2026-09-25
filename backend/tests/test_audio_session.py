import unittest

from app.audio_session import AudioSession, AudioSessionError


class AudioSessionTests(unittest.TestCase):
    def test_accepts_device_stream_rate(self):
        session = AudioSession(max_seconds=2)
        session.start("pcm_s16le", 12000, 1)
        session.append(b"\x00\x00" * 240)

        pcm, audio_format = session.finish()

        self.assertEqual(audio_format.sample_rate, 12000)
        self.assertEqual(pcm, b"\x00\x00" * 240)

    def test_collects_one_pcm_turn(self):
        session = AudioSession(max_seconds=1)
        session.start("pcm_s16le", 24000, 1)
        session.append(b"\x01\x02")
        session.append(b"\x03\x04")

        pcm, audio_format = session.finish()

        self.assertEqual(pcm, b"\x01\x02\x03\x04")
        self.assertEqual(audio_format.sample_rate, 24000)
        self.assertFalse(session.active)

    def test_rejects_binary_data_outside_a_turn(self):
        session = AudioSession(max_seconds=1)

        with self.assertRaisesRegex(AudioSessionError, "without audio.start"):
            session.append(b"\x00\x00")

    def test_resets_after_size_limit(self):
        session = AudioSession(max_seconds=0.0001)
        session.start("pcm_s16le", 24000, 1)

        with self.assertRaisesRegex(AudioSessionError, "duration limit"):
            session.append(b"\x00" * 5)

        self.assertFalse(session.active)


if __name__ == "__main__":
    unittest.main()
