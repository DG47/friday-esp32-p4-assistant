"""Authenticated same-device job-incarnation Stop, separate from playback and reports."""
import asyncio
from .stop_observer import StopObserver

CAPABILITY='execution_stop_v1'

class ExecutionStop:
    def __init__(self,store,worker):
        self.store,self.worker=store,worker
        self.observer=StopObserver()
        self.holds=set()
    def pending_for(self,device):
        blocked=False
        for scope in list(self.holds):
            if scope[0]!=device:continue
            try:status=self.store.execution_status(*scope)
            except Exception:
                blocked=True
                continue
            if status['outcome_uncertain'] or status['job_status'] in {'queued','running'}:blocked=True
            else:self.holds.discard(scope)
        return blocked
    def recovery(self,device):
        durable=self.store.execution_recovery(device)
        if durable:return durable
        if self.pending_for(device):
            scope=next(scope for scope in self.holds if scope[0]==device)
            return {'job_id':scope[1],'execution_id':scope[2]}
        return None
    async def request(self,device,job_id,incarnation,stop_id):
        # Validate exact ownership before a same-device hold. The observer cache
        # may evict a failed attempt; the admission hold must outlive that cache.
        await asyncio.to_thread(self.store.execution_status,device,job_id,incarnation)
        scope=(device,job_id,incarnation)
        if scope not in self.holds and len(self.holds)>=256:
            return {'type':'execution.stop.observed','request_id':stop_id,'job_id':job_id,'execution_id':incarnation,'ack_confirmed':False,'authority_revoked':False,'handler_drained':False,'state_at_ack':None,'message':'Stop not confirmed; work may continue. Review status.'}
        self.holds.add(scope)
        async def persist():
            return await asyncio.to_thread(self.store.request_execution_stop,device,job_id,incarnation,stop_id)
        key=(device,job_id,incarnation,stop_id)
        prior=self.observer.attempts.get(key)
        if prior and prior[0].done() and not prior[0].cancelled() and prior[0].exception() is not None and prior[1].done():
            # Only an explicit retry restarts a completed failed storage attempt.
            # The atomic ledger still coalesces a commit whose delivery was lost.
            self.observer.attempts.pop(key)
        result=await self.observer.request(key,0,persist,lambda:self.worker._handlers.get(job_id))
        return {'type':'execution.stop.observed','request_id':stop_id,'job_id':job_id,'execution_id':incarnation,
                'ack_confirmed':result.ack_confirmed,'authority_revoked':result.authority_revoked,
                'handler_drained':result.handler_drained,'state_at_ack':result.state_at_ack,
                'message':'Request '+job_id+'. '+result.message}
    async def status(self,device,job_id,incarnation,request_id):
        status=await asyncio.to_thread(self.store.execution_status,device,job_id,incarnation)
        return {'type':'execution.stop.status','request_id':request_id,'job_id':job_id,'execution_id':incarnation,**status}

    async def reconciliation_preview(self,device,job_id,incarnation):
        status=await asyncio.to_thread(self.store.execution_status,device,job_id,incarnation)
        held=self.pending_for(device) and (device,job_id,incarnation) in self.holds
        return {'job_id':job_id,'execution_id':incarnation,**status,'observer_hold':held,'replay_allowed':False,
                'effects_reversed':False,'automatic_resolution_available':False,
                'needs_effect_evidence':status['outcome_uncertain'] or held,
                'message':'Unknown effects stay quarantined. A drained coroutine is not evidence that native or remote effects stopped. No replay or clear operation is offered.'}
