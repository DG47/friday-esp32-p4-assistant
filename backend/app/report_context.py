"""Trusted device-owner projection; enrollment is supplied by the gated runtime."""
import secrets
import time
from .models import Surface, SurfaceAction

class ReportContext:
    def __init__(self,store,device_owners):
        self.store,self.device_owners,self.tokens=store,dict(device_owners),{}
    def owner(self,device):
        owner=self.device_owners.get(device)
        if not owner:raise ValueError('Task owner binding unavailable')
        return owner
    def scene(self,device,task_id):
        owner=self.owner(device);task=self.store.get(owner,task_id)
        self.tokens={key:value for key,value in self.tokens.items() if value['expires']>time.time()}
        actions=[]
        if task['state'] not in {'ready_for_review','failed','stopped','uncertain'}:
            existing=next((key for key,value in self.tokens.items() if value['device']==device and value['task']==task_id and value['generation']==task['generation']),None)
            self.tokens={key:value for key,value in self.tokens.items() if value['device']!=device or key==existing}
            token=existing or 'reportstop:'+secrets.token_hex(12)
            self.tokens[token]={'device':device,'owner':owner,'task':task_id,'generation':task['generation'],'expires':time.time()+300}
            actions.append(SurfaceAction(id=token,label='Stop'))
        actions.append(SurfaceAction(id='dismiss',label='Dismiss'))
        items=[{'label':'Task '+task_id,'detail':'Revision '+str(task['revision'])}]
        if task['artifact']:items.append({'label':'Report verified locally','detail':'SHA256 '+task['artifact']['sha256']})
        return Surface(kind='artifact',title='Project report',subtitle='State: '+task['state'],items=items,actions=actions,expires_ms=30000)
    def ask(self,device,task_id,turn_id,text):
        return self.store.conversation(self.owner(device),task_id,turn_id,'LCD',text)
    def stop_grant(self,device,token):
        grant=self.tokens.get(token);owner=self.owner(device)
        if not grant or grant['device']!=device or grant['owner']!=owner or time.time()>=grant['expires']:raise ValueError('Task control expired')
        return dict(grant)
    def stop(self,device,token):
        grant=self.stop_grant(device,token)
        task=self.store.get(grant['owner'],grant['task'])
        if task['generation']!=grant['generation']:raise ValueError('Task control superseded')
        return self.store.stop(grant['owner'],grant['task'],token,expected_generation=grant['generation'])
