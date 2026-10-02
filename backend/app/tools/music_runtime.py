"""Opt-in owned music views. Ephemeral timing and privacy; no model/history."""
import asyncio
from dataclasses import dataclass,replace
import secrets
import time
from ..models import Surface
from .lyrics_sync import LyricsSyncEngine,PlaybackObservation
from .lyrics_session import TimedLyricsSession

CAPABILITY='media_updates_v1'

@dataclass
class View:
    device: str
    websocket: object
    request: str
    session: str
    deadline: float
    sequence: int = 0
    signature: object = None

class LocalMusicRuntime:
    def __init__(self,hub,source=None,provider=None,enabled=False,timing=False,clock=time.monotonic,
                 poll_seconds=1,view_seconds=300,blocked=None,owned_client=None,music=None):
        self.hub=hub;self.source=source;self.provider=provider;self.enabled=enabled;self.clock=clock
        self.poll_seconds=poll_seconds;self.view_seconds=view_seconds;self.blocked=blocked or (lambda device:False)
        self.music=music;self.source_status="unconnected";self.controls={}
        self.owned_client=owned_client;self.views={};self.protected=set();self.busy=set()
        self.engine=LyricsSyncEngine(enabled=timing,clock=clock)
        self.session=TimedLyricsSession(self.engine,provider,reanchor=self._reanchor) if timing and provider else None
        self.latest=None;self.observation_sequence=0;self.read_lock=asyncio.Lock()
        self.poll_task=None;self.render_task=None;self.retired=set();self.closed=False

    def _key(self,device,websocket):return device,id(websocket)
    def _retire(self,task):
        if task and not task.done():
            task.cancel();self.retired.add(task);task.add_done_callback(self.retired.discard)
    def stop(self,device,websocket=None):
        for key,view in list(self.views.items()):
            if view.device==device and (websocket is None or view.websocket is websocket):self.views.pop(key,None)
        if not self.views:
            self._retire(self.poll_task);self._retire(self.render_task)
            if self.session:self._retire(self.session.task);self.session.last_identity=None
            self.latest=None
            self.engine.sequence+=1
            self.engine.observe(PlaybackObservation(self.engine.sequence+1,None,0,0,False,self.clock(),status='unavailable'))
            if self.music:self.music.tokens.clear()
            self.controls.clear()
            if self.provider and hasattr(self.provider,'cache'):self.provider.cache.clear()

    def interrupt_message(self,device,message):
        kind=message.get('type');surface=message.get('surface')
        if kind=='state':
            if message.get('state') in {'thinking','listening','speaking'}:
                self.busy.add(device);self.stop(device)
            elif message.get('state')=='idle':self.busy.discard(device)
        if kind=='reminder.alert':self.protected.add(device)
        if isinstance(surface,dict):
            actions=surface.get('actions',[])
            if (surface.get('kind')=='reminder' or any(isinstance(a,dict) and
                (a.get('id') in {'confirm','decline'} or str(a.get('id','')).startswith(('memyes:','memno:'))) for a in actions)):
                self.protected.add(device)
        if kind in {'surface','agent.result','reminder.alert','assistant.text'} or kind=='tool.activity' and message.get('status')=='working':
            self.stop(device)

    def clear_protection(self,device):self.protected.discard(device)
    def _allowed(self,view):
        return (not self.closed and self.views.get(self._key(view.device,view.websocket)) is view
            and self.clock()<view.deadline and view.device not in self.protected and view.device not in self.busy
            and self.hub.peer_has_capability(view.device,view.websocket,CAPABILITY))

    async def open(self,device,websocket,request):
        if not self.enabled or self.closed:raise ValueError('Local music disabled')
        if not isinstance(request,str) or not 1<=len(request)<=128:raise ValueError('Music request identity required')
        if device in self.protected or device in self.busy or self.blocked(device):raise ValueError('Resolve the current interaction first')
        if not self.hub.peer_has_capability(device,websocket,CAPABILITY):
            return Surface(kind='media',title='Local music',subtitle='Timed view requires a client update.',
                items=[{'time':'Mode','label':'Read only; automatic updates unavailable'}],actions=[{'id':'dismiss','label':'Dismiss'}],expires_ms=30000)
        self.views.pop(self._key(device,websocket),None)
        view=View(device,websocket,request,secrets.token_hex(12),self.clock()+self.view_seconds)
        self.views[self._key(device,websocket)]=view
        await self._publish(view,force=True)
        if self.poll_task is None or self.poll_task.done() or self.poll_task.cancelling():
            self.poll_task=asyncio.create_task(self._poll(),name='local-music-poll')
        if self.render_task is None or self.render_task.done() or self.render_task.cancelling():
            self.render_task=asyncio.create_task(self._render(),name='local-music-lines')
        return None

    async def _read(self):
        async with self.read_lock:
            try:
                async with asyncio.timeout(10):result=await self.source.read()
            except asyncio.CancelledError:raise
            except Exception:result=None
            self.observation_sequence=max(self.observation_sequence,self.engine.sequence)+1
            self.source_status=result.status if result else "unavailable"
            self.latest=result if result and result.status=='ready' and result.observation else None
            if self.latest:return replace(result.observation,sequence=self.observation_sequence),result.query
            return PlaybackObservation(self.observation_sequence,None,0,0,False,self.clock(),status='unavailable'),None

    async def _reanchor(self):
        if not self.views:return None
        observation,_=await self._read()
        return observation

    async def _poll(self):
        try:
            while self.views and not self.closed:
                observation,query=await self._read()
                if not self.views:return
                if self.session:self.session.observe(observation,query)
                # Metadata-only mode never invokes a lyric provider.
                await asyncio.sleep(self.poll_seconds)
        except asyncio.CancelledError:raise
        except Exception:
            for device,_ in list(self.views):self.stop(device)

    def _surface(self,view):
        current=self.engine.current() if self.session else {'status':'timing_disabled','line':None}
        title='Spotify';artist='Not available';subtitle='Reading Spotify playback.'
        progress='Unavailable';state='Source unavailable';source='Spotify | remote devices'
        if self.latest:
            query=self.latest.query;observation=self.latest.observation
            if query:title=query.track_name[:64];artist=query.artist_name[:80]
            elif self.music:title=self.music.cache.title;artist=self.music.cache.artist
            position=observation.position_ms
            if observation.playing:position+=int(max(0,min(5,self.clock()-observation.observed_at))*1000)
            position=min(observation.duration_ms,position)
            def stamp(ms):return f'{ms//60000}:{ms//1000%60:02d}'
            progress=stamp(position)+' / '+stamp(observation.duration_ms)
            state='Playing' if observation.playing else 'Paused'
            subtitle=current.get('line') or {
                'loading':'Loading timed lyrics.','no_lyrics':'No synced lyrics.','no_synced_lyrics':'No synced lyrics.',
                'timecoded':'No current line.','waiting':'Instrumental gap.','ended':'Playback ended.',
                'rate_limited':'Lyrics rate limited.','backoff':'Lyrics retry is waiting.','unmatched':'Recording mismatch; lyrics hidden.',
                'invalid_timing':'Invalid lyric timing; text hidden.','stale':'Playback clock stale; lyrics hidden.',
                'unavailable':'Timed lyrics unavailable.','timing_disabled':'Timed lyrics disabled.'}.get(current['status'],'No current line.')
            if self.engine.document:source=self.engine.document.attribution
            if self.clock()-observation.observed_at>5:state='Stale; controls hidden'
            if query is None:subtitle='Recording metadata incomplete; lyrics hidden.'
        elif self.latest is None:
            subtitle={'no_playback':'No Spotify playback.','no_device':'No active Spotify device.',
                'unsupported':'Unsupported item; lyrics hidden.','revoked':'Spotify connection unavailable; reconnect.',
                'rate_limited':'Spotify rate limited; waiting.','forbidden_or_quota':'Spotify access restricted.'}.get(self.source_status,'Spotify unavailable; no lyrics shown.')
        actions=[{'id':'dismiss','label':'Dismiss'}]
        if self.music and self.latest and self.latest.status=='ready' and self.clock()-self.latest.observation.observed_at<=5:
            snapshot=self.music.cache
            entry=self.controls.get(view.device)
            if not entry or snapshot.context!=entry[0] or self.clock()>=entry[1]:
                entry=(snapshot.context,self.clock()+25,self.music._scene(view.device,snapshot).actions)
                self.controls[view.device]=entry
            actions=entry[2]
        surface=Surface(kind='media',title=title,subtitle=subtitle,
            items=[{'time':'Artist','label':artist},{'time':'Progress','label':progress},
                {'time':'Source','label':source},{'time':'State','label':state}],
            actions=actions,expires_ms=max(1000,int((view.deadline-self.clock())*1000)))
        return surface

    async def _publish(self,view,force=False):
        if not self._allowed(view):self.stop(view.device,view.websocket);return
        surface=self._surface(view)
        signature=(surface.title,surface.subtitle,repr(surface.items),repr(surface.actions),self.engine.generation)
        if not force and signature==view.signature:return
        view.signature=signature;view.sequence+=1
        message={'type':'surface','surface':surface.model_dump(mode='json'),'media_request_id':view.request,
            'media_session_id':view.session,'media_sequence':view.sequence}
        recipients=await self.hub.send_media(view.device,view.websocket,message,lambda:self._allowed(view))
        if not recipients:self.stop(view.device,view.websocket)

    async def _render(self):
        try:
            while self.views and not self.closed:
                for view in list(self.views.values()):await self._publish(view)
                await asyncio.sleep(.1)
        except asyncio.CancelledError:raise
        except Exception:
            for device,_ in list(self.views):self.stop(device)

    async def close(self):
        self.closed=True
        for device,_ in list(self.views):self.stop(device)
        tasks=list(self.retired)
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if self.session:await self.session.close()
        self.latest=None;self.engine.document=None
        if self.owned_client:await self.owned_client.aclose()
