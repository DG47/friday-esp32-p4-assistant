import asyncio
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import wave

from app.local_speech import MacSpeech, LocalSpeechUnavailable, MAX_INTERMEDIATE_BYTES
from app.speech import MAX_PCM_BYTES
from app.config import Settings


class MacSpeechTests(unittest.IsolatedAsyncioTestCase):
    def provider(self):
        with patch('app.local_speech.sys.platform', 'darwin'), patch('app.local_speech.Path.is_file', return_value=True):
            return MacSpeech()

    async def test_conversion_gain_private_files_and_no_text_in_argv(self):
        provider = self.provider()
        calls, directories = [], []
        async def run(arguments, output):
            calls.append(arguments)
            directories.append(output.parent)
            self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)
            source = output.parent / 'input.txt'
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('$(private) `content`', ' '.join(arguments))
            if output.suffix == '.aiff': output.write_bytes(b'fake intermediate')
            else:
                with wave.open(str(output), 'wb') as stream:
                    stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                    stream.writeframes(struct.pack('<hh', 12000, -12000))
        with patch.object(provider, '_run', side_effect=run):
            pcm = await provider.synthesize('$(private) `content`')
        self.assertEqual(struct.unpack('<hh', pcm), (3000, -3000))
        self.assertTrue(all(not directory.exists() for directory in directories))
        self.assertEqual(calls[0][0], '/usr/bin/say')
        self.assertEqual(calls[1][0], '/usr/bin/afconvert')
        self.assertIn('LEI16@24000', calls[1])

    async def test_cancellation_cleans_temp_text(self):
        provider = self.provider()
        started, paths = asyncio.Event(), []
        async def run(arguments, output):
            paths.append(output.parent)
            started.set()
            await asyncio.Future()
        with patch.object(provider, '_run', side_effect=run):
            task = asyncio.create_task(provider.synthesize('Private test text'))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(paths[0].exists())

    async def test_subprocess_killed_and_reaped_on_cancellation(self):
        provider = self.provider()
        class Process:
            returncode = None
            killed = False
            done = asyncio.Event()
            async def wait(self):
                await self.done.wait()
                return self.returncode
            def kill(self):
                self.killed = True
                self.returncode = -9
                self.done.set()
        process = Process()
        async def create(*args, **kwargs): return process
        with patch('app.local_speech.asyncio.create_subprocess_exec', side_effect=create):
            task = asyncio.create_task(provider._run(['/usr/bin/say', '-f', '/tmp/input'], Path('/tmp/no-friday-output')))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(process.killed)
        self.assertTrue(process.done.is_set())

    async def test_bad_format_and_excess_duration_are_rejected(self):
        provider = self.provider()
        for rate, frames in [(44100, 2), (24000, MAX_PCM_BYTES // 2 + 1)]:
            async def run(arguments, output):
                if output.suffix == '.aiff': output.touch()
                else:
                    with wave.open(str(output), 'wb') as stream:
                        stream.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                        stream.writeframes(b'\0\0' * frames)
            with patch.object(provider, '_run', side_effect=run):
                with self.assertRaises(ValueError): await provider.synthesize('Diagnostic')

    def test_portable_unavailable_and_opt_in_without_api_key(self):
        with patch('app.local_speech.sys.platform', 'linux'):
            with self.assertRaisesRegex(LocalSpeechUnavailable, 'requires installed macOS'):
                MacSpeech()
        settings = Settings(_env_file=None, tts_provider='macos', openai_api_key='')
        self.assertEqual(settings.tts_provider, 'macos')
        self.assertEqual(settings.tts_local_gain, 0.25)

    async def test_timeout_cleans_tempfiles(self):
        provider = self.provider()
        paths = []
        async def run(arguments, output):
            paths.append(output.parent)
            raise TimeoutError('Synthetic timeout')
        with patch.object(provider, '_run', side_effect=run):
            with self.assertRaises(TimeoutError): await provider.synthesize('Diagnostic')
        self.assertFalse(paths[0].exists())

    async def test_large_subprocess_output_kills_owned_process(self):
        import tempfile
        provider = self.provider()
        class Process:
            returncode = None
            killed = False
            done = asyncio.Event()
            async def wait(self):
                await self.done.wait()
                return self.returncode
            def kill(self):
                self.killed = True
                self.returncode = -9
                self.done.set()
        process = Process()
        async def create(*args, **kwargs): return process
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'synthetic.aiff'
            with output.open('wb') as stream: stream.truncate(MAX_INTERMEDIATE_BYTES + 1)
            with patch('app.local_speech.asyncio.create_subprocess_exec', side_effect=create):
                with self.assertRaises(ValueError): await provider._run(['/usr/bin/say'], output)
        self.assertTrue(process.killed)
