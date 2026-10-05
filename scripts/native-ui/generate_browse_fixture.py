"""Production scene generation with only synthetic data; no credentials/network."""
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'backend'))
from calendar_qa_fixture import fixtures
from app.scenes import local_scene
async def main():
    values=await fixtures()
    values={'calendar-'+k:v for k,v in values.items()}
    for value in values.values():
        value['subtitle']=value['subtitle'].removeprefix('FIXTURE · ')
    for page in ['calendar-first','calendar-second','calendar-stale']:
        for item in values[page]['items']:
            item['label']={'<script>Fixture literal text</script>':'Studio planning','Fixture all-day block':'A day for deep work','Fixture planning 3':'Design catch-up','Fixture planning 4':'Afternoon walk','Fixture planning 5':'Project review','Fixture planning 6':'Wrap up the day'}.get(item['label'],item['label'])
    values['calendar-detail']['title']='Design catch-up'
    values['calendar-detail']['items'][2]['label']='The studio'
    settings=SimpleNamespace(assistant_provider='Synthetic local',calendar_provider='none',tts_provider='none')
    values['local-menu']=local_scene('status','fixture',settings,None,None).model_dump(mode='json')
    values['calendar-long']={**values['calendar-first'],'items':[{'time':'All day','label':'Synthetic planning session with a deliberately very long title that remains available through native scrolling','action':'event:fixture'}]}
    values['calendar-loading']={'kind':'timeline','title':'Calendar','subtitle':'Loading your day…','actions':[{'id':'dismiss','label':'Dismiss'}]}
    values['calendar-error']={'kind':'message','title':'Calendar unavailable','subtitle':'Calendar is temporarily unavailable. Try refresh shortly.','actions':[{'id':'calendar_refresh','label':'Refresh'},{'id':'dismiss','label':'Dismiss'}]}
    out=ROOT/'outputs/native-ui';out.mkdir(parents=True,exist_ok=True);(out/'browse-fixtures.json').write_text(json.dumps(values))
asyncio.run(main())
