"""Pure PKCE request builder. No browser, listener, token exchange or storage."""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

SCOPES = ('user-read-playback-state', 'user-modify-playback-state')


def authorization_request(client_id: str, port: int):
    if not isinstance(client_id, str) or not client_id.isascii() or not client_id.isalnum() or len(client_id) > 128:
        raise ValueError('Invalid client ID')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid callback port')
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
    redirect = f'http://127.0.0.1:{port}/oauth/spotify/callback'
    url = 'https://accounts.spotify.com/authorize?' + urlencode({
        'client_id': client_id, 'response_type': 'code', 'redirect_uri': redirect,
        'scope': ' '.join(SCOPES), 'state': state, 'code_challenge_method': 'S256',
        'code_challenge': challenge})
    return url, state, verifier, redirect
