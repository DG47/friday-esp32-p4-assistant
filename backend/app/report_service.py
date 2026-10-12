"""Explicit report supervisor, integrated only behind the production feature gate."""
import asyncio
import fcntl
import os
from .project_report import ReportWorker
from .task_telegram_transport import offloop

class ReportService:
    def __init__(self,store,planner,transport,*,enabled=False):
        self.store,self.planner,self.transport,self.enabled=store,planner,transport,enabled
        self.owners=sorted({grant.owner for grant in store.projects.values()})
        self.fd=None;self.lock=asyncio.Lock()
    async def start(self):
        if not self.enabled:raise ValueError('Report service is disabled')
        if self.fd is not None:raise ValueError('Already started')
        fd=os.open(str(self.store.path)+'.report-supervisor',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.fd=fd
            guard=getattr(self.transport,'acquire_consumer_guard',None)
            if guard is not None:await offloop(guard)
            configure=getattr(self.transport,'configure_authorized',None)
            if configure is not None:await configure()
            for owner in self.owners:await self.store.io(self.store.recover,owner)
            if self.transport is not None:await self.transport.recover()
        except BaseException:
            release=getattr(self.transport,'release_consumer_guard',None)
            if release is not None:release()
            self.fd=None;os.close(fd);raise
    async def tick(self):
        if self.fd is None:raise ValueError('Supervisor not started')
        async with self.lock:
            for owner in self.owners:
                tasks=await self.store.io(self.store.list,owner)
                for task in tasks:
                    if task['state']=='awaiting_planning':
                        await self.planner.propose(owner,task['id'])
                        current=await self.store.io(self.store.get,owner,task['id'])
                        if self.transport is not None and current.get('delivery_binding'):
                            try:await self.transport.notify(owner,task['id'])
                            except ValueError:pass
                        return True
                    if task['state']=='queued':
                        await ReportWorker(self.store,owner).run(task['id'])
                        current=await self.store.io(self.store.get,owner,task['id'])
                        if self.transport is not None and current.get('delivery_binding'):
                            try:await self.transport.notify(owner,task['id'])
                            except ValueError:pass
                        return True
                    if task['state'] in {'awaiting_approval','ready_for_review','failed','stopped','uncertain'} and self.transport is not None and task.get('delivery_binding'):
                        try:await self.transport.notify(owner,task['id'])
                        except ValueError:pass  # Revoked/stale binding: retain task locally, do not reroute.
            return False
    async def close(self):
        async with self.lock:
            if self.fd is not None:
                fd,self.fd=self.fd,None
                fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
                release=getattr(self.transport,'release_consumer_guard',None)
                if release is not None:release()
    async def run(self,stop_event):
        await self.start()
        delivery=asyncio.create_task(self.transport.run(stop_event),name='offline-report-telegram') if self.transport is not None else None
        try:
            while not stop_event.is_set():
                if delivery is not None and delivery.done():
                    await delivery
                    if not stop_event.is_set():raise RuntimeError('Report transport supervisor stopped')
                await self.tick()
                try:await asyncio.wait_for(stop_event.wait(),1)
                except TimeoutError:pass
        finally:
            if delivery is not None:
                delivery.cancel()
                await asyncio.gather(delivery,return_exceptions=True)
            await self.close()
