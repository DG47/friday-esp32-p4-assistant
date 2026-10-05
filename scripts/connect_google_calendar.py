#!/usr/bin/env python3
"""Explicit operator-run local Desktop OAuth connection, never a LAN endpoint."""
import argparse
import os
import tempfile
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import time
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import httpx
from app.google_oauth import (SCOPE, GoogleCredentials, OAuthUnavailable,
    authorization_request, callback_code, desktop_client, token_request, write_credentials)


def obtain_code(client_id, browser_open=webbrowser.open, lifetime=180):
    result = {}
    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)
        def log_message(self, *args): pass  # Never log callback URLs/codes.
        def do_GET(self):
            if self.headers.get('Host') != f'127.0.0.1:{server.server_port}':
                self.send_error(400);return
            try:
                result['code'] = callback_code(self.path, state)
            except OAuthUnavailable as error:
                if error.reason == 'denied': result['denied'] = True
                self.send_response(400);self.end_headers();self.wfile.write(b'Calendar connection was not accepted. Return to Friday on the Mac.');return
            self.send_response(200)
            self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Referrer-Policy','no-referrer')
            self.end_headers();self.wfile.write(b'Authorization received. Close this tab and return to Friday on the Mac.')
    with HTTPServer(('127.0.0.1', 0), Callback) as server:
        server.timeout = 0.5
        url, state, verifier, redirect = authorization_request(client_id, server.server_port)
        if not browser_open(url): raise OAuthUnavailable('browser_unavailable')
        until=time.monotonic()+lifetime
        while time.monotonic()<until and not result:
            server.handle_request()
    if result.get('denied'): raise OAuthUnavailable('denied')
    if not result.get('code'): raise OAuthUnavailable('timeout')
    return result['code'], verifier, redirect


async def exchange_and_store(client_id, client_secret, code, verifier, redirect, destination, client=None):
    owned = client is None
    client = client or httpx.AsyncClient(timeout=8, follow_redirects=False)
    try:
        result = await token_request(client, {'client_id':client_id,'client_secret':client_secret,
            'code':code,'code_verifier':verifier,'redirect_uri':redirect,'grant_type':'authorization_code'})
        # Require the reported grant to match the requested read-only scope exactly.
        if result.get('scope') != SCOPE or not isinstance(result.get('refresh_token'),str) or not result['refresh_token']:
            raise OAuthUnavailable('scope_or_offline_grant_missing')
        write_credentials(destination,GoogleCredentials(client_id,client_secret,result['refresh_token']))
    finally:
        if owned: await client.aclose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client',required=True,type=Path,help='Private user-owned Google Desktop client JSON')
    parser.add_argument('--destination',type=Path,default=Path('data/google-calendar/credentials.json'))
    parser.add_argument('--approve-account-access',action='store_true',help='Explicitly permit account authorization and private local refresh-token storage')
    args=parser.parse_args()
    if not args.approve_account_access:parser.error('Account connection is a separate approval; pass --approve-account-access only after reviewing the documented scope/storage.')
    if args.destination.exists():parser.error('Destination exists. Preserve/revoke the previous grant deliberately before reconnecting; it will not be overwritten.')
    args.destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata=args.destination.parent.stat()
    if args.destination.is_symlink() or args.destination.parent.is_symlink() or metadata.st_mode & 0o077 or metadata.st_uid != os.getuid():
        parser.error('OAuth storage must be a private owned directory, with no destination symlink.')
    with tempfile.TemporaryFile(dir=args.destination.parent): pass
    print('Permission: view Google Calendar events (no writes). Runtime reads the selected primary calendar.')
    print('Grant scope:',SCOPE)
    print('Private refresh-token destination:',args.destination.resolve())
    try:
        client_id,client_secret=desktop_client(args.client)
        code,verifier,redirect=obtain_code(client_id)
        asyncio.run(exchange_and_store(client_id,client_secret,code,verifier,redirect,args.destination))
    except (OAuthUnavailable,OSError,ValueError):
        raise SystemExit('Calendar connection did not complete. No credential values are logged; review account/permission/local-storage setup.') from None
    print('Read-only Calendar authorization stored privately. Enable CALENDAR_PROVIDER=google separately after reviewing the account and local configuration.')


if __name__=='__main__':main()
