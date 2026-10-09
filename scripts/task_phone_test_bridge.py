"""Stdio-only in-process ASGI bridge for actual phone JS integration tests.
No listener, accounts, browser profile, production app or network requests.
"""
import asyncio,json,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'backend'))
from app.task_demo import create_demo_app
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as directory:
 app=create_demo_app(Path(directory)/'test-demo.db',enabled=True)
 async def yield_for_stop(task):await asyncio.sleep(.12)
 app.state.task_worker.before_execute=yield_for_stop
 with TestClient(app,client=('127.0.0.1',50000)) as client:
  for line in sys.stdin:
   request=json.loads(line)
   try:
    response=client.request(request['method'],request['path'],json=request.get('body'),headers=request.get('headers',{}))
    body=response.json()
    print(json.dumps({'id':request['id'],'status':response.status_code,'body':body}),flush=True)
   except Exception as error:
    print(json.dumps({'id':request['id'],'status':500,'body':{'detail':type(error).__name__}}),flush=True)
