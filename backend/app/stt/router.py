from __future__ import annotations

from ..config import Settings
from .base import SpeechTranscriber
from .faster_whisper_provider import FasterWhisperTranscriber


def create_transcriber(settings: Settings) -> SpeechTranscriber | None:
    if settings.stt_provider == "none":
        return None
    return FasterWhisperTranscriber(
        model_name=settings.stt_model,
        device=settings.stt_device,
        compute_type=settings.stt_compute_type,
        language=settings.stt_language,
    )
