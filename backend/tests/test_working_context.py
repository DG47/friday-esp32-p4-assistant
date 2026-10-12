"""Real reference binding and delivery, scripted provider; no model-understanding claim."""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from app.agent import FridayAgent
from app.agent_jobs import AgentJobStore, AgentJobWorker
from app.db import ReminderStore
from app.device_auth import DeviceCredentialStore
from app.device_hub import DeviceHub, weather_packet
from app.experience import CAPABILITY
from app.models import CalendarEvent
from app.providers.base import ProviderTurn
from app.speech import SpeechDirector
from app.tools.google_calendar import CalendarSnapshot
from app.tools.music import MusicService
from app.tools.music_runtime import LocalMusicRuntime
from app.working_context import digest
from test_conversation_scenario import NOW, FixtureDateTime, ScriptedProvider, SyntheticWeather, InterceptedPlayback, settings_for, synthetic_calendar, call


class ReferenceContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory();self.settings=settings_for(Path(self.directory.name)/'test.db')
        self.agent=FridayAgent(self.settings,ReminderStore(self.settings.assistant_database_path))
        synthetic_calendar(self.agent);self.agent.weather=SyntheticWeather(self.settings.weather_locations)
        self.clock=NOW;self.agent.calendar.clock=lambda:self.clock
        self.context=self.agent.working_context
        self.request='initial';self.context.begin('device',self.request)
    async def asyncTearDown(self):self.directory.cleanup()
    async def read(self,name,args,request=None):
        with patch('app.agent.weather_now',lambda:self.clock):
            return await self.agent._execute_read_tool(name,args,'device',request or self.request,0,for_model=True)
    async def calendar(self):
        result=await self.read('get_calendar',{'range':'today'})
        return json.loads(result.content)['context_refs'][0]['event_ref'],result

    async def test_same_event_and_weather_identity_across_correction(self):
        ref,calendar=await self.calendar()
        first=await self.read('get_weather',{'location':'context'})
        original=first.surface.context
        second=await self.read('get_weather',{'event_ref':ref,'location':'10010'})
        self.assertEqual(original['event_ref'],second.surface.context['event_ref'])
        self.assertNotEqual(original['weather_ref'],second.surface.context['weather_ref'])
        self.assertEqual(self.agent.weather.calls,['07306','10010'])
        self.assertEqual(self.context.state('device').events[ref].location,'Jersey City, NJ (07306)')
        self.assertEqual(calendar.surface.items[0]['object_id'],ref)
        self.assertIn('10010',second.surface.items[2]['time'])
        self.assertIn('07306',second.surface.items[1]['label'])
        # A later generic speech follow-up must use the corrected relation,
        # exactly as a fresh Weather touch does, without rewriting Calendar.
        followup=await self.read('get_weather',{'location':'context'})
        _,_,touched=self.context.touch('device',followup.surface.actions[1].id)
        self.assertEqual(followup.surface.context['weather_ref'],second.surface.context['weather_ref'])
        self.assertEqual(touched.context['weather_location'],'10010')
        self.assertEqual(self.agent.weather.calls,['07306','10010','10010'])
        self.assertEqual(self.context.state('device').events[ref].location,'Jersey City, NJ (07306)')
        # Expiring private event facts and refreshing public Weather must not
        # silently discard the correction when Calendar later rereads that event.
        self.clock=NOW+timedelta(seconds=31)
        self.assertEqual(self.context.state('device').events,{})
        public=await self.read('get_weather',{'location':'10010'})
        self.context.attach_weather('device',ref,'10010',public.surface)
        await self.calendar()
        self.assertEqual(self.context.resolve('device')[1],'10010')
        self.assertNotIn('Jersey City',self.context.state('device').weather_source_fingerprint)
        synthetic_calendar(self.agent,'Somewhere ambiguous');await self.calendar()
        self.assertFalse((await self.read('get_weather',{'location':'context'})).success)
        self.assertEqual(self.agent.weather.calls,['07306','10010','10010','10010'])
        synthetic_calendar(self.agent,'Jersey City 07306');await self.calendar()
        rebound=await self.read('get_weather',{'location':'context'})
        self.assertEqual(rebound.surface.context['weather_location'],'07306')


    async def test_touch_focus_resolves_same_referent_as_speech_not_position(self):
        event=CalendarEvent(id='other',title='Studio visit',starts_at=NOW+timedelta(hours=2),ends_at=NOW+timedelta(hours=3),location='New York 10010')
        snapshot=CalendarSnapshot('sample',[event,*synthetic_calendar(self.agent).events],NOW)
        async def snapshot_read(range='today',force=False):return deepcopy(snapshot)
        self.agent.calendar.snapshot=snapshot_read
        result=await self.read('get_calendar',{'range':'today'})
        ambiguous=await self.read('get_weather',{'location':'context'})
        self.assertFalse(ambiguous.success);self.assertEqual(self.agent.weather.calls,[])
        ref,operation,surface=self.context.touch('device',result.surface.items[1]['action'])
        self.assertEqual(operation,'event')
        weather=await self.read('get_weather',{'location':'context'})
        self.assertEqual(weather.surface.context['event_ref'],ref)
        self.assertEqual(self.agent.weather.calls,['07306'])
        self.assertEqual(self.context.provider_context('device')['focused_event_ref'],ref)

    async def test_ambiguous_location_or_unconfigured_zip_never_reads(self):
        for place in ['Somewhere','07306 and 10010','PRIVATE_PLACE','073060','10010 Main Street']:
            synthetic_calendar(self.agent,place);await self.calendar()
            outcome=await self.read('get_weather',{'location':'context'})
            self.assertFalse(outcome.success)
        ref,_=await self.calendar()
        self.assertFalse((await self.read('get_weather',{'event_ref':ref,'location':'99999'})).success)
        self.assertEqual(self.agent.weather.calls,[])

    async def test_repeated_touch_preserves_objects_and_original_leases_no_io(self):
        ref,_=await self.calendar();result=await self.read('get_weather',{'location':'context'})
        state=self.context.state('device');event_until,weather_until=state.event_until,state.weather_until
        token=result.surface.actions[1].id;revision=state.revision
        for _ in range(20):
            selected,op,surface=self.context.touch('device',token)
            self.assertEqual(selected,ref);self.assertEqual(surface.context['revision'],revision)
        self.assertEqual(self.agent.weather.calls,['07306'])
        self.assertEqual((state.event_until,state.weather_until),(event_until,weather_until))

    async def test_dismiss_revokes_reference_and_touch_no_resurrection(self):
        ref,_=await self.calendar();weather=await self.read('get_weather',{'location':'context'})
        token=weather.surface.actions[1].id;self.context.dismiss('device')
        with self.assertRaises(ValueError):self.context.touch('device',token)
        self.assertFalse((await self.read('get_weather',{'event_ref':ref,'location':'context'})).success)
        self.assertEqual(self.context.state('device').events,{})
        self.assertIsNone(self.context.state('device').weather)
        self.assertEqual(self.agent.weather.calls,['07306'])

    async def test_corrected_context_rejects_old_tokens_and_unknown_device(self):
        ref,_=await self.calendar();first=await self.read('get_weather',{'location':'context'})
        old=first.surface.actions[1].id
        await self.read('get_weather',{'event_ref':ref,'location':'10010'})
        with self.assertRaises(ValueError):self.context.touch('device',old)
        with self.assertRaises(ValueError):self.context.touch('another-device',old)

    async def test_late_weather_cannot_overwrite_new_request_or_dismissal(self):
        for dismiss in (False,True):
            self.context.begin('device','initial');await self.calendar()
            start=asyncio.Event();finish=asyncio.Event();read=self.agent.weather.current
            async def delayed(location):start.set();await finish.wait();return await read(location)
            self.agent.weather.current=delayed
            task=asyncio.create_task(self.read('get_weather',{'location':'context'}));await start.wait()
            if dismiss:self.context.dismiss('device')
            else:self.context.begin('device','newer')
            finish.set();result=await task
            self.assertFalse(result.success);self.assertEqual(json.loads(result.content)['status'],'superseded')
            self.assertIsNone(self.context.state('device').weather)
            self.agent.weather.current=read

    async def test_late_calendar_cannot_replace_current_focus(self):
        ref,_=await self.calendar();start=asyncio.Event();finish=asyncio.Event();read=self.agent.calendar.snapshot
        async def delayed(range,force=False):start.set();await finish.wait();return await read(range,force=force)
        self.agent.calendar.snapshot=delayed
        task=asyncio.create_task(self.read('get_calendar',{'range':'today'}));await start.wait()
        self.context.dismiss('device');finish.set();await task
        self.assertEqual(self.context.state('device').events,{})

    async def test_event_lease_erases_private_facts_weather_stays_until_dismissed(self):
        ref,_=await self.calendar();result=await self.read('get_weather',{'location':'context'})
        self.clock+=timedelta(seconds=31)
        surface=self.context.surface('device');self.assertEqual(self.context.state('device').events,{})
        self.assertNotIn('Dinner',json.dumps(surface.model_dump(mode='json')))
        self.assertEqual(surface.context['event_valid_ms'],0);self.assertGreater(surface.context['weather_valid_ms'],0)
        self.assertEqual(surface.items[2]['object_id'],result.surface.items[2]['object_id'])
        self.context.touch('device',surface.actions[0].id)  # public Weather remains accessible
        self.clock+=timedelta(seconds=30)
        expired=self.context.surface('device');self.assertIn('Reading expired',expired.items[2]['label'])
        self.assertEqual(expired.context['weather_ref'],result.surface.context['weather_ref'])
        self.assertIsNone(expired.expires_ms)

    async def test_send_time_replay_never_renews_fact_leases(self):
        await self.calendar();result=await self.read('get_weather',{'location':'context'})
        packet={'type':'surface','surface':result.surface.model_dump(mode='json')}
        delayed=weather_packet(packet,NOW+timedelta(seconds=40))
        self.assertEqual(delayed['surface']['context']['event_valid_ms'],0)
        self.assertEqual(delayed['surface']['context']['weather_valid_ms'],20000)
        self.assertEqual(packet['surface']['context']['event_valid_ms'],30000)

    async def test_calendar_privacy_disables_model_binding_before_weather_io(self):
        self.settings.calendar_provider='google';self.settings.calendar_model_context=False
        result=await self.read('get_calendar',{'range':'today'})
        self.assertNotIn('context_refs',json.loads(result.content));self.assertEqual(self.context.state('device').events,{})
        self.assertFalse((await self.read('get_weather',{'location':'context'})).success)
        self.assertEqual(self.agent.weather.calls,[])
        self.assertEqual(self.context.provider_context('device'),{'status':'local_only'})

    async def test_speech_dismissal_and_provider_authoritative_focus(self):
        await self.calendar();await self.read('get_weather',{'location':'context'})
        self.agent.providers.select=lambda text:ScriptedProvider([ProviderTurn(text='Which new location?')])
        reply=await self.agent.handle('device','Dismiss the plan','dismiss')
        self.assertEqual(reply.surface.kind,'ambient');self.assertIsNone(self.context.state('device').weather)
        provider=ScriptedProvider([call('get_weather',{'location':'context'}),ProviderTurn(text='Which event should I use?')]);self.agent.providers.select=lambda text:provider
        await self.agent.handle('device','And the weather there?','after-dismiss')
        self.assertIn('no_current_event',provider.requests[0]['instructions'])
        self.assertEqual(self.agent.weather.calls,['07306'])

    async def test_context_memory_bounded_and_legacy_actions_preserved(self):
        self.agent.on_context_enabled=lambda device:False
        result=await self.read('get_calendar',{'range':'today'})
        self.assertTrue(result.surface.items[0]['action'].startswith('event:'))
        self.assertNotIn('context_refs',json.loads(result.content))
        for index in range(40):self.context.begin(str(index),'request')
        self.assertLessEqual(len(self.context.devices),32)

    async def test_event_details_remain_available_with_same_weather_object(self):
        await self.calendar();result=await self.read('get_weather',{'location':'context'})
        ref,op,surface=self.context.touch('device',result.surface.actions[0].id)
        self.assertEqual(surface.title,'Dinner with a friend')
        self.assertEqual([item['time'] for item in surface.items],['When','Until','Where'])
        self.assertEqual(surface.context['weather_ref'],result.surface.context['weather_ref'])
        self.assertIn('70.7',surface.context['weather_summary']);self.assertIn('07306',surface.context['weather_summary'])
        self.assertEqual(self.agent.weather.calls,['07306'])

    async def test_calendar_correction_keeps_weather_until_explicit_new_read(self):
        ref,_=await self.calendar();result=await self.read('get_weather',{'location':'context'})
        synthetic_calendar(self.agent,'New York 10010');await self.calendar()
        self.assertEqual(self.context.surface('device').context['weather_ref'],result.surface.context['weather_ref'])
        self.assertEqual(self.context.state('device').weather_event_ref,ref)
        newer=await self.read('get_weather',{'location':'context'})
        self.assertEqual(newer.surface.context['event_ref'],ref)
        self.assertEqual(self.agent.weather.calls,['07306','10010'])

    async def test_invalid_references_and_context_metadata_fail_closed(self):
        from app.models import Surface
        from pydantic import ValidationError
        await self.calendar()
        for ref in [[],{},42,'unknown']:
            result=await self.read('get_weather',{'event_ref':ref,'location':'context'})
            self.assertFalse(result.success)
        self.assertEqual(self.agent.weather.calls,[])
        with self.assertRaises(ValidationError):
            Surface(kind='artifact',context={'revision':0,'view':'both','event_valid_ms':300001,'weather_valid_ms':0})

    async def test_system_prefix_stays_fixed_within_tool_loop(self):
        provider=ScriptedProvider([call('get_calendar',{'range':'today'}),call('get_weather',{'location':'context'}),ProviderTurn(text='Conditions are displayed for that event.')])
        self.agent.providers.select=lambda text:provider
        with patch('app.agent.weather_now',lambda:NOW):await self.agent.handle('device','My plan and its weather?','one-loop')
        self.assertEqual(len({request['instructions'] for request in provider.requests}),1)
        self.assertEqual(self.agent.weather.calls,['07306'])

class SharedReferenceSocket(unittest.TestCase):
    def test_speech_touch_correction_dismissal_through_authenticated_packets(self):
        from app import main
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            settings=settings_for(Path(directory)/'socket.db');store=ReminderStore(settings.assistant_database_path)
            agent=FridayAgent(settings,store);synthetic_calendar(agent);agent.weather=SyntheticWeather(settings.weather_locations)
            device='shared-reference-fixture';ref=digest(device,'original-evening',(NOW+timedelta(hours=1,minutes=30)).isoformat())
            provider=ScriptedProvider([
                call('get_calendar',{'range':'today'}),ProviderTurn(text='Dinner is at 6:30 in Jersey City.'),
                call('get_weather',{'location':'context'}),ProviderTurn(text='For that dinner: clear now, about 71 degrees. The next full-hour rain chance is 10%.'),
                call('get_weather',{'event_ref':ref,'location':'10010'}),ProviderTurn(text='Weather context is now 10010. The dinner location stays as listed.'),
                call('get_weather',{'location':'context'}),ProviderTurn(text='The corrected Weather stays at 10010 for this plan.'),
                call('get_weather',{'location':'context'}),ProviderTurn(text='The plan was dismissed. Which event or ZIP should I use?'),
            ])
            agent.providers.select=lambda text:provider
            jobs=AgentJobStore(store.path);hub=DeviceHub();runtime=LocalMusicRuntime(hub)
            hub.scene_observer=main.hub.scene_observer;hub.scene_peer_observer=main.hub.scene_peer_observer
            credentials=DeviceCredentialStore(store.path);transport=InterceptedPlayback();music=MusicService('spotify',transport)
            worker=AgentJobWorker(jobs,agent,hub,timeout_seconds=5,concurrency=1);worker.on_finished=main.work_finished
            speech=SpeechDirector(hub,None)
            values={'settings':settings,'store':store,'agent':agent,'job_store':jobs,'job_worker':worker,'hub':hub,'local_music':runtime,'music':music,'credential_store':credentials,'speech':speech,'transcriber':None,'scene_navigation':{}}
            for key,value in values.items():stack.enter_context(patch.object(main,key,value))
            stack.enter_context(patch.object(main.reminder_scheduler,'store',store))
            stack.enter_context(patch('app.agent.weather_now',lambda:NOW));stack.enter_context(patch('app.agent.datetime',FixtureDateTime));stack.enter_context(patch('app.device_hub.datetime',FixtureDateTime))
            stack.enter_context(patch('asyncio.BaseEventLoop.create_datagram_endpoint',new=AsyncMock(side_effect=OSError('Synthetic rig: discovery disabled'))))
            main.configure_agent_callbacks(agent)
            frames=[];results=[];surfaces=[];errors=[];serial=0
            def receive(socket):
                async def bounded():return await asyncio.wait_for(socket._send_rx.receive(),5)
                message=socket.portal.call(bounded);socket._raise_on_close(message);return json.loads(message['text'])
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/'+device) as socket:
                self.assertEqual(receive(socket)['type'],'hello.required');credentials.open_pairing_window(device,'aaaaaaaa')
                socket.send_json({'type':'hello','device_token':'a'*64,'capabilities':[CAPABILITY,'result_ack','media_updates_v1','media_persistent_v1']})
                ready=receive(socket);self.assertEqual(ready['type'],'ready');frames.append({'direction':'output','packet':ready})
                def send(packet,label=None,error_only=False):
                    nonlocal serial
                    serial+=1;packet['request_id']='shared-reference-'+str(serial)
                    frame={'direction':'input','packet':packet}
                    if label:frame['native_touch_label']=label
                    frames.append(frame);socket.send_json(packet)
                    while True:
                        result=receive(socket);frames.append({'direction':'output','packet':result})
                        if result['type']=='agent.result':
                            self.assertEqual(result['status'],'completed');results.append(result)
                            socket.send_json({'type':'result.ack','job_id':result['job_id'],'delivery_id':result['delivery_id']})
                        if result['type']=='surface':surfaces.append(result['surface'])
                        if result['type']=='error':
                            errors.append(result)
                            if error_only:break
                        if result['type']=='experience.finished' and result.get('experience_id')==packet['request_id']:break
                send({'type':'utterance','text':'How does my evening look?'})
                send({'type':'utterance','text':'And the weather there?'})
                shared=results[-1]['surface'];old=shared['actions'][1]['id']
                for label,index in [('Event',0),('Weather',1),('Weather',1)]:
                    send({'type':'scene.action','action':shared['actions'][index]['id']},label)
                    self.assertEqual(surfaces[-1]['context']['event_ref'],ref)
                    self.assertEqual(surfaces[-1]['context']['weather_ref'],shared['context']['weather_ref'])
                pending=store.request_confirmation(confirmation_id='synthetic-protected',device_id=device,request_id='synthetic-review',actions=[{'name':'create_reminder','arguments':{'text':'Original synthetic review','due_at':(NOW+timedelta(hours=1)).isoformat()},'operation_index':0}],summary='Save the original synthetic review?',expires_at=datetime.now(NOW.tzinfo)+timedelta(minutes=5))
                before=len(agent.weather.calls)
                send({'type':'scene.action','action':shared['actions'][1]['id']},error_only=True)
                self.assertEqual(errors[-1]['code'],'interaction_pending')
                self.assertEqual(len(agent.weather.calls),before)
                self.assertEqual(store.pending_confirmation(device).status,'pending')
                store.finish_confirmation(pending.id,'cancelled')
                send({'type':'utterance','text':'Actually use 10010 for the same plan.'})
                corrected=results[-1]['surface']
                self.assertEqual(corrected['context']['event_ref'],ref)
                self.assertNotEqual(corrected['context']['weather_ref'],shared['context']['weather_ref'])
                send({'type':'utterance','text':'And the weather there again?'})
                followup=results[-1]['surface']
                self.assertEqual(followup['context']['weather_ref'],corrected['context']['weather_ref'])
                self.assertEqual(followup['context']['weather_location'],'10010')
                send({'type':'scene.action','action':followup['actions'][1]['id']},'Weather')
                self.assertEqual(surfaces[-1]['context']['weather_location'],'10010')
                send({'type':'scene.action','action':old})
                self.assertEqual(errors[-1]['code'],'bad_action')
                for action in ['calendar','status']:
                    send({'type':'scene.action','action':action})
                    self.assertEqual(agent.working_context.state(device).location,'10010')
                    send({'type':'scene.action','action':'weather_context'})
                    self.assertEqual(surfaces[-1]['context']['weather_location'],'10010')
                send({'type':'utterance','text':'Dismiss the plan'})
                self.assertTrue(results[-1]['surface']['clear_weather_context'])
                before=len(agent.weather.calls)
                send({'type':'scene.action','action':'weather_context'})
                self.assertEqual(errors[-1]['code'],'bad_action')
                self.assertEqual(len(agent.weather.calls),before)
                send({'type':'scene.action','action':corrected['actions'][1]['id']})
                self.assertEqual(errors[-1]['code'],'bad_action')
                send({'type':'utterance','text':'And the weather there again?'})
                self.assertEqual(results[-1]['surface']['title'],'Which context?')
                self.assertEqual(agent.weather.calls,['07306','10010','10010'])
                self.assertEqual(transport.calls,[]);self.assertEqual(len(results),6);self.assertEqual(provider.turns,[])
            destination=os.environ.get('FRIDAY_SHARED_REFERENCE_FIXTURE')
            if destination:
                path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps({'synthetic_only':True,'actual_model_reasoning':False,'actual_actions':False,'fixture_clock':NOW.isoformat(),'frames':frames,'provider_inputs':provider.requests,'weather_reads':agent.weather.calls,'intercepted_calls':transport.calls,'expected_results':6},indent=2))


class RetainedWeatherRefreshRaces(unittest.TestCase):
    def test_expired_refresh_rechecks_dismissal_revision_navigation_and_protection(self):
        from app import main
        from app.models import Surface
        for interruption in ['dismiss','correction','navigation','protected','already_protected']:
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                settings=settings_for(Path(directory)/'race.db');store=ReminderStore(settings.assistant_database_path)
                agent=FridayAgent(settings,store);snapshot=synthetic_calendar(agent)
                agent.providers.select=lambda text: (_ for _ in ()).throw(AssertionError('No inference'))
                hub=DeviceHub();runtime=LocalMusicRuntime(hub);credentials=DeviceCredentialStore(store.path)
                jobs=AgentJobStore(store.path);worker=AgentJobWorker(jobs,agent,hub,timeout_seconds=5,concurrency=1)
                speech=SpeechDirector(hub,None)
                values={'settings':settings,'store':store,'agent':agent,'job_store':jobs,'job_worker':worker,'hub':hub,'local_music':runtime,'credential_store':credentials,'speech':speech,'transcriber':None,'scene_navigation':{}}
                for key,value in values.items():stack.enter_context(patch.object(main,key,value))
                stack.enter_context(patch.object(main.reminder_scheduler,'store',store))
                stack.enter_context(patch('app.agent.weather_now',lambda:NOW))
                stack.enter_context(patch('asyncio.BaseEventLoop.create_datagram_endpoint',new=AsyncMock(side_effect=OSError('Offline fixture'))))
                main.configure_agent_callbacks(agent)
                device='refresh-race';ref=digest(device,snapshot.events[0].id,snapshot.events[0].starts_at.isoformat())
                agent.working_context.observe_calendar(device,snapshot,Surface(kind='timeline',expires_ms=30000),{'events':[{'id':snapshot.events[0].id,'starts_at':snapshot.events[0].starts_at.isoformat()}]})
                old=Surface(kind='metric',items=[{'label':'OLD_EXPIRED_READING','condition':'Clear'},{'label':'Old forecast'}],weather_valid_ms=1,deadline_at=NOW-timedelta(seconds=1))
                agent.working_context.attach_weather(device,ref,'10010',old)
                before=agent.working_context.state(device).revision
                def protect():
                    return store.request_confirmation('race-review',device,'review-request',[{'name':'create_reminder','arguments':{'text':'Complete original review fact','due_at':(NOW+timedelta(hours=1)).isoformat()}}],'Complete original review fact',datetime.now(NOW.tzinfo)+timedelta(minutes=5))
                if interruption=='already_protected':protect()
                class InterruptedWeather(SyntheticWeather):
                    async def current(self,location='both'):
                        result=await super().current(location)
                        await asyncio.sleep(0)  # deterministic boundary before the late read returns
                        if interruption=='dismiss':agent.working_context.dismiss(device)
                        elif interruption=='correction':agent.working_context.attach_weather(device,ref,'07306',old)
                        elif interruption=='navigation':await main.claim_scene_navigation(device)
                        elif interruption=='protected':protect()
                        return result
                agent.weather=InterruptedWeather(settings.weather_locations)
                with TestClient(main.app) as client,client.websocket_connect('/ws/device/'+device) as socket:
                    self.assertEqual(socket.receive_json()['type'],'hello.required');credentials.open_pairing_window(device,'aaaaaaaa')
                    socket.send_json({'type':'hello','device_token':'a'*64,'capabilities':[CAPABILITY]})
                    self.assertEqual(socket.receive_json()['type'],'ready')
                    socket.send_json({'type':'scene.action','action':'weather_context','request_id':'late-refresh'})
                    packets=[]
                    while True:
                        packet=socket.receive_json();packets.append(packet)
                        if packet['type']=='experience.finished':break
                    self.assertFalse(any(packet.get('surface',{}).get('context') for packet in packets))
                    state=agent.working_context.state(device)
                    if interruption=='dismiss':self.assertIsNone(state.weather)
                    else:
                        self.assertEqual(state.weather.items[0]['label'],'OLD_EXPIRED_READING')
                        self.assertEqual(state.location,'07306' if interruption=='correction' else '10010')
                        self.assertEqual(state.revision,before+(interruption=='correction'))
                    if interruption in {'protected','already_protected'}:
                        self.assertEqual(store.pending_confirmation(device).status,'pending')
                        if interruption=='already_protected':
                            self.assertTrue(any(packet['type']=='error' and packet['code']=='interaction_pending' for packet in packets))
                        else:
                            self.assertTrue(any('Complete original review fact' in packet.get('surface',{}).get('subtitle','') for packet in packets))
                    self.assertEqual(agent.weather.calls,[] if interruption=='already_protected' else ['10010'])
