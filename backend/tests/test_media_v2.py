import asyncio
import base64
import hashlib
import io
import json
import unittest
from types import SimpleNamespace
import httpx
from PIL import Image
from app.device_hub import DeviceHub
from app.tools.artwork import ArtworkRef,ArtworkProvider,decode_artwork,RAW_BYTES,select_artwork
from app.tools.music_runtime import LocalMusicRuntime
from test_music_runtime import Socket,Source,settle

URL='https://i.scdn.co/image/'+'a'*40
REF=ArtworkRef(URL,192,192)
def png(size=(192,192)):
    output=io.BytesIO();Image.new('RGB',size,(255,0,0)).save(output,'PNG');return output.getvalue()

class ArtworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_origin_geometry_decode_and_cache_bounds(self):
        calls=[]
        def handle(request):
            calls.append(request)
            self.assertNotIn('authorization',request.headers);self.assertNotIn('cookie',request.headers)
            return httpx.Response(200,content=png(),headers={'Content-Type':'image/png','Set-Cookie':'cover_private=value; Path=/'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider=ArtworkProvider(client,True)
            self.assertEqual((await provider.fetch(ArtworkRef('https://evil.test/image',192,192))).status,'unavailable')
            self.assertFalse(calls)
            for n in range(6):
                result=await provider.fetch(ArtworkRef('https://i.scdn.co/image/'+f'{n:040x}',192,192))
                self.assertEqual(result.status,'ready');self.assertEqual(len(result.image.pixels),RAW_BYTES)
                self.assertEqual(result.image.digest,hashlib.sha256(result.image.pixels).hexdigest())
            self.assertFalse(client.cookies);self.assertLessEqual(len(provider.cache),4);self.assertLessEqual(provider.cache_bytes,327680)
            provider.clear();self.assertEqual(provider.cache_bytes,0)
        with self.assertRaises(ValueError):decode_artwork(png((193,192)),REF)
        with self.assertRaises(Exception):decode_artwork(b'invalid',REF)
        self.assertIsNone(select_artwork({'images':[{'url':URL+'?x','width':192,'height':192}]}))

    async def test_redirect_oversize_disabled_and_rate_limit(self):
        for status,headers,body in [(302,{'Location':'https://evil.test'},b''),(200,{'Content-Type':'image/png','Content-Length':'262145'},b''),(429,{'Retry-After':'nan'},b'')]:
            calls=[]
            def handle(request):calls.append(request);return httpx.Response(status,headers=headers,content=body)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                provider=ArtworkProvider(client,False)
                self.assertEqual((await provider.fetch(REF)).status,'unavailable');self.assertFalse(calls)
                provider.enabled=True
                self.assertEqual((await provider.fetch(REF)).status,'unavailable')
                self.assertEqual((await provider.fetch(REF)).status,'unavailable');self.assertEqual(len(calls),1)

class PersistentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now=0;self.hub=DeviceHub();self.socket=Socket()
        self.hub.register('d',self.socket,{'media_updates_v1','media_persistent_v1','media_artwork_v1'})
        self.runtime=LocalMusicRuntime(self.hub,Source(lambda:self.now),enabled=True,poll_seconds=.04,clock=lambda:self.now)
        self.hub.scene_observer=self.runtime.interrupt_message
    async def asyncTearDown(self):await self.runtime.close()
    def resume_packet(self,view):return dict(media_request_id=view.request,media_session_id=view.session,overlay_id=view.overlay)
    async def test_protected_pending_ready_then_late_dismiss_ignored(self):
        await self.runtime.open('d',self.socket,'request');await settle();view=next(iter(self.runtime.views.values()))
        self.now=1000;await settle();self.assertIsNone(self.socket.messages[-1]['surface']['expires_ms'])
        await self.hub.broadcast('d',{'type':'reminder.alert'})
        binding=self.socket.messages[-1]['media_overlay'];self.assertEqual(binding['state'],'suspend')
        packet=self.resume_packet(view);self.assertEqual(await self.runtime.resume('d',self.socket,packet),'pending')
        self.runtime.clear_protection('d');self.assertEqual(await self.runtime.resume('d',self.socket,packet),'ready')
        self.assertGreater(self.socket.messages[-1]['media_sequence'],binding['media_sequence'])
        self.runtime.stop('d',self.socket);self.assertEqual(await self.runtime.resume('d',self.socket,packet),'ignored')
        self.assertFalse(self.runtime.views)
    async def test_per_peer_binding_and_queued_frame_suspension(self):
        other=Socket();self.hub.register('d',other,{'media_updates_v1','media_persistent_v1'})
        await self.runtime.open('d',self.socket,'one');await self.runtime.open('d',other,'two');await settle()
        view=self.runtime.views[self.runtime._key('d',self.socket)]
        peer=self.hub.connections['d'][0];await peer.send_lock.acquire()
        queued=asyncio.create_task(self.runtime._publish(view,True));await asyncio.sleep(.01)
        priority=asyncio.create_task(self.hub.broadcast('d',{'type':'state','state':'thinking'}));await asyncio.sleep(.01)
        count=len(self.socket.messages);peer.send_lock.release();await queued;await priority
        self.assertEqual(len(self.socket.messages),count+1)
        self.assertNotEqual(self.socket.messages[-1]['media_overlay']['media_session_id'],other.messages[-1]['media_overlay']['media_session_id'])
        self.assertEqual(await self.runtime.resume('d',self.socket,self.resume_packet(view)),'pending')
    async def test_cover_exact_payload_and_same_view_dedup(self):
        await self.runtime.open('d',self.socket,'r');view=next(iter(self.runtime.views.values()))
        self.runtime.cover=decode_artwork(png(),REF);self.runtime.cover_status='ready';self.runtime.track_key='b'*24
        await self.runtime._publish(view,True)
        frame=self.socket.messages[-1];art=frame['surface']['artwork']
        self.assertEqual(len(art['data_base64']),98304);self.assertEqual(len(base64.b64decode(art['data_base64'])),73728)
        self.assertLess(len(json.dumps(frame).encode()),131072)
        await self.runtime._publish(view,True);self.assertNotIn('data_base64',self.socket.messages[-1]['surface']['artwork'])
        view.artwork_sent=None;await self.runtime._publish(view,True)
        self.assertIn('data_base64',self.socket.messages[-1]['surface']['artwork'])

    async def test_late_artwork_cannot_resurrect_after_dismiss(self):
        from app.tools.artwork import ArtworkResult
        from app.tools.music import Snapshot
        started=asyncio.Event();release=asyncio.Event()
        class Provider:
            cache={}
            def clear(self):self.cache.clear()
            async def fetch(self,ref):
                started.set()
                try:await release.wait()
                except asyncio.CancelledError:await release.wait()
                return ArtworkResult('ready',decode_artwork(png(),REF))
        self.runtime.artwork=Provider()

        # Bind a valid source snapshot without issuing an external read.
        view_request='r'
        await self.runtime.open('d',self.socket,view_request)
        self.runtime._retire(self.runtime.poll_task);self.runtime._retire(self.runtime.render_task)
        await asyncio.sleep(0)
        self.runtime.music=SimpleNamespace(cache=Snapshot('ready',track='fixture',version='one',artwork=REF),tokens={})
        self.runtime.latest=SimpleNamespace(status='ready')
        self.runtime._update_artwork();await asyncio.wait_for(started.wait(),1)
        task=self.runtime.artwork_task;self.runtime.stop('d',self.socket);release.set();await task
        self.assertIsNone(self.runtime.cover);self.assertFalse(self.runtime.views)

    async def test_confirmation_resolution_idle_and_stale_overlay(self):
        await self.runtime.open('d',self.socket,'r');view=next(iter(self.runtime.views.values()))
        await self.hub.broadcast('d',{'type':'surface','surface':{'kind':'message','actions':[{'id':'confirm'}]}})
        old=self.resume_packet(view)
        await self.hub.broadcast('d',{'type':'reminder.alert'})
        self.assertEqual(await self.runtime.resume('d',self.socket,old),'ignored')
        await self.hub.broadcast('d',{'type':'state','state':'idle'})
        self.assertTrue(view.suspended)
        self.assertIn("d",self.runtime.protected)

    async def test_memory_confirmation_survives_idle_invalid_and_expired_tokens(self):
        import tempfile
        from pathlib import Path
        from app.memory import MemoryStore
        with tempfile.TemporaryDirectory() as directory:
            store=MemoryStore(str(Path(directory)/'memory.db'))
            token=store.propose('d','remember',None,'Original synthetic preference')
            self.runtime.blocked=lambda device:store.pending_confirmation(device)
            # A proposal can arrive after opening Music; its actual protected surface owns the barrier.
            self.runtime.blocked=lambda device:False
            await self.runtime.open('d',self.socket,'r')
            self.runtime.blocked=lambda device:store.pending_confirmation(device)
            await self.hub.broadcast('d',{'type':'surface','surface':{'kind':'message','actions':[{'id':'memno:'+token}]}})
            view=next(iter(self.runtime.views.values()));packet=self.resume_packet(view)
            await self.hub.broadcast('d',{'type':'state','state':'idle'})
            self.assertIn('d',self.runtime.protected)
            self.assertFalse(self.runtime.resolve_memory('d','0'*24,store.confirmation_resolved))
            self.assertEqual(await self.runtime.resume('d',self.socket,packet),'pending')
            store.decide('d',token,False)
            self.assertTrue(self.runtime.resolve_memory('d',token,store.confirmation_resolved))
            self.assertEqual(await self.runtime.resume('d',self.socket,packet),'ready')
            expired=store.propose('d','remember',None,'Second synthetic preference')
            with store.connect() as db:
                db.execute("UPDATE memory_confirmations SET expires_at='2000-01-01' WHERE token=?",(expired,))
            await self.hub.broadcast('d',{'type':'surface','surface':{'kind':'message','actions':[{'id':'memno:'+expired}]}})
            await self.hub.broadcast('d',{'type':'state','state':'idle'})
            self.assertTrue(store.pending_confirmation('d'))
            with self.assertRaises(ValueError):store.decide('d',expired,True)
            with self.assertRaises(ValueError):store.decide('other-owner',expired,False)
            with self.assertRaises(ValueError):store.decide('d','0'*24,False)
            self.assertFalse(self.runtime.resolve_memory('d',expired,store.confirmation_resolved))
            self.assertIn('d',self.runtime.protected)
            packet=self.resume_packet(view)
            self.assertEqual(await self.runtime.resume('d',self.socket,packet),'pending')
            store.decide('d',expired,False)
            self.assertFalse(store.pending_confirmation('d'))
            with self.assertRaises(ValueError):store.decide('d',expired,False)
            self.assertTrue(self.runtime.resolve_memory('d',expired,store.confirmation_resolved))
            self.assertEqual(await self.runtime.resume('d',self.socket,packet),'ready')
            stale=store.propose('d','remember',None,'Superseded synthetic preference')
            replacement=store.propose('d','remember',None,'Replacement synthetic preference')
            with self.assertRaises(ValueError):store.decide('d',stale,False)
            self.assertTrue(store.pending_confirmation('d'))
            store.decide('d',replacement,False)

    async def test_final_disconnect_clears_busy_but_preserves_pending_protection(self):
        await self.runtime.open('d',self.socket,'r')
        await self.hub.broadcast('d',{'type':'state','state':'listening'})
        other=Socket();self.hub.register('d',other,{'media_updates_v1','media_persistent_v1'})
        self.hub.disconnect('d',self.socket);self.runtime.stop('d',self.socket);self.runtime.disconnected('d')
        self.assertIn('d',self.runtime.busy)
        self.hub.disconnect('d',other);self.runtime.disconnected('d')
        self.assertNotIn('d',self.runtime.busy)
        self.runtime.protected.add('d');self.runtime.disconnected('d');self.assertIn('d',self.runtime.protected)
        self.runtime.clear_protection('d');self.hub.register('d',self.socket,{'media_updates_v1','media_persistent_v1'})
        await self.runtime.open('d',self.socket,'reconnected');self.assertTrue(self.runtime.views)
