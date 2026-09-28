from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import FridayAgent
from .agent_jobs import AgentJobStore, AgentJobWorker
from .audio_session import AudioSession, AudioSessionError
from .config import get_settings
from .db import ReminderStore
from .device_auth import TOKEN_PATTERN, DeviceCredentialStore
from .device_hub import DeviceHub
from .discovery import FridayDiscoveryProtocol
from .reminder_scheduler import ReminderScheduler
from .stt import TranscriptionError, create_transcriber
from .wake_session import WakeAudioSession, contains_wake_word, pcm_peak


settings = get_settings()
store = ReminderStore(settings.assistant_database_path)
agent = FridayAgent(settings, store)
job_store = AgentJobStore(settings.assistant_database_path)
credential_store = DeviceCredentialStore(settings.assistant_database_path)
transcriber = create_transcriber(settings)
static_dir = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)
hub = DeviceHub()
reminder_scheduler = ReminderScheduler(
    store,
    hub,
    poll_seconds=settings.assistant_reminder_poll_seconds,
    retry_seconds=settings.assistant_reminder_retry_seconds,
    max_attempts=settings.assistant_reminder_max_attempts,
)
job_worker = AgentJobWorker(
    job_store, agent, hub, timeout_seconds=settings.assistant_job_timeout_seconds
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    discovery_transport: asyncio.DatagramTransport | None = None
    try:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: FridayDiscoveryProtocol(
                settings.assistant_public_port,
                settings.assistant_public_scheme,
            ),
            local_addr=("0.0.0.0", settings.assistant_discovery_port),
            allow_broadcast=True,
        )
        discovery_transport = transport  # type: ignore[assignment]
        logger.info(
            "Friday discovery listening on UDP %s",
            settings.assistant_discovery_port,
        )
    except OSError as error:
        logger.warning("Friday discovery unavailable: %s", error)
    tasks = [
        asyncio.create_task(
            reminder_scheduler.run(), name="friday-reminder-scheduler"
        ),
        asyncio.create_task(job_worker.run(), name="friday-agent-job-worker"),
    ]
    try:
        yield
    finally:
        if discovery_transport is not None:
            discovery_transport.close()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Friday Assistant", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def is_local_host(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def simulator_token_valid(token: str) -> bool:
    configured = settings.assistant_simulator_token
    return (
        bool(TOKEN_PATTERN.fullmatch(configured))
        and bool(TOKEN_PATTERN.fullmatch(token))
        and secrets.compare_digest(token.lower(), configured.lower())
    )


def admin_token_valid(token: str) -> bool:
    configured = settings.assistant_admin_token
    return (
        bool(TOKEN_PATTERN.fullmatch(configured))
        and bool(TOKEN_PATTERN.fullmatch(token))
        and secrets.compare_digest(token.lower(), configured.lower())
    )


def require_local_admin(request: Request) -> None:
    if not is_local_host(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="Administration is loopback-only")
    if not admin_token_valid(request.headers.get("x-friday-admin-token", "")):
        raise HTTPException(status_code=401, detail="Valid Friday admin token required")


async def handle_utterance(
    device_id: str, text: str, request_id: str | None = None
) -> str:
    job = job_store.enqueue(device_id, text, request_id=request_id)
    await hub.broadcast(device_id, {"type": "state", "state": "thinking"})
    await hub.broadcast(
        device_id,
        {"type": "job.accepted", "job_id": job.id},
    )
    return job.id


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


@app.post("/api/devices/pairing-window")
async def open_pairing_window(request: Request) -> dict[str, str]:
    require_local_admin(request)
    if request.headers.get("x-friday-pairing") != "open":
        raise HTTPException(status_code=400, detail="Pairing confirmation header required")
    expires_at = credential_store.open_pairing_window()
    logger.info("Device pairing window opened until %s", expires_at.isoformat())
    return {"status": "pairing", "expires_at": expires_at.isoformat()}


def require_local_device_action(request: Request, action: str) -> None:
    require_local_admin(request)
    if request.headers.get("x-friday-device-action") != action:
        raise HTTPException(status_code=400, detail="Device action confirmation required")


@app.post("/api/devices/{device_id}/rotate")
async def rotate_device_identity(device_id: str, request: Request) -> dict[str, str]:
    require_local_device_action(request, "rotate")
    if not credential_store.is_paired(device_id):
        raise HTTPException(status_code=404, detail="Device is not paired")
    recipients = await hub.broadcast(
        device_id,
        {"type": "identity.rotate"},
        required_capability="identity_rotate",
    )
    if recipients == 0:
        raise HTTPException(status_code=409, detail="Device must be online to rotate")
    credential_store.revoke(device_id)
    expires_at = credential_store.open_pairing_window()
    logger.warning("Rotating identity for device %s", device_id)
    return {"status": "rotating", "expires_at": expires_at.isoformat()}


@app.post("/api/devices/{device_id}/revoke")
async def revoke_device_identity(device_id: str, request: Request) -> dict[str, str]:
    require_local_device_action(request, "revoke")
    if not credential_store.is_paired(device_id):
        raise HTTPException(status_code=404, detail="Device is not paired")
    await hub.broadcast(
        device_id,
        {"type": "identity.revoked"},
        required_capability="identity_rotate",
    )
    credential_store.revoke(device_id)
    logger.warning("Revoked identity for device %s", device_id)
    return {"status": "revoked"}


@app.websocket("/ws/device/{device_id}")
async def device_socket(websocket: WebSocket, device_id: str) -> None:
    audio = AudioSession(max_seconds=settings.stt_max_seconds)
    wake_audio = WakeAudioSession()
    wake_enabled = False
    wake_probe_task: asyncio.Task[None] | None = None
    registered = False

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

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "hello.required",
            "protocol": 1,
            "device_id": device_id,
        }
    )

    try:
        while True:
            event = await websocket.receive()
            if event["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(event.get("code", 1000))

            binary = event.get("bytes")
            if binary is not None:
                if not registered:
                    await send_error(
                        websocket,
                        "authentication_required",
                        "Send an authenticated hello before device data",
                    )
                    continue
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
                capabilities = {
                    str(item)
                    for item in message.get("capabilities", [])
                    if isinstance(item, str)
                }
                if not registered:
                    token = str(message.get("device_token", ""))
                    auth_status = (
                        "simulator"
                        if simulator_token_valid(token)
                        else credential_store.authorize(device_id, token)
                    )
                    if auth_status is None:
                        await send_error(
                            websocket,
                            "pairing_required",
                            "Open a local pairing window, then reconnect Friday",
                        )
                        await websocket.close(code=1008)
                        return
                    hub.register(device_id, websocket, capabilities)
                    registered = True
                    logger.info(
                        "Device %s authenticated via %s", device_id, auth_status
                    )
                    await websocket.send_json(
                        {
                            "type": "ready",
                            "protocol": 1,
                            "device_id": device_id,
                            "state": "idle",
                            "surface": {"kind": "ambient", "title": "Friday"},
                        }
                    )
                else:
                    hub.set_capabilities(device_id, websocket, capabilities)
                wake_audio.reset()
                wake_enabled = transcriber is not None
                await hub.broadcast(device_id, {"type": "state", "state": "idle"})
                await job_worker.deliver_pending(device_id)
            elif not registered:
                await send_error(
                    websocket,
                    "authentication_required",
                    "Send hello before other messages",
                )
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
                request_id = message.get("request_id")
                if request_id is not None and (
                    not isinstance(request_id, str) or len(request_id) > 128
                ):
                    await send_error(
                        websocket, "bad_message", "request_id must be a short string"
                    )
                    continue
                try:
                    await handle_utterance(
                        device_id,
                        text,
                        request_id=request_id,
                    )
                except ValueError as error:
                    await send_error(websocket, "request_conflict", str(error))
            elif message_type == "reminder.ack":
                if not hub.peer_has_capability(
                    device_id, websocket, "reminder_ack"
                ):
                    await send_error(
                        websocket,
                        "capability_required",
                        "reminder_ack capability was not declared",
                    )
                    continue
                reminder_id = message.get("reminder_id")
                if not isinstance(reminder_id, int):
                    await send_error(
                        websocket, "bad_message", "reminder_id must be an integer"
                    )
                    continue
                store.mark_delivered(
                    reminder_id, device_id, datetime.now(timezone.utc)
                )
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
        if registered:
            hub.disconnect(device_id, websocket)
