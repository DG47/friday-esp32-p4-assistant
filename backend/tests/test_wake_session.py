import unittest

from app.wake_session import WakeAudioSession, contains_wake_word, pcm_peak


class WakeAudioSessionTests(unittest.TestCase):
    def test_emits_overlapping_audio_probes(self):
        session = WakeAudioSession(
            sample_rate=10, channels=1, probe_seconds=1.0, overlap_seconds=0.2
        )
        session.append(b"a" * 20)

        self.assertEqual(session.take_probe(), b"a" * 20)
        self.assertEqual(bytes(session.data), b"a" * 4)

    def test_waits_for_a_complete_probe(self):
        session = WakeAudioSession(
            sample_rate=10, channels=1, probe_seconds=1.0, overlap_seconds=0.2
        )
        session.append(b"a" * 18)

        self.assertIsNone(session.take_probe())

    def test_matches_friday_as_a_word(self):
        self.assertTrue(contains_wake_word("Friday."))
        self.assertTrue(contains_wake_word("hey, FRIDAY!"))
        self.assertFalse(contains_wake_word("Fridays are useful"))

    def test_measures_pcm_peak(self):
        pcm = b"".join(
            sample.to_bytes(2, "little", signed=True)
            for sample in (-12, 4000, -5000, 20)
        )
        self.assertEqual(pcm_peak(pcm), 5000)


if __name__ == "__main__":
    unittest.main()
