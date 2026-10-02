"""Recording-bound future lyrics interface. No source, scraping or timing enabled."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class TrackIdentity:
    track_id: str
    isrc: str
    version: str

    def matches(self, other):
        if not isinstance(other,TrackIdentity) or self.version!=other.version:return False
        if other.track_id:return self.track_id==other.track_id and (not other.isrc or other.isrc==self.isrc)
        return bool(self.isrc and other.isrc==self.isrc)

@dataclass(frozen=True)
class LyricsDocument:
    identity: TrackIdentity
    source: str
    attribution: str
    text: str
    mode: str = 'manual'

class LyricsProvider(Protocol):
    async def lyrics(self, identity: TrackIdentity) -> LyricsDocument | None: ...

class UnavailableLyrics:
    async def lyrics(self, identity):return None

class LyricsBinding:
    def __init__(self,provider=None):
        self.provider=provider or UnavailableLyrics()
        self.identity=None;self.document=None
        self.generation=0;self.request=0;self.observed=float('-inf')
        self.status='no_source';self.paused=False

    def observe(self,snapshot):
        # MusicService serializes observations; reject demonstrably older fresh snapshots.
        eligible=snapshot.status in {'ready','sample'} and bool(snapshot.track and snapshot.version)
        if eligible and snapshot.observed<self.observed:return
        if eligible:self.observed=snapshot.observed
        identity=TrackIdentity(snapshot.track,snapshot.isrc,snapshot.version) if eligible else None
        if identity!=self.identity or not eligible:
            self.generation+=1;self.request+=1;self.document=None
            self.identity=identity
            self.status='no_source' if isinstance(self.provider,UnavailableLyrics) else 'unavailable'
        self.paused=eligible and not snapshot.playing
        # Manual text has no timed cursor. Pause/seek never infer a current lyric line.

    async def load_current(self):
        identity=self.identity
        if identity is None:return False
        generation=self.generation
        self.request+=1;request=self.request
        self.document=None;self.status='loading'
        failed=False
        try:result=await self.provider.lyrics(identity)
        except asyncio.CancelledError:
            if generation==self.generation and request==self.request:
                self.status='unavailable'
            raise
        except Exception:result=None;failed=True
        if generation!=self.generation or request!=self.request or identity!=self.identity:return False
        if result is None:
            self.status='unavailable' if failed else 'no_source' if isinstance(self.provider,UnavailableLyrics) else 'no_lyrics'
            return False
        if (not isinstance(result,LyricsDocument) or not identity.matches(result.identity)
            or not isinstance(result.source,str) or not 1<=len(result.source.strip())<=120
            or not isinstance(result.attribution,str) or not 1<=len(result.attribution.strip())<=240
            or not isinstance(result.text,str) or not 1<=len(result.text.strip())<=16000):
            self.status='unmatched';return False
        if result.mode!='manual':
            self.status='timing_disabled';return False
        self.document=result;self.status='manual';return True

    def label(self):
        if self.status=='manual':return 'Matched manual text; not time-synced'
        return {'no_source':'No authorized source connected','loading':'Checking recording match',
            'no_lyrics':'No lyrics for this recording','unmatched':'Recording mismatch; lyrics hidden',
            'timing_disabled':'Timed lyrics disabled','unavailable':'Lyrics unavailable'}.get(self.status,'Lyrics unavailable')

    def payload(self):
        # Local structured contract only; never added to a model/history/job.
        identity=self.identity
        return {'status':self.status,'track_id':identity.track_id if identity else None,
            'isrc':identity.isrc if identity else None,'version':identity.version if identity else None,
            'mode':'manual' if self.document else None,'synchronized':False,
            'paused':self.paused,'source':self.document.source if self.document else None,
            'attribution':self.document.attribution if self.document else None,
            'text':self.document.text if self.document else None}
