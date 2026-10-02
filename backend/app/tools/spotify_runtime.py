"""Official opt-in account-wide playback, owned visible scenes only."""
import contextvars
from contextlib import contextmanager
import logging
from types import SimpleNamespace
import httpx
from ..spotify_auth import TokenManager,AuthUnavailable
from .music import MusicService
from .music_runtime import LocalMusicRuntime
from .lrclib import LRCLIBAdapter
from .artwork import ArtworkProvider

_private=contextvars.ContextVar('friday_private_transport',default=False)
class PrivateTransportFilter(logging.Filter):
    def filter(self,record):return not _private.get()
@contextmanager
def private_transport():
    # HTTPX logs complete LRCLIB query URLs at INFO. Suppress only this task's
    # private requests; unrelated model/calendar logging remains unchanged.
    for name in ('httpx','httpcore','httpcore.http11','httpcore.http2','httpcore.connection','httpcore.proxy'):
        logger=logging.getLogger(name)
        if not any(isinstance(f,PrivateTransportFilter) for f in logger.filters):logger.addFilter(PrivateTransportFilter())
    token=_private.set(True)
    try:yield
    finally:_private.reset(token)

class AuthorizedTransport:
    def __init__(self,client,manager):self.client=client;self.manager=manager
    async def request(self,method,path):
        if method not in {'GET','PUT','POST'} or not path.startswith('/me/player'):raise ValueError('Unsupported Spotify operation')
        with private_transport():
            try:token=await self.manager.token()
            except AuthUnavailable as error:
                return httpx.Response(429 if error.reason=='backoff' else 401,headers={'Retry-After':'30'})
            response=await self.client.request(method,'https://api.spotify.com/v1'+path,
                headers={'Authorization':'Bearer '+token},timeout=8,follow_redirects=False)
            if response.status_code==401:self.manager.clear();self.manager.blocked='revoked'
            return response

class WebPlaybackSource:
    def __init__(self,music):self.music=music;self.sequence=0
    async def read(self):
        async with self.music.lock:
            self.music.cache=await self.music._read()
            self.sequence+=1
            observation,query=self.music.cache.timed_context(self.sequence)
            return SimpleNamespace(status=self.music.cache.status,observation=observation,query=query)

class PrivateLyricsProvider:
    def __init__(self,provider):self.provider=provider;self.cache=provider.cache
    def clear(self):
        if hasattr(self.provider,"clear"):self.provider.clear()
        else:self.cache.clear()
    async def fetch(self,query):
        with private_transport():return await self.provider.fetch(query)

def create_runtime(settings,hub,blocked=None):
    if settings.music_provider!='spotify':return LocalMusicRuntime(hub,blocked=blocked),None
    client=httpx.AsyncClient(timeout=8,follow_redirects=False)
    manager=TokenManager(settings.music_credentials_path,client)
    music=MusicService('spotify',AuthorizedTransport(client,manager))
    provider=None
    if settings.music_timed_display_enabled:
        provider=PrivateLyricsProvider(LRCLIBAdapter(client=client,user_agent=settings.lyrics_client_identifier,enabled=True))
    artwork=None;artwork_client=None
    if settings.music_artwork_enabled:
        artwork_client=httpx.AsyncClient(timeout=6,follow_redirects=False,trust_env=False)
        artwork=PrivateLyricsProvider(ArtworkProvider(artwork_client,enabled=True))
    runtime=LocalMusicRuntime(hub,WebPlaybackSource(music),provider,enabled=True,
        timing=settings.music_timed_display_enabled,poll_seconds=settings.music_poll_seconds,
        blocked=blocked,owned_client=client,music=music,artwork=artwork,owned_artwork_client=artwork_client)
    return runtime,music
