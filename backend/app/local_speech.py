"""Local macOS speech, private temporary files and no network speech provider."""
import asyncio
from array import array
from pathlib import Path
import sys
import tempfile
import wave

from .speech import MAX_PCM_BYTES

MAX_INTERMEDIATE_BYTES = 8 * 1024 * 1024


class LocalSpeechUnavailable(RuntimeError):
    pass


class MacSpeech:
    def __init__(self, voice='Daniel (English (UK))', rate=180, gain=0.25):
        if sys.platform != 'darwin' or not all(Path(p).is_file() for p in ('/usr/bin/say', '/usr/bin/afconvert')):
            raise LocalSpeechUnavailable('Local Mac speech requires installed macOS say and afconvert')
        if not isinstance(voice, str) or not voice or len(voice) > 100:
            raise ValueError('Invalid local voice name')
        if not 100 <= rate <= 250 or not 0 < gain <= 1:
            raise ValueError('Invalid local voice rate or gain')
        self.voice, self.rate, self.gain = voice, rate, gain

    async def _run(self, arguments, output):
        process = await asyncio.create_subprocess_exec(
            *arguments, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        waiter = asyncio.create_task(process.wait())
        try:
            while not waiter.done():
                await asyncio.wait({waiter}, timeout=0.1)
                if output.exists() and output.stat().st_size > MAX_INTERMEDIATE_BYTES:
                    raise ValueError('Local synthesis file exceeds limit')
            if await waiter != 0:
                raise LocalSpeechUnavailable('Installed local voice synthesis failed')
        finally:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(asyncio.shield(waiter), 3)

    async def synthesize(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Speech text is empty')
        # TemporaryDirectory is private; text never enters argv, logs or persistent storage.
        with tempfile.TemporaryDirectory(prefix='friday-local-speech-') as directory:
            root = Path(directory)
            source, intermediate, converted = root / 'input.txt', root / 'speech.aiff', root / 'speech.wav'
            source.touch(mode=0o600)
            source.write_text(text[:1000], encoding='utf-8')
            async with asyncio.timeout(30):
                await self._run(['/usr/bin/say', '-v', self.voice, '-r', str(self.rate),
                                 '-o', str(intermediate), '-f', str(source)], intermediate)
                await self._run(['/usr/bin/afconvert', '-f', 'WAVE', '-d', 'LEI16@24000',
                                 '-c', '1', str(intermediate), str(converted)], converted)
            if converted.stat().st_size > MAX_PCM_BYTES + 4096:
                raise ValueError('Local speech exceeds playback limit')
            with wave.open(str(converted), 'rb') as stream:
                if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype()) != (1, 2, 24000, 'NONE'):
                    raise ValueError('Local speech has incompatible PCM format')
                size = stream.getnframes() * 2
                if not 0 < size <= MAX_PCM_BYTES:
                    raise ValueError('Local speech exceeds playback limit')
                pcm = stream.readframes(stream.getnframes())
            if len(pcm) != size:
                raise ValueError('Local speech is incomplete')
            samples = array('h')
            samples.frombytes(pcm)
            if sys.byteorder != 'little':
                samples.byteswap()
            for index, sample in enumerate(samples):
                samples[index] = round(sample * self.gain)
            if sys.byteorder != 'little':
                samples.byteswap()
            return samples.tobytes()

    async def close(self):
        # SpeechDirector owns/cancels synthesis tasks; their subprocesses/tempfiles are scoped.
        pass
