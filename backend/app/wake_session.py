from __future__ import annotations

import re
from dataclasses import dataclass, field

from .stt.base import AudioFormat


@dataclass
class WakeAudioSession:
    sample_rate: int = 12000
    channels: int = 1
    probe_seconds: float = 3.0
    overlap_seconds: float = 1.0
    data: bytearray = field(default_factory=bytearray)

    @property
    def audio_format(self) -> AudioFormat:
        return AudioFormat("pcm_s16le", self.sample_rate, self.channels)

    @property
    def probe_bytes(self) -> int:
        return int(self.sample_rate * self.channels * 2 * self.probe_seconds)

    @property
    def overlap_bytes(self) -> int:
        return int(self.sample_rate * self.channels * 2 * self.overlap_seconds)

    def append(self, chunk: bytes) -> None:
        self.data.extend(chunk)
        maximum = self.probe_bytes + self.overlap_bytes
        if len(self.data) > maximum:
            del self.data[: len(self.data) - maximum]

    def take_probe(self) -> bytes | None:
        if len(self.data) < self.probe_bytes:
            return None
        probe = bytes(self.data[-self.probe_bytes :])
        overlap = bytes(self.data[-self.overlap_bytes :])
        self.data.clear()
        self.data.extend(overlap)
        return probe

    def reset(self) -> None:
        self.data.clear()


def contains_wake_word(text: str, wake_word: str = "friday") -> bool:
    words = re.findall(r"[a-z]+", text.casefold())
    return wake_word.casefold() in words


def pcm_peak(pcm: bytes) -> int:
    samples = memoryview(pcm).cast("h")
    return max((abs(sample) for sample in samples), default=0)
