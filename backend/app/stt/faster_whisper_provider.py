from __future__ import annotations

import asyncio
import tempfile
import wave
from pathlib import Path

from .base import AudioFormat, TranscriptionError
from .diagnostics import FailureReason, Phase, measure, phase


class FasterWhisperTranscriber:
    name = "faster-whisper"

    def __init__(self, model_name: str, device: str, compute_type: str, language: str):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language or None
        self._model = None
        self._model_lock = asyncio.Lock()
        self._model_task = None
        self._worker_lock = asyncio.Lock()
        self._workers = set()

    async def transcribe(self, pcm: bytes, audio_format: AudioFormat) -> str:
        if audio_format.encoding != "pcm_s16le":
            raise TranscriptionError(f"Unsupported audio encoding: {audio_format.encoding}")
        if audio_format.channels != 1:
            raise TranscriptionError("Local transcription currently requires mono PCM")

        failure_reason = FailureReason.MODEL_LOAD
        try:
            with measure(Phase.MODEL_LOAD):
                model = await self._get_model()
            failure_reason = FailureReason.STT_ERROR
            phase(Phase.WORKER_WAIT)
            return await self._run_worker(self._transcribe_sync, model, pcm, audio_format)
        except TranscriptionError as error:
            if error.reason is None:
                error.reason = failure_reason
            raise
        except Exception as error:
            raise TranscriptionError(f"Could not load the local Whisper model: {error}", reason=failure_reason) from error

    async def transcribe_wake(self, pcm: bytes, audio_format: AudioFormat) -> str:
        if audio_format.encoding != "pcm_s16le" or audio_format.channels != 1:
            raise TranscriptionError("Wake detection requires mono pcm_s16le audio")
        try:
            model = await self._get_model()
            return await self._run_worker(
                self._transcribe_sync,
                model,
                pcm,
                audio_format,
                "Friday",
                "The wake word is Friday.",
                False,
                1,
            )
        except TranscriptionError:
            raise
        except Exception as error:
            raise TranscriptionError(f"Local wake detection failed: {error}") from error

    def _worker_finished(self, task):
        self._workers.discard(task)
        # An abandoned worker's exception must be retrieved without exposing its
        # text. Active callers still receive the same exception from their await.
        if not task.cancelled():
            task.exception()

    async def _await_owned(self, task):
        # wait() does not cancel its child when this waiter is canceled. Unlike
        # shield() on Python 3.14, it also does not send a late private exception
        # to the loop exception handler after an abandoned waiter.
        await asyncio.wait({task})
        return task.result()

    async def _run_worker(self, function, *args, **kwargs):
        started = False

        async def run():
            nonlocal started
            async with self._worker_lock:
                started = True
                return await asyncio.to_thread(function, *args, **kwargs)

        task = asyncio.create_task(run())
        self._workers.add(task)
        task.add_done_callback(self._worker_finished)
        try:
            return await self._await_owned(task)
        except asyncio.CancelledError:
            if not started:
                # Queued work whose caller expired must never consume the model.
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # A running thread cannot be stopped by Task.cancel(). Its owner
            # keeps the permit until it actually exits, while this caller returns.
            raise

    async def _get_model(self):
        if self._model is not None:
            return self._model
        if self._model_task is None or self._model_task.done():
            self._model_task = asyncio.create_task(self._load_model())
            self._model_task.add_done_callback(self._worker_finished)
        # Cold construction has an owner too: canceled waiters cannot abandon
        # the cached result or allow another constructor to race the first.
        return await self._await_owned(self._model_task)

    async def _load_model(self):
        async with self._model_lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as error:
                    raise TranscriptionError(
                        "faster-whisper is not installed; install backend requirements"
                    ) from error
                self._model = await self._run_worker(
                    WhisperModel,
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=2,
                    num_workers=1,
                )
        return self._model

    def _transcribe_sync(
        self,
        model,
        pcm: bytes,
        audio_format: AudioFormat,
        hotwords: str | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
        beam_size: int = 5,
    ) -> str:
        path: Path | None = None
        failure_reason = FailureReason.INPUT_PREPARE
        try:
            with measure(Phase.INPUT_PREPARE):
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
                    path = Path(audio_file.name)
                with wave.open(str(path), "wb") as wav_file:
                    wav_file.setnchannels(audio_format.channels)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(audio_format.sample_rate)
                    wav_file.writeframes(pcm)

            failure_reason = FailureReason.DECODE
            with measure(Phase.DECODE):
                segments, _ = model.transcribe(
                    str(path),
                    language=self.language,
                    beam_size=beam_size,
                    condition_on_previous_text=False,
                    vad_filter=vad_filter,
                    hotwords=hotwords,
                    initial_prompt=initial_prompt,
                )
                return " ".join(segment.text.strip() for segment in segments).strip()
        except TranscriptionError as error:
            if error.reason is None:
                error.reason = failure_reason
            raise
        except Exception as error:
            raise TranscriptionError(f"Local transcription failed: {error}", reason=failure_reason) from error
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
