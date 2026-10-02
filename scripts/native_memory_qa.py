#!/usr/bin/env python3
"""Compile a read-only layout probe against installed pinned LVGL; install nothing."""
import hashlib
import random
import string
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build_probe(lvgl_root, output):
    sources=['src/font/lv_font.c','src/font/lv_font_fmt_txt.c','src/font/lv_font_montserrat_24.c','src/misc/lv_text.c','src/misc/lv_utils.c','src/stdlib/builtin/lv_string_builtin.c']
    command=['cc','-O2','-ffunction-sections','-fdata-sections','-Wl,-dead_strip','-DLV_CONF_PATH="'+str(ROOT/'scripts/native-memory/lv_conf.h')+'"','-I'+str(lvgl_root),str(ROOT/'scripts/native-memory/probe.c'),*[str(lvgl_root/source) for source in sources],'-o',str(output)]
    subprocess.run(command,check=True,capture_output=True,text=True)


def verify(probe, lvgl_root):
    sys.path.insert(0,str(ROOT/'backend'))
    from app.memory_font_metrics import FONT_SOURCE_SHA256,GLYPH_WIDTHS
    from app.memory_text import line_ends,text_pages,display_page,WIDTH,HEIGHT,LAYOUT_VERSION
    font_file=lvgl_root/'src/font/lv_font_montserrat_24.c'
    if hashlib.sha256(font_file.read_bytes()).hexdigest()!=FONT_SOURCE_SHA256:
        raise ValueError('Pinned font source differs; regenerate/review measurements')
    native_metrics=json.loads(subprocess.check_output([str(probe),'--metrics'],text=True))
    if bytes(value for row in native_metrics for value in row)!=GLYPH_WIDTHS:
        raise ValueError('Pinned glyph widths do not match the actual LVGL getter')
    common=('I like green tea without sugar and prefer a quiet workspace when I read. Please keep replies concise!')[:100]
    named={'common100':common,'common500':((common+' ')*5)[:500],'widest_unbroken500':'W'*500,'adversarial_wordwrap500':(('W'*11+' ')*60)[:500],'long_word_fragment500':(('W'*22+' ')*60)[:500],'narrow500':'i'*500}
    texts=list(named.values())
    texts += [character*length for character in [chr(i) for i in range(32,127)] for length in [1,20,21,40,41,100,499,500]]
    texts += [((('W'*length+' ')*501)[:500]) for length in range(1,70)]
    texts += [('W'*21+' '*spaces+'W'*21) for spaces in range(1,40)]
    texts += [('.,;:-_)}[]{}<>!@#$%^&*()  '*25)[:500],('a'+' '*498+'z'),' '*250+'W'*250]
    rng=random.Random(71)
    texts += [''.join(rng.choices(' '+string.ascii_letters+string.digits+string.punctuation,k=rng.randint(1,500))) for _ in range(3000)]
    def native(values,hex_input=False):
        encoded=[value.encode('ascii').hex() for value in values] if hex_input else values
        raw=subprocess.check_output([str(probe)]+(['--hex'] if hex_input else []),input='\n'.join(encoded)+'\n',text=True)
        return [json.loads(line) for line in raw.splitlines()]
    measurements=native(texts)
    for text,measurement in zip(texts,measurements):
        if line_ends(text)!=[line['end'] for line in measurement['lines']]:
            raise ValueError('Native line-break mismatch: '+repr(text))
    pages=[page for text in texts for page in text_pages(text)]
    displays=[display_page(page) for page in pages]
    if any(display.replace('\n','')!=page for display,page in zip(displays,pages)):raise ValueError('Display wrapping changed source text')
    measured_pages=native(displays,hex_input=True)
    for page,measurement in zip(pages,measured_pages):
        if measurement['width']>WIDTH or measurement['height']>HEIGHT or len(measurement['lines'])>2:
            raise ValueError('Native page exceeds available geometry: '+repr(page)+' '+str(measurement))
    for text in texts:
        if ''.join(text_pages(text))!=text:raise ValueError('Pagination lost characters')
    return {'layout_version':LAYOUT_VERSION,'font_sha256':FONT_SOURCE_SHA256,'glyph_measurements_matched':len(GLYPH_WIDTHS),'native_line_break_cases':len(texts),'native_pages_measured':len(pages),'display_only_line_breaks_preserve_source':True,'max_page_width':max(value['width'] for value in measured_pages),'max_page_height':max(value['height'] for value in measured_pages),'available_width':WIDTH,'available_height':HEIGHT,'named_cases':{name:{'characters':len(text),'pages':len(text_pages(text)),'page_lengths':list(map(len,text_pages(text)))} for name,text in named.items()},'max_500_character_page_count_in_test_corpus':max(len(text_pages(text)) for text in texts if len(text)==500),'all_passed':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lvgl-root',type=Path,required=True)
    parser.add_argument('--metrics',type=Path)
    parser.add_argument('--probe-output',type=Path)
    parser.add_argument('--verify',type=Path,help='Write native parity/geometry verification JSON')
    args=parser.parse_args()
    output=args.probe_output or Path(tempfile.gettempdir())/'friday-memory-native-probe'
    try:build_probe(args.lvgl_root,output)
    except subprocess.CalledProcessError as error:raise SystemExit(error.stderr)
    if args.metrics:
        data=subprocess.check_output([str(output),'--metrics'],text=True)
        args.metrics.write_text(data)
    if args.verify:
        report=verify(output,args.lvgl_root)
        args.verify.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
    else:print('Compiled native LVGL text-layout probe: '+str(output))
