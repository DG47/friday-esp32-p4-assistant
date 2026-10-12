import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.config import Settings
from app.device_hub import DeviceHub
from app.speech import SpeechDirector
from app.speech_provider import create_speech_provider, speech_availability, UnavailableSpeech


class DeploymentSpeechTests(unittest.IsolatedAsyncioTestCase):
    def settings(self, provider):
        return Settings(_env_file=None, tts_provider=provider)

    async def test_linux_macos_configuration_reports_unavailable_and_never_falls_back(self):
        with patch('app.local_speech.sys.platform', 'linux'), patch('app.speech.OpenAISpeech') as paid:
            provider = create_speech_provider(self.settings('macos'))
        self.assertEqual(speech_availability(provider), {'state': 'unavailable', 'reason': 'macos_runtime_unavailable'})
        paid.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            await provider.synthesize('Original synthetic phrase')

    async def test_missing_macos_executable_is_explicit(self):
        with patch('app.local_speech.sys.platform', 'darwin'), patch('app.local_speech.Path.is_file', return_value=False):
            self.assertIsInstance(create_speech_provider(self.settings('macos')), UnavailableSpeech)

    async def test_disabled_provider_does_not_construct_or_send(self):
        with patch('app.speech.OpenAISpeech') as paid:
            self.assertIsNone(create_speech_provider(self.settings('none')))
            paid.assert_not_called()
        self.assertEqual(speech_availability(None)['state'], 'disabled')

    async def test_unavailable_provider_emits_error_without_claiming_playback(self):
        hub, messages = DeviceHub(), []
        class Peer:
            async def send_json(self, message):
                messages.append(message)
        hub.register('synthetic-device', Peer(), {'pcm_speech'})
        director = SpeechDirector(hub, UnavailableSpeech('macos', 'macos_runtime_unavailable'))
        await director.speak('synthetic-device', 'Original synthetic phrase')
        await director.tasks['synthetic-device']
        self.assertTrue(any(m.get('code') == 'speech_unavailable' for m in messages))
        self.assertFalse(any(m['type'] in {'speech.start', 'speech.chunk', 'speech.end'} for m in messages))
        await director.close()

    async def test_available_only_means_provider_construction(self):
        self.assertEqual(speech_availability(object()), {'state': 'available', 'reason': 'playback_not_verified'})


class DeploymentReadinessTests(unittest.TestCase):
    def test_configured_unavailable_speech_keeps_liveness_but_fails_readiness(self):
        from app.main import app
        with patch('app.main.speech.provider', UnavailableSpeech('macos', 'macos_runtime_unavailable')):
            client = TestClient(app)  # no lifespan/provider inference or listening socket
            self.assertEqual(client.get('/health').status_code, 200)
            response = client.get('/ready')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['detail'], 'Configured speech provider unavailable')

    def test_provider_availability_is_admin_only_and_contains_no_provider_data(self):
        from app.main import app
        with patch('app.main.speech.provider', UnavailableSpeech('macos', 'macos_runtime_unavailable')):
            client = TestClient(app, client=('127.0.0.1', 1))
            self.assertEqual(client.get('/api/diagnostics').status_code, 401)
            with patch('app.main.admin_token_valid', return_value=True):
                response = client.get('/api/diagnostics', headers={'x-friday-admin-token': 'original-synthetic-fixture'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['speech'], {'state': 'unavailable', 'reason': 'macos_runtime_unavailable'})
