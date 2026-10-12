"""Opt-in existing-queue playback. No selection, model metadata or retry on doubt."""
from contextlib import closing
import json
import sqlite3
from .agent import ToolOutcome
from .models import Surface, SurfaceAction


class ConversationMedia:
    def __init__(self, settings, store, service, current, protected):
        self.settings,self.store,self.service,self.current,self.protected=settings,store,service,current,protected

    def allowed(self,device):
        return self.current(device) and not self.protected(device)

    @staticmethod
    def result(payload,surface=None):
        surface=surface or Surface(kind="message",title="Music",subtitle=payload["message"],actions=[SurfaceAction(id="music",label="Music")])
        return ToolOutcome(json.dumps(payload),surface,payload["ok"])

    def claim(self,device,request,index):
        with closing(sqlite3.connect(self.store().path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS conversation_media_actions (device TEXT, request TEXT, operation INTEGER, result TEXT, PRIMARY KEY(device,request,operation))")
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT result FROM conversation_media_actions WHERE device=? AND request=? AND operation=?",(device,request,index)).fetchone()
            if row:return json.loads(row[0])
            if db.execute("SELECT COUNT(*) FROM conversation_media_actions").fetchone()[0]>=4096:
                return {"ok":False,"status":"unavailable","message":"Playback requests need maintenance. No command was sent."}
            pending={"ok":False,"status":"uncertain","message":"The earlier playback command has no confirmed outcome. I will not send it again."}
            db.execute("INSERT INTO conversation_media_actions VALUES(?,?,?,?)",(device,request,index,json.dumps(pending)))
            db.commit()
        return None

    def finish(self,device,request,index,payload):
        with closing(sqlite3.connect(self.store().path)) as db:
            db.execute("UPDATE conversation_media_actions SET result=? WHERE device=? AND request=? AND operation=?",(json.dumps(payload),device,request,index));db.commit()

    @staticmethod
    def presentation(service,device,snapshot,notice,surface=None):
        surface=surface or service._scene(device,snapshot,notice)
        surface.subtitle=notice
        # Full private metadata stays on the authorized native display; neither
        # the provider payload nor the action journal contains titles/devices.
        surface.items=[surface.items[0],surface.items[1],{"time":"Artist","label":snapshot.artist},{"time":"Source","label":"Spotify / snapshot; no timed lyrics"}]
        surface.actions=[SurfaceAction(id="music",label="Music controls"),SurfaceAction(id="dismiss",label="Dismiss")]
        return surface

    async def __call__(self,device,name,request,index):
        if name not in {"get_music_state","resume_music"}:
            return self.result({"ok":False,"status":"unavailable","message":"That playback operation is not supported."})
        # A regenerated model tool chain can renumber operations after a worker
        # retry. Existing-queue resume is at most once per original user job.
        if name=="resume_music":index=0
        service=self.service()
        if not self.settings.music_conversation_enabled or self.settings.music_provider!="spotify" or service is None:
            return self.result({"ok":False,"status":"unavailable","message":"Conversation playback is unavailable. No music command was sent."})
        if not self.allowed(device):
            return self.result({"ok":False,"status":"blocked","message":"This request is no longer current, or another interaction needs attention. No music command was sent."})
        if name=="resume_music":
            cached=self.claim(device,request,index)
            if cached is not None:return self.result(cached)
        async with service.lock:
            snapshot=await service._read();service.cache=snapshot
            internal=service._scene(device,snapshot,"Current playback.")
            resume=next((action.id for action in internal.actions if service.tokens.get(action.id.split(":")[-1],(None,None))[1]=="play"),None)
            surface=self.presentation(service,device,snapshot,"Current playback.",internal)
        if not self.allowed(device):
            payload={"ok":False,"status":"superseded","message":"That request was superseded. No music command was sent."}
        elif name=="get_music_state":
            ready=snapshot.status=="ready"
            payload={"ok":ready,"status":snapshot.status,"playing":snapshot.playing if ready else False,"can_resume":bool(resume),"message":"Existing playback is playing." if ready and snapshot.playing else "The existing queue is paused." if ready else "Playback is unavailable."}
            return self.result(payload,surface)
        elif snapshot.status=="ready" and snapshot.playing:
            payload={"ok":True,"status":"already_playing","playing":True,"command_sent":False,"message":"The existing queue is already playing."}
        elif not resume:
            payload={"ok":False,"status":"restricted" if snapshot.status=="ready" else snapshot.status,"command_sent":False,"message":"I cannot resume the current queue on an active permitted device. No command was sent."}
        else:
            surface=await service.scene(device,resume,allowed=lambda:self.allowed(device))
            notice=next((item["label"] for item in surface.items if item.get("time")=="State"),"")
            observed=service.cache
            accepted=notice=="Command accepted; state refreshed"
            playing=accepted and observed.status=="ready" and observed.playing and observed.device==snapshot.device
            status="playing" if playing else "accepted_unobserved" if accepted else "uncertain" if notice=="Command uncertain" else "rejected"
            message="The existing queue is playing." if playing else "The command was accepted, but playback is not confirmed." if accepted else "The playback command's outcome is uncertain. I will not retry it." if status=="uncertain" else "Playback was not resumed. Please check the active player."
            payload={"ok":playing,"status":status,"playing":playing,"message":message}
            surface=self.presentation(service,device,observed,message)
        if name=="resume_music":self.finish(device,request,index,payload)
        return self.result(payload,surface)
