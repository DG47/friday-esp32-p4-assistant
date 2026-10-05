"""Explicit synthetic session wiring. No automatic poller or live-service integration."""
import asyncio
from .lrclib import LyricQuery

class TimedLyricsSession:
    def __init__(self,engine,provider,reanchor=None):
        self.engine=engine;self.provider=provider;self.reanchor=reanchor
        self.task=None;self.last_identity=None;self.retry_at=0

    def observe(self,observation,query=None):
        previous=self.engine.observation.identity if self.engine.observation else None
        accepted=self.engine.observe(observation)
        if not accepted:
            if self.engine.observation is None:
                self.last_identity=None
                if self.task and not self.task.done():self.task.cancel()
            return False
        identity=self.engine.observation.identity
        if previous!=identity:self.last_identity=None
        if identity==self.last_identity and not (self.engine.document is None and self.engine.status in {'unavailable','backoff','rate_limited'} and self.engine.clock()>=self.retry_at):return True  # Pause/seek reads never refetch lyrics.
        if self.task and not self.task.done():self.task.cancel()
        if (not isinstance(query,LyricQuery) or query.identity!=identity
            or query.duration_ms!=observation.duration_ms):return True
        self.last_identity=identity
        ticket=self.engine.begin_load()
        self.task=asyncio.create_task(self._fetch(ticket,query))
        return True

    async def _fetch(self,ticket,query):
        try:
            result=await self.provider.fetch(query)
            if not self.engine.is_current(ticket):return
            # A fresh read after fetch avoids using fetch-start playback position.
            if result.document is not None and self.reanchor is not None:
                fresh=await self.reanchor()
                if fresh is not None:self.engine.observe(fresh)
            if not self.engine.is_current(ticket):return
            if result.document is None:
                self.retry_at=self.engine.clock()+30
                self.engine.fail(ticket,result.status)
            else:self.engine.apply(ticket,result.document)
        except asyncio.CancelledError:
            self.engine.fail(ticket,'unavailable')
            raise
        except Exception:
            self.engine.fail(ticket,'unavailable')

    async def close(self):
        if self.task and not self.task.done():self.task.cancel()
        if self.task:
            try:await self.task
            except asyncio.CancelledError:pass
