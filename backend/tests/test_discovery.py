import json
import unittest

from app.discovery import FridayDiscoveryProtocol


class FakeTransport:
    def __init__(self):
        self.packets = []

    def sendto(self, data, address):
        self.packets.append((data, address))


class FridayDiscoveryProtocolTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.protocol = FridayDiscoveryProtocol(
            public_port=8000, resolver=lambda _: "192.168.4.38"
        )
        self.protocol.connection_made(self.transport)

    def test_returns_reachable_websocket_url(self):
        self.protocol.datagram_received(
            b"FRIDAY_DISCOVER_V1 friday-p3a", ("192.168.4.46", 49152)
        )

        data, address = self.transport.packets[0]
        self.assertEqual(address, ("192.168.4.46", 49152))
        self.assertEqual(
            json.loads(data),
            {
                "protocol": 1,
                "ws_url": "wss://192.168.4.38:8000/ws/device/friday-p3a",
            },
        )

    def test_ignores_invalid_discovery_packets(self):
        self.protocol.datagram_received(b"not-friday", ("192.168.4.46", 49152))
        self.protocol.datagram_received(
            b"FRIDAY_DISCOVER_V1 invalid/device", ("192.168.4.46", 49152)
        )

        self.assertEqual(self.transport.packets, [])


if __name__ == "__main__":
    unittest.main()
