const screen = document.getElementById("screen");
const orb = document.getElementById("orb");
const status = document.getElementById("status");
const transcript = document.getElementById("transcript");
const title = document.getElementById("scene-title");
const subtitle = document.getElementById("scene-subtitle");
const eyebrow = document.getElementById("eyebrow");
const items = document.getElementById("scene-items");
const input = document.getElementById("utterance");
const form = document.getElementById("utterance-form");
const dot = document.getElementById("connection-dot");
const connectionLabel = document.getElementById("connection-label");
const simulatorToken = document.getElementById("simulator-token");

const executionNotice = document.getElementById('execution-notice');
const executionButton = document.getElementById('execution-stop');
const executionStatus = document.getElementById('execution-status');
let executionSupported = false, latestSubmitted = null;
const executionControl = new ExecutionStopClient(packet => {
  if (!authenticated || socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(packet)); return true;
});
function executionRequestId() { return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function executionRender() {
  executionNotice.textContent = executionControl.notice;
  executionButton.disabled = !executionSupported || !executionControl.target;
  executionStatus.disabled = !executionSupported || !executionControl.target;
}
executionButton.addEventListener('click', () => { stopSpeech(); executionControl.request(executionRequestId()); executionRender(); });
executionStatus.addEventListener('click', () => { executionControl.status(executionRequestId()); executionRender(); });
setInterval(() => { executionControl.tick(); executionRender(); }, 100);
let socket;
let reconnectTimer;
let surfaceTimer;
let countdownTimer;
let lastAssistantText = "";

let pendingReminderId = null;
let actionPending = false;
let musicDismissed = false;
let mediaRequest = null, mediaSession = null, mediaSequence = 0;
function clearMediaOwner() { mediaRequest = null; mediaSession = null; mediaSequence = 0; }
function acceptMedia(message) {
  if (!mediaRequest || message.media_request_id !== mediaRequest || typeof message.media_session_id !== "string" ||
      !/^[0-9a-f]{24}$/.test(message.media_session_id) || !Number.isSafeInteger(message.media_sequence) || message.media_sequence <= mediaSequence) return false;
  if (mediaSession && mediaSession !== message.media_session_id) return false;
  mediaSession = message.media_session_id; mediaSequence = message.media_sequence; return true;
}
let authenticated = false;
let authBlocked = false;
let reconnectDelay = 1200;
// Credentials stay in memory for this page; never persist a bearer token.
simulatorToken.addEventListener("change", () => {
  authBlocked = false;
  clearTimeout(reconnectTimer);
  socket?.close();
  reconnectDelay = 1200;
  connect();
});

function connect() {
  authenticated = false;
  if (authBlocked) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const deviceId = new URLSearchParams(location.search).get("device") || "friday-p3a";
  socket = new WebSocket(`${scheme}://${location.host}/ws/device/${encodeURIComponent(deviceId)}`);
  socket.addEventListener("open", () => {
    connectionLabel.textContent = "Authenticating";
    socket.send(JSON.stringify({
      type: "hello",
      protocol: 1,
      device_token: simulatorToken.value.trim(),
      capabilities: ["display", "touch", "speech", "reminder_ack", "result_ack", "media_updates_v1", "execution_stop_v1"],
    }));
  });
  const current = socket;
  socket.addEventListener("close", () => {
    if (current !== socket) return;
    authenticated = false; executionSupported = false;
    executionControl.disconnected(); executionRender();
    clearMediaOwner();
    stopSpeech();
    actionPending = false;
    dot.classList.remove("online");
    connectionLabel.textContent = authBlocked ? "Credential required" : "Reconnecting";
    clearTimeout(reconnectTimer);
    if (!authBlocked) reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(30000, reconnectDelay * 1.6);
  });
  socket.addEventListener("message", event => { if (current === socket) handleMessage(JSON.parse(event.data)); });
}

const acceptedResults = new Set();
function acknowledgeResult(message) {
  if (authenticated && socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "result.ack", job_id: message.job_id, delivery_id: message.delivery_id }));
  }
}

function handleMessage(message) {
  if (message.type === 'execution.stop.observed' || message.type === 'execution.stop.status') {
    executionControl.receive(message); executionRender(); return;
  }
  if (message.type === 'job.accepted' && message.job_id === latestSubmitted) {
    executionControl.capture(message.job_id, message.execution_id); executionRender();
  }
  if (["agent.result", "reminder.alert", "assistant.text", "ready", "error"].includes(message.type) ||
      (message.type === "state" && ["listening", "thinking", "speaking"].includes(message.state)) ||
      (message.type === "tool.activity" && message.status === "working")) clearMediaOwner();
  if (message.type === "agent.result") {
    if (typeof message.delivery_id !== "string" || !/^[0-9a-f]{32}$/.test(message.delivery_id) || typeof message.job_id !== "string" || !message.job_id || message.job_id.length > 128 || typeof message.text !== "string" || !message.surface || !["completed", "failed", "expired", "late"].includes(message.status)) return;
    if (!acceptedResults.has(message.delivery_id)) {
      // Remember only after synchronous DOM acceptance; exceptions leave it retryable.
      renderSurface(message.surface);
      transcript.textContent = message.text;
      actionPending = false;
      executionControl.result(message.job_id, message.delivery_id); executionRender();
      acceptedResults.add(message.delivery_id);
      if (acceptedResults.size > 64) acceptedResults.delete(acceptedResults.values().next().value);
      acknowledgeResult(message);
      if (message.status === "completed") speak(message.text);
    } else {
      acknowledgeResult(message);
    }
  } else if (message.type === "ready") {
    authenticated = true; executionSupported = message.execution_stop_v1 === true;
    if (message.execution_recovery) executionControl.restore(message.execution_recovery);
    if (executionControl.target) executionControl.status(executionRequestId());
    executionRender();
    reconnectDelay = 1200;
    dot.classList.add("online");
    connectionLabel.textContent = "Friday authenticated";
    setState(message.state);
    renderSurface(message.surface);
  } else if (message.type === "reminder.alert") {
    pendingReminderId = message.reminder_id;
    renderSurface({ ...message.surface, actions: [{ id: "ack", label: "Acknowledge" }] });
    setState("alert");
    transcript.textContent = message.text;
  } else if (message.type === "state") {
    setState(message.state);

  } else if (message.type === "surface") {
    if (message.media_request_id !== undefined || message.media_session_id !== undefined || message.media_sequence !== undefined) {
      if (message.surface?.kind !== "media" || !acceptMedia(message)) return;
    } else if (message.surface?.kind !== "media") clearMediaOwner();
    if (message.surface?.kind === "media") {
      actionPending = false;
      if (musicDismissed) return;
      transcript.textContent = "";
      setState("idle");
    }
    renderSurface(message.surface);
  } else if (message.type === "assistant.text") {
    actionPending = false;
    lastAssistantText = message.text;
    transcript.textContent = message.text;
    speak(message.text);
  } else if (message.type === "transcript") {
    input.value = message.text;
    transcript.textContent = `“${message.text}”`;
  } else if (message.type === "tool.activity") {
    status.textContent = `${message.name.toUpperCase()} · ${message.status.toUpperCase()}`;
  } else if (message.type === "error") {
    actionPending = false;
    if (message.code === "pairing_required") authBlocked = true;
    transcript.textContent = message.message;
    setState("idle");
  }
}

function setState(next) {
  if (next === "idle" && window.speechSynthesis?.speaking) return;
  screen.dataset.state = next;
  status.textContent = next.toUpperCase();
  if (next === "listening") transcript.textContent = "Listening…";
}

function renderSurface(surface = { kind: "ambient", title: "Friday" }) {
  clearTimeout(surfaceTimer);
  clearInterval(countdownTimer);
  const kind = surface.kind || "ambient";
  screen.dataset.surface = kind;
  eyebrow.textContent = kind.toUpperCase();
  title.textContent = surface.title || "Friday";
  subtitle.textContent = surface.subtitle || "Ready when needed.";
  items.replaceChildren();
  const actions = document.getElementById("scene-actions");
  actions.replaceChildren();
  for (const action of (surface.actions || []).slice(0, 4)) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", () => sendSceneAction(action.id));
    actions.append(button);
  }
  for (const item of (surface.items || []).slice(0, 4)) {
    const row = document.createElement(item.action ? "button" : "div");
    if (item.action) {
      row.type = "button";
      row.addEventListener("click", () => sendSceneAction(item.action));
    }
    row.className = `scene-item ${item.priority || ""}`;
    const time = document.createElement("span");
    time.textContent = item.time || "";
    const label = document.createElement("strong");
    label.textContent = item.label || "";
    row.append(time, label);
    items.append(row);
  }
  if (surface.countdown_ms) {
    const remaining = surface.deadline_at ? Math.max(0, Date.parse(surface.deadline_at) - Date.now()) : surface.countdown_ms;
    const deadline = performance.now() + remaining;
    const countdown = document.createElement("div");
    countdown.className = "countdown";
    items.prepend(countdown);
    function tick() {
      const seconds = Math.max(0, Math.ceil((deadline - performance.now()) / 1000));
      countdown.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
      if (!seconds) {
        clearInterval(countdownTimer);
        subtitle.textContent = "Timer finished. Reminder delivery is handled by Friday.";
      }
    }
    tick();
    countdownTimer = setInterval(tick, 250);
  }
  if (surface.expires_ms) {
    surfaceTimer = setTimeout(() => {
      const wasOwned = !!mediaRequest;
      clearMediaOwner();
      if (wasOwned && authenticated && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type:"scene.action",action:"dismiss"}));
      renderSurface({ kind: "ambient", title: "Friday" });
      if (screen.dataset.state === "alert") {
        pendingReminderId = null;
        setState("idle");
      }
    }, surface.expires_ms);
  }
}

function sendUtterance(value) {
  const text = value.trim();
  if (!text || !authenticated || !socket || socket.readyState !== WebSocket.OPEN) {
    transcript.textContent = "Connect Friday with a valid simulator credential first.";
    return;
  }
  if (text.toLowerCase() === 'stop' && executionControl.target) {
    stopSpeech(); executionControl.request(executionRequestId()); executionRender(); return;
  }
  if (executionControl.blocked()) { executionRender(); return; }
  clearMediaOwner();
  stopSpeech();
  transcript.textContent = `“${text}”`;
  const requestId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  latestSubmitted = requestId;
  socket.send(JSON.stringify({ type: "utterance", text, request_id: requestId }));
}

form.addEventListener("submit", event => {
  event.preventDefault();
  sendUtterance(input.value);
});

document.querySelectorAll("[data-utterance]").forEach(button => {
  button.addEventListener("click", () => {
    input.value = button.dataset.utterance;
    sendUtterance(input.value);
  });
});

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition;
if (Recognition) {
  recognition = new Recognition();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.addEventListener("start", () => {
    setState("listening");
    socket?.send(JSON.stringify({ type: "activate", source: "touch" }));
  });
  recognition.addEventListener("result", event => {
    const result = Array.from(event.results).map(item => item[0].transcript).join(" ");
    transcript.textContent = result;
    if (event.results[event.results.length - 1].isFinal) {
      input.value = result;
      sendUtterance(result);
    }
  });
  recognition.addEventListener("end", () => {
    if (screen.dataset.state === "listening") setState("idle");
  });
  recognition.addEventListener("error", event => {
    transcript.textContent = `Speech input unavailable: ${event.error}`;
    setState("idle");
  });
} else {
  document.getElementById("speech-hint").textContent = "Speech recognition is unavailable in this browser. Typed input still works.";
}

orb.addEventListener("click", () => {
  if (!authenticated) {
    transcript.textContent = "Connect Friday first.";
    return;
  }
  stopSpeech();
  if (recognition) {
    try { recognition.start(); } catch { recognition.stop(); }
  }
  else input.focus();
});

function sendSceneAction(action) {
  if (action !== "music" && !action.startsWith("musicctl:")) clearMediaOwner();
  if (action === "ack") {
    if (authenticated && pendingReminderId && socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "reminder.ack", reminder_id: pendingReminderId }));
      pendingReminderId = null;
      renderSurface({ kind: "ambient", title: "Friday" });
      setState("idle");
    }
    return;
  }
  if (action === "dismiss") {
    if (screen.dataset.surface === "media") {
      musicDismissed = true;
      actionPending = false;
      if (authenticated && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "scene.action", action: "dismiss" }));
      }
    }
    stopSpeech();
    renderSurface({ kind: "ambient", title: "Friday" });
    return;
  }
  if (!authenticated || socket?.readyState !== WebSocket.OPEN) {
    transcript.textContent = "Friday is offline. This action was not sent.";
    return;
  }
  if (action === "music") musicDismissed = false;
  const mutation = /^(snooze|complete|cancel|musicctl):/.test(action);
  if (mutation && executionControl.blocked()) { executionRender(); return; }
  if (mutation && actionPending) return;
  if (mutation) {
    actionPending = true;
    setState("thinking");
  }
  const requestId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  if (action === "music" || action.startsWith("musicctl:")) {
    mediaRequest = requestId; mediaSession = null; mediaSequence = 0;
  }
  socket.send(JSON.stringify({ type: "scene.action", action, request_id: requestId }));
}

document.querySelectorAll("[data-action]").forEach(button => {
  button.addEventListener("click", () => sendSceneAction(button.dataset.action));
});
orb.addEventListener("contextmenu", event => {
  event.preventDefault();
  sendSceneAction("status");
});

let speechGeneration = 0;
function stopSpeech() {
  speechGeneration++;
  window.speechSynthesis?.cancel();
}

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  stopSpeech();
  const generation = speechGeneration;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.04;
  utterance.pitch = 0.86;
  utterance.addEventListener("start", () => { if (generation === speechGeneration) setState("speaking"); });
  utterance.addEventListener("end", () => { if (generation === speechGeneration) setState("idle"); });
  utterance.addEventListener("error", () => { if (generation === speechGeneration) setState("idle"); });
  speechSynthesis.speak(utterance);
}

function updateClock() {
  const now = new Date();
  document.getElementById("clock").textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  document.getElementById("date").textContent = now.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

updateClock();
setInterval(updateClock, 1000);
connect();
