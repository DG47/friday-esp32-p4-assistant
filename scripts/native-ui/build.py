import argparse,subprocess,sys,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--lvgl',type=Path,required=True);p.add_argument('--cjson',type=Path,required=True);p.add_argument('--before',action='store_true');p.add_argument('--baseline',default='b32b57ce0027bf20e7e73a86fa7dc4e2ff0784df');a=p.parse_args()
r=Path(__file__).resolve().parents[2];h=r/'scripts/native-ui';out=r/'outputs/native-ui'/('before' if a.before else 'after');out.mkdir(parents=True,exist_ok=True)
if a.before:
 (out/'before_ui.c').write_bytes(subprocess.check_output(['git','show',a.baseline+':firmware/main/friday_ui.c'],cwd=r))
subprocess.run(['python3',str(h/'generate_fixture.py')],check=True)
subprocess.run([sys.executable,str(h/'generate_browse_fixture.py')],check=True)
subprocess.run([sys.executable,str(h/'generate_weather_fixture.py')],check=True)
if not a.before:
 env=os.environ.copy();env['PYTHONPATH']=str(r/'backend')+os.pathsep+str(r/'backend/tests');env['FRIDAY_RECOVERY_FIXTURE']=str(out.parent/'music-recovery-fixture.json')
 subprocess.run([sys.executable,'-m','unittest','test_device_socket.DeviceSocketTests.test_music_restores_current_confirmation_without_consent'],cwd=r,env=env,check=True)
 env['FRIDAY_WEATHER_TOUCH_FIXTURE']=str(out.parent/'weather-touch-fixture.json')
 subprocess.run([sys.executable,'-m','unittest','test_weather_touch.WeatherTouchTests.test_protected_confirmation_busy_memory_reminder_and_recovery'],cwd=r,env=env,check=True)
if not a.before:
 backend=(r/'firmware/main/friday_backend.c').read_text();start=backend.index('bool friday_backend_scene_action(');end=backend.index('\nstatic const char *json_string',start)
 sender=out/'production_sender.c'
 sender.write_text('#include <stdio.h>\n#include <stdint.h>\n#include <string.h>\n#include "friday_ui.h"\nstatic bool backend_ready=true,scene_action_pending;\nextern int native_sender_send_text(const char*,int);\nstatic int send_text(const char*p,int t){return native_sender_send_text(p,t);}\nstatic bool resume_media(void){return false;}\nvoid native_sender_set_ready(bool ready){backend_ready=ready;}\nstatic uint32_t esp_random(void){static uint32_t n;return ++n;}\n#define pdMS_TO_TICKS(x) (x)\n'+backend[start:end])
sources=list((a.lvgl/'src').rglob('*.c'))
cmd=['cc','-O2','-std=c11','-DLV_CONF_PATH="'+str(h/'lv_conf.h')+'"','-I'+str(a.lvgl),'-I'+str(h/'stubs'),'-I'+str(r/'firmware/main'),'-I'+str(a.cjson),str(h/'capture.c'),str(out/'before_ui.c' if a.before else r/'firmware/main/friday_ui.c'),str(r/'firmware/main/friday_media_overlay.c'),str(r/'firmware/main/friday_artwork.c'),str(r/'firmware/main/friday_presence.c'),str(r/'firmware/main/friday_state.c'),str(r/'firmware/main/friday_display_text.c'),str(r/'firmware/main/friday_media_guard.c'),str(a.cjson/'cJSON.c'),*[str(s) for s in sources],'-o',str(out/'capture')]
# Current baseline supports the same ownership and capture regressions.
if a.before: cmd.insert(1,'-DFRIDAY_COMPARE_BASE')
else: cmd.insert(1,'-DFRIDAY_NATIVE_SENDER');cmd.insert(-2,str(sender))
subprocess.run(cmd,check=True,timeout=180);subprocess.run([str(out/'capture'),str(out)],check=True,stdout=(out/'diagnostics.log').open('w'),timeout=30)
