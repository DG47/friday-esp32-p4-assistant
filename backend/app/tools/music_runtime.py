"""Opt-in owned music views. Ephemeral timing and privacy; no model/history."""
import asyncio
import base64
import json
from dataclasses import dataclass,replace
import secrets
import time
from ..models import Surface
from .lyrics_sync import LyricsSyncEngine,PlaybackObservation
from .lyrics_session import TimedLyricsSession

CAPABILITY='media_updates_v1'

class MusicInteractionPending(ValueError):
    """Music cannot replace an unresolved or active interaction."""


@dataclass
class View:
    device: str
    websocket: object
    request: str
    session: str
    deadline: float
    sequence: int = 0
    signature: object = None
    suspended: bool = False
    overlay: str = ""
    artwork_sent: object = None

class LocalMusicRuntime:
    def __init__(self,hub,source=None,provider=None,enabled=False,timing=False,clock=time.monotonic,
                 poll_seconds=1,view_seconds=300,blocked=None,owned_client=None,music=None,artwork=None,owned_artwork_client=None):
        self.hub=hub;self.source=source;self.provider=provider;self.enabled=enabled;self.clock=clock
        self.poll_seconds=poll_seconds;self.view_seconds=view_seconds;self.blocked=blocked or (lambda device:False)
        self.artwork=artwork;self.owned_artwork_client=owned_artwork_client
        self.artwork_task=None;self.artwork_identity=None;self.artwork_generation=0;self.track_key=secrets.token_hex(12);self.cover=None;self.cover_status="unavailable"
        self.music=music;self.source_status="unconnected";self.controls={}
        self.owned_client=owned_client;self.views={};self.protected=set();self.memory_confirmations={};self.confirmations=set();self.reminder_devices=set();self.busy=set()
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
            self._retire(self.artwork_task)
            self.artwork_identity=None;self.cover=None;self.track_key=secrets.token_hex(12)
            if self.artwork:self.artwork.clear()
            self.latest=None
            self.engine.sequence+=1
            self.engine.observe(PlaybackObservation(self.engine.sequence+1,None,0,0,False,self.clock(),status='unavailable'))
            if self.music:self.music.tokens.clear()
            self.controls.clear()
            if self.provider and hasattr(self.provider,'cache'):self.provider.cache.clear()

    def persistent(self,view):
        return self.hub.peer_has_capability(view.device,view.websocket,'media_persistent_v1')

    def suspend(self,device,websocket=None,new_overlay=False):
        bindings={}
        for view in list(self.views.values()):
            if view.device!=device or websocket is not None and view.websocket is not websocket:continue
            if not self.persistent(view):self.stop(device,view.websocket);continue
            if not view.suspended or new_overlay:
                view.overlay=secrets.token_hex(12);view.sequence+=1
            view.suspended=True;view.signature=None;view.artwork_sent=None
            bindings[id(view.websocket)]={'media_request_id':view.request,'media_session_id':view.session,
                'overlay_id':view.overlay,'media_sequence':view.sequence,'state':'suspend'}
        if bindings:
            self.artwork_generation+=1;self.cover=None;self.cover_status='unavailable';self._retire(self.artwork_task)
        if self.views and all(v.suspended for v in self.views.values()):
            self._retire(self.artwork_task)
            self._retire(self.poll_task)
            if self.session:self._retire(self.session.task);self.session.last_identity=None
            self.latest=None;self.controls.clear()
            if self.music:self.music.tokens.clear()
        return bindings

    def interrupt_message(self,device,message):
        kind=message.get('type');surface=message.get('surface');protected=False
        if kind=='state':
            if message.get('state') in {'thinking','listening','speaking'}:self.busy.add(device)
            elif message.get('state')=='idle':
                self.busy.discard(device)
                if device in self.confirmations and device not in self.memory_confirmations and not self.blocked(device):
                    self.confirmations.discard(device)
                    if device not in self.reminder_devices:self.protected.discard(device)
        if kind=='reminder.alert':protected=True;self.reminder_devices.add(device)
        if isinstance(surface,dict):
            protected=surface.get('kind')=='reminder' or any(isinstance(a,dict) and
                (a.get('id') in {'confirm','decline'} or str(a.get('id','')).startswith(('memyes:','memno:')))
                for a in surface.get('actions',[]))
        if protected:
            if isinstance(surface,dict):
                for action in surface.get('actions',[]):
                    identity=action.get('id','') if isinstance(action,dict) else ''
                    if isinstance(identity,str) and identity.startswith(('memyes:','memno:')):
                        self.memory_confirmations[device]=identity.split(':')[1]
            self.protected.add(device)
            if isinstance(surface,dict) and surface.get('kind')=='reminder':self.reminder_devices.add(device)
            if kind!='reminder.alert' and isinstance(surface,dict) and surface.get('kind')!='reminder':self.confirmations.add(device)
        priority=(kind in {'surface','agent.result','reminder.alert','assistant.text'} or
            kind=='tool.activity' and message.get('status')=='working' or
            kind=='state' and message.get('state') in {'thinking','listening','speaking'})
        bindings=self.suspend(device,new_overlay=protected) if priority else {}
        def decorate(websocket,packet):
            binding=bindings.get(id(websocket))
            return dict(packet,media_overlay=binding) if binding else packet
        return decorate

    async def resume(self,device,websocket,message):
        view=self.views.get(self._key(device,websocket))
        fields={key:message.get(key) if isinstance(message.get(key),str) and len(message.get(key))<=128 else None for key in ('media_request_id','media_session_id','overlay_id')}
        status='ignored'
        if view and self.persistent(view) and view.suspended and fields=={
            'media_request_id':view.request,'media_session_id':view.session,'overlay_id':view.overlay}:
            status='pending' if device in self.protected or device in self.busy or self.blocked(device) else 'ready'
        def still_valid():
            return not self.closed and (status=='ignored' or self.views.get(self._key(device,websocket)) is view
                and view.suspended and view.overlay==fields['overlay_id']
                and (status!='ready' or device not in self.protected and device not in self.busy and not self.blocked(device)))
        sent=await self.hub.send_media(device,websocket,dict(type='media.resume.result',status=status,**fields),still_valid)
        if status=='ready' and sent and still_valid():
            view.suspended=False;view.signature=None;view.artwork_sent=None
            await self._publish(view,force=True)
            self._start_tasks()
        return status

    def _start_tasks(self):
        if self.poll_task is None or self.poll_task.done() or self.poll_task.cancelling():
            self.poll_task=asyncio.create_task(self._poll(),name='local-music-poll')
        if self.render_task is None or self.render_task.done() or self.render_task.cancelling():
            self.render_task=asyncio.create_task(self._render(),name='local-music-lines')

    def disconnected(self,device):
        if device not in self.hub.device_ids():self.busy.discard(device)

    def resolve_memory(self,device,token,resolved):
        if self.memory_confirmations.get(device)!=token or not resolved(device,token):return False
        self.memory_confirmations.pop(device,None);self.confirmations.discard(device)
        if device not in self.reminder_devices:self.protected.discard(device)
        return True

    def clear_protection(self,device):self.protected.discard(device);self.confirmations.discard(device);self.reminder_devices.discard(device);self.memory_confirmations.pop(device,None)
    def _allowed(self,view):
        return (not self.closed and self.views.get(self._key(view.device,view.websocket)) is view
            and not view.suspended and view.device not in self.protected and view.device not in self.busy
            and self.hub.peer_has_capability(view.device,view.websocket,CAPABILITY))

    async def open(self,device,websocket,request):
        if not self.enabled or self.closed:raise ValueError('Local music disabled')
        if not isinstance(request,str) or not 1<=len(request)<=128:raise ValueError('Music request identity required')
        if device in self.protected or device in self.busy or self.blocked(device):raise MusicInteractionPending('Resolve the current interaction first')
        if not self.hub.peer_has_capability(device,websocket,CAPABILITY):
            return Surface(kind='media',title='Local music',subtitle='Timed view requires a client update.',
                items=[{'time':'Mode','label':'Read only; automatic updates unavailable'}],actions=[{'id':'dismiss','label':'Dismiss'}],expires_ms=30000)
        self.views.pop(self._key(device,websocket),None)
        view=View(device,websocket,request,secrets.token_hex(12),self.clock()+self.view_seconds)
        self.views[self._key(device,websocket)]=view
        await self._publish(view,force=True)
        self._start_tasks()
        return None

    async def _read(self):
        async with self.read_lock:
            try:
                async with asyncio.timeout(10):result=await self.source.read()
            except asyncio.CancelledError:raise
            except Exception:result=None
            if not any(self._allowed(v) for v in self.views.values()):
                return PlaybackObservation(self.engine.sequence+1,None,0,0,False,self.clock(),status="unavailable"),None
            self.observation_sequence=max(self.observation_sequence,self.engine.sequence)+1
            self.source_status=result.status if result else "unavailable"
            self.latest=result if result and result.status=='ready' and result.observation else None
            self._update_artwork()
            if self.latest:return replace(result.observation,sequence=self.observation_sequence),result.query
            return PlaybackObservation(self.observation_sequence,None,0,0,False,self.clock(),status='unavailable'),None

    def _update_artwork(self):
        snapshot=self.music.cache if self.music and self.latest else None
        identity=(snapshot.track,snapshot.version,snapshot.artwork) if snapshot else None
        if identity!=self.artwork_identity:
            self._retire(self.artwork_task);self.artwork_identity=identity
            self.artwork_generation+=1;self.track_key=secrets.token_hex(12);self.cover=None
            self.cover_status='unavailable'
        capable=any(self._allowed(v) and self.hub.peer_has_capability(v.device,v.websocket,'media_artwork_v1') for v in self.views.values())
        if not snapshot or not snapshot.artwork or not self.artwork or not capable:return
        if self.cover is not None:return
        if self.artwork_task and not self.artwork_task.done() and not self.artwork_task.cancelling():return
        generation=self.artwork_generation;self.cover_status='loading'
        async def fetch():
            result=await self.artwork.fetch(snapshot.artwork)
            if generation==self.artwork_generation and identity==self.artwork_identity and any(self._allowed(v) for v in self.views.values()):
                self.cover=result.image;self.cover_status=result.status
        self.artwork_task=asyncio.create_task(fetch(),name='local-music-artwork')

    async def _reanchor(self):
        if not self.views:return None
        observation,_=await self._read()
        return observation

    async def _poll(self):
        try:
            while any(not v.suspended for v in self.views.values()) and not self.closed:
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
            actions=actions,expires_ms=None)
        return surface

    async def _publish(self,view,force=False):
        if not self._allowed(view):
            if not self.hub.peer_has_capability(view.device,view.websocket,CAPABILITY):self.stop(view.device,view.websocket)
            return
        surface=self._surface(view)
        payload=surface.model_dump(mode='json')
        artwork_signature=None
        if self.hub.peer_has_capability(view.device,view.websocket,'media_artwork_v1'):
            from .artwork import SIZE
            artwork_signature=(self.track_key,self.artwork_generation,self.cover_status,self.cover.digest if self.cover else None)
            art={'track_key':self.track_key,'width':SIZE,'height':SIZE,'format':'rgb565_le','status':self.cover_status}
            if self.cover:
                art['sha256']=self.cover.digest
                if view.artwork_sent!=artwork_signature:art['data_base64']=base64.b64encode(self.cover.pixels).decode('ascii')
            payload.update(track_key=self.track_key,artwork_generation=self.artwork_generation,artwork=art)
        signature=(artwork_signature,surface.title,surface.subtitle,repr(surface.items),repr(surface.actions),self.engine.generation)
        if not force and signature==view.signature:return
        view.signature=signature;view.sequence+=1
        message={'type':'surface','surface':payload,'media_request_id':view.request,
            'media_session_id':view.session,'media_sequence':view.sequence}
        limit=131072 if self.hub.peer_has_capability(view.device,view.websocket,'media_artwork_v1') else 16384
        if len(json.dumps(message,separators=(',',':')).encode())>limit:raise ValueError('Media frame exceeds capability budget')
        recipients=await self.hub.send_media(view.device,view.websocket,message,lambda:self._allowed(view))
        if recipients:
            view.artwork_sent=artwork_signature
            if payload.get('artwork',{}).get('data_base64'):
                import logging
                logging.getLogger("uvicorn.error").info("Native music artwork delivered bytes=%s sequence=%s persistent=%s",len(self.cover.pixels),view.sequence,payload.get('expires_ms') is None)
        elif not view.suspended:self.stop(view.device,view.websocket)

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
        if self.owned_artwork_client:await self.owned_artwork_client.aclose()
