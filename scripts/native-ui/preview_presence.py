"""Package actual production-rendered PPM frames; requires Pillow."""
from PIL import Image, ImageDraw
from pathlib import Path
r=Path(__file__).resolve().parents[2];src=r/'outputs/native-ui/after';out=r/'outputs/presence';out.mkdir(exist_ok=True)
frames=[Image.open(f).convert('RGB') for f in sorted(src.glob('presence-[0-9][0-9]-*.ppm'))];assert len(frames)==11
frames[0].save(out/'Friday-native-presence.gif',save_all=True,append_images=frames[1:],duration=100,loop=0)
strip=Image.new('RGB',(1440,398),'#080e18');d=ImageDraw.Draw(strip)
for i,(label,n) in enumerate([('Touch',1),('Listening',3),('Thinking',5),('Completion',7)]):
 strip.paste(frames[n].resize((360,360)),(i*360,38));d.text((i*360+18,12),label,fill='#b8c6d8')
strip.save(out/'Friday-native-presence-strip.png')
