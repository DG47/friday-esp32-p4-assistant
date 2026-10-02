"""Bounded Spotify album artwork, private memory only; no account authorization."""
import asyncio
from contextlib import asynccontextmanager
from http.cookiejar import DefaultCookiePolicy
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from io import BytesIO
import math
import re
import time
import warnings

SIZE=192
RAW_BYTES=SIZE*SIZE*2
MAX_SOURCE_BYTES=256*1024
MAX_SOURCE_PIXELS=1024*1024
MAX_CACHE_BYTES=320*1024
URL=re.compile(r'^https://i\.scdn\.co/image/[0-9a-f]{40}$')

@dataclass(frozen=True,repr=False)
class ArtworkRef:
    url:str
    width:int
    height:int
@dataclass(frozen=True,repr=False)
class Artwork:
    pixels:bytes
    digest:str
@dataclass(frozen=True,repr=False)
class ArtworkResult:
    status:str
    image:Artwork|None=None

def valid_ref(ref):
    return (isinstance(ref,ArtworkRef) and isinstance(ref.url,str) and URL.fullmatch(ref.url) is not None
        and type(ref.width) is int and type(ref.height) is int and 1<=ref.width<=1024 and 1<=ref.height<=1024
        and ref.width*ref.height<=MAX_SOURCE_PIXELS)

def select_artwork(album):
    candidates=[]
    images=album.get('images') if isinstance(album,dict) else None
    if not isinstance(images,list):return None
    for row in images[:20]:
        if not isinstance(row,dict):continue
        ref=ArtworkRef(row.get('url'),row.get('width'),row.get('height'))
        if valid_ref(ref):candidates.append(ref)
    # Prefer smallest source with enough real pixels. The bounded largest fallback
    # preserves the complete composition when only a smaller official image exists.
    sufficient=[ref for ref in candidates if min(ref.width,ref.height)>=SIZE]
    return min(sufficient,key=lambda r:r.width*r.height) if sufficient else max(candidates,key=lambda r:r.width*r.height,default=None)

def decode_artwork(body,ref):
    from PIL import Image,ImageOps
    if not valid_ref(ref) or not isinstance(body,bytes) or not 1<=len(body)<=MAX_SOURCE_BYTES:raise ValueError('Invalid artwork')
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(BytesIO(body),formats=['JPEG','PNG']) as image:
            if image.size!=(ref.width,ref.height) or image.width*image.height>MAX_SOURCE_PIXELS or getattr(image,'n_frames',1)!=1:
                raise ValueError('Invalid image geometry')
            image.verify()
        with Image.open(BytesIO(body),formats=['JPEG','PNG']) as image:
            image.load()
            image=ImageOps.exif_transpose(image)
            # Display adaptation only: full image, aspect-preserving fit, no crop,
            # decoration/filter/overlay or artist-image content transformation.
            fitted=ImageOps.contain(image.convert('RGB'),(SIZE,SIZE),Image.Resampling.LANCZOS)
            canvas=Image.new('RGB',(SIZE,SIZE),(25,20,20))
            canvas.paste(fitted,((SIZE-fitted.width)//2,(SIZE-fitted.height)//2))
            raw=bytearray(RAW_BYTES)
            for index,(red,green,blue) in enumerate(canvas.get_flattened_data()):
                pixel=((red>>3)<<11)|((green>>2)<<5)|(blue>>3)
                raw[index*2]=pixel&255;raw[index*2+1]=pixel>>8
    pixels=bytes(raw)
    return Artwork(pixels,hashlib.sha256(pixels).hexdigest())

class NoArtworkCookies(DefaultCookiePolicy):
    def set_ok(self,*args,**kwargs):return False
    def return_ok(self,*args,**kwargs):return False

class ArtworkProvider:
    def __init__(self,client=None,enabled=False,clock=time.monotonic):
        self.client=client;self.enabled=enabled;self.clock=clock
        if client is not None:
            client.cookies.clear();client.cookies.jar.set_policy(NoArtworkCookies())
        self.lock=asyncio.Lock();self.cache=OrderedDict();self.cache_bytes=0;self.retry_until=0
    @asynccontextmanager
    async def _stream(self,ref):
        request=self.client.build_request('GET',ref.url,headers={'Accept':'image/jpeg,image/png'},timeout=6)
        for name in ('Cookie','Authorization','Proxy-Authorization'):request.headers.pop(name,None)
        response=await self.client.send(request,auth=None,stream=True,follow_redirects=False)
        try:yield response
        finally:
            await response.aclose()
            self.client.cookies.clear()

    def clear(self):self.cache.clear();self.cache_bytes=0
    async def fetch(self,ref):
        if not self.enabled:return ArtworkResult('unavailable')
        if self.client is None or not valid_ref(ref):return ArtworkResult('unavailable')
        async with self.lock:
            if ref in self.cache:self.cache.move_to_end(ref);return ArtworkResult('ready',self.cache[ref])
            if self.clock()<self.retry_until:return ArtworkResult('unavailable')
            try:
                async with asyncio.timeout(8):
                    # Fixed allowlisted URL. Explicitly no bearer/cookies/auth or redirects.
                    async with self._stream(ref) as response:
                        if response.status_code!=200:
                            delay=30
                            if response.status_code==429:
                                try:delay=float(response.headers.get('Retry-After','60'))
                                except (ValueError,TypeError):delay=60
                                if not math.isfinite(delay):delay=60
                                delay=max(1,min(86400,delay))
                            self.retry_until=self.clock()+delay;return ArtworkResult('unavailable')
                        if response.headers.get('Content-Type','').split(';')[0].strip().lower() not in {'image/jpeg','image/png'}:
                            raise ValueError('Invalid content type')
                        length=response.headers.get('Content-Length')
                        if length is not None and (not length.isdecimal() or int(length)>MAX_SOURCE_BYTES):raise ValueError('Invalid length')
                        body=bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body)>MAX_SOURCE_BYTES:raise ValueError('Oversized artwork')
                # Decode synchronously within the shared lock: bounded 1MP input prevents
                # orphaned to_thread jobs after dismissal/rapid track changes.
                image=decode_artwork(bytes(body),ref)
                self.cache[ref]=image;self.cache_bytes+=len(image.pixels)
                while len(self.cache)>4 or self.cache_bytes>MAX_CACHE_BYTES:
                    _,old=self.cache.popitem(last=False);self.cache_bytes-=len(old.pixels)
                return ArtworkResult('ready',image)
            except asyncio.CancelledError:raise
            except Exception:
                self.retry_until=self.clock()+30;return ArtworkResult('unavailable')
