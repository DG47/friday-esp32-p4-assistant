"""Disabled, provider-independent timecoded engine. Original synthetic QA only."""
from bisect import bisect_right
from dataclasses import dataclass
import math
import time
from .lyrics import TrackIdentity
from ..models import Surface

@dataclass(frozen=True)
class PlaybackObservation:
    sequence: int
    identity: TrackIdentity | None
    position_ms: int
    duration_ms: int
    playing: bool
    observed_at: float
    status: str = 'ready'
    item_kind: str = 'track'

@dataclass(frozen=True)
class TimedLine:
    start_ms: int
    text: str
    end_ms: int | None = None

@dataclass(frozen=True)
class TimedDocument:
    identity: TrackIdentity
    duration_ms: int
    lines: tuple[TimedLine, ...]
    source: str
    attribution: str
    offset_ms: int = 0

@dataclass(frozen=True)
class LoadTicket:
    generation: int
    request: int
    identity: TrackIdentity

class LyricsSyncEngine:
    def __init__(self, enabled=False, clock=time.monotonic, stale_after=5, duration_tolerance_ms=2000):
        if not isinstance(stale_after,(int,float)) or not math.isfinite(stale_after) or not 1<=stale_after<=30:
            raise ValueError('Invalid freshness window')
        if type(duration_tolerance_ms) is not int or not 0<=duration_tolerance_ms<=2000:raise ValueError('Invalid duration tolerance')
        self.duration_tolerance_ms=duration_tolerance_ms
        self.enabled=enabled;self.clock=clock;self.stale_after=stale_after
        self.observation=None;self.document=None;self.generation=0;self.request=0
        self.sequence=-1;self.status='disabled' if not enabled else 'unavailable';self.offset_ms=0

    def observe(self,observation):
        if not self.enabled:return False
        if not isinstance(observation,PlaybackObservation) or type(observation.sequence) is not int:return False
        if observation.sequence<=self.sequence:return False
        self.sequence=observation.sequence
        previous=self.observation.identity if self.observation else None
        valid=(observation.status=='ready' and observation.item_kind=='track'
            and isinstance(observation.identity,TrackIdentity) and bool(observation.identity.version)
            and bool(observation.identity.track_id or observation.identity.isrc)
            and type(observation.position_ms) is int and type(observation.duration_ms) is int
            and 0<=observation.position_ms<=observation.duration_ms<=86400000 and observation.duration_ms>0
            and type(observation.playing) is bool
            and isinstance(observation.observed_at,(int,float)) and math.isfinite(observation.observed_at)
            and observation.observed_at<=self.clock())
        if not valid or previous!=observation.identity:
            self.generation+=1;self.request+=1;self.document=None;self.offset_ms=0
        self.observation=observation if valid else None
        if not valid:self.status='unavailable'
        elif self.document is None and previous!=observation.identity:self.status='no_lyrics'
        # Seek/pause/resume require a new observation; no monotonic-position assumption.
        return valid

    def begin_load(self):
        if not self.enabled or self.observation is None:return None
        self.request+=1;self.document=None;self.status='loading'
        return LoadTicket(self.generation,self.request,self.observation.identity)

    def is_current(self,ticket):
        current=self.observation
        return (self.enabled and current is not None and isinstance(ticket,LoadTicket)
            and ticket.generation==self.generation and ticket.request==self.request
            and ticket.identity==current.identity)

    def fail(self,ticket,status='no_synced_lyrics'):
        if not self.is_current(ticket):return False
        self.request+=1;self.document=None
        self.status=status if status in {'disabled','unconfigured','unmatched','backoff','rate_limited','unavailable','no_synced_lyrics','invalid_timing'} else 'unavailable'
        return True

    def apply(self,ticket,document):
        current=self.observation
        if not self.is_current(ticket):return False
        self.request+=1  # A request result is consumed once, including empty/error results.
        self.document=None
        if document is None:self.status='no_synced_lyrics';return False
        if (not isinstance(document,TimedDocument) or not current.identity.matches(document.identity)
            or type(document.duration_ms) is not int or not 0<document.duration_ms<=86400000 or abs(document.duration_ms-current.duration_ms)>self.duration_tolerance_ms
            or type(document.offset_ms) is not int or abs(document.offset_ms)>10000
            or not isinstance(document.source,str) or not 1<=len(document.source.strip())<=120
            or not isinstance(document.attribution,str) or not 1<=len(document.attribution.strip())<=240
            or not isinstance(document.lines,tuple) or not 1<=len(document.lines)<=2000):
            self.status='mismatch';return False
        previous=-1
        for index,line in enumerate(document.lines):
            if (not isinstance(line,TimedLine) or type(line.start_ms) is not int
                or not previous<line.start_ms<document.duration_ms
                or not isinstance(line.text,str) or not 1<=len(line.text.strip())<=500):
                self.status='invalid_timing';return False
            next_start=document.lines[index+1].start_ms if index+1<len(document.lines) and isinstance(document.lines[index+1],TimedLine) else document.duration_ms
            if type(next_start) is not int:
                self.status='invalid_timing';return False
            if line.end_ms is not None and (type(line.end_ms) is not int or not line.start_ms<line.end_ms<=next_start):
                self.status='invalid_timing';return False
            previous=line.start_ms
        self.document=document;self.status='timecoded';return True

    def set_offset(self,offset_ms):
        if type(offset_ms) is not int or abs(offset_ms)>10000:raise ValueError('Offset outside supported range')
        self.offset_ms=offset_ms

    def current(self):
        observation=self.observation
        empty={'status':self.status,'mode':'timecoded','live_use_approved':False,'line':None,'line_index':None}
        if not self.enabled or observation is None:return empty
        age=self.clock()-observation.observed_at
        if age<0 or age>self.stale_after:return dict(empty,status='stale')
        if self.document is None:return empty
        position=min(observation.duration_ms,observation.position_ms+(int(age*1000) if observation.playing else 0))
        payload=dict(empty,status='playing' if observation.playing else 'paused',
            track_id=observation.identity.track_id,isrc=observation.identity.isrc,version=observation.identity.version,
            position_ms=position,source=self.document.source,attribution=self.document.attribution)
        if position>=observation.duration_ms:return dict(payload,status='ended')
        effective=position+self.document.offset_ms+self.offset_ms
        lines=self.document.lines
        index=bisect_right([line.start_ms for line in lines],effective)-1
        if index<0:return dict(payload,status='waiting')
        line=lines[index]
        end=line.end_ms if line.end_ms is not None else lines[index+1].start_ms if index+1<len(lines) else self.document.duration_ms
        if effective>=end:return dict(payload,status='waiting')
        return dict(payload,line=line.text,line_index=index)

    def synthetic_surface(self):
        # Explicit QA surface only; this is never routed into the live MusicService.
        current=self.current()
        return Surface(kind='media',title='Original timing fixture',
            subtitle=current.get('line') or 'No current line',
            items=[{'time':'Mode','label':'Synthetic timecoded preview'},
                {'time':'State','label':current['status']},
                {'time':'Source','label':current.get('source','Original QA text only')},
                {'time':'Attribution','label':current.get('attribution','No live rights/source configured')}],
            actions=[{'id':'dismiss','label':'Dismiss'}],expires_ms=5000)
