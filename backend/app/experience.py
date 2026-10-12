"""Negotiated presentation ownership; never an authorization/consent token."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re
import secrets

CAPABILITY = "experience_context_v1"
REQUEST = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
PRESENTATION = {"state", "surface", "assistant.text", "tool.activity", "agent.result", "error", "job.accepted", "experience.finished", "experience.rejected"}


@dataclass(frozen=True)
class Origin:
    device: str
    epoch: str
    request: str
    peer: str | None = None


class Experience:
    def __init__(self):
        self.epochs = {}
        self.current = {}
        self.sequences = {}
        self.jobs = OrderedDict()
        self.finished = OrderedDict()
        self.origin = ContextVar("friday_presentation_origin", default=None)

    def session(self, device, reset=False):
        if reset or device not in self.epochs:
            self.epochs[device] = secrets.token_hex(16)
            self.current.pop(device, None)
        return self.epochs[device]

    def prepare(self, device, request, peer=None):
        if not isinstance(request, str) or not REQUEST.fullmatch(request):
            return None
        return Origin(device, self.session(device), request, peer)

    @contextmanager
    def scope(self, device, request, peer=None):
        token = self.origin.set(self.prepare(device, request, peer))
        try:
            yield
        finally:
            self.origin.reset(token)

    def set_request(self, device, request):
        self.origin.set(self.prepare(device, request))

    def claim(self, device):
        origin = self.origin.get()
        if origin and origin.device == device and origin.epoch == self.session(device):
            self.current[device] = origin

    def bind_job(self, device, job_id):
        source = self.origin.get()
        origin = self.jobs.get(job_id) or (source if source and source.device == device else self.prepare(device, job_id))
        self.origin.set(origin)
        self.claim(device)
        if origin:
            self.jobs[job_id] = origin
            self.jobs.move_to_end(job_id)
            while len(self.jobs) > 1024:
                self.jobs.popitem(last=False)

    @contextmanager
    def for_job(self, job_id):
        # Recovered/unknown jobs are historical, never rebound to current work.
        token = self.origin.set(self.jobs.get(job_id))
        try:
            yield
        finally:
            self.origin.reset(token)

    def envelope(self, device, packet):
        if packet.get("type") not in PRESENTATION or (packet.get("type") == "surface" and packet.get("media_request_id")):
            return packet
        origin = self.origin.get()
        if origin is None or origin.device != device:
            return {**packet, "experience_historical": True}
        key = (origin.device, origin.epoch, origin.request)
        sequence = self.sequences.get(key, 0) + 1
        self.sequences[key] = sequence
        # Bound metadata, with no user text or private facts retained.
        if len(self.sequences) > 2048:
            active = {(o.device, o.epoch, o.request) for o in self.current.values()}
            for old in self.sequences:
                if old not in active:
                    self.sequences.pop(old)
                    break
        return {**packet, "experience_epoch": origin.epoch,
                "experience_id": origin.request, "experience_sequence": sequence,
                "experience_peer": origin.peer}

    def current_packet(self, device, packet):
        origin = self.current.get(device)
        return bool(not packet.get("experience_historical") and origin and packet.get("experience_epoch") == origin.epoch
                    and packet.get("experience_id") == origin.request)

    def for_peer(self, device, packet, peer=None):
        if packet.get("type") not in PRESENTATION or (packet.get("type") == "surface" and packet.get("media_request_id")):
            return packet  # Media, reminders/security and PCM have separate owners.
        if self.current_packet(device, packet):
            if packet.get("experience_peer") and packet["experience_peer"] != peer:
                return None  # A second display did not initiate this presentation.
            origin = self.current[device]
            if packet.get("type") == "tool.activity" and origin in self.finished:
                return None
            if packet.get("type") == "experience.finished":
                self.finished[origin] = True
                while len(self.finished) > 1024:
                    self.finished.popitem(last=False)
            return packet
        if packet.get("type") == "experience.rejected":
            current = self.current.get(device)
            previous = current.request if current else ""
            if packet.get("experience_previous") == previous and packet.get("experience_epoch") == self.epochs.get(device) and packet.get("experience_peer") in {None, peer}:
                return packet
        if packet.get("type") == "agent.result":
            # ACK transport delivery without sending private historical facts.
            return {**packet, "experience_historical": True,
                    "text": "That result is no longer current.",
                    "surface": {"kind": "message", "title": "Previous result", "subtitle": "Recover any pending request explicitly."}}
        return None
