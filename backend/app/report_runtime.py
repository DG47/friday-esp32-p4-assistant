"""Off-default production dispatcher integration; enrollment is local trusted configuration."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import secrets
import stat

from .project_report import ProjectGrant, ReportStore, ReportPlanner, root_fd
from .providers.ollama_provider import OllamaProvider
from .report_context import ReportContext
from .report_service import ReportService
from .report_telegram import ReportTelegramTransport
from .report_telegram_production import attach_configured_telegram
from .task_slice import digest
from .experience import CAPABILITY
from .stop_observer import StopObserver
from .agent_jobs import JobAdmissionError

TERMINAL={'ready_for_review','failed','stopped','uncertain'}

class ReportStopPending(JobAdmissionError):
    code='report_stop_unresolved'
    def __init__(self):
        super().__init__('Report Stop outcome unresolved. No new work started; read report status or recovery on the Mac.')


class ReportRuntime:
    def __init__(self,store,planner,device_owners,hub,authorized,can_present):
        self.store,self.planner,self.hub=store,planner,hub
        self.bridge=ReportContext(store,device_owners);self.authorized,self.can_present=authorized,can_present
        self.service=ReportService(store,planner,None,enabled=True);self.transport=None;self.stop_observer=StopObserver();self.stop_tasks={}
        self.origins={};self.sent={};self.stop_event=asyncio.Event();self.ready=False;self.delivery_task=None
        with store.db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS report_device_views(device TEXT PRIMARY KEY,owner TEXT NOT NULL,task TEXT NOT NULL); CREATE TABLE IF NOT EXISTS report_device_deliveries(device TEXT NOT NULL,owner TEXT NOT NULL,task TEXT NOT NULL,revision INTEGER NOT NULL,delivery TEXT NOT NULL UNIQUE,acked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(device,task,revision));')
    def owner(self,device):
        if not self.authorized(device):raise ValueError('Authenticated paired report device required')
        return self.bridge.owner(device)
    async def attach_mock_telegram(self,http,bindings):
        self.transport=await ReportTelegramTransport.create(self.store,http,enabled=True)
        for user,chat,owner in bindings:await self.transport.bind_verified(user,chat,owner)
        self.service.transport=self.transport
    def _view(self,device):
        owner=self.owner(device)
        with self.store.db(write=False) as db:
            row=db.execute('SELECT task FROM report_device_views WHERE device=? AND owner=?',(device,owner)).fetchone()
        if row:return self.store.get(owner,row[0])
        tasks=self.store.list(owner)
        return tasks[0] if len(tasks)==1 else None
    async def available(self,device):
        try:return bool(await self.store.io(self._view,device))
        except ValueError:return False
    async def subscribe(self,device,task_id):
        owner=self.owner(device);await self.store.io(self.store.get,owner,task_id)
        def save():
            with self.store.db() as db:db.execute('INSERT INTO report_device_views VALUES(?,?,?) ON CONFLICT(device) DO UPDATE SET owner=excluded.owner,task=excluded.task',(device,owner,task_id))
        await self.store.io(save)
        origin=self.hub.experience.origin.get()
        if origin is None or origin.device!=device:raise ValueError('Valid report request identity required')
        self.hub.experience.claim(device);self.origins[device]=origin;self.sent.pop(device,None)
    async def _attach_delivery(self,owner,task_id):
        if self.transport is None:return
        async with self.transport.lock:
            def attach():
                with self.store.db() as db:
                    bindings=db.execute('SELECT b.user_id,b.chat_id,f.generation FROM telegram_bindings b JOIN telegram_binding_fences f ON f.user_id=b.user_id WHERE b.owner=? AND b.active=1',(owner,)).fetchall()
                    if len(bindings)!=1:return
                    user,chat,generation=bindings[0];task=self.store._get(db,owner,task_id)
                    binding={'user_id':user,'chat_id':chat,'owner':owner,'generation':generation}
                    if task.get('delivery_binding') not in (None,binding):raise ValueError('Report delivery requires fresh owner selection')
                    task['delivery_binding']=binding;db.execute('UPDATE demo_tasks SET body=? WHERE id=? AND owner=?',(json.dumps(task),task_id,owner))
            await self.store.io(attach)
    @staticmethod
    def accepts(text):
        value=text.strip()
        first=value.split(maxsplit=1)[0] if value else ''
        return first in {'report','/report','task','/task','/status','/ask'} or value.lower() in {'report status','task status'}
    def require_ready(self):
        if not self.ready or self.stop_event.is_set() or self.service.fd is None or self.delivery_task is not None and self.delivery_task.done():raise ValueError('Report supervisor unavailable')
    async def start(self):
        await self.service.start();self.ready=True
    def review(self,owner,task_id):
        self.require_ready()
        task=self.store.get(owner,task_id)
        for _ in range(3):
            if task['state']!='awaiting_approval' or self.store.clock()<task['expires_at']:return task
            try:return self.store.review(owner,task_id,'admin-review:'+secrets.token_hex(16),task['revision'])
            except ValueError:
                latest=self.store.get(owner,task_id)
                if latest['revision']==task['revision']:raise
                task=latest
        raise ValueError('Concurrent review changed; refresh')
    async def dispatch(self,device,text,request_id):
        self.require_ready()
        words=text.strip().split(maxsplit=2)
        command=words[0].lstrip('/') if words else ''
        if text.strip().lower() in {'report status','task status'}:
            command='status';words=['status']
        elif command in {'status','ask'} and not text.strip().startswith('/'):
            return None
        if not self.accepts(text):return None
        owner=self.owner(device)
        origin=self.hub.experience.origin.get()
        if not request_id or origin is None:raise ValueError('Report request_id required')
        if not any(peer.key==origin.peer and {CAPABILITY,'result_ack'}<=peer.capabilities for peer in self.hub.connections.get(device,[])):raise ValueError('Report device requires experience_context_v1 and result_ack')
        if command=='report':
            if await self.mutation_blocked(device):raise ReportStopPending()
            if len(words)!=3:raise ValueError('Use report PROJECT GOAL with a locally enrolled alias')
            task=await self.store.io(self.store.create,owner,'device:'+digest({'device':device,'request':request_id}),words[2],words[1])
            await self._attach_delivery(owner,task['id'])
        else:
            task_id=words[1] if len(words)>1 else None
            task=await self.store.io(self.store.get,owner,task_id) if task_id else await self.store.io(self._view,device)
            if not task:raise ValueError('No report task selected')
            if command=='ask':
                if len(words)!=3:raise ValueError('Use ask TASK_ID QUESTION')
                await self.store.io(self.store.conversation,owner,task['id'],'device-question:'+digest({'device':device,'request':request_id}),'LCD',words[2])
        await self.subscribe(device,task['id'])
        await self.hub.broadcast(device,{'type':'job.accepted','job_id':task['id']})
        await self.publish(device,force=True)
        return task['id']
    async def recover(self,device):
        task=await self.store.io(self._view,device)
        if not task:return False
        await self.subscribe(device,task['id']);await self.publish(device,force=True);return True
    async def mutation_blocked(self,device):
        if device not in self.bridge.device_owners:return False
        owner=self.owner(device)
        keys=[key for key in self.stop_tasks if key[:2]==(owner,device)]
        for key in keys:
            attempt=self.stop_observer.attempts.get(key)
            if attempt is None:continue
            persistence,observation,_=attempt
            if not persistence.done() or not observation.done():return True
            task=await self.store.io(self.store.get,owner,self.stop_tasks[key])
            if task['state'] in {'running','planning','stopping','uncertain'}:return True
        # Restart has no observer tasks; retain the existing explicit device view's
        # durable quarantine instead of inferring that absence means drained.
        def quarantined_view():
            with self.store.db(write=False) as db:
                rows=db.execute('SELECT task FROM report_device_stops WHERE device=? AND owner=? UNION SELECT task FROM report_device_views WHERE device=? AND owner=?',(device,owner,device,owner)).fetchall()
                return any(self.store._get(db,owner,row[0])['state'] in {'stopping','uncertain'} for row in rows)
        return await self.store.io(quarantined_view)
    def _stop_result(self,key,observation):
        task_id=self.stop_tasks.get(key)
        return replace(observation,message='Report task '+task_id+'. '+observation.message) if task_id else observation
    async def stop(self,device,token):
        owner=self.owner(device)
        key=(owner,device,token)
        if key in self.stop_observer.attempts:
            return self._stop_result(key,await self.stop_observer.request(key,None,None,None))
        grant=self.bridge.stop_grant(device,token)
        self.stop_tasks={k:v for k,v in self.stop_tasks.items() if k in self.stop_observer.attempts}
        self.stop_tasks[key]=grant['task']
        async def persist():
            return await self.store.io(self.bridge.stop,device,token)
        return self._stop_result(key,await self.stop_observer.request(key,grant['generation'],persist,
            lambda:self.service.runner(owner,grant['task'])))
    def _packet(self,device,task):
        owner=self.owner(device);surface=self.bridge.scene(device,task['id']).model_dump(mode='json')
        text='Project report '+task['id']+'. State: '+task['state']+'. '+task['events'][-1]['message']
        if task['state']=='awaiting_approval':
            surface['subtitle']+='; review exact scope on paired Telegram or trusted admin.'
            text+=' Review the full exact scope through the trusted local admin view or paired Telegram before approval.'
        if task['state'] not in TERMINAL:return {'type':'surface','job_id':task['id'],'report_revision':task['revision'],'surface':surface}
        with self.store.db() as db:
            row=db.execute('SELECT delivery,acked FROM report_device_deliveries WHERE device=? AND owner=? AND task=? AND revision=?',(device,owner,task['id'],task['revision'])).fetchone()
            if not row:
                row=(secrets.token_hex(16),0);db.execute('INSERT INTO report_device_deliveries VALUES(?,?,?,?,?,0)',(device,owner,task['id'],task['revision'],row[0]))
        if row[1]:return {'type':'surface','job_id':task['id'],'report_revision':task['revision'],'surface':surface}
        return {'type':'agent.result','job_id':task['id'],'delivery_id':row[0],'status':'completed' if task['state']=='ready_for_review' else 'failed','report_state':task['state'],'report_revision':task['revision'],'text':text,'surface':surface}
    async def publish(self,device,force=False):
        origin=self.origins.get(device)
        if not origin or self.hub.experience.current.get(device)!=origin or not self.can_present(device):return False
        try:task=await self.store.io(self._view,device)
        except ValueError:return False
        if not task or not force and self.sent.get(device)==(task['id'],task['revision']):return False
        packet=await self.store.io(self._packet,device,task)
        with self.hub.experience.scope(device,origin.request,origin.peer):
            if self.hub.experience.current.get(device)!=origin or not self.can_present(device):return False
            recipients=await self.hub.broadcast(device,packet)
        if recipients:self.sent[device]=(task['id'],task['revision'])
        return bool(recipients)
    def acknowledge(self,device,task_id,delivery):
        owner=self.owner(device)
        with self.store.db() as db:
            row=db.execute('SELECT acked FROM report_device_deliveries WHERE device=? AND owner=? AND task=? AND delivery=?',(device,owner,task_id,delivery)).fetchone()
            if row is None:return False
            db.execute('UPDATE report_device_deliveries SET acked=1 WHERE device=? AND owner=? AND task=? AND delivery=?',(device,owner,task_id,delivery));return True
    async def run(self):
        if self.service.fd is None:await self.start()
        delivery=asyncio.create_task(self.transport.run(self.stop_event)) if self.transport else None
        self.delivery_task=delivery
        try:
            while not self.stop_event.is_set():
                if delivery and delivery.done():
                    await delivery
                    raise RuntimeError('Report transport stopped')
                await self.service.tick()
                for device in list(self.origins):await self.publish(device)
                await self.store.wait_changed(timeout=.25)
        finally:
            self.ready=False
            if delivery:delivery.cancel();await asyncio.gather(delivery,return_exceptions=True)
            await self.stop_observer.close()
            await self.service.close()

async def create_report_runtime(settings,hub,authorized,can_present):
    if not settings.assistant_reports_enabled:return None
    if settings.assistant_provider!='local-only':raise ValueError('Reports require explicit local-only reasoning')
    if not settings.assistant_reports_allow_real_projects and settings.assistant_env!='test':raise ValueError('Real-project enrollment is disabled')
    path=settings.assistant_reports_enrollment_path
    if path is None:raise ValueError('Explicit report enrollment required')
    def load():
        validation=settings.assistant_reports_validation_root
        if not settings.assistant_reports_allow_real_projects and (validation is None or not path.is_relative_to(validation)):
            raise ValueError('Enrollment must stay in explicit synthetic validation root')
        parent=root_fd(path.parent)
        try:
            fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            with os.fdopen(fd,'rb') as source:
                info=os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077 or info.st_size>65536:raise ValueError('Private bounded enrollment file required')
                value=json.loads(source.read(65537))
        finally:os.close(parent)
        if not isinstance(value,dict) or set(value)!={'version','state_path','artifact_root','device_owners','projects'} or value['version']!=1:raise ValueError('Invalid report enrollment schema')
        if not isinstance(value['projects'],list) or not 1<=len(value['projects'])<=16 or not isinstance(value['device_owners'],dict) or len(value['device_owners'])>32:raise ValueError('Invalid report enrollment bounds')
        grants=[]
        for entry in value['projects']:
            if not isinstance(entry,dict) or set(entry)!={'owner','alias','root','files'}:raise ValueError('Invalid project grant')
            if not settings.assistant_reports_allow_real_projects and not Path(entry['root']).is_relative_to(validation):raise ValueError('Project outside synthetic validation root')
            grants.append(ProjectGrant(entry['owner'],entry['alias'],Path(entry['root']),tuple(entry['files'])))
        owners={grant.owner for grant in grants}
        for device,owner in value['device_owners'].items():
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',device) or owner not in owners:raise ValueError('Invalid device-owner enrollment')
        if not settings.assistant_reports_allow_real_projects:
            validation=settings.assistant_reports_validation_root
            if validation is None:raise ValueError('Synthetic validation root required')
            root_fd_check=root_fd(validation);os.close(root_fd_check)
            if not all(grant.root.is_relative_to(validation) for grant in grants):raise ValueError('Only enrolled synthetic validation roots allowed')
        if not settings.assistant_reports_allow_real_projects and not all(Path(value[key]).is_relative_to(validation) for key in ('state_path','artifact_root')):raise ValueError('State/output outside synthetic validation root')
        store=ReportStore(Path(value['state_path']),grants,Path(value['artifact_root']))
        return store,value['device_owners']
    store,owners=await asyncio.to_thread(load)
    provider=OllamaProvider(settings.ollama_base_url,settings.ollama_model,local_only=True,context_tokens=8192)
    runtime=ReportRuntime(store,ReportPlanner(store,provider),owners,hub,authorized,can_present)
    await attach_configured_telegram(runtime,settings)
    return runtime
