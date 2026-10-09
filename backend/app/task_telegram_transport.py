"""Off-default single-process Telegram delivery service; no live configuration."""
import asyncio
import json
import logging
import re

import httpx

_locks = {}
_runners = set()
_poll_locks = {}


class _TokenFilter(logging.Filter):
    def __init__(self, token):
        super().__init__(); self.token = token

    def filter(self, record):
        record.msg = record.getMessage().replace(self.token, '[redacted]')
        record.args = ()
        return True


class TelegramHTTPS:
    """Fixed HTTPS connector. Construction does not perform any request."""
    offline_fixture = False
    methods = frozenset({'getUpdates', 'sendMessage', 'answerCallbackQuery'})

    def __init__(self, token, *, live_authorized=False, transport=None):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
            raise ValueError('Invalid configured Telegram token')
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError('Only MockTransport may override HTTPS transport')
        self._token, self.live_authorized = token, live_authorized
        self.offline_fixture = isinstance(transport, httpx.MockTransport)
        self._transport = transport
        redactor = _TokenFilter(token)
        for name in ('httpx', 'httpcore.http11', 'httpcore.http2', 'httpcore.connection', 'httpcore.proxy', 'httpcore.socks'):
            logging.getLogger(name).addFilter(redactor)

    async def call(self, method, payload):
        if method not in self.methods: raise ValueError('Unsupported Telegram method')
        if not self.offline_fixture and self.live_authorized is not True:
            raise ValueError('Live Telegram access is not authorized')
        try:
            async with httpx.AsyncClient(verify=True, trust_env=False, timeout=35 if method == 'getUpdates' else 5,
                                         follow_redirects=False, transport=self._transport) as client:
                async with client.stream('POST', 'https://api.telegram.org/bot'+self._token+'/'+method, json=payload) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 1_000_000: raise ValueError('Oversized response')
                        content.extend(chunk)
                # Bot API failures may use non-2xx with a structured ok:false body.
                body = json.loads(content)
                if not isinstance(body, dict) or type(body.get('ok')) is not bool:
                    raise ValueError('Invalid response')
                return body
        except Exception:
            raise ValueError('Telegram HTTPS request failed') from None


def split_text(text, limit=3500):
    """Bound Telegram text by UTF-16 units, without splitting a code point."""
    parts, current, size = [], [], 0
    for char in text:
        units = 2 if ord(char) > 0xffff else 1
        if size + units > limit:
            parts.append(''.join(current)); current, size = [], 0
        current.append(char); size += units
    if current: parts.append(''.join(current))
    return parts or [' ']


async def offloop(call, *args, **kwargs):
    """Drain an admitted thread operation before releasing its async authority lock."""
    task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try: await asyncio.shield(task)
            except asyncio.CancelledError: continue
        if not task.cancelled(): task.exception()
        raise


class TelegramTransport:
    ordinary_limit = 5000
    stop_reserve = 128
    retention_seconds = 86400

    def __init__(self, adapter, http, *, enabled=False):
        if getattr(http, 'offline_fixture', False) is not True and not isinstance(http, TelegramHTTPS):
            raise ValueError('Use an offline fixture or the fixed HTTPS connector')
        if enabled and isinstance(http, TelegramHTTPS) and not http.offline_fixture and http.live_authorized is not True:
            raise ValueError('Live Telegram access is not authorized')
        self.adapter, self.http, self.enabled = adapter, http, enabled
        key = str(adapter.store.path.resolve())
        self.lock = _locks.setdefault(key, asyncio.Lock())
        self.poll_lock = _poll_locks.setdefault(key, asyncio.Lock())
        self._initialize()

    @classmethod
    async def create(cls, store, http, **kwargs):
        from .task_telegram import TelegramFixtureAdapter
        adapter = await offloop(TelegramFixtureAdapter, store)
        return await offloop(cls, adapter, http, **kwargs)

    def _initialize(self):
        with self.adapter.store.db() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS telegram_transport_meta (id INTEGER PRIMARY KEY CHECK(id=1), offset INTEGER NOT NULL, retry_at REAL NOT NULL DEFAULT 0);
              INSERT OR IGNORE INTO telegram_transport_meta(id,offset) VALUES(1,0);
              CREATE TABLE IF NOT EXISTS telegram_outbox (
                update_id INTEGER NOT NULL, part INTEGER NOT NULL, method TEXT NOT NULL,
                payload TEXT NOT NULL, binding TEXT NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                wave INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0, review_task_id TEXT,
                PRIMARY KEY(update_id,part));
              CREATE TABLE IF NOT EXISTS telegram_decisions (update_id INTEGER PRIMARY KEY, outcome TEXT NOT NULL, created_at REAL NOT NULL);
            """)
            existing = {r[1] for r in db.execute('PRAGMA table_info(telegram_outbox)')}
            for name, definition in [('wave','INTEGER NOT NULL DEFAULT 0'), ('priority','INTEGER NOT NULL DEFAULT 0'),
                                     ('created_at','REAL NOT NULL DEFAULT 0'), ('review_task_id','TEXT')]:
                if name not in existing: db.execute('ALTER TABLE telegram_outbox ADD COLUMN '+name+' '+definition)

    async def bind_verified(self, *args):
        async with self.lock: await offloop(self.adapter.bind_verified, *args)

    async def revoke(self, user_id):
        async with self.lock: await offloop(self.adapter.revoke, user_id)

    def _recover(self):
        with self.adapter.store.db() as db:
            db.execute("UPDATE telegram_outbox SET status='uncertain' WHERE status='sending'")

    async def recover(self):
        async with self.lock: await offloop(self._recover)

    async def run(self, stop_event, *, interval=1):
        """Independent long-poll and delivery loops; explicit startup only."""
        if not self.enabled: return
        if not isinstance(interval,(int,float)) or not 0.01 <= interval <= 30:
            raise ValueError('Invalid scheduler interval')
        key = str(self.adapter.store.path.resolve())
        if key in _runners: raise ValueError('A transport consumer is already running')
        _runners.add(key)
        workers=[]
        async def loop(operation):
            failures=0
            while not stop_event.is_set():
                try:
                    changed=await operation()
                    failures=0
                except Exception:
                    changed=False; failures=min(failures+1,5)
                delay=0.01 if changed else min(30,interval*(2**failures))
                try: await asyncio.wait_for(stop_event.wait(),delay)
                except TimeoutError: pass
        try:
            await self.recover()
            workers=[asyncio.create_task(loop(self.poll_once)),asyncio.create_task(loop(self.deliver_once))]
            await stop_event.wait()
        finally:
            for task in workers: task.cancel()
            await asyncio.gather(*workers,return_exceptions=True)
            _runners.discard(key)

    def _offset(self):
        with self.adapter.store.db(write=False) as db:
            return db.execute('SELECT offset FROM telegram_transport_meta WHERE id=1').fetchone()[0]

    def _poll_parameters(self):
        with self.adapter.store.db(write=False) as db:
            offset,retry_at=db.execute('SELECT offset,retry_at FROM telegram_transport_meta WHERE id=1').fetchone()
        return None if self.adapter.store.clock()<retry_at else {'offset':offset,'limit':25,'timeout':20,
                                                               'allowed_updates':['message','callback_query']}

    def _authorize(self, binding, payload):
        self.adapter.authorize_delivery(**binding)
        keyboard=payload.get('reply_markup',{}).get('inline_keyboard')
        if not keyboard:return
        token=keyboard[0][0]['callback_data']
        with self.adapter.store.db(write=False) as db:
            row=db.execute('SELECT body FROM telegram_callbacks WHERE token=? AND user_id=? AND chat_id=? AND owner=?',
                           (token,binding['user_id'],binding['chat_id'],binding['owner'])).fetchone()
        if not row:raise ValueError('Approval unavailable')
        grant=json.loads(row[0]);task=self.adapter.store.get(binding['owner'],grant['id'])
        if (self.adapter.store.clock()>=grant['expires_at'] or task['state']!='awaiting_approval'
                or any(task[k]!=grant[k] for k in ('revision','scope_digest','nonce','expires_at'))):
            raise ValueError('Approval changed or expired')

    def _callback_admitted(self, update):
        callback=update.get('callback_query')
        if not isinstance(callback,dict):return
        token=callback.get('data')
        if not isinstance(token,str) or len(token)!=32:raise ValueError('Invalid callback')
        with self.adapter.store.db(write=False) as db:
            row=db.execute("SELECT binding FROM telegram_outbox o WHERE json_extract(payload,'$.reply_markup.inline_keyboard[0][0].callback_data')=? AND attempts>0 AND status IN ('sent','sending','uncertain') AND NOT EXISTS (SELECT 1 FROM telegram_outbox p WHERE p.update_id=o.update_id AND p.wave=o.wave AND p.part<o.part AND p.status!='sent')",(token,)).fetchone()
        if not row:raise ValueError('Full scope delivery is not established')
        binding=json.loads(row[0]);sender=callback.get('from') or {};chat=(callback.get('message') or {}).get('chat') or {}
        if sender.get('id')!=binding['user_id'] or chat.get('id')!=binding['chat_id']:
            raise ValueError('Callback owner changed')
        self.adapter.authorize_delivery(**binding)

    def _prune(self):
        """Only retire wholly confirmed/withheld old groups, never uncertain work."""
        now=self.adapter.store.clock()
        with self.adapter.store.db() as db:
            candidates=db.execute("SELECT update_id FROM telegram_outbox GROUP BY update_id HAVING MAX(created_at)<? AND SUM(status NOT IN ('sent','withheld'))=0",(now-self.retention_seconds,)).fetchall()
            for (number,) in candidates:
                tokens=db.execute("SELECT json_extract(payload,'$.reply_markup.inline_keyboard[0][0].callback_data') FROM telegram_outbox WHERE update_id=?",(number,)).fetchall()
                live=False
                for (token,) in tokens:
                    if token and db.execute("SELECT 1 FROM telegram_callbacks WHERE token=? AND json_extract(body,'$.expires_at')>?",(token,now)).fetchone():
                        live=True;break
                if live:continue
                db.execute('DELETE FROM telegram_outbox WHERE update_id=?',(number,))
                db.execute('DELETE FROM telegram_updates WHERE update_id=?',(number,))
                db.execute('UPDATE telegram_update_floor SET floor=MAX(floor,?) WHERE id=1',(number+1,))
            db.execute("DELETE FROM telegram_callbacks WHERE json_extract(body,'$.expires_at')<=?",(now,))
            db.execute('DELETE FROM telegram_decisions WHERE update_id NOT IN (SELECT update_id FROM telegram_decisions ORDER BY update_id DESC LIMIT 1000)')

    def _decision(self,number,outcome):
        with self.adapter.store.db() as db:
            db.execute('INSERT OR REPLACE INTO telegram_decisions VALUES(?,?,?)',(number,outcome,self.adapter.store.clock()))
            db.execute('UPDATE telegram_transport_meta SET offset=MAX(offset,?) WHERE id=1',(number+1,))
            db.execute('DELETE FROM telegram_decisions WHERE update_id NOT IN (SELECT update_id FROM telegram_decisions ORDER BY update_id DESC LIMIT 1000)')

    @staticmethod
    def _is_stop(update):
        message=update.get('message')
        return isinstance(message,dict) and isinstance(message.get('text'),str) and message['text'].strip().split(maxsplit=1)[0:1]==['/stop']

    def _enqueue(self,number,response,callback_id=None,priority=0,wave=0):
        binding=response['delivery_binding'];rows=[]
        if isinstance(callback_id,str) and 0<len(callback_id)<=256:
            rows.append(('answerCallbackQuery',{'callback_query_id':callback_id,'text':'Request received.'}))
        for text in split_text(response['text']):
            rows.append(('sendMessage',{'chat_id':binding['chat_id'],'text':text,'link_preview_options':{'is_disabled':True}}))
        if response.get('buttons'):
            rows.append(('sendMessage',{'chat_id':binding['chat_id'],'text':'Review the full scope above before approving.',
                         'reply_markup':{'inline_keyboard':[response['buttons']]},'link_preview_options':{'is_disabled':True}}))
        with self.adapter.store.db() as db:
            count=db.execute('SELECT COUNT(*) FROM telegram_outbox').fetchone()[0]
            limit=self.ordinary_limit+(self.stop_reserve if priority else 0)
            if len(rows)>128 or count+len(rows)>limit:return False
            start=db.execute('SELECT COALESCE(MAX(part)+1,0) FROM telegram_outbox WHERE update_id=?',(number,)).fetchone()[0]
            for index,(method,payload) in enumerate(rows):
                db.execute('INSERT INTO telegram_outbox(update_id,part,method,payload,binding,status,wave,priority,created_at,review_task_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (number,start+index,method,json.dumps(payload),json.dumps(binding),'pending',wave,priority,self.adapter.store.clock(),response.get('review_task_id')))
            db.execute('INSERT OR REPLACE INTO telegram_decisions VALUES(?,?,?)',(number,'enqueued',self.adapter.store.clock()))
            db.execute('UPDATE telegram_transport_meta SET offset=MAX(offset,?) WHERE id=1',(number+1,))
        return True

    def _process_updates(self,result):
        self._prune()
        if result.get('ok') is False and result.get('error_code')==429:
            delay=(result.get('parameters') or {}).get('retry_after',1)
            delay=delay if type(delay) is int and 0<delay<=86400 else 86400
            with self.adapter.store.db() as db:
                db.execute('UPDATE telegram_transport_meta SET retry_at=? WHERE id=1',(self.adapter.store.clock()+delay,))
            return 0
        if result.get('ok') is not True or not isinstance(result.get('result'),list):raise ValueError('Invalid polling response')
        updates=result['result']
        if len(updates)>25:raise ValueError('Polling batch limit')
        valid=[u for u in updates if isinstance(u,dict) and type(u.get('update_id')) is int and 0<=u['update_id']<2**63-1]
        count=0
        for update in sorted(valid,key=lambda u:u['update_id']):
            number=update['update_id']
            if number<self._offset():continue
            priority=int(self._is_stop(update))
            with self.adapter.store.db(write=False) as db: full=db.execute('SELECT COUNT(*) FROM telegram_outbox').fetchone()[0]>=self.ordinary_limit
            if full and not priority:
                self._decision(number,'backpressure: no action admitted');continue
            try:
                self.adapter._identity(update)
                self._callback_admitted(update)
                response=self.adapter.respond(update)
            except ValueError:
                try:response=self.adapter.error_response(update,callback=isinstance(update.get('callback_query'),dict))
                except ValueError:
                    self._decision(number,'invalid or unbound: no action admitted');continue
            callback=(update.get('callback_query') or {})
            callback_id=callback.get('id') if isinstance(callback,dict) else None
            if self._enqueue(number,response,callback_id,priority):count+=1
            else:self._decision(number,'stop admitted; response backpressure' if priority else 'response backpressure; action response retained in core cache')
        return count

    async def process_update(self, update):
        """Trusted update ingestion, also used for offline tests; no listener."""
        if not self.enabled:return 0
        async with self.lock:return await offloop(self._process_updates,{'ok':True,'result':[update]})

    async def poll_once(self):
        if not self.enabled:return 0
        async with self.poll_lock:
            parameters=await offloop(self._poll_parameters)
            if parameters is None:return 0
            # No binding/send lock spans long polling; revocation and Stop stay responsive.
            result=await asyncio.wait_for(self.http.call('getUpdates',parameters),35)
            async with self.lock:return await offloop(self._process_updates,result)

    def _prepare_delivery(self, allow_refresh=True):
        self._prune()
        with self.adapter.store.db(write=False) as db:
            row=db.execute("SELECT update_id,part,method,payload,binding,attempts,wave,priority,review_task_id FROM telegram_outbox o WHERE status='pending' AND retry_at<=? AND NOT EXISTS (SELECT 1 FROM telegram_outbox p WHERE p.update_id=o.update_id AND p.wave=o.wave AND p.part<o.part AND p.status!='sent') ORDER BY priority DESC,update_id,part LIMIT 1",(self.adapter.store.clock(),)).fetchone()
        if not row:return None
        number,part,method,payload,raw_binding,attempts,wave,priority,task_id=row
        binding=json.loads(raw_binding);payload=json.loads(payload)
        try:self.adapter.authorize_delivery(**binding)
        except ValueError:
            with self.adapter.store.db() as db:db.execute("UPDATE telegram_outbox SET status='uncertain' WHERE update_id=? AND status='pending'",(number,))
            return None
        try:self._authorize(binding,payload)
        except ValueError:
            with self.adapter.store.db() as db:db.execute("UPDATE telegram_outbox SET status='withheld' WHERE update_id=? AND part=? AND attempts=0",(number,part))
            if task_id and attempts==0 and allow_refresh:
                response=self.adapter.current_review(binding,task_id)
                if response.get('buttons') and self._enqueue(number,response,priority=priority,wave=wave+1):
                    return self._prepare_delivery(allow_refresh=False)
            return None
        if attempts>=3:
            with self.adapter.store.db() as db:db.execute("UPDATE telegram_outbox SET status='uncertain' WHERE update_id=? AND part=?",(number,part))
            return None
        with self.adapter.store.db() as db:db.execute("UPDATE telegram_outbox SET status='sending',attempts=attempts+1 WHERE update_id=? AND part=?",(number,part))
        return number,part,method,payload

    def _finish_delivery(self,number,part,status,retry_at=0):
        with self.adapter.store.db() as db:db.execute('UPDATE telegram_outbox SET status=?,retry_at=? WHERE update_id=? AND part=?',(status,retry_at,number,part))

    async def deliver_once(self):
        if not self.enabled:return False
        async with self.lock:
            row=await offloop(self._prepare_delivery)
            if not row:return False
            number,part,method,payload=row
            try:
                result=await asyncio.wait_for(self.http.call(method,payload),5)
                retry_at=0;receipt=result.get('result')
                confirmed=(receipt is True if method=='answerCallbackQuery' else isinstance(receipt,dict) and type(receipt.get('message_id')) is int)
                if result.get('ok') is True and confirmed:status='sent'
                elif result.get('ok') is False and result.get('error_code')==429:
                    delay=(result.get('parameters') or {}).get('retry_after',1)
                    if type(delay) is int and 0<delay<=86400:
                        status='pending';retry_at=self.adapter.store.clock()+delay
                    else:status='uncertain'
                else:status='uncertain'
            except BaseException:
                await offloop(self._finish_delivery,number,part,'uncertain')
                raise
            await offloop(self._finish_delivery,number,part,status,retry_at)
            return status=='sent'
