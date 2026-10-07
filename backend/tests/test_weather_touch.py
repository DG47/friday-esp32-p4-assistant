import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
import httpx
from app import main
from app.agent import FridayAgent
from app.config import Settings
from app.models import Surface, SurfaceAction
from app.tools.music_runtime import LocalMusicRuntime
from app.tools.weather import WeatherLocations
from tests import test_device_socket
import unittest
from tests.test_weather_locations import LOCATIONS
from tests.test_weather import payload


def weather_agent(store, response):
    settings=Settings(_env_file=None,assistant_provider='mock',weather_provider='open-meteo',weather_locations=LOCATIONS)
    agent=FridayAgent(settings,store)
    agent.weather=WeatherLocations(settings.weather_locations,httpx.MockTransport(response))
    return agent


def receive_surface(socket):
    while True:
        packet=socket.receive_json()
        if packet['type'] in {'surface','error'}:
            return packet


class WeatherTouchTests(unittest.TestCase):
    setUp = test_device_socket.DeviceSocketTests.setUp
    tearDown = test_device_socket.DeviceSocketTests.tearDown
    authenticate = test_device_socket.DeviceSocketTests.authenticate

    def test_menu_fifth_action_and_weather_dismiss_reopen_without_model(self):
        seen=[]
        agent=weather_agent(self.reminder_store,lambda req:(seen.append(req),httpx.Response(200,json=payload()))[1])
        with patch('app.main.agent',agent),patch.object(agent.providers,'select',side_effect=AssertionError('No model allowed')):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/touch-weather') as socket:
                self.authenticate(socket,'touch-weather')
                for cycle in range(10):
                    socket.send_json({'type':'scene.action','action':'status','request_id':f'menu-{cycle}'})
                    menu=receive_surface(socket)['surface']
                    self.assertEqual([item['id'] for item in menu['actions']],['memories','reminders','calendar','music','weather'])
                    socket.send_json({'type':'scene.action','action':'weather','request_id':f'weather-{cycle}'})
                    surface=receive_surface(socket)['surface']
                    self.assertEqual(surface['title'],'Weather')
                    self.assertEqual([item['time'] for item in surface['items'] if item['time']],['07306','10010'])
                    self.assertNotIn('Open-Meteo',json.dumps(surface));self.assertNotIn('UTC',json.dumps(surface))
                    socket.send_json({'type':'scene.action','action':'dismiss','request_id':f'dismiss-{cycle}'})
                    self.assertEqual(receive_surface(socket)['surface']['kind'],'ambient')
                self.assertEqual(len(seen),2)

    def test_protected_confirmation_busy_memory_reminder_and_recovery(self):
        seen=[]
        agent=weather_agent(self.reminder_store,lambda req:(seen.append(req),httpx.Response(200,json=payload()))[1])
        runtime=LocalMusicRuntime(main.hub,enabled=True,blocked=lambda device:bool(self.reminder_store.pending_confirmation(device)))
        packets=[]
        with patch('app.main.agent',agent),patch('app.main.local_music',runtime),patch('app.main.hub.scene_observer',runtime.interrupt_message):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/weather-owned') as socket:
                self.authenticate(socket,'weather-owned')
                pending=self.reminder_store.request_confirmation('weather-confirm','weather-owned','fixture-request',
                    [{'name':'complete_reminder','arguments':{'reminder_id':1}}],'Complete the synthetic reminder?',datetime.now(timezone.utc)+timedelta(minutes=5))
                for _ in range(3):
                    socket.send_json({'type':'scene.action','action':'weather','request_id':'weather-pending'})
                    packet=receive_surface(socket);packets.append(packet)
                    self.assertEqual(packet['surface']['title'],'Confirmation required')
                    self.assertEqual(self.reminder_store.pending_confirmation('weather-owned').id,pending.id)
                self.assertEqual(seen,[])
                for attribute in ('busy','reminder_devices','protected'):
                    getattr(runtime,attribute).add('weather-owned')
                    socket.send_json({'type':'scene.action','action':'weather','request_id':f'weather-{attribute}'})
                    packet=receive_surface(socket);packets.append(packet)
                    # protected ownership alone restores the pending confirmation;
                    # busy/reminder ownership retains the existing surface via notice.
                    if attribute=='protected':self.assertEqual(packet['surface']['title'],'Confirmation required')
                    else:self.assertEqual(packet['code'],'interaction_pending');self.assertIn('Weather',packet['message'])
                    getattr(runtime,attribute).discard('weather-owned')
                from types import SimpleNamespace
                with patch.object(agent.memory,'store',SimpleNamespace(pending_confirmation=lambda device:{'token':'f'*24})):
                    socket.send_json({'type':'scene.action','action':'weather','request_id':'weather-memory'})
                    packet=receive_surface(socket);packets.append(packet)
                    self.assertEqual(packet['code'],'interaction_pending');self.assertIn('memory review',packet['message'])
                self.assertEqual(self.reminder_store.pending_confirmation('weather-owned').id,pending.id)
                self.assertEqual(seen,[])
                self.reminder_store.finish_confirmation(pending.id,'cancelled')
                socket.send_json({'type':'scene.action','action':'weather','request_id':'weather-recovery'})
                packet=receive_surface(socket);packets.append(packet)
                self.assertEqual(packet['surface']['title'],'Weather');self.assertEqual(len(seen),2)
        fixture=os.environ.get('FRIDAY_WEATHER_TOUCH_FIXTURE')
        if fixture:Path(fixture).write_text(json.dumps(packets))

    def test_protection_appearing_during_fetch_prevents_weather_publication(self):
        created=[]
        def response(request):
            if not created:
                pending=self.reminder_store.request_confirmation('during-weather','during-weather','fixture-request',
                    [{'name':'complete_reminder','arguments':{'reminder_id':1}}],'Keep this new request pending.',datetime.now(timezone.utc)+timedelta(minutes=5))
                created.append(pending.id)
            return httpx.Response(200,json=payload())
        agent=weather_agent(self.reminder_store,response)
        with patch('app.main.agent',agent):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/during-weather') as socket:
                self.authenticate(socket,'during-weather')
                socket.send_json({'type':'scene.action','action':'weather','request_id':'weather-delayed'})
                packet=receive_surface(socket)
                self.assertEqual(packet['surface']['title'],'Confirmation required')
                self.assertEqual(self.reminder_store.pending_confirmation('during-weather').id,created[0])

    def test_weather_failure_stays_honest_and_recovers(self):
        failed=True
        agent=weather_agent(self.reminder_store,lambda req:httpx.Response(503) if failed else httpx.Response(200,json=payload()))
        with patch('app.main.agent',agent):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/weather-retry') as socket:
                self.authenticate(socket,'weather-retry')
                socket.send_json({'type':'scene.action','action':'weather','request_id':'failed'})
                self.assertIn('unavailable',receive_surface(socket)['surface']['subtitle'])
                failed=False
                socket.send_json({'type':'scene.action','action':'weather','request_id':'retry'})
                self.assertNotIn('unavailable',receive_surface(socket)['surface']['subtitle'])

    def test_fifth_action_is_reserved_for_launcher(self):
        actions=[SurfaceAction(id=action,label=action) for action in ['memories','reminders','calendar','music','weather']]
        self.assertEqual(len(Surface(kind='metric',actions=actions).actions),5)
        with self.assertRaises(ValidationError):Surface(kind='message',actions=actions)
        with self.assertRaises(ValidationError):Surface(kind='metric',actions=[*actions[:4],SurfaceAction(id='confirm',label='Confirm')])

    def test_disabled_weather_touch_is_honest_without_model_or_provider(self):
        agent=FridayAgent(Settings(_env_file=None,assistant_provider='mock'),self.reminder_store)
        self.assertIsNone(agent.weather)
        with patch('app.main.agent',agent),patch.object(agent.providers,'select',side_effect=AssertionError('No model allowed')):
            with TestClient(main.app) as client,client.websocket_connect('/ws/device/weather-disabled') as socket:
                self.authenticate(socket,'weather-disabled')
                socket.send_json({'type':'scene.action','action':'weather','request_id':'disabled'})
                surface=receive_surface(socket)['surface']
                self.assertEqual(surface['title'],'Weather unavailable')
                self.assertIn('not connected',surface['subtitle'])

    def test_two_authenticated_peers_supersede_delayed_weather(self):
        import asyncio
        import copy
        import threading
        for newer_action in ('dismiss','status','reminders','calendar','music','memories','weather'):
            with self.subTest(newer_action=newer_action):
                device='weather-race-'+newer_action
                agent=weather_agent(self.reminder_store,lambda req:httpx.Response(200,json=payload()))
                entered,release=threading.Event(),threading.Event()
                original_read=agent._execute_read_tool
                calls=0
                async def held_read(*args,**kwargs):
                    nonlocal calls
                    calls+=1
                    if calls==1:
                        entered.set()
                        await asyncio.to_thread(release.wait,5)
                        if not release.is_set():raise AssertionError('Test release timed out')
                    return await original_read(*args,**kwargs)
                observed=[]
                original_broadcast=main.hub.broadcast
                async def observe(target,packet,*args,**kwargs):
                    if target==device:observed.append(copy.deepcopy(packet))
                    return await original_broadcast(target,packet,*args,**kwargs)
                with patch('app.main.agent',agent),patch.object(agent,'_execute_read_tool',side_effect=held_read),patch.object(main.hub,'broadcast',side_effect=observe):
                    with TestClient(main.app) as client,client.websocket_connect('/ws/device/'+device) as first,client.websocket_connect('/ws/device/'+device) as second:
                        self.authenticate(first,device)
                        self.authenticate(second,device)
                        self.assertEqual(first.receive_json()['type'],'state')
                        first.send_json({'type':'scene.action','action':'weather','request_id':'old-weather'})
                        self.assertTrue(entered.wait(3))
                        second.send_json({'type':'scene.action','action':newer_action,'request_id':'new-navigation'})
                        newer=receive_surface(second)
                        self.assertEqual(newer['type'],'surface')
                        self.assertEqual(receive_surface(first)['surface'],newer['surface'])
                        boundary=len(observed)
                        release.set()
                        # FIFO on the first authenticated peer is a completion
                        # barrier: this menu runs only after its held Weather turn.
                        first.send_json({'type':'scene.action','action':'status','request_id':'completion-barrier'})
                        after=receive_surface(first)
                        self.assertEqual(after['surface']['title'],'At your service')
                        self.assertEqual(receive_surface(second)['surface']['title'],'At your service')
                        late=observed[boundary:]
                        self.assertFalse(any(packet.get('type')=='tool.activity' and packet.get('name')=='get_weather' for packet in late))
                        self.assertFalse(any(packet.get('surface',{}).get('title')=='Weather' for packet in late))
