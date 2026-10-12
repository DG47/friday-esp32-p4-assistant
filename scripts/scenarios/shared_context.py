"""Offline shared-reference scenario and actual native LVGL evidence exporter."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

root=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--native-binary',type=Path,required=True)
p.add_argument('--before-binary',type=Path)
p.add_argument('--baseline-renderer',default='7f9a902d6aad175039d0d1afba1776d1aa731e6e')
a=p.parse_args();out=root/'outputs/shared-context';out.mkdir(parents=True,exist_ok=True)
env={**os.environ,'PYTHONPATH':str(root/'backend'),'PYTHONDONTWRITEBYTECODE':'1','LLM_PROVIDER':'mock','TTS_PROVIDER':'none','STT_PROVIDER':'none','WEATHER_PROVIDER':'none','FRIDAY_SHARED_REFERENCE_FIXTURE':str(out/'scenario.json')}
with (out/'focused-tests.log').open('w') as log:
    subprocess.run([sys.executable,'-m','unittest','discover','-s','backend/tests','-p','test_working_context.py'],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
fixture=json.loads((out/'scenario.json').read_text());rows={}
for name in ['browse-fixtures.json','artwork-fixture.json']:
    shutil.copyfile(root/'outputs/native-ui'/name,out/name)
for label,binary in [('after',a.native_binary),('before',a.before_binary)]:
    if not binary:continue
    target=out/label;target.mkdir(exist_ok=True)
    for stale in (target/'conversation').glob('frame-*.ppm'):stale.unlink()
    with (out/(label+'-native.log')).open('w') as log:
        subprocess.run([str(binary.resolve()),str(target)],env={**env,'FRIDAY_CONVERSATION_REPLAY':str(out/'scenario.json')},stdout=log,stderr=subprocess.STDOUT,check=True)
    rows[label]=sorted((target/'conversation').glob('frame-*.ppm'))
from PIL import Image,ImageDraw,ImageFont
frames=[Image.open(path).convert('RGB') for path in rows['after']]
assert all(frame.size==(720,720) for frame in frames)
frames[0].save(out/'shared-context-native.gif',save_all=True,append_images=frames[1:],duration=40,loop=0,optimize=False)
embedded=[];hashes={}
for label,paths in rows.items():
    png_dir=out/(label+'-png');png_dir.mkdir(exist_ok=True)
    for path in paths:
        frame=Image.open(path).convert('RGB');buffer=io.BytesIO();frame.save(buffer,format='PNG');raw=buffer.getvalue()
        (png_dir/(path.stem+'.png')).write_bytes(raw);hashes[label+'/'+path.stem]=hashlib.sha256(raw).hexdigest()
        if label=='after':embedded.append(base64.b64encode(raw).decode())
(out/'shared-context-native.html').write_text('''<!doctype html><meta charset="utf-8"><title>Friday shared context / native replay</title><style>body{background:#10141a;color:#e4e9e9;font:18px system-ui;max-width:760px;margin:24px auto}img{width:100%;max-width:720px}input{width:100%}</style><h1>Friday / shared event and Weather</h1><p>Actual 720 × 720 LVGL RGB565 frames from authenticated backend packets. Original synthetic content, scripted provider, no inference or real integrations. Touch focus keeps the same references; corrections replace Weather independently; dismissal revokes old actions. This proves implementation and delivery, not model understanding.</p><img id="frame"><input id="slider" type="range" min="0" max="'''+str(len(frames)-1)+'''" value="0"><p id="position"></p><script>const frames='''+json.dumps(embedded)+''';let index=0;const slider=document.getElementById('slider');function show(){document.getElementById('frame').src='data:image/png;base64,'+frames[index];slider.value=index;document.getElementById('position').textContent='Frame '+index+' / '+(frames.length-1)}slider.oninput=()=>{index=+slider.value;show()};show();</script>''')
selected={};n=0;requests={}
for event in fixture['frames']:
    packet=event['packet']
    if event['direction']=='input':
        requests[packet['request_id']]=packet.get('text') or event.get('native_touch_label') or 'Rejected old reference'
    else:
        n+=20 if packet['type']=='experience.finished' or 'media_request_id' in packet and 'surface' in packet else 1
        if packet['type']=='experience.finished':selected[packet.get('experience_id')]=n-1
assert n==len(frames)
labels=[('shared-reference-1','Calendar / stable event'),('shared-reference-2','Event and its Weather'),('shared-reference-3','Touch Event / details + retained Weather'),('shared-reference-4','Touch Weather / same objects'),('shared-reference-7','Correction / new ZIP, same event'),('shared-reference-8','Generic follow-up / corrected ZIP'),('shared-reference-11','Unrelated Calendar / retained Weather'),('shared-reference-12','Return to corrected Weather'),('shared-reference-13','Status / retained Weather'),('shared-reference-15','Dismissed / references revoked'),('shared-reference-18','Old speech referent / clarification')]
font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',23)
columns=[label for label in ("before","after") if label in rows];board=Image.new('RGB',(720*len(columns),788*len(labels)),'#10141a');draw=ImageDraw.Draw(board)
for column,label in enumerate(columns):
    for row,(request,title) in enumerate(labels):
        index=selected[request];x=column*720;y=row*788
        draw.text((x+18,y+12),label.title()+' / '+title,font=font,fill='#e4e9e9')
        board.paste(Image.open(rows[label][index]),(x,y+56))
board.save(out/'shared-context-before-after.png')
audit_names=['shared-context-long-text','shared-context-event-expired','shared-context-weather-expired','shared-context-dismissed','shared-context-replay-expired','shared-context-disconnected']
audit=Image.new('RGB',(1440,788*3),'#10141a');draw=ImageDraw.Draw(audit)
for index,name in enumerate(audit_names):
    x=(index%2)*720;y=(index//2)*788;draw.text((x+18,y+12),name.removeprefix('shared-context-').replace('-',' ').title(),font=font,fill='#e4e9e9');audit.paste(Image.open(out/'after'/(name+'.ppm')),(x,y+56))
audit.save(out/'shared-context-lease-audit.png')
cross_names=['shared-context-across-calendar','shared-context-return-calendar','shared-context-across-status','shared-context-return-status','shared-context-across-music','shared-context-return-music','shared-context-public-protected','shared-context-public-expired','shared-context-public-dismissed']
cross=Image.new('RGB',(2160,788*3),'#10141a');draw=ImageDraw.Draw(cross)
for index,name in enumerate(cross_names):
    x=(index%3)*720;y=(index//3)*788;draw.text((x+18,y+12),name.removeprefix('shared-context-').replace('-',' ').title(),font=font,fill='#e4e9e9');cross.paste(Image.open(out/'after'/(name+'.ppm')),(x,y+56))
cross.save(out/'shared-context-cross-scene.png')

transcript=[]
for event in fixture['frames']:
    packet=event['packet']
    if event['direction']=='input':transcript.append('User '+('speech: '+packet['text'] if packet['type']=='utterance' else 'touch: '+event.get('native_touch_label','old reference')))
    elif packet['type']=='agent.result':transcript.append('Friday (scripted/local): '+packet['text'])
(out/'transcript.txt').write_text('\n\n'.join(transcript)+'\n')
log=(out/'after-native.log').read_text();render=[float(line.rsplit('cpu_ms=',1)[1]) for line in log.splitlines() if line.startswith('HOST_RENDER')]
result={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'baseline_renderer':a.baseline_renderer,'baseline_note':'Same new backend packets rendered by the previous native renderer; not a previous-backend replay.','synthetic_only':True,'actual_model_reasoning':False,'actual_actions':False,'focused_tests':19,'native_frames':len(frames),'native_tick_ms':40,'native_duration_ms':40*len(frames),'host_render_cpu_ms':{'max':max(render),'mean':sum(render)/len(render)},'selected_frames':selected,'weather_reads':fixture['weather_reads'],'frame_sha256':hashes}
(out/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({key:value for key,value in result.items() if key!='frame_sha256'}))
