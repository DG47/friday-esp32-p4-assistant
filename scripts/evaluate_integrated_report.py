#!/usr/bin/env python3
"""Actual production dispatcher acceptance on synthetic enrollment; inference opt-in."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
import subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-inference',action='store_true')
parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
parser.add_argument('--evidence',type=Path)
args=parser.parse_args()
if not args.run_inference:
    print(json.dumps({'mode':'preparation_only','model':'qwen3.6:35b','telegram':'mock_only','production_path':'FastAPI /ws/device','live_changes':False}));raise SystemExit(0)
if args.evidence is None:parser.error('--evidence is required')
if not args.worker:
    try:
        result=subprocess.run([sys.executable,__file__,'--run-inference','--worker','--evidence',str(args.evidence)],timeout=90)
    except subprocess.TimeoutExpired:
        raise SystemExit('Acceptance exceeded 90-second wall deadline; no success claimed')
    raise SystemExit(result.returncode)
repo=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(repo/'backend'),str(repo/'backend/tests')]
from test_report_integration import IntegratedReports
from app import main
from app.providers.ollama_provider import OllamaProvider

rounds=[]
class ObservedLocal(OllamaProvider):
    async def complete(self,*a,**kw):
        start=time.monotonic();turn=await super().complete(*a,**kw)
        rounds.append({'seconds':round(time.monotonic()-start,3),'text':turn.text,'tool_calls':[vars(c) for c in turn.tool_calls]})
        return turn
# Verify strict loopback/cloud-disabled readiness before any admission.
asyncio.run(ObservedLocal('http://127.0.0.1:11434','qwen3.6:35b',local_only=True,context_tokens=8192).ensure_available())
IntegratedReports.provider_type=ObservedLocal
IntegratedReports.model_name='qwen3.6:35b'
IntegratedReports.review_polls=3500
f=IntegratedReports();f.setUp();args.evidence.mkdir(parents=True,exist_ok=True)
evidence={'status':'failed','production_dispatcher':True,'real_local_inference_requested':True,'real_local_inference_observed':False,'wall_deadline_seconds':90,'telegram':'mock_only','model_concurrency':1,'real_account_calls':0,'live_changes':False}
try:
    before=(f.root/'source'/'README.md').read_bytes()
    with f.client.websocket_connect('/ws/device/synthetic-p4') as ws:
        f.authenticate(ws);task=f.create(ws);review=f.review(task)
        f.assertEqual(review['state'],'awaiting_approval')
        f.assertEqual(list((f.root/'artifacts').iterdir()),[])
        # Duplicate actual utterance retains durable task and does not replan.
        f.assertEqual(f.create(ws),task)
        f.assertEqual(len(main.report_runtime.store.list('original-owner')),1)
        f.approve(task,review)
        result=f.until(ws,lambda p:p['type']=='agent.result')
        f.assertEqual(result['status'],'completed');f.assertEqual(result['report_state'],'ready_for_review')
        finished=main.report_runtime.store.get('original-owner',task)
        data=(f.root/'artifacts'/finished['artifact']['name']).read_bytes()
        f.assertEqual(hashlib.sha256(data).hexdigest(),finished['artifact']['sha256'])
        f.assertIn(before.decode().strip(),data.decode())
        f.assertIn('README.md',data.decode())
        f.assertEqual((f.root/'source'/'README.md').read_bytes(),before)
        f.client.portal.call(main.report_runtime.transport.process_update,{'update_id':10,'message':{'from':{'id':100,'is_bot':False},'chat':{'id':100,'type':'private'},'text':'/ask '+task+' status'}})
        phone=main.report_runtime.store.turns('original-owner',task)[-1]
        ws.send_json({'type':'utterance','request_id':'original-device-question','text':'/ask '+task+' status'})
        f.until(ws,lambda p:p['type']=='job.accepted')
        f.until(ws,lambda p:p.get('job_id')==task and p['type'] in {'surface','agent.result'})
        device=main.report_runtime.store.turns('original-owner',task)[-1]
        f.assertEqual(phone['answer'],device['answer'])
        with f.jobs._connect() as db:f.assertEqual(db.execute('SELECT COUNT(*) FROM agent_jobs').fetchone()[0],0)
    with f.client.websocket_connect('/ws/device/synthetic-p4') as ws:
        f.authenticate(ws)
        ws.send_json({'type':'scene.action','request_id':'original-real-recover','action':'recover'})
        replay=f.until(ws,lambda p:p['type']=='agent.result')
        f.assertEqual(replay['delivery_id'],result['delivery_id']);f.assertEqual(replay['job_id'],task)
        ws.send_json({'type':'result.ack','job_id':task,'delivery_id':replay['delivery_id']})
        ws.send_json({'type':'scene.action','request_id':'original-real-recover-acked','action':'recover'})
        restored=f.until(ws,lambda p:p['type']=='surface' and p.get('job_id')==task)
        f.assertEqual(len(list((f.root/'artifacts').iterdir())),1)
        f.assertEqual(len(rounds),1)
        final_same=main.report_runtime.store.get('original-owner',task)
        f.assertEqual(final_same['fence'],1)
        # New task: same production route and native Stop before execution/approval.
        ws.send_json({'type':'utterance','request_id':'original-real-stop-task','text':'report demo Prepare a read-only project report'})
        stopped_id=f.until(ws,lambda p:p['type']=='job.accepted')['job_id']
        stop_review=f.review(stopped_id)
        surface=f.until(ws,lambda p:p['type']=='surface' and p.get('job_id')==stopped_id and p['surface']['subtitle'].startswith('State: awaiting_approval'))
        token=next(a['id'] for a in surface['surface']['actions'] if a['label']=='Stop')
        ws.send_json({'type':'scene.action','request_id':'original-real-stop','action':token})
        stopped=f.until(ws,lambda p:p['type']=='agent.result' and p['job_id']==stopped_id)
        f.assertEqual(stopped['report_state'],'stopped');f.assertEqual(stopped['status'],'failed')
        f.assertEqual(len(list((f.root/'artifacts').iterdir())),1)
    (args.evidence/'original-integrated-report.md').write_bytes(data)
    for name,packet in [('result',result),('recovered',replay),('acked-surface',restored),('stopped',stopped)]:
        (args.evidence/(name+'.json')).write_text(json.dumps(packet,indent=2)+'\n')
    evidence.update(status='passed',real_local_inference_observed=True,execution_admission_fence=final_same['fence'],task_events=final_same['events'],task_id=task,artifact=finished['artifact'],scope=finished['scope'],shared_status=phone['answer'],model_rounds=rounds,execution_count=1,stop={'task_id':stopped_id,'state':stopped['report_state'],'artifact_created':False},duplicate_and_reconnect='one durable task, same delivery, one artifact, no repeated inference',generic_agent_jobs=0)
except Exception as error:
    evidence.update(error=type(error).__name__+': '+str(error),model_rounds=rounds,real_local_inference_observed=bool(rounds))
    raise
finally:
    (args.evidence/'acceptance.json').write_text(json.dumps(evidence,indent=2)+'\n');f.tearDown()
print(json.dumps({'status':evidence['status'],'evidence':str(args.evidence/'acceptance.json')}))
