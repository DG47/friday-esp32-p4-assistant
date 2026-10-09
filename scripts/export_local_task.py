"""Run the contained original fixture and export actual process evidence."""
import asyncio
import json
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
from app.task_local import LocalTaskStore,LocalTaskWorker

async def main():
    out=root/'outputs/task-slice'
    store=LocalTaskStore(out/'local-task-demo.db',out/'local-projects')
    task=store.create('local-evidence-owner','local-demo-create:'+store.executor_digest()[:16],'Fix the registered disposable Python project','range-total')
    if task['state']=='awaiting_approval':
        store.approve('local-evidence-owner',task['id'],'local-demo-approve:'+task['id'],task['revision'],task['scope_digest'],task['nonce'])
    await LocalTaskWorker(store,'local-evidence-owner').run(task['id'])
    task=store.get('local-evidence-owner',task['id'])
    (out/'local-command-evidence.json').write_text(json.dumps(task,indent=2)+'\n')
    lines=['# Actual disposable project execution','', 'Final state: '+task['state'], '',
           'Planner: deterministic registered failure/patch; no model reasoning. No live project or Telegram connection.','']
    for e in task['evidence']:
        result=e['output'];lines+=['## '+e['step'],'',result['summary'],'']
        if 'argv' in result:lines+=['Command argv: `'+json.dumps(result['argv'])+'`','', 'Exit code: '+str(result['exit_code']), '', '```text',result['output'],'```','']
        if 'patch' in result:lines+=['```diff',result['patch'],'```','']
    (out/'local-execution-report.md').write_text('\n'.join(lines))
    print('Actual local fixture final state: '+task['state'])
    if task['state']!='ready_for_review':raise SystemExit(1)

asyncio.run(main())
