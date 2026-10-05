"""Explicit personal PKCE auth. No runtime integration or credential logging."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import stat
import time
from urllib.parse import parse_qs, urlsplit
import httpx
from .spotify_pkce import SCOPES

TOKEN_URL = 'https://accounts.spotify.com/api/token'
CALLBACK_PATH = '/oauth/spotify/callback'

class AuthUnavailable(RuntimeError):
    def __init__(self, reason='unavailable'):
        self.reason=reason
        super().__init__('Spotify authorization unavailable: '+reason)

@dataclass(repr=False)
class Credentials:
    client_id: str
    refresh_token: str = field(repr=False)
    scope: str = ' '.join(SCOPES)
    enabled: bool = True


def exact_scope(value):
    if not isinstance(value,str) or len(value)>256: raise AuthUnavailable('scope_mismatch')
    parts=value.split()
    if len(parts)!=len(SCOPES) or set(parts)!=set(SCOPES): raise AuthUnavailable('scope_mismatch')
    return ' '.join(SCOPES)


def opaque(value):
    return isinstance(value,str) and 1<=len(value)<=8192 and all(33<=ord(c)<=126 for c in value)


def validate_credentials(value):
    if (not isinstance(value,dict) or set(value)!={'client_id','refresh_token','scope','enabled'}
        or not isinstance(value['client_id'],str) or not value['client_id'].isascii()
        or not value['client_id'].isalnum() or len(value['client_id'])>128
        or not opaque(value['refresh_token']) or type(value['enabled']) is not bool):
        raise AuthUnavailable('unsafe_storage')
    exact_scope(value['scope'])
    return Credentials(**value)


@contextmanager
def private_directory(path: Path, create=False):
    path=Path(path)
    descriptor=None
    try:
        if create: path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        meta=os.fstat(descriptor)
        if meta.st_uid!=os.getuid() or meta.st_mode&0o077: raise AuthUnavailable('unsafe_storage')
        yield descriptor
    except FileNotFoundError: raise AuthUnavailable('unconnected') from None
    except OSError: raise AuthUnavailable('unsafe_storage') from None
    finally:
        if descriptor is not None: os.close(descriptor)


def read_at(directory,name):
    try:
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as source:
            meta=os.fstat(source.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid() or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_size>65536:
                raise AuthUnavailable('unsafe_storage')
            return validate_credentials(json.loads(source.read(65537)))
    except FileNotFoundError: raise AuthUnavailable('unconnected') from None
    except (OSError,ValueError,UnicodeError): raise AuthUnavailable('unsafe_storage') from None


def read_credentials(path):
    with private_directory(path) as directory:return read_at(directory,Path(path).name)


def encode(value):
    data={'client_id':value.client_id,'refresh_token':value.refresh_token,'scope':value.scope,'enabled':value.enabled}
    validate_credentials(data)
    return json.dumps(data).encode('ascii')


def write_new(path,value):
    data=encode(value);path=Path(path)
    with private_directory(path,create=True) as directory:
        try:fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        except FileExistsError:raise AuthUnavailable('destination_exists') from None
        try:
            with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
            os.fsync(directory)
        except OSError:
            # Preserve evidence instead of deleting a potentially partial credential file.
            raise AuthUnavailable('storage_failed') from None


@contextmanager
def credential_lock(path):
    path=Path(path)
    with private_directory(path) as directory:
        fd=os.open(path.name+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            meta=os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid() or stat.S_IMODE(meta.st_mode)!=0o600:
                raise AuthUnavailable('unsafe_storage')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise AuthUnavailable('busy') from None
            yield directory
        finally:os.close(fd)


def replace_locked(directory,name,value):
    data=encode(value);temporary=name+'.rotation-'+secrets.token_hex(12)
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
    try:
        with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(temporary,name,src_dir_fd=directory,dst_dir_fd=directory)
        os.fsync(directory)
    except OSError:raise AuthUnavailable('storage_failed') from None
    finally:
        # Only remove this operation's private temporary file; never delete the grant.
        try:os.unlink(temporary,dir_fd=directory)
        except FileNotFoundError:pass


def callback_code(target,state,expected_origin=None):
    def invalid(detail):
        error=AuthUnavailable('invalid_callback');error.safe_detail=detail
        raise error
    if not isinstance(target,str) or len(target)>16384:invalid('target_size')
    try:parsed=urlsplit(target)
    except ValueError:invalid('target_format')
    # HTTP origin-form is normal. HTTP/1.1 also requires accepting absolute-form
    # at origin servers; only the listener's exact approved loopback origin may
    # be used here. Host validation remains separate and mandatory in the listener.
    if parsed.scheme or parsed.netloc:
        if expected_origin is None:invalid('absolute_target')
        try:origin=urlsplit(expected_origin)
        except ValueError:invalid('origin_format')
        if (parsed.scheme!='http' or parsed.netloc!=origin.netloc or origin.scheme!='http'
            or origin.hostname!='127.0.0.1' or parsed.username or parsed.password):invalid('absolute_origin')
    if parsed.fragment or parsed.path!=CALLBACK_PATH:invalid('callback_path')
    try:values=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=32,strict_parsing=True)
    except ValueError:invalid('query_format')
    if any(len(v)!=1 for v in values.values()):invalid('duplicate_parameter')
    if 'state' not in values:invalid('missing_state')
    if not opaque(values['state'][0]) or not opaque(state):invalid('state_format')
    if not secrets.compare_digest(values['state'][0],state):invalid('state_mismatch')
    # RFC 6749 4.1.2 requires ignoring unrecognized response parameters.
    # Optional issuer is checked if present; none of the other extras are used,
    # persisted, echoed or logged. Authorization remains bound by state + PKCE.
    if 'iss' in values and values['iss'][0]!='https://accounts.spotify.com':invalid('issuer_mismatch')
    if 'error' in values:
        if 'code' in values:invalid('mixed_response')
        raise AuthUnavailable('denied')
    codes=values.get('code',[])
    if len(codes)!=1 or not opaque(codes[0]):invalid('code_format')
    return codes[0]


async def token_request(client,values,previous_scope=None):
    try:
        async with asyncio.timeout(10):
            async with client.stream('POST',TOKEN_URL,data=values,timeout=8,follow_redirects=False) as response:
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>65536:raise AuthUnavailable('oversized_response')
                if response.status_code==429:
                    try:
                        delay=float(response.headers.get('Retry-After','60'))
                        if not math.isfinite(delay) or delay<0:delay=60
                        else:delay=max(1,min(86400,delay))
                    except (ValueError,TypeError):delay=60
                    error=AuthUnavailable('rate_limited');error.retry_after=delay;raise error
                if response.status_code in {400,401}:
                    try:error=json.loads(data).get('error')
                    except (ValueError,AttributeError):error=None
                    raise AuthUnavailable('revoked' if error=='invalid_grant' else 'grant_rejected')
                if response.status_code!=200:raise AuthUnavailable('unavailable')
                result=json.loads(data)
                if not isinstance(result,dict):raise AuthUnavailable('invalid_response')
                exact_scope(result.get('scope',previous_scope))
                if not opaque(result.get('access_token')) or str(result.get('token_type','')).lower()!='bearer':
                    raise AuthUnavailable('invalid_response')
                lifetime=result.get('expires_in')
                if type(lifetime) is not int or not 60<=lifetime<=86400:raise AuthUnavailable('invalid_response')
                if 'refresh_token' in result and not opaque(result['refresh_token']):raise AuthUnavailable('invalid_response')
                return result
    except (httpx.HTTPError,TimeoutError,ValueError,UnicodeError):raise AuthUnavailable('unavailable') from None


async def exchange_and_store(client,client_id,code,verifier,redirect,path):
    # Authorization helper generates these values; refuse non-loopback substitutions.
    validate_credentials({'client_id':client_id,'refresh_token':'validation-only','scope':' '.join(SCOPES),'enabled':True})
    try:
        parsed=urlsplit(redirect)
        port=parsed.port
    except (ValueError,TypeError):raise AuthUnavailable('invalid_redirect') from None
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not port or not 1024<=port<=65535 or parsed.path!=CALLBACK_PATH or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AuthUnavailable('invalid_redirect')
    if not opaque(code) or not isinstance(verifier,str) or not 43<=len(verifier)<=128 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~' for c in verifier):
        raise AuthUnavailable('invalid_exchange')
    with private_directory(path,create=True) as directory:
        try:os.stat(Path(path).name,dir_fd=directory,follow_symlinks=False)
        except FileNotFoundError:pass
        else:raise AuthUnavailable('destination_exists')
    result=await token_request(client,{'grant_type':'authorization_code','client_id':client_id,
        'code':code,'code_verifier':verifier,'redirect_uri':redirect})
    if not opaque(result.get('refresh_token')):raise AuthUnavailable('offline_grant_missing')
    write_new(path,Credentials(client_id,result['refresh_token']))


class TokenManager:
    def __init__(self,path,client,clock=time.monotonic):
        self.path,self.client,self.clock=Path(path),client,clock
        self.lock=asyncio.Lock();self.access_token=None;self.expires=0;self.retry_until=0;self.blocked=None

    def clear(self):self.access_token=None;self.expires=0

    async def token(self):
        async with self.lock:
            try:return await self._locked_token()
            except AuthUnavailable:
                self.clear()
                raise

    async def _locked_token(self):
        now=self.clock()
        # Always check the private enabled flag; another process may have disconnected.
        with credential_lock(self.path) as directory:
            credentials=read_at(directory,self.path.name)
            if not credentials.enabled:self.clear();raise AuthUnavailable('disconnected')
            if self.blocked:self.clear();raise AuthUnavailable(self.blocked)
            if self.access_token and now<self.expires:return self.access_token
            if now<self.retry_until:raise AuthUnavailable('backoff')
            try:
                result=await token_request(self.client,{'grant_type':'refresh_token','client_id':credentials.client_id,
                    'refresh_token':credentials.refresh_token},previous_scope=credentials.scope)
                rotated=result.get('refresh_token',credentials.refresh_token)
                if rotated!=credentials.refresh_token:
                    replace_locked(directory,self.path.name,Credentials(credentials.client_id,rotated))
                self.access_token=result['access_token'];self.expires=self.clock()+result['expires_in']-30
                return self.access_token
            except AuthUnavailable as error:
                self.clear()
                if error.reason in {'revoked','scope_mismatch','grant_rejected'}:self.blocked=error.reason
                else:self.retry_until=self.clock()+getattr(error,'retry_after',30)
                raise

    async def disconnect(self):
        async with self.lock:
            self.clear();self.blocked='disconnected'
            disable_local(self.path)


def disable_local(path):
    # Preserve the grant for deliberate recovery/revocation. No Spotify revoke call or deletion.
    path=Path(path)
    with credential_lock(path) as directory:
        value=read_at(directory,path.name)
        replace_locked(directory,path.name,Credentials(value.client_id,value.refresh_token,value.scope,False))
