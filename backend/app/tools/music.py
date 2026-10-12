"""Touch-only personal prototype. No auth storage, voice routing or model tools."""
from __future__ import annotations
import asyncio
import secrets
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote
from ..models import Surface, SurfaceAction
from .lyrics import LyricsBinding, LyricsProvider, UnavailableLyrics


class Transport(Protocol):
    async def request(self, method: str, path: str): ...


class SpotifyTransport:
    """Injected HTTP client/token only; never constructed by default configuration."""
    def __init__(self, client, access_token):
        self.client, self.access_token = client, access_token

    async def request(self, method, path):
        return await self.client.request(method, 'https://api.spotify.com/v1' + path,
            headers={'Authorization': 'Bearer ' + self.access_token}, timeout=8,
            follow_redirects=False)


def text(value, limit=64):
    if not isinstance(value, str): return ''
    return ''.join(c for c in value if c.isprintable() and c not in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069')[:limit]


@dataclass
class Snapshot:
    status: str = 'disabled'
    track: str = ''
    title: str = ''
    artist: str = ''
    device: str = ''
    device_name: str = ''
    playing: bool = False
    progress: int = 0
    duration: int = 0
    observed: float = 0
    restricted: bool = True
    disallowed: dict | None = None
    isrc: str = ''
    version: str = ''
    full_title: str = ''
    full_artist: str = ''
    album_name: str = ''
    artwork: object = None

    def timed_context(self,sequence):
        # Local conversion only; does not fetch lyrics or enable timing.
        from .lyrics import TrackIdentity
        from .lyrics_sync import PlaybackObservation
        from .lrclib import LyricQuery
        identity=TrackIdentity(self.track,self.isrc,self.version) if self.track and self.version else None
        observation=PlaybackObservation(sequence,identity,self.progress,self.duration,self.playing,self.observed,
            'ready' if self.status in {'ready','sample'} else self.status)
        query=(LyricQuery(identity,self.full_title,self.full_artist,self.album_name,self.duration)
            if identity and all(isinstance(v,str) and 1<=len(v)<=1024 for v in [self.full_title,self.full_artist,self.album_name]) else None)
        return observation,query

    @property
    def context(self): return self.track, self.device, self.playing


class MusicService:
    def __init__(self, provider='none', transport=None, clock=time.monotonic, lyrics_provider=None):
        if provider not in {'none', 'synthetic', 'spotify'}: raise ValueError('Invalid provider')
        self.provider, self.transport, self.clock = provider, transport, clock
        self.lock = asyncio.Lock()
        self.cache = Snapshot()
        self.lyrics = LyricsBinding(lyrics_provider)
        self.tokens = {}
        self.retry_until = 0
        self.last_read = float('-inf')

    async def _read(self):
        now = self.clock()
        if self.provider == 'none': return Snapshot()
        if self.provider == 'synthetic':
            return Snapshot('sample', 'original-fixture', 'Synthetic track', 'Fixture artist',
                'fixture-device', 'Sample speaker', True, 42000, 180000, now, False, {}, '', 'Synthetic track | fixture | 180000ms')
        if self.transport is None: return Snapshot('unconnected')
        if now < self.retry_until: return Snapshot('rate_limited')
        try:
            response = await self.transport.request('GET', '/me/player')
            self.last_read = now
            if response.status_code == 204: return Snapshot('no_playback')
            if response.status_code == 401:
                self.tokens.clear(); self.cache = Snapshot('revoked')
                return self.cache
            if response.status_code == 403: return Snapshot('forbidden_or_quota')
            if response.status_code == 429:
                try: delay = max(1, min(86400, float(response.headers.get('Retry-After', '60'))))
                except (ValueError, TypeError): delay = 60
                self.retry_until = now + delay
                return Snapshot('rate_limited')
            if response.status_code != 200: return Snapshot('unavailable')
            data = response.json(); item = data.get('item'); device = data.get('device')
            if not isinstance(item, dict) or data.get('currently_playing_type') != 'track':
                return Snapshot('unsupported')
            if not isinstance(device, dict): return Snapshot('no_device')
            duration, progress = item.get('duration_ms'), data.get('progress_ms')
            if type(duration) is not int or type(progress) is not int or duration <= 0:
                return Snapshot('unavailable')
            disallowed = data.get('actions', {}).get('disallows', {})
            if not isinstance(disallowed, dict): return Snapshot('unavailable')
            track_id=item.get('id')
            if not isinstance(track_id,str) or not 1<=len(track_id)<=128 or not track_id.isascii() or not track_id.isalnum():
                return Snapshot('unsupported')
            external=item.get('external_ids', {})
            raw_isrc=external.get('isrc') if isinstance(external,dict) else None
            isrc=raw_isrc.upper() if isinstance(raw_isrc,str) and raw_isrc.isascii() else ''
            if not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}[0-9]{7}',isrc):isrc=''
            album=item.get('album', {})
            raw_name=item.get('name');raw_album=album.get('name','') if isinstance(album,dict) else ''
            version=(text(raw_name,1024)+' | '+text(raw_album,1024)+' | '+str(duration)+'ms'
                if isinstance(raw_name,str) and isinstance(raw_album,str) and len(raw_name)<=1024 and len(raw_album)<=1024 else '')
            artist_values=item.get('artists',[])
            full_artist=', '.join(p.get('name','') for p in artist_values if isinstance(p,dict) and isinstance(p.get('name'),str)) if isinstance(artist_values,list) else ''
            from .artwork import select_artwork
            artwork=select_artwork(album)
            return Snapshot('ready', track_id, text(item.get('name')),
                text(', '.join(text(a.get('name')) for a in item.get('artists', []) if isinstance(a, dict))),
                text(device.get('id')), text(device.get('name'), 40), data.get('is_playing') is True,
                max(0, min(progress, duration)), duration, (now+self.clock())/2,
                device.get('is_restricted') is not False, disallowed, isrc, version, text(raw_name,1024) if isinstance(raw_name,str) and len(raw_name)<=1024 else '',
                text(full_artist,1024) if len(full_artist)<=1024 else '',text(raw_album,1024) if isinstance(raw_album,str) and len(raw_album)<=1024 else '',artwork)
        except Exception:
            # Do not propagate provider bodies, tokens or song content to logs/model errors.
            return Snapshot('unavailable')

    def _allowed(self, snapshot, command):
        key = {'play': 'resuming', 'pause': 'pausing', 'next': 'skipping_next', 'previous': 'skipping_prev'}[command]
        return (snapshot.status == 'ready' and snapshot.device and snapshot.track
                and not snapshot.restricted and not (snapshot.disallowed or {}).get(key, False))

    def dismiss(self, device_id):
        self.tokens = {k: v for k, v in self.tokens.items() if v[0] != device_id}

    def _scene(self, device_id, snapshot, notice=''):
        self.lyrics.observe(snapshot)
        self.tokens = {k: v for k, v in self.tokens.items() if v[0] != device_id and self.clock() < v[3]}
        if len(self.tokens) > 1000: self.tokens.clear()
        actions = []
        if snapshot.status == 'ready':
            for command in ['pause' if snapshot.playing else 'play', 'previous', 'next']:
                if self._allowed(snapshot, command):
                    token = secrets.token_hex(12)
                    self.tokens[token] = (device_id, command, snapshot.context, self.clock() + 30)
                    actions.append(SurfaceAction(id='musicctl:' + token, label=command.title()))
        actions.append(SurfaceAction(id='dismiss', label='Dismiss'))
        elapsed = snapshot.progress
        if snapshot.status == 'ready' and snapshot.playing:
            elapsed += int(max(0, min(15, self.clock() - snapshot.observed)) * 1000)
        def timestamp(ms): return f'{ms // 60000}:{ms // 1000 % 60:02d}'
        ready = snapshot.status in {'ready', 'sample'}
        def display(value, limit=40):
            normalized = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
            return normalized.encode('ascii', 'replace').decode('ascii')[:limit]
        return Surface(kind='media', title=display(snapshot.title, 48) if ready else 'Spotify',
            subtitle=(display(snapshot.artist) + ' | ' if ready else '') + ('Synthetic preview' if snapshot.status == 'sample' else 'Spotify'),
            items=[{'time': 'State', 'label': notice or (('Playing' if snapshot.playing else 'Paused') + ('; controls restricted' if snapshot.restricted else '') if ready else snapshot.status.replace('_', ' '))},
                {'time': 'Progress', 'label': timestamp(min(elapsed, snapshot.duration)) + ' / ' + timestamp(snapshot.duration) if ready else 'Unavailable'},
                {'time': 'Device', 'label': display(snapshot.device_name) if ready else 'Not available'},
                {'time': 'Lyrics', 'label': self.lyrics.label()}],
            actions=actions, expires_ms=30000)

    async def scene(self, device_id, action, *, allowed=None):
        async with self.lock:
            if action.startswith('musicctl:'):
                entry = self.tokens.pop(action.split(':', 1)[1], None)
                if not entry or entry[0] != device_id or self.clock() >= entry[3]:
                    raise ValueError('Expired music action')
                current = await self._read()
                command = entry[1]
                if current.context != entry[2] or not self._allowed(current, command):
                    self.cache = current
                    return self._scene(device_id, current, 'Playback changed; refresh')
                if allowed is not None and not allowed():
                    return self._scene(device_id, current, 'Request no longer current; no command sent')
                # Consumed token prevents repeated taps and replay. Never retry a mutation.
                self.tokens.clear()
                method = 'POST' if command in {'next', 'previous'} else 'PUT'
                try:
                    response = await self.transport.request(method,
                        '/me/player/' + command + '?device_id=' + quote(current.device, safe=''))
                    code = response.status_code
                    if code == 429:
                        try: delay = max(1, min(86400, float(response.headers.get('Retry-After', '60'))))
                        except (ValueError, TypeError): delay = 60
                        self.retry_until = self.clock() + delay
                except Exception: code = 0
                blocked = {401: 'revoked', 403: 'forbidden_or_quota', 429: 'rate_limited'}
                self.cache = Snapshot(blocked[code]) if code in blocked else await self._read()
                notice = 'Command accepted; state refreshed' if code == 204 else 'Command uncertain' if code == 0 else 'Command rejected; refresh'
                return self._scene(device_id, self.cache, notice)
            if action != 'music': raise ValueError('Invalid music action')
            # No automatic/background polling. Explicit visible-scene reads are bounded.
            if self.clock() - self.last_read >= 15:
                self.cache = await self._read()
            return self._scene(device_id, self.cache)
