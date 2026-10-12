"""Offline Telegram transport core. No token, networking, polling or webhook.

Bindings must be supplied by a trusted operator after out-of-band verification.
Telegram display names and client-supplied owner labels never establish identity.
Responses are plain text; a future sender must disable parse_mode and previews.
"""
import json
import secrets
import threading
from datetime import datetime, timezone
from functools import wraps
from .task_slice import digest

_locks = {}
_locks_guard = threading.Lock()

HELP = ('Fixture assistant commands:\n/help — show commands\n/tasks — list your tasks\n'
        '/fixture range-total — propose the registered fixture repair\n/status TASK_ID — progress and summary\n'
        '/review TASK_ID — refresh exact approval scope\n/ask TASK_ID QUESTION — ask about a task\n'
        '/stop [TASK_ID] — stop one task; no approval required')


def scope_text(scope):
    """Render every scope field as labelled prose, including unknown future fields."""
    labels={'executor':'Executor', 'fixture':'Registered fixture', 'operations':'Allowed steps',
            'writes':'Permitted writes', 'network':'Network access', 'commands':'Run commands',
            'models':'Use models during execution', 'publish':'Publish results', 'grant_seconds':'Execution grant (seconds)',
            'original_digest':'Original source fingerprint', 'executor_digest':'Executor fingerprint',
            'argv':'Exact command arguments', 'fixture_root':'Disposable fixture root',
            'timeout_seconds':'Child timeout (seconds)', 'output_bytes':'Maximum captured bytes', 'sandbox':'Containment limits',
            'planner':'Planning source'}
    def value(v):
        if isinstance(v,bool): return 'yes' if v else 'no'
        if isinstance(v,dict): return '\n'+ '\n'.join('  '+str(k).replace('_',' ').capitalize()+': '+value(x) for k,x in v.items())
        if isinstance(v,list): return '\n'+ '\n'.join('  '+str(i+1)+'. '+value(x) for i,x in enumerate(v))
        return str(v)
    return '\n'.join(labels.get(k,k.replace('_',' ').capitalize())+': '+value(v) for k,v in scope.items())


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class TelegramFixtureAdapter:
    def __init__(self, store):
        self.store = store
        with _locks_guard:
            self._lock = _locks.setdefault(str(store.path.resolve()), threading.RLock())
        with store.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS telegram_bindings (
                user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
                owner TEXT NOT NULL, active INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_callbacks (
                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL, owner TEXT NOT NULL, body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_updates (
                update_id INTEGER PRIMARY KEY, digest TEXT NOT NULL, response TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_binding_fences (
                user_id INTEGER PRIMARY KEY, generation INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_update_floor (id INTEGER PRIMARY KEY CHECK(id=1), floor INTEGER NOT NULL);
              INSERT OR IGNORE INTO telegram_update_floor VALUES(1,0);
            ''')

    @serialized
    def bind_verified(self, user_id, chat_id, owner):
        if type(user_id) is not int or not 0 < user_id < 2**63 or type(chat_id) is not int or chat_id != user_id:
            raise ValueError('Verified private user chat required')
        if not isinstance(owner, str) or not owner or len(owner) > 128:
            raise ValueError('Invalid owner')
        with self.store.db() as db:
            db.execute('INSERT INTO telegram_binding_fences VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET generation=generation+1', (user_id,))
            db.execute('INSERT OR REPLACE INTO telegram_bindings VALUES(?,?,?,1)',
                       (user_id, chat_id, owner))
            db.execute('DELETE FROM telegram_callbacks WHERE user_id=?', (user_id,))

    @serialized
    def revoke(self, user_id):
        with self.store.db() as db:
            db.execute('INSERT INTO telegram_binding_fences VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET generation=generation+1', (user_id,))
            db.execute('UPDATE telegram_bindings SET active=0 WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM telegram_callbacks WHERE user_id=?', (user_id,))

    def _binding(self, user_id, chat_id):
        with self.store.db(write=False) as db:
            row = db.execute('SELECT b.owner,f.generation FROM telegram_bindings b JOIN telegram_binding_fences f ON f.user_id=b.user_id WHERE b.user_id=? AND b.chat_id=? AND b.active=1',
                             (user_id, chat_id)).fetchone()
        if not row: raise ValueError('Access unavailable')
        return row

    def _identity(self, update):
        if not isinstance(update,dict) or type(update.get('update_id')) is not int or not 0 <= update['update_id'] < 2**63-1:
            raise ValueError('Invalid update')
        callback=update.get('callback_query')
        if callback is not None and not isinstance(callback,dict):raise ValueError('Invalid callback container')
        message=callback.get('message') if isinstance(callback,dict) else update.get('message')
        if not isinstance(message,dict): raise ValueError('Invalid message')
        sender=callback.get('from') if isinstance(callback,dict) else message.get('from')
        chat=message.get('chat')
        if (not isinstance(sender,dict) or not isinstance(chat,dict) or type(sender.get('id')) is not int
                or not 0<sender['id']<2**63 or sender.get('is_bot') is not False or chat.get('type')!='private'
                or type(chat.get('id')) is not int or chat.get('id')!=sender['id']):
            raise ValueError('Verified private user chat required')
        uid,cid=sender['id'],chat['id']
        owner,generation=self._binding(uid,cid)
        return uid,cid,owner,generation,message,callback

    @serialized
    def error_response(self, update, *, callback=False):
        uid,cid,owner,generation,_,_=self._identity(update)
        return {'text':('That approval is unavailable, expired, or has not been fully delivered. Use /tasks, then /review TASK_ID.'
                        if callback else 'I could not complete that command. Use /help or /tasks; use /stop TASK_ID to stop work.'),
                'buttons':[], 'delivery_binding':{'user_id':uid,'chat_id':cid,'owner':owner,'generation':generation}}

    @serialized
    def respond(self, update):
        try: return self.handle(update)
        except ValueError: return self.error_response(update,callback=isinstance(update,dict) and isinstance(update.get('callback_query'),dict))

    @serialized
    def current_review(self, binding, task_id):
        self.authorize_delivery(**binding)
        task=self.store.get(binding['owner'],task_id)
        if task['state']=='awaiting_approval' and self.store.clock()>=task['expires_at']:
            task=self.store.review(binding['owner'],task_id,'telegram-refresh:'+task_id+':'+str(task['revision']),task['revision'])
        result=self._review(binding['owner'],binding['user_id'],binding['chat_id'],task)
        result['delivery_binding']=binding
        return result

    @serialized
    def authorize_delivery(self, user_id, chat_id, owner, generation):
        """Future sender must check immediately before each outbound delivery."""
        if self._binding(user_id, chat_id) != (owner, generation):
            raise ValueError('Access changed')
        return True

    def _review(self, owner, user_id, chat_id, task):
        response = {'text': 'Fixture-only task '+task['id']+'\n'+task['goal']+'\nState: '+task['state'], 'buttons': []}
        if task['state'] == 'awaiting_approval':
            response['text'] += '\nExact scope:\n'+scope_text(task['scope'])+'\nScope digest: '+task['scope_digest']+'\nApproval expires (UTC): '+datetime.fromtimestamp(task['expires_at'],timezone.utc).isoformat()
            token = secrets.token_hex(16)
            with self.store.db() as db:
                db.execute('DELETE FROM telegram_callbacks WHERE json_extract(body,\'$.expires_at\') <= ?', (self.store.clock(),))
                if db.execute('SELECT COUNT(*) FROM telegram_callbacks').fetchone()[0] >= 1000:
                    raise ValueError('Callback limit reached')
                db.execute('INSERT INTO telegram_callbacks VALUES(?,?,?,?,?)', (token,user_id,chat_id,owner,json.dumps({k:task[k] for k in ('id','revision','scope_digest','nonce','expires_at')})))
            response['buttons'] = [{'text': 'Approve this exact fixture scope', 'callback_data': token}]
            response['review_task_id']=task['id']
        return response

    @serialized
    def handle(self, update):
        """Return an outbound message description; never send it or log updates."""
        uid,cid,owner,generation,message,callback=self._identity(update)
        is_stop=not callback and isinstance(message.get('text'),str) and message['text'].strip().split(maxsplit=1)[0:1]==['/stop']
        hashed = digest({'owner': owner, 'generation': generation, 'update': update})
        with self.store.db(write=False) as db:
            if update['update_id']<db.execute('SELECT floor FROM telegram_update_floor WHERE id=1').fetchone()[0]:
                raise ValueError('Update retired; use a new request')
            row = db.execute('SELECT digest,response FROM telegram_updates WHERE update_id=?', (update['update_id'],)).fetchone()
            if row:
                if row[0] != hashed: raise ValueError('Update identity conflict')
            elif not is_stop and db.execute('SELECT COUNT(*) FROM telegram_updates').fetchone()[0] >= 1000:
                raise ValueError('Offline update limit reached')
        if row:
            self.authorize_delivery(uid,cid,owner,generation)
            return json.loads(row[1])
        action = 'telegram:'+str(update['update_id'])
        if callback:
            token = callback.get('data')
            if not isinstance(token, str) or len(token) != 32: raise ValueError('Invalid approval')
            with self.store.db(write=False) as db:
                row = db.execute('SELECT body FROM telegram_callbacks WHERE token=? AND user_id=? AND chat_id=? AND owner=?', (token,uid,cid,owner)).fetchone()
            if not row: raise ValueError('Approval unavailable')
            grant = json.loads(row[0])
            if self.store.clock() >= grant['expires_at']: raise ValueError('Approval expired')
            task = self.store.approve(owner,grant['id'],action,grant['revision'],grant['scope_digest'],grant['nonce'])
            response = {'text': 'Fixture task '+task['id']+' state: '+task['state'], 'buttons': []}
        else:
            text = message.get('text')
            if not isinstance(text, str) or len(text) > 600: raise ValueError('Invalid command')
            parts = text.strip().split(maxsplit=2)
            if not parts: raise ValueError('Empty command')
            if parts[0]=='/help' and len(parts)==1:
                response={'text':HELP,'buttons':[]}
            elif parts[0]=='/tasks' and len(parts)==1:
                tasks=self.store.list(owner)
                response={'text':('Your tasks:\n'+'\n'.join(t['id']+' — '+t['state']+' — '+t['goal'] for t in tasks)) if tasks else 'No tasks yet. Use /fixture range-total.', 'buttons':[]}
            elif parts[0]=='/stop' and len(parts)==1:
                active=[t for t in self.store.list(owner) if t['state'] not in {'ready_for_review','stopped','failed'}]
                if len(active)==1:
                    task=self.store.stop(owner,active[0]['id'],action)
                    response={'text':'Stop state: '+task['state'],'buttons':[]}
                else:
                    response={'text':('Choose a task with /stop TASK_ID:\n'+'\n'.join(t['id']+' — '+t['state'] for t in active)) if active else 'No active tasks to stop. Use /tasks.', 'buttons':[]}
            elif parts[0] == '/fixture' and len(parts) == 2:
                task = self.store.create(owner,action,'Prepare a synthetic arithmetic fixture fix',parts[1])
                response = self._review(owner,uid,cid,task)
            elif parts[0] in {'/status','/review','/stop','/ask'} and len(parts) >= 2:
                task_id = parts[1]
                if parts[0] == '/stop':
                    task = self.store.stop(owner,task_id,action)
                    response = {'text': 'Stop state: '+task['state'], 'buttons': []}
                elif parts[0] == '/ask' and len(parts) == 3:
                    response = {'text': self.store.conversation(owner,task_id,action,'Telegram',parts[2]), 'buttons': []}
                else:
                    task = self.store.get(owner,task_id)
                    if parts[0] == '/review':
                        task = self.store.review(owner,task_id,action,task['revision'])
                    response = self._review(owner,uid,cid,task)
                    evidence=task['evidence']
                    response['text'] += '\nEvidence: '+str(len(evidence))+' recorded steps. Raw output is withheld from chat.'
                    for entry in evidence:
                        output=entry.get('output') or {}
                        response['text']+='\n'+str(entry.get('step','step'))+': '+('exit '+str(output['exit_code']) if 'exit_code' in output else 'recorded')
                    response['text']+='\nArtifact: '+('available for local review' if task['artifact'] else 'none yet')
            else: raise ValueError('Use /fixture, /status, /review, /ask or /stop')
        self.authorize_delivery(uid,cid,owner,generation)
        response['delivery_binding'] = {'user_id':uid,'chat_id':cid,'owner':owner,'generation':generation}
        # Store actions are idempotent across an interrupted response save.
        with self.store.db() as db:
            if not is_stop or db.execute('SELECT COUNT(*) FROM telegram_updates').fetchone()[0]<1000:
                db.execute('INSERT OR IGNORE INTO telegram_updates VALUES(?,?,?)', (update['update_id'],hashed,json.dumps(response)))
        return response
