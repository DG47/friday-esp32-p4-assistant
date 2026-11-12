// Deterministic DOM/network race tests of the actual phone client source.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map(),timers=new Map();let timerId=0,requests=[];
const element=()=>({hidden:false,disabled:false,textContent:'',value:'',replaceChildren(){},querySelector(){return this.button||(this.button=element());}});
const context=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element},crypto:require('crypto').webcrypto,AbortController,console,
 setInterval(){},setTimeout(fn){timers.set(++timerId,fn);return timerId;},clearTimeout(id){timers.delete(id);},
 fetch(path,options){return new Promise((resolve,reject)=>{requests.push({path,options,resolve,reject});options.signal.addEventListener('abort',()=>reject(new Error('aborted')));});}});
vm.runInContext(fs.readFileSync(require('path').join(__dirname,'../backend/app/static/task-phone.js'),'utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const snapshot=(id,revision,state='queued')=>({id,revision,state,goal:'synthetic',events:[{message:state}],evidence:[],artifact:null,scope:{},scope_digest:'a'.repeat(64),nonce:'b'.repeat(32)});
const answer=(request,body)=>request.resolve({ok:true,json:async()=>body});
(async()=>{
 answer(requests.shift(),{tasks:[]});await tick();
 context.sample=snapshot('task',3);vm.runInContext('render(sample);online=true;busy=true;controls()',context);
 assert.equal(elements.get('stop').disabled,false,'Stop must remain available during another request');
 context.old=snapshot('task',2,'running');vm.runInContext('render(old)',context);assert.equal(elements.get('state').textContent,'queued');
 // A newer selection wins even when the earlier response arrives later.
 vm.runInContext("select('a');select('b')",context);const a=requests.shift(),b=requests.shift();answer(b,snapshot('b',1));await tick();answer(a,snapshot('a',9));await tick();assert.equal(vm.runInContext('task.id',context),'b');
 // Stop runs while chat/approval is busy and a stale response cannot undo it.
 context.sample=snapshot('task',4);vm.runInContext('render(sample);online=true;busy=true',context);
 elements.get('stop').onclick();const stop=requests.shift();answer(stop,snapshot('task',5,'stopping'));await tick();context.old=snapshot('task',4,'queued');vm.runInContext('render(old)',context);assert.equal(elements.get('state').textContent,'stopping');
 // A failed Stop retains the exact action ID for explicit retry.
 vm.runInContext('busy=false',context);elements.get('stop').onclick();const first=requests.shift();const id=JSON.parse(first.options.body).action_id;first.reject(new Error('offline'));await tick();assert.match(elements.get('error').textContent,/Stop not confirmed/);
 elements.get('stop').onclick();const retry=requests.shift();assert.equal(JSON.parse(retry.options.body).action_id,id);answer(retry,snapshot('task',6,'stopped'));await tick();
 // Timeout remains active while response.json() stalls, not only until headers.
 const pending=vm.runInContext("api('/hung-body')",context);const body=requests.shift();let rejectBody;
 body.resolve({ok:true,json:()=>new Promise((resolve,reject)=>{rejectBody=reject;body.options.signal.addEventListener('abort',()=>reject(new Error('body aborted')));})});await tick();assert.equal(timers.size,1);[...timers.values()][0]();await assert.rejects(pending,/body aborted/);assert.equal(timers.size,0);
 console.log('Phone client Stop independence, stale revision/selection, idempotent retry and body timeout: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
