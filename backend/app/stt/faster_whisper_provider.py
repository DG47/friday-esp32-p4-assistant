from __future__ import annotations

import asyncio
import tempfile
import wave
from pathlib import Path

from .base import AudioFormat, TranscriptionError


class FasterWhisperTranscriber:
    name = "faster-whisper"

    def __init__(self, model_name: str, device: str, compute_type: str, language: str):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language or None
        self._model = None
        self._model_lock = asyncio.Lock()

    async def transcribe(self, pcm: bytes, audio_format: AudioFormat) -> str:
        if audio_format.encoding != "pcm_s16le":
            raise TranscriptionError(f"Unsupported audio encoding: {audio_format.encoding}")
        if audio_format.channels != 1:
            raise TranscriptionError("Local transcription currently requires mono PCM")

        try:
            model = await self._get_model()
            return await asyncio.to_thread(self._transcribe_sync, model, pcm, audio_format)
        except TranscriptionError:
            raise
        except Exception as error:
            raise TranscriptionError(f"Could not load the local Whisper model: {error}") from error

    async def _get_model(self):
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as error:
                    raise TranscriptionError(
                        "faster-whisper is not installed; install backend requirements"
                    ) from error
                self._model = await asyncio.to_thread(
                    WhisperModel,
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                )
        return self._model

    def _transcribe_sync(self, model, pcm: bytes, audio_format: AudioFormat) -> str:
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
                path = Path(audio_file.name)
            with wave.open(str(path), "wb") as wav_file:
                wav_file.setnchannels(audio_format.channels)
                wav_file.setsampwidth(2)
                wav_file.setframerate(audio_format.sample_rate)
                wav_file.writeframes(pcm)

            segments, _ = model.transcribe(
                str(path),
                language=self.language,
                beam_size=5,
                condition_on_previous_text=False,
                vad_filter=True,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
        except TranscriptionError:
            raise
        except Exception as error:
            raise TranscriptionError(f"Local transcription failed: {error}") from error
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
