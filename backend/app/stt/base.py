from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioFormat:
    encoding: str
    sample_rate: int
    channels: int


class TranscriptionError(RuntimeError):
    pass


class SpeechTranscriber(Protocol):
    name: str

    async def transcribe(self, pcm: bytes, audio_format: AudioFormat) -> str:
        """Return recognized text for one complete speech turn."""
