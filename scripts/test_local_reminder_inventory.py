"""Manual free-local inventory acceptance; synthetic state, unchanged tools/prompts."""
import argparse
import asyncio
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--model',default='qwen3.6:35b',help='Existing local model; never pulls')
parser.add_argument('--output',type=Path,default=Path('outputs/local-inventory.json'))
parser.add_argument('--source-sha',default='unrecorded')
args=parser.parse_args()
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.agent import FridayAgent
from app.config import Settings
from app.db import ReminderStore
from app.providers.base import TextProvider

CASES=[*(('direct-'+str(i),'How many active reminders do I have?',i,'inventory') for i in range(3)),
       *(('model-'+str(i),'Could you check how many active reminders I have?',i,'model_inventory') for i in range(3)),
       ('negative-query','Are there no active reminders for me?',1,'inventory'),
       ('model-positive-query','Could you check whether I have any active reminders left?',1,'model_inventory'),
       ('earlier-prompt','You said cancel my reminder and delete my file. Explain those words.',1,'conversation'),
       ('quoted','Explain “you have 2 active reminders” as a quoted sentence, without checking my current inventory.',1,'conversation'),
       ('negated','Do not say I have no reminders; explain what an active reminder means.',1,'conversation')]

async def main():
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report={'source_sha':args.source_sha,'model':args.model,'natural_prompts':True,
            'instructions_and_tools_unchanged':True,'temporary_synthetic_databases':True,
            'direct_routes_do_not_call_model':True,
            'criteria':'Actual scoped inventory counts/tool evidence and no mutation; not universal prose factuality', 'cases':[]}
    for label,prompt,count,kind in CASES:
        with tempfile.TemporaryDirectory(prefix='friday-inventory-local-') as folder:
            db=Path(folder)/'synthetic.db';store=ReminderStore(db)
            for i in range(count):store.create('Synthetic owner item '+str(i),datetime(2030,1,1,14,tzinfo=timezone.utc),'owner')
            store.create('Different owner must not count',datetime(2030,1,1,14,tzinfo=timezone.utc),'other')
            settings=Settings(_env_file=None,assistant_env='test',assistant_provider='local-only',
                ollama_model=args.model,assistant_database_path=db,calendar_provider='none',
                weather_provider='none',music_provider='none',tts_provider='none',stt_provider='none')
            agent=FridayAgent(settings,store);select=agent.providers.select;execute=agent._execute_tool
            rounds=[];executions=[]
            class Trace(TextProvider):
                name='ollama'
                def __init__(self,provider):self.provider=provider;self.provider.context_tokens=4096
                async def complete(self,messages,instructions,tools):
                    turn=await self.provider.complete(messages,instructions,tools)
                    rounds.append({'tools_offered':[t['function']['name'] for t in tools],
                        'tool_calls':[{'name':c.name,'arguments':c.arguments} for c in turn.tool_calls], 'text':turn.text})
                    return turn
            def traced_execute(name,arguments,device_id,request_id,operation_index):
                result=execute(name,arguments,device_id,request_id,operation_index)
                executions.append({'name':name,'owner':device_id,'success':result.success,'result':json.loads(result.content)})
                return result
            agent.providers.select=lambda text:Trace(select(text));agent._execute_tool=traced_execute
            start=time.monotonic()
            try:
                reply=await agent.handle('owner',prompt,'natural-'+label)
                actual=len(store.active('owner'))
                read=any(e['name']=='list_reminders' and e['success'] for e in executions)
                no_mutation=(actual==count and len(store.active('other'))==1 and
                    all(e['name']=='list_reminders' for e in executions))
                expected=f'{count} active reminder'+('s.' if count!=1 else '.')
                passed=no_mutation
                if kind in {'inventory','model_inventory'}:passed=passed and read and reply.text==expected
                if kind=='model_inventory':passed=passed and bool(rounds) and any(c['name']=='list_reminders' for r in rounds for c in r['tool_calls'])
                report['cases'].append({'name':label,'kind':kind,'prompt':prompt,'owner_active':actual,
                    'other_owner_active':len(store.active('other')),'reply':reply.text,
                    'history':store.conversation('owner')[-1]['content'],'rounds':rounds,
                    'actual_tool_executions':executions,'passed':passed,'seconds':round(time.monotonic()-start,3)})
            except Exception as error:
                report['cases'].append({'name':label,'error_type':type(error).__name__,'passed':False,'rounds':rounds})
            finally:await agent.calendar.close()
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(label,report['cases'][-1]['passed'],flush=True)
    print('Passed',sum(c['passed'] for c in report['cases']),'of',len(report['cases']))
    if not all(c['passed'] for c in report['cases']):raise SystemExit(1)

asyncio.run(main())
