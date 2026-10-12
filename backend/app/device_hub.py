from __future__ import annotations

import asyncio
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect
from .experience import Experience, CAPABILITY, PRESENTATION


def weather_packet(message: dict, now: datetime | None = None) -> dict:
    """Rebound a Weather lease at actual send time, including stored replays.

    A queued/old result must not regain its original five-minute lifetime.
    Called inside the existing peer send lock; it changes no ownership rules.
    """
    surface = message.get('surface')
    if isinstance(surface, dict) and isinstance(surface.get('context'), dict):
        context = dict(surface['context'])
        for name in ('event', 'weather'):
            remaining = 0
            try:
                lease = context[name+'_valid_ms']
                deadline = datetime.fromisoformat(context[name+'_deadline_at'])
                if deadline.tzinfo is not None and type(lease) is int and 0 <= lease <= 300000:
                    remaining = max(0, min(lease, int((deadline-(now or datetime.now(timezone.utc))).total_seconds()*1000)))
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
            context[name+'_valid_ms'] = remaining
        return {**message, 'surface': {**surface, 'context': context}}
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
    key: str = field(default_factory=lambda: secrets.token_hex(16))


class DeviceHub:
    def __init__(self, send_timeout: float = 3.0) -> None:
        self.connections: dict[str, list[DevicePeer]] = defaultdict(list)
        self.send_timeout = send_timeout
        self.scene_observer = None
        self.scene_peer_observer = None
        self.experience = Experience()

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.register(device_id, websocket)

    def register(
        self,
        device_id: str,
        websocket: WebSocket,
        capabilities: set[str] | None = None,
    ) -> None:
        if capabilities and CAPABILITY in capabilities:
            self.experience.session(device_id, reset=not self.connections.get(device_id))
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

    def peer_key(self, device_id, websocket):
        return next((p.key for p in self.connections.get(device_id, []) if p.websocket is websocket), None)

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
        scoped = self.experience.envelope(device_id, message) if CAPABILITY in peer.capabilities else message
        async def send():
            async with peer.send_lock:
                if not allowed():return 0
                packet = self.experience.for_peer(device_id, scoped, peer.key) if CAPABILITY in peer.capabilities else message
                if packet is None:return 0
                await peer.websocket.send_json(weather_packet(packet))
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
        negotiated = self.has_capability(device_id, CAPABILITY)
        scoped = self.experience.envelope(device_id, message) if negotiated else message
        current = self.experience.current_packet(device_id, scoped)
        historical = negotiated and (scoped.get("experience_historical") or ("experience_id" in scoped and not current))
        decorate = None
        observed = False
        async def send(peer: DevicePeer) -> int:
            try:
                # Bound lock acquisition too: an unresponsive display must not stall
                # reminder delivery or other devices. Serialize concurrent producers.
                async def serialized_send() -> int:
                    nonlocal decorate, observed
                    async with peer.send_lock:
                        packet = self.experience.for_peer(device_id, scoped, peer.key) if CAPABILITY in peer.capabilities else message
                        if packet is None:return 0
                        # Observer creation mutates Media ownership too. It must
                        # occur after the final freshness check, never while queued.
                        stale = negotiated and not self.experience.current_packet(device_id, scoped) and scoped.get("type") in PRESENTATION
                        legacy_stale = negotiated and CAPABILITY not in peer.capabilities and (historical or stale)
                        if legacy_stale:
                            peer_observer = self.scene_peer_observer
                            if peer_observer:packet=peer_observer(device_id,peer.websocket,message)(peer.websocket,packet)
                        if self.scene_observer and not historical and not stale and not observed:
                            decorate = self.scene_observer(device_id, message)
                            observed = True
                        if decorate and not packet.get("experience_historical"):
                            decorated=decorate(peer.websocket,packet)
                            packet=decorated
                        await peer.websocket.send_json(weather_packet(packet))
                        return 1
                return await asyncio.wait_for(serialized_send(), self.send_timeout)
            except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
                self.disconnect(device_id, peer.websocket)
                return 0

        peers = [peer for peer in list(self.connections.get(device_id, []))
                 if (not required_capability or required_capability in peer.capabilities)
                 and (not excluded_capability or excluded_capability not in peer.capabilities)]
        return sum(await asyncio.gather(*(send(peer) for peer in peers)))
