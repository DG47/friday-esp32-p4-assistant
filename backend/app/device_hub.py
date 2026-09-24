from __future__ import annotations

from collections import defaultdict

from fastapi import WebSocket, WebSocketDisconnect


class DeviceHub:
    def __init__(self) -> None:
        self.connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[device_id].append(websocket)

    def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        peers = self.connections.get(device_id, [])
        if websocket in peers:
            peers.remove(websocket)
        if not peers:
            self.connections.pop(device_id, None)

    async def broadcast(self, device_id: str, message: dict) -> None:
        for peer in list(self.connections.get(device_id, [])):
            try:
                await peer.send_json(message)
            except (RuntimeError, WebSocketDisconnect):
                self.disconnect(device_id, peer)
