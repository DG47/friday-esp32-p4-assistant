import asyncio
import unittest
from app.device_hub import DeviceHub
from app.speech import SpeechDirector

class SpeechRevocation(unittest.IsolatedAsyncioTestCase):
    async def test_resistant_synthesis_is_detached_but_cannot_emit_late_audio(self):
        entered,release=asyncio.Event(),asyncio.Event();messages=[]
        class Provider:
            async def synthesize(self,text):
                entered.set()
                while not release.is_set():
                    try:await release.wait()
                    except asyncio.CancelledError:pass
                return b'\0\0'*480
            async def close(self):pass
        class Peer:
            async def send_json(self,message):
                messages.append(message)
                if message['type']=='speech.end':director.finished('device',message['stream_id'])
        hub=DeviceHub();hub.register('device',Peer(),{'pcm_speech'})
        director=SpeechDirector(hub,Provider());director.cancel_grace=.01
        await director.speak('device','Original synthetic speech')
        await entered.wait();old=director.tasks['device']
        cancel=asyncio.create_task(director.cancel('device'))
        try:
            done,_=await asyncio.wait({cancel},timeout=.2)
            self.assertTrue(done,'Playback Stop must return without waiting for resistant synthesis')
            result=cancel.result()
            self.assertTrue(result['producer_revoked']);self.assertFalse(result['handler_drained'])
            self.assertIn(old,director.retired)
            boundary=len(messages);release.set();await asyncio.wait_for(old,1)
            self.assertFalse(any(m['type'] in {'speech.start','speech.chunk','speech.end','state'} for m in messages[boundary:]))
        finally:
            release.set();await asyncio.wait_for(old,1);await asyncio.wait_for(cancel,1);await director.close()
