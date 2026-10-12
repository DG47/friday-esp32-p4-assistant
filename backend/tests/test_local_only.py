"""No inference or live daemon: native Ollama wire fixtures and synthetic tools only."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.config import Settings
from app.providers.local_policy import LocalModelUnavailable, local_endpoint
from app.providers.ollama_provider import OllamaProvider
from app.providers.router import ProviderRouter
from app.agent import FridayAgent
from app.db import ReminderStore
from test_conversation_scenario import NOW, SyntheticWeather, synthetic_calendar, settings_for

REAL_CLIENT = httpx.AsyncClient
LOCAL_META = {'model_info': {'general.architecture': 'synthetic'}, 'capabilities': ['completion', 'tools']}


def settings(**overrides):
    return Settings(_env_file=None, **({'assistant_env': 'test', 'assistant_provider': 'local-only',
                                       'ollama_model': 'original-fixture:local', 'stt_provider': 'none',
                                       'tts_provider': 'none'} | overrides))


class Wire:
    def __init__(self, *, status=None, metadata=None, show_code=200, chat=None, chat_code=200, error=False):
        self.status = {'cloud': {'disabled': True}} if status is None else status
        self.metadata = deepcopy(LOCAL_META) if metadata is None else metadata
        self.show_code, self.chat_code, self.error = show_code, chat_code, error
        self.chat = list(chat or [{'message': {'content': 'Original synthetic reply'}}])
        self.calls, self.options = [], []

    def handle(self, request):
        assert request.url.host in {'127.0.0.1', '::1'}, request.url
        assert request.url.port == 11434, request.url
        self.calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if self.error:
            raise httpx.ConnectError('Original synthetic unavailable daemon', request=request)
        if request.url.path == '/api/status':
            return httpx.Response(200, json=self.status)
        if request.url.path == '/api/show':
            return httpx.Response(self.show_code, json=self.metadata)
        assert request.url.path == '/api/chat', request.url
        return httpx.Response(self.chat_code, json=self.chat.pop(0))

    def client(self, **kwargs):
        self.options.append(kwargs)
        return REAL_CLIENT(transport=httpx.MockTransport(self.handle), **kwargs)

    def patched(self):
        return patch('app.providers.ollama_provider.httpx.AsyncClient', side_effect=self.client)


class LocalPolicyTests(unittest.TestCase):
    def test_accept_only_loopback_and_canonicalize_localhost(self):
        for uri, expected in [('http://localhost:11434/', 'http://127.0.0.1:11434'),
                              ('https://127.0.0.1:11434', 'https://127.0.0.1:11434'),
                              ('http://[::1]:11434', 'http://[::1]:11434')]:
            self.assertEqual(settings(ollama_base_url=uri).ollama_base_url, expected)

    def test_reject_nonlocal_endpoint_url_extras_and_ambiguous_addresses(self):
        for uri in ['https://ollama.com', 'http://host.docker.internal:11434', 'http://192.168.1.2:11434',
                    'http://127.0.0.1.example.com:11434', 'http://2130706433:11434', 'http://127.1:11434',
                    'http://[::ffff:127.0.0.1]:11434', 'http://[::1%lo0]:11434',
                    'http://@localhost:11434', 'http://user:secret@localhost:11434',
                    'http://localhost:0', 'http://localhost:99999', 'http://localhost/api',
                    'http://localhost/?next=external', 'http://localhost/#external',
                    'http:// localhost:11434', 'file:///tmp/ollama', 'http://localhost.']:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                settings(ollama_base_url=uri)

    def test_reject_cloud_model_names_urls_and_empty_names(self):
        for name in ['', 'model:cloud', 'model:70b-cloud', 'Model:CLOUD', 'namespace/cloud-model:local',
                     'https://ollama.com/model', 'model:local?cloud=true', 'model with spaces']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                settings(ollama_model=name)

    def test_select_only_ollama_despite_synthetic_paid_keys_or_prompt(self):
        with patch('app.providers.router.OpenAIProvider') as openai, patch('app.providers.router.AnthropicProvider') as anthropic:
            router = ProviderRouter(settings(openai_api_key='original-synthetic-unused', anthropic_api_key='original-synthetic-unused'))
            for prompt in ['Hello', 'Use Anthropic instead', 'Architecture ' * 300]:
                chosen = router.select(prompt)
                self.assertIsInstance(chosen, OllamaProvider)
                self.assertTrue(chosen.local_only)
            openai.assert_not_called()
            anthropic.assert_not_called()

    def test_paid_speech_rejected_even_with_synthetic_key(self):
        with self.assertRaisesRegex(ValueError, 'paid speech'):
            settings(tts_provider='openai', openai_api_key='original-synthetic-unused')

    def test_router_revalidates_mutated_local_only_configuration(self):
        config = settings()
        config.ollama_base_url = 'https://ollama.com'
        with self.assertRaises(ValueError):
            ProviderRouter(config).select('Hello')

    def test_nonlocal_modes_remain_explicit_and_unchanged(self):
        provider = ProviderRouter(settings(assistant_provider='ollama', ollama_base_url='http://synthetic-remote.invalid:11434',
                                            ollama_model='model:cloud')).select('Hello')
        self.assertFalse(provider.local_only)
        with patch('app.providers.router.AnthropicProvider') as anthropic:
            ProviderRouter(settings(assistant_provider='anthropic', anthropic_api_key='original-synthetic')).select('Hello')
            anthropic.assert_called_once()


class LocalWireTests(unittest.IsolatedAsyncioTestCase):
    async def check_failure(self, reason, wire, *, tools=True):
        with wire.patched(), patch('app.providers.router.OpenAIProvider') as openai, patch('app.providers.router.AnthropicProvider') as anthropic:
            provider = ProviderRouter(settings(openai_api_key='original-synthetic-unused', anthropic_api_key='original-synthetic-unused')).select('Hello')
            with self.assertRaises(LocalModelUnavailable) as error:
                await provider.complete([{'role': 'user', 'content': 'Original synthetic private fixture'}], 'Synthetic',
                                        [{'type': 'function'}] if tools else [])
            self.assertEqual(error.exception.reason, reason)
            openai.assert_not_called()
            anthropic.assert_not_called()
            return wire.calls

    async def test_server_missing_fails_without_paid_fallback_or_chat(self):
        calls = await self.check_failure('local_server_unavailable', Wire(error=True))
        self.assertEqual([p for _, p, _ in calls], ['/api/status'])

    async def test_model_missing_fails_without_download_inference_or_fallback(self):
        calls = await self.check_failure('local_model_missing', Wire(show_code=404))
        self.assertEqual([p for _, p, _ in calls], ['/api/status', '/api/show'])

    async def test_cloud_disabled_is_strict_before_sending_any_prompt(self):
        for status in [{'cloud': {'disabled': False}}, {'cloud': {'disabled': 'true'}},
                       {'cloud': {'disabled': 1}}, {}, [], {'cloud': []}]:
            with self.subTest(status=status):
                calls = await self.check_failure('cloud_disable_required', Wire(status=status))
                self.assertEqual([p for _, p, _ in calls], ['/api/status'])
                self.assertIsNone(calls[0][2])

    async def test_remote_aliases_and_missing_capabilities_fail_before_chat(self):
        for field in ['remote_host', 'remote_model']:
            for value in ['synthetic-remote', False, 0, [], {}, None]:
                calls = await self.check_failure('remote_model_rejected', Wire(metadata=LOCAL_META | {field: value}))
                self.assertNotIn('/api/chat', [p for _, p, _ in calls])
        for metadata in [{}, {'model_info': {}, 'capabilities': ['completion', 'tools']},
                         LOCAL_META | {'capabilities': ['completion']}, LOCAL_META | {'capabilities': 'tools'}]:
            calls = await self.check_failure('local_model_capabilities_unverified', Wire(metadata=metadata))
            self.assertNotIn('/api/chat', [p for _, p, _ in calls])

    async def test_metadata_redirect_is_not_followed(self):
        wire = Wire()
        original = wire.handle
        def redirect(request):
            if request.url.path == '/api/status':
                wire.calls.append(('GET', '/api/status', None))
                return httpx.Response(302, headers={'Location': 'https://synthetic-remote.invalid'})
            return original(request)
        wire.handle = redirect
        self.assertEqual(len(await self.check_failure('cloud_status_unverified', wire)), 1)

    async def test_proxy_environment_ignored_and_attestation_fresh_each_round(self):
        wire = Wire(chat=[{'message': {'content': 'Synthetic one'}}, {'message': {'content': 'Synthetic two'}}])
        with wire.patched(), patch.dict('os.environ', {'HTTP_PROXY': 'http://synthetic-remote.invalid', 'HTTPS_PROXY': 'http://synthetic-remote.invalid'}):
            provider = ProviderRouter(settings()).select('Hello')
            await provider.complete([{'role': 'user', 'content': 'Hello'}], 'Synthetic', [])
            await provider.complete([{'role': 'user', 'content': 'Again'}], 'Synthetic', [])
        self.assertEqual([p for _, p, _ in wire.calls], ['/api/status', '/api/show', '/api/chat'] * 2)
        self.assertTrue(all(option['trust_env'] is False and option['follow_redirects'] is False for option in wire.options))

    async def test_chat_redirect_http_error_and_remote_response_fail_without_escape(self):
        await self.check_failure('local_inference_failed', Wire(chat_code=302))
        await self.check_failure('local_model_missing', Wire(chat_code=404))
        for value in ['synthetic-remote', False, 0, [], {}, None]:
            await self.check_failure('remote_response_rejected', Wire(chat=[{'remote_host': value, 'message': {'content': 'Never use'}}]))

    async def test_native_same_name_tool_ids_and_result_ids_round_trip(self):
        directory = tempfile.TemporaryDirectory()
        try:
            config = settings(assistant_database_path=Path(directory.name)/'fixture.db')
            agent = FridayAgent(config, ReminderStore(config.assistant_database_path))
            from app.config import WeatherLocation
            config.weather_locations = [WeatherLocation(label='07306', latitude=1, longitude=1), WeatherLocation(label='10010', latitude=2, longitude=2)]
            agent.weather = SyntheticWeather(config.weather_locations)
            def call(identifier, zip):
                return {'id': identifier, 'function': {'name': 'get_weather', 'arguments': {'location': zip}}}
            wire = Wire(chat=[{'message': {'content': '', 'tool_calls': [call('native-one', '07306'), call('native-two', '10010')]}},
                              {'message': {'content': '', 'tool_calls': [{'function': {'name': 'get_weather', 'arguments': {'location': '07306'}}}]}},
                              {'message': {'content': 'Original synthetic final reply'}}])
            with wire.patched(), patch('app.agent.weather_now', lambda: NOW), patch('app.providers.router.OpenAIProvider') as openai, patch('app.providers.router.AnthropicProvider') as anthropic:
                await agent.handle('device', 'Check my approved locations please', request_id='roundtrip')
                openai.assert_not_called(); anthropic.assert_not_called()
            chats = [data for _, path, data in wire.calls if path == '/api/chat']
            self.assertEqual(len(chats), 3)
            second = chats[1]['messages']
            calls = next(m['tool_calls'] for m in second if m.get('tool_calls'))
            results = [m for m in second if m['role'] == 'tool']
            self.assertEqual([c['id'] for c in calls], ['native-one', 'native-two'])
            self.assertEqual([r['tool_call_id'] for r in results], ['native-one', 'native-two'])
            self.assertEqual([r['tool_name'] for r in results], ['get_weather', 'get_weather'])
            self.assertEqual([c['function']['index'] for c in calls], [0, 1])
            last = chats[2]['messages']
            missing_call = [m['tool_calls'][0] for m in last if m.get('tool_calls')][-1]
            self.assertEqual(last[-1]['tool_call_id'], missing_call['id'])
            self.assertNotIn(missing_call['id'], ['native-one', 'native-two'])
            self.assertEqual(agent.weather.calls, ['07306', '10010', '07306'])
        finally:
            directory.cleanup()

    async def test_generated_ids_differ_across_missing_id_rounds(self):
        response = {'message': {'content': '', 'tool_calls': [{'function': {'name': 'get_weather', 'arguments': {}}}]}}
        wire = Wire(chat=[response, response])
        with wire.patched():
            provider = ProviderRouter(settings()).select('Hello')
            one = await provider.complete([], 'Synthetic', [])
            two = await provider.complete([], 'Synthetic', [])
        self.assertNotEqual(one.tool_calls[0].id, two.tool_calls[0].id)

    async def test_malformed_or_duplicate_calls_rejected_before_any_tool_execution(self):
        for calls in [[{'function': {'name': 'get_weather', 'arguments': []}}],
                      [{'function': {'name': 'get_weather', 'arguments': '{bad'}}],
                      [{'id': 'same', 'function': {'name': 'get_weather'}}, {'id': 'same', 'function': {'name': 'get_weather'}}],
                      *[ [{'id': identifier, 'function': {'name': 'get_weather'}}] for identifier in [12, 0, False, [], {}, ''] ]]:
            with self.subTest(calls=calls):
                await self.check_failure('local_response_invalid', Wire(chat=[{'message': {'content': '', 'tool_calls': calls}}]))

    async def test_provider_instance_mutation_cannot_escape(self):
        provider = ProviderRouter(settings()).select('Hello')
        provider.base_url = 'https://synthetic-remote.invalid'
        with patch('app.providers.ollama_provider.httpx.AsyncClient') as client:
            with self.assertRaises(ValueError):
                await provider.complete([], 'Synthetic', [])
            client.assert_not_called()

    async def test_availability_probe_revalidates_before_any_http_client(self):
        for field, value in [('base_url', 'https://synthetic-remote.invalid'), ('model', 'model:cloud')]:
            provider = ProviderRouter(settings()).select('Hello')
            setattr(provider, field, value)
            with patch('app.providers.ollama_provider.httpx.AsyncClient') as client:
                with self.assertRaises(ValueError):
                    await provider.ensure_available()
                client.assert_not_called()

    async def test_missing_model_job_fails_once_with_clear_legacy_and_native_ack_notice(self):
        from app.agent_jobs import AgentJobStore, AgentJobWorker
        from app.device_hub import DeviceHub
        from test_agent_jobs import FakeWebSocket
        for ack in [False, True]:
            with tempfile.TemporaryDirectory() as directory:
                config = settings(assistant_database_path=Path(directory)/'unavailable.db')
                agent = FridayAgent(config, ReminderStore(config.assistant_database_path))
                store = AgentJobStore(config.assistant_database_path)
                hub = DeviceHub(); socket = FakeWebSocket()
                await hub.connect('device', socket)
                if ack:
                    hub.set_capabilities('device', socket, {'result_ack'})
                job = store.enqueue('device', 'Original synthetic reasoning request', request_id='missing-job')
                wire = Wire(show_code=404)
                with wire.patched(), patch('app.providers.router.OpenAIProvider') as openai, patch('app.providers.router.AnthropicProvider') as anthropic:
                    worker = AgentJobWorker(store, agent, hub)
                    self.assertTrue(await worker.process_once())
                    self.assertFalse(await worker.process_once())
                    openai.assert_not_called(); anthropic.assert_not_called()
                saved = store.get(job.id)
                self.assertEqual((saved.status, saved.attempts, saved.error), ('failed', 1, 'local-only:local_model_missing'))
                message = next(m for m in socket.messages if m['type'] == ('agent.result' if ack else 'error'))
                self.assertIn('model is not installed', message['text' if ack else 'message'])
                if ack:
                    self.assertEqual(message['status'], 'failed')
                    self.assertEqual(message['delivery_id'], saved.delivery_id)
                    self.assertEqual(message['surface']['title'], 'Local reasoning unavailable')
                else:
                    self.assertEqual(message['code'], 'local_reasoning_unavailable')
                self.assertEqual([path for _, path, _ in wire.calls], ['/api/status', '/api/show'])

    async def test_unavailable_job_unknown_reason_never_exposes_raw_payload(self):
        from app.agent_jobs import AgentJobStore, AgentJobWorker
        from app.device_hub import DeviceHub
        from test_agent_jobs import FakeWebSocket
        with tempfile.TemporaryDirectory() as directory:
            store = AgentJobStore(Path(directory)/'unknown.db')
            hub = DeviceHub(); socket = FakeWebSocket()
            await hub.connect('device', socket)
            class Unavailable:
                async def handle(self, *args, **kwargs):
                    raise LocalModelUnavailable('ORIGINAL_SYNTHETIC_PRIVATE_ERROR')
            store.enqueue('device', 'Original synthetic request', request_id='unknown-job')
            self.assertTrue(await AgentJobWorker(store, Unavailable(), hub).process_once())
            saved = store.get('unknown-job')
            self.assertEqual(saved.error, 'local-only:local_inference_failed')
            self.assertNotIn('PRIVATE_ERROR', json.dumps(socket.messages))
            self.assertIn('Local reasoning', json.dumps(socket.messages))

    async def test_readiness_missing_model_is_503_without_inference(self):
        from fastapi import HTTPException
        import app.main as main
        wire = Wire(show_code=404)
        config = settings()
        with wire.patched(), patch.object(main, 'settings', config), patch.object(main.agent, 'providers', ProviderRouter(config)):
            with self.assertRaises(HTTPException) as error:
                await main.readiness()
            self.assertEqual(error.exception.status_code, 503)
            self.assertIn('local_model_missing', error.exception.detail)
            self.assertNotIn('/api/chat', [p for _, p, _ in wire.calls])


class LocalWorkingContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = settings_for(Path(self.directory.name)/'context.db')
        base.assistant_provider = 'local-only'
        base.ollama_model = 'original-fixture:local'
        self.agent = FridayAgent(base, ReminderStore(base.assistant_database_path))
        synthetic_calendar(self.agent)
        self.agent.weather = SyntheticWeather(base.weather_locations)
        self.clock = NOW
        self.agent.calendar.clock = lambda: self.clock
        self.agent.working_context.begin('device', 'setup')
        with patch('app.agent.weather_now', lambda: self.clock):
            calendar = await self.agent._execute_read_tool('get_calendar', {'range': 'today'}, 'device', 'setup', 0, True)
            self.ref = json.loads(calendar.content)['context_refs'][0]['event_ref']
            await self.agent._execute_read_tool('get_weather', {'location': '10010', 'event_ref': self.ref}, 'device', 'setup', 1, True)
        self.agent.store.append_exchange('device', 'old-correction', 'Original correction marker: choose 10010', 'Correction applied.')
        for number in range(12):
            self.agent.store.append_exchange('device', 'filler-' + str(number), 'Original synthetic turn ' + str(number), 'Original synthetic reply')

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_correction_survives_whole_turn_cutoff_and_native_model_roundtrip(self):
        wire = Wire(chat=[{'message': {'content': '', 'tool_calls': [{'id': 'context-weather', 'function': {'name': 'get_weather', 'arguments': {'location': 'context'}}}]}},
                          {'message': {'content': 'Original synthetic context acknowledgment'}}])
        with wire.patched(), patch('app.agent.weather_now', lambda: self.clock):
            reply = await self.agent.handle('device', 'Check weather for that plan again please', request_id='after-cutoff')
        chats = [data for _, path, data in wire.calls if path == '/api/chat']
        self.assertNotIn('Original correction marker', json.dumps(chats[0]))
        self.assertIn('"focused_event_weather_location": "10010"', chats[0]['messages'][0]['content'])
        result = next(m for m in chats[1]['messages'] if m['role'] == 'tool')
        self.assertEqual(json.loads(result['content'])['context']['location'], '10010')
        self.assertEqual(result['tool_call_id'], 'context-weather')
        self.assertEqual(reply.surface.context['weather_location'], '10010')
        self.assertEqual(reply.surface.context['event_ref'], self.ref)
        self.assertEqual(self.agent.working_context.state('device').events[self.ref].location, 'Jersey City, NJ (07306)')
        self.assertEqual(self.agent.weather.calls, ['10010', '10010'])

    async def test_changed_calendar_source_disambiguates_old_reading_from_current_binding(self):
        synthetic_calendar(self.agent, 'Another approved place (07306)')
        await self.agent._execute_read_tool('get_calendar', {'range': 'today'}, 'device', 'setup', 2, True)
        snapshot = self.agent.working_context.provider_context('device')
        self.assertEqual(snapshot['weather_location'], '10010')
        self.assertEqual(snapshot['weather_event_ref'], self.ref)
        self.assertEqual(snapshot['focused_event_weather_location'], '07306')
        self.assertEqual(self.agent.weather.calls, ['10010'])  # Context inspection does not fetch anything.

    async def test_expiry_dismissal_and_other_device_cannot_restore_private_binding(self):
        from datetime import timedelta
        self.clock += timedelta(seconds=31)
        snapshot = self.agent.working_context.provider_context('device')
        self.assertIsNone(snapshot['focused_event_ref'])
        self.assertIsNone(snapshot['focused_event_weather_location'])
        self.assertEqual(snapshot['event_refs'], [])
        self.assertNotIn('Jersey City', json.dumps(snapshot))
        self.agent.working_context.dismiss('device')
        self.assertIsNone(self.agent.working_context.provider_context('device')['weather_location'])
        other = self.agent.working_context.provider_context('other-device')
        self.assertIsNone(other['weather_event_ref'])
        self.assertIsNone(other['focused_event_weather_location'])

    async def test_calendar_sharing_gate_still_erases_provider_context(self):
        self.agent.settings.calendar_provider = 'google'
        self.agent.settings.calendar_model_context = False
        self.assertEqual(self.agent.working_context.provider_context('device'), {'status': 'local_only'})
