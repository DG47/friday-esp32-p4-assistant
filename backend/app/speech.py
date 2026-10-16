"""Opt-in synthesized speech; no credentials or provider calls in default mode."""
import asyncio
import base64
import logging
import uuid

from openai import AsyncOpenAI

MAX_PCM_BYTES = 24000 * 2 * 30
logger = logging.getLogger("uvicorn.error")


class OpenAISpeech:
    def __init__(self, key, model, voice):
        self.client = AsyncOpenAI(api_key=key, timeout=30, max_retries=0)
        self.model, self.voice = model, voice

    async def synthesize(self, text):
        output = bytearray()
        async with self.client.audio.speech.with_streaming_response.create(
            model=self.model, voice=self.voice, input=text[:1000], response_format="pcm"
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4800):
                output.extend(chunk)
                if len(output) > MAX_PCM_BYTES:
                    raise ValueError("Speech exceeds 30 second playback limit")
        if not output or len(output) % 2:
            raise ValueError("Provider returned incomplete PCM")
        return bytes(output)

    async def close(self):
        await self.client.close()


class SpeechDirector:
    def __init__(self, hub, provider=None):
        self.hub, self.provider = hub, provider
        self.tasks = {}
        self.waiting = {}
        self.latest = {}
        self.failed_streams = set()
        self.synthesis_slots = asyncio.Semaphore(4)
        self.control_locks = {}
        self.generations = {}
        self.retired = set()
        self.cancel_grace = .25

    def control_lock(self, device_id):
        # Serialize cancel/replacement so an old cancellation cannot stop a new stream.
        return self.control_locks.setdefault(device_id, asyncio.Lock())

    async def cancel(self, device_id):
        async with self.control_lock(device_id):
            return await self._cancel_locked(device_id)

    async def _cancel_locked(self, device_id):
        self.generations[device_id] = self.generations.get(device_id, 0) + 1
        task = self.tasks.pop(device_id, None)
        drained = True
        if task:
            self.retired.add(task)
            def finished(old):
                self.retired.discard(old)
                if not old.cancelled():old.exception()
            task.add_done_callback(finished)
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=self.cancel_grace)
            drained = bool(done)
        self.waiting.pop(device_id, None)
        await self.hub.broadcast(device_id, {"type": "speech.cancel"}, required_capability="pcm_speech")
        return {"producer_revoked": True, "handler_drained": drained}

    async def speak(self, device_id, text, job_id=None):
        async with self.control_lock(device_id):
            if not self.presentation_current(device_id):
                return
            if device_id in self.latest and self.latest[device_id] != job_id:
                return
            await self._cancel_locked(device_id)
            if device_id in self.latest and self.latest[device_id] != job_id:
                return
            if self.provider and self.hub.has_capability(device_id, "pcm_speech"):
                self.tasks[device_id] = asyncio.create_task(self._play(device_id, text, self.generations[device_id]))
            else:
                await self.hub.broadcast(device_id, {"type": "state", "state": "idle"}, required_capability="pcm_speech")


    def finished(self, device_id, stream_id, status="completed"):
        entry = self.waiting.get(device_id)
        if entry and entry[0] == stream_id and isinstance(status, str) and status in {"completed", "failed"}:
            if status == "failed":
                self.failed_streams.add(stream_id)
            entry[1].set()
            logger.info("speech_playback_ack device=%s status=%s", device_id, status)

    async def _play(self, device_id, text, generation):
        owner = asyncio.current_task()
        def current():
            return self.tasks.get(device_id) is owner and self.generations.get(device_id) == generation and self.presentation_current(device_id)
        stream_id = uuid.uuid4().hex
        try:
            async with self.synthesis_slots:
                pcm = await asyncio.wait_for(self.provider.synthesize(text), 35)
            if not current():
                return
            if not pcm or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
                raise ValueError("Invalid synthesized PCM")
            logger.info("speech_playback_start device=%s bytes=%d sample_rate=24000", device_id, len(pcm))
            done = asyncio.Event()
            self.waiting[device_id] = (stream_id, done)
            async def send(kind, **fields):
                return await self.hub.broadcast(device_id, {"type": kind, "stream_id": stream_id, **fields}, required_capability="pcm_speech", allowed=current)
            if not await send("speech.start", sample_rate=24000, format="pcm_s16le", channels=1, bytes=len(pcm)):
                return
            for sequence, offset in enumerate(range(0, len(pcm), 4800)):
                if not await send("speech.chunk", sequence=sequence, pcm=base64.b64encode(pcm[offset:offset + 4800]).decode("ascii")):
                    return
            await send("speech.end")
            await self.hub.broadcast(device_id, {"type": "state", "state": "speaking"}, required_capability="pcm_speech", allowed=current)
            await asyncio.wait_for(done.wait(), len(pcm) / 48000 + 5)
            if stream_id in self.failed_streams:
                raise ValueError("Device could not play audio")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never include provider exception bodies (may contain credential/request data).
            logger.warning("Device speech failed for %s", device_id)
            await self.hub.broadcast(device_id, {"type": "speech.cancel"}, required_capability="pcm_speech", allowed=current)
            await self.hub.broadcast(device_id, {"type": "error", "code": "speech_unavailable", "message": "Speech playback failed; the written response is available."}, required_capability="pcm_speech", allowed=current)
        finally:
            self.failed_streams.discard(stream_id)
            entry = self.waiting.get(device_id)
            if entry and entry[0] == stream_id:
                self.waiting.pop(device_id, None)
            if not asyncio.current_task().cancelling() and current():
                await self.hub.broadcast(device_id, {"type": "state", "state": "idle"}, required_capability="pcm_speech", allowed=current)
            if self.tasks.get(device_id) is owner:
                self.tasks.pop(device_id, None)

    def presentation_current(self, device_id):
        experience = getattr(self.hub, "experience", None)
        return not experience or not self.hub.has_capability(device_id, "experience_context_v1") or experience.origin.get() is not None and experience.current.get(device_id) == experience.origin.get()

    async def close(self):
        for device_id in list(self.tasks):
            await self.cancel(device_id)
        if self.retired:
            await asyncio.gather(*(asyncio.shield(task) for task in list(self.retired)), return_exceptions=True)
        if self.provider:
            await self.provider.close()
