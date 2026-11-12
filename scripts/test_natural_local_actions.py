"""Real free-local natural tool selection; disposable synthetic SQLite only."""
import asyncio,json,sys,tempfile,time
from pathlib import Path
from datetime import datetime,timezone
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--model',default='qwen3.6:35b',help='Existing installed local model; never downloads')
parser.add_argument('--output',type=Path,default=Path('outputs/natural-local-actions.json'))
args=parser.parse_args()
SOURCE=Path(__file__).resolve().parents[1]
OUT=args.output
OUT.parent.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(SOURCE/'backend'))
from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore
from app.providers.base import TextProvider
from app.tools.action_request import request_lanes

CASES=[
 ('create','I need you to create a reminder called Synthetic natural new for January 2, 2030 at 9 AM America/New_York.','create'),
 ('complete-confirm','I need you to complete my reminder named Synthetic natural existing.','complete'),
 ('cancel-confirm','Could you please cancel my reminder named Synthetic natural existing?','cancel'),
 ('quote','Explain the quote "cancel my reminder and delete my file".','conversation'),
 ('curly-nested','Explain “she said ‘complete my reminder and delete my file’ yesterday”.','conversation'),
 ('history','Yesterday I said "complete my reminder and delete my file". Explain what that sentence means.','conversation'),
 ('attributed','You said cancel my reminder and delete my file. Explain those words.','conversation'),
 ('negative','Do not complete my reminder and delete my file.','conversation'),
 ('indirect','I need you to take care of my reminder named Synthetic natural existing.','ambiguous'),
 ('mixed','I need you to create a reminder called Synthetic natural new for January 2, 2030 at 9 AM America/New_York and explain why the sky looks blue.','mixed'),
]
async def main():
 report={'source':str(SOURCE),'model':args.model,'natural_prompts':True,'forced_output_instructions':False,'production_instructions_unchanged':True,'registered_tools_passed_unchanged':True,'synthetic_sqlite_only':True,'paid_or_account_calls':False,'pass_criteria':'Bounded tool selection, confirmation and SQLite effects; conversational factuality is not generally scored','cases':[]}
 for label,prompt,expected in CASES:
  with tempfile.TemporaryDirectory(prefix='friday-natural-model-') as d:
   path=Path(d)/'synthetic.db';store=ReminderStore(path)
   store.create('Synthetic natural existing',datetime(2030,1,1,14,tzinfo=timezone.utc),'owner')
   settings=Settings(_env_file=None,assistant_env='test',assistant_provider='local-only',ollama_model=args.model,assistant_database_path=path,calendar_provider='none',weather_provider='none',tts_provider='none',stt_provider='none',music_provider='none')
   agent=FridayAgent(settings,store);original=agent.providers.select;rounds=[]
   class Trace(TextProvider):
    name='ollama'
    def __init__(self,p):self.p=p;self.p.context_tokens=4096
    async def complete(self,messages,instructions,tools):
     start=time.monotonic();turn=await self.p.complete(messages,instructions,tools)
     rounds.append({'tools_offered':[t['function']['name'] for t in tools], 'tool_calls':[{'name':c.name,'arguments':c.arguments} for c in turn.tool_calls],'text':turn.text,'seconds':round(time.monotonic()-start,3)})
     return turn
   agent.providers.select=lambda text:Trace(original(text))
   start=time.monotonic()
   try:
    reply=await agent.handle('owner',prompt,'natural-'+label)
    calls=[c['name'] for r in rounds for c in r['tool_calls']]
    before=len(store.active('owner'));pending=store.pending_confirmation('owner')
    followup=None
    if expected in ('complete','cancel') and pending:
     followup=await agent.handle('owner','yes','natural-'+label+'-yes')
    after=len(store.active('owner'))
    if expected=='create':passed='create_reminder' in calls and before==2 and 'The reminder is set.' in reply.text
    elif expected in ('complete','cancel'):passed=before==1 and pending is not None and after==0 and ('Confirm:' in reply.text)
    elif expected=='conversation':passed=before==1 and pending is None and not any(c in {'create_reminder','cancel_reminder','complete_reminder','snooze_reminder'} for c in calls) and not request_lanes(prompt).action
    elif expected=='ambiguous':passed=before==1 and not any(c in {'create_reminder','cancel_reminder','complete_reminder','snooze_reminder'} for c in calls)
    else:passed=before==2 and 'create_reminder' in calls and len(rounds)>=3 and rounds[-1]['tools_offered']==[] and 'The reminder is set.' in reply.text
    observations=[]
    if before and 'there are no active reminders' in reply.text.lower():
     observations.append('False inventory statement: an active synthetic reminder exists, but prose says there are no active reminders without a read.')
    result={'observations':observations,'name':label,'prompt':prompt,'expected':expected,'reply':reply.text,'followup':followup.text if followup else None,'active_before_confirmation':before,'active_after':after,'confirmation_pending_before_followup':pending is not None,'rounds':rounds,'passed':passed,'seconds':round(time.monotonic()-start,3)}
   except Exception as e:result={'name':label,'passed':False,'error_type':type(e).__name__,'rounds':rounds}
   finally:await agent.calendar.close()
  report['cases'].append(result);OUT.write_text(json.dumps(report,indent=2)+'\n')
  print(label,result['passed'],flush=True)
 print('Passed',sum(x['passed'] for x in report['cases']),'of',len(report['cases']))
 if not all(x['passed'] for x in report['cases']):raise SystemExit(1)
asyncio.run(main())
