import asyncio
import unittest
from app.experience import Experience, CAPABILITY
from app.device_hub import DeviceHub
from test_device_hub import FakeWebSocket

class OwnershipTests(unittest.TestCase):
    def test_scope_replay_and_reconnect_never_rebind_old_job(self):
        e=Experience()
        with e.scope('d','voice','peer'):
            e.bind_job('d','job'); old=e.origin.get();self.assertEqual(old.request,'voice')
            with e.for_job('unknown'):
                self.assertIsNone(e.origin.get())
                self.assertTrue(e.envelope('d',{'type':'agent.result'})['experience_historical'])
            self.assertEqual(e.origin.get(),old)
        self.assertIsNone(e.origin.get())
        e.session('d',reset=True)
        with e.for_job('job'):
            packet=e.envelope('d',{'type':'agent.result','text':'private','surface':{'subtitle':'private'}})
            rendered=e.for_peer('d',packet,'peer')
        self.assertTrue(rendered['experience_historical']);self.assertNotIn('private',str(rendered))
        self.assertIsNone(e.origin.get())

    def test_rejection_preserves_previous_and_finished_blocks_tool(self):
        e=Experience()
        with e.scope('d','old','p'):
            e.claim('d')
            terminal=e.envelope('d',{'type':'experience.finished'});self.assertIsNotNone(e.for_peer('d',terminal,'p'))
            self.assertIsNone(e.for_peer('d',e.envelope('d',{'type':'tool.activity'}),'p'))
        with e.scope('d','new','p'):
            rejection=e.envelope('d',{'type':'experience.rejected','experience_previous':'old'})
            self.assertIsNotNone(e.for_peer('d',rejection,'p'))
            self.assertEqual(e.current['d'].request,'old')
        self.assertIsNone(e.origin.get())

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_send_rechecks_before_observer_or_private_delivery(self):
        h=DeviceHub();w=FakeWebSocket();h.register('d',w,{CAPABILITY});p=h.connections['d'][0]
        observed=[];h.scene_observer=lambda *a: observed.append(a)
        await p.send_lock.acquire()
        with h.experience.scope('d','old',p.key):
            h.experience.claim('d');pending=asyncio.create_task(h.broadcast('d',{'type':'surface','surface':{'subtitle':'private old'}}))
        await asyncio.sleep(0)
        with h.experience.scope('d','new',p.key):h.experience.claim('d')
        p.send_lock.release();self.assertEqual(await pending,0)
        self.assertEqual(w.messages,[]);self.assertEqual(observed,[])

    async def test_source_peer_owns_new_result_other_peer_gets_scrubbed_history(self):
        h=DeviceHub();a=FakeWebSocket();b=FakeWebSocket();h.register('d',a,{CAPABILITY});h.register('d',b,{CAPABILITY})
        with h.experience.scope('d','a',h.peer_key('d',a)):
            h.experience.claim('d');await h.broadcast('d',{'type':'surface','surface':{'subtitle':'only a'}})
        self.assertEqual(len(a.messages),1);self.assertEqual(b.messages,[])
        with h.experience.scope('d','old',h.peer_key('d',a)):
            await h.broadcast('d',{'type':'agent.result','text':'secret','surface':{'subtitle':'secret'}})
        for w in (a,b):self.assertNotIn('secret',str(w.messages[-1]));self.assertTrue(w.messages[-1]['experience_historical'])

    async def test_mixed_stale_legacy_cannot_suspend_current_capable_music(self):
        from app.tools.music_runtime import LocalMusicRuntime, View
        h=DeviceHub();cap=FakeWebSocket();legacy=FakeWebSocket()
        h.register('d',cap,{CAPABILITY,'media_updates_v1','media_persistent_v1'});h.register('d',legacy,{'media_updates_v1','media_persistent_v1'})
        from app import main
        from unittest.mock import patch
        runtime=LocalMusicRuntime(h,enabled=True);h.scene_observer=main.hub.scene_observer;h.scene_peer_observer=main.hub.scene_peer_observer
        current=View('d',cap,'current-media','current-session',999999999)
        old=View('d',legacy,'legacy-media','legacy-session',999999999)
        runtime.views[runtime._key('d',cap)]=current;runtime.views[runtime._key('d',legacy)]=old
        runtime.cover=b'current-art';runtime.cover_status='ready';generation=runtime.artwork_generation
        with h.experience.scope('d','current',h.peer_key('d',cap)):h.experience.claim('d')
        with patch('app.main.local_music',runtime), h.experience.scope('d','old',h.peer_key('d',legacy)):
            await h.broadcast('d',{'type':'surface','surface':{'kind':'message','actions':[{'id':'memyes:'+'f'*24}]}})
            await h.broadcast('d',{'type':'state','state':'idle'})
        self.assertEqual(cap.messages,[]);self.assertFalse(current.suspended);self.assertTrue(old.suspended)
        self.assertIn('media_overlay',legacy.messages[0]);self.assertNotIn('d',runtime.protected)
        self.assertEqual(runtime.cover,b'current-art');self.assertEqual(runtime.artwork_generation,generation)

    async def test_rejection_is_delivered_only_to_its_source_peer(self):
        h=DeviceHub();a=FakeWebSocket();b=FakeWebSocket()
        h.register('d',a,{CAPABILITY});h.register('d',b,{CAPABILITY})
        with h.experience.scope('d','review',h.peer_key('d',a)):h.experience.claim('d')
        with h.experience.scope('d','rejected-mutation',h.peer_key('d',a)):
            await h.broadcast('d',{'type':'experience.rejected','experience_previous':'review','code':'interaction_pending'})
        self.assertEqual(len(a.messages),1);self.assertEqual(a.messages[0]['experience_id'],'rejected-mutation')
        self.assertEqual(b.messages,[])
        with h.experience.scope('d','old',h.peer_key('d',b)):
            await h.broadcast('d',{'type':'experience.rejected','experience_previous':'stale-owner','code':'interaction_pending'})
        self.assertEqual(len(a.messages),1);self.assertEqual(b.messages,[])
