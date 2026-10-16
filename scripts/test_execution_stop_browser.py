#!/usr/bin/env python3
"""Actual local Chrome DOM, synthetic socket/clock, no server/account/audio work."""
import re
import subprocess
import sys
import tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
html=(root/'backend/app/static/index.html').read_text()
html=re.sub(r'<script src="[^"]+"></script>','',html)
bootstrap='''class SyntheticSocket extends EventTarget { static OPEN=1; constructor(){super();this.readyState=1;this.sent=[];} send(value){this.sent.push(JSON.parse(value));} close(){this.dispatchEvent(new Event('close'));} } window.WebSocket=SyntheticSocket;'''
qa=r'''
const results=[];function check(name,condition){results.push({name,passed:!!condition});}
try {
let clock=0;executionControl.now=()=>clock;socket.dispatchEvent(new Event('open'));
check('Not enabled before negotiated readiness',executionButton.disabled);
handleMessage({type:'ready',execution_stop_v1:true,state:'idle',surface:{kind:'ambient',title:'Synthetic Friday'}});
sendUtterance('Synthetic original task');const job=latestSubmitted,execution='a'.repeat(32);
handleMessage({type:'job.accepted',job_id:job,execution_id:execution});check('Explicit admitted target enables Stop',!executionButton.disabled);
executionButton.click();const stop=socket.sent.at(-1);check('Scoped Stop packet',stop.type==='execution.stop'&&stop.job_id===job&&stop.execution_id===execution);
const count=socket.sent.length;executionButton.click();check('Pending duplicate touch disabled',executionButton.disabled&&socket.sent.length===count);
clock=1001;executionControl.tick();executionRender();check('Deadline is unconfirmed with explicit retry',executionControl.phase==='unconfirmed'&&executionButton.textContent==='Retry Stop'&&executionNotice.textContent.includes('may continue'));
sendSceneAction('complete:1');sendSceneAction('confirm');sendSceneAction('memyes:synthetic');sendUtterance('Synthetic later task');check('Unknown outcome holds mutations',socket.sent.length===count);
sendSceneAction('reminders');check('Read-only navigation remains available',socket.sent.at(-1).action==='reminders');
socket.dispatchEvent(new Event('close'));clearTimeout(reconnectTimer);check('Disconnect disables controls without success',executionButton.disabled&&executionControl.phase==='unconfirmed');
handleMessage({type:'ready',execution_stop_v1:true,state:'idle',surface:{kind:'ambient',title:'Synthetic Friday'}});check('Reconnect requests status, never automatic Stop',socket.sent.at(-1).type==='execution.status');
executionButton.click();check('Explicit retry retains action identity',socket.sent.at(-1).request_id===stop.request_id);
handleMessage({...stop,type:'execution.stop.observed',ack_confirmed:true,authority_revoked:true,handler_drained:true,state_at_ack:'outcome_uncertain'});
check('Drained handler remains uncertain',executionControl.phase==='uncertain'&&executionButton.disabled&&executionNotice.textContent.includes('effects may remain'));
handleMessage({...stop,type:'execution.stop.observed',ack_confirmed:true,authority_revoked:true,handler_drained:false,state_at_ack:'stopped_before_admission'});
check('Only pre-admission proof shows Stopped',executionControl.phase==='stopped_before_admission'&&executionButton.disabled);
handleMessage({type:'job.accepted',job_id:job,execution_id:execution});check('Late acceptance cannot reopen settled target',executionButton.disabled&&!executionControl.target);
} catch(error){results.push({name:String(error),passed:false});}
const report=document.createElement('pre');report.id='qa-result';report.textContent=JSON.stringify(results);document.body.append(report);
'''
scripts=''.join('<script>'+s+'</script>' for s in [bootstrap,(root/'backend/app/static/execution_stop.js').read_text(),(root/'backend/app/static/app.js').read_text(),qa])
with tempfile.TemporaryDirectory(prefix='friday-stop-dom-') as folder:
    fixture=Path(folder)/'fixture.html';fixture.write_text(html.replace('</body>',scripts+'</body>'))
    subprocess.run([sys.executable,str(root/'scripts/visual_qa.py'),str(fixture)],check=True)
