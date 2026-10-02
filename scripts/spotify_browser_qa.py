"""Actual simulator/Chrome CDP QA. Requires isolated loopback fixture server."""
import asyncio,base64,json,urllib.request
from pathlib import Path
import websockets
OUT=Path('outputs/spotify');OUT.mkdir(parents=True,exist_ok=True)
async def run():
    tabs=json.load(urllib.request.urlopen('http://127.0.0.1:18766/json'))
    async with websockets.connect(next(t for t in tabs if t['type']=='page')['webSocketDebuggerUrl']) as ws:
        seq=0
        async def call(method,params={}):
            nonlocal seq
            seq+=1;identifier=seq;await ws.send(json.dumps({'id':identifier,'method':method,'params':params}))
            while True:
                response=json.loads(await asyncio.wait_for(ws.recv(),10))
                if response.get('id')==identifier:
                    if 'error' in response:raise RuntimeError(response['error'])
                    return response.get('result',{})
        async def js(expression):
            result=await call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})
            if 'exceptionDetails' in result:raise RuntimeError(result['exceptionDetails'])
            return result['result'].get('value')
        async def pause():await asyncio.sleep(.25)
        async def button(label):
            await js("[...document.querySelectorAll('button')].find(x=>x.textContent=="+json.dumps(label)+")?.click()")
            await pause()
        async def snap(name):
            await asyncio.sleep(.6)
            result=await call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False,'fromSurface':True})
            (OUT/(name+'.png')).write_bytes(base64.b64decode(result['data']))
        await call('Page.enable');await call('Runtime.enable')
        await call('Page.navigate',{'url':'http://127.0.0.1:18765/'})
        await asyncio.sleep(.8)
        report={}
        for width,height in [(1280,900),(720,1000),(390,900)]:
            await call('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':False})
            await js("fetch('/qa/ready',{method:'POST'}).then(r=>r.json())")
            await button('Local status');await button('Music');print('scene',await js("document.querySelector('#screen').dataset.surface"),flush=True)
            await snap(f'ready-{width}')
            report[str(width)]=await js("(()=>{let s=document.querySelector('#screen').getBoundingClientRect(),a=document.querySelector('#scene-actions').getBoundingClientRect();return {surface:document.querySelector('#screen').dataset.surface,screen:{w:s.width,h:s.height},actionsVisible:a.bottom<=s.bottom,labels:[...document.querySelectorAll('#scene-actions button')].map(x=>x.textContent),horizontalOverflow:document.documentElement.scrollWidth>innerWidth}})()")
        await js("fetch('/qa/state').then(r=>r.json()).then(x=>window.qaOldToken=x.action_ids[0])")
        await js("fetch('/qa/expire',{method:'POST'}).then(r=>r.json())")
        await js("socket.send(JSON.stringify({type:'scene.action',action:window.qaOldToken}))")
        await pause();report['expired']=await js("document.querySelector('#transcript').textContent")
        await js("fetch('/qa/ready',{method:'POST'}).then(r=>r.json())")
        await button('Local status');await button('Music')
        await button('Pause');report['pause']=await js("[...document.querySelectorAll('#scene-actions button')].map(x=>x.textContent)")
        await button('Play')
        beforeSkip=await js("fetch('/qa/state').then(r=>r.json()).then(x=>x.calls.filter(c=>c[0]=='POST').length)")
        await js("document.querySelector('#scene-actions button:nth-child(3)').click();document.querySelector('#scene-actions button:nth-child(3)').click()")
        await pause();report['duplicate']=await js("fetch('/qa/state').then(r=>r.json())")
        report['duplicateSentOnce']=sum(c[0]=='POST' for c in report['duplicate']['calls'])-beforeSkip==1
        await button('Dismiss');report['dismiss']=await js("document.querySelector('#screen').dataset.surface")
        report['tokensAfterDismiss']=await js("fetch('/qa/state').then(r=>r.json())")
        await button('Local status');await button('Music')
        await js("document.querySelector('#scene-actions button:nth-child(3)').click();sendSceneAction('dismiss')")
        await pause();report['interruptedDismiss']=await js("document.querySelector('#screen').dataset.surface")
        for scenario in ['disabled','stale','empty','no-device','restricted','revoked','error','backoff','unicode']:
            await js("fetch('/qa/"+scenario+"',{method:'POST'}).then(r=>r.json())")
            await button('Local status');await button('Music');print('scene',await js("document.querySelector('#screen').dataset.surface"),flush=True)
            await snap(scenario+'-390')
            report[scenario]=await js("({title:document.querySelector('#scene-title').textContent,subtitle:document.querySelector('#scene-subtitle').textContent,state:document.querySelector('#scene-items').textContent,actions:[...document.querySelectorAll('#scene-actions button')].map(x=>x.textContent)})")
            if scenario=='backoff':
                before=await js("fetch('/qa/state').then(r=>r.json()).then(x=>x.calls.length)")
                await button('Local status');await button('Music')
                after=await js("fetch('/qa/state').then(r=>r.json()).then(x=>x.calls.length)")
                report['backoffNoExtraReads']=before==after
            await button('Dismiss')
        await js("fetch('/qa/sync',{method:'POST'}).then(r=>r.json())")
        await button('Local status');await button('Music')
        report['timedLines']=[]
        for position,expected in [(1200,'The copper kite turns.'),(3200,'A paper boat follows.'),(7200,'We leave a light by the gate.'),(1200,'The copper kite turns.')]:
            await js("fetch('/qa/sync_position/"+str(position)+"',{method:'POST'}).then(r=>r.json())")
            await pause();line=await js("document.querySelector('#scene-subtitle').textContent")
            assert line==expected
            report['timedLines'].append({'position_ms':position,'line':line})
            await snap('sync-'+str(position)+'-390')
        await button('Dismiss')
        await js("fetch('/qa/owned',{method:'POST'}).then(r=>r.json())")
        await button('Local status');await button('Music');await pause()
        assert await js("document.querySelector('#scene-subtitle').textContent")=='The copper kite turns.'
        report['ownedSession']=await js("({request:mediaRequest,session:mediaSession,sequence:mediaSequence})")
        await js("window.oldOwned={type:'surface',surface:{kind:'media',title:'Old fixture',subtitle:'Old in-flight lyric'},media_request_id:mediaRequest,media_session_id:mediaSession,media_sequence:mediaSequence+100}")
        await js("fetch('/qa/owned_position/3200',{method:'POST'}).then(r=>r.json())");await pause();await pause()
        assert await js("document.querySelector('#scene-subtitle').textContent")=='A paper boat follows.'
        await snap('owned-timed-390')
        await button('Dismiss');await js("handleMessage(window.oldOwned)")
        report['ownedDismissRejectsOld']=await js("screen.dataset.surface==='ambient'")
        await button('Local status');await button('Music');await pause()
        await js("handleMessage(window.oldOwned)")
        report['ownedReentryRejectsOld']=await js("title.textContent!=='Old fixture' && mediaRequest!==window.oldOwned.media_request_id")
        await js("handleMessage({type:'reminder.alert',reminder_id:1,text:'Original fixture reminder',surface:{kind:'reminder',title:'Fixture reminder'}});handleMessage(window.oldOwned)")
        report['ownedReminderPriority']=await js("screen.dataset.surface==='reminder' && mediaRequest===null")
        await button('Acknowledge');await button('Local status');await button('Music');await pause()
        await js("renderSurface({...oldOwned.surface,expires_ms:1000})");await asyncio.sleep(1.4)
        await js("handleMessage(window.oldOwned)")
        report['ownedExpiryRejectsOld']=await js("screen.dataset.surface==='ambient' && mediaRequest===null")
        assert all(report[key] for key in ['ownedDismissRejectsOld','ownedReentryRejectsOld','ownedReminderPriority','ownedExpiryRejectsOld'])
        assert all(report[str(w)]['actionsVisible'] and not report[str(w)]['horizontalOverflow'] for w in [1280,720,390])
        assert report['pause'][0]=='Play' and report['duplicateSentOnce']
        assert report['dismiss']=='ambient' and report['interruptedDismiss']=='ambient'
        assert report['tokensAfterDismiss']['tokens']==0 and report['backoffNoExtraReads']
        assert 'expired' in report['expired']
        assert all(report[s]['actions']==['Dismiss'] for s in ['disabled','stale','empty','no-device','restricted','revoked','error','backoff'])
        (OUT/'browser-qa.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
asyncio.run(run())
