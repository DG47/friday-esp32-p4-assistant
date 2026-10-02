import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
import httpx
from app.spotify_auth import *
from app.spotify_pkce import authorization_request

SCOPE=' '.join(SCOPES)
def grant(**updates):return dict({'access_token':'synthetic-access','refresh_token':'synthetic-rotated','token_type':'Bearer','expires_in':3600,'scope':SCOPE},**updates)

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'private/grant.json'
    def tearDown(self):self.tmp.cleanup()
    def test_storage_and_no_overwrite_or_symlink(self):
        write_new(self.path,Credentials('client123','synthetic-refresh'))
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        self.assertEqual(self.path.parent.stat().st_mode&0o777,0o700)
        self.assertEqual(read_credentials(self.path).refresh_token,'synthetic-refresh')
        with self.assertRaises(AuthUnavailable):write_new(self.path,Credentials('client123','other'))
        self.path.chmod(0o644)
        with self.assertRaises(AuthUnavailable):read_credentials(self.path)
        self.path.chmod(0o600)
        alias=self.path.parent/'alias';alias.symlink_to(self.path)
        with self.assertRaises(AuthUnavailable):read_credentials(alias)
        self.assertNotIn('synthetic-refresh',repr(Credentials('client123','synthetic-refresh')))
    def test_local_disable_preserves_grant(self):
        write_new(self.path,Credentials('client123','synthetic-refresh'));disable_local(self.path)
        value=read_credentials(self.path)
        self.assertFalse(value.enabled);self.assertEqual(value.refresh_token,'synthetic-refresh')
    def test_private_directory_and_lock(self):
        write_new(self.path,Credentials('client123','synthetic-refresh'))
        with credential_lock(self.path):
            with self.assertRaises(AuthUnavailable) as error:
                with credential_lock(self.path):pass
            self.assertEqual(error.exception.reason,'busy')
        self.path.parent.chmod(0o755)
        with self.assertRaises(AuthUnavailable):read_credentials(self.path)
    def test_callback_rejects_duplicate_wrong_state_and_absolute_urls(self):
        self.assertEqual(callback_code(CALLBACK_PATH+'?code=synthetic-code&state=s','s'),'synthetic-code')
        for target in [CALLBACK_PATH+'?code=c&state=wrong',CALLBACK_PATH+'?code=c&code=d&state=s','http://127.0.0.1'+CALLBACK_PATH+'?code=c&state=s',CALLBACK_PATH+'?state=s&code=c#x','/other?code=c&state=s']:
            with self.assertRaises(AuthUnavailable):callback_code(target,'s')
        self.assertEqual(callback_code(CALLBACK_PATH+'?code=c&state=s&extra=x','s'),'c')
        with self.assertRaises(AuthUnavailable) as e:callback_code(CALLBACK_PATH+'?error=access_denied&state=s','s')
        self.assertEqual(e.exception.reason,'denied')
    def test_pkce_is_s256_and_unique(self):
        url,state,verifier,redirect=authorization_request('client123',8765)
        query=parse_qs(urlsplit(url).query)
        import base64
        self.assertEqual(query['code_challenge'],[base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()])
        self.assertNotEqual(state,authorization_request('client123',8765)[1])

class TokenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'private/grant.json';self.calls=[]
    def tearDown(self):self.tmp.cleanup()
    def client(self,response):
        def handler(request):
            self.calls.append(request)
            self.assertEqual(str(request.url),TOKEN_URL)
            self.assertNotIn('authorization',request.headers)
            return response(request) if callable(response) else response
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async def test_exchange_stores_only_refresh_grant(self):
        async with self.client(httpx.Response(200,json=grant())) as client:
            await exchange_and_store(client,'client123','synthetic-code','a'*43,'http://127.0.0.1:8765'+CALLBACK_PATH,self.path)
        self.assertEqual(read_credentials(self.path).refresh_token,'synthetic-rotated')
        self.assertNotIn('synthetic-access',self.path.read_text())
        self.assertIn(b'code_verifier=',self.calls[0].content)
    async def test_scope_missing_broader_duplicate_and_malformed_are_rejected(self):
        for data in [dict(grant(),scope=SCOPE+' user-read-email'),dict(grant(),scope=SCOPE+' '+SCOPES[0]),{k:v for k,v in grant().items() if k!='scope'},dict(grant(),expires_in=True),dict(grant(),token_type='MAC'),dict(grant(),access_token='secret\nheader')]:
            async with self.client(httpx.Response(200,json=data)) as client:
                with self.assertRaises(AuthUnavailable):await token_request(client,{'grant_type':'authorization_code'})
        self.assertFalse(self.path.exists())
    async def test_refresh_rotation_concurrent_cache_and_omitted_refresh_scope(self):
        write_new(self.path,Credentials('client123','synthetic-refresh'))
        async with self.client(httpx.Response(200,json=grant())) as client:
            manager=TokenManager(self.path,client)
            tokens=await asyncio.gather(*(manager.token() for _ in range(8)))
            self.assertEqual(tokens,['synthetic-access']*8);self.assertEqual(len(self.calls),1)
            self.assertEqual(read_credentials(self.path).refresh_token,'synthetic-rotated')
        data=grant();data.pop('scope');data.pop('refresh_token')
        async with self.client(httpx.Response(200,json=data)) as client:
            await TokenManager(self.path,client).token()
        self.assertEqual(read_credentials(self.path).refresh_token,'synthetic-rotated')
    async def test_revocation_blocks_without_deleting(self):
        write_new(self.path,Credentials('client123','synthetic-refresh'))
        async with self.client(httpx.Response(400,json={'error':'invalid_grant'})) as client:
            manager=TokenManager(self.path,client)
            for _ in range(2):
                with self.assertRaises(AuthUnavailable) as error:await manager.token()
                self.assertEqual(error.exception.reason,'revoked')
        self.assertEqual(len(self.calls),1);self.assertTrue(self.path.exists())
    async def test_rate_limit_retry_after_and_network_failure(self):
        write_new(self.path,Credentials('client123','synthetic-refresh'));now=[0]
        async with self.client(httpx.Response(429,headers={'Retry-After':'120'})) as client:
            manager=TokenManager(self.path,client,lambda:now[0])
            with self.assertRaises(AuthUnavailable):await manager.token()
            now[0]=20
            with self.assertRaises(AuthUnavailable) as error:await manager.token()
            self.assertEqual(error.exception.reason,'backoff');self.assertEqual(len(self.calls),1)
        async with self.client(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('synthetic',request=request))) as client:
            with self.assertRaises(AuthUnavailable):await TokenManager(self.path,client).token()
    async def test_local_disconnect_clears_cache_for_all_managers(self):
        write_new(self.path,Credentials('client123','synthetic-refresh'))
        async with self.client(httpx.Response(200,json=grant())) as client:
            first,second=TokenManager(self.path,client),TokenManager(self.path,client)
            await first.token();await second.token();await first.disconnect()
            with self.assertRaises(AuthUnavailable) as error:await second.token()
            self.assertEqual(error.exception.reason,'disconnected');self.assertIsNone(second.access_token)
        self.assertFalse(read_credentials(self.path).enabled)
    async def test_redirect_and_response_bounds(self):
        async with self.client(httpx.Response(302,headers={'Location':'https://example.com'})) as client:
            with self.assertRaises(AuthUnavailable):await token_request(client,{})
        async with self.client(httpx.Response(200,content=b'x'*65537)) as client:
            with self.assertRaises(AuthUnavailable) as error:await token_request(client,{})
            self.assertEqual(error.exception.reason,'oversized_response')

class ListenerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).parents[2]/'scripts/connect_spotify.py'
        spec=importlib.util.spec_from_file_location('connect_spotify_test',path)
        cls.helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.helper)
    def fake_listener(self,mode='code'):
        shared={'clock':0,'status':None,'handler':None}
        class FakeServer:
            def __init__(self,address,handler):
                if address!=('127.0.0.1',8765):raise AssertionError(address)
                self.server_port=address[1];shared['handler']=handler
            def __enter__(self):return self
            def __exit__(self,*args):shared['closed']=True
            def handle_request(self):
                shared['clock']+=1
                if mode=='timeout':return
                handler=object.__new__(shared['handler'])
                import io
                handler.wfile=io.BytesIO();handler.client_address=('127.0.0.1',1234)
                from email.message import Message
                handler.headers=Message()
                if mode!='no_host':handler.headers['Host']='127.0.0.1:8765'
                if mode=='wrong_host':handler.headers.replace_header('Host','localhost:8765')
                if mode=='duplicate_host':handler.headers['Host']='127.0.0.1:8765'
                if mode=='conflicting_host':handler.headers['Host']='example.invalid'
                query=parse_qs(urlsplit(shared['url']).query)
                state=query['state'][0]
                handler.path=CALLBACK_PATH+'?state='+state+('&error=access_denied' if mode=='denied' else '&code=synthetic-code')
                handler.respond=lambda status:shared.update(status=status)
                handler.do_GET()
        def browser(url):shared['url']=url;return True
        return shared,FakeServer,browser
    def test_valid_callback_denial_timeout_and_host(self):
        for mode in ['code','denied','timeout','wrong_host']:
            shared,server,browser=self.fake_listener(mode)
            if mode=='code':
                code,verifier,redirect=self.helper.obtain_code('client123',browser_open=browser,lifetime=3,server_factory=server,clock=lambda:shared['clock'])
                self.assertEqual(code,'synthetic-code');self.assertEqual(shared['status'],200)
            else:
                with self.assertRaises(AuthUnavailable) as error:
                    self.helper.obtain_code('client123',browser_open=browser,lifetime=3,server_factory=server,clock=lambda:shared['clock'])
                self.assertEqual(error.exception.reason,'denied' if mode=='denied' else 'timeout')
            self.assertTrue(shared['closed'])
    def test_missing_approval_is_zero_io(self):
        with patch.object(self.helper,'obtain_code') as consent,patch.object(self.helper,'private_directory') as storage:
            with self.assertRaises(SystemExit):self.helper.main(['connect','--client-id','client123','--destination','synthetic-path'])
            consent.assert_not_called();storage.assert_not_called()
    def test_unicode_state_is_safely_rejected(self):
        with self.assertRaises(AuthUnavailable):callback_code(CALLBACK_PATH+'?state=%C3%A9&code=c','s')

class FailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_rotation_preserves_old_grant_and_clears_memory(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private/grant.json';write_new(path,Credentials('client123','synthetic-old'))
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=grant()))) as client:
                manager=TokenManager(path,client)
                with patch('app.spotify_auth.replace_locked',side_effect=AuthUnavailable('storage_failed')):
                    with self.assertRaises(AuthUnavailable):await manager.token()
                self.assertIsNone(manager.access_token)
                self.assertEqual(read_credentials(path).refresh_token,'synthetic-old')
    async def test_exchange_rejects_existing_or_unsafe_destination_before_network(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private/grant.json';write_new(path,Credentials('client123','synthetic-old'))
            calls=[]
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:calls.append(request))) as client:
                with self.assertRaises(AuthUnavailable):await exchange_and_store(client,'client123','synthetic-code','a'*43,'http://127.0.0.1:8765'+CALLBACK_PATH,path)
            self.assertEqual(calls,[])

class AdditionalFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsafe_storage_clears_cached_access_and_sends_no_request(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private/grant.json';write_new(path,Credentials('client123','synthetic-old'));calls=[]
            def handler(request):calls.append(request);return httpx.Response(200,json=grant())
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                manager=TokenManager(path,client);await manager.token();path.chmod(0o644)
                with self.assertRaises(AuthUnavailable):await manager.token()
                self.assertIsNone(manager.access_token);self.assertEqual(len(calls),1)
    async def test_two_manager_rotation_collision_fails_busy_without_network(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private/grant.json';write_new(path,Credentials('client123','synthetic-old'))
            entered=asyncio.Event();release=asyncio.Event();calls=[]
            async def handler(request):
                calls.append(request);entered.set();await release.wait();return httpx.Response(200,json=grant())
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                first,second=TokenManager(path,client),TokenManager(path,client)
                task=asyncio.create_task(first.token());await entered.wait()
                with self.assertRaises(AuthUnavailable) as error:await second.token()
                self.assertEqual(error.exception.reason,'busy');release.set();await task
                self.assertEqual(len(calls),1);self.assertEqual(read_credentials(path).refresh_token,'synthetic-rotated')
    async def test_atomic_rotation_os_failure_keeps_original_file(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private/grant.json';write_new(path,Credentials('client123','synthetic-old'))
            with credential_lock(path) as directory:
                with patch('app.spotify_auth.os.replace',side_effect=OSError('synthetic-failure')):
                    with self.assertRaises(AuthUnavailable):replace_locked(directory,path.name,Credentials('client123','synthetic-new'))
            self.assertEqual(read_credentials(path).refresh_token,'synthetic-old')
            self.assertEqual(list(path.parent.glob('*.rotation-*')),[])

class HandoffTests(unittest.TestCase):
    def test_disconnect_approval_gate_has_zero_io(self):
        spec=importlib.util.spec_from_file_location('spotify_handoff',Path(__file__).parents[2]/'scripts/connect_spotify.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        with patch.object(helper,'disable_local') as disconnect:
            with self.assertRaises(SystemExit):helper.main(['disconnect','--destination','synthetic-path'])
            disconnect.assert_not_called()
    def test_real_runtime_activation_is_still_rejected(self):
        from app.config import Settings
        with self.assertRaises(ValueError):Settings(_env_file=None,music_provider='spotify')

class ReviewRegressionTests(unittest.TestCase):
    def test_private_fifo_is_rejected_without_blocking(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private/grant.json';path.parent.mkdir(mode=0o700)
            os.mkfifo(path,0o600)
            code="from pathlib import Path; import sys; from app.spotify_auth import read_credentials,AuthUnavailable\ntry: read_credentials(Path(sys.argv[1]))\nexcept AuthUnavailable as e: sys.exit(0 if e.reason=='unsafe_storage' else 2)\nelse: sys.exit(3)"
            result=subprocess.run([sys.executable,'-c',code,str(path)],timeout=2,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
    def test_malformed_callback_urls_return_fixed_rejection(self):
        for target in ['http://[/', 'http://[?state=s&code=c', '//[/', 'https://[::1']:
            with self.assertRaises(AuthUnavailable) as error:callback_code(target,'s')
            self.assertEqual(error.exception.reason,'invalid_callback')
            self.assertEqual(str(error.exception),'Spotify authorization unavailable: invalid_callback')
    def test_duplicate_conflicting_and_missing_host_headers_fail(self):
        helper=ListenerTests();helper.setUpClass()
        for mode in ['duplicate_host','conflicting_host','no_host']:
            shared,server,browser=helper.fake_listener(mode)
            with self.assertRaises(AuthUnavailable) as error:
                helper.helper.obtain_code('client123',browser_open=browser,lifetime=3,server_factory=server,clock=lambda:shared['clock'])
            self.assertEqual(error.exception.reason,'timeout')
            self.assertEqual(shared['status'],400);self.assertTrue(shared['closed'])

class RetryAfterRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonfinite_negative_huge_and_invalid_delays_are_bounded(self):
        import math
        for value,expected in [('Infinity',60),('-Infinity',60),('NaN',60),('-5',60),('1e300',86400),('1e309',60),('invalid',60),('0',1),('0.5',1),('120',120)]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(429,headers={'Retry-After':value}))) as client:
                with self.assertRaises(AuthUnavailable) as error:await token_request(client,{})
                self.assertEqual(error.exception.retry_after,expected)
                self.assertTrue(math.isfinite(error.exception.retry_after))
    async def test_infinity_does_not_permanently_disable_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private/grant.json';write_new(path,Credentials('client123','synthetic-old'))
            calls=[];now=[0]
            def handler(request):
                calls.append(request)
                return httpx.Response(429,headers={'Retry-After':'Infinity'}) if len(calls)==1 else httpx.Response(200,json=grant())
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                manager=TokenManager(path,client,lambda:now[0])
                with self.assertRaises(AuthUnavailable):await manager.token()
                now[0]=61
                self.assertEqual(await manager.token(),'synthetic-access');self.assertEqual(len(calls),2)
