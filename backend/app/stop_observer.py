"""Bounded observation of durable Stop; never a forced-effect termination primitive."""
import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class StopObservation:
    code: str
    message: str
    ack_confirmed: bool = False
    authority_revoked: bool = False
    handler_drained: bool = False
    state_at_ack: str | None = None


UNCONFIRMED = StopObservation('stop_not_confirmed', 'Stop not confirmed; work may continue. Recover status before retrying. No termination or reversal is confirmed.')


class StopObserver:
    def __init__(self, *, ack_budget=1.0, drain_grace=.25, capacity=256):
        self.ack_budget, self.drain_grace, self.capacity = ack_budget, drain_grace, capacity
        self.attempts = {}

    async def _observe(self, persist, runner, generation):
        try:
            task = await asyncio.shield(persist)
        except Exception:
            return UNCONFIRMED  # No provider/storage details or false commit claim.
        revoked = task['generation'] > generation
        if not revoked:
            return StopObservation('stop_already_terminal', 'The task was already terminal. No execution authority was changed and prior effects were not reversed.', True, False, False, task['state'])
        active = runner()
        drained = False
        if active is not None:
            if not active.done():
                active.cancel()  # Best effort only, AFTER the durable revocation.
            done, _ = await asyncio.wait({active}, timeout=self.drain_grace)
            drained = bool(done)
        # Drainage of a coroutine is not proof that model/remote computation stopped.
        # Persisted stopped status is obtained separately by normal status/recovery.
        message = 'Stop requested; execution authority revoked. '
        message += 'The owned handler drained; previously admitted effects are not reversed. Recover durable status.' if drained else 'Work may still be outstanding. Previously admitted effects are not reversed.'
        message += ' Durable state at revocation: '+task['state']+'.'
        return StopObservation('stop_authority_revoked', message, True, True, drained, task['state'])

    async def request(self, key, generation, persist, runner):
        attempt = self.attempts.get(key)
        if attempt is None:
            if len(self.attempts) >= self.capacity:
                for old, (_, observation, _) in list(self.attempts.items()):
                    if observation.done():
                        self.attempts.pop(old)
                        break
                else:
                    return UNCONFIRMED  # Never evict/cancel an outstanding revoke.
            persistence = asyncio.create_task(persist(), name='friday-stop-revocation')
            observation = asyncio.create_task(self._observe(persistence, runner, generation), name='friday-stop-observer')
            attempt = self.attempts[key] = (persistence, observation, generation)
        persistence, observation, generation = attempt
        done, _ = await asyncio.wait({persistence}, timeout=self.ack_budget)
        if not done:
            return UNCONFIRMED  # Owned tasks continue; timeout is NOT cancellation.
        done, _ = await asyncio.wait({observation}, timeout=self.drain_grace)
        if done:
            return observation.result()
        # Persistence completed, but don't guess its result or cancel the observer.
        try:
            task = persistence.result()
        except Exception:
            return UNCONFIRMED
        if task['generation'] <= generation:
            return StopObservation('stop_already_terminal', 'The task was already terminal. No execution authority was changed and prior effects were not reversed.', True, False, False, task['state'])
        return StopObservation('stop_authority_revoked', 'Stop requested; execution authority revoked. Work may still be outstanding. Previously admitted effects are not reversed. Durable state at revocation: '+task['state']+'.', True, True, False, task['state'])

    async def close(self):
        # Lifetime ownership is retained until outstanding persistence/observation
        # drains. Shutdown itself has no hard deadline in this contract.
        tasks = [job for persistence, observation, _ in self.attempts.values() for job in (persistence, observation)]
        if tasks:
            await asyncio.gather(*(asyncio.shield(job) for job in tasks), return_exceptions=True)
