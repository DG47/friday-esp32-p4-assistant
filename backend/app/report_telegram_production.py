"""Explicit protected-config Telegram report connector. No automatic enrollment."""
import json
import os
from pathlib import Path
import re
import stat
import hashlib
import fcntl
from .project_report import root_fd
from .report_telegram import ReportTelegramAdapter, ReportTelegramTransport
from .task_telegram_transport import TelegramHTTPS, offloop


def private_read(path,limit=65536):
    path=Path(path)
    parent=root_fd(path.parent)
    try:
        p=os.fstat(parent)
        if p.st_uid!=os.getuid() or p.st_mode & 0o077:raise ValueError('Owner-private Telegram config directory required')
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode & 0o077 or info.st_size>limit:raise ValueError('Private bounded regular Telegram config required')
            return source.read(limit+1)
    finally:os.close(parent)


def load_config(path,owners):
    try:
        config=json.loads(private_read(path))
        if not isinstance(config,dict) or set(config)!={'version','token_path','bindings'} or config['version']!=1:raise ValueError()
        bindings=config['bindings']
        if not isinstance(bindings,list) or not 1<=len(bindings)<=16:raise ValueError()
        allow={}
        for row in bindings:
            if not isinstance(row,dict) or set(row)!={'user_id','chat_id','owner','verified_private_owner'}:raise ValueError()
            uid=row['user_id']
            if type(uid) is not int or not 0<uid<2**52 or type(row['chat_id']) is not int or row['chat_id']!=uid or row['owner'] not in owners or row['verified_private_owner'] is not True or uid in allow:raise ValueError()
            allow[uid]=row['owner']
        token=private_read(config['token_path'],512).decode('ascii').strip()
        if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token):raise ValueError()
        return token,allow
    except (ValueError,TypeError,KeyError,UnicodeError,OSError):raise ValueError('Invalid protected Telegram enrollment') from None


class ConfiguredReportAdapter(ReportTelegramAdapter):
    def __init__(self,store,allow):
        self.allow=dict(allow);super().__init__(store)
    def _identity(self,update):
        result=super()._identity(update)
        uid,cid,owner,_,message,callback=result
        if self.allow.get(uid)!=owner:raise ValueError('Configured private owner required')
        return result
    def authorize_delivery(self,user_id,chat_id,owner,generation):
        if self.allow.get(user_id)!=owner or user_id!=chat_id:raise ValueError('Configured private owner required')
        return super().authorize_delivery(user_id,chat_id,owner,generation)
    def handle(self,update):
        _,_,_,_,message,callback=self._identity(update)
        if not callback:
            # Files, forwards and embedded content are never report capabilities.
            if any(k in message for k in ('document','photo','audio','voice','video','caption','forward_origin','contact','location','web_app_data')):raise ValueError('Unsupported content')
            text=message.get('text','')
            if not isinstance(text,str):raise ValueError('Text commands required')
            if (re.search(r'\b(ignore|override|privileges?|enroll|allowlist|credentials?|passwords?|tokens?|upload|download|export|shell|execute|install|delete|edit|send|email|sudo)\b',text,re.I)
                    or re.search(r'\b(change|set|grant)\b.*\b(owner|permissions?|root|access)\b',text,re.I)
                    or re.search(r'\b(show|print|reveal|paste|transmit)\b.*\b(files?|source|contents?|secrets?)\b',text,re.I)):
                raise ValueError('Unsupported privilege or content action')
        return super().handle(update)
    def error_response(self,update,*,callback=False):
        result=super().error_response(update,callback=callback)
        if not callback:result['text']='Unsupported or unavailable command. Only enrolled read-only reports, exact-scope review, task status/questions and Stop are supported. Files, content exports and permission changes are rejected. Use /help.'
        return result


class ProductionReportTelegramTransport(ReportTelegramTransport):
    """Real fixed outbound HTTPS capability, also contract-tested using MockTransport."""
    consumer_fd=None
    consumer_directory=None
    def acquire_consumer_guard(self):
        if self.consumer_fd is not None:raise ValueError('Telegram consumer already started')
        if self.consumer_directory is None:raise ValueError('Protected Telegram consumer directory required')
        parent=root_fd(self.consumer_directory)
        try:
            info=os.fstat(parent)
            if info.st_uid!=os.getuid() or info.st_mode & 0o077:raise ValueError('Private consumer directory required')
            fd=os.open('telegram-consumer.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=parent)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode & 0o077:raise ValueError('Private consumer lock required')
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);self.consumer_fd=fd
            except BaseException:os.close(fd);raise
        finally:os.close(parent)
    def release_consumer_guard(self):
        if self.consumer_fd is not None:
            fd,self.consumer_fd=self.consumer_fd,None
            fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
    async def run(self,stop_event,**kwargs):
        if self.consumer_fd is None:raise ValueError('Telegram consumer guard required')
        await super().run(stop_event,**kwargs)
    def validate_http(self,http):
        if not isinstance(http,TelegramHTTPS) or http.live_authorized is not True:raise ValueError('Explicit fixed Telegram HTTPS connector required')
    @classmethod
    async def create(cls,store,http,allow,**kwargs):
        adapter=await offloop(ConfiguredReportAdapter,store,allow)
        result=await offloop(cls,adapter,http,**kwargs)
        result.pending_configuration=(dict(allow),hashlib.sha256(http._token.encode()).hexdigest())
        return result
    async def configure_authorized(self):
        async with self.lock:await offloop(self.configure,*self.pending_configuration)
    def configure(self,allow,bot_identity):
        if self.consumer_fd is None:raise ValueError('Configuration requires supervisor-owned consumer guard')
        with self.adapter.store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS report_telegram_bot(id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL)')
            previous=db.execute('SELECT identity FROM report_telegram_bot WHERE id=1').fetchone()
            if previous and previous[0]!=bot_identity:raise ValueError('Bot credential changed: use a separate reviewed task database')
            if not previous:
                if db.execute('SELECT COUNT(*) FROM telegram_bindings').fetchone()[0] or db.execute('SELECT offset FROM telegram_transport_meta WHERE id=1').fetchone()[0]:raise ValueError('Production Telegram requires fresh or identity-bound state')
                db.execute('INSERT INTO report_telegram_bot VALUES(1,?)',(bot_identity,))
            existing={uid:(owner,active) for uid,owner,active in db.execute('SELECT user_id,owner,active FROM telegram_bindings')}
        # No startup rebind for unchanged verified owners: preserve callback generations.
        for uid,(owner,active) in existing.items():
            if active and allow.get(uid)!=owner:self.adapter.revoke(uid)
        for uid,owner in allow.items():
            if existing.get(uid)!=(owner,1):self.adapter.bind_verified(uid,uid,owner)


async def attach_configured_telegram(runtime,settings):
    if not settings.assistant_report_telegram_enabled:return
    if settings.assistant_report_telegram_config_path is None:raise ValueError('Explicit protected Telegram enrollment required')
    token,allow=await offloop(load_config,settings.assistant_report_telegram_config_path,{g.owner for g in runtime.store.projects.values()})
    http=TelegramHTTPS(token,live_authorized=True)
    runtime.transport=await ProductionReportTelegramTransport.create(runtime.store,http,allow,enabled=True)
    runtime.transport.consumer_directory=settings.assistant_report_telegram_config_path.parent
    runtime.service.transport=runtime.transport
