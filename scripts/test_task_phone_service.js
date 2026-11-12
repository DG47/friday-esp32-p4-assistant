// Actual phone JS -> stdio bridge -> actual isolated FastAPI/SQLite task service.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path'),cp=require('child_process'),readline=require('readline');
const python=process.env.FRIDAY_TEST_PYTHON;if(!python)throw Error('Provide the existing offline test interpreter');
const bridge=cp.spawn(python,[path.join(__dirname,'task_phone_test_bridge.py')],{stdio:['pipe','pipe','inherit'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
const pending=new Map();let requestId=0;const elements=new Map();
readline.createInterface({input:bridge.stdout}).on('line',line=>{const message=JSON.parse(line);const resolve=pending.get(message.id);if(resolve){pending.delete(message.id);resolve({ok:message.status<400,json:async()=>message.body});}});
const element=()=>({hidden:false,disabled:false,textContent:'',value:'',replaceChildren(...children){this.children=children;},querySelector(){return this.button||(this.button=element());}});
for(const id of ['goal','fixture','client','message'])elements.set(id,element());
const context=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element},crypto:require('crypto').webcrypto,AbortController,console,setTimeout,clearTimeout,setInterval(){},
 fetch(url,options){return new Promise((resolve,reject)=>{const id=++requestId;pending.set(id,resolve);options.signal.addEventListener('abort',()=>{pending.delete(id);reject(Error('aborted'));});bridge.stdin.write(JSON.stringify({id,path:url,method:options.method,headers:options.headers,body:options.body?JSON.parse(options.body):undefined})+'\n');});}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../backend/app/static/task-phone.js'),'utf8'),context);
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(condition){for(let i=0;i<200;i++){if(vm.runInContext(condition,context))return;await delay(10);if(!vm.runInContext('busy||stopBusy',context))await vm.runInContext('refresh()',context);}throw Error('Timed out: '+condition);}
(async()=>{
 await waitFor('online');elements.get('goal').value='Diagnose synthetic check and prepare a reviewable patch';
 elements.get('start').onsubmit({preventDefault(){}});await waitFor("task?.state==='awaiting_clarification'&&!busy");
 const first=vm.runInContext('task.id',context);elements.get('fixture').value='range-total';elements.get('clarify').onclick();await waitFor("task?.state==='awaiting_approval'&&!busy");
 assert.equal(elements.get('approval').hidden,false);assert.match(elements.get('scope').textContent,/commands/);
 elements.get('approve').onclick();await waitFor("task?.state==='running'");assert.match(elements.get('progress').textContent,/Running/);
 await waitFor("task?.state==='ready_for_review'&&!busy");assert.equal(vm.runInContext('task.evidence.length',context),5);assert.match(elements.get('patch').textContent,/inclusive/);
 elements.get('client').value='Phone';elements.get('message').value='What failed?';elements.get('ask').onsubmit({preventDefault(){}});await waitFor('!busy');assert.match(elements.get('answer').textContent,/expected 10, got 6/);
 elements.get('client').value='Device text harness';elements.get('message').value='What changed?';elements.get('ask').onsubmit({preventDefault(){}});await waitFor('!busy');assert.match(elements.get('answer').textContent,/include the final/);assert.equal(vm.runInContext('task.conversation.length',context),2);assert.equal(vm.runInContext('task.id',context),first);
 // A second task proves the same actual client Stop control against live fixture work.
 elements.get('start').onsubmit({preventDefault(){}});await waitFor(`task?.id!=='${first}'&&task?.state==='awaiting_clarification'&&!busy`);
 elements.get('clarify').onclick();await waitFor("task?.state==='awaiting_approval'&&!busy");elements.get('approve').onclick();await waitFor("task?.state==='running'");
 vm.runInContext('busy=true;controls()',context);assert.equal(elements.get('stop').disabled,false);elements.get('stop').onclick();await waitFor("task?.state==='stopping'||task?.state==='stopped'");vm.runInContext('busy=false',context);await waitFor("task?.state==='stopped'");assert.equal(vm.runInContext('task.evidence.length',context),0);
 console.log('Actual phone JS + in-process task API: start/clarify/exact approval/progress/evidence/shared questions/running Stop PASS');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{bridge.stdin.end();});
