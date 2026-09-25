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

let socket;
let reconnectTimer;
let surfaceTimer;
let lastAssistantText = "";

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const deviceId = new URLSearchParams(location.search).get("device") || "friday-p3a";
  socket = new WebSocket(`${scheme}://${location.host}/ws/device/${encodeURIComponent(deviceId)}`);
  socket.addEventListener("open", () => {
    dot.classList.add("online");
    connectionLabel.textContent = "Backend connected";
    socket.send(JSON.stringify({ type: "hello", protocol: 1, capabilities: ["display", "touch", "speech"] }));
  });
  socket.addEventListener("close", () => {
    dot.classList.remove("online");
    connectionLabel.textContent = "Reconnecting";
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 1200);
  });
  socket.addEventListener("message", event => handleMessage(JSON.parse(event.data)));
}

function handleMessage(message) {
  if (message.type === "ready") {
    setState(message.state);
    renderSurface(message.surface);
  } else if (message.type === "state") {
    setState(message.state);
    if (message.state === "speaking" && lastAssistantText) speak(lastAssistantText);
  } else if (message.type === "surface") {
    renderSurface(message.surface);
  } else if (message.type === "assistant.text") {
    lastAssistantText = message.text;
    transcript.textContent = message.text;
  } else if (message.type === "transcript") {
    input.value = message.text;
    transcript.textContent = `“${message.text}”`;
  } else if (message.type === "tool.activity") {
    status.textContent = `${message.name.toUpperCase()} · ${message.status.toUpperCase()}`;
  } else if (message.type === "error") {
    transcript.textContent = message.message;
    setState("idle");
  }
}

function setState(next) {
  screen.dataset.state = next;
  status.textContent = next.toUpperCase();
  if (next === "listening") transcript.textContent = "Listening…";
}

function renderSurface(surface = { kind: "ambient", title: "Friday" }) {
  clearTimeout(surfaceTimer);
  const kind = surface.kind || "ambient";
  screen.dataset.surface = kind;
  eyebrow.textContent = kind.toUpperCase();
  title.textContent = surface.title || "Friday";
  subtitle.textContent = surface.subtitle || "Ready when needed.";
  items.replaceChildren();
  for (const item of surface.items || []) {
    const row = document.createElement("div");
    row.className = `scene-item ${item.priority || ""}`;
    const time = document.createElement("span");
    time.textContent = item.time || "";
    const label = document.createElement("strong");
    label.textContent = item.label || "";
    row.append(time, label);
    items.append(row);
  }
  if (surface.expires_ms) {
    surfaceTimer = setTimeout(() => renderSurface({ kind: "ambient", title: "Friday" }), surface.expires_ms);
  }
}

function sendUtterance(value) {
  const text = value.trim();
  if (!text || !socket || socket.readyState !== WebSocket.OPEN) return;
  transcript.textContent = `“${text}”`;
  socket.send(JSON.stringify({ type: "utterance", text }));
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
  if (recognition) recognition.start();
  else input.focus();
});

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.04;
  utterance.pitch = 0.86;
  utterance.addEventListener("end", () => setState("idle"));
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
