"""Opt-in, injected LRCLIB GET adapter. No default client or real calls in tests."""
import asyncio
from dataclasses import dataclass
import json
import math
import re
import time
import unicodedata
import httpx
from .lyrics import TrackIdentity
from .lyrics_sync import TimedDocument, TimedLine

@dataclass(frozen=True)
class LyricQuery:
    identity: TrackIdentity
    track_name: str
    artist_name: str
    album_name: str
    duration_ms: int

@dataclass(frozen=True)
class TimedFetch:
    status: str
    document: TimedDocument | None = None

class LRCInvalid(ValueError):pass
STAMP=re.compile(r'\[(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?\]')
OFFSET=re.compile(r'^\[offset:([+-]?\d+)\]$',re.I)
META=re.compile(r'^\[(?:ar|ti|al|by|re|ve|length|id):[^\]]*\]$',re.I)

def parse_lrc(value,duration_ms):
    if not isinstance(value,str) or not 1<=len(value)<=100000:raise LRCInvalid('Invalid LRC')
    events={};offset=None
    for raw in value.splitlines():
        raw=raw.strip('\ufeff\r\n ')
        if not raw:continue
        setting=OFFSET.fullmatch(raw)
        if setting:
            number=int(setting.group(1))
            if abs(number)>10000 or offset is not None and offset!=number:raise LRCInvalid('Invalid offset')
            offset=number;continue
        if META.fullmatch(raw):continue
        position=0;times=[]
        while match:=STAMP.match(raw,position):
            minute,second,fraction=match.groups()
            timestamp=int(minute)*60000+int(second)*1000+int((fraction or '').ljust(3,'0') or '0')
            if timestamp>=duration_ms:raise LRCInvalid('Timestamp outside recording')
            times.append(timestamp);position=match.end()
        if not times:raise LRCInvalid('Missing timestamps')
        text=raw[position:]
        text=re.sub(r'<\d{1,3}:[0-5]\d(?:\.\d{1,3})?>','',text)
        text=''.join(c for c in text if c.isprintable() and c not in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069').strip()
        if len(text)>500:raise LRCInvalid('Line too long')
        for timestamp in times:
            group=events.setdefault(timestamp,[])
            if text not in group:group.append(text)
        if len(events)>2000:raise LRCInvalid('Too many lines')
    points=sorted(events);lines=[]
    for index,start in enumerate(points):
        text='\n'.join(s for s in events[start] if s)
        if not text:continue  # Blank timestamp ends the prior line and creates a gap.
        if len(text)>500:raise LRCInvalid('Combined line too long')
        end=points[index+1] if index+1<len(points) else duration_ms
        lines.append(TimedLine(start,text,end))
    if not lines:raise LRCInvalid('No timed lines')
    return tuple(lines),offset or 0


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC',value).split()).casefold() if isinstance(value,str) else None

def valid_query(query):
    return (isinstance(query,LyricQuery) and isinstance(query.identity,TrackIdentity)
        and bool(query.identity.version) and bool(query.identity.track_id or query.identity.isrc)
        and all(isinstance(v,str) and 1<=len(v.strip())<=1024 for v in [query.track_name,query.artist_name,query.album_name])
        and type(query.duration_ms) is int and 0<query.duration_ms<=86400000)

class LRCLIBAdapter:
    def __init__(self,client=None,user_agent=None,enabled=False,clock=time.monotonic):
        self.client=client;self.user_agent=user_agent;self.enabled=enabled;self.clock=clock
        self.lock=asyncio.Lock();self.cache={};self.retry_until=0

    async def fetch(self,query):
        if not self.enabled:return TimedFetch('disabled')
        if self.client is None or not isinstance(self.user_agent,str) or not 1<=len(self.user_agent)<=256 or any(ord(c)<32 or ord(c)>126 for c in self.user_agent):return TimedFetch('unconfigured')
        if not valid_query(query):return TimedFetch('unmatched')
        key=(query.identity,query.track_name,query.artist_name,query.album_name,query.duration_ms)
        async with self.lock:
            if key in self.cache:return self.cache[key]
            if self.clock()<self.retry_until:return TimedFetch('backoff')
            try:
                async with asyncio.timeout(10):
                    async with self.client.stream('GET','https://lrclib.net/api/get',
                        params={'track_name':query.track_name,'artist_name':query.artist_name,'album_name':query.album_name,'duration':query.duration_ms/1000},
                        headers={'User-Agent':self.user_agent,'Accept':'application/json'},timeout=8,follow_redirects=False) as response:
                        body=bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body)>262144:raise LRCInvalid('Oversized response')
                        if response.status_code==429:
                            try:delay=float(response.headers.get('Retry-After','60'))
                            except (ValueError,TypeError):delay=60
                            if not math.isfinite(delay) or delay<0:delay=60
                            self.retry_until=self.clock()+max(1,min(86400,delay));return TimedFetch('rate_limited')
                        if response.status_code==404:result=TimedFetch('no_synced_lyrics')
                        elif response.status_code!=200:
                            self.retry_until=self.clock()+30;return TimedFetch('unavailable')
                        else:result=self._parse(query,json.loads(body))
            except (httpx.HTTPError,TimeoutError,ValueError,UnicodeError,TypeError):
                self.retry_until=self.clock()+30;return TimedFetch('unavailable')
            self.cache[key]=result
            if len(self.cache)>32:self.cache.pop(next(iter(self.cache)))
            return result

    def _parse(self,query,data):
        if not isinstance(data,dict):return TimedFetch('unmatched')
        for key,expected in [('trackName',query.track_name),('artistName',query.artist_name),('albumName',query.album_name)]:
            if normalized(data.get(key))!=normalized(expected):return TimedFetch('unmatched')
        duration=data.get('duration')
        if type(duration) not in {int,float} or not math.isfinite(duration) or duration<=0 or abs(duration*1000-query.duration_ms)>2000:return TimedFetch('unmatched')
        if data.get('instrumental') is True or not data.get('syncedLyrics'):return TimedFetch('no_synced_lyrics')
        try:lines,offset=parse_lrc(data['syncedLyrics'],int(duration*1000))
        except (LRCInvalid,TypeError):return TimedFetch('invalid_timing')
        record=data.get('id')
        if type(record) is not int or record<1:return TimedFetch('unmatched')
        document=TimedDocument(query.identity,int(duration*1000),lines,'LRCLIB',f'LRCLIB record {record}; metadata matched; rights unverified',offset)
        return TimedFetch('timecoded',document)
