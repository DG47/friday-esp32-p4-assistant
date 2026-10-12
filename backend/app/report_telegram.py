"""Trusted-update report adapter; authenticated Telegram enrollment remains external."""
import json
from .task_slice import digest
from .task_telegram import TelegramFixtureAdapter, serialized
from .task_telegram_transport import TelegramTransport, offloop, split_text

HELP='/report PROJECT GOAL — propose an enrolled read-only report\n/review TASK_ID — display exact scope\n/status TASK_ID — verified state\n/ask TASK_ID QUESTION — shared task context\n/stop [TASK_ID] — request Stop\n/tasks — your tasks'

class ReportTelegramAdapter(TelegramFixtureAdapter):
    def _review(self,owner,user_id,chat_id,task):
        response=super()._review(owner,user_id,chat_id,task)
        response['text']=response['text'].replace('Fixture-only task ','Project report task ',1)
        response['text']+='\nLatest: '+task['events'][-1]['message']
        for button in response['buttons']:button['text']='Approve this exact report scope'
        if task['artifact']:
            response['text']+='\nVerified report SHA256: '+task['artifact']['sha256']+'\nReport contents remain local.'
        return response
    @serialized
    def handle(self,update):
        uid,cid,owner,generation,message,callback=self._identity(update)
        text=message.get('text','')
        if callback or not isinstance(text,str) or not text.startswith('/report '):
            if text.strip()=='/help' and not callback:
                result=super().handle(update);result['text']=HELP;return result
            if text.startswith('/fixture') and not callback:raise ValueError('Unsupported report capability')
            result=super().handle(update)
            result['text']=result['text'].replace('Fixture task ','Project report task ').replace('Use /fixture range-total.','Use /report PROJECT GOAL.')
            return result
        if len(text)>600:raise ValueError('Goal too large')
        parts=text.split(maxsplit=2)
        if len(parts)!=3:raise ValueError('Use /report PROJECT GOAL')
        hashed=digest({'owner':owner,'generation':generation,'update':update})
        with self.store.db(write=False) as db:
            if update['update_id']<db.execute('SELECT floor FROM telegram_update_floor WHERE id=1').fetchone()[0]:raise ValueError('Retired update')
            row=db.execute('SELECT digest,response FROM telegram_updates WHERE update_id=?',(update['update_id'],)).fetchone()
            if row:
                if row[0]!=hashed:raise ValueError('Update identity conflict')
                self.authorize_delivery(uid,cid,owner,generation);return json.loads(row[1])
            if db.execute('SELECT COUNT(*) FROM telegram_updates').fetchone()[0]>=1000:raise ValueError('Update capacity')
        task=self.store.create(owner,'telegram:'+str(update['update_id']),parts[2],parts[1])
        binding={'user_id':uid,'chat_id':cid,'owner':owner,'generation':generation}
        with self.store.db() as db:
            current=self.store._get(db,owner,task['id'])
            prior=current.get('delivery_binding')
            if prior and prior!=binding:raise ValueError('Task delivery binding changed')
            current['delivery_binding']=binding
            db.execute('UPDATE demo_tasks SET body=? WHERE id=? AND owner=?',(json.dumps(current),task['id'],owner))
        response=self._review(owner,uid,cid,task);response['text']+='\nPlanning pending; no files will be reported until exact scope approval.'
        response['delivery_binding']=binding;self.authorize_delivery(**binding)
        with self.store.db() as db:db.execute('INSERT OR IGNORE INTO telegram_updates VALUES(?,?,?)',(update['update_id'],hashed,json.dumps(response)))
        return response

class ReportTelegramTransport(TelegramTransport):
    """Mock-only rehearsal. No live connector is accepted by this slice."""
    def __init__(self,adapter,http,**kwargs):
        self.validate_http(http)
        super().__init__(adapter,http,**kwargs)
        with adapter.store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS report_notifications (owner TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL, identity INTEGER PRIMARY KEY AUTOINCREMENT, UNIQUE(owner,task_id,revision))')
    def validate_http(self,http):
        if getattr(http,'offline_fixture',False) is not True:raise ValueError('Only injected offline Telegram fixtures accepted')
    @classmethod
    async def create(cls,store,http,**kwargs):
        adapter=await offloop(ReportTelegramAdapter,store)
        return await offloop(cls,adapter,http,**kwargs)
    async def notify(self,owner,task_id):
        async with self.lock:return await offloop(self._notify_report,owner,task_id)
    def _notify_report(self,owner,task_id):
        task=self.adapter.store.get(owner,task_id);binding=task.get('delivery_binding')
        if not binding:raise ValueError('No report delivery binding')
        self.adapter.authorize_delivery(**binding)
        with self.adapter.store.db(write=False) as db:
            if db.execute('SELECT 1 FROM report_notifications WHERE owner=? AND task_id=? AND revision=?',(owner,task_id,task['revision'])).fetchone():return False
        response=self.adapter.current_review(binding,task_id)
        rows=[{'chat_id':binding['chat_id'],'text':text,'link_preview_options':{'is_disabled':True}} for text in split_text(response['text'])]
        if response['buttons']:rows.append({'chat_id':binding['chat_id'],'text':'Review the full scope above before approving.','reply_markup':{'inline_keyboard':[response['buttons']]},'link_preview_options':{'is_disabled':True}})
        with self.adapter.store.db() as db:
            if db.execute('SELECT 1 FROM report_notifications WHERE owner=? AND task_id=? AND revision=?',(owner,task_id,task['revision'])).fetchone():return False
            if db.execute('SELECT COUNT(*) FROM telegram_outbox').fetchone()[0]+len(rows)>self.ordinary_limit:raise ValueError('Notification backpressure; no delivery admitted')
            identity=db.execute('INSERT INTO report_notifications(owner,task_id,revision) VALUES(?,?,?)',(owner,task_id,task['revision'])).lastrowid
            for part,payload in enumerate(rows):
                db.execute('INSERT INTO telegram_outbox(update_id,part,method,payload,binding,status,wave,priority,created_at,review_task_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(-identity,part,'sendMessage',json.dumps(payload),json.dumps(binding),'pending',0,0,self.adapter.store.clock(),response.get('review_task_id')))
        return True
