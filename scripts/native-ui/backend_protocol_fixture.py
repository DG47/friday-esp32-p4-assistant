"""Real backend publisher/ownership/cover conversion -> native replay. All sources synthetic."""
import asyncio,copy,io,json,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
import httpx
from PIL import Image
from app.device_hub import DeviceHub
from app.models import Surface,SurfaceAction
from app.memory import MemoryStore
from app.tools.artwork import ArtworkRef,ArtworkProvider
from app.tools.music_runtime import LocalMusicRuntime
from app.tools.lyrics import TrackIdentity
from app.tools.lyrics_sync import PlaybackObservation,TimedDocument,TimedLine
from app.tools.lrclib import LyricQuery,TimedFetch

async def generate(destination):
    operations=[];now=[1.0];ref=ArtworkRef('https://i.scdn.co/image/'+'a'*40,192,192)
    image=Image.open(destination/'synthetic-cover.ppm');data=io.BytesIO();image.save(data,'PNG');source_bytes=data.getvalue()
    class Socket:
        def __init__(self):self.messages=[]
        async def send_json(self,message):
            m=copy.deepcopy(message);self.messages.append(m)
            if m['type']=='surface' and m.get('surface',{}).get('kind')=='media':operations.append(dict(op='media',frame=m,accepted=True))
            elif m['type']=='media.resume.result':operations.append(dict(op='result',frame=m))
            elif 'media_overlay' in m:operations.append(dict(op='overlay',frame=m))
            elif m['type']=='state':operations.append(dict(op='state',frame=m))
    class Music:
        def __init__(self):self.tokens={};self.cache=SimpleNamespace(track='synthetic-a',version='original',artwork=ref,context=('synthetic-a','fixture'),title='Quiet Satellites',artist='The Paper Observatory')
        def _scene(self,*args):return Surface(kind='media',title=self.cache.title,actions=[SurfaceAction(id='musicctl:'+f'{index:024x}',label=label) for index,label in enumerate(['Previous','Pause','Next'],1)]+[SurfaceAction(id='dismiss',label='Dismiss')])
    music=Music()
    class Source:
        def __init__(self):self.sequence=0
        async def read(self):
            self.sequence+=1;identity=TrackIdentity(music.cache.track,'original',music.cache.version)
            return SimpleNamespace(status='ready',observation=PlaybackObservation(self.sequence,identity,84000,222000,True,now[0]),query=LyricQuery(identity,music.cache.title,music.cache.artist,'Original synthetic album',222000))
    class Provider:
        async def fetch(self,query):return TimedFetch('timecoded',TimedDocument(query.identity,222000,(TimedLine(0,'We make a little room for the morning',222000),),'Original fixture','Original synthetic cover and timing fixture'))
    def request(view):return dict(media_request_id=view.request,media_session_id=view.session,overlay_id=view.overlay)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=source_bytes,headers={'Content-Type':'image/png'}))) as client:
        art=ArtworkProvider(client,True);hub=DeviceHub();socket=Socket();hub.register('fixture',socket,{'media_updates_v1','media_artwork_v1','media_persistent_v1'})
        with tempfile.TemporaryDirectory() as folder:
            store=MemoryStore(Path(folder)/'synthetic.db')
            runtime=LocalMusicRuntime(hub,Source(),Provider(),enabled=True,timing=True,clock=lambda:now[0],poll_seconds=.02,music=music,artwork=art,blocked=store.pending_confirmation)
            hub.scene_observer=runtime.interrupt_message;operations.append(dict(op='begin',request='backend-fixture'))
            await runtime.open('fixture',socket,'backend-fixture');await asyncio.sleep(.3)
            assert runtime.views,(runtime.source_status,runtime.cover_status,len(socket.messages))
            await runtime._publish(next(iter(runtime.views.values())),True)
            ready=next(m for m in reversed(socket.messages) if m.get('surface',{}).get('artwork',{}).get('status')=='ready')
            (destination/'backend-artwork-frame.json').write_text(json.dumps(ready,separators=(',',':')))
            operations.append(dict(op='check',artwork=True,suspended=False,capture='backend-artwork'))
            operations.append(dict(op='wait',milliseconds=1000000))
            now[0]=1001;await asyncio.sleep(.08);assert runtime.views
            operations.append(dict(op='check',suspended=False,capture='backend-persistent'))
            await hub.broadcast('fixture',dict(type='reminder.alert',reminder_id=7,text='A small break, if you please.',surface=dict(kind='reminder',title='Time for a small break',subtitle='A small break, if you please.',actions=[dict(id='complete:7',label='All done'),dict(id='snooze:7',label='Snooze')],expires_ms=5000)))
            view=next(iter(runtime.views.values()));binding=request(view)
            operations.append(dict(op='request',expected=None));operations.append(dict(op='media',frame=ready,accepted=False));operations.append(dict(op='check',artwork=False,suspended=True,capture='backend-reminder'))
            operations.extend([dict(op='resolved'),dict(op='request',expected=binding)])
            assert await runtime.resume('fixture',socket,binding)=='pending'
            operations.append(dict(op='check',suspended=True));runtime.clear_protection('fixture')
            operations.extend([dict(op='resolved'),dict(op='request',expected=binding)])
            assert await runtime.resume('fixture',socket,binding)=='ready';await asyncio.sleep(.1)
            operations.append(dict(op='check',artwork=True,suspended=False,capture='backend-resumed'))
            token=store.propose('fixture','remember',text='I prefer a quiet workspace in the morning.')
            store.review('fixture',token,0)
            await hub.broadcast('fixture',dict(type='surface',surface=dict(kind='message',title='Remember 1/1',subtitle='I prefer a quiet workspace in the morning.',actions=[dict(id='memyes:'+token,label='Confirm'),dict(id='memno:'+token,label='Cancel')])))
            view=next(iter(runtime.views.values()));binding=request(view)
            operations.append(dict(op='request',expected=None));operations.append(dict(op='check',suspended=True,capture='backend-confirmation'))
            await hub.broadcast('fixture',dict(type='state',state='idle'));assert await runtime.resume('fixture',socket,binding)=='pending'
            assert not runtime.resolve_memory('fixture','wrong',store.confirmation_resolved)
            store.decide('fixture',token,False);assert runtime.resolve_memory('fixture',token,store.confirmation_resolved)
            operations.append(dict(op='action',action='memno:'+token))
            await hub.broadcast('fixture',dict(type='surface',surface=dict(kind='message',title='Memory cancelled',subtitle='Nothing saved.',actions=[])))
            operations.append(dict(op='completed',frame=socket.messages[-1]));operations.append(dict(op='request',expected=binding))
            assert await runtime.resume('fixture',socket,binding)=='ready';await asyncio.sleep(.1)
            operations.append(dict(op='check',suspended=False,capture='backend-memory-resumed'))
            last=next(m for m in reversed(socket.messages) if m.get('surface',{}).get('kind')=='media')
            runtime.stop('fixture',socket);operations.append(dict(op='clear'));operations.append(dict(op='media',frame=last,accepted=False))
            operations.append(dict(op='begin',request='backend-new'));await runtime.open('fixture',socket,'backend-new');await asyncio.sleep(.1)
            operations.append(dict(op='media',frame=last,accepted=False));operations.append(dict(op='check',suspended=False,capture='backend-reentry'))
            hub.disconnect('fixture',socket);runtime.stop('fixture',socket);runtime.disconnected('fixture');operations.append(dict(op='clear'))
            assert not runtime.views;await runtime.close()
    (destination/'backend-stream.jsonl').write_text(''.join(json.dumps(o,separators=(',',':'))+'\n' for o in operations))
    print(json.dumps({'backend_native_replay_operations':len(operations),'original_synthetic_source_only':True,'actual_backend_cover_decode':True,'persistence_over_1000_seconds':True,'protected_reminder_pending_ready':True,'memory_idle_invalid_token_barrier':True,'verified_memory_cancel_resume':True,'late_frames_dismiss_reentry_disconnect':True,'max_frame_bytes':max(len(json.dumps(o.get('frame',{}),separators=(',',':')).encode()) for o in operations)}))

if __name__=='__main__':asyncio.run(generate(ROOT/'outputs/native-ui'))
