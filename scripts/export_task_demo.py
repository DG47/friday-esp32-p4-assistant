"""Export actual fixture-store transitions as an offline, recorded phone replay.
This artifact displays real recorded evidence; controls only browse snapshots.
"""
import asyncio,json,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'backend'))
from app.task_slice import TaskStore,TaskWorker
out=root/'outputs/task-slice';out.mkdir(parents=True,exist_ok=True)
async def main():
 with tempfile.TemporaryDirectory() as directory:
  store=TaskStore(Path(directory)/'demo.sqlite');owner='demo';frames=[]
  def capture(task):frames.append({**store.get(owner,task['id']),'conversation':store.turns(owner,task['id'])})
  task=store.create(owner,'original-start','Diagnose the synthetic failure and prepare a patch');capture(task)
  task=store.clarify(owner,task['id'],'choose',task['revision'],'range-total');capture(task)
  task=store.approve(owner,task['id'],'approve',task['revision'],task['scope_digest'],task['nonce']);capture(task)
  worker=TaskWorker(store,owner)
  for _ in range(5):
   await worker.step(task['id']);capture(task)
   if store.get(owner,task['id'])['step']==2:
    store.conversation(owner,task['id'],'phone-question','Phone','What failed?');capture(task)
  store.conversation(owner,task['id'],'device-question','Device text harness','What changed?');capture(task)
  (out/'recorded-transitions.json').write_text(json.dumps(frames,indent=2)+'\n')
  css=(root/'backend/app/static/task-phone.css').read_text();html=(root/'backend/app/static/task-phone.html').read_text()
  html=html.replace('<link rel="stylesheet" href="/task-phone.css">','<style>'+css+'</style>').replace('<script src="/task-phone.js" defer></script>','')
  html=html.replace('Local fixture proof · No real Mac control, model, login or remote access.','Recorded backend execution · Replay only. No live actions, Mac control, model, login or remote access.')
  js=(root/'backend/app/static/task-phone.js').read_text();js=js[:js.index("el('start').onsubmit=")];js=js.replace('refresh();setInterval', '//refresh();setInterval')
  replay='''\nconst frames=RECORDS;let frame=0;online=false;busy=true;
 el('start').hidden=true;el('ask').hidden=true;el('stop').disabled=true;el('client').disabled=true;
 const replay=document.createElement('section');const back=document.createElement('button');const next=document.createElement('button');const note=document.createElement('p');
 back.textContent='Previous recorded step';next.textContent='Next recorded step';replay.append(back,next,note);document.querySelector('header').after(replay);
 function show(){task=null;render(frames[frame]);note.textContent=`Recorded snapshot ${frame+1} of ${frames.length}. These are persisted fixture outputs, not live task controls.`;el('connection').textContent='Offline replay';el('stop').disabled=true;}
 back.onclick=()=>{frame=Math.max(0,frame-1);show();};next.onclick=()=>{frame=Math.min(frames.length-1,frame+1);show();};
 const chosen=Number(new URLSearchParams(location.search).get('frame'));if(Number.isInteger(chosen)&&chosen>=0&&chosen<frames.length)frame=chosen;
 show();\n'''.replace('RECORDS',json.dumps(frames).replace('<','\\u003c'))
  html=html.replace('</body>','<script>'+js+replay+'</script></body>');(out/'phone-recorded-demo.html').write_text(html)
 print('Exported',len(frames),'actual fixture task snapshots and recorded phone replay.')
asyncio.run(main())
