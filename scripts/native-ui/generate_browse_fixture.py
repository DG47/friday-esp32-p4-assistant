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
    settings=SimpleNamespace(assistant_provider='Synthetic local',calendar_provider='none',tts_provider='none')
    values['local-menu']=local_scene('status','fixture',settings,None,None).model_dump(mode='json')
    values['calendar-long']={**values['calendar-first'],'items':[{'time':'All day','label':'Synthetic planning session with a deliberately very long title that remains available through native scrolling','action':'event:fixture'}]}
    values['calendar-loading']={'kind':'timeline','title':'Calendar','subtitle':'Loading your day…','actions':[{'id':'dismiss','label':'Dismiss'}]}
    values['calendar-error']={'kind':'message','title':'Calendar unavailable','subtitle':'Calendar is temporarily unavailable. Try refresh shortly.','actions':[{'id':'calendar_refresh','label':'Refresh'},{'id':'dismiss','label':'Dismiss'}]}
    out=ROOT/'outputs/native-ui';out.mkdir(parents=True,exist_ok=True);(out/'browse-fixtures.json').write_text(json.dumps(values))
asyncio.run(main())
