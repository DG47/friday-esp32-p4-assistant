#!/usr/bin/env python3
"""Prepare protected Telegram token/config locally. Never enables or contacts Telegram."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
import warnings

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--directory',type=Path,required=True)
parser.add_argument('--owner',required=True)
parser.add_argument('--private-user-id',type=int,required=True)
parser.add_argument('--verified-out-of-band',action='store_true',required=True)
args=parser.parse_args()
if not sys.stdin.isatty() or not sys.stderr.isatty():parser.error('Interactive terminal required; token cannot come from arguments or piped input')
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',args.owner) or not 0<args.private_user_id<2**52:parser.error('Invalid verified owner/private user ID')
repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo/'backend'))
from app.project_report import root_fd
path=args.directory
if not path.is_absolute() or path==repo or path.is_relative_to(repo):parser.error('Choose an absolute private secret directory outside this repository')
# No parent creation or permission changes. Operator must choose a private directory.
fd=root_fd(path)
try:
    info=os.fstat(fd)
    if info.st_uid!=os.getuid() or info.st_mode & 0o077:parser.error('Directory must already be owned by you with mode 0700')
    if any(name in os.listdir(fd) for name in ('bot-token','telegram.json')):parser.error('Refusing to replace existing enrollment; rotate through a separate reviewed workflow')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        token=getpass.getpass('BotFather token (hidden; stays in this local directory): ')
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token):parser.error('Invalid token format')
    if input('Confirm numeric private user ID was verified out of band; type VERIFIED: ')!='VERIFIED':raise SystemExit('No enrollment written')
    payloads={'bot-token':token+'\n','telegram.json':json.dumps({'version':1,'token_path':str(path/'bot-token'),'bindings':[{'user_id':args.private_user_id,'chat_id':args.private_user_id,'owner':args.owner,'verified_private_owner':True}]},indent=2)+'\n'}
    created=[]
    try:
        for name,text in payloads.items():
            output=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd);created.append(name)
            with os.fdopen(output,'w') as stream:stream.write(text);stream.flush();os.fsync(stream.fileno())
        os.fsync(fd)
    except BaseException:
        for name in created:os.unlink(name,dir_fd=fd)
        raise SystemExit('Enrollment failed; partial files removed') from None
finally:os.close(fd)
print('Protected enrollment prepared. Connector remains disabled. Review local configuration before separately enabling it.')
