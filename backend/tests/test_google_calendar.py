import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore
from app.google_oauth import (SCOPE, GoogleCredentials, OAuthUnavailable, authorization_request,
    callback_code, read_credentials, write_credentials, token_request)
from app.models import SurfaceAction
from app.tools.google_calendar import CalendarService, parse_event

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)

def event(identifier='fixture-one', start='2026-10-01T10:00:00-04:00', end='2026-10-01T11:00:00-04:00', **kwargs):
    return {'id':identifier, 'summary':'Fixture planning', 'start':{'dateTime':start}, 'end':{'dateTime':end}, **kwargs}


class CalendarTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'credentials.json'
        write_credentials(self.path,GoogleCredentials('fixture.apps.googleusercontent.com','fixture-client-secret','fixture-refresh-token'))
        self.now=NOW
        self.settings=Settings(_env_file=None,calendar_provider='google',calendar_google_credentials_path=self.path,assistant_timezone='America/New_York')
        self.responses=[{'items':[event()]}]
        self.requests=[]
        self.token_error=None
        def transport(request):
            self.requests.append(request)
            if request.url.host=='oauth2.googleapis.com':
                if self.token_error:return httpx.Response(400,json={'error':self.token_error})
                return httpx.Response(200,json={'access_token':'fixture-access-token','token_type':'Bearer','expires_in':3600,'scope':SCOPE})
            response=self.responses.pop(0)
            if isinstance(response,Exception):raise response
            if isinstance(response,tuple):return httpx.Response(response[0],json=response[1])
            return httpx.Response(200,json=response)
        self.client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.service=CalendarService(self.settings,client=self.client,clock=lambda:self.now)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    def gets(self):return [r for r in self.requests if r.method=='GET']

    async def test_expanded_recurring_all_day_cancelled_and_empty_page_pagination(self):
        self.responses=[{'items':[], 'nextPageToken':'page2'}, {'items':[
            event('recurring-one',recurringEventId='series'),
            event('recurring-two',start='2026-10-01T12:00:00-04:00',end='2026-10-01T13:00:00-04:00',recurringEventId='series'),
            {'id':'all-day','summary':'Fixture away','start':{'date':'2026-10-01'},'end':{'date':'2026-10-03'}},
            {'id':'cancelled','status':'cancelled'}]}]
        snapshot=await self.service.snapshot()
        self.assertEqual(snapshot.status,'ready')
        self.assertEqual(len(snapshot.events),3)
        self.assertTrue(snapshot.events[0].all_day)
        self.assertTrue(snapshot.events[1].recurring)
        first=self.gets()[0]
        self.assertEqual(first.url.params['singleEvents'],'true')
        self.assertEqual(first.url.params['showDeleted'],'false')
        self.assertEqual(first.url.params['orderBy'],'startTime')
        self.assertEqual(self.gets()[1].url.params['pageToken'],'page2')
        self.assertTrue(all(r.method in {'GET','POST'} for r in self.requests))
        self.assertTrue(all(r.url.host=='oauth2.googleapis.com' for r in self.requests if r.method=='POST'))

    async def test_spring_and_fall_day_windows(self):
        for date,hours in [(datetime(2026,3,8,12,tzinfo=timezone.utc),23),(datetime(2026,11,1,12,tzinfo=timezone.utc),25)]:
            self.now=date;self.responses=[{'items':[]}]
            await self.service.snapshot(force=True)
            params=self.gets()[-1].url.params
            lower=datetime.fromisoformat(params['timeMin']);upper=datetime.fromisoformat(params['timeMax'])
            self.assertEqual((upper-lower).total_seconds()/3600,hours)

    def test_all_day_exclusive_end_and_dst_fold_offsets(self):
        zone=ZoneInfo('America/New_York')
        parsed=parse_event(event(start='2026-11-01T01:30:00-04:00',end='2026-11-01T01:45:00-05:00'),zone)
        self.assertEqual(parsed.ends_at.timestamp()-parsed.starts_at.timestamp(),75*60)
        with self.assertRaises(ValueError):
            parse_event({'id':'gap','start':{'dateTime':'2026-03-08T02:30:00','timeZone':zone.key},'end':{'dateTime':'2026-03-08T03:30:00','timeZone':zone.key}},zone)

    async def test_stale_cache_is_labelled_then_expires(self):
        await self.service.snapshot()
        self.now+=timedelta(seconds=61);self.responses=[(503,{'error':'fixture failure'})]
        result=await self.service.snapshot()
        self.assertEqual(result.status,'stale')
        scene=await self.service.scene('calendar','device-a')
        self.assertIn('stale',scene.subtitle)
        self.now+=timedelta(minutes=11);self.responses=[(503,{})]
        self.assertEqual((await self.service.snapshot()).status,'error')

    async def test_revoked_permission_discards_cached_events_and_touch_tokens(self):
        scene=await self.service.scene('calendar','device-a')
        action=scene.items[0]['action']
        self.responses=[(403,{'error':{'errors':[{'reason':'forbidden'}]}})]
        result=await self.service.snapshot(force=True)
        self.assertEqual(result.status,'revoked');self.assertFalse(result.events)
        self.assertFalse(self.service.cache);self.assertFalse(self.service.actions)
        self.token_error='invalid_grant'
        detail=await self.service.scene(action,'device-a')
        self.assertEqual(detail.title,'Event unavailable')

    async def test_refresh_invalid_grant_is_revoked_not_generic_stale(self):
        self.token_error='invalid_grant'
        self.assertEqual((await self.service.snapshot()).status,'revoked')
        self.assertFalse(self.gets())

    async def test_http401_refreshes_once_and_does_not_leak_auth(self):
        self.responses=[(401,{}),{'items':[event()]}]
        result=await self.service.snapshot()
        self.assertEqual(result.status,'ready')
        self.assertEqual(len(self.gets()),2)
        self.assertNotIn('fixture-access-token',json.dumps(result.payload()))
        self.assertEqual(len([r for r in self.requests if r.method=='POST']),2)

    async def test_rate_limit403_is_transient_not_permission_revocation(self):
        await self.service.snapshot()
        self.now+=timedelta(seconds=61)
        self.responses=[(403,{'error':{'errors':[{'reason':'userRateLimitExceeded'}]}})]
        self.assertEqual((await self.service.snapshot()).status,'stale')

    async def test_card_pages_details_scope_expiry_and_all_day_readability(self):
        self.responses=[{'items':[event(f'fixture-{i}') for i in range(6)]}]
        scene=await self.service.scene('calendar','device-a')
        self.assertEqual(len(scene.items),4)
        self.assertIn('calendar:1',[a.id for a in scene.actions])
        detail=await self.service.scene(scene.items[0]['action'],'device-a')
        self.assertEqual(detail.kind,'artifact')
        denied=await self.service.scene(scene.items[0]['action'],'device-b')
        self.assertEqual(denied.title,'Event unavailable')
        second=await self.service.scene('calendar:1','device-a')
        self.assertEqual(len(second.items),2)
        for surface in [scene,detail,second]:
            self.assertLessEqual(len(surface.items),4);self.assertLessEqual(len(surface.actions),4)
            for action in surface.actions:self.assertLess(len(action.id.encode()),64)
        self.now+=timedelta(minutes=3);self.responses=[{'items':[event()]}]
        self.assertEqual((await self.service.scene(scene.items[0]['action'],'device-a')).title,'Event unavailable')

    async def test_missing_connection_and_empty_calendar_are_different(self):
        self.path.unlink()
        self.assertEqual((await self.service.snapshot()).status,'unconnected')
        self.assertFalse(self.requests)
        write_credentials(self.path,GoogleCredentials('fixture.apps.googleusercontent.com','fixture-client-secret','fixture-refresh-token'))
        self.responses=[{'items':[]}]
        result=await self.service.snapshot(force=True)
        self.assertEqual(result.status,'ready');self.assertFalse(result.events)
        scene=await self.service.scene('calendar','device-a')
        self.assertIn('No events today',scene.subtitle)

    async def test_repeated_page_token_and_invalid_dates_fail_honestly(self):
        self.responses=[{'items':[], 'nextPageToken':'same'}, {'items':[], 'nextPageToken':'same'}]
        self.assertEqual((await self.service.snapshot()).status,'error')
        self.now+=timedelta(seconds=16);self.responses=[{'items':[{'id':'malformed','start':{},'end':{}}]}]
        self.assertEqual((await self.service.snapshot()).status,'error')

    async def test_pagination_is_bounded_and_partial_results_are_labelled(self):
        self.responses=[{'items':[],'nextPageToken':str(i)} for i in range(10)]
        result=await self.service.snapshot()
        self.assertTrue(result.truncated);self.assertEqual(len(self.gets()),10)
        self.assertLessEqual(len(result.events),1000)

    async def test_clock_and_range_validation(self):
        with self.assertRaises(ValueError):await self.service.snapshot('writes')
        with self.assertRaises(ValueError):await self.service.snapshot(now=datetime(2026,10,1))

    async def test_titles_are_untrusted_text_not_executed_or_logged(self):
        self.responses=[{'items':[event(summary='<script>fixture()</script>\u202e\n')]}]
        scene=await self.service.scene('calendar','device-a')
        self.assertEqual(scene.items[0]['label'],'<script>fixture()</script>')
        self.assertNotIn('\u202e',scene.items[0]['label'])


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    def test_pkce_state_and_readonly_scope(self):
        import base64,hashlib
        url,state,verifier,redirect=authorization_request('fixture.apps.googleusercontent.com',42424)
        query=parse_qs(urlsplit(url).query)
        self.assertEqual(query['scope'],[SCOPE]);self.assertEqual(query['code_challenge_method'],['S256'])
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(query['code_challenge'],[challenge]);self.assertTrue(redirect.startswith('http://127.0.0.1:'))
        self.assertEqual(callback_code('/oauth/callback?state='+state+'&code=fixture-code',state),'fixture-code')
        for path in ['/oauth/callback?state=wrong&code=fixture','/other?state='+state+'&code=fixture','/oauth/callback?state='+state+'&code=one&code=two']:
            with self.assertRaises(OAuthUnavailable):callback_code(path,state)

    def test_private_credentials_no_overwrite_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'credentials.json'
            value=GoogleCredentials('fixture.apps.googleusercontent.com','fixture-client-secret','fixture-refresh-token')
            write_credentials(path,value)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(read_credentials(path).refresh_token,'fixture-refresh-token')
            with self.assertRaises(FileExistsError):write_credentials(path,value)
            symlink=Path(directory)/'linked.json';symlink.symlink_to(path)
            with self.assertRaises(OAuthUnavailable):read_credentials(symlink)
            path.chmod(0o644)
            with self.assertRaises(OAuthUnavailable):read_credentials(path)
            self.assertNotIn('fixture-refresh-token',repr(value))

    async def test_redirect_or_wider_scope_token_responses_are_rejected(self):
        for response in [httpx.Response(302,headers={'Location':'https://example.invalid/token'},json={}),httpx.Response(200,json={'access_token':'fixture-token','token_type':'Bearer','scope':'https://www.googleapis.com/auth/calendar'})]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:response)) as client:
                with self.assertRaises(OAuthUnavailable):await token_request(client,{'grant_type':'fixture'})

    async def test_exchange_stores_only_exact_scope_offline_grant(self):
        script=Path(__file__).resolve().parents[2]/'scripts/connect_google_calendar.py'
        spec=importlib.util.spec_from_file_location('connect_fixture',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'credentials.json'
            response={'access_token':'fixture-token','token_type':'Bearer','scope':SCOPE,'refresh_token':'fixture-refresh-token'}
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))) as client:
                await module.exchange_and_store('fixture.apps.googleusercontent.com','fixture-client-secret','fixture-code','fixture-verifier','http://127.0.0.1:42424/oauth/callback',path,client)
            self.assertNotIn('access_token',path.read_text())

class GoogleCalendarIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_reads_do_not_call_model_or_leak_calendar_facts_to_history(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings(_env_file=None,assistant_provider='mock',calendar_provider='google',calendar_google_credentials_path=Path(directory)/'missing.json')
            store=ReminderStore(Path(directory)/'test.db');agent=FridayAgent(settings,store)
            from app.tools.google_calendar import CalendarSnapshot
            from app.models import CalendarEvent
            record=CalendarEvent(id='fixture-private-id',title='Fixture private planning',starts_at=NOW+timedelta(hours=1),ends_at=NOW+timedelta(hours=2))
            snapshot=CalendarSnapshot('ready',[record],NOW)
            agent.calendar.clock=lambda:NOW
            with patch.object(agent.calendar,'snapshot',return_value=snapshot), patch.object(agent.providers,'select',side_effect=AssertionError('No model request allowed')):
                reply=await agent.handle('device','what is next',request_id='fixture-native')
                self.assertIn('Fixture private planning',reply.text)
            history=json.dumps(store.conversation('device'))
            self.assertNotIn('Fixture private planning',history)
            with patch.object(agent.calendar,'snapshot',return_value=snapshot):
                outcome=await agent._execute_read_tool('get_calendar',{'range':'today'},'device','fixture-model',0,for_model=True)
                self.assertEqual(json.loads(outcome.content)['status'],'sharing_disabled')
                self.assertNotIn('Fixture private planning',outcome.content)
                self.assertIn('Fixture private planning',str(outcome.surface.items))
                settings.calendar_model_context=True
                allowed=await agent._execute_read_tool('get_calendar',{'range':'today'},'device','fixture-model-allowed',0,for_model=True)
                self.assertIn('Fixture private planning',allowed.content)
            await agent.calendar.close()

    async def test_explicit_sample_preview_and_default_unconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ReminderStore(Path(directory)/'test.db')
            for provider,expected in [('none','Calendar not connected'),('sample','Calendar preview')]:
                settings=Settings(_env_file=None,assistant_provider='mock',calendar_provider=provider)
                agent=FridayAgent(settings,store)
                reply=await agent.handle('device','show calendar',request_id='fixture-'+provider)
                self.assertEqual(reply.surface.title,expected)
                if provider=='sample':self.assertIn('Sample calendar',reply.surface.subtitle)
                await agent.calendar.close()

class GoogleCalendarSocketTests(unittest.TestCase):
    def setUp(self):
        import test_device_socket
        test_device_socket.DeviceSocketTests.setUp(self)
        path=Path(self.temp_dir.name)/'credentials.json'
        write_credentials(path,GoogleCredentials('fixture.apps.googleusercontent.com','fixture-client-secret','fixture-refresh-token'))
        settings=Settings(_env_file=None,calendar_provider='google',calendar_google_credentials_path=path)
        self.responses=[{'items':[event('fixture-'+str(i)) for i in range(6)]}]
        def transport(request):
            if request.url.host=='oauth2.googleapis.com':return httpx.Response(200,json={'access_token':'fixture-access-token','token_type':'Bearer','expires_in':3600,'scope':SCOPE})
            return httpx.Response(200,json=self.responses.pop(0))
        self.http=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.calendar=CalendarService(settings,client=self.http,clock=lambda:NOW)
        self.calendar_patch=patch('app.main.agent.calendar',self.calendar);self.calendar_patch.start()

    def tearDown(self):
        import test_device_socket
        self.calendar_patch.stop()
        asyncio.run(self.http.aclose())
        test_device_socket.DeviceSocketTests.tearDown(self)

    def surface(self,socket):
        for _ in range(8):
            message=socket.receive_json()
            if message['type']=='surface':return message['surface']
        self.fail('Calendar surface not received')

    def test_authenticated_calendar_navigation_detail_and_refresh(self):
        import test_device_socket
        from app.main import app
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            with client.websocket_connect('/ws/device/friday-p3a') as socket:
                test_device_socket.DeviceSocketTests.authenticate(self,socket,'friday-p3a')
                socket.send_json({'type':'scene.action','action':'calendar'})
                first=self.surface(socket);self.assertEqual(len(first['items']),4)
                token=first['items'][0]['action']
                socket.send_json({'type':'scene.action','action':'calendar:1'})
                self.assertEqual(len(self.surface(socket)['items']),2)
                socket.send_json({'type':'scene.action','action':token})
                self.assertEqual(self.surface(socket)['kind'],'artifact')
                self.responses=[{'items':[]}]
                socket.send_json({'type':'scene.action','action':'calendar_refresh'})
                self.assertIn('No events today',self.surface(socket)['subtitle'])
                socket.send_json({'type':'scene.action','action':token})
                self.assertEqual(self.surface(socket)['title'],'Event unavailable')
                socket.send_json({'type':'scene.action','action':'dismiss'})
                self.assertEqual(self.surface(socket)['kind'],'ambient')

class CalendarEdgeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = CalendarTests.asyncSetUp
    asyncTearDown = CalendarTests.asyncTearDown
    gets = CalendarTests.gets
    async def test_all_day_uses_calendar_zone_but_preserves_original_dates(self):
        self.responses=[{'timeZone':'Asia/Tokyo','items':[{'id':'fixture-tokyo','summary':'Fixture Tokyo day','start':{'date':'2026-10-02'},'end':{'date':'2026-10-03'}}]}]
        result=await self.service.snapshot('next')
        item=result.events[0]
        self.assertEqual(item.starts_at.astimezone(timezone.utc),datetime(2026,10,1,15,tzinfo=timezone.utc))
        self.assertEqual(item.all_day_start.isoformat(),'2026-10-02')
        scene=await self.service.scene('calendar_next','device-a',snapshot=result)
        self.assertIn('Oct 02',scene.items[0]['label'])
        self.assertIn('Oct 02',scene.items[1]['label'])

    async def test_failed_forced_refresh_does_not_relabel_cache_as_live(self):
        await self.service.snapshot()
        self.responses=[(503,{})]
        self.assertEqual((await self.service.snapshot(force=True)).status,'stale')
        self.assertEqual((await self.service.snapshot()).status,'stale')

    async def test_concurrent_reads_share_one_fetch(self):
        results=await asyncio.gather(self.service.snapshot(),self.service.snapshot())
        self.assertTrue(all(result.status=='ready' for result in results))
        self.assertEqual(len(self.gets()),1)

    async def test_timeout_is_honest_and_does_not_leak_response_errors(self):
        self.responses=[httpx.ReadTimeout('fixture-private-provider-body')]
        result=await self.service.snapshot()
        self.assertEqual(result.status,'error')
        self.assertNotIn('fixture-private-provider-body',str(result.payload()))

class CalendarHistoryPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabling_model_sharing_excludes_whole_prior_calendar_exchange(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ReminderStore(Path(directory)/'test.db')
            store.append_messages('device','fixture-calendar',[{'role':'user','content':'calendar question'},
                {'role':'assistant','tool_calls':[{'id':'fixture-call','type':'function','function':{'name':'get_calendar','arguments':'{}'}}],'content':''},
                {'role':'tool','name':'get_calendar','tool_call_id':'fixture-call','content':'fixture-private-calendar-fact'},
                {'role':'assistant','content':'fixture-private-calendar-answer'}])
            store.append_messages('device','fixture-local-calendar',[{'role':'user','content':'show calendar'},
                {'role':'assistant','name':'google_calendar','content':'fixture-local-calendar-fact'}])
            store.append_exchange('device','fixture-normal','timer question','timer answer')
            store.append_exchange('other-device','fixture-other','other question','other answer')
            history=store.conversation('device',exclude_calendar=True)
            self.assertEqual(len(history),2)
            self.assertNotIn('calendar',json.dumps(history))
            self.assertNotIn('other',json.dumps(history))
            self.assertIn('fixture-private-calendar-fact',json.dumps(store.conversation('device')))

class FinalCalendarReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_token_type_is_safe_unavailable(self):
        response={'access_token':'fixture-token','token_type':None}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))) as client:
            with self.assertRaises(OAuthUnavailable):await token_request(client,{'grant_type':'fixture'})

    async def test_invalid_model_range_returns_tool_error_without_request(self):
        with tempfile.TemporaryDirectory() as directory:
            agent=FridayAgent(Settings(_env_file=None,assistant_provider='mock'),ReminderStore(Path(directory)/'test.db'))
            with patch.object(agent.calendar,'snapshot',side_effect=AssertionError('Calendar request should not execute')):
                for value in ['writes',{},None]:
                    result=await agent._execute_read_tool('get_calendar',{'range':value},'device','fixture-bad',0,for_model=True)
                    self.assertFalse(result.success)
                    self.assertIn('read only',result.surface.subtitle)
            await agent.calendar.close()

    def test_connection_cli_requires_separate_approval_before_any_storage(self):
        import subprocess,sys
        script=Path(__file__).resolve().parents[2]/'scripts/connect_google_calendar.py'
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,str(script),'--client','missing-client.json'],cwd=directory,capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIn('separate approval',result.stderr)
            self.assertFalse((Path(directory)/'data').exists())
