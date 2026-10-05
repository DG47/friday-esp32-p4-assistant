"""Read-only Mac playback candidate. Never runs AppleScript unless explicitly injected/enabled."""
import asyncio
from dataclasses import dataclass
import json
import math
import re
import time
from .lyrics import TrackIdentity
from .lyrics_sync import PlaybackObservation
from .lrclib import LyricQuery

READ_SCRIPT='''use framework "Foundation"
use scripting additions
if application "Spotify" is not running then return "{\\"status\\":\\"not_running\\"}"
tell application "Spotify"
    set stateValue to player state as text
    if stateValue is "stopped" then return "{\\"status\\":\\"no_playback\\"}"
    set trackValue to current track
    set beforeId to id of trackValue
    set nameValue to name of trackValue
    set artistValue to artist of trackValue
    set albumValue to album of trackValue
    set durationValue to duration of trackValue
    set positionValue to player position
    set versionValue to version
    if beforeId is not id of current track then return "{\\"status\\":\\"changed\\"}"
end tell
set payload to current application's NSMutableDictionary's dictionary()
payload's setObject:beforeId forKey:"id"
payload's setObject:nameValue forKey:"name"
payload's setObject:artistValue forKey:"artist"
payload's setObject:albumValue forKey:"album"
payload's setObject:durationValue forKey:"duration_raw"
payload's setObject:positionValue forKey:"position_seconds"
payload's setObject:stateValue forKey:"state"
payload's setObject:versionValue forKey:"app_version"
set encoded to current application's NSJSONSerialization's dataWithJSONObject:payload options:0 |error|:(missing value)
return (current application's NSString's alloc()'s initWithData:encoded encoding:(current application's NSUTF8StringEncoding)) as text
'''

class LocalPlaybackUnavailable(RuntimeError):
    def __init__(self):super().__init__('Local Spotify playback unavailable')

class ApprovedOsaRunner:
    def __init__(self,approved=False):self.approved=approved
    async def __call__(self,script):
        if not self.approved:raise LocalPlaybackUnavailable()
        process=None
        try:
            async with asyncio.timeout(3):
                process=await asyncio.create_subprocess_exec('/usr/bin/osascript','-e',script,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
                body=bytearray()
                while chunk:=await process.stdout.read(4096):
                    body.extend(chunk)
                    if len(body)>16384:raise LocalPlaybackUnavailable()
                await process.wait()
                if process.returncode:raise LocalPlaybackUnavailable()
                return bytes(body)
        except (OSError,TimeoutError):raise LocalPlaybackUnavailable() from None
        finally:
            if process is not None and process.returncode is None:
                process.kill();await process.wait()

@dataclass(frozen=True)
class LocalRead:
    status: str
    observation: PlaybackObservation | None = None
    query: LyricQuery | None = None

class MacSpotifyAdapter:
    def __init__(self,runner=None,enabled=False,verified_version=None,duration_unit=None,clock=time.monotonic,timeout=3):
        self.runner=runner;self.enabled=enabled;self.verified_version=verified_version
        self.duration_unit=duration_unit;self.clock=clock;self.timeout=timeout
        self.sequence=0;self.lock=asyncio.Lock()

    async def read(self):
        if not self.enabled:return LocalRead('disabled')
        if self.runner is None or not self.verified_version or self.duration_unit not in {'seconds','milliseconds'}:return LocalRead('dictionary_or_units_unverified')
        async with self.lock:
            self.sequence+=1;started=self.clock()
            try:
                async with asyncio.timeout(self.timeout):raw=await self.runner(READ_SCRIPT)
                if not isinstance(raw,(str,bytes)) or len(raw)>16384:return LocalRead('invalid_output')
                data=json.loads(raw)
                if not isinstance(data,dict):return LocalRead('invalid_output')
                if data.get('status'):return LocalRead(data['status'] if data['status'] in {'not_running','no_playback','changed'} else 'unavailable')
                if data.get('app_version')!=self.verified_version:return LocalRead('dictionary_unverified')
                identifier=data.get('id')
                if not isinstance(identifier,str) or not re.fullmatch(r'spotify:track:[A-Za-z0-9]{1,128}',identifier):return LocalRead('unsupported')
                fields=[data.get(key) for key in ['name','artist','album']]
                if not all(isinstance(value,str) and 1<=len(value)<=1024 and all(c.isprintable() for c in value) for value in fields):return LocalRead('invalid_output')
                raw_duration=data.get('duration_raw');raw_position=data.get('position_seconds');state=data.get('state')
                if type(raw_duration) not in {int,float} or type(raw_position) not in {int,float} or not math.isfinite(raw_duration) or not math.isfinite(raw_position) or state not in {'playing','paused'}:return LocalRead('invalid_output')
                duration=int(raw_duration*(1000 if self.duration_unit=='seconds' else 1));position=int(raw_position*1000)
                if not 0<=position<=duration<=86400000 or duration<=0:return LocalRead('invalid_output')
                track=identifier.split(':')[-1];version=fields[0]+' | '+fields[2]+' | '+str(duration)+'ms'
                identity=TrackIdentity(track,'',version)
                observed=(started+self.clock())/2
                observation=PlaybackObservation(self.sequence,identity,position,duration,state=='playing',observed)
                return LocalRead('ready',observation,LyricQuery(identity,*fields,duration))
            except (TimeoutError,ValueError,TypeError,LocalPlaybackUnavailable,OSError):return LocalRead('unavailable')
