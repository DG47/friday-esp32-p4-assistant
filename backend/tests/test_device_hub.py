import unittest
from anyio import ClosedResourceError, BrokenResourceError, EndOfStream

from app.device_hub import DeviceHub


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        self.messages.append(message)


class DeviceHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_broadcasts_to_browser_and_physical_device(self):
        hub = DeviceHub()
        browser = FakeWebSocket()
        device = FakeWebSocket()

        await hub.connect("friday-p3a", browser)
        await hub.connect("friday-p3a", device)
        hub.set_capabilities("friday-p3a", device, {"reminder_ack"})
        await hub.broadcast("friday-p3a", {"type": "state", "state": "thinking"})

        self.assertTrue(browser.accepted)
        self.assertTrue(device.accepted)
        self.assertEqual(browser.messages, device.messages)
        self.assertEqual(device.messages[0]["state"], "thinking")

        await hub.broadcast(
            "friday-p3a",
            {"type": "reminder.alert"},
            required_capability="reminder_ack",
        )
        self.assertEqual(len(browser.messages), 1)
        self.assertEqual(device.messages[-1]["type"], "reminder.alert")

        hub.disconnect("friday-p3a", device)
        self.assertEqual(len(hub.connections["friday-p3a"]), 1)
        self.assertIs(hub.connections["friday-p3a"][0].websocket, browser)


    async def test_closed_stream_peer_is_removed_without_stopping_other_delivery(self):
        for failure in (ClosedResourceError,BrokenResourceError,EndOfStream):
            with self.subTest(failure=failure.__name__):
                hub=DeviceHub();healthy=FakeWebSocket()
                class Closed(FakeWebSocket):
                    async def send_json(self,message):raise failure()
                closed=Closed()
                await hub.connect('synthetic-device',closed)
                await hub.connect('synthetic-device',healthy)
                self.assertEqual(await hub.broadcast('synthetic-device',{'type':'state','state':'idle'}),1)
                self.assertEqual([p.websocket for p in hub.connections['synthetic-device']],[healthy])
                self.assertEqual(len(healthy.messages),1)
                self.assertEqual(await hub.broadcast('synthetic-device',{'type':'state','state':'thinking'}),1)
                self.assertEqual(len(healthy.messages),2)


    async def test_closed_stream_peer_can_be_closed(self):
        for failure in (ClosedResourceError,BrokenResourceError,EndOfStream):
            with self.subTest(failure=failure.__name__):
                hub=DeviceHub()
                class Closed(FakeWebSocket):
                    async def close(self,code):raise failure()
                await hub.connect('synthetic-device',Closed())
                await hub.close_device('synthetic-device')
                self.assertEqual(hub.device_ids(),[])
    async def test_closed_stream_targeted_media_peer_is_removed(self):
        for failure in (ClosedResourceError,BrokenResourceError,EndOfStream):
            with self.subTest(failure=failure.__name__):
                hub=DeviceHub()
                class Closed(FakeWebSocket):
                    async def send_json(self,message):raise failure()
                peer=Closed();await hub.connect('synthetic-device',peer)
                self.assertEqual(await hub.send_media('synthetic-device',peer,{'type':'state','state':'idle'},lambda:True),0)
                self.assertEqual(hub.device_ids(),[])


if __name__ == "__main__":
    unittest.main()
