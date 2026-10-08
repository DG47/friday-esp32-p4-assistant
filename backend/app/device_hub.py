from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect


def weather_packet(message: dict, now: datetime | None = None) -> dict:
    """Rebound a Weather lease at actual send time, including stored replays.

    A queued/old result must not regain its original five-minute lifetime.
    Called inside the existing peer send lock; it changes no ownership rules.
    """
    surface = message.get('surface')
    if not isinstance(surface, dict) or surface.get('kind') != 'metric' or surface.get('title') != 'Weather':
        return message
    lease = surface.get('weather_valid_ms')
    if lease is None:
        return message  # all-unavailable: no measurements to invalidate
    remaining = 1
    try:
        deadline = datetime.fromisoformat(surface['deadline_at'])
        if deadline.tzinfo is not None and type(lease) is int and 1 <= lease <= 300000:
            remaining = max(1, min(lease, int((deadline - (now or datetime.now(timezone.utc))).total_seconds() * 1000)))
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return {**message, 'surface': {**surface, 'weather_valid_ms': remaining}}


@dataclass
class DevicePeer:
    websocket: WebSocket
    capabilities: set[str] = field(default_factory=set)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DeviceHub:
    def __init__(self, send_timeout: float = 3.0) -> None:
        self.connections: dict[str, list[DevicePeer]] = defaultdict(list)
        self.send_timeout = send_timeout
        self.scene_observer = None

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

    async def close_device(self, device_id: str) -> None:
        async def close(peer: DevicePeer) -> None:
            try:
                await asyncio.wait_for(peer.websocket.close(code=1008), self.send_timeout)
            except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
                pass
            finally:
                self.disconnect(device_id, peer.websocket)
        await asyncio.gather(*(close(peer) for peer in list(self.connections.get(device_id, []))))

    async def send_media(self,device_id,websocket,message,allowed) -> int:
        peer=next((p for p in self.connections.get(device_id,[]) if p.websocket is websocket),None)
        if peer is None:return 0
        async def send():
            async with peer.send_lock:
                if not allowed():return 0
                await peer.websocket.send_json(weather_packet(message))
                return 1
        try:return await asyncio.wait_for(send(),self.send_timeout)
        except (RuntimeError,WebSocketDisconnect,OSError,TimeoutError):
            self.disconnect(device_id,websocket);return 0

    async def broadcast(
        self,
        device_id: str,
        message: dict,
        required_capability: str | None = None,
        excluded_capability: str | None = None,
    ) -> int:
        decorate=self.scene_observer(device_id,message) if self.scene_observer else None
        async def send(peer: DevicePeer) -> int:
            try:
                # Bound lock acquisition too: an unresponsive display must not stall
                # reminder delivery or other devices. Serialize concurrent producers.
                async def serialized_send() -> None:
                    async with peer.send_lock:
                        await peer.websocket.send_json(weather_packet(decorate(peer.websocket,message) if decorate else message))
                await asyncio.wait_for(serialized_send(), self.send_timeout)
                return 1
            except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
                self.disconnect(device_id, peer.websocket)
                return 0

        peers = [peer for peer in list(self.connections.get(device_id, []))
                 if (not required_capability or required_capability in peer.capabilities)
                 and (not excluded_capability or excluded_capability not in peer.capabilities)]
        return sum(await asyncio.gather(*(send(peer) for peer in peers)))
