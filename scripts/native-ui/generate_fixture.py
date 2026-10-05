"""Original synthetic cover pixels, never downloaded or borrowed artwork."""
from pathlib import Path
import math,base64,hashlib,json

def generate(destination):
    pixels=bytearray();rgb=bytearray();n=192
    for y in range(n):
        for x in range(n):
            u=x/n;v=y/n
            c=[int(28+38*v),int(36+40*v),int(68+65*v)]
            # A soft sun behind an original paper-like mountain composition.
            sun=((u-.66)**2+(v-.34)**2)**.5
            if sun<.195:
                f=min(1,max(0,(.195-sun)*n))
                warm=[239-int(16*v),193-int(30*v),137-int(15*v)]
                c=[int(a*(1-f)+b*f) for a,b in zip(c,warm)]
            ridge=.60+.075*math.sin(u*5+1)+.03*math.sin(u*14)
            if v>ridge:c=[int(95+70*v),int(111+65*v),int(154+34*v)]
            ridge2=.76+.08*math.sin(u*4-1)
            if v>ridge2:c=[int(175+45*v),int(146+43*v),int(134+32*v)]
            if v>.90+.028*math.sin(u*9):c=[34,42,65]
            rgb.extend(c);value=(c[0]>>3)<<11|(c[1]>>2)<<5|(c[2]>>3);pixels.extend(value.to_bytes(2,'little'))
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'synthetic-cover.ppm').write_bytes(b'P6\n192 192\n255\n'+rgb)
    (destination/'synthetic-cover.rgb565').write_bytes(pixels)
    frame=dict(type='surface',media_request_id='art-fixture',media_session_id='0123456789abcdef01234567',media_sequence=1,
      surface=dict(kind='media',title='Quiet Satellites',subtitle='We make a little room for the morning',track_key='synthetic:quiet-satellites',artwork_generation=1,
      artwork=dict(track_key='synthetic:quiet-satellites',width=192,height=192,format='rgb565_le',status='ready',sha256=hashlib.sha256(pixels).hexdigest(),data_base64=base64.b64encode(pixels).decode()),
      items=[dict(time='Artist',label='The Paper Observatory'),dict(time='Progress',label='1:24 / 3:42'),dict(time='Source',label='Original synthetic cover and timing fixture'),dict(time='State',label='Playing')],
      actions=[dict(id='musicctl:'+'1'*24,label='Previous'),dict(id='musicctl:'+'2'*24,label='Pause'),dict(id='musicctl:'+'3'*24,label='Next'),dict(id='dismiss',label='Dismiss')]))
    (destination/'artwork-fixture.json').write_text(json.dumps(frame,separators=(',',':')))
    assert len(pixels)==73728 and len(frame['surface']['artwork']['data_base64'])==98304
    assert (destination/'artwork-fixture.json').stat().st_size<131072

if __name__=='__main__':generate(Path(__file__).resolve().parents[2]/'outputs/native-ui')
