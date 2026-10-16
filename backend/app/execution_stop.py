"""Authenticated same-device job-incarnation Stop, separate from playback and reports."""
import asyncio
from .stop_observer import StopObserver

CAPABILITY='execution_stop_v1'

class ExecutionStop:
    def __init__(self,store,worker):
        self.store,self.worker=store,worker
        self.observer=StopObserver()
    async def request(self,device,job_id,incarnation,stop_id):
        async def persist():
            return await asyncio.to_thread(self.store.request_execution_stop,device,job_id,incarnation,stop_id)
        result=await self.observer.request((device,job_id,incarnation,stop_id),0,persist,lambda:self.worker._handlers.get(job_id))
        return {'type':'execution.stop.observed','request_id':stop_id,'job_id':job_id,'execution_id':incarnation,
                'ack_confirmed':result.ack_confirmed,'authority_revoked':result.authority_revoked,
                'handler_drained':result.handler_drained,'state_at_ack':result.state_at_ack,
                'message':'Request '+job_id+'. '+result.message}
    async def status(self,device,job_id,incarnation,request_id):
        status=await asyncio.to_thread(self.store.execution_status,device,job_id,incarnation)
        return {'type':'execution.stop.status','request_id':request_id,'job_id':job_id,'execution_id':incarnation,**status}

    async def reconciliation_preview(self,device,job_id,incarnation):
        status=await asyncio.to_thread(self.store.execution_status,device,job_id,incarnation)
        return {'job_id':job_id,'execution_id':incarnation,**status,'replay_allowed':False,
                'effects_reversed':False,'automatic_resolution_available':False,
                'needs_effect_evidence':status['outcome_uncertain'],
                'message':'Unknown effects stay quarantined. A drained coroutine is not evidence that native or remote effects stopped. No replay or clear operation is offered.'}
