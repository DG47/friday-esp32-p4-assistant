#!/usr/bin/env python3
"""Explicit operator-only Spotify PKCE connection; runtime remains disabled."""
import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import sys
import time
import webbrowser
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx
from app.spotify_pkce import authorization_request, SCOPES
from app.spotify_auth import (AuthUnavailable, callback_code, exchange_and_store,
    private_directory, disable_local)


def obtain_code(client_id, port=8765, browser_open=webbrowser.open, lifetime=180,
                server_factory=HTTPServer, clock=time.monotonic):
    if not 1<=lifetime<=300:raise AuthUnavailable('invalid_timeout')
    result={}; rejected=set()
    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup();self.connection.settimeout(3)
        def log_message(self,*args):pass
        def respond(self,status):
            self.send_response(status)
            self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'")
            self.end_headers()
            self.wfile.write(b'Spotify callback accepted. Return to Terminal for the final setup result.' if status==200 else b'Spotify callback not accepted. Return to the original consent tab and local Terminal. No credentials are displayed here.')
        def report_rejection(self,reason):
            if reason not in rejected:
                rejected.add(reason)
                print('Spotify callback rejected:',reason,flush=True)
        def do_GET(self):
            hosts=self.headers.get_all('Host',[])
            if result or clock()>=deadline or self.client_address[0]!='127.0.0.1' or hosts!=[f'127.0.0.1:{server.server_port}']:
                self.report_rejection('callback_host_or_expired')
                self.respond(400);return
            try:code=callback_code(self.path,state,f'http://127.0.0.1:{server.server_port}')
            except AuthUnavailable as error:
                if error.reason=='denied':result['denied']=True
                self.report_rejection(error.reason + (':' + error.safe_detail if hasattr(error,'safe_detail') else ''))
                self.respond(400);return
            result['code']=code;self.respond(200)
        def do_POST(self):self.respond(405)
    with server_factory(('127.0.0.1',port),Callback) as server:
        server.timeout=.5
        url,state,verifier,redirect=authorization_request(client_id,server.server_port)
        deadline=clock()+lifetime
        if not browser_open(url):raise AuthUnavailable('browser_unavailable')
        while clock()<deadline and not result:server.handle_request()
    if result.get('denied'):raise AuthUnavailable('denied')
    if not result.get('code'):raise AuthUnavailable('timeout')
    return result['code'],verifier,redirect


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    connect=commands.add_parser('connect')
    connect.add_argument('--client-id',required=True,help='Public ID of your approved personal Spotify app; no client secret')
    connect.add_argument('--port',type=int,default=8765)
    connect.add_argument('--destination',required=True,type=Path,help='Approved private token file outside source control')
    connect.add_argument('--approve-account-access',action='store_true')
    disconnect=commands.add_parser('disconnect')
    disconnect.add_argument('--destination',required=True,type=Path)
    disconnect.add_argument('--approve-local-disconnect',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='disconnect':
        if not args.approve_local_disconnect:parser.error('Explicit local-disconnect approval is required.')
        try:disable_local(args.destination)
        except (AuthUnavailable,OSError):raise SystemExit('Local disconnect could not be saved. Credentials were not deleted.') from None
        print('Local authorization disabled and preserved. Spotify account grant was not revoked; review app access in Spotify separately.')
        return
    if not args.approve_account_access:parser.error('Account consent/storage requires separate explicit approval; this helper does not approve Spotify policy compliance.')
    try:
        # Validate all local inputs/storage BEFORE opening consent or exchanging a code.
        authorization_request(args.client_id,args.port)
        with private_directory(args.destination,create=True) as directory:
            try:os.stat(args.destination.name,dir_fd=directory,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise AuthUnavailable('destination_exists')
        print('This grants playback-state reads and remote playback controls for the approved personal account.')
        print('Scopes:', ' '.join(SCOPES))
        print('Private file:',args.destination.absolute())
        print('Storage is owner-permission protected, not encrypted. Runtime Spotify activation stays disabled.')
        code,verifier,redirect=obtain_code(args.client_id,args.port)
        async def exchange():
            async with httpx.AsyncClient(timeout=8,follow_redirects=False) as client:
                await exchange_and_store(client,args.client_id,code,verifier,redirect,args.destination)
        asyncio.run(exchange())
    except AuthUnavailable as error:
        raise SystemExit('Spotify setup did not complete. Safe reason: '+error.reason+'. No credentials are displayed.') from None
    except (OSError,ValueError):
        raise SystemExit('Spotify setup did not complete. No credential values are logged; review approval, app, redirect, grant and private storage.') from None
    print('Private refresh grant saved. Real Friday access remains disabled pending separate policy/account/activation review.')

if __name__=='__main__':main()
