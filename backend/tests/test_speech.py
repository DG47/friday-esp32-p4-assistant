import asyncio
import base64
import json
import unittest

import httpx
from openai import AsyncOpenAI

from app.device_hub import DeviceHub
from app.speech import SpeechDirector, OpenAISpeech, MAX_PCM_BYTES


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    def setup_director(self, provider, ack=True):
        hub = DeviceHub()
        messages = []
        director = SpeechDirector(hub, provider)
        class Peer:
            async def send_json(self, message):
                messages.append(message)
                if ack and message['type'] == 'speech.end':
                    director.finished('device', message['stream_id'], ack if isinstance(ack, str) else 'completed')
        hub.register('device', Peer(), {'pcm_speech'})
        return director, messages

    async def test_pcm_order_content_and_matching_completion(self):
        class Provider:
            async def synthesize(self, text): return b'\x01\x00' * 4000
        director, messages = self.setup_director(Provider())
        await director.speak('device', 'hello')
        await director.tasks['device']
        chunks = [m for m in messages if m['type'] == 'speech.chunk']
        self.assertEqual([m['sequence'] for m in chunks], [0, 1])
        self.assertEqual(b''.join(base64.b64decode(m['pcm']) for m in chunks), b'\x01\x00' * 4000)
        self.assertEqual(messages[-1], {'type': 'state', 'state': 'idle'})
        self.assertFalse(director.tasks)

    async def test_cancel_stops_synthesis_and_does_not_emit_late_audio(self):
        began = asyncio.Event()
        class Provider:
            async def synthesize(self, text):
                began.set()
                await asyncio.sleep(60)
        director, messages = self.setup_director(Provider())
        await director.speak('device', 'hello')
        await began.wait()
        await director.cancel('device')
        self.assertFalse(director.tasks)
        self.assertFalse(any(m['type'] == 'speech.start' for m in messages))
        self.assertEqual(messages[-1]['type'], 'speech.cancel')

    async def test_wrong_completion_does_not_finish_and_touch_interrupts(self):
        class Provider:
            async def synthesize(self, text): return b'\x00\x00' * 480
        director, messages = self.setup_director(Provider(), ack=False)
        await director.speak('device', 'hello')
        for _ in range(20):
            await asyncio.sleep(0)
            if any(m['type'] == 'speech.end' for m in messages): break
        director.finished('device', 'wrong-stream')
        self.assertFalse(director.waiting['device'][1].is_set())
        await director.cancel('device')
        self.assertFalse(director.waiting)
        self.assertFalse(director.tasks)

    async def test_invalid_pcm_fails_without_leaking_provider_exception(self):
        for value in (b'x', b'\x00' * (MAX_PCM_BYTES + 2)):
            class Provider:
                async def synthesize(self, text): return value
            director, messages = self.setup_director(Provider())
            await director.speak('device', 'hello')
            await director.tasks['device']
            self.assertTrue(any(m.get('code') == 'speech_unavailable' for m in messages))
            self.assertFalse(any(m['type'] == 'speech.start' for m in messages))

    async def test_device_failure_is_not_reported_as_success(self):
        class Provider:
            async def synthesize(self, text): return b'\x00\x00' * 480
        director, messages = self.setup_director(Provider(), ack='failed')
        await director.speak('device', 'hello')
        await director.tasks['device']
        self.assertTrue(any(m.get('code') == 'speech_unavailable' for m in messages))
        self.assertFalse(director.failed_streams)

    async def test_repeated_speech_then_disconnect_cleans_pending_stream(self):
        class Provider:
            async def synthesize(self, text): return b'\x00\x00' * 480
        director, messages = self.setup_director(Provider(), ack=False)
        await director.speak('device', 'first')
        old_task = director.tasks['device']
        await director.speak('device', 'second')
        self.assertTrue(old_task.cancelled())
        for _ in range(30):
            await asyncio.sleep(0)
            if director.waiting.get('device'): break
        stream_id, event = director.waiting['device']
        director.finished('device', stream_id, ['bad status'])
        self.assertFalse(event.is_set())
        peer = director.hub.connections['device'][0]
        director.hub.disconnect('device', peer.websocket)
        await director.cancel('device')
        self.assertFalse(director.tasks)
        self.assertFalse(director.waiting)

    async def test_superseded_reply_never_synthesizes(self):
        class Provider:
            async def synthesize(self, text): raise AssertionError('must not call')
        director, messages = self.setup_director(Provider())
        director.latest['device'] = 'new-job'
        await director.speak('device', 'old reply', 'old-job')
        self.assertFalse(director.tasks)
        self.assertFalse(messages)

    async def test_disabled_tts_returns_capable_device_to_idle(self):
        director, messages = self.setup_director(None)
        await director.speak('device', 'written reply')
        self.assertEqual(messages[-1], {'type': 'state', 'state': 'idle'})
        self.assertFalse(director.tasks)

    async def test_no_provider_or_capability_never_calls_synthesis(self):
        class Provider:
            async def synthesize(self, text): raise AssertionError('must not call')
        director = SpeechDirector(DeviceHub(), Provider())
        await director.speak('device', 'hello')
        self.assertFalse(director.tasks)

    async def test_openai_adapter_contract_uses_pcm_and_bounded_text(self):
        requests = []
        def response(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, content=b'\x01\x00' * 100, headers={'content-type': 'application/octet-stream'})
        provider = OpenAISpeech('synthetic-test-key', 'tts-1', 'onyx')
        await provider.client.close()
        provider.client = AsyncOpenAI(api_key='synthetic-test-key', http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
        self.assertEqual(len(await provider.synthesize('x' * 2000)), 200)
        self.assertEqual(requests[0]['response_format'], 'pcm')
        self.assertEqual(len(requests[0]['input']), 1000)
        await provider.close()

    async def test_concurrent_replacements_leave_one_owned_stream(self):
        class Provider:
            async def synthesize(self, text): return b'\x00\x00' * 480
        director, messages = self.setup_director(Provider(), ack=False)
        for iteration in range(20):
            await asyncio.gather(director.speak('device', 'original synthetic one'),
                                 director.speak('device', 'original synthetic two'))
            for _ in range(20):
                await asyncio.sleep(0)
                if director.waiting.get('device'): break
            self.assertEqual(len(director.tasks), 1)
            stream, done = director.waiting['device']
            director.finished('device', '0' * 32)
            self.assertFalse(done.is_set())
            director.finished('device', stream)
            await director.tasks['device']
            self.assertFalse(director.waiting)
        await director.cancel('device')
        self.assertFalse(director.tasks)

    async def test_cancel_and_replacement_cannot_emit_cancel_after_new_start(self):
        class Provider:
            async def synthesize(self, text): return b'\x00\x00' * 480
        director, messages = self.setup_director(Provider(), ack=False)
        await director.speak('device', 'original synthetic first')
        for _ in range(20):
            await asyncio.sleep(0)
            if director.waiting.get('device'): break
        start = len(messages)
        await asyncio.gather(director.cancel('device'), director.speak('device', 'original synthetic replacement'))
        for _ in range(20):
            await asyncio.sleep(0)
            if director.waiting.get('device'): break
        kinds = [m['type'] for m in messages[start:]]
        self.assertIn('speech.start', kinds)
        self.assertNotIn('speech.cancel', kinds[len(kinds)-kinds[::-1].index('speech.start'):])
        await director.cancel('device')
        self.assertFalse(director.tasks)
        self.assertFalse(director.waiting)
