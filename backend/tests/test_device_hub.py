import unittest

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
        await hub.broadcast("friday-p3a", {"type": "state", "state": "thinking"})

        self.assertTrue(browser.accepted)
        self.assertTrue(device.accepted)
        self.assertEqual(browser.messages, device.messages)
        self.assertEqual(device.messages[0]["state"], "thinking")

        hub.disconnect("friday-p3a", device)
        self.assertEqual(hub.connections["friday-p3a"], [browser])


if __name__ == "__main__":
    unittest.main()
