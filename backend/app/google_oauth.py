"""User-owned Google Desktop OAuth; no dot credentials or calendar write scope."""
from __future__ import annotations
import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

SCOPE = 'https://www.googleapis.com/auth/calendar.events.readonly'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'


class OAuthUnavailable(RuntimeError):
    def __init__(self, reason='error'):
        self.reason = reason
        super().__init__('Google Calendar account authorization is unavailable')


@dataclass(repr=False)
class GoogleCredentials:
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)


def private_json(path: Path) -> dict:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_uid != os.getuid() or metadata.st_size > 65536:
                raise OAuthUnavailable('unsafe_storage')
            data = source.read(65537)
            if len(data) > 65536: raise OAuthUnavailable("unsafe_storage")
            value = json.loads(data)
        if not isinstance(value, dict): raise OAuthUnavailable('unsafe_storage')
        return value
    except FileNotFoundError:
        raise OAuthUnavailable('unconnected') from None
    except (OSError, ValueError, UnicodeError):
        raise OAuthUnavailable('unsafe_storage') from None


def read_credentials(path: Path) -> GoogleCredentials:
    value = private_json(path)
    if value.get('scope') != SCOPE or any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 8192 for key in ('client_id', 'client_secret', 'refresh_token')):
        raise OAuthUnavailable('unsafe_storage')
    return GoogleCredentials(value['client_id'], value['client_secret'], value['refresh_token'])


def write_credentials(path: Path, value: GoogleCredentials) -> None:
    # First connection only: reconnect replacement is deliberately an operator action.
    if any(not isinstance(v, str) or not 1 <= len(v) <= 8192 for v in (value.client_id, value.client_secret, value.refresh_token)):
        raise OAuthUnavailable("invalid_grant_storage")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat()
    if path.parent.is_symlink() or parent.st_mode & 0o077 or parent.st_uid != os.getuid():
        raise OAuthUnavailable('unsafe_storage')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as destination:
            json.dump({'client_id': value.client_id, 'client_secret': value.client_secret,
                       'refresh_token': value.refresh_token, 'scope': SCOPE}, destination)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def desktop_client(path: Path) -> tuple[str, str]:
    value = private_json(path).get('installed', {})
    if not isinstance(value, dict) or not isinstance(value.get('client_id'), str) or not value['client_id'].endswith('.apps.googleusercontent.com') or not isinstance(value.get('client_secret'), str) or not 1 <= len(value['client_id']) <= 512 or not 1 <= len(value['client_secret']) <= 4096:
        raise OAuthUnavailable('invalid_client')
    return value['client_id'], value['client_secret']


def authorization_request(client_id: str, port: int) -> tuple[str, str, str, str]:
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
    redirect = f'http://127.0.0.1:{port}/oauth/callback'
    query = {'client_id': client_id, 'redirect_uri': redirect, 'response_type': 'code',
             'scope': SCOPE, 'state': state, 'code_challenge': challenge,
             'code_challenge_method': 'S256', 'access_type': 'offline', 'prompt': 'consent',
             'include_granted_scopes': 'false'}
    return AUTH_URL + '?' + urlencode(query), state, verifier, redirect


def callback_code(path: str, state: str) -> str:
    parsed = urlsplit(path)
    if len(path) > 4096 or parsed.path != '/oauth/callback': raise OAuthUnavailable('invalid_callback')
    values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
    if len(values.get('state', [])) != 1 or not secrets.compare_digest(values['state'][0], state):
        raise OAuthUnavailable('invalid_callback')
    if 'error' in values: raise OAuthUnavailable('denied')
    codes = values.get('code', [])
    if len(codes) != 1 or not 1 <= len(codes[0]) <= 2048: raise OAuthUnavailable('invalid_callback')
    return codes[0]


async def token_request(client: httpx.AsyncClient, values: dict) -> dict:
    try:
        async with client.stream('POST', TOKEN_URL, data=values, timeout=8, follow_redirects=False) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 65536: raise OAuthUnavailable('error')
            result = json.loads(data)
            if response.status_code in (400, 401) and isinstance(result, dict) and result.get('error') == 'invalid_grant':
                raise OAuthUnavailable('revoked')
            if response.status_code != 200 or not isinstance(result, dict): raise OAuthUnavailable('error')
            token = result.get('access_token')
            token_type = result.get('token_type')
            if not isinstance(token, str) or not 1 <= len(token) <= 8192 or not isinstance(token_type, str) or token_type.lower() != 'bearer':
                raise OAuthUnavailable('error')
            if result.get('scope', SCOPE) != SCOPE: raise OAuthUnavailable('revoked')
            return result
    except (httpx.HTTPError, ValueError, UnicodeError):
        raise OAuthUnavailable('error') from None
