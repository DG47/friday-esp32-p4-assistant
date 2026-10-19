"""Actual browser DOM against isolated source; synthetic packets and silent speech spy."""
from pathlib import Path
import re,subprocess,sys,tempfile
root=Path(__file__).resolve().parents[1]
html=re.sub(r'<script src="[^"]+"></script>','',(root/'backend/app/static/index.html').read_text())
bootstrap='''class SyntheticSocket extends EventTarget { static OPEN=1; constructor(){super();this.readyState=1;this.sent=[];} send(value){this.sent.push(JSON.parse(value));} } window.WebSocket=SyntheticSocket;
window.syntheticSpoken=[];Object.defineProperty(window,'speechSynthesis',{value:{cancel(){},speak(value){syntheticSpoken.push(value.text)},speaking:false},configurable:true});
window.SpeechSynthesisUtterance=class extends EventTarget {constructor(text){super();this.text=text;}};
'''
qa=r'''
const results=[];function check(name,condition,observed){results.push({name,passed:!!condition,observed});}
socket.dispatchEvent(new Event('open'));
handleMessage({type:'ready',execution_stop_v1:true,state:'idle',surface:{kind:'ambient',title:'Synthetic ready'}});
sendUtterance('Synthetic slow task');const job=latestSubmitted,execution='a'.repeat(32);
handleMessage({type:'job.accepted',job_id:job,execution_id:execution});
executionButton.click();const stop=socket.sent.at(-1);
check('Scoped Stop enters pending',executionControl.phase==='pending',executionControl.phase);
const completed={type:'agent.result',job_id:job,delivery_id:execution,status:'completed',text:'Synthetic old task completion',surface:{kind:'message',title:'Synthetic obsolete completion'}};
renderSurface({kind:'message',title:'Synthetic read-only reference'});
handleMessage(completed);
check('In-flight result preserves read-only view',title.textContent==='Synthetic read-only reference',title.textContent);
check('Interrupted result still gets exact delivery ACK',socket.sent.at(-1).type==='result.ack'&&socket.sent.at(-1).job_id===job&&socket.sent.at(-1).delivery_id===execution,socket.sent.at(-1));
handleMessage(completed);
check('Duplicate interrupted result is ACKed without speech',socket.sent.at(-1).type==='result.ack'&&syntheticSpoken.length===0,syntheticSpoken.slice());
handleMessage({type:'reminder.alert',reminder_id:4242,text:'Synthetic unrelated alert',surface:{kind:'message',title:'Synthetic priority alert'}});
handleMessage(completed);
check('Unrelated alert keeps priority during interruption',title.textContent==='Synthetic priority alert',title.textContent);
check('In-flight completion after Stop intent must not restart speech',syntheticSpoken.length===0,syntheticSpoken.slice());
check('Normal result must not settle pending Stop',executionControl.phase==='pending',executionControl.phase);
handleMessage({...stop,type:'execution.stop.observed',ack_confirmed:true,authority_revoked:true,handler_drained:false,state_at_ack:'outcome_uncertain'});
check('Revocation retains uncertain state',executionControl.phase==='uncertain',executionControl.phase);
// A second independent run uses a fresh controller state to test a delayed result
// after an observed running-job revocation rather than after the initial intent.
executionControl.pending=null;executionControl.query=null;executionControl.target=null;executionControl.phase='idle';
sendUtterance('Synthetic second slow task');const second=latestSubmitted,secondExecution='b'.repeat(32);
handleMessage({type:'job.accepted',job_id:second,execution_id:secondExecution});executionButton.click();const secondStop=socket.sent.at(-1);
handleMessage({...secondStop,type:'execution.stop.observed',ack_confirmed:true,authority_revoked:true,handler_drained:false,state_at_ack:'outcome_uncertain'});
renderSurface({kind:'message',title:'Synthetic interrupted second task'});const before=syntheticSpoken.length;
handleMessage({...completed,job_id:second,delivery_id:secondExecution,text:'Synthetic late success after revocation'});
check('Delayed completion after revocation must not restart speech',syntheticSpoken.length===before,syntheticSpoken.slice(before));
check('Delayed completion must not replace interrupted task surface',title.textContent!=='Synthetic obsolete completion',title.textContent);
check('Delayed result keeps uncertain authority notice',executionControl.phase==='uncertain',executionNotice.textContent);
// Independent new-task fixture; this is not an automatic release of uncertainty.
executionControl.pending=null;executionControl.query=null;executionControl.target=null;executionControl.phase='idle';
sendUtterance('Synthetic permitted future task');const future=latestSubmitted,futureExecution='c'.repeat(32);
handleMessage({type:'job.accepted',job_id:future,execution_id:futureExecution});
handleMessage({...completed,job_id:future,delivery_id:futureExecution,text:'Synthetic normal future result',surface:{kind:'message',title:'Synthetic future task'}});
check('New admitted task still renders',title.textContent==='Synthetic future task',title.textContent);
check('New admitted task still speaks once',syntheticSpoken.at(-1)==='Synthetic normal future result',syntheticSpoken.slice());
const spokenCount=syntheticSpoken.length;
handleMessage({...completed,job_id:future,delivery_id:futureExecution,text:'Synthetic normal future result',surface:{kind:'message',title:'Synthetic future task'}});
check('Duplicate future result does not speak again',syntheticSpoken.length===spokenCount,syntheticSpoken.slice());
// Scene actions that enqueue ordinary reminder jobs need the same target capture.
sendSceneAction('complete:17');const sceneRequest=socket.sent.at(-1).request_id;
handleMessage({type:'job.accepted',job_id:sceneRequest,execution_id:'d'.repeat(32)});
check('Reminder scene-action job becomes a Stop target',executionControl.target?.job_id===sceneRequest&&!executionButton.disabled,executionControl.target);
const report=document.createElement('pre');report.id='qa-result';report.textContent=JSON.stringify(results);document.body.append(report);
'''
scripts=''.join('<script>'+s+'</script>' for s in (bootstrap,(root/'backend/app/static/execution_stop.js').read_text(),(root/'backend/app/static/app.js').read_text(),qa))
with tempfile.TemporaryDirectory(prefix='friday-interrupted-dom-') as folder:
    fixture=Path(folder)/'fixture.html';fixture.write_text(html.replace('</body>',scripts+'</body>'))
    subprocess.run([sys.executable,str(root/'scripts/visual_qa.py'),str(fixture)],check=True)
