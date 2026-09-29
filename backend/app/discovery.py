from __future__ import annotations

import asyncio
import json
import re
import socket
from collections.abc import Callable
from urllib.parse import quote


DISCOVERY_PREFIX = "FRIDAY_DISCOVER_V1 "
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def local_ip_for_peer(peer_ip: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((peer_ip, 9))
        return str(probe.getsockname()[0])


class FridayDiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        public_port: int,
        public_scheme: str = "wss",
        resolver: Callable[[str], str] = local_ip_for_peer,
        public_host: str | None = None,
    ) -> None:
        self.public_port = public_port
        self.public_scheme = public_scheme
        self.resolver = resolver
        self.public_host = public_host
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        try:
            message = data.decode("ascii")
        except UnicodeDecodeError:
            return
        if not message.startswith(DISCOVERY_PREFIX):
            return
        device_id = message[len(DISCOVERY_PREFIX) :].strip()
        if not DEVICE_ID_PATTERN.fullmatch(device_id):
            return
        try:
            host = self.public_host or self.resolver(address[0])
        except OSError:
            return
        response = json.dumps(
            {
                "protocol": 1,
                "ws_url": (
                    f"{self.public_scheme}://{host}:{self.public_port}/ws/device/"
                    f"{quote(device_id, safe='')}"
                ),
            },
            separators=(",", ":"),
        ).encode("ascii")
        if self.transport is not None:
            self.transport.sendto(response, address)
