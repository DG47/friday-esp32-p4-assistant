import asyncio,json,unittest
import httpx
from app.tools.lyrics import TrackIdentity
from app.tools.lyrics_sync import LyricsSyncEngine,PlaybackObservation
from app.tools.lrclib import *
from app.tools.spotify_macos import MacSpotifyAdapter,ApprovedOsaRunner,READ_SCRIPT
from app.tools.lyrics_session import TimedLyricsSession

IDENTITY=TrackIdentity('fixture','', 'Original fixture | Original album | 10000ms')
QUERY=LyricQuery(IDENTITY,'Original fixture','Fixture artist','Original album',10000)
USER_AGENT='Friday/0.1 (+https://example.invalid/offline-qa)'
def response(**extra):
    return dict({'id':123,'trackName':QUERY.track_name,'artistName':QUERY.artist_name,'albumName':QUERY.album_name,'duration':10,'instrumental':False,'syncedLyrics':'[00:01.00]The copper kite turns.\n[00:03.00]A paper boat follows.','plainLyrics':'Original untimed sample only.'},**extra)
def local(**extra):
    return json.dumps(dict({'id':'spotify:track:fixture','name':QUERY.track_name,'artist':QUERY.artist_name,'album':QUERY.album_name,'duration_raw':10000,'position_seconds':1.2,'state':'playing','app_version':'1.3.1.234'},**extra))

class LRCTests(unittest.TestCase):
    def test_multitimestamps_repeats_blank_gaps_and_offset(self):
        lines,offset=parse_lrc('[ar:Fixture artist]\n[offset:+100]\n[00:01.0][00:04.00]Original repeating sample\n[00:02.000]\n[00:06.00]Original final sample',10000)
        self.assertEqual([line.start_ms for line in lines],[1000,4000,6000]);self.assertEqual(lines[0].end_ms,2000)
        self.assertEqual(lines[0].text,lines[1].text);self.assertEqual(offset,100)
    def test_duplicate_timestamps_merge_unique_text_and_strip_word_tags(self):
        lines,_=parse_lrc('[00:01]Original A\n[00:01]Original A\n[00:01]<00:01.00>Original B',10000)
        self.assertEqual(len(lines),1);self.assertEqual(lines[0].text,'Original A\nOriginal B')
    def test_malformed_and_conflicting_offsets_are_refused(self):
        for value in ['[00:60]Bad','[offset:1]\n[offset:2]\n[00:01]Original','[00:10]Original','Untimed original sample','[00:01]']:
            with self.assertRaises(LRCInvalid):parse_lrc(value,10000)

class LRCLIBTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_unconfigured_send_no_metadata(self):
        calls=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:calls.append(request))) as client:
            self.assertEqual((await LRCLIBAdapter(client).fetch(QUERY)).status,'disabled')
            self.assertEqual((await LRCLIBAdapter(client,enabled=True).fetch(QUERY)).status,'unconfigured')
        self.assertEqual(calls,[])
    async def test_exact_get_matching_header_and_single_cached_fetch(self):
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(200,json=response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider=LRCLIBAdapter(client,USER_AGENT,True)
            results=await asyncio.gather(*(provider.fetch(QUERY) for _ in range(5)))
        self.assertEqual(len(calls),1);self.assertTrue(all(r.status=='timecoded' for r in results))
        request=calls[0];self.assertEqual(request.url.host,'lrclib.net');self.assertEqual(request.url.path,'/api/get')
        self.assertEqual(request.url.params['album_name'],QUERY.album_name);self.assertEqual(request.headers['user-agent'],USER_AGENT)
        self.assertIn('rights unverified',results[0].document.attribution)
    async def test_missing_synced_never_substitutes_plain_text(self):
        for data in [response(syncedLyrics=None),response(syncedLyrics=''),response(instrumental=True)]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=data))) as client:
                result=await LRCLIBAdapter(client,USER_AGENT,True).fetch(QUERY)
                self.assertEqual(result.status,'no_synced_lyrics');self.assertIsNone(result.document)
    async def test_wrong_album_artist_version_and_duration_are_refused(self):
        for data in [response(albumName='Different album'),response(artistName='Other artist'),response(trackName='Original fixture (Live)'),response(duration=13),response(duration=float('nan'))]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=json.dumps(data)))) as client:
                self.assertEqual((await LRCLIBAdapter(client,USER_AGENT,True).fetch(QUERY)).status,'unmatched')
    async def test_rate_limit_sequential_backoff_and_bounds(self):
        calls=[];now=[0]
        def handler(request):calls.append(request);return httpx.Response(429,headers={'Retry-After':'Infinity'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider=LRCLIBAdapter(client,USER_AGENT,True,lambda:now[0])
            self.assertEqual((await provider.fetch(QUERY)).status,'rate_limited');now[0]=20
            self.assertEqual((await provider.fetch(QUERY)).status,'backoff');self.assertEqual(len(calls),1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*262145))) as client:
            self.assertEqual((await LRCLIBAdapter(client,USER_AGENT,True).fetch(QUERY)).status,'unavailable')
    async def test_not_found_and_redirect_have_no_search_or_followup(self):
        for code,status in [(404,'no_synced_lyrics'),(302,'unavailable'),(500,'unavailable')]:
            calls=[]
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:(calls.append(request),httpx.Response(code,headers={'Location':'https://example.invalid'}))[1])) as client:
                self.assertEqual((await LRCLIBAdapter(client,USER_AGENT,True).fetch(QUERY)).status,status)
                self.assertEqual(len(calls),1)

class MacAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_and_unverified_units_never_run_process(self):
        calls=[]
        async def runner(script):calls.append(script);return local()
        self.assertEqual((await MacSpotifyAdapter(runner).read()).status,'disabled')
        self.assertEqual((await MacSpotifyAdapter(runner,True,verified_version='1.3.1.234').read()).status,'dictionary_or_units_unverified')
        self.assertEqual(calls,[])
        with self.assertRaises(Exception):await ApprovedOsaRunner()(READ_SCRIPT)
    async def test_mocked_milliseconds_and_seconds_versions(self):
        for unit,raw in [('milliseconds',10000),('seconds',10)]:
            async def runner(script):return local(duration_raw=raw)
            read=await MacSpotifyAdapter(runner,True,'1.3.1.234',unit,lambda:0).read()
            self.assertEqual(read.status,'ready');self.assertEqual(read.observation.position_ms,1200)
            self.assertEqual(read.query.duration_ms,10000);self.assertEqual(read.query.identity.track_id,'fixture')
    async def test_paused_episodes_ads_bad_version_and_output(self):
        for data,status in [(local(state='paused'),'ready'),(local(id='spotify:episode:fixture'),'unsupported'),(local(id='spotify:ad:fixture'),'unsupported'),(local(app_version='other'),'dictionary_unverified'),('malformed','unavailable'),(json.dumps({'status':'not_running'}),'not_running')]:
            async def runner(script):return data
            read=await MacSpotifyAdapter(runner,True,'1.3.1.234','milliseconds',lambda:0).read()
            self.assertEqual(read.status,status)
            if 'paused' in data:self.assertFalse(read.observation.playing)
    async def test_process_timeout_and_oversized_output(self):
        async def slow(script):await asyncio.sleep(10)
        self.assertEqual((await MacSpotifyAdapter(slow,True,'v','milliseconds',timeout=.01).read()).status,'unavailable')
        async def large(script):return 'x'*16385
        self.assertEqual((await MacSpotifyAdapter(large,True,'v','milliseconds').read()).status,'invalid_output')

class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_latency_reanchors_and_seek_does_not_refetch(self):
        now=[0];calls=[]
        def handler(request):calls.append(request);now[0]=4;return httpx.Response(200,json=response())
        async def reanchor():return PlaybackObservation(2,IDENTITY,4000,10000,True,now[0])
        engine=LyricsSyncEngine(True,lambda:now[0])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            session=TimedLyricsSession(engine,LRCLIBAdapter(client,USER_AGENT,True,lambda:now[0]),reanchor)
            session.observe(PlaybackObservation(1,IDENTITY,0,10000,True,0),QUERY);await session.task
            self.assertEqual(engine.current()['line'],'A paper boat follows.')
            session.observe(PlaybackObservation(3,IDENTITY,1200,10000,False,4),QUERY)
            self.assertEqual(engine.current()['line'],'The copper kite turns.');self.assertEqual(len(calls),1)
            await session.close()
    async def test_track_change_and_absent_synced_result_are_guarded(self):
        engine=LyricsSyncEngine(True,lambda:0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response(syncedLyrics=None)))) as client:
            session=TimedLyricsSession(engine,LRCLIBAdapter(client,USER_AGENT,True))
            session.observe(PlaybackObservation(1,IDENTITY,0,10000,True,0),QUERY);await session.task
            self.assertEqual(engine.current()['status'],'no_synced_lyrics');self.assertIsNone(engine.current()['line'])
            await session.close()

class FurtherAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_episode_gap_then_same_track_triggers_new_generation_fetch(self):
        calls=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:(calls.append(request),httpx.Response(200,json=response()))[1])) as client:
            engine=LyricsSyncEngine(True,lambda:0);provider=LRCLIBAdapter(client,USER_AGENT,True)
            session=TimedLyricsSession(engine,provider)
            session.observe(PlaybackObservation(1,IDENTITY,1200,10000,False,0),QUERY);await session.task
            session.observe(PlaybackObservation(2,IDENTITY,0,10000,False,0,item_kind='episode'),QUERY)
            self.assertIsNone(engine.current()['line'])
            session.observe(PlaybackObservation(3,IDENTITY,1200,10000,False,0),QUERY);await session.task
            self.assertEqual(engine.current()['line'],'The copper kite turns.');self.assertEqual(len(calls),1)
            await session.close()
    async def test_old_provider_result_after_track_change_cannot_repopulate(self):
        entered=asyncio.Event();release=asyncio.Event()
        class Provider:
            async def fetch(self,query):
                entered.set()
                try:await release.wait()
                except asyncio.CancelledError:pass  # Model a provider whose cancellation loses a race.
                lines,_=parse_lrc(response()['syncedLyrics'],10000)
                return TimedFetch('timecoded',TimedDocument(query.identity,10000,lines,'Original fixture','Original QA'))
        engine=LyricsSyncEngine(True,lambda:0);session=TimedLyricsSession(engine,Provider())
        session.observe(PlaybackObservation(1,IDENTITY,1200,10000,False,0),QUERY);await entered.wait();old=session.task
        other=TrackIdentity('other','','Other version')
        session.observe(PlaybackObservation(2,other,0,10000,False,0));release.set();await old
        self.assertIsNone(engine.document);self.assertEqual(engine.observation.identity,other);await session.close()
    async def test_duration_within_two_second_api_tolerance_matches(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response(duration=11.5)))) as client:
            result=await LRCLIBAdapter(client,USER_AGENT,True).fetch(QUERY)
            engine=LyricsSyncEngine(True,lambda:0);engine.observe(PlaybackObservation(1,IDENTITY,1200,10000,False,0))
            self.assertTrue(engine.apply(engine.begin_load(),result.document))

class WebSourceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_source_uses_full_recording_metadata_without_external_fetch(self):
        from app.tools.music import MusicService
        from test_music import playback,Response,Transport
        data=playback();data['item']['name']='Original long fixture title '*4
        data['item']['artists']=[{'name':'Original fixture artist '*4}];data['item']['album']={'name':'Original album'}
        transport=Transport([Response(data=data)]);service=MusicService('spotify',transport,lambda:0)
        await service.scene('d','music');observation,query=service.cache.timed_context(1)
        self.assertEqual(query.track_name,data['item']['name']);self.assertEqual(query.artist_name,data['item']['artists'][0]['name'])
        self.assertEqual(observation.identity,query.identity);self.assertEqual(len(transport.calls),1)

class LateMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_arriving_after_playback_still_fetches_and_switch_back_restores(self):
        calls=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:(calls.append(request),httpx.Response(200,json=response()))[1])) as client:
            engine=LyricsSyncEngine(True,lambda:0);session=TimedLyricsSession(engine,LRCLIBAdapter(client,USER_AGENT,True))
            session.observe(PlaybackObservation(1,IDENTITY,1200,10000,False,0))
            self.assertIsNone(session.task)
            session.observe(PlaybackObservation(2,IDENTITY,1200,10000,False,0),QUERY);await session.task
            other=TrackIdentity('other','','Other version')
            session.observe(PlaybackObservation(3,other,0,10000,False,0))
            session.observe(PlaybackObservation(4,IDENTITY,1200,10000,False,0),QUERY);await session.task
            self.assertEqual(engine.current()['line'],'The copper kite turns.');self.assertEqual(len(calls),1)
            await session.close()
