'use strict';
const el = id => document.getElementById(id);
let task = null, online = false, busy = false, stopBusy = false;
const stopIds = new Map();
let selectionGeneration = 0;
const identity = () => crypto.randomUUID();
async function api(path, payload) {
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),8000);
  try {
    const response = await fetch(path, {method: payload ? 'POST' : 'GET', headers: payload ? {'Content-Type':'application/json','X-Friday-Fixture':'1'} : {}, body: payload ? JSON.stringify(payload) : undefined, cache:'no-store', signal:controller.signal});
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Request rejected');
    return body;
  } finally { clearTimeout(timer); }
}
function render(t) {
  if(task && task.id===t.id && t.revision<task.revision)return;
  task=t; el('task').hidden=false; el('identity').textContent=`TASK ${t.id.slice(0,8)} · REV ${t.revision}`;
  el('goal-view').textContent=t.goal; el('state').textContent=t.state.replaceAll('_',' ');
  el('progress').textContent=t.events.at(-1)?.message || '';
  el('clarification').hidden=t.state!=='awaiting_clarification'; el('approval').hidden=t.state!=='awaiting_approval';
  el('scope').textContent=JSON.stringify({scope:t.scope,revision:t.revision,digest:t.scope_digest},null,2);
  el('events').replaceChildren(...t.events.map(event=>{const li=document.createElement('li');li.textContent=event.message;return li;}));
  el('artifact').hidden=!t.artifact; el('patch').textContent=t.artifact?.patch || '';
  el('evidence').textContent=JSON.stringify(t.evidence,null,2);
  el('conversation').replaceChildren(...(t.conversation||[]).map(turn=>{const p=document.createElement('p');p.textContent=`${turn.client}: ${turn.text}\nFriday: ${turn.answer}`;return p;}));
  controls();
}
function controls(){['approve','clarify','review'].forEach(id=>el(id).disabled=!online||busy);el('stop').disabled=!online||stopBusy||!task||['stopped','failed','ready_for_review'].includes(task.state);el('start').querySelector('button').disabled=!online||busy;el('ask').querySelector('button').disabled=!online||busy;}
async function refresh(){try{const data=await api('/api/tasks');online=true;el('connection').textContent='Local · connected';el('tasks').replaceChildren(...data.tasks.map(t=>{const b=document.createElement('button');b.textContent=t.goal.slice(0,28);b.onclick=()=>select(t.id);return b;}));if(task){const selected=task.id;const latest=await api(`/api/tasks/${selected}`);if(task?.id===selected)render(latest);}}catch(error){online=false;el('connection').textContent='Offline · last known';el('error').textContent='Connection unavailable. No action is queued; Stop is not confirmed.';}controls();}
async function select(id){const generation=++selectionGeneration;try{const chosen=await api(`/api/tasks/${id}`);if(generation===selectionGeneration)render(chosen);}catch(error){el('error').textContent=error.message;}}
async function act(operation){if(!online||busy)return;busy=true;controls();el('error').textContent='';try{await operation();await refresh();}catch(error){el('error').textContent=error.message;await refresh();}finally{busy=false;controls();}}
el('start').onsubmit=e=>{e.preventDefault();const generation=++selectionGeneration;act(async()=>{const created=await api('/api/tasks',{action_id:identity(),goal:el('goal').value});if(generation===selectionGeneration)render(created);});};
el('clarify').onclick=()=>{const shown=task;act(async()=>{const updated=await api(`/api/tasks/${shown.id}/clarify`,{action_id:identity(),revision:shown.revision,fixture:el('fixture').value});if(task?.id===shown.id)render(updated);});};
el('approve').onclick=()=>{const displayed=task;act(async()=>{const updated=await api(`/api/tasks/${displayed.id}/approve`,{action_id:identity(),revision:displayed.revision,scope_digest:displayed.scope_digest,nonce:displayed.nonce});if(task?.id===displayed.id)render(updated);});};
el('review').onclick=()=>{const shown=task;act(async()=>{const updated=await api(`/api/tasks/${shown.id}/review`,{action_id:identity(),revision:shown.revision});if(task?.id===shown.id)render(updated);});};
el('stop').onclick=async()=>{
  if(!online||stopBusy||!task)return;
  const selected=task.id;const action=stopIds.get(selected)||identity();stopIds.set(selected,action);
  stopBusy=true;controls();el('error').textContent='';
  try {const stopped=await api(`/api/tasks/${selected}/stop`,{action_id:action});if(task?.id===selected)render(stopped);}
  catch(error){el('error').textContent='Stop not confirmed. Refresh and explicitly retry Stop; the same request identity is retained.';}
  finally {stopBusy=false;controls();}
};
el('ask').onsubmit=e=>{e.preventDefault();act(async()=>{const result=await api(`/api/tasks/${task.id}/conversation`,{action_id:identity(),client:el('client').value,text:el('message').value});el('answer').textContent=result.answer;el('message').value='';});};
refresh();setInterval(()=>{if(!busy)refresh();},700);
