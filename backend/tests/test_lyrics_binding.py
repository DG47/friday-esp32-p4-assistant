import asyncio
import unittest
from app.tools.lyrics import LyricsBinding,LyricsDocument,TrackIdentity
from app.tools.music import MusicService,Snapshot
from test_music import Transport,Response,playback

def song(track='a',version='Original fixture | Original album | 180000ms',progress=12000,playing=True,observed=0):
    return Snapshot(status='ready',track=track,title='Original fixture',artist='Fixture artist',
        playing=playing,duration=180000,progress=progress,observed=observed,isrc='ZZAAA2600001',version=version)

def document(identity,mode='manual'):
    return LyricsDocument(identity,'Original synthetic fixture','Written for offline QA','Original sample line one.\nOriginal sample line two.',mode)

class Deferred:
    def __init__(self):self.pending=[]
    async def lyrics(self,identity):
        future=asyncio.get_running_loop().create_future();self.pending.append((identity,future));return await future

class BindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_source_is_honest_and_does_not_claim_timing(self):
        binding=LyricsBinding();binding.observe(song());self.assertFalse(await binding.load_current())
        self.assertEqual(binding.status,'no_source');self.assertFalse(binding.payload()['synchronized'])
    async def test_change_clears_immediately_and_late_result_is_discarded(self):
        provider=Deferred();binding=LyricsBinding(provider);binding.observe(song())
        old=asyncio.create_task(binding.load_current());await asyncio.sleep(0)
        binding.observe(song('b',observed=1));self.assertIsNone(binding.document)
        new=asyncio.create_task(binding.load_current());await asyncio.sleep(0)
        provider.pending[1][1].set_result(document(provider.pending[1][0]));self.assertTrue(await new)
        provider.pending[0][1].set_result(document(provider.pending[0][0]));self.assertFalse(await old)
        self.assertEqual(binding.payload()['track_id'],'b');self.assertEqual(binding.status,'manual')
    async def test_same_track_out_of_order_requests_keep_latest(self):
        provider=Deferred();binding=LyricsBinding(provider);binding.observe(song())
        first=asyncio.create_task(binding.load_current());await asyncio.sleep(0)
        second=asyncio.create_task(binding.load_current());await asyncio.sleep(0)
        provider.pending[1][1].set_result(document(provider.pending[1][0]));await second
        provider.pending[0][1].set_result(None);self.assertFalse(await first)
        self.assertEqual(binding.status,'manual')
    async def test_version_and_recording_mismatches_fail_closed(self):
        for identity in [TrackIdentity('other','ZZAAA2600001',song().version),TrackIdentity('a','OTHER0000001',song().version),TrackIdentity('a','ZZAAA2600001','Live version'),TrackIdentity('','','')]:
            class Provider:
                async def lyrics(self,requested):return document(identity)
            binding=LyricsBinding(Provider());binding.observe(song());self.assertFalse(await binding.load_current())
            self.assertEqual(binding.status,'unmatched');self.assertIsNone(binding.document)
    async def test_isrc_fallback_requires_same_version_and_attribution(self):
        class Provider:
            async def lyrics(self,identity):return document(TrackIdentity('',identity.isrc,identity.version))
        binding=LyricsBinding(Provider());binding.observe(song());self.assertTrue(await binding.load_current())
        payload=binding.payload();self.assertEqual(payload['mode'],'manual');self.assertFalse(payload['synchronized'])
        self.assertEqual(payload['source'],'Original synthetic fixture');self.assertIn('not time-synced',binding.label())
    async def test_pause_and_seek_keep_manual_match_without_timed_cursor(self):
        class Provider:
            async def lyrics(self,identity):return document(identity)
        binding=LyricsBinding(Provider());binding.observe(song());await binding.load_current()
        binding.observe(song(progress=170000,playing=False,observed=1));self.assertEqual(binding.status,'manual')
        self.assertTrue(binding.payload()['paused']);self.assertNotIn('active_line',binding.payload())
        binding.observe(song(progress=1000,playing=True,observed=2));self.assertEqual(binding.status,'manual')
    async def test_ads_episodes_disconnection_and_no_lyrics_clear(self):
        class Provider:
            async def lyrics(self,identity):return document(identity)
        binding=LyricsBinding(Provider())
        for status in ['unsupported','no_playback','disabled','revoked','unavailable','stale','no_device']:
            binding.observe(song(observed=binding.observed+1));await binding.load_current()
            binding.observe(Snapshot(status=status));self.assertIsNone(binding.document);self.assertIsNone(binding.identity)
        class Empty:
            async def lyrics(self,identity):return None
        binding=LyricsBinding(Empty());binding.observe(song());await binding.load_current();self.assertEqual(binding.status,'no_lyrics')
    async def test_timed_result_is_not_enabled(self):
        class Provider:
            async def lyrics(self,identity):return document(identity,'timed')
        binding=LyricsBinding(Provider());binding.observe(song());self.assertFalse(await binding.load_current())
        self.assertEqual(binding.status,'timing_disabled');self.assertIsNone(binding.payload()['text'])
    async def test_service_binds_exact_metadata_and_clears_on_next_read(self):
        data=playback();data['item']['album']={'name':'Original album'};data['item']['external_ids']={'isrc':'ZZAAA2600001'}
        nextdata=playback('next');nextdata['item']['name']='Different original fixture'
        now=[0];m=MusicService('spotify',Transport([Response(data=data),Response(data=nextdata)]),lambda:now[0])
        await m.scene('d','music');self.assertEqual(m.lyrics.identity.isrc,'ZZAAA2600001')
        self.assertIn('Original album',m.lyrics.identity.version)
        now[0]=16;await m.scene('d','music');self.assertEqual(m.lyrics.identity.track_id,'next');self.assertIsNone(m.lyrics.document)

class MoreBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_change_same_id_clears_before_provider_response(self):
        class Provider:
            async def lyrics(self,identity):return document(identity)
        binding=LyricsBinding(Provider());binding.observe(song());await binding.load_current()
        binding.observe(song(version='Live fixture | Other album | 200000ms',observed=1))
        self.assertIsNone(binding.document);self.assertEqual(binding.status,'unavailable')
    async def test_failure_and_missing_attribution_never_display_text(self):
        for mode in ['failure','missing_attribution']:
            class Provider:
                async def lyrics(self,identity):
                    if mode=='failure':raise RuntimeError('synthetic provider failure')
                    return LyricsDocument(identity,'Fixture','','Original sample')
            binding=LyricsBinding(Provider());binding.observe(song());self.assertFalse(await binding.load_current())
            self.assertIsNone(binding.payload()['text'])
    async def test_old_observation_cannot_replace_new_recording_binding(self):
        binding=LyricsBinding();binding.observe(song('b',observed=5));binding.observe(song('a',observed=4))
        self.assertEqual(binding.identity.track_id,'b')
    async def test_oversized_version_metadata_cannot_silently_match(self):
        data=playback();data['item']['name']='Original fixture '*100
        service=MusicService('spotify',Transport([Response(data=data)]));await service.scene('d','music')
        self.assertIsNone(service.lyrics.identity)

class IsrcIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_isrc_is_never_truncated_into_false_match(self):
        for value in ['ZZAAA2600001extra','bad','éZAAA2600001']:
            data=playback();data['item']['external_ids']={'isrc':value}
            service=MusicService('spotify',Transport([Response(data=data)]));await service.scene('d','music')
            self.assertEqual(service.lyrics.identity.isrc,'')

class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_latest_provider_request_does_not_stay_loading(self):
        provider=Deferred();binding=LyricsBinding(provider);binding.observe(song())
        pending=asyncio.create_task(binding.load_current());await asyncio.sleep(0);pending.cancel()
        with self.assertRaises(asyncio.CancelledError):await pending
        self.assertEqual(binding.status,'unavailable');self.assertIsNone(binding.document)
