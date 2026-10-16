#!/usr/bin/env python3
"""Run real pinned LVGL glyph and scoped execution Stop tests on macOS/Linux; no firmware or app install."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SOURCES=['src/font/lv_font.c','src/font/lv_font_fmt_txt.c',
         'src/font/lv_font_montserrat_18.c','src/font/lv_font_montserrat_24.c',
         'src/font/lv_font_montserrat_32.c','src/misc/lv_text.c','src/misc/lv_utils.c',
         'src/stdlib/builtin/lv_string_builtin.c']

def execution_stop_test(directory, cjson_root):
    output=Path(directory)/'execution-stop-test'
    command=['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
             '-I'+str(ROOT/'firmware/main'),str(ROOT/'firmware/tests/test_execution_stop.c'),
             str(ROOT/'firmware/main/friday_execution_stop.c')]
    if cjson_root.is_dir():
        # Compile existing third-party JSON separately so platform deprecation
        # warnings do not weaken strict warnings on Friday's own sources.
        obj=Path(directory)/'cjson.o'
        subprocess.run(['cc','-std=c11','-fsanitize=address,undefined',
                        *(['-Wno-deprecated-declarations'] if sys.platform=='darwin' else []),
                        '-I'+str(cjson_root),'-c',str(cjson_root/'cJSON.c'),'-o',str(obj)],check=True)
        command += ['-I'+str(cjson_root),str(obj)]
    else:
        # The existing Linux native-test job already installs libcjson-dev.
        command += ['-I/usr/include/cjson','-lcjson']
    subprocess.run([*command,'-o',str(output)],check=True)
    subprocess.run([str(output)],check=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lvgl-root',required=True,type=Path)
    parser.add_argument('--cjson-root',type=Path,default=ROOT/'firmware/managed_components/espressif__cjson/cJSON')
    args=parser.parse_args();lvgl=args.lvgl_root.resolve()
    if not (lvgl/'lvgl.h').is_file() or any(not (lvgl/s).is_file() for s in SOURCES):
        raise SystemExit('Pinned LVGL source/header missing; use LVGL v9.3.0.')
    with tempfile.TemporaryDirectory(prefix='friday-glyph-host-') as directory:
        output=Path(directory)/'glyph-test'
        command=['cc','-std=c11','-O2','-Wall','-Wextra','-ffunction-sections','-fdata-sections',
            '-Wl,-dead_strip' if sys.platform=='darwin' else '-Wl,--gc-sections',
            '-DLV_CONF_PATH="'+str(ROOT/'scripts/native-memory/lv_conf.h')+'"',
            '-DLV_FONT_MONTSERRAT_18=1','-DLV_FONT_MONTSERRAT_32=1',
            '-I'+str(lvgl),'-I'+str(ROOT/'firmware/main'),
            str(ROOT/'firmware/tests/test_display_text.c'),str(ROOT/'firmware/main/friday_display_text.c'),
            *[str(lvgl/s) for s in SOURCES],'-o',str(output)]
        subprocess.run(command,check=True)
        subprocess.run([str(output)],check=True)
        execution_stop_test(directory,args.cjson_root)
if __name__=='__main__':main()
