from __future__ import annotations

from dataclasses import dataclass, field

from .stt.base import AudioFormat


class AudioSessionError(ValueError):
    pass


@dataclass
class AudioSession:
    max_seconds: float
    audio_format: AudioFormat | None = None
    data: bytearray = field(default_factory=bytearray)

    @property
    def active(self) -> bool:
        return self.audio_format is not None

    def start(self, encoding: str, sample_rate: int, channels: int) -> None:
        if self.active:
            raise AudioSessionError("An audio turn is already active")
        if encoding != "pcm_s16le" or sample_rate not in (16000, 24000) or channels != 1:
            raise AudioSessionError("Expected mono pcm_s16le audio at 16 or 24 kHz")
        self.audio_format = AudioFormat(encoding, sample_rate, channels)
        self.data.clear()

    def append(self, chunk: bytes) -> None:
        if not self.active:
            raise AudioSessionError("Binary audio arrived without audio.start")
        assert self.audio_format is not None
        max_bytes = (
            self.max_seconds
            * self.audio_format.sample_rate
            * self.audio_format.channels
            * 2
        )
        if len(self.data) + len(chunk) > max_bytes:
            self.reset()
            raise AudioSessionError("Audio turn exceeded the configured duration limit")
        self.data.extend(chunk)

    def finish(self) -> tuple[bytes, AudioFormat]:
        if self.audio_format is None:
            raise AudioSessionError("audio.end arrived without audio.start")
        audio_format = self.audio_format
        data = bytes(self.data)
        self.reset()
        return data, audio_format

    def reset(self) -> None:
        self.audio_format = None
        self.data.clear()
