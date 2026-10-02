import argparse,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--lvgl',type=Path,required=True);p.add_argument('--cjson',type=Path,required=True);p.add_argument('--before',action='store_true');a=p.parse_args()
r=Path(__file__).resolve().parents[2];h=r/'scripts/native-ui';out=r/'outputs/native-ui'/('before' if a.before else 'after');out.mkdir(parents=True,exist_ok=True)
if a.before:
 (out/'before_ui.c').write_bytes(subprocess.check_output(['git','show','647b3da:firmware/main/friday_ui.c'],cwd=r))
subprocess.run(['python3',str(h/'generate_fixture.py')],check=True)
sources=list((a.lvgl/'src').rglob('*.c'))
cmd=['cc','-O2','-std=c11','-DLV_CONF_PATH="'+str(h/'lv_conf.h')+'"','-I'+str(a.lvgl),'-I'+str(h/'stubs'),'-I'+str(r/'firmware/main'),'-I'+str(a.cjson),str(h/'capture.c'),str(out/'before_ui.c' if a.before else r/'firmware/main/friday_ui.c'),str(r/'firmware/main/friday_media_overlay.c'),str(r/'firmware/main/friday_artwork.c'),str(r/'firmware/main/friday_presence.c'),str(r/'firmware/main/friday_state.c'),str(r/'firmware/main/friday_display_text.c'),str(r/'firmware/main/friday_media_guard.c'),str(a.cjson/'cJSON.c'),*[str(s) for s in sources],'-o',str(out/'capture')]
if a.before: cmd.insert(1,'-DFRIDAY_BASELINE')
subprocess.run(cmd,check=True,timeout=180);subprocess.run([str(out/'capture'),str(out)],check=True,stdout=(out/'diagnostics.log').open('w'),timeout=30)
