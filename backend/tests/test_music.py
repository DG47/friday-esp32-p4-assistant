import asyncio
import unittest
from app.tools.music import MusicService

class Response:
    def __init__(self, code=200, data=None, headers=None):
        self.status_code, self.data, self.headers = code, data, headers or {}
    def json(self): return self.data

def playback(track='t', restricted=False, playing=True):
    return {'currently_playing_type':'track', 'item':{'id':track,'name':'Original fixture','artists':[{'name':'Sample artist'}], 'duration_ms':180000},'device':{'id':'device','name':'Speaker','is_restricted':restricted},'is_playing':playing,'progress_ms':12000,'actions':{'disallows':{}}}

class Transport:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    async def request(self, method, path):
        self.calls.append((method,path))
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return result

class MusicTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_has_no_account_io(self):
        t = Transport([]); s = await MusicService(transport=t).scene('d','music')
        self.assertEqual(t.calls, []); self.assertEqual(s.items[0]['label'],'disabled')
        self.assertEqual(len(s.actions),1)

    async def test_native_contract_and_synthetic_no_controls(self):
        s = await MusicService('synthetic').scene('d','music')
        self.assertEqual(len(s.items),4); self.assertLessEqual(len(s.actions),4)
        self.assertIn('Synthetic',s.subtitle); self.assertEqual(s.actions[0].id,'dismiss')
        self.assertTrue(all(set(item)=={'time','label'} for item in s.items))

    async def test_control_recheck_and_replay(self):
        t=Transport([Response(data=playback()),Response(data=playback()),Response(204),Response(data=playback(playing=False))])
        m=MusicService('spotify',t); s=await m.scene('d','music'); action=s.actions[0].id
        s=await m.scene('d',action)
        self.assertEqual([x[0] for x in t.calls],['GET','GET','PUT','GET'])
        self.assertIn('accepted',s.items[0]['label'])
        with self.assertRaises(ValueError): await m.scene('d',action)

    async def test_changed_track_does_not_skip(self):
        t=Transport([Response(data=playback()),Response(data=playback('other'))]); m=MusicService('spotify',t)
        s=await m.scene('d','music'); await m.scene('d',s.actions[2].id)
        self.assertEqual([x[0] for x in t.calls],['GET','GET'])

    async def test_tokens_are_device_scoped_and_expire(self):
        now=[0]; t=Transport([Response(data=playback())]); m=MusicService('spotify',t,lambda:now[0]); s=await m.scene('d','music')
        with self.assertRaises(ValueError): await m.scene('other',s.actions[0].id)
        s=await m.scene('d','music'); now[0]=31
        with self.assertRaises(ValueError): await m.scene('d',s.actions[0].id)

    async def test_restricted_and_errors(self):
        for response,status in [(Response(data=playback(restricted=True)),'ready'),(Response(204),'no_playback'),(Response(401),'revoked'),(Response(403),'forbidden_or_quota'),(Response(429,headers={'Retry-After':'300'}),'rate_limited'),(Response(500),'unavailable')]:
            t=Transport([response]); m=MusicService('spotify',t); s=await m.scene('d','music')
            self.assertEqual(s.items[0]['label'],status.replace('_',' ') if status!='ready' else 'Playing; controls restricted'); self.assertEqual(len(s.actions),1)

    async def test_uncertain_skip_is_not_retried(self):
        t=Transport([Response(data=playback()),Response(data=playback()),TimeoutError(),Response(500)])
        m=MusicService('spotify',t); s=await m.scene('d','music'); s=await m.scene('d',s.actions[2].id)
        self.assertEqual(sum(method=='POST' for method,_ in t.calls),1)
        self.assertIn('uncertain',s.items[0]['label']); self.assertEqual(len(s.actions),1)

    async def test_refresh_backoff_and_sanitation(self):
        now=[0]; t=Transport([Response(429,headers={'Retry-After':'300'})]); m=MusicService('spotify',t,lambda:now[0])
        await m.scene('d','music'); now[0]=20; await m.scene('d','music'); self.assertEqual(len(t.calls),1)

if __name__=='__main__': unittest.main()

class BoundaryTests(unittest.TestCase):
    def test_pkce_scopes_and_numeric_loopback(self):
        from urllib.parse import urlsplit, parse_qs
        from app.spotify_pkce import authorization_request, SCOPES
        url,state,verifier,redirect = authorization_request('abc123',8765)
        query=parse_qs(urlsplit(url).query)
        self.assertEqual(redirect,'http://127.0.0.1:8765/oauth/spotify/callback')
        self.assertEqual(query['scope'],[' '.join(SCOPES)])
        self.assertEqual(query['code_challenge_method'],['S256'])
        self.assertGreaterEqual(len(verifier),43)

    def test_no_voice_or_model_integration_and_no_real_config(self):
        from pathlib import Path
        from app.config import Settings
        self.assertEqual(Settings(_env_file=None).music_provider,'none')
        with self.assertRaises(ValueError): Settings(_env_file=None,music_provider='spotify')
        agent=(Path(__file__).parents[1]/'app/agent.py').read_text()
        self.assertNotIn('musicctl',agent); self.assertNotIn('MusicService',agent)

class CommandErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_rejection_clears_controls_and_honors_backoff(self):
        for code, status in [(401,'revoked'),(403,'forbidden_or_quota'),(429,'rate_limited')]:
            now=[0]; t=Transport([Response(data=playback()),Response(data=playback()),Response(code,headers={'Retry-After':'120'})])
            m=MusicService('spotify',t,lambda:now[0]); s=await m.scene('d','music'); s=await m.scene('d',s.actions[0].id)
            self.assertEqual(len(s.actions),1); self.assertEqual(m.cache.status,status)
            if code==429:
                now[0]=20; await m.scene('d','music'); self.assertEqual(len(t.calls),3)

    async def test_parallel_duplicate_tap_sends_once(self):
        t=Transport([Response(data=playback()),Response(data=playback()),Response(204),Response(data=playback())])
        m=MusicService('spotify',t); s=await m.scene('d','music'); action=s.actions[2].id
        outcomes=await asyncio.gather(m.scene('d',action),m.scene('d',action),return_exceptions=True)
        self.assertEqual(sum(method=='POST' for method,_ in t.calls),1)
        self.assertEqual(sum(isinstance(x,ValueError) for x in outcomes),1)

class MoreFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_dismiss_invalidates_all_device_actions(self):
        t=Transport([Response(data=playback())]);m=MusicService('spotify',t)
        s=await m.scene('d','music');m.dismiss('d')
        with self.assertRaises(ValueError):await m.scene('d',s.actions[0].id)
        self.assertEqual(len(t.calls),1)

    async def test_unsupported_no_device_and_long_unicode_fallback(self):
        for data,status in [(dict(playback(),device=None),'no_device'),(dict(playback(),currently_playing_type='episode'),'unsupported')]:
            s=await MusicService('spotify',Transport([Response(data=data)])).scene('d','music')
            self.assertEqual(s.items[0]['label'],status.replace('_',' ') if status!='ready' else 'Playing; controls restricted');self.assertEqual(len(s.actions),1)
        data=playback();data['item']['name']='Café 東京 🎵 '+('Long metadata '*30)
        data['item']['artists']=[{'name':'Élodie 東京'}]
        s=await MusicService('spotify',Transport([Response(data=data)])).scene('d','music')
        self.assertTrue(s.title.isascii());self.assertTrue(s.subtitle.isascii())
        self.assertLessEqual(len(s.title),48);self.assertIn('?',s.title)

class MusicSocketPrivacyTests(unittest.TestCase):
    from test_device_socket import DeviceSocketTests
    setUp=DeviceSocketTests.setUp
    tearDown=DeviceSocketTests.tearDown
    authenticate=DeviceSocketTests.authenticate

    def test_scene_and_controls_never_enter_jobs_history_or_model(self):
        from unittest.mock import patch,AsyncMock
        from fastapi.testclient import TestClient
        from app.main import app
        import sqlite3
        from contextlib import closing
        t=Transport([Response(data=playback()),Response(data=playback()),Response(204),Response(data=playback(playing=False))])
        m=MusicService('spotify',t)
        with patch('app.main.music',m),patch('app.main.agent.handle',new_callable=AsyncMock) as model,patch('app.main.job_store.enqueue') as enqueue:
            with TestClient(app) as client:
                with client.websocket_connect('/ws/device/music-private') as socket:
                    self.authenticate(socket,'music-private')
                    socket.send_json({'type':'scene.action','action':'music'})
                    surface=socket.receive_json()['surface']
                    self.assertEqual(surface['title'],'Original fixture')
                    token=surface['actions'][0]['id']
                    socket.send_json({'type':'scene.action','action':token})
                    self.assertEqual(socket.receive_json()['type'],'surface')
                    socket.send_json({'type':'scene.action','action':token})
                    self.assertEqual(socket.receive_json()['code'],'bad_action')
                    socket.send_json({'type':'scene.action','action':'dismiss'})
                    self.assertEqual(socket.receive_json()['surface']['kind'],'ambient')
            model.assert_not_called();enqueue.assert_not_called()
        self.assertEqual(self.reminder_store.conversation('music-private'),[])
        with closing(sqlite3.connect(self.job_store.path)) as db:
            self.assertEqual(db.execute('select count(*) from agent_jobs').fetchone()[0],0)

class HttpAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapter_with_mocked_http_never_needs_account(self):
        import httpx
        from app.tools.music import SpotifyTransport
        calls=[]
        def respond(request):
            calls.append(request)
            self.assertEqual(request.url.host,'api.spotify.com')
            self.assertEqual(request.headers['Authorization'],'Bearer synthetic-token')
            return httpx.Response(200,json=playback()) if request.method=='GET' else httpx.Response(204)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            m=MusicService('spotify',SpotifyTransport(client,'synthetic-token'))
            scene=await m.scene('d','music');await m.scene('d',scene.actions[0].id)
        self.assertEqual([(r.method,r.url.path) for r in calls],[('GET','/v1/me/player'),('GET','/v1/me/player'),('PUT','/v1/me/player/pause'),('GET','/v1/me/player')])

    async def test_track_specific_disallows_hide_only_unsupported_controls(self):
        data=playback();data['actions']['disallows']={'skipping_next':True,'pausing':True}
        s=await MusicService('spotify',Transport([Response(data=data)])).scene('d','music')
        self.assertEqual([a.label for a in s.actions],['Previous','Dismiss'])
