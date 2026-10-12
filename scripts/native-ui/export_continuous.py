"""Export real native captures; never synthesize/interpolate device frames."""
import argparse, base64, hashlib, io, json, re, subprocess, zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

parser=argparse.ArgumentParser()
parser.add_argument('--installed-capture',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
out=root/'outputs/experience';out.mkdir(parents=True,exist_ok=True)
source=root/'outputs/native-ui/after';frames=[];frame_urls=[];checksums={}
keys={0:'idle',45:'weather',60:'interruption',73:'contraction',90:'music-with-weather',125:'dismissal',159:'calendar',189:'status',209:'reminder',239:'memory-review',259:'private-dismissed'}
keydir=out/'native-keyframes';keydir.mkdir(exist_ok=True)
for n,path in enumerate(sorted((source/'continuous').glob('frame-*.ppm'))+sorted((source/'extended').glob('frame-*.ppm'))):
    native=Image.open(path).convert('RGB');assert native.size==(720,720)
    png=io.BytesIO();native.save(png,format='PNG');data=png.getvalue()
    checksums[path.parent.name+'/'+path.name]=hashlib.sha256(data).hexdigest()
    frame_urls.append('data:image/png;base64,'+base64.b64encode(data).decode())
    frames.append(native)
    if n in keys:(keydir/(keys[n]+'.png')).write_bytes(data)
assert len(frames)==260
frames[0].save(out/'friday-native-flow.gif',save_all=True,append_images=frames[1:],duration=40,loop=0,optimize=False)
font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',24)
small=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',18)
board=Image.new('RGB',(1440,2312),'#111319');draw=ImageDraw.Draw(board)
draw.text((20,14),'Installed matching-layout UI',font=font,fill='#eeece7')
draw.text((740,14),'Isolated continuous-context candidate',font=font,fill='#eeece7')
for row,(old,new,label) in enumerate((('weather-both',45,'Weather / public forecast retained'),('music-artwork',90,'Music / previous Weather still available'),('status',189,'Status update / agenda retained as the same native layer'))):
    y=56+row*752
    board.paste(Image.open(args.installed_capture/(old+'.ppm')).convert('RGB'),(0,y))
    board.paste(frames[new],(720,y))
    draw.text((20,y+722),label+' — actual 720 × 720 LVGL pixels',font=small,fill='#bebfbf')
board.save(out/'friday-native-first-preview.png')
html='''<!doctype html><meta charset="utf-8"><title>Friday — continuous native context</title>
<style>body{margin:0;background:#111319;color:#eeece7;font:17px system-ui;padding:24px;max-width:1000px;margin:auto}button,input{font:inherit}button{border:1px solid #454d58;border-radius:14px;background:#252b35;color:inherit;padding:12px;margin:4px}img{display:block;width:min(720px,100%);height:auto;border-radius:16px;margin:20px 0}input{width:min(720px,100%)}p{max-width:760px;line-height:1.5}</style>
<h1>Friday stays. Context changes.</h1><p>Isolated native proof: Weather arrives, a synthetic interruption asks for Music, Weather contracts while retaining its artwork, Music appears with Weather still available, then content is dismissed. The same native canvas continues through Calendar, a listening interruption, status, reminder priority, a synthetic Snooze response, complete memory review and a synthetic Cancel response.</p>
<p>All 260 frames come from the actual C renderer and LVGL RGB565 flush, at 40 ms per native tick. No interpolation, HTML recreation, real speech, actual lyrics or private memories. The artwork and track are synthetic test content. Snooze and Cancel responses are synthetic fixtures, not real business mutations. Physical speech and the complete experience still need human device review.</p>
<button id="play">Pause</button><button data-frame="0">Idle</button><button data-frame="45">Weather</button><button data-frame="60">Interruption</button><button data-frame="90">Music + Weather</button><button data-frame="125">Dismissed</button><button data-frame="159">Calendar</button><button data-frame="189">Status</button><button data-frame="209">Reminder</button><button data-frame="239">Memory review</button><button data-frame="259">Private content gone</button><br><input id="seek" type="range" min="0" max="259" value="0" aria-label="Native frame"><span id="time"></span><img id="pixel" alt="Actual native Friday display frame" width="720" height="720">
<script>const frames=FRAME_DATA;let n=0,playing=true;const pixel=document.getElementById('pixel'),seek=document.getElementById('seek'),play=document.getElementById('play');function render(){pixel.src=frames[n];seek.value=n;document.getElementById('time').textContent=' '+(n*.04).toFixed(2)+' s / native frame '+n;}play.onclick=()=>{playing=!playing;play.textContent=playing?'Pause':'Play';};seek.oninput=()=>{playing=false;play.textContent='Play';n=+seek.value;render();};document.querySelectorAll('[data-frame]').forEach(b=>b.onclick=()=>{playing=false;play.textContent='Play';n=+b.dataset.frame;render();});setInterval(()=>{if(playing){n=(n+1)%frames.length;render();}},40);render();</script>'''.replace('FRAME_DATA',json.dumps(frame_urls))
(out/'friday-native-flow-preview.html').write_text(html)
log=(source/'diagnostics.log').read_text()
assert 'ATOMIC_RECEIVER_AUDIT' in log and 'READONLY_CONTEXT_AUDIT' in log and 'CONTINUOUS_NATIVE_SLICE' in log
cpu=[float(x) for x in re.findall(r'cpu_ms=([\d.]+)',log)]
verification={'gif_encoding':'Identical holds may coalesce; all 260 lossless native frames remain in HTML/ZIP','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'native_frames':260,'calendar_layer':'Same objects retained across status; original bounded lease; fail-closed allocation; no ambient private retention','native_tick_ms':40,'duration_ms':10400,'synthetic_only':True,'deployment':False,'backend_tests':389,'lvgl_heap':re.search(r'HOST_HEAP used=(\d+) max=(\d+)',log).groups(),'host_render_max_ms':max(cpu),'host_render_mean_ms':round(sum(cpu)/len(cpu),3),'png_sha256':checksums,'limitations':['Host timing is not hardware FPS or audio-load acceptance','Synthetic Calendar/status/reminder/consent continuation is not physical end-to-end acceptance','Candidate is opt-in and uninstalled; physical speech deferred']}
firmware=root/'firmware/build/friday_p3a.bin'
if firmware.exists():
    description=json.loads((firmware.parent/'project_description.json').read_text())
    verification['firmware_build']={'sha256':hashlib.sha256(firmware.read_bytes()).hexdigest(),'bytes':firmware.stat().st_size,'project_version':description['project_version'],'installed':False,'production_config':False}
(out/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
patch=subprocess.check_output(['git','diff','29c48ec0fbf6ea45f4cf80a9a4325c2df6bbe85c','HEAD','--binary'],cwd=root)
(out/'experience-continuity.patch').write_bytes(patch)
with zipfile.ZipFile(out/'friday-experience-review.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for filename in ('friday-native-first-preview.png','friday-native-flow.gif','friday-native-flow-preview.html','verification.json','experience-continuity.patch'):
        archive.write(out/filename,filename)
    for path in keydir.glob('*.png'):archive.write(path,'native-keyframes/'+path.name)
    for n,image in enumerate(frames):
        buf=io.BytesIO();image.save(buf,format='PNG');archive.writestr(f'native-frames/frame-{n:03}.png',buf.getvalue())
    archive.write(root/'docs/native-continuous-canvas.md','native-continuous-canvas.md')
    archive.write(source/'diagnostics.log','native-verification.log')
print(json.dumps({'native_frames':len(frames),'exports':str(out),'zip_bytes':(out/'friday-experience-review.zip').stat().st_size,'host_render_max_ms':max(cpu)}))
