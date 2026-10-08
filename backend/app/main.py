from __future__ import annotations

import asyncio
import ipaddress
import hashlib
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager, closing, suppress
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
from .stt.diagnostics import FailureReason, Phase, TranscriptionDiagnostics, observe, log_failure
from .wake_session import WakeAudioSession, contains_wake_word, pcm_peak
from .scenes import local_scene
from .tools.music import MusicService
from .tools.spotify_runtime import create_runtime
from .memory import prepare_memory_input
from .models import Surface, SurfaceAction
from .tools.music_runtime import MusicInteractionPending
from .speech import OpenAISpeech, SpeechDirector
from .local_speech import MacSpeech
from .scene_diagnostics import log_scene_action


settings = get_settings()
store = ReminderStore(settings.assistant_database_path)
agent = FridayAgent(settings, store)
music = MusicService(settings.music_provider if settings.music_provider != "spotify" else "none")
job_store = AgentJobStore(settings.assistant_database_path)
credential_store = DeviceCredentialStore(settings.assistant_database_path)
transcriber = create_transcriber(settings)
static_dir = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)
AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS = 60
hub = DeviceHub()
local_music, official_music = create_runtime(settings, hub, blocked=lambda device: bool(store.pending_confirmation(device) or agent.memory.store is not None and agent.memory.store.pending_confirmation(device)))
if official_music is not None: music = official_music
hub.scene_observer = lambda device, message: local_music.interrupt_message(device, message)


async def publish_tool_progress(device_id: str, name: str, status: str) -> None:
    await hub.broadcast(device_id, {"type": "tool.activity", "name": name, "status": status})


agent.on_tool = publish_tool_progress
reminder_scheduler = ReminderScheduler(
    store,
    hub,
    poll_seconds=settings.assistant_reminder_poll_seconds,
    retry_seconds=settings.assistant_reminder_retry_seconds,
    max_attempts=settings.assistant_reminder_max_attempts,
)
job_worker = AgentJobWorker(
    job_store,
    agent,
    hub,
    timeout_seconds=settings.assistant_job_timeout_seconds,
    max_age_seconds=settings.assistant_job_max_age_seconds,
    concurrency=settings.assistant_job_concurrency,
)
speech_provider = None
if settings.tts_provider == "macos":
    speech_provider = MacSpeech(settings.tts_local_voice, settings.tts_local_rate, settings.tts_local_gain)
elif settings.tts_provider == "openai":
    speech_provider = OpenAISpeech(settings.openai_api_key, settings.tts_model, settings.tts_voice)
speech = SpeechDirector(hub, speech_provider)
job_worker.on_reply = speech.speak
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
active_sockets: set[WebSocket] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    discovery_transport: asyncio.DatagramTransport | None = None
    try:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: FridayDiscoveryProtocol(
                settings.assistant_public_port,
                settings.assistant_public_scheme,
                public_host=settings.assistant_public_host or None,
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
    app.state.background_tasks = tasks
    try:
        yield
    finally:
        close_transcriber = getattr(transcriber, "close", None)
        if close_transcriber is not None:
            drained = await close_transcriber()
            if drained is False:
                logger.warning("Local transcription shutdown deadline exceeded; native worker remains active")
        await local_music.close()
        await speech.close()
        if discovery_transport is not None:
            discovery_transport.close()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await agent.calendar.close()


app = FastAPI(title="Friday Assistant", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


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


def simulator_access_valid(token: str, device_id: str, host: str | None) -> bool:
    return (
        is_local_host(host)
        and device_id == settings.assistant_simulator_device_id
        and simulator_token_valid(token)
    )


def require_local_admin(request: Request) -> None:
    if not is_local_host(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="Administration is loopback-only")
    if not admin_token_valid(request.headers.get("x-friday-admin-token", "")):
        raise HTTPException(status_code=401, detail="Valid Friday admin token required")


def require_local_read_access(request: Request) -> None:
    if not is_local_host(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="Private APIs are loopback-only")
    simulator_token = request.headers.get("x-friday-simulator-token", "")
    admin_token = request.headers.get("x-friday-admin-token", "")
    if not (simulator_token_valid(simulator_token) or admin_token_valid(admin_token)):
        raise HTTPException(status_code=401, detail="Valid Friday read token required")


# One ownership token per device, shared by all authenticated peers. Publication
# and new navigation claims serialize so a checked result cannot race a newer tap.
scene_navigation = {}


async def claim_scene_navigation(device_id):
    state = scene_navigation.setdefault(device_id, {"lock": asyncio.Lock(), "owner": None})
    async with state["lock"]:
        state["owner"] = object()
        return state, state["owner"]


def weather_navigation_current(device_id, navigation):
    state, owner = navigation
    return scene_navigation.get(device_id) is state and state["owner"] is owner


async def publish_current_weather(device_id, navigation, packet, peer=None):
    state, _ = navigation
    async with state["lock"]:
        if not weather_navigation_current(device_id, navigation):
            return False
        if peer is None:
            return bool(await hub.broadcast(device_id, packet))
        # Reuse the hub's bounded peer lock + send path. A blocked or disconnected
        # peer must release this navigation lock and allow the next owner through.
        return bool(await hub.send_media(device_id, peer, packet,
            lambda: weather_navigation_current(device_id, navigation)))


def require_weather_navigation(device_id: str) -> None:
    # Read-only navigation must not replace protected ownership or imply consent.
    if (store.pending_confirmation(device_id)
        or agent.memory.store is not None and agent.memory.store.pending_confirmation(device_id)
        or device_id in local_music.busy
        or device_id in local_music.reminder_devices
        or device_id in local_music.protected):
        raise MusicInteractionPending("Resolve the current interaction before opening Weather")


async def handle_utterance(
    device_id: str, text: str, request_id: str | None = None
) -> str:
    await claim_scene_navigation(device_id)
    text = prepare_memory_input(text)
    job = job_store.enqueue(device_id, text, request_id=request_id)
    speech.latest[device_id] = job.id
    await speech.cancel(device_id)
    await hub.broadcast(device_id, {"type": "state", "state": "thinking"})
    await hub.broadcast(
        device_id,
        {"type": "job.accepted", "job_id": job.id},
    )
    return job.id


async def send_error(websocket: WebSocket, code: str, message: str) -> None:
    logging.getLogger("uvicorn.error").warning("device_error utc=%s code=%s", datetime.now(timezone.utc).isoformat(), code)
    await websocket.send_json({"type": "error", "code": code, "message": message})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": settings.assistant_provider}


@app.get("/ready")
async def readiness() -> dict[str, str]:
    tasks = getattr(app.state, "background_tasks", [])
    if not tasks or any(task.done() for task in tasks):
        raise HTTPException(status_code=503, detail="Background workers unavailable")
    try:
        # No private contents are exposed by this database connectivity probe.
        with closing(job_store._connect()) as connection:
            connection.execute("SELECT 1").fetchone()
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ready"}


@app.get("/api/calendar/today")
async def calendar_today(request: Request) -> dict:
    require_local_read_access(request)
    return (await agent.calendar.snapshot()).payload()


@app.get("/api/diagnostics")
async def diagnostics(request: Request) -> dict:
    require_local_admin(request)
    with closing(job_store._connect()) as connection:
        counts = dict(connection.execute(
            "SELECT status, COUNT(*) FROM agent_jobs GROUP BY status"
        ).fetchall())
    return {"jobs": counts, "connected_devices": len(hub.device_ids()),
            "workers_healthy": bool(getattr(app.state, "background_tasks", [])) and all(not task.done() for task in getattr(app.state, "background_tasks", []))}


@app.get("/api/reminders")
async def reminders(request: Request) -> list[dict]:
    require_local_read_access(request)
    return [
        item.model_dump(mode="json")
        for item in store.pending(device_id=settings.assistant_simulator_device_id)
    ]


@app.post("/api/devices/pairing-window")
async def open_pairing_window(request: Request) -> dict[str, str]:
    require_local_admin(request)
    if request.headers.get("x-friday-pairing") != "open":
        raise HTTPException(status_code=400, detail="Pairing confirmation header required")
    device_id = request.headers.get("x-friday-device-id", "")
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise HTTPException(status_code=400, detail="Valid target device ID required")
    try:
        expires_at = credential_store.open_pairing_window(
            device_id, request.headers.get("x-friday-pairing-code", "")
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    logger.info(
        "Device pairing window for %s opened until %s",
        device_id,
        expires_at.isoformat(),
    )
    return {
        "status": "pairing",
        "device_id": device_id,
        "expires_at": expires_at.isoformat(),
    }


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
    await hub.close_device(device_id)
    logger.warning("Rotating identity for device %s", device_id)
    return {"status": "awaiting_pairing_code"}


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
    await hub.close_device(device_id)
    logger.warning("Revoked identity for device %s", device_id)
    return {"status": "revoked"}


@app.websocket("/ws/device/{device_id}")
async def device_socket(websocket: WebSocket, device_id: str) -> None:
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        await websocket.close(code=1008)
        return
    if len(active_sockets) >= 128:
        await websocket.close(code=1013)
        return
    active_sockets.add(websocket)
    authentication_deadline = asyncio.get_running_loop().time() + 10.0
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
        logger.debug("Wake-word probe finished for %s", device_id)
        if wake_enabled and contains_wake_word(text):
            wake_enabled = False
            wake_audio.reset()
            logger.info("Wake word detected for %s", device_id)
            await hub.broadcast(
                device_id, {"type": "wake.detected", "wake_word": "Friday"}
            )

    try:
        await websocket.accept()
        await websocket.send_json(
            {"type": "hello.required", "protocol": 1, "device_id": device_id}
        )
    except BaseException:
        active_sockets.discard(websocket)
        raise

    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    websocket.receive(), timeout=max(0.001, authentication_deadline - asyncio.get_running_loop().time()) if not registered else None
                )
            except TimeoutError:
                await websocket.close(code=1008)
                return
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
                if len(binary) > 1048576 or len(binary) % 2:
                    audio.reset()
                    wake_audio.reset()
                    await send_error(websocket, "bad_audio", "PCM frame must contain complete samples and fit within 1 MiB")
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
                if len(event.get("text") or "") > 16384:
                    await send_error(websocket, "bad_message", "Control message is too large")
                    continue
                message = json.loads(event.get("text") or "")
            except (json.JSONDecodeError, TypeError):
                await send_error(websocket, "bad_message", "Expected a JSON control message")
                continue
            if not isinstance(message, dict):
                await send_error(websocket, "bad_message", "Expected a JSON object")
                continue
            message_type = message.get("type")

            if message_type == "hello":
                declared = message.get("capabilities", [])
                if not isinstance(declared, list) or len(declared) > 16 or any(
                    not isinstance(item, str) or len(item) > 64 for item in declared
                ):
                    await send_error(websocket, "bad_message", "Invalid capabilities")
                    continue
                capabilities = {
                    str(item)
                    for item in declared
                    if isinstance(item, str)
                }
                if not registered:
                    token = str(message.get("device_token", ""))
                    client_host = websocket.client.host if websocket.client else None
                    auth_status = (
                        "simulator"
                        if simulator_access_valid(token, device_id, client_host)
                        else credential_store.authorize(device_id, token)
                    )
                    if auth_status is None:
                        await send_error(
                            websocket,
                            "pairing_required",
                            "Enter the pairing code shown on Friday, then reconnect",
                        )
                        await websocket.close(code=1008)
                        return
                    if len(hub.connections.get(device_id, [])) >= 4:
                        await websocket.close(code=1013)
                        return
                    hub.register(device_id, websocket, capabilities)
                    registered = True
                    logging.getLogger("uvicorn.error").info(
                        "Device %s authenticated via %s media_updates_v1=%s media_artwork_v1=%s media_persistent_v1=%s", device_id, auth_status, "media_updates_v1" in capabilities, "media_artwork_v1" in capabilities, "media_persistent_v1" in capabilities
                    )
                    await websocket.send_json(
                        {
                            "type": "ready",
                            "protocol": 1,
                            "device_id": device_id,
                            "state": "idle",
                            "surface": {
                                "kind": "ambient",
                                "title": "Friday",
                                "subtitle": "Paired and ready.",
                            },
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
            elif message_type == "media.resume":
                await claim_scene_navigation(device_id)
                await local_music.resume(device_id, websocket, message)
            elif message_type == "scene.action":
                try:
                    action = SurfaceAction(id=message.get("action"), label="Action").id
                    navigation = await claim_scene_navigation(device_id)
                    if action.startswith("reqcancel:"):
                        if device_id in local_music.busy or device_id in local_music.reminder_devices or (agent.memory.store is not None and agent.memory.store.pending_confirmation(device_id)):
                            await send_error(websocket, "interaction_pending", "Finish the current interaction before cancelling this request.")
                            continue
                        pending = store.pending_confirmation(device_id)
                        token = hashlib.sha256(pending.id.encode()).hexdigest()[:24] if pending else ""
                        if not pending or not secrets.compare_digest(action.split(":", 1)[1], token) or not store.cancel_pending_confirmation(pending.id, device_id):
                            await send_error(websocket, "interaction_pending", "That request is no longer current. Return to the current request.")
                            continue
                        # Consent is never inferred: this action only cancels the
                        # exact pending request, without invoking its tools.
                        surface = Surface(kind="message", title="Request cancelled",
                            subtitle="You can return to Music.", actions=[SurfaceAction(id="music", label="Music")])
                        await hub.broadcast(device_id, {"type": "surface", "status": "completed", "surface": surface.model_dump(mode="json")})
                        continue
                    if action == 'weather':
                        require_weather_navigation(device_id)
                    if action not in {'music'} and not action.startswith('musicctl:'):
                        if action == "dismiss":local_music.stop(device_id, websocket)
                        else:local_music.suspend(device_id, websocket)
                    if action == 'dismiss':local_music.clear_protection(device_id)
                    if action == "dismiss":
                        music.dismiss(device_id)
                    if settings.music_provider == 'spotify' and (action == 'music' or action.startswith('musicctl:')):
                        if action.startswith('musicctl:'):
                            owned = local_music.views.get(local_music._key(device_id, websocket))
                            if owned is None or not local_music._allowed(owned):
                                raise ValueError('Reopen Music before controlling playback')
                            if device_id in local_music.protected or device_id in local_music.busy or local_music.blocked(device_id):
                                raise MusicInteractionPending('Resolve the current interaction first')
                            await music.scene(device_id, action)
                            local_music.stop(device_id, websocket)
                        surface = await local_music.open(device_id, websocket, message.get('request_id'))
                        if surface is not None:
                            await hub.broadcast(device_id, {'type':'surface','surface':surface.model_dump(mode='json')})
                        continue
                    if action == "music" or action.startswith("musicctl:"):
                        surface = await music.scene(device_id, action)
                    elif action.startswith(("memories", "memory:", "memforget:", "memyes:", "memno:", "memreview:")):
                        surface = agent.memory.scene(device_id, action)
                    elif action == "weather":
                        if not await publish_current_weather(device_id, navigation,
                            {"type": "tool.activity", "name": "get_weather", "status": "working"}):
                            continue
                        outcome = await agent._execute_read_tool("get_weather", {}, device_id,
                            message.get("request_id") or "touch-weather", 0)
                        # Another authenticated peer may create a protected request
                        # while weather is loading. Recheck before publishing it.
                        if not weather_navigation_current(device_id, navigation):
                            continue
                        require_weather_navigation(device_id)
                        surface = outcome.surface
                        await publish_current_weather(device_id, navigation,
                            {"type": "tool.activity", "name": "get_weather", "status": "complete" if outcome.success else "failed"})
                    elif action.startswith(("calendar", "event:")):
                        await publish_tool_progress(device_id, "get_calendar", "working")
                        surface = await agent.calendar.scene(action, device_id)
                        await publish_tool_progress(device_id, "get_calendar", "complete" if agent.calendar.last_status in {"ready", "sample"} else "failed")
                    else:
                        surface = local_scene(action, device_id, settings, store, agent.calendar)
                    if action.startswith(('memyes:','memno:')) and agent.memory.store is not None:
                        local_music.resolve_memory(device_id, action.split(':')[1], agent.memory.store.confirmation_resolved)
                    log_scene_action(device_id, action, surface)
                    if surface is not None:
                        packet = {"type": "surface", "surface": surface.model_dump(mode="json")}
                        if action == "weather":
                            await publish_current_weather(device_id, navigation, packet)
                        else:
                            await hub.broadcast(device_id, packet)
                    else:
                        request_id = message.get("request_id")
                        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                            raise ValueError("A request_id is required for an action")
                        if action in {"confirm", "decline"}:
                            text = "yes" if action == "confirm" else "no"
                        else:
                            verb, reminder_id = action.split(":")
                            if not any(item.id == int(reminder_id) for item in store.active(device_id=device_id)):
                                raise ValueError("That reminder is no longer active for this device")
                            text = f"{verb} reminder #{reminder_id}" + (" for 10 minutes" if verb == "snooze" else "")
                        await handle_utterance(device_id, text, request_id=request_id)
                except MusicInteractionPending:
                    if action == "weather" and not weather_navigation_current(device_id, navigation):
                        continue
                    # Re-read authoritative ownership at delivery time; never retain
                    # a stale confirmation or turn a Music tap into consent.
                    pending = store.pending_confirmation(device_id)
                    memory_pending = agent.memory.store is not None and agent.memory.store.pending_confirmation(device_id)
                    if pending is not None and not memory_pending and device_id not in local_music.busy and device_id not in local_music.reminder_devices:
                        surface = Surface(
                            kind="message", title="Confirmation required",
                            subtitle=pending.summary + "\nSay yes to continue, or tap Cancel request.",
                            actions=[SurfaceAction(id="reqcancel:" + hashlib.sha256(pending.id.encode()).hexdigest()[:24], label="Cancel request")],
                            expires_ms=30000,
                        )
                        packet = {"type": "surface", "surface": surface.model_dump(mode="json")}
                        if action == "weather":
                            await publish_current_weather(device_id, navigation, packet)
                        else:
                            await hub.broadcast(device_id, packet)
                    else:
                        destination = "opening Weather" if action == "weather" else "returning to Music"
                        message = (
                            f"Finish or cancel the pending memory review before {destination}."
                            if memory_pending else
                            f"Finish the current interaction before {destination}."
                        )
                        if action == "weather":
                            await publish_current_weather(device_id, navigation,
                                {"type": "error", "code": "interaction_pending", "message": message}, websocket)
                        else:
                            await send_error(websocket, "interaction_pending", message)
                except ValueError:
                    await send_error(websocket, "bad_action", "Action is unavailable or invalid; refresh the scene")
            elif message_type == "result.ack":
                job_id, delivery_id = message.get("job_id"), message.get("delivery_id")
                if (not hub.peer_has_capability(device_id, websocket, "result_ack")
                    or not isinstance(job_id, str) or not 1 <= len(job_id) <= 128
                    or not isinstance(delivery_id, str) or re.fullmatch(r"[0-9a-f]{32}", delivery_id) is None
                    or not job_store.acknowledge_result(device_id, job_id, delivery_id)):
                    await send_error(websocket, "bad_result_ack", "Result acknowledgement is invalid or unavailable")
            elif message_type == "speech.finished":
                if hub.peer_has_capability(device_id, websocket, "pcm_speech"):
                    speech.finished(device_id, message.get("stream_id"), message.get("status"))
            elif message_type == "speech.cancel":
                await speech.cancel(device_id)
            elif message_type == "activate":
                await claim_scene_navigation(device_id)
                speech.latest[device_id] = None
                await speech.cancel(device_id)
                wake_enabled = False
                wake_audio.reset()
                await hub.broadcast(device_id, {"type": "state", "state": "listening"})
            elif message_type == "wake.start":
                if transcriber is None:
                    await send_error(
                        websocket, "wake_unavailable", "Local speech transcription is disabled"
                    )
                    continue
                if wake_probe_task is not None and not wake_probe_task.done():
                    wake_probe_task.cancel()
                    await asyncio.gather(wake_probe_task, return_exceptions=True)
                wake_probe_task = None
                wake_audio.reset()
                wake_enabled = True
            elif message_type == "utterance":
                await speech.cancel(device_id)
                raw_text = message.get("text", "")
                if not isinstance(raw_text, str) or len(raw_text) > 4096:
                    await send_error(websocket, "bad_message", "text must be a string of at most 4096 characters")
                    continue
                text = raw_text.strip()
                if not text:
                    await send_error(websocket, "empty_utterance", "I heard a truly immaculate silence.")
                    continue
                request_id = message.get("request_id")
                if request_id is not None and (
                    not isinstance(request_id, str) or not request_id or len(request_id) > 128
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
                if type(reminder_id) is not int or reminder_id <= 0:
                    await send_error(
                        websocket, "bad_message", "reminder_id must be an integer"
                    )
                    continue
                if store.mark_delivered(
                    reminder_id, device_id, datetime.now(timezone.utc)
                ):local_music.clear_protection(device_id)
            elif message_type == "audio.start":
                speech.latest[device_id] = None
                await speech.cancel(device_id)
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
                capture_format = audio.audio_format
                diagnostics = TranscriptionDiagnostics(len(audio.data),
                    capture_format.sample_rate if capture_format else 0,
                    capture_format.channels if capture_format else 0)
                diagnostics.input_level(audio.data, capture_format.encoding if capture_format else "")
                reason = (FailureReason.CAPTURE_MISSING if not audio.active
                          else FailureReason.CAPTURE_MALFORMED)
                with observe(diagnostics):
                    try:
                        pcm, audio_format = audio.finish()
                        minimum_bytes = audio_format.sample_rate * audio_format.channels * 2 // 5
                        if len(pcm) < minimum_bytes:
                            reason = FailureReason.INPUT_SHORT
                            raise AudioSessionError("Audio turn was too short")
                        if transcriber is None:
                            reason = FailureReason.STT_DISABLED
                            raise TranscriptionError("Speech transcription is disabled")
                        reason = FailureReason.STT_ERROR
                        await hub.broadcast(device_id, {"type": "state", "state": "thinking"})
                        diagnostics.phase(Phase.WORKER_WAIT)
                        text = (await asyncio.wait_for(transcriber.transcribe(pcm, audio_format), timeout=AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS)).strip()
                        diagnostics.phase(Phase.RESULT)
                        if not text:
                            reason = FailureReason.EMPTY
                            raise TranscriptionError("No speech was recognized")
                    except (AudioSessionError, TranscriptionError, TimeoutError) as error:
                        if isinstance(error, TimeoutError):
                            reason = FailureReason.TIMEOUT
                        elif isinstance(error, TranscriptionError) and isinstance(error.reason, FailureReason):
                            reason = error.reason
                        log_failure(logging.getLogger("uvicorn.error"), diagnostics, reason)
                        await send_error(websocket, "transcription_failed", "Speech transcription timed out" if isinstance(error, TimeoutError) else str(error))
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
        active_sockets.discard(websocket)
        if wake_probe_task is not None and not wake_probe_task.done():
            wake_probe_task.cancel()
            await asyncio.gather(wake_probe_task, return_exceptions=True)
        if registered:
            local_music.stop(device_id, websocket)
            hub.disconnect(device_id, websocket)
            if not hub.connections.get(device_id):
                scene_navigation.pop(device_id, None)
            local_music.disconnected(device_id)
            if device_id not in hub.device_ids():
                await speech.cancel(device_id)
                speech.latest.pop(device_id, None)
