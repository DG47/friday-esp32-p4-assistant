#!/usr/bin/env python3
"""Synthetic Mac report acceptance; dry-run default, mock Telegram, real local inference opt-in."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

class OfflineHTTP:
    offline_fixture=True
    def __init__(self):self.calls=[]
    async def call(self,method,payload):
        self.calls.append((method,payload))
        return {'ok':True,'result':{'message_id':len(self.calls)} if method=='sendMessage' else True}

async def evaluate(args):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    from app.project_report import ProjectGrant, ReportStore, ReportPlanner
    from app.providers.ollama_provider import OllamaProvider
    from app.report_context import ReportContext
    from app.report_telegram import ReportTelegramAdapter, ReportTelegramTransport
    from app.report_service import ReportService
    report={'status':'failed','real_local_inference':True,'telegram':'mock_only','real_account_calls':0,'source':'new original synthetic project','live_changes':False,'model_rounds':[],'paid_calls':0}
    args.evidence.mkdir(parents=True,exist_ok=True)
    class ObservedLocal(OllamaProvider):
        async def complete(self,*arguments):
            start=time.monotonic();turn=await super().complete(*arguments)
            report['model_rounds'].append({'seconds':round(time.monotonic()-start,3),'text':turn.text,'tool_calls':[vars(call) for call in turn.tool_calls]});return turn
    provider=ObservedLocal('http://127.0.0.1:11434',args.model,local_only=True,context_tokens=8192)
    with tempfile.TemporaryDirectory(prefix='friday-original-report-') as temporary:
        root=Path(temporary).resolve();project=root/'project';project.mkdir();(project/'src').mkdir()
        (project/'README.md').write_text('Original synthetic Lantern project.\nA tiny example for report verification.\n')
        (project/'src'/'example.py').write_text('def original_lantern_count():\n    return 23\n')
        artifacts=root/'reports';artifacts.mkdir(mode=0o700)
        grant=ProjectGrant('synthetic-owner','lantern',project,('README.md','src/example.py'))
        store=ReportStore(root/'tasks.db',[grant],artifacts);http=OfflineHTTP()
        transport=await ReportTelegramTransport.create(store,http,enabled=True)
        planner=ReportPlanner(store,provider);service=ReportService(store,planner,transport,enabled=True)
        def update(number,text):return {'update_id':number,'message':{'from':{'id':424242,'is_bot':False},'chat':{'id':424242,'type':'private'},'text':text}}
        try:
            await provider.ensure_available();await transport.bind_verified(424242,424242,'synthetic-owner');await service.start()
            before=grant.manifest()
            await transport.process_update(update(1,'/report lantern Prepare a read-only project report with inventory, README excerpt and source-line statistics.'))
            task=store.list('synthetic-owner')[0];await service.tick();planned=store.get('synthetic-owner',task['id'])
            if planned['state']!='awaiting_approval':raise RuntimeError('Actual local-model report proposal did not pass validation')
            if list(artifacts.iterdir()):raise RuntimeError('Execution occurred before approval')
            while await transport.deliver_once():pass
            token=next(payload['reply_markup']['inline_keyboard'][0][0]['callback_data'] for method,payload in http.calls if 'reply_markup' in payload)
            callback={'update_id':2,'callback_query':{'id':'original-synthetic-approval','from':{'id':424242,'is_bot':False},'message':{'chat':{'id':424242,'type':'private'}},'data':token}}
            await transport.process_update(callback);await service.tick()
            finished=store.get('synthetic-owner',task['id'])
            if finished['state']!='ready_for_review':raise RuntimeError('Report did not reach verified result')
            artifact=finished['artifact'];data=(artifacts/artifact['name']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=artifact['sha256'] or grant.manifest()!=before:raise RuntimeError('Report bytes/source verification failed')
            if not all(section in finished['scope']['sections'] for section in ['inventory','readme','source_statistics']):raise RuntimeError('Requested report sections were not planned')
            (args.evidence/'original-synthetic-project-report.md').write_bytes(data)
            while await transport.deliver_once():pass
            bridge=ReportContext(store,{'synthetic-lcd':'synthetic-owner'})
            phone=store.conversation('synthetic-owner',task['id'],'original-telegram-question','Telegram','status')
            lcd=bridge.ask('synthetic-lcd',task['id'],'original-lcd-question','status')
            if phone!=lcd:raise RuntimeError('Cross-surface record differs')
            report.update(task_id=task['id'],state=finished['state'],artifact=artifact,scope=finished['scope'],shared_context={'telegram':phone,'lcd':lcd,'native_surface':bridge.scene('synthetic-lcd',task['id']).model_dump(mode='json'),'production_device_route':'not mounted'},telegram_calls=len(http.calls),report_contents_transmitted=False)
            rounds_before_refusal=len(report['model_rounds'])
            unsupported=store.create('synthetic-owner','original-unsupported','Reserve a flight for tomorrow.','lantern')
            refusal=await planner.propose('synthetic-owner',unsupported['id'])
            if refusal['state']!='failed' or refusal.get('report_plan'):raise RuntimeError('Actual model did not refuse unsupported booking goal')
            if len(report['model_rounds'])!=rounds_before_refusal:raise RuntimeError('Out-of-scope goal reached the model')
            report['unsupported_goal']='deterministic capability refusal before model; no execution'
            report['goal_admission_scope']='Bounded English report intent; arbitrary semantic understanding not established'
            report['status']='passed'
        except Exception as error:report['error']=type(error).__name__+': '+str(error)
        finally:await service.close()
    (args.evidence/'project-report-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'evidence':str(args.evidence/'project-report-acceptance.json')}))
    if report['status']!='passed':raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-inference',action='store_true');parser.add_argument('--model',default='qwen3.6:35b');parser.add_argument('--evidence',type=Path);args=parser.parse_args()
    if not args.run_inference:print(json.dumps({'mode':'preparation_only','telegram':'mock_only','real_account_calls':0,'model_downloads':False,'capability':'enrolled read-only project report'}))
    else:
        if args.evidence is None:parser.error('--evidence is required')
        asyncio.run(evaluate(args))
