"""Real isolated loopback HTTP semantics, synthetic callback only. No OAuth/exchange."""
import importlib.util
from pathlib import Path
import sys
import threading
import http.client
from urllib.parse import urlsplit,parse_qs,urlencode
from http.server import HTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.spotify_auth import AuthUnavailable,CALLBACK_PATH
spec=importlib.util.spec_from_file_location('helper',Path(__file__).with_name('connect_spotify.py'))
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

for mode in ('origin','absolute','extra','issuer','trailing','state','host','duplicate','wrong_origin','wrong_issuer'):
    record={};threads=[]
    def browser(url):
        query=parse_qs(urlsplit(url).query);redirect=urlsplit(query['redirect_uri'][0]);port=redirect.port
        state=query['state'][0];params={'code':'synthetic-code','state':'wrong' if mode=='state' else state}
        if mode=='extra':params['future_option']='original-fixture'
        if mode in {'issuer','wrong_issuer'}:params['iss']='https://accounts.spotify.com' if mode=='issuer' else 'https://example.invalid'
        target=CALLBACK_PATH+('/' if mode=='trailing' else '')+'?'+urlencode(params)
        if mode=='duplicate':target+='&code=duplicate'
        if mode in {'absolute','wrong_origin'}:target=('http://127.0.0.1:'+str(port) if mode=='absolute' else 'http://example.invalid')+target
        def request():
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
            connection.putrequest('GET',target,skip_host=True)
            connection.putheader('Host','localhost:'+str(port) if mode=='host' else '127.0.0.1:'+str(port))
            connection.endheaders();response=connection.getresponse();record['status']=response.status;response.read();connection.close()
        thread=threading.Thread(target=request);thread.start();threads.append(thread)
        return True
    try:
        code,_,_=helper.obtain_code('fixtureclient',port=0,browser_open=browser,lifetime=1)
        record['accepted']=code=='synthetic-code'
    except AuthUnavailable as error:record['accepted']=False;record['safe_reason']=error.reason
    finally:
        for thread in threads:thread.join(3)
    expected=mode in {'origin','absolute','extra','issuer'}
    assert record['accepted']==expected,(mode,record)
    assert record['status']==(200 if expected else 400),(mode,record)
    print(mode,record)
print('10 real-loopback scenarios passed; no credential exchange.')
