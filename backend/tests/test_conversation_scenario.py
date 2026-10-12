"""Synthetic providers exercise real Friday plumbing, never model competence."""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.agent import FridayAgent
from app.agent_jobs import AgentJobStore, AgentJobWorker
from app.config import Settings, WeatherLocation
from app.conversation_media import ConversationMedia
from app.db import ReminderStore
from app.device_auth import DeviceCredentialStore
from app.device_hub import DeviceHub
from app.experience import CAPABILITY
from app.models import CalendarEvent
from app.providers.base import TextProvider, ProviderTurn, ToolCall
from app.speech import SpeechDirector
from app.tools.google_calendar import CalendarSnapshot
from app.tools.music import MusicService
from app.tools.music_runtime import LocalMusicRuntime
from app.tools.spotify_runtime import WebPlaybackSource
from app.tools.weather import WeatherLocations, WeatherUnavailable, next_forecast_window

NOW=datetime(2026,10,8,21,0,tzinfo=timezone.utc)
class FixtureDateTime(datetime):
    @classmethod
    def now(cls,tz=None):return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

class ScriptedProvider(TextProvider):
    name="scripted-fixture-not-a-model"
    def __init__(self,turns):self.turns=list(turns);self.requests=[]
    async def complete(self,messages,instructions,tools):
        self.requests.append(deepcopy({"messages":messages,"instructions":instructions,"tools":tools}))
        return self.turns.pop(0) if self.turns else ProviderTurn(text="No scripted output remains.")

def call(name,args=None):return ProviderTurn(tool_calls=[ToolCall("fixture-"+name,name,args or {})])

class FakeResponse:
    def __init__(self,code,data=None):self.status_code=code;self.data=data;self.headers={}
    def json(self):return self.data

class InterceptedPlayback:
    def __init__(self,code=204,observe=True):
        self.playing=False;self.code=code;self.observe=observe;self.calls=[];self.before_read=None
    async def request(self,method,path):
        self.calls.append((method,path))
        if method=="GET":
            if self.before_read:self.before_read(len(self.calls))
            return FakeResponse(200,{"currently_playing_type":"track","item":{"id":"originalfixture","name":"Quiet Satellites","artists":[{"name":"The Paper Planets"}],"duration_ms":222000},"device":{"id":"syntheticSpeaker","name":"Fixture speaker","is_restricted":False},"is_playing":self.playing,"progress_ms":84000,"actions":{"disallows":{}}})
        if isinstance(self.code,Exception):raise self.code
        if self.code==204 and self.observe:self.playing=True
        return FakeResponse(self.code)

class SyntheticWeather(WeatherLocations):
    def __init__(self,locations):super().__init__(locations);self.calls=[]
    async def current(self,location="both"):
        if location not in {"both",*self.services}:raise WeatherUnavailable("Choose a configured ZIP location or both.")
        self.calls.append(location);start,end=next_forecast_window(NOW)
        labels=list(self.services) if location=="both" else [location]
        return [{"ok":True,"location":label,"temperature_2m":70.7,"weather_code":0,"is_day":1,"wind_speed_10m":8,"relative_humidity_2m":65,"rain_forecast":{"starts_at":start.isoformat(),"ends_at":end.isoformat(),"probability":10},"_fresh_for_ms":60000,"_fresh_until":(NOW+timedelta(seconds=60)).isoformat()} for label in labels]

def settings_for(path,**overrides):
    return Settings(_env_file=None,assistant_env="test",assistant_provider="mock",assistant_database_path=path,
        calendar_provider="sample",weather_provider="none",tts_provider="none",stt_provider="none",
        music_provider="spotify",music_account_access_approved=True,music_conversation_enabled=True,
        weather_locations=[WeatherLocation(label="07306",latitude=40.73,longitude=-74.06),WeatherLocation(label="10010",latitude=40.74,longitude=-73.98)],**overrides)

def synthetic_calendar(agent,place="Jersey City, NJ (07306)"):
    event=CalendarEvent(id="original-evening",title="Dinner with a friend",starts_at=NOW+timedelta(hours=1,minutes=30),ends_at=NOW+timedelta(hours=3),location=place)
    snapshot=CalendarSnapshot("sample",[event],NOW)
    async def read(range="today",force=False):
        result=deepcopy(snapshot)
        if force: result.fetched_at=agent.calendar.clock()
        return result
    agent.calendar.snapshot=read;agent.calendar.clock=lambda:NOW
    return snapshot

class ConversationSocketScenario(unittest.TestCase):
    def test_evening_weather_music_through_real_authenticated_socket(self):
        from app import main
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            settings=settings_for(Path(directory)/"fixture.db");store=ReminderStore(settings.assistant_database_path)
            agent=FridayAgent(settings,store);synthetic_calendar(agent);agent.weather=SyntheticWeather(settings.weather_locations)
            provider=ScriptedProvider([
                call("get_calendar",{"range":"today"}),ProviderTurn(text="Sample calendar: dinner at 6:30 in Jersey City. A civilized gap before it."),
                call("get_weather",{"location":"07306"}),ProviderTurn(text="Jersey City is clear now, about 71 degrees. The next full-hour rain chance is 10%; that is not an evening forecast."),
                call("get_music_state"),ProviderTurn(text="I can resume your current queue. Shall I?"),
                call("resume_music"),ProviderTurn(text="Untrusted provider acknowledgment; adapter result must own this."),
                ProviderTurn(text="All right. I will leave playback as it is."),
            ])
            agent.providers.select=lambda text:provider
            jobs=AgentJobStore(store.path);hub=DeviceHub();runtime=LocalMusicRuntime(hub)
            hub.scene_observer=main.hub.scene_observer;hub.scene_peer_observer=main.hub.scene_peer_observer
            credentials=DeviceCredentialStore(store.path);transport=InterceptedPlayback();music=MusicService("spotify",transport)
            runtime.source=WebPlaybackSource(music);runtime.music=music;runtime.enabled=True
            worker=AgentJobWorker(jobs,agent,hub,timeout_seconds=5,concurrency=1);worker.on_finished=main.work_finished
            speech=SpeechDirector(hub,None)
            values={"settings":settings,"store":store,"agent":agent,"job_store":jobs,"job_worker":worker,"hub":hub,"local_music":runtime,"music":music,"credential_store":credentials,"speech":speech,"transcriber":None,"scene_navigation":{}}
            for key,value in values.items():stack.enter_context(patch.object(main,key,value))
            stack.enter_context(patch.object(main.reminder_scheduler,"store",store));stack.enter_context(patch("app.agent.weather_now",lambda:NOW));stack.enter_context(patch("app.device_hub.datetime",FixtureDateTime))
            main.configure_agent_callbacks(agent)
            frames=[];results=[];device="conversation-fixture"
            def receive(socket):
                async def bounded():return await asyncio.wait_for(socket._send_rx.receive(),5)
                try:message=socket.portal.call(bounded)
                except TimeoutError:self.fail("Socket timed out after packets: "+json.dumps(frames[-8:]))
                socket._raise_on_close(message);return json.loads(message["text"])
            with TestClient(main.app) as client,client.websocket_connect("/ws/device/"+device) as socket:
                self.assertEqual(receive(socket)["type"],"hello.required")
                credentials.open_pairing_window(device,"aaaaaaaa")
                socket.send_json({"type":"hello","device_token":"a"*64,"capabilities":[CAPABILITY,"result_ack","media_updates_v1","media_persistent_v1"]})
                ready=receive(socket);self.assertEqual(ready["type"],"ready");frames.append({"direction":"output","packet":ready})
                for index,text in enumerate(["How does my evening look?","And the weather there?","Put something on while I get ready.","Yes, resume the current queue.","Actually, leave it as it is."]):
                    request=f"conversation-{index+1}";packet={"type":"utterance","text":text,"request_id":request}
                    frames.append({"direction":"input","packet":packet});socket.send_json(packet)
                    while True:
                        packet=receive(socket);frames.append({"direction":"output","packet":packet})
                        if packet["type"]=="agent.result":
                            self.assertEqual(packet["status"],"completed");results.append(packet)
                            socket.send_json({"type":"result.ack","job_id":packet["job_id"],"delivery_id":packet["delivery_id"]})
                        if packet["type"]=="experience.finished" and packet.get("experience_id")==request:break
                packet={"type":"scene.action","action":"music","request_id":"conversation-controls"}
                frames.append({"direction":"input","packet":packet});socket.send_json(packet)
                while True:
                    packet=receive(socket);frames.append({"direction":"output","packet":packet})
                    if packet.get("media_request_id")=="conversation-controls" and packet.get("surface",{}).get("title")=="Quiet Satellites":
                        self.assertTrue(packet["media_session_id"])
                        self.assertTrue(any(action["id"].startswith("musicctl:") for action in packet["surface"]["actions"]))
                        self.assertTrue(any(view.request=="conversation-controls" and runtime._allowed(view) for view in runtime.views.values()))
                        break
            self.assertGreater(results[1]["surface"]["weather_valid_ms"],1000)
            self.assertEqual(len(results),5);self.assertEqual(agent.weather.calls,["07306"])
            self.assertEqual(results[-2]["text"],"The existing queue is playing.")
            self.assertTrue(results[-1]["retain_context"])
            self.assertEqual(sum(method=="PUT" for method,path in transport.calls),1)
            self.assertEqual(results[2]["surface"]["kind"],"media")
            self.assertIn("Shall I?",results[2]["text"])
            second=json.dumps(provider.requests[2]["messages"]);self.assertIn("original-evening",second);self.assertIn("07306",second)
            final=json.dumps(provider.requests[6]["messages"]);self.assertIn("get_calendar",final);self.assertIn("get_weather",final);self.assertIn("get_music_state",final)
            self.assertNotIn("Quiet Satellites",final);self.assertNotIn("syntheticSpeaker",final)
            self.assertEqual(provider.turns,[])
            destination=os.environ.get("FRIDAY_CONVERSATION_FIXTURE")
            if destination:
                path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps({"fixture_clock":NOW.isoformat(),"synthetic_only":True,"actual_model_reasoning":False,"actual_actions":False,"path":"authenticated websocket -> handle_utterance -> durable job -> FridayAgent/provider/tool loop -> DeviceHub -> native receiver replay","frames":frames,"provider_inputs":provider.requests,"intercepted_calls":transport.calls},indent=2))

class ConversationContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory();self.settings=settings_for(Path(self.directory.name)/"fixture.db")
        self.store=ReminderStore(self.settings.assistant_database_path);self.agent=FridayAgent(self.settings,self.store)
        self.transport=InterceptedPlayback();self.music=MusicService("spotify",self.transport);self.current=True;self.protected=False
        self.adapter=ConversationMedia(self.settings,lambda:self.store,lambda:self.music,lambda device:self.current,lambda device:self.protected)
        self.agent.on_media=self.adapter
    async def asyncTearDown(self):self.directory.cleanup()

    async def test_empty_response_does_not_imply_action_succeeded(self):
        self.agent.providers.select=lambda text:ScriptedProvider([ProviderTurn()])
        reply=await self.agent.handle("device","Help me think","empty")
        self.assertNotEqual(reply.text,"Done.");self.assertIn("couldn't",reply.text)

    async def test_resume_failure_uncertainty_no_retry_and_truthful_reply(self):
        for index,(code,observe,status) in enumerate([(403,True,"rejected"),(204,False,"accepted_unobserved"),(TimeoutError(),True,"uncertain")]):
            self.transport=InterceptedPlayback(code,observe);self.music=MusicService("spotify",self.transport)
            request=f"failure-{index}"
            provider=ScriptedProvider([call("resume_music"),ProviderTurn(text="I started the music successfully.")]);self.agent.providers.select=lambda text:provider
            reply=await self.agent.handle("device","Resume my existing queue",request)
            self.assertNotIn("successfully",reply.text)
            saved=json.loads(self.store.conversation("device")[-2]["content"]);self.assertEqual(saved["status"],status);self.assertFalse(saved["ok"])
            outcome=await self.adapter("device","resume_music",request,7)
            self.assertEqual(json.loads(outcome.content)["status"],status)
            self.assertEqual(sum(method=="PUT" for method,path in self.transport.calls),1)

    async def test_resume_truth_survives_later_read_tools(self):
        self.transport.code=TimeoutError()
        provider=ScriptedProvider([call("resume_music"),call("get_music_state"),ProviderTurn(text="Music started successfully.")])
        self.agent.providers.select=lambda text:provider
        reply=await self.agent.handle("device","Resume my queue and check it","multiple-tools")
        self.assertIn("uncertain",reply.text);self.assertNotIn("successfully",reply.text)
        self.assertEqual(sum(method=="PUT" for method,path in self.transport.calls),1)

    async def test_snapshot_has_only_owned_navigation_actions(self):
        outcome=await self.adapter("device","get_music_state","snapshot",0)
        self.assertEqual([action.id for action in outcome.surface.actions],["music","dismiss"])
        self.assertIn("snapshot",outcome.surface.items[-1]["label"])
        self.assertFalse(any(action.id.startswith("musicctl:") for action in outcome.surface.actions))

    async def test_ownership_changed_during_fresh_read_prevents_command(self):
        self.transport.before_read=lambda count:setattr(self,"current",False) if count==2 else None
        outcome=await self.adapter("device","resume_music","late",0)
        self.assertFalse(outcome.success);self.assertFalse(any(method=="PUT" for method,path in self.transport.calls))

    async def test_disabled_protected_and_restricted_are_truthful_no_io_or_action(self):
        for enabled,protected,current in [(False,False,True),(True,True,True),(True,False,False)]:
            self.settings.music_conversation_enabled=enabled;self.protected=protected;self.current=current
            outcome=await self.adapter("device","resume_music","blocked",0)
            self.assertFalse(outcome.success);self.assertEqual(self.transport.calls,[])

    async def test_ambiguous_place_and_music_choice_remain_clarifications(self):
        provider=ScriptedProvider([ProviderTurn(text="Which location should I check?"),ProviderTurn(text="I can resume the existing queue. Is that what you want?")])
        self.agent.providers.select=lambda text:provider
        first=await self.agent.handle("device","And the weather there?","no-place")
        second=await self.agent.handle("device","Put something on","ambiguous-music")
        self.assertEqual(self.transport.calls,[]);self.assertIn("Which location",first.text);self.assertIn("Is that",second.text)
        self.assertIn("never silently substitute",provider.requests[0]["instructions"])

    async def test_changed_plan_and_superseded_reply_not_saved_as_current_plan(self):
        provider=ScriptedProvider([ProviderTurn(text="Proceed to the earlier destination."),ProviderTurn(text="Which place is the new plan for?")]);self.agent.providers.select=lambda text:provider
        self.agent.on_turn_current=lambda device:False
        await self.agent.handle("device","Use my earlier evening plan","superseded")
        self.agent.on_turn_current=lambda device:True
        await self.agent.handle("device","Plans changed. I am staying elsewhere. Weather there?","changed")
        history=json.dumps(provider.requests[1]["messages"])
        self.assertNotIn("Proceed to the earlier destination",history);self.assertIn("superseded",history)

    async def test_calendar_privacy_then_follow_up_never_shares_native_facts(self):
        synthetic_calendar(self.agent,"PRIVATE_PLACE_SENTINEL");self.settings.calendar_provider="google";self.settings.calendar_model_context=False
        provider=ScriptedProvider([call("get_calendar",{"range":"today"}),ProviderTurn(text="Your calendar is displayed locally. Which location should I use?"),ProviderTurn(text="Which location should I check?")]);self.agent.providers.select=lambda text:provider
        await self.agent.handle("device","My evening?","private-calendar")
        await self.agent.handle("device","Weather there?","private-follow-up")
        for request in provider.requests:self.assertNotIn("PRIVATE_PLACE_SENTINEL",json.dumps(request))
        self.assertIn("sharing_disabled",json.dumps(provider.requests[1]["messages"]))

    async def test_history_budget_retains_whole_tool_turns_and_device_isolation(self):
        for index in range(11):
            self.store.append_messages("device",str(index),[{"role":"user","content":f"request {index}"},{"role":"assistant","content":"","tool_calls":[{"id":str(index),"name":"get_calendar","arguments":{}}]},{"role":"tool","name":"get_calendar","tool_call_id":str(index),"content":"{}"},{"role":"assistant","content":"reply"}])
        history=self.store.conversation("device",limit=10);self.assertEqual(len(history),40);self.assertEqual(history[0]["content"],"request 1")
        self.assertEqual(self.store.conversation("other"),[])

    async def test_late_interrupted_job_is_scrubbed_and_new_plan_keeps_context(self):
        from app import main
        from test_device_hub import FakeWebSocket
        hub=DeviceHub();socket=FakeWebSocket();device="interrupt-fixture";hub.register(device,socket,{CAPABILITY,"result_ack"})
        runtime=LocalMusicRuntime(hub);hub.scene_observer=main.hub.scene_observer;hub.scene_peer_observer=main.hub.scene_peer_observer
        jobs=AgentJobStore(self.store.path);worker=AgentJobWorker(jobs,self.agent,hub,timeout_seconds=5,concurrency=1)
        started=asyncio.Event();release=asyncio.Event()
        class InterruptedProvider(ScriptedProvider):
            async def complete(inner,messages,instructions,tools):
                if not inner.requests:started.set();await release.wait()
                return await super().complete(messages,instructions,tools)
        provider=InterruptedProvider([ProviderTurn(text="SUPERSEDED_PLAN_SENTINEL"),ProviderTurn(text="Which place is the new plan for?")]);self.agent.providers.select=lambda text:provider
        with ExitStack() as stack:
            for key,value in {"settings":self.settings,"store":self.store,"agent":self.agent,"job_store":jobs,"job_worker":worker,"hub":hub,"local_music":runtime,"music":self.music,"speech":SpeechDirector(hub,None),"scene_navigation":{}}.items():stack.enter_context(patch.object(main,key,value))
            main.configure_agent_callbacks(self.agent)
            peer=hub.peer_key(device,socket)
            with hub.experience.scope(device,"old-evening",peer):await main.handle_utterance(device,"My evening plan?","old-evening")
            task=asyncio.create_task(worker.process_once());await asyncio.wait_for(started.wait(),2)
            with hub.experience.scope(device,"changed-plan",peer):await main.handle_utterance(device,"Plans changed. What is the weather there?","changed-plan")
            release.set();self.assertTrue(await task);self.assertTrue(await worker.process_once())
        results=[message for message in socket.messages if message["type"]=="agent.result"]
        self.assertEqual(len(results),2);self.assertTrue(results[0]["experience_historical"])
        self.assertNotIn("SUPERSEDED_PLAN_SENTINEL",json.dumps(results));self.assertTrue(results[1]["retain_context"])
        self.assertNotIn("SUPERSEDED_PLAN_SENTINEL",json.dumps(provider.requests[1]["messages"]));self.assertIn("superseded",json.dumps(provider.requests[1]["messages"]))
        self.assertEqual(self.transport.calls,[])

    async def test_resume_success_replay_across_restart_is_once_per_user_job(self):
        first=await self.adapter("device","resume_music","once",3);self.assertTrue(first.success)
        restarted=ConversationMedia(self.settings,lambda:self.store,lambda:self.music,lambda device:True,lambda device:False)
        second=await restarted("device","resume_music","once",9);self.assertEqual(first.content,second.content)
        self.assertEqual(sum(method=="PUT" for method,path in self.transport.calls),1)

    async def test_cancel_during_command_keeps_uncertainty_and_prevents_replay(self):
        original=self.transport.request
        async def interrupted(method,path):
            if method=="PUT":
                self.transport.calls.append((method,path));self.transport.playing=True
                raise asyncio.CancelledError()
            return await original(method,path)
        self.transport.request=interrupted
        with self.assertRaises(asyncio.CancelledError):await self.adapter("device","resume_music","cancel-during-command",0)
        outcome=await self.adapter("device","resume_music","cancel-during-command",1)
        self.assertEqual(json.loads(outcome.content)["status"],"uncertain")
        self.assertEqual(sum(method=="PUT" for method,path in self.transport.calls),1)
        self.assertNotIn("No changes",json.loads(outcome.content)["message"])

    async def test_restricted_or_missing_player_never_sends_playback(self):
        original=self.transport.request
        for missing in (False,True):
            async def restricted(method,path):
                response=await original(method,path)
                if method=="GET":
                    response.data["device"]={} if missing else {"id":"syntheticSpeaker","name":"Fixture speaker","is_restricted":True}
                return response
            self.transport.request=restricted
            outcome=await self.adapter("device","resume_music","restricted-"+str(missing),0)
            self.assertFalse(outcome.success)
        self.assertFalse(any(method=="PUT" for method,path in self.transport.calls))

    async def test_unconfigured_weather_reference_is_not_a_home_fallback(self):
        self.agent.weather=SyntheticWeather(self.settings.weather_locations)
        outcome=await self.agent._execute_read_tool("get_weather",{"location":"unestablished-place"},device_id="device",request_id="missing-place",operation_index=0,for_model=True)
        self.assertFalse(outcome.success);self.assertEqual(self.agent.weather.calls,[])
        self.assertIn("configured",outcome.content)

    async def test_music_tools_are_not_advertised_without_explicit_opt_in(self):
        self.settings.music_conversation_enabled=False
        provider=ScriptedProvider([ProviderTurn(text="Conversation playback is unavailable; Music controls remain on the display.")]);self.agent.providers.select=lambda text:provider
        reply=await self.agent.handle("device","Put something on","not-enabled")
        self.assertTrue(reply.retain_context);self.assertNotIn("resume_music",json.dumps(provider.requests[0]["tools"]));self.assertEqual(self.transport.calls,[])

if __name__=="__main__":unittest.main()
