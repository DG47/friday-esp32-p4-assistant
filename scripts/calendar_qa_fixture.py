#!/usr/bin/env python3
"""Generate synthetic local browser fixtures; no credentials or service connection."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings
from app.models import CalendarEvent
from app.tools.google_calendar import CalendarService,CalendarSnapshot,parse_event

async def fixtures():
    now=datetime(2026,10,1,12,tzinfo=timezone.utc)
    service=CalendarService(Settings(_env_file=None,calendar_provider='google'),clock=lambda:now)
    records=[CalendarEvent(id='fixture-'+str(i),title=('Fixture planning '+str(i+1)) if i else '<script>Fixture literal text</script>',starts_at=now+timedelta(hours=i+1),ends_at=now+timedelta(hours=i+2),all_day=i==1,location='Fixture studio',recurring=i==2) for i in range(6)]
    from zoneinfo import ZoneInfo
    records[1]=parse_event({'id':'fixture-all-day','summary':'Fixture all-day block','start':{'date':'2026-10-01'},'end':{'date':'2026-10-02'}},ZoneInfo('America/New_York'))
    snapshot=CalendarSnapshot('ready',records,now)
    first=await service.scene('calendar','fixture-device',snapshot=snapshot)
    second=await service.scene('calendar:1','fixture-device',snapshot=snapshot)
    async def mocked_snapshot(*args,**kwargs):return snapshot
    service.snapshot=mocked_snapshot
    detail=await service.scene(first.items[2]['action'],'fixture-device')
    stale=await service.scene('calendar','fixture-device',snapshot=CalendarSnapshot('stale',records,now,message='Fixture cached data'))
    empty=await service.scene('calendar','fixture-device',snapshot=CalendarSnapshot('ready',[],now))
    revoked=await service.scene('calendar','fixture-device',snapshot=CalendarSnapshot('revoked',message='Fixture access revoked'))
    unconnected=await service.scene('calendar','fixture-device',snapshot=CalendarSnapshot('unconnected',message='Fixture account not connected'))
    await service.close()
    result={name:value.model_dump(mode='json') for name,value in [('first',first),('second',second),('detail',detail),('stale',stale),('empty',empty),('revoked',revoked),('unconnected',unconnected)]}
    for value in result.values():value['subtitle']='FIXTURE · '+value['subtitle']
    return result


def create(destination,mode='test'):
    surfaces=asyncio.run(fixtures())
    html=(ROOT/'backend/app/static/index.html').read_text().replace('/static/styles.css',(ROOT/'backend/app/static/styles.css').as_uri())
    app=(ROOT/'backend/app/static/app.js').read_text()
    stub='''const qaMessages=[];
class FixtureSocket {static OPEN=1;readyState=1;addEventListener(){};close(){this.readyState=3};send(value){qaMessages.push(JSON.parse(value))}}
window.WebSocket=FixtureSocket;
'''
    payload='const qaSurfaces='+json.dumps(surfaces).replace('<','\\u003c')+';'
    test='''
socket=new FixtureSocket();authenticated=true;
const qaResults=[];function check(name,passed){qaResults.push({name,passed:Boolean(passed)})}
renderSurface(qaSurfaces.first);
check("four cards",items.children.length===4);
const box=screen.getBoundingClientRect();
check("cards and actions inside display",Array.from(document.querySelectorAll(".scene-item,#scene-actions button")).every(e=>{const r=e.getBoundingClientRect();return r.left>=box.left&&r.right<=box.right&&r.top>=box.top&&r.bottom<=box.bottom}));
check("literal title does not inject script",items.textContent.includes("<script>")&&!items.querySelector("script"));
items.children[2].click();check("event touch",qaMessages.at(-1).action===qaSurfaces.first.items[2].action);
renderSurface(qaSurfaces.detail);check("event detail",screen.dataset.surface==="artifact");
renderSurface(qaSurfaces.first);
Array.from(document.querySelectorAll("#scene-actions button")).find(b=>b.textContent==="Next").click();
check("next page action",qaMessages.at(-1).action==="calendar:1");
renderSurface(qaSurfaces.second);check("second page two cards",items.children.length===2);
Array.from(document.querySelectorAll("#scene-actions button")).find(b=>b.textContent==="Previous").click();
check("previous action",qaMessages.at(-1).action==="calendar:0");
renderSurface(qaSurfaces.stale);check("stale disclosure",subtitle.textContent.includes("stale"));
renderSurface(qaSurfaces.empty);check("empty without fake cards",items.children.length===0&&subtitle.textContent.includes("No events today"));
renderSurface(qaSurfaces.revoked);check("revoked without private cards",items.children.length===0&&title.textContent.includes("revoked"));
renderSurface(qaSurfaces.unconnected);check("unconnected",title.textContent.includes("not connected"));
renderSurface(qaSurfaces.first);
for(let i=0;i<3;i++)Array.from(document.querySelectorAll("#scene-actions button")).find(b=>b.textContent==="Refresh").click();
check("repeated refresh is read only",qaMessages.slice(-3).every(m=>m.action==="calendar_refresh"));
Array.from(document.querySelectorAll("#scene-actions button")).find(b=>b.textContent==="Dismiss").click();check("dismiss returns ambient",screen.dataset.surface==="ambient");
renderSurface(qaSurfaces.first);authenticated=false;
const before=qaMessages.length;items.children[0].click();check("offline never sends action",qaMessages.length===before&&transcript.textContent.includes("offline"));
authenticated=true;socket=new FixtureSocket();renderSurface(qaSurfaces.first);
const pre=document.createElement("pre");pre.id="qa-result";pre.textContent=JSON.stringify(qaResults);document.body.append(pre);
'''
    if mode!='test':
        test=f'socket=new FixtureSocket();authenticated=true;renderSurface(qaSurfaces.{mode});'
    inline=stub+app+'\n'+payload+test
    html=html.replace('<script src="/static/app.js"></script>','<script>'+inline.replace('</script','<\\/script')+'</script>')
    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(html)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination',type=Path);parser.add_argument('--scene',default='test',choices=['test','first','detail','stale','unconnected']);args=parser.parse_args();create(args.destination,args.scene)
