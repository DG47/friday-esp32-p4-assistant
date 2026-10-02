import asyncio
import logging
import unittest
from types import SimpleNamespace
import httpx
from app.device_hub import DeviceHub
from app.tools.music_runtime import LocalMusicRuntime,CAPABILITY
from app.tools.spotify_runtime import AuthorizedTransport,WebPlaybackSource,private_transport,create_runtime
from app.tools.music import MusicService
from app.tools.lyrics import TrackIdentity
from app.tools.lyrics_sync import PlaybackObservation,TimedDocument,TimedLine
from app.tools.lrclib import LyricQuery,TimedFetch
from app.config import Settings

class Socket:
    def __init__(self):self.messages=[]
    async def send_json(self,message):self.messages.append(message)
class Source:
    def __init__(self,clock):self.clock=clock;self.reads=0;self.track='a';self.position=100;self.playing=True;self.wait=None;self.cancelled=False
    async def read(self):
        self.reads+=1
        if self.wait:
            try:await self.wait.wait()
            except asyncio.CancelledError:self.cancelled=True;raise
        identity=TrackIdentity(self.track,'',self.track+' version')
        obs=PlaybackObservation(self.reads,identity,self.position,20000,self.playing,self.clock())
        return SimpleNamespace(status='ready',observation=obs,query=LyricQuery(identity,'Original fixture','Original author','Original album',20000))
class Provider:
    def __init__(self):self.calls=[];self.wait=None;self.cache={}
    async def fetch(self,query):
        self.calls.append(query)
        if self.wait:await self.wait.wait()
        return TimedFetch('timecoded',TimedDocument(query.identity,20000,(TimedLine(0,'Original first line',1000),TimedLine(1000,'Original second line',20000)),'Fixture','Original fixture; no copyrighted lyrics'))
async def settle():
    for _ in range(12):await asyncio.sleep(.01)

class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now=0.;self.clock=lambda:self.now;self.hub=DeviceHub();self.socket=Socket()
        self.hub.register('d',self.socket,{CAPABILITY});self.source=Source(self.clock);self.provider=Provider()
        self.runtime=LocalMusicRuntime(self.hub,self.source,self.provider,True,True,self.clock,poll_seconds=.04,view_seconds=10)
        self.hub.scene_observer=self.runtime.interrupt_message
    async def asyncTearDown(self):await self.runtime.close()
    async def test_default_and_old_client_make_zero_source_calls(self):
        disabled=LocalMusicRuntime(self.hub,self.source)
        with self.assertRaises(ValueError):await disabled.open('d',self.socket,'r')
        self.hub.set_capabilities('d',self.socket,set())
        surface=await self.runtime.open('d',self.socket,'r')
        self.assertIn('client update',surface.subtitle);self.assertEqual(self.source.reads,0)
        await disabled.close()
    async def test_clock_line_pause_seek_and_no_repeated_fetch(self):
        await self.runtime.open('d',self.socket,'r');await settle()
        self.assertEqual(len(self.provider.calls),1);self.assertEqual(self.runtime.engine.current()['line'],'Original first line')
        self.now=1.1;self.source.position=1200;await settle()
        self.assertEqual(self.runtime.engine.current()['line'],'Original second line')
        self.source.playing=False;self.source.position=100;await settle();self.now=2.;await settle()
        self.assertEqual(self.runtime.engine.current()['line'],'Original first line');self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(self.socket.messages[-1]['media_request_id'],'r')
        self.assertEqual([m['media_sequence'] for m in self.socket.messages],sorted(set(m['media_sequence'] for m in self.socket.messages)))
    async def test_track_changes_cancel_old_fetch_and_reanchor(self):
        self.provider.wait=asyncio.Event();await self.runtime.open('d',self.socket,'r');await settle()
        self.source.track='b';self.source.position=1500;await settle();self.provider.wait.set();await settle()
        self.assertEqual(self.runtime.engine.document.identity.track_id,'b');self.assertEqual(self.runtime.engine.current()['line'],'Original second line')
        self.assertEqual(len(self.provider.calls),2)
    async def test_dismiss_rapid_reentry_and_disconnect(self):
        await self.runtime.open('d',self.socket,'old');await settle();session=self.socket.messages[-1]['media_session_id']
        self.runtime.stop('d',self.socket);count=len(self.socket.messages);await settle();self.assertEqual(len(self.socket.messages),count)
        self.assertIsNone(self.runtime.latest);self.assertIsNone(self.runtime.engine.document)
        await self.runtime.open('d',self.socket,'new');await settle()
        self.assertNotEqual(self.socket.messages[-1]['media_session_id'],session)
        self.hub.disconnect('d',self.socket);await settle();self.assertFalse(self.runtime.views)
    async def test_reminder_confirmation_and_agent_priority(self):
        await self.runtime.open('d',self.socket,'r');await settle()
        await self.hub.broadcast('d',{'type':'reminder.alert','surface':{'kind':'reminder'}})
        self.assertFalse(self.runtime.views)
        with self.assertRaises(ValueError):await self.runtime.open('d',self.socket,'r2')
        self.runtime.clear_protection('d');await self.runtime.open('d',self.socket,'r3')
        await self.hub.broadcast('d',{'type':'surface','surface':{'kind':'message','actions':[{'id':'memyes:abc'}]}})
        self.assertIn('d',self.runtime.protected);self.assertFalse(self.runtime.views)
        self.runtime.clear_protection('d');await self.runtime.open('d',self.socket,'r4')
        await self.hub.broadcast('d',{'type':'state','state':'thinking'});self.assertFalse(self.runtime.views)
        with self.assertRaises(ValueError):await self.runtime.open('d',self.socket,'r5')
    async def test_queued_old_frame_is_rejected_after_dismiss(self):
        await self.runtime.open('d',self.socket,'r');await settle()
        peer=self.hub.connections['d'][0];await peer.send_lock.acquire()
        view=next(iter(self.runtime.views.values()));task=asyncio.create_task(self.runtime._publish(view,force=True))
        await asyncio.sleep(.01);self.runtime.stop('d',self.socket);count=len(self.socket.messages);peer.send_lock.release()
        await task;await settle();self.assertEqual(len(self.socket.messages),count)
    async def test_expiry_shutdown_and_owned_client_cleanup(self):
        self.source.wait=asyncio.Event();await self.runtime.open('d',self.socket,'r');await asyncio.sleep(.02)
        self.now=11;await settle();self.assertFalse(self.runtime.views);self.assertTrue(self.source.cancelled)
        self.assertIsNone(self.runtime.latest)
    async def test_shared_polling_and_one_peer_dismiss(self):
        other=Socket();self.hub.register('d',other,{CAPABILITY})
        await self.runtime.open('d',self.socket,'r');poll=self.runtime.poll_task
        await self.runtime.open('d',other,'s');self.assertIs(self.runtime.poll_task,poll);await settle()
        self.runtime.stop('d',self.socket);await settle();self.assertTrue(self.runtime.views);self.assertTrue(other.messages)
    async def test_default_factory_zero_credentials_or_client(self):
        runtime,music=create_runtime(Settings(_env_file=None),self.hub)
        self.assertIsNone(music);self.assertIsNone(runtime.owned_client);self.assertFalse(runtime.enabled);await runtime.close()
        with self.assertRaises(ValueError):Settings(_env_file=None,music_provider='spotify')
    async def test_private_transport_hides_query_logs_only_in_context(self):
        logger=logging.getLogger('httpx');records=[]
        class Capture(logging.Handler):
            def emit(self,record):records.append(record.getMessage())
        handler=Capture();old=logger.level;logger.addHandler(handler);logger.setLevel(logging.INFO)
        try:
            with private_transport():logger.info('PRIVATE_FIXTURE_QUERY')
            logger.info('public transport diagnostic')
            self.assertEqual(records,['public transport diagnostic'])
        finally:logger.removeHandler(handler);logger.setLevel(old)
    async def test_real_adapter_uses_official_get_with_injected_transport(self):
        calls=[]
        class Manager:
            blocked=None
            async def token(self):return 'synthetic-token'
            def clear(self):pass
        async def handler(request):
            calls.append(request)
            return httpx.Response(204)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source=WebPlaybackSource(MusicService('spotify',AuthorizedTransport(client,Manager())))
            result=await source.read()
        self.assertEqual(result.status,'no_playback');self.assertEqual(len(calls),1)
        self.assertEqual(str(calls[0].url),'https://api.spotify.com/v1/me/player');self.assertEqual(calls[0].method,'GET')

class GatewayTests(unittest.TestCase):
    def test_native_capability_routes_owned_frames_and_dismiss_returns_ambient(self):
        from fastapi.testclient import TestClient
        from app.main import app,hub,settings
        from unittest.mock import patch
        import tempfile
        from pathlib import Path
        from app.db import ReminderStore
        from app.agent_jobs import AgentJobStore
        from app.device_auth import DeviceCredentialStore
        import time
        with tempfile.TemporaryDirectory() as directory:
            store=ReminderStore(Path(directory)/'test.db');jobs=AgentJobStore(Path(directory)/'test.db');credentials=DeviceCredentialStore(Path(directory)/'test.db')
            source=Source(time.monotonic);runtime=LocalMusicRuntime(hub,source,Provider(),True,True,time.monotonic,poll_seconds=.05)
            with patch('app.main.store',store),patch('app.main.agent.store',store),patch('app.main.reminder_scheduler.store',store),patch('app.main.job_store',jobs),patch('app.main.job_worker.store',jobs),patch('app.main.credential_store',credentials),patch('app.main.local_music',runtime),patch.object(settings,'music_provider','spotify'):
                with TestClient(app) as client:
                    credentials.open_pairing_window('music-test','aaaaaaaa')
                    with client.websocket_connect('/ws/device/music-test') as socket:
                        self.assertEqual(socket.receive_json()['type'],'hello.required')
                        socket.send_json({'type':'hello','device_token':'a'*64,'capabilities':['display','touch',CAPABILITY]})
                        self.assertEqual(socket.receive_json()['type'],'ready');self.assertEqual(socket.receive_json()['type'],'state')
                        socket.send_json({'type':'scene.action','action':'music','request_id':'native-request'})
                        message=socket.receive_json();self.assertEqual(message['media_request_id'],'native-request')
                        self.assertEqual(message['surface']['kind'],'media')
                        socket.send_json({'type':'scene.action','action':'dismiss'})
                        for _ in range(8):
                            message=socket.receive_json()
                            if message.get('surface',{}).get('kind')=='ambient':break
                        self.assertEqual(message['surface']['kind'],'ambient')
                self.assertFalse(runtime.views);self.assertIsNone(runtime.latest)
