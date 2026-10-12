"""Reproduce isolated conversation plumbing; no inference or real action adapters."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--native-binary',type=Path)
p.add_argument('--before-binary',type=Path)
a=p.parse_args()
out=root/'outputs/conversation';out.mkdir(parents=True,exist_ok=True)
env={**os.environ,'PYTHONPATH':str(root/'backend'),'LLM_PROVIDER':'mock','TTS_PROVIDER':'none','STT_PROVIDER':'none','WEATHER_PROVIDER':'none','FRIDAY_CONVERSATION_FIXTURE':str(out/'scenario.json')}
with (out/'scenario-tests.log').open('w') as log:
    subprocess.run([sys.executable,'-m','unittest','discover','-s','backend/tests','-p','test_conversation_scenario.py'],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
fixture=json.loads((out/'scenario.json').read_text())
result={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'synthetic_only':True,'model_reasoning_proven':False,'real_actions_executed':False,'scenario_tests':17,'fixture_clock':fixture['fixture_clock']}
transcript=[]
for event in fixture['frames']:
    packet=event['packet']
    if event['direction']=='input' and packet['type']=='utterance':transcript.append('User: '+packet['text'])
    if event['direction']=='output' and packet['type']=='agent.result':transcript.append('Friday (scripted/adapter): '+packet['text'])
(out/'transcript.txt').write_text('\n\n'.join(transcript)+'\n')
if a.native_binary:
    from PIL import Image,ImageDraw,ImageFont
    rows={}
    for label,binary in [('after',a.native_binary),('before',a.before_binary)]:
        if not binary:continue
        target=root/'outputs/native-ui'/label;target.mkdir(parents=True,exist_ok=True)
        for stale in (target/'conversation').glob('frame-*.ppm'):stale.unlink()
        with (out/(label+'-native.log')).open('w') as log:
            subprocess.run([str(binary.resolve()),str(target)],env={**env,'FRIDAY_CONVERSATION_REPLAY':str(out/'scenario.json')},stdout=log,stderr=subprocess.STDOUT,check=True)
        rows[label]=sorted((target/'conversation').glob('frame-*.ppm'))
    frames=[Image.open(path).convert('RGB') for path in rows['after']]
    assert all(frame.size==(720,720) for frame in frames)
    frames[0].save(out/'conversation-native.gif',save_all=True,append_images=frames[1:],duration=40,loop=0,optimize=False)
    embedded=[]
    for frame in frames:
        b=io.BytesIO();frame.save(b,format='PNG');embedded.append(base64.b64encode(b.getvalue()).decode())
    (out/'conversation-native.html').write_text('''<!doctype html><meta charset="utf-8"><title>Friday native conversation replay</title><style>body{background:#10141a;color:#e4e9e9;font:18px system-ui;max-width:760px;margin:24px auto}img{width:100%;max-width:720px}input{width:100%}</style><h1>Friday / native conversation replay</h1><p>Original synthetic Calendar, Weather and playback. Scripted provider and intercepted commands: plumbing proof, not model reasoning. Actual LVGL RGB565 frames at 720 × 720; host cadence is 40 ms.</p><img id="frame"><input id="slider" type="range" min="0" max="'''+str(len(frames)-1)+'''" value="0"><p id="position"></p><script>const frames='''+json.dumps(embedded)+''';let index=0;const slider=document.getElementById('slider');function show(){document.getElementById('frame').src='data:image/png;base64,'+frames[index];slider.value=index;document.getElementById('position').textContent='Frame '+index+' / '+(frames.length-1)}slider.oninput=()=>{index=+slider.value;show()};show();</script>''')
    selected=[];n=0
    for event in fixture['frames']:
        if event['direction']=='output':
            owned='media_request_id' in event['packet'] and 'surface' in event['packet']
            n+=20 if event['packet']['type']=='experience.finished' or owned else 1
            if event['packet']['type']=='experience.finished':selected.append(n-1)
    if fixture['frames'][-1]['packet'].get('media_session_id'):selected.append(n-1)
    labels=['Evening / synthetic Calendar','Weather / established ZIP','Music / clarification','Resume / intercepted adapter','Leave it / no further command','Music controls / fresh owned view']
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',24)
    columns=[label for label in ("before","after") if label in rows];board=Image.new('RGB',(720*len(columns),788*len(labels)),'#10141a');draw=ImageDraw.Draw(board)
    for column,label in enumerate(columns):
        for row,index in enumerate(selected):
            x=column*720;y=row*788;draw.text((x+20,y+12),label.title()+' / '+labels[row],font=font,fill='#e4e9e9');board.paste(Image.open(rows[label][index]),(x,y+56))
    board.save(out/'conversation-native-before-after.png')
    result.update(native_frames=len(frames),native_tick_ms=40,native_duration_ms=40*len(frames),baseline='695439a7bd4ab0387a9256d182220f892e6f0c45' if a.before_binary else None,selected_frames=selected,frame_sha256={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for paths in rows.values() for path in paths})
(out/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='frame_sha256'}))
