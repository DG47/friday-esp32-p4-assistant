import argparse, subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--lvgl',type=Path,required=True);a=p.parse_args()
h=Path(__file__).resolve().parent;r=h.parents[1];out=r/'outputs/native-ui';out.mkdir(parents=True,exist_ok=True)
cmd=['cc','-O2','-std=c11','-DLV_CONF_PATH="'+str(h/'lv_conf.h')+'"','-I'+str(a.lvgl),'-I'+str(r/'firmware/main'),str(h/'test_presence.c'),*[str(s) for s in (a.lvgl/'src').rglob('*.c')],'-o',str(out/'test_presence')]
subprocess.run(cmd,check=True);subprocess.run([str(out/'test_presence')],check=True)
