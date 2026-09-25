from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import FridayAgent
from .audio_session import AudioSession, AudioSessionError
from .config import get_settings
from .db import ReminderStore
from .device_hub import DeviceHub
from .stt import TranscriptionError, create_transcriber
from .wake_session import WakeAudioSession, contains_wake_word, pcm_peak


settings = get_settings()
store = ReminderStore(settings.assistant_database_path)
agent = FridayAgent(settings, store)
transcriber = create_transcriber(settings)
static_dir = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)

app = FastAPI(title="Friday Assistant", version="0.1.0")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


hub = DeviceHub()


async def handle_utterance(device_id: str, text: str) -> None:
    await hub.broadcast(device_id, {"type": "state", "state": "thinking"})
    reply = await agent.handle(device_id, text)
    if reply.tool:
        await hub.broadcast(
            device_id,
            {"type": "tool.activity", "name": reply.tool, "status": "complete"},
        )
    await hub.broadcast(
        device_id,
        {"type": "surface", "surface": reply.surface.model_dump(mode="json")},
    )
    await hub.broadcast(
        device_id,
        {
            "type": "assistant.text",
            "text": reply.text,
            "provider": reply.provider,
        },
    )
    await hub.broadcast(device_id, {"type": "state", "state": "speaking"})
    await asyncio.sleep(min(0.9, max(0.25, len(reply.text) / 180)))
    await hub.broadcast(device_id, {"type": "state", "state": "idle"})


async def send_error(websocket: WebSocket, code: str, message: str) -> None:
    logger.warning("Device request failed [%s]: %s", code, message)
    await websocket.send_json({"type": "error", "code": code, "message": message})


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
    audio = AudioSession(max_seconds=settings.stt_max_seconds)
    wake_audio = WakeAudioSession()
    wake_enabled = False
    wake_probe_task: asyncio.Task[None] | None = None

    async def detect_wake_word(pcm: bytes) -> None:
        nonlocal wake_enabled
        if transcriber is None or pcm_peak(pcm) < 1000:
            return
        try:
            wake_transcribe = getattr(transcriber, "transcribe_wake", transcriber.transcribe)
            text = (await wake_transcribe(pcm, wake_audio.audio_format)).strip()
        except TranscriptionError as error:
            logger.debug("Wake-word transcription failed: %s", error)
            return
        logger.debug("Wake-word probe for %s: %r", device_id, text)
        if wake_enabled and contains_wake_word(text):
            wake_enabled = False
            wake_audio.reset()
            logger.info("Wake word detected for %s: %s", device_id, text)
            await hub.broadcast(
                device_id, {"type": "wake.detected", "wake_word": "Friday"}
            )

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
            event = await websocket.receive()
            if event["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(event.get("code", 1000))

            binary = event.get("bytes")
            if binary is not None:
                if audio.active:
                    try:
                        audio.append(binary)
                    except AudioSessionError as error:
                        await send_error(websocket, "bad_audio", str(error))
                        await hub.broadcast(device_id, {"type": "state", "state": "idle"})
                elif wake_enabled:
                    wake_audio.append(binary)
                    if wake_probe_task is None or wake_probe_task.done():
                        probe = wake_audio.take_probe()
                        if probe is not None:
                            wake_probe_task = asyncio.create_task(detect_wake_word(probe))
                else:
                    # A few idle frames can race with connection setup or a wake event.
                    # They are safe to discard until the next explicit audio mode begins.
                    pass
                continue

            try:
                message = json.loads(event.get("text") or "")
            except (json.JSONDecodeError, TypeError):
                await send_error(websocket, "bad_message", "Expected a JSON control message")
                continue
            if not isinstance(message, dict):
                await send_error(websocket, "bad_message", "Expected a JSON object")
                continue
            message_type = message.get("type")

            if message_type == "hello":
                wake_audio.reset()
                wake_enabled = transcriber is not None
                await hub.broadcast(device_id, {"type": "state", "state": "idle"})
            elif message_type == "activate":
                wake_enabled = False
                wake_audio.reset()
                await hub.broadcast(device_id, {"type": "state", "state": "listening"})
            elif message_type == "wake.start":
                if transcriber is None:
                    await send_error(
                        websocket, "wake_unavailable", "Local speech transcription is disabled"
                    )
                    continue
                wake_audio.reset()
                wake_enabled = True
            elif message_type == "utterance":
                text = str(message.get("text", "")).strip()
                if not text:
                    await send_error(websocket, "empty_utterance", "I heard a truly immaculate silence.")
                    continue
                await handle_utterance(device_id, text)
            elif message_type == "audio.start":
                try:
                    wake_enabled = False
                    wake_audio.reset()
                    audio.start(
                        str(message.get("format", "")),
                        int(message.get("sample_rate", 0)),
                        int(message.get("channels", 0)),
                    )
                except (AudioSessionError, TypeError, ValueError) as error:
                    await send_error(websocket, "bad_audio", str(error))
                    continue
                await hub.broadcast(device_id, {"type": "state", "state": "listening"})
            elif message_type == "audio.end":
                try:
                    pcm, audio_format = audio.finish()
                    minimum_bytes = audio_format.sample_rate * audio_format.channels * 2 // 5
                    if len(pcm) < minimum_bytes:
                        raise AudioSessionError("Audio turn was too short")
                    if transcriber is None:
                        raise TranscriptionError("Speech transcription is disabled")
                    await hub.broadcast(device_id, {"type": "state", "state": "thinking"})
                    text = (await transcriber.transcribe(pcm, audio_format)).strip()
                    if not text:
                        raise TranscriptionError("No speech was recognized")
                except (AudioSessionError, TranscriptionError) as error:
                    await send_error(websocket, "transcription_failed", str(error))
                    await hub.broadcast(device_id, {"type": "state", "state": "idle"})
                    continue
                await hub.broadcast(
                    device_id,
                    {"type": "transcript", "text": text, "provider": transcriber.name},
                )
                await handle_utterance(device_id, text)
            else:
                await send_error(
                    websocket, "bad_message", f"Unsupported message type: {message_type}"
                )
    except WebSocketDisconnect:
        pass
    finally:
        if wake_probe_task is not None and not wake_probe_task.done():
            wake_probe_task.cancel()
        hub.disconnect(device_id, websocket)
