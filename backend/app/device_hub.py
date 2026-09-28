from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect


@dataclass
class DevicePeer:
    websocket: WebSocket
    capabilities: set[str] = field(default_factory=set)


class DeviceHub:
    def __init__(self) -> None:
        self.connections: dict[str, list[DevicePeer]] = defaultdict(list)

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.register(device_id, websocket)

    def register(
        self,
        device_id: str,
        websocket: WebSocket,
        capabilities: set[str] | None = None,
    ) -> None:
        self.connections[device_id].append(
            DevicePeer(websocket, capabilities or set())
        )

    def set_capabilities(
        self, device_id: str, websocket: WebSocket, capabilities: set[str]
    ) -> None:
        for peer in self.connections.get(device_id, []):
            if peer.websocket is websocket:
                peer.capabilities = capabilities
                return

    def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        peers = self.connections.get(device_id, [])
        self.connections[device_id] = [
            peer for peer in peers if peer.websocket is not websocket
        ]
        if not self.connections[device_id]:
            self.connections.pop(device_id, None)

    def has_capability(self, device_id: str, capability: str) -> bool:
        return any(
            capability in peer.capabilities
            for peer in self.connections.get(device_id, [])
        )

    def peer_has_capability(
        self, device_id: str, websocket: WebSocket, capability: str
    ) -> bool:
        return any(
            peer.websocket is websocket and capability in peer.capabilities
            for peer in self.connections.get(device_id, [])
        )

    def device_ids(self) -> list[str]:
        return list(self.connections)

    async def broadcast(
        self,
        device_id: str,
        message: dict,
        required_capability: str | None = None,
    ) -> int:
        delivered = 0
        for peer in list(self.connections.get(device_id, [])):
            if required_capability and required_capability not in peer.capabilities:
                continue
            try:
                await peer.websocket.send_json(message)
                delivered += 1
            except (RuntimeError, WebSocketDisconnect):
                self.disconnect(device_id, peer.websocket)
        return delivered
