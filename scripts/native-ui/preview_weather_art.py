"""Verify and package lightweight native city/weather illustration mockups.

Run after build.py. Art crops must respond to CURRENT condition/day fields,
never future precipitation probability. All fixtures are synthetic.
"""
from pathlib import Path
from PIL import Image, ImageChops

root = Path(__file__).resolve().parents[2]
source = root / 'outputs/native-ui/after'
def image(name):return Image.open(source / (name + '.ppm')).convert('RGB')
def art(name, city):return image(name).crop((530, 242 + city*200, 680, 396 + city*200))
def same(a, b):return ImageChops.difference(a, b).getbbox() is None
for city in (0, 1):
    assert same(art('weather-both',city),art('weather-low',city)), 'future probability changed current artwork'
    assert same(art('weather-both',city),art('weather-forecast-unknown',city)), 'unknown future forecast changed current artwork'
    assert not same(art('weather-both',city),art('weather-night',city)), 'reported night/day treatment missing'
assert not same(art('weather-both',0),art('weather-both',1)), 'city silhouettes must be distinct'
assert not same(art('weather-both',0),art('weather-current-rain',0)), 'reported current rain treatment missing'
assert same(art('weather-both',1),art('weather-current-rain',1)), 'independent city weather changed'
for original,output in [('weather-both','Friday-city-weather-clear'),('weather-current-rain','Friday-city-weather-rain'),
                        ('weather-night','Friday-city-weather-night'),('weather-condition-unknown','Friday-city-weather-unknown')]:
    rendered=image(original);target=source/(output+'.png');rendered.save(target)
    assert same(rendered,Image.open(target).convert('RGB'))
print('CITY_WEATHER_PIXEL_AUDIT distinct-cities/current-rain/day-night/future-independent/unknown-independent/native-PNG-match PASS')
