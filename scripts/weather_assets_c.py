"""Emit compile-time RGB565 descriptors from bounded bundled city assets.

No network, dynamic decoding, canvas, transport changes or runtime allocations.
Both native and firmware builds consume the exact same checked-in bytes.
"""
from pathlib import Path
import sys
import json
import hashlib

root = Path(__file__).resolve().parents[1]
assets = root / 'firmware/main/weather_assets'
width, height = 680, 190
states = ('day', 'neutral', 'night', 'rain', 'snow')
output = Path(sys.argv[1])
manifest = json.loads((assets / 'manifest.json').read_text())
assert (manifest['width'], manifest['height']) == (width, height)
expected = {entry['name']: entry for entry in manifest['assets']}
assert len(expected) == 10
with output.open('w') as out:
    out.write('#include "lvgl.h"\n#include "friday_weather_art.h"\n')
    for city in ('jersey', 'manhattan'):
        for state in states:
            name = f'{city}_{state}'
            data = (assets / (name + '.rgb565')).read_bytes()
            assert len(data) == width * height * 2, name
            entry = expected[name + '.rgb565']
            assert entry['bytes'] == len(data), name
            assert entry['sha256'] == hashlib.sha256(data).hexdigest(), name
            out.write(f'static const unsigned char {name}_pixels[] = {{\n')
            for offset in range(0, len(data), 32):
                out.write(','.join(str(b) for b in data[offset:offset + 32]) + ',\n')
            out.write('};\n')
            out.write(f'static const lv_image_dsc_t {name} = {{.header={{.magic=LV_IMAGE_HEADER_MAGIC,.cf=LV_COLOR_FORMAT_RGB565,.w={width},.h={height},.stride={width*2}}},.data_size=sizeof({name}_pixels),.data={name}_pixels}};\n')
    out.write('const lv_image_dsc_t *friday_weather_art(int city, int code, int day) {\n')
    out.write('if(city<1 || city>2) return NULL;\n')
    out.write('int state=1; /* neutral: missing/invalid condition or day/night */\n')
    out.write('if(friday_weather_code_known(code) && (day==0 || day==1)) {\n')
    out.write('if(day==0) state=2; /* neutral night; condition remains text */\n')
    out.write('else if((code>=51 && code<=67)||(code>=80 && code<=82)||code>=95) state=3;\n')
    out.write('else if((code>=71 && code<=77)||(code>=85 && code<=86)) state=4;\n')
    out.write('else if(code<=2) state=0;\n}\n')
    out.write('static const lv_image_dsc_t *const images[2][5] = {\n')
    for city in ('jersey', 'manhattan'):
        out.write('{' + ','.join('&' + city + '_' + s for s in states) + '},\n')
    out.write('}; return images[city-1][state];}\n')
