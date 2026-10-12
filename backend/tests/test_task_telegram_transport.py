import asyncio
import tempfile
import unittest
import json
import logging
import threading
from contextlib import contextmanager
from unittest.mock import patch
import httpx
from pathlib import Path
from app.task_slice import TaskStore
from app.task_telegram import TelegramFixtureAdapter
from app.task_telegram_transport import TelegramTransport, TelegramHTTPS, split_text


class FakeHTTP:
    offline_fixture = True
    def __init__(self):
        self.updates, self.calls, self.replies = [], [], []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.block = False

    async def call(self, method, payload):
        self.calls.append((method, payload))
        if method == 'getUpdates':
            return {'ok': True, 'result': [u for u in self.updates if u['update_id'] >= payload['offset']]}
        if self.block:
            self.entered.set(); await self.release.wait()
        result = self.replies.pop(0) if self.replies else {'ok': True, 'result': True if method=='answerCallbackQuery' else {'message_id': len(self.calls)}}
        if isinstance(result, BaseException): raise result
        return result


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000]
        self.store = TaskStore(Path(self.temp.name)/'tasks.db', lambda: self.now[0])
        self.adapter = TelegramFixtureAdapter(self.store)
        self.http = FakeHTTP()
        self.transport = TelegramTransport(self.adapter, self.http, enabled=True)
        await self.transport.bind_verified(7,7,'owner')

    async def asyncTearDown(self): self.temp.cleanup()

    def update(self, number=1, text='/fixture range-total', callback=None):
        sender = {'id':7,'is_bot':False}
        message = {'chat': {'id':7,'type':'private'}, 'from':sender, 'text':text}
        if callback:
            return {'update_id':number, 'callback_query': {'id':'query-'+str(number), 'from':sender,
                    'message':message, 'data':callback}}
        return {'update_id':number, 'message':message}

    def rows(self):
        with self.store.db() as db:
            return db.execute('SELECT update_id,part,status,attempts FROM telegram_outbox ORDER BY update_id,part').fetchall()

    async def enqueue(self):
        self.http.updates = [self.update()]
        self.assertEqual(await self.transport.poll_once(), 1)

    async def test_disabled_and_fake_only(self):
        with self.assertRaises(ValueError): TelegramTransport(self.adapter, object())
        disabled = TelegramTransport(self.adapter, self.http)
        self.assertEqual(await disabled.poll_once(), 0)
        self.assertFalse(await disabled.deliver_once())
        self.assertEqual(self.http.calls, [])

    async def test_durable_offset_duplicate_and_scope_before_keyboard(self):
        await self.enqueue()
        restarted = TelegramTransport(TelegramFixtureAdapter(self.store), self.http, enabled=True)
        self.assertEqual(await restarted.poll_once(), 0)
        self.assertEqual(len(self.store.list('owner')), 1)
        self.assertEqual(len(self.rows()), 2)
        self.assertTrue(await restarted.deliver_once())
        text = self.http.calls[-1][1]
        self.assertIn('Exact scope:', text['text'])
        self.assertNotIn('reply_markup', text)
        self.assertNotIn('parse_mode', text)
        self.assertTrue(await restarted.deliver_once())
        self.assertIn('reply_markup', self.http.calls[-1][1])
        self.assertEqual([r[2] for r in self.rows()], ['sent','sent'])

    async def test_lost_reply_is_uncertain_and_withholds_keyboard(self):
        await self.enqueue()
        self.http.replies = [TimeoutError('reply lost after possible send')]
        with self.assertRaises(TimeoutError): await self.transport.deliver_once()
        self.assertEqual([r[2] for r in self.rows()], ['uncertain','pending'])
        before = len(self.http.calls)
        self.assertFalse(await self.transport.deliver_once())
        self.assertEqual(len(self.http.calls), before)

    async def test_interrupted_sending_recovered_without_resend(self):
        await self.enqueue()
        with self.store.db() as db:
            db.execute("UPDATE telegram_outbox SET status='sending' WHERE part=0")
        await self.transport.recover()
        self.assertEqual(self.rows()[0][2], 'uncertain')
        self.assertFalse(await self.transport.deliver_once())

    async def test_explicit_rate_limit_waits_and_retries_bounded(self):
        await self.enqueue()
        self.http.replies = [{'ok':False,'error_code':429,'parameters':{'retry_after':10}}]*3
        for _ in range(3):
            self.assertFalse(await self.transport.deliver_once())
            calls = len(self.http.calls)
            self.assertFalse(await self.transport.deliver_once())
            self.assertEqual(len(self.http.calls), calls)
            self.now[0] += 10
        self.assertFalse(await self.transport.deliver_once())
        self.assertEqual(self.rows()[0][2:], ('uncertain',3))

    async def test_revoke_serialized_through_send_then_blocks_remainder(self):
        await self.enqueue()
        self.http.block = True
        sending = asyncio.create_task(self.transport.deliver_once())
        await self.http.entered.wait()
        second = TelegramTransport(TelegramFixtureAdapter(self.store), self.http, enabled=True)
        revoke = asyncio.create_task(second.revoke(7))
        await asyncio.sleep(0)
        self.assertFalse(revoke.done())
        self.http.release.set()
        self.assertTrue(await sending)
        await revoke
        self.assertFalse(await self.transport.deliver_once())
        self.assertEqual([r[2] for r in self.rows()], ['sent','uncertain'])

    async def test_rebind_blocks_old_owner_outbox(self):
        await self.enqueue()
        await self.transport.bind_verified(7,7,'another-owner')
        self.assertFalse(await self.transport.deliver_once())
        self.assertTrue(all(r[2]=='uncertain' for r in self.rows()))

    async def test_expired_keyboard_withheld_after_scope_delivered(self):
        await self.enqueue(); await self.transport.deliver_once()
        self.now[0] += 301
        calls = len(self.http.calls)
        self.assertTrue(await self.transport.deliver_once())
        self.assertEqual(len(self.http.calls), calls+1)
        self.assertNotIn('reply_markup',self.http.calls[-1][1])
        self.assertIn('Exact scope:',self.http.calls[-1][1]['text'])
        self.assertEqual(self.rows()[1][2], 'withheld')

    async def test_changed_exact_scope_withholds_old_keyboard(self):
        await self.enqueue(); await self.transport.deliver_once()
        task = self.store.list('owner')[0]
        self.store.stop('owner',task['id'],'stop-before-keyboard')
        self.assertFalse(await self.transport.deliver_once())
        self.assertEqual(self.rows()[1][2], 'withheld')

    async def test_callback_ack_and_duplicate_action(self):
        await self.enqueue()
        await self.transport.deliver_once(); await self.transport.deliver_once()
        token = self.adapter.handle(self.update())['buttons'][0]['callback_data']
        self.http.updates = [self.update(2, callback=token)]
        self.assertEqual(await self.transport.poll_once(), 1)
        self.assertEqual(await self.transport.poll_once(), 0)
        self.assertEqual(self.store.list('owner')[0]['state'], 'queued')
        self.assertTrue(await self.transport.deliver_once())
        self.assertEqual(self.http.calls[-1], ('answerCallbackQuery', {'callback_query_id':'query-2','text':'Request received.'}))
        self.assertTrue(await self.transport.deliver_once())

    async def test_callback_before_scope_or_keyboard_attempt_cannot_approve(self):
        await self.enqueue()
        token = self.adapter.handle(self.update())['buttons'][0]['callback_data']
        for number in (2,3):
            self.http.updates = [self.update(number,callback=token)]
            self.assertEqual(await self.transport.poll_once(),1)
            self.assertEqual(self.store.list('owner')[0]['state'],'awaiting_approval')
            if number == 2: await self.transport.deliver_once()
        await self.transport.deliver_once()
        self.http.updates = [self.update(4,callback=token)]
        self.assertEqual(await self.transport.poll_once(),1)
        self.assertEqual(self.store.list('owner')[0]['state'],'queued')

    async def test_callback_after_uncertain_scope_cannot_approve(self):
        await self.enqueue()
        token = self.adapter.handle(self.update())['buttons'][0]['callback_data']
        self.http.replies = [TimeoutError('scope receipt unknown')]
        with self.assertRaises(TimeoutError): await self.transport.deliver_once()
        self.http.updates = [self.update(2,callback=token)]
        self.assertEqual(await self.transport.poll_once(),1)
        self.assertEqual(self.store.list('owner')[0]['state'],'awaiting_approval')

    async def test_full_long_scope_all_chunks_before_button(self):
        original = self.adapter.handle
        scope = ('synthetic scope 😀\n'*700)
        def handle(update):
            result = original(update); result['text'] = scope; return result
        self.adapter.handle = handle
        await self.enqueue()
        while await self.transport.deliver_once(): pass
        messages = [p for m,p in self.http.calls if m=='sendMessage']
        self.assertEqual(''.join(p['text'] for p in messages[:-1]), scope)
        self.assertTrue(all('reply_markup' not in p for p in messages[:-1]))
        self.assertIn('reply_markup', messages[-1])
        self.assertTrue(all(len(p['text'].encode('utf-16-le'))//2 <=3500 for p in messages))

    async def test_invalid_update_advances_without_private_reply(self):
        bad = self.update(); bad['message']['chat']['type'] = 'group'
        self.http.updates = [bad]
        self.assertEqual(await self.transport.poll_once(), 0)
        self.assertEqual(self.transport._offset(), 2)
        self.assertEqual(self.rows(), [])

    async def test_unrecognized_success_withholds_keyboard(self):
        await self.enqueue(); self.http.replies = [{'ok':True}]
        self.assertFalse(await self.transport.deliver_once())
        self.assertEqual(self.rows()[0][2], 'uncertain')
        self.assertFalse(await self.transport.deliver_once())

    async def test_https_setup_alone_does_not_enable_live_access(self):
        connector = TelegramHTTPS('123:SYNTHETIC_PLACEHOLDER')
        disabled = TelegramTransport(self.adapter, connector)
        self.assertEqual(await disabled.poll_once(), 0)
        with self.assertRaises(ValueError): TelegramTransport(self.adapter, connector, enabled=True)
        with self.assertRaisesRegex(ValueError,'not authorized'):
            await connector.call('getUpdates', {})

    async def test_https_protocol_via_mock_only(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={'ok':True,'result':[]})
        connector = TelegramHTTPS('123:SYNTHETIC_PLACEHOLDER', transport=httpx.MockTransport(respond))
        service = TelegramTransport(self.adapter, connector, enabled=True)
        self.assertEqual(await service.poll_once(), 0)
        self.assertEqual(seen[0].method, 'POST')
        self.assertEqual(seen[0].url.host, 'api.telegram.org')
        self.assertEqual(seen[0].url.scheme, 'https')
        self.assertEqual(json.loads(seen[0].content)['offset'], 0)
        with self.assertRaises(ValueError): await connector.call('deleteMessage', {})
        with self.assertRaises(ValueError): TelegramHTTPS('123:bad/path')

    async def test_https_failures_and_logs_redact_token(self):
        token = '123:SYNTHETIC_LOG_SECRET'
        def fail(request): raise httpx.ConnectError('secret '+token, request=request)
        connector = TelegramHTTPS(token, transport=httpx.MockTransport(fail))
        with self.assertRaisesRegex(ValueError, '^Telegram HTTPS request failed$'):
            await connector.call('getUpdates', {})
        with self.assertLogs('httpx', level='INFO') as logs:
            logging.getLogger('httpx').info('POST https://api.telegram.org/bot%s/getUpdates', token)
        self.assertNotIn(token, '\n'.join(logs.output))
        self.assertIn('[redacted]', '\n'.join(logs.output))

    async def test_https_mock_429_and_malformed_response(self):
        def rate_limit(request):
            return httpx.Response(429,json={'ok':False,'error_code':429,'parameters':{'retry_after':7}})
        connector = TelegramHTTPS('123:SYNTHETIC_RATE', transport=httpx.MockTransport(rate_limit))
        result = await connector.call('sendMessage', {'chat_id':7,'text':'synthetic'})
        self.assertEqual(result['parameters']['retry_after'],7)
        def malformed(request): return httpx.Response(200,content=b'not JSON')
        connector = TelegramHTTPS('123:SYNTHETIC_BAD', transport=httpx.MockTransport(malformed))
        with self.assertRaises(ValueError): await connector.call('getUpdates',{})

    async def test_poll_rate_limit_delay_is_durable(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(429,json={'ok':False,'error_code':429,'parameters':{'retry_after':7}})
        connector = TelegramHTTPS('123:SYNTHETIC_POLL_RATE',transport=httpx.MockTransport(respond))
        service = TelegramTransport(self.adapter,connector,enabled=True)
        self.assertEqual(await service.poll_once(),0)
        restarted = TelegramTransport(self.adapter,connector,enabled=True)
        self.assertEqual(await restarted.poll_once(),0)
        self.assertEqual(len(calls),1)
        self.now[0] += 7
        await restarted.poll_once()
        self.assertEqual(len(calls),2)

    async def test_long_poll_releases_binding_lock(self):
        entered,release=asyncio.Event(),asyncio.Event()
        original=self.http.call
        async def call(method,payload):
            if method=='getUpdates':
                self.assertGreaterEqual(payload['timeout'],20)
                entered.set(); await release.wait()
            return await original(method,payload)
        self.http.call=call
        polling=asyncio.create_task(self.transport.poll_once())
        try:
            await asyncio.wait_for(entered.wait(),0.5)
            await asyncio.wait_for(self.transport.revoke(7),0.1)
        finally:
            release.set(); await asyncio.gather(polling,return_exceptions=True)

    async def test_stale_initial_keyboard_automatically_sends_current_scope(self):
        await self.enqueue()
        task=self.store.list('owner')[0]
        with self.store.db() as db:
            current=self.store._get(db,'owner',task['id'])
            current['scope']['planner']={'provider':'synthetic-provider','proposal_digest':'synthetic-plan-digest','boundary':'Registered repair only'}
            from app.task_slice import digest
            current['scope_digest']=digest(current['scope'])
            self.store._save(db,'owner',current,'Fixture planner refreshed scope','scope')
        for _ in range(10):
            await self.transport.deliver_once()
        messages=[p for m,p in self.http.calls if m=='sendMessage']
        self.assertTrue(any('synthetic-provider' in p['text'] for p in messages))
        self.assertTrue(any('reply_markup' in p for p in messages))
        self.assertEqual(self.store.get('owner',task['id'])['scope']['planner']['proposal_digest'],'synthetic-plan-digest')

    async def test_invalid_authorized_callback_gets_ack_without_approval(self):
        self.http.updates=[self.update(1,callback='0'*32)]
        await self.transport.poll_once()
        for _ in range(3): await self.transport.deliver_once()
        self.assertTrue(any(m=='answerCallbackQuery' for m,p in self.http.calls))
        self.assertEqual(self.store.list('owner'),[])

    async def test_poison_update_does_not_block_later_help(self):
        self.http.updates=[{'update_id':1,'message':[]},self.update(2,'/help')]
        await self.transport.poll_once()
        self.assertEqual(self.transport._offset(),3)
        for _ in range(3): await self.transport.deliver_once()
        self.assertTrue(any('/tasks' in p.get('text','') for m,p in self.http.calls))

    async def test_malformed_callback_sender_does_not_block_later_stop(self):
        await self.enqueue()
        task=self.store.list('owner')[0]
        poison=self.update(2,callback='0'*32);poison['callback_query']['from']=[]
        self.http.updates=[poison,self.update(3,'/stop')]
        await self.transport.poll_once()
        self.assertEqual(self.store.get('owner',task['id'])['state'],'stopped')
        self.assertEqual(self.transport._offset(),4)

    async def test_truthy_malformed_callback_container_does_not_block_stop(self):
        await self.enqueue()
        task = self.store.list('owner')[0]
        poison = self.update(2, '/help')
        poison['callback_query'] = 'original-synthetic-malformed-container'
        self.http.updates = [poison, self.update(3, '/stop')]
        await self.transport.poll_once()
        self.assertEqual(self.store.get('owner', task['id'])['state'], 'stopped')
        self.assertEqual(self.transport._offset(), 4)
        with self.store.db(write=False) as db:
            decision = db.execute('SELECT outcome FROM telegram_decisions WHERE update_id=2').fetchone()
        self.assertEqual(decision[0], 'invalid or unbound: no action admitted')

    async def test_stop_bypasses_ordinary_cache_and_outbox_caps(self):
        await self.enqueue()
        task=self.store.list('owner')[0]
        self.transport.ordinary_limit=2
        with self.store.db() as db:
            db.executemany('INSERT INTO telegram_updates VALUES(?,?,?)',[(i,'synthetic','{}') for i in range(100,1100)])
        self.http.updates=[self.update(2000,'/stop '+task['id'])]
        self.assertEqual(await self.transport.poll_once(),1)
        self.assertEqual(self.store.get('owner',task['id'])['state'],'stopped')
        self.assertTrue(await self.transport.deliver_once())
        self.assertIn('Stop state: stopped',self.http.calls[-1][1]['text'])

    async def test_backpressure_has_durable_decision_without_ordinary_action(self):
        await self.enqueue();self.transport.ordinary_limit=2
        self.http.updates=[self.update(2,'/fixture range-total-two'),self.update(3,'/stop')]
        await self.transport.poll_once()
        self.assertEqual(len(self.store.list('owner')),1)
        self.assertEqual(self.store.list('owner')[0]['state'],'stopped')
        with self.store.db(write=False) as db:
            self.assertEqual(db.execute('SELECT outcome FROM telegram_decisions WHERE update_id=2').fetchone()[0],'backpressure: no action admitted')
        self.assertEqual(self.transport._offset(),4)

    async def test_retention_prunes_only_confirmed_groups_and_preserves_replay_fence(self):
        await self.enqueue()
        await self.transport.deliver_once();await self.transport.deliver_once()
        self.http.updates=[self.update(2,'/help')];await self.transport.poll_once()
        self.http.replies=[TimeoutError('synthetic uncertain')]
        with self.assertRaises(TimeoutError):await self.transport.deliver_once()
        self.http.updates=[self.update(3,'/help')];await self.transport.poll_once()
        self.now[0]+=86401
        await self.transport.poll_once()
        with self.store.db(write=False) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM telegram_outbox WHERE update_id=1').fetchone())
            self.assertIsNone(db.execute('SELECT 1 FROM telegram_updates WHERE update_id=1').fetchone())
            self.assertEqual(db.execute('SELECT status FROM telegram_outbox WHERE update_id=2').fetchone()[0],'uncertain')
            self.assertEqual(db.execute('SELECT status FROM telegram_outbox WHERE update_id=3').fetchone()[0],'pending')
        with self.assertRaises(ValueError):self.adapter.handle(self.update())
        self.assertEqual(len(self.store.list('owner')),1)

    async def test_runtime_storage_is_offloop_and_cancellation_drains_admission(self):
        entered,release=threading.Event(),threading.Event()
        original=self.adapter.respond
        main=threading.get_ident()
        def slow(update):
            self.assertNotEqual(threading.get_ident(),main)
            entered.set();release.wait(1)
            return original(update)
        with patch.object(self.adapter,'respond',slow):
            task=asyncio.create_task(self.transport.process_update(self.update()))
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(entered.is_set())
            task.cancel()
            revoke=asyncio.create_task(self.transport.revoke(7))
            await asyncio.sleep(.01)
            self.assertFalse(task.done());self.assertFalse(revoke.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            await revoke
        self.assertEqual(len(self.store.list('owner')),1)
        self.assertFalse(await self.transport.deliver_once())

    async def test_async_factory_and_all_runtime_db_calls_use_threads(self):
        main=threading.get_ident();calls=[];original=self.store.db
        @contextmanager
        def guarded(**kwargs):
            calls.append(threading.get_ident())
            self.assertNotEqual(threading.get_ident(),main)
            with original(**kwargs) as db:yield db
        with patch.object(self.store,'db',guarded):
            service=await TelegramTransport.create(self.store,self.http,enabled=True)
            await service.bind_verified(7,7,'owner')
            await service.recover()
            self.http.updates=[self.update(1,'/help')]
            await service.poll_once();await service.deliver_once()
            await service.revoke(7)
        self.assertGreater(len(calls),10)

    async def test_expired_planner_scope_refresh_keeps_metadata(self):
        await self.enqueue();task=self.store.list('owner')[0]
        from app.task_slice import digest
        with self.store.db() as db:
            current=self.store._get(db,'owner',task['id'])
            current['scope']['planner']={'provider':'synthetic-expired-planner','proposal_digest':'original-proposal','boundary':'Fixed repair only'}
            current['scope_digest']=digest(current['scope'])
            self.store._save(db,'owner',current,'Synthetic model proposal')
        await self.transport.deliver_once()
        self.now[0]+=301
        while await self.transport.deliver_once():pass
        refreshed=self.store.get('owner',task['id'])
        self.assertEqual(refreshed['scope']['planner']['proposal_digest'],'original-proposal')
        self.assertGreater(refreshed['expires_at'],self.now[0])
        self.assertTrue(any('synthetic-expired-planner' in p.get('text','') for m,p in self.http.calls))

    async def test_expired_callback_ack_and_safe_error(self):
        await self.enqueue();await self.transport.deliver_once();await self.transport.deliver_once()
        token=self.adapter.handle(self.update())['buttons'][0]['callback_data']
        self.now[0]+=301
        self.http.updates=[self.update(2,callback=token)]
        await self.transport.poll_once()
        await self.transport.deliver_once();await self.transport.deliver_once()
        self.assertTrue(any(m=='answerCallbackQuery' for m,p in self.http.calls))
        self.assertIn('expired',self.http.calls[-1][1]['text'])
        self.assertEqual(self.store.list('owner')[0]['state'],'awaiting_approval')

    async def test_cancelled_send_marks_uncertain(self):
        await self.enqueue(); self.http.block = True
        task = asyncio.create_task(self.transport.deliver_once())
        await self.http.entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.rows()[0][2], 'uncertain')

    async def test_scheduler_stop_inflight_is_uncertain_without_resend(self):
        self.http.updates = [self.update()]; self.http.block = True
        stop = asyncio.Event()
        running = asyncio.create_task(self.transport.run(stop, interval=0.01))
        await asyncio.wait_for(self.http.entered.wait(),1)
        second = TelegramTransport(self.adapter, self.http, enabled=True)
        with self.assertRaises(ValueError): await second.run(asyncio.Event())
        stop.set()
        await asyncio.wait_for(running,1)
        self.assertEqual(self.rows()[0][2],'uncertain')
        before = len(self.http.calls)
        await self.transport.run(stop, interval=0.01)
        self.assertEqual(len(self.http.calls),before)
        self.assertFalse(await self.transport.deliver_once())

    async def test_scheduler_delivers_scope_and_keyboard_then_stops(self):
        self.http.updates = [self.update()]
        stop = asyncio.Event()
        original = self.http.call
        async def call(method,payload):
            result = await original(method,payload)
            if 'reply_markup' in payload: stop.set()
            return result
        self.http.call = call
        await asyncio.wait_for(self.transport.run(stop,interval=0.01),1)
        self.assertEqual([r[2] for r in self.rows()],['sent','sent'])

    def test_unicode_split(self):
        text = '😀'*4000+'abc'
        parts = split_text(text)
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(p.encode('utf-16-le'))//2 <=3500 for p in parts))
