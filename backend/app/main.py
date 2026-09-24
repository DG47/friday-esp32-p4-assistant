from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import FridayAgent
from .config import get_settings
from .db import ReminderStore
from .device_hub import DeviceHub


settings = get_settings()
store = ReminderStore(settings.assistant_database_path)
agent = FridayAgent(settings, store)
static_dir = Path(__file__).parent / "static"

app = FastAPI(title="Friday Assistant", version="0.1.0")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


hub = DeviceHub()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": settings.assistant_provider}


@app.get("/api/calendar/today")
async def calendar_today() -> list[dict]:
    return [event.model_dump(mode="json") for event in agent.calendar.today()]


@app.get("/api/reminders")
async def reminders() -> list[dict]:
    return [item.model_dump(mode="json") for item in store.pending()]


@app.websocket("/ws/device/{device_id}")
async def device_socket(websocket: WebSocket, device_id: str) -> None:
    await hub.connect(device_id, websocket)
    await websocket.send_json(
        {
            "type": "ready",
            "protocol": 1,
            "device_id": device_id,
            "state": "idle",
            "surface": {"kind": "ambient", "title": "Friday"},
        }
    )

    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")

            if message_type == "hello":
                await hub.broadcast(device_id, {"type": "state", "state": "idle"})
            elif message_type == "activate":
                await hub.broadcast(device_id, {"type": "state", "state": "listening"})
            elif message_type == "utterance":
                text = str(message.get("text", "")).strip()
                if not text:
                    await websocket.send_json(
                        {"type": "error", "code": "empty_utterance", "message": "I heard a truly immaculate silence."}
                    )
                    continue

                await hub.broadcast(device_id, {"type": "state", "state": "thinking"})
                reply = await agent.handle(device_id, text)
                if reply.tool:
                    await hub.broadcast(
                        device_id,
                        {"type": "tool.activity", "name": reply.tool, "status": "complete"}
                    )
                await hub.broadcast(
                    device_id,
                    {"type": "surface", "surface": reply.surface.model_dump(mode="json")}
                )
                await hub.broadcast(
                    device_id,
                    {
                        "type": "assistant.text",
                        "text": reply.text,
                        "provider": reply.provider,
                    }
                )
                await hub.broadcast(device_id, {"type": "state", "state": "speaking"})
                await asyncio.sleep(min(0.9, max(0.25, len(reply.text) / 180)))
                await hub.broadcast(device_id, {"type": "state", "state": "idle"})
            else:
                await websocket.send_json(
                    {"type": "error", "code": "bad_message", "message": f"Unsupported message type: {message_type}"}
                )
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(device_id, websocket)
