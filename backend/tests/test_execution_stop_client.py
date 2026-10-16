from pathlib import Path
import shutil
import subprocess
import unittest
class ExecutionStopClientTests(unittest.TestCase):
    def test_deadline_scope_disconnect_lost_ack_and_result_do_not_imply_stop_success(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Node is required for client state regression')
        module=Path(__file__).resolve().parents[1]/'app/static/execution_stop.js'
        script=r'''
const assert=require('node:assert/strict');const {ExecutionStopClient}=require(process.argv[1]);
let now=0,sent=[];const c=new ExecutionStopClient(p=>{sent.push(p);return true},()=>now);
const job='synthetic-job',execution='a'.repeat(32);c.capture(job,execution);c.request('stop-1');
now=1001;c.tick();assert.equal(c.phase,'unconfirmed');assert.equal(c.blocked(),true);
c.disconnected();assert.equal(c.phase,'unconfirmed');assert.equal(sent.length,1);
c.result(job,execution);assert.equal(c.phase,'unconfirmed');
let ack={type:'execution.stop.observed',request_id:'stop-1',job_id:job,execution_id:execution,ack_confirmed:true,authority_revoked:true,handler_drained:true,state_at_ack:'outcome_uncertain'};
assert.equal(c.receive({...ack,execution_id:'b'.repeat(32)}),false);
assert.equal(c.receive({...ack,request_id:'stale'}),false);
assert.equal(c.receive(ack),true);assert.equal(c.phase,'uncertain');assert.equal(c.blocked(),true);
c.status('read-1');assert.equal(sent.at(-1).type,'execution.status');
assert.equal(c.receive({type:'execution.stop.status',request_id:'read-1',job_id:job,execution_id:execution,job_status:'running',authority_revoked:true,outcome_uncertain:true,no_execution_admitted:false}),true);
assert.equal(c.phase,'uncertain');c.request('new-stop-id');assert.equal(sent.at(-1).request_id,'stop-1');
ack={...ack,state_at_ack:'stopped_before_admission',handler_drained:false};assert.equal(c.receive(ack),true);
assert.equal(c.phase,'stopped_before_admission');assert.equal(c.blocked(),false);
assert.equal(c.receive({...ack,state_at_ack:'outcome_uncertain'}),false);
assert.equal(c.capture(job,execution),false); // terminal before delayed acceptance
const d=new ExecutionStopClient(()=>false,()=>0);d.capture(job,'c'.repeat(32));d.request('offline');
assert.equal(d.phase,'unconfirmed');assert.equal(d.blocked(),true);
const restored=new ExecutionStopClient(()=>true,()=>0);
assert.equal(restored.restore({job_id:job,execution_id:execution}),true);
assert.equal(restored.blocked(),true);
assert.equal(restored.restore({job_id:'different',execution_id:'b'.repeat(32)}),false);
restored.status('read-restored');
assert.equal(restored.receive({type:'execution.stop.status',request_id:'read-restored',job_id:job,execution_id:execution,job_status:'failed',authority_revoked:true,outcome_uncertain:true,no_execution_admitted:true}),false);
assert.equal(restored.phase,'uncertain');
console.log('deadline, disconnect, lost/stale ACK, scope, retry, status, terminal ordering: pass');
'''
        result=subprocess.run([node,'-e',script,str(module)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
