"""Explicit local memory; never provided to a model or conversation history."""
import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from .models import AgentReply, Surface, SurfaceAction


from .memory_text import text_pages, display_page, LAYOUT_VERSION


SECRET_PATTERN = re.compile(r'(?i)(password|api.?key|access.?token|private.?key|secret|bearer\s|sk-ant-|sk-proj-)')


def prepare_memory_input(text):
    """Reject recognizable credential requests before durable job ingestion."""
    if re.match(r'(?i)^\s*(remember\b|update memory\b)', text) and SECRET_PATTERN.search(text):
        return 'memory credential rejected'
    return text


def now():
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, path):
        self.path = path
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS explicit_memories (id INTEGER PRIMARY KEY, owner TEXT NOT NULL, text TEXT NOT NULL, provenance TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS memory_versions (memory_id INTEGER NOT NULL, revision INTEGER NOT NULL, owner TEXT NOT NULL, snapshot TEXT NOT NULL, archived_at TEXT NOT NULL, PRIMARY KEY(memory_id,revision))')
            db.execute('CREATE INDEX IF NOT EXISTS memory_owner ON explicit_memories(owner,status,id)')
            db.execute('CREATE TABLE IF NOT EXISTS memory_confirmations (token TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL)')

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def records(self, owner, page=0):
        if not 0 <= page <= 24:
            raise ValueError('Invalid memory page')
        with closing(self.connect()) as db:
            return [dict(r) for r in db.execute("SELECT * FROM explicit_memories WHERE owner=? AND status='active' ORDER BY id LIMIT 4 OFFSET ?", (owner,page*4))]

    def get(self, owner, identity):
        if not isinstance(identity,int) or not 1 <= identity <= 9223372036854775807:
            raise ValueError('Invalid memory ID')
        with closing(self.connect()) as db:
            r = db.execute("SELECT * FROM explicit_memories WHERE owner=? AND id=? AND status='active'",(owner,identity)).fetchone()
            if r is None: raise ValueError('Memory unavailable; refresh')
            return dict(r)

    def propose(self, owner, operation, identity=None, text='', request_id=None):
        if operation not in {'remember','update','forget'}: raise ValueError('Invalid operation')
        if operation in {'remember','update'} and any(not 32 <= ord(character) <= 126 for character in text):
            raise ValueError('Use printable ASCII text; this device font cannot review other characters')
        text = text.strip()
        if operation == 'forget' and text: raise ValueError('Forget requires only the exact memory ID')
        if operation == 'remember' and identity is not None: raise ValueError('Remember creates a new memory')
        if operation != 'remember' and identity is None: raise ValueError('An exact memory ID is required')
        if operation in {'remember','update'}:
            if not 1 <= len(text) <= 500: raise ValueError('Use 1-500 characters')
            if SECRET_PATTERN.search(text): raise ValueError('Do not store credentials or secrets')
        revision = self.get(owner,identity)['revision'] if identity is not None else None
        token = hashlib.sha256((owner+"\x00"+request_id).encode()).hexdigest()[:24] if request_id else secrets.token_hex(12)
        with closing(self.connect()) as db, db:
            existing = db.execute('SELECT * FROM memory_confirmations WHERE token=? AND owner=?',(token,owner)).fetchone()
            if existing is not None:
                payload = json.loads(existing['payload'])
                if (payload['operation'],payload['identity'],payload['text']) != (operation,identity,text): raise ValueError('Request ID reused for a different memory operation')
                if existing['status'] != 'pending' or existing['expires_at'] <= now(): raise ValueError('Memory request already resolved or expired')
                return token
            db.execute("UPDATE memory_confirmations SET status='superseded' WHERE owner=? AND status='pending'",(owner,))
            db.execute('INSERT INTO memory_confirmations VALUES (?,?,?,?,?)',(token,owner,json.dumps(dict(operation=operation,identity=identity,text=text,revision=revision,reviewed_through=-1,layout_version=LAYOUT_VERSION)),(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),'pending'))
        return token

    def review(self, owner, token, page):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM memory_confirmations WHERE owner=? AND token=? AND status='pending'",(owner,token)).fetchone()
            if row is None or row['expires_at'] <= now(): raise ValueError('Confirmation expired or used; refresh')
            payload = json.loads(row['payload'])
            if payload.get('layout_version') != LAYOUT_VERSION: raise ValueError('Memory layout changed; request a fresh confirmation')
            text = payload['text'] if payload['operation'] != 'forget' else self.get(owner,payload['identity'])['text']
            if payload['identity'] is not None and self.get(owner,payload['identity'])['revision'] != payload['revision']:
                raise ValueError('Memory changed; request fresh confirmation')
            pages = text_pages(text)
            previous = payload.get('reviewed_through',-1)
            if not 0 <= page < len(pages) or page > previous + 1:
                raise ValueError('Review every page in order before confirming')
            payload['reviewed_through'] = max(page,previous)
            db.execute('UPDATE memory_confirmations SET payload=? WHERE token=?',(json.dumps(payload),token))
            return payload,pages

    def decide(self, owner, token, accept):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            r = db.execute("SELECT * FROM memory_confirmations WHERE owner=? AND token=? AND status='pending'",(owner,token)).fetchone()
            if r is None or r['expires_at'] <= now(): raise ValueError('Confirmation expired or used; refresh')
            p = json.loads(r['payload'])
            if accept:
                if p.get('layout_version') != LAYOUT_VERSION: raise ValueError('Memory layout changed; request a fresh confirmation')
                preview = p['text'] if p['operation'] != 'forget' else self.get(owner,p['identity'])['text']
                if p.get('reviewed_through',-1) != len(text_pages(preview))-1:
                    raise ValueError('Review every page before confirming')
                if p['operation'] == 'remember':
                    if db.execute("SELECT count(*) FROM explicit_memories WHERE owner=? AND status='active'",(owner,)).fetchone()[0] >= 100: raise ValueError('100 active memory limit reached')
                    db.execute('INSERT INTO explicit_memories(owner,text,provenance,created_at,updated_at,status,revision) VALUES (?,?,?,?,?,?,1)',(owner,p['text'],'explicit-user-request',now(),now(),'active'))
                else:
                    previous = db.execute("SELECT * FROM explicit_memories WHERE owner=? AND id=? AND revision=? AND status='active'",(owner,p['identity'],p['revision'])).fetchone()
                    if previous is None: raise ValueError('Memory changed; request fresh confirmation')
                    db.execute('INSERT INTO memory_versions VALUES (?,?,?,?,?)',(p['identity'],p['revision'],owner,json.dumps(dict(previous)),now()))
                    c = db.execute("UPDATE explicit_memories SET text=CASE WHEN ?='update' THEN ? ELSE text END,status=CASE WHEN ?='forget' THEN 'forgotten' ELSE status END,updated_at=?,revision=revision+1 WHERE owner=? AND id=? AND revision=? AND status='active'",(p['operation'],p['text'],p['operation'],now(),owner,p['identity'],p['revision']))
                    if c.rowcount != 1: raise ValueError('Memory changed; request fresh confirmation')
            db.execute('UPDATE memory_confirmations SET status=? WHERE token=?',('confirmed' if accept else 'cancelled',token))


def button(identity, label): return SurfaceAction(id=identity,label=label)


class MemoryController:
    def __init__(self, settings, path):
        self.enabled = settings.assistant_memory_enabled
        self.store = MemoryStore(path) if self.enabled else None

    def confirmation(self, owner, token, page=0):
        payload,pages = self.store.review(owner,token,page)
        operation = payload['operation'].capitalize()
        if operation == 'Forget': operation = 'Hide (recoverable)'
        actions = [button(f'memreview:{token}:{page-1}','Previous')] if page else []
        if page < len(pages)-1:
            actions.append(button(f'memreview:{token}:{page+1}','Next'))
        else:
            actions.append(button('memyes:'+token,'Confirm'))
        actions.append(button('memno:'+token,'Cancel'))
        return Surface(kind='message',title=f'{operation} {page+1}/{len(pages)}',subtitle=display_page(pages[page]),actions=actions)

    def scene(self, owner, action):
        try:
            return self._scene(owner, action)
        except (ValueError, sqlite3.Error) as error:
            return Surface(kind="message", title="Memory unavailable", subtitle=str(error) if isinstance(error, ValueError) else "Local storage unavailable; retry later.")

    def _scene(self, owner, action):
        if not self.enabled: return Surface(kind='message',title='Memory disabled',subtitle='Requires separately approved deployment.')
        if action.startswith('memreview:'):
            _,token,page=action.split(':')
            return self.confirmation(owner,token,int(page))
        if action.startswith(('memyes:','memno:')):
            self.store.decide(owner,action.split(':')[1],action.startswith('memyes:'))
            return self.scene(owner,'memories')
        if action.startswith('memforget:'):
            identity = int(action.split(':')[1]); item = self.store.get(owner,identity)
            return self.confirmation(owner,self.store.propose(owner,'forget',identity))
        if action.startswith('memory:'):
            parts=action.split(':')
            item = self.store.get(owner,int(parts[1]))
            page=int(parts[2]) if len(parts)==3 else 0
            pages=text_pages(item['text'])
            if not 0 <= page < len(pages): raise ValueError('Memory page unavailable')
            actions=[button(f"memory:{item['id']}:{page-1}",'Previous')] if page else []
            if page < len(pages)-1: actions.append(button(f"memory:{item['id']}:{page+1}",'Next'))
            actions.extend([button(f"memforget:{item['id']}",'Forget'),button('memories','Back')])
            return Surface(kind='message',title=f"Memory #{item['id']} {page+1}/{len(pages)}",subtitle=display_page(pages[page]),items=[{'time':'Source','label':'Explicit user request'},{'time':'Created','label':item['created_at'][:19]},{'time':'Updated','label':item['updated_at'][:19]},{'time':'Edit','label':f"Say: update memory #{item['id']}: new text"}],actions=actions)
        page = int(action.split(':')[1]) if ':' in action else 0
        items = self.store.records(owner,page)
        actions = [button(f'memories:{page-1}','Previous')] if page else []
        if page<24 and self.store.records(owner,page+1): actions.append(button(f'memories:{page+1}','Next'))
        actions.append(button('dismiss','Dismiss'))
        return Surface(kind='list',title='What Friday remembers',subtitle='Local only · Say “remember: …” to add. No automatic extraction.',items=[{'time':f"#{i['id']}",'label':i['text'],'action':f"memory:{i['id']}"} for i in items],actions=actions)

    def handle(self, owner, text, request_id=None):
        if text == 'memory credential rejected':
            return AgentReply(text='Do not store credentials or secrets.',surface=Surface(kind='message',title='Memory request rejected',subtitle='Do not store credentials or secrets.'),provider='local',tool='explicit_memory')
        text = re.sub(r'(?i)^\s*remember(?: that)?\s+', 'remember: ', text)
        match = re.fullmatch(r'(?is)\s*(remember|update memory #?(\d+)|forget memory #?(\d+))\s*(?::\s*(.*))?\s*',text)
        recall = text.strip().lower().rstrip('.!?') in {'recall memory','show memory','show memories','what do you remember'}
        if not match and not recall:
            if re.match(r'(?i)^\s*(remember\b|update memory\b|forget memory\b)',text):
                return AgentReply(text='Use an explicit memory command.',surface=Surface(kind='message',title='Memory command help',subtitle='remember: text · update memory #ID: text · forget memory #ID'),provider='local',tool='explicit_memory')
            return None
        try:
            if not self.enabled or recall: surface = self.scene(owner,'memories')
            else:
                op = 'remember' if match[1].lower()=='remember' else ('update' if match[2] else 'forget')
                identity = int(match[2] or match[3]) if match[2] or match[3] else None
                token = self.store.propose(owner,op,identity,match[4] or '',request_id=request_id)
                surface = self.confirmation(owner,token)
        except (ValueError,sqlite3.Error) as error:
            surface = Surface(kind='message',title='Memory unavailable',subtitle=str(error) if isinstance(error,ValueError) else 'Storage unavailable; retry later.')
        return AgentReply(text=surface.subtitle or surface.title,surface=surface,provider='local',tool='explicit_memory')
