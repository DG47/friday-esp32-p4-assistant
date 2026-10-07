"""Package production native weather frames and verify restrained state feedback.

Synthetic provider fixtures, deterministic LVGL time; no network or device I/O.
Run after build.py with the backend Python environment (Pillow required).
"""
from pathlib import Path
from PIL import Image, ImageChops

root = Path(__file__).resolve().parents[2]
source = root / 'outputs/native-ui/after'
frames = [Image.open(path).convert('RGB') for path in sorted(source.glob('weather-motion-*.ppm'))]
assert len(frames) == 11
assert ImageChops.difference(frames[2], frames[3]).getbbox() is not None, 'working motion missing'
assert ImageChops.difference(frames[6], frames[7]).getbbox() is not None, 'completion cue missing'
assert ImageChops.difference(frames[9], frames[10]).getbbox() is None, 'idle result must settle'
cancelled = [Image.open(source / f'weather-cancel-{n:02d}-menu.ppm').convert('RGB') for n in (3, 4)]
assert ImageChops.difference(*cancelled).getbbox() is None, 'new navigation must cancel weather motion'
# No loop extension: a short opening/working/completion sequence, then a still result.
frames[0].save(source / 'Friday-weather-presence.gif', save_all=True,
               append_images=frames[1:], duration=[500] + [100] * 9 + [3000], disposal=2)
for original, output in [('weather-both', 'Friday-weather-forecast-final'),
                         ('weather-forecast-unknown', 'Friday-weather-unknown-final'),
                         ('weather-partial', 'Friday-weather-partial-final')]:
    image = Image.open(source / (original + '.ppm')).convert('RGB')
    target = source / (output + '.png')
    image.save(target)
    assert ImageChops.difference(image, Image.open(target).convert('RGB')).getbbox() is None
print('WEATHER_MOTION_PIXEL_AUDIT working/completion/idle-settle/navigation-cancel/native-PNG-match PASS')
