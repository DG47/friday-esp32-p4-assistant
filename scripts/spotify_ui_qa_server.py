"""Loopback-only QA: actual simulator assets, actual MusicService, fake Spotify transport."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import time
from app.device_hub import DeviceHub
from app.tools.spotify_runtime import WebPlaybackSource
from app.tools.music_runtime import LocalMusicRuntime
from app.tools.lrclib import TimedFetch
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.tools.music import MusicService, Snapshot
from app.models import Surface
from app.tools.lyrics import TrackIdentity
from app.tools.lyrics_sync import LyricsSyncEngine,PlaybackObservation,TimedDocument,TimedLine
import uvicorn

class Response:
    def __init__(self,code,data=None,headers=None):self.status_code,self.data,self.headers=code,data,headers or {}
    def json(self):return self.data
class Fixture:
    def __init__(self):self.scenario='ready';self.playing=True;self.calls=[];self.now=0;self.position=1200
    async def request(self,method,path):
        self.calls.append((method,path))
        if method!='GET':
            if '/pause?' in path:self.playing=False
            if '/play?' in path:self.playing=True
            return Response(204)
        if self.scenario in {'empty','revoked','error','backoff'}:
            return Response({'empty':204,'revoked':401,'error':500,'backoff':429}[self.scenario],headers={'Retry-After':'120'})
        item={'id':'fixture','name':'Original synthetic fixture','album':{'name':'Original fixture album'},'artists':[{'name':'Sample artist'}],'duration_ms':10000 if self.scenario=='owned' else 180000}
        if self.scenario=='unicode':
            item['name']='Café 東京 🎵 '+('Extremely long original synthetic metadata '*4)
            item['artists']=[{'name':'Beyoncé Élodie / 東京 🎵 '+'Long artist '*8}]
        return Response(200,{'currently_playing_type':'track','item':item,'device':None if self.scenario=='no-device' else {'id':'fixture-device','name':'Fixture speaker','is_restricted':self.scenario=='restricted'},'is_playing':self.playing,'progress_ms':self.position if self.scenario=='owned' else 42000,'actions':{'disallows':{}}})
fixture=Fixture();music=MusicService('spotify',fixture,lambda:fixture.now)
sync_identity=TrackIdentity('originalfixture','','Original timing fixture | 10000ms')
sync_engine=LyricsSyncEngine(True,lambda:0)
sync_sequence=1
sync_engine.observe(PlaybackObservation(sync_sequence,sync_identity,1200,10000,False,0))
sync_engine.apply(sync_engine.begin_load(),TimedDocument(sync_identity,10000,(TimedLine(1000,'The copper kite turns.'),TimedLine(3000,'A paper boat follows.'),TimedLine(7000,'We leave a light by the gate.')),'Original synthetic text','Invented solely for offline QA'))
class OriginalLyrics:
    cache={}
    async def fetch(self,query):
        return TimedFetch('timecoded',TimedDocument(query.identity,10000,(TimedLine(1000,'The copper kite turns.'),TimedLine(3000,'A paper boat follows.'),TimedLine(7000,'We leave a light by the gate.')),'Original synthetic text','Invented solely for offline QA'))
qa_hub=DeviceHub()
owned_runtime=LocalMusicRuntime(qa_hub,WebPlaybackSource(music),OriginalLyrics(),True,True,lambda:fixture.now,poll_seconds=.05,music=music)
qa_hub.scene_observer=owned_runtime.interrupt_message
qa_sockets=set()
app=FastAPI();static=Path(__file__).resolve().parents[1]/'backend/app/static'
app.mount('/static',StaticFiles(directory=static),name='static')
@app.get('/')
async def index():return FileResponse(static/'index.html')
@app.post('/qa/{scenario}')
async def scenario(scenario:str):
    if scenario=='expire':
        fixture.now+=31
        return {'scenario':scenario}
    music.provider='none' if scenario=='disabled' else 'spotify'
    fixture.scenario=scenario;fixture.now+=200;music.last_read=float('-inf');music.retry_until=0;music.tokens.clear()
    return {'scenario':scenario}
@app.post('/qa/sync_position/{position}')
async def sync_position(position:int):
    global sync_sequence
    if not 0<=position<=10000:return {'error':'Invalid synthetic position'}
    sync_sequence+=1
    sync_engine.observe(PlaybackObservation(sync_sequence,sync_identity,position,10000,False,0))
    surface=sync_engine.synthetic_surface().model_dump(mode='json')
    for socket in list(qa_sockets):
        await socket.send_json({'type':'surface','surface':surface})
    return {'position':position}
@app.post('/qa/owned_position/{position}')
async def owned_position(position:int):
    fixture.position=position
    return {'position':position}
@app.get('/qa/state')
async def state():return {'calls':fixture.calls,'tokens':len(music.tokens),'action_ids':['musicctl:'+k for k in music.tokens]}
@app.websocket('/ws/device/{device}')
async def websocket(ws:WebSocket,device:str):
    await ws.accept();qa_hub.register(device,ws);qa_sockets.add(ws);await ws.send_json({'type':'hello.required'})
    try:
        while True:
            msg=await ws.receive_json();action=msg.get('action')
            if msg.get('type')=='hello':
                qa_hub.set_capabilities(device,ws,set(msg.get('capabilities',[])))
                await ws.send_json({'type':'ready','state':'idle','surface':{'kind':'ambient','title':'Friday'}})
            elif action=='music' or (isinstance(action,str) and action.startswith('musicctl:')):
                if fixture.scenario=='owned':
                    if action.startswith('musicctl:'):await music.scene(device,action);owned_runtime.stop(device,ws)
                    await owned_runtime.open(device,ws,msg.get('request_id'));continue
                try: surface=sync_engine.synthetic_surface() if fixture.scenario=='sync' else music._scene(device,Snapshot('stale')) if fixture.scenario=='stale' else await music.scene(device,action)
                except ValueError:
                    await ws.send_json({'type':'error','code':'bad_action','message':'Action expired; refresh music.'});continue
                await ws.send_json({'type':'surface','surface':surface.model_dump(mode='json')})
            elif action=='dismiss':
                owned_runtime.stop(device,ws)
                music.dismiss(device);await ws.send_json({'type':'surface','surface':{'kind':'ambient','title':'Friday'}})
            elif action=='status':
                owned_runtime.stop(device,ws)
                await ws.send_json({'type':'surface','surface':Surface(kind='metric',title='Local status',actions=[{'id':'music','label':'Music'},{'id':'dismiss','label':'Dismiss'}]).model_dump(mode='json')})
    except Exception as error:print(type(error).__name__, flush=True)
    finally:owned_runtime.stop(device,ws);qa_hub.disconnect(device,ws);qa_sockets.discard(ws)
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=18765,log_level='warning')
