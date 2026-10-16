/* Target-bound observer state. A deadline is uncertainty, never success. */
(function (root) {
  const id = value => typeof value === 'string' && /^[A-Za-z0-9._:-]{1,128}$/.test(value);
  const incarnation = value => typeof value === 'string' && /^[0-9a-f]{32}$/.test(value);
  class ExecutionStopClient {
    constructor(send, now = () => performance.now()) {
      this.send = send; this.now = now; this.target = null; this.pending = null;
      this.query = null; this.phase = 'idle'; this.notice = ''; this.finished = new Set();
    }
    blocked() { return ['pending', 'unconfirmed', 'uncertain'].includes(this.phase); }
    capture(job, execution) {
      if (!id(job) || !incarnation(execution) || this.finished.has(execution) || this.blocked()) return false;
      this.target = {job_id: job, execution_id: execution}; this.phase = 'available'; this.notice = ''; return true;
    }
    restore(target) {
      if (!target || !id(target.job_id) || !incarnation(target.execution_id)) return false;
      if (this.pending && (this.pending.job_id !== target.job_id || this.pending.execution_id !== target.execution_id)) return false;
      this.target = {job_id: target.job_id, execution_id: target.execution_id};
      this.phase = 'uncertain'; this.notice = 'Earlier execution outcome is uncertain; review it on the Mac. No automatic replay.'; return true;
    }
    request(request) {
      if (!this.target || !id(request)) return false;
      // Explicit retry uses the same action identity and target, never a replacement.
      const action = this.pending?.request_id || request;
      this.pending = {...this.target, request_id: action}; this.phase = 'pending';
      this.deadline = this.now() + 1000; this.notice = 'Stop requested; acknowledgement pending.';
      if (!this.send({type: 'execution.stop', ...this.pending})) this.unknown();
      return true;
    }
    status(request) {
      if (!this.target || !id(request)) return false;
      this.query = {...this.target, request_id: request};
      return this.send({type: 'execution.status', ...this.query});
    }
    unknown() { this.phase = 'unconfirmed'; this.notice = 'Stop not confirmed; work may continue. Review status before sending more work.'; }
    tick() { if (this.phase === 'pending' && this.now() >= this.deadline) this.unknown(); }
    disconnected() { if (this.target || this.pending) this.unknown(); }
    match(frame, expected) {
      return expected && frame.request_id === expected.request_id && frame.job_id === expected.job_id && frame.execution_id === expected.execution_id;
    }
    settled(phase, notice) { this.phase = phase; this.notice = notice; this.pending = null; this.query = null; this.target = null; }
    receive(frame) {
      if (frame.type === 'execution.stop.observed') {
        if (!this.match(frame, this.pending) || typeof frame.ack_confirmed !== 'boolean' || typeof frame.authority_revoked !== 'boolean' || typeof frame.handler_drained !== 'boolean') return false;
        if (!frame.ack_confirmed) { if (frame.authority_revoked) return false; this.unknown(); return true; }
        if (frame.authority_revoked && frame.state_at_ack === 'stopped_before_admission') this.settled('stopped_before_admission', 'Stopped before admission; no operation from this request started.');
        else if (!frame.authority_revoked && frame.state_at_ack === 'already_terminal') this.settled('already_terminal', 'Request already finished; Stop changed no authority. Prior effects may remain.');
        else if (frame.authority_revoked && frame.state_at_ack === 'outcome_uncertain') { this.phase = 'uncertain'; this.notice = 'Execution authority revoked; effects may remain. Review the outcome on the Mac.'; }
        else return false;
        return true;
      }
      if (frame.type === 'execution.stop.status') {
        if (!this.match(frame, this.query) || !['queued','running','completed','failed','expired','late'].includes(frame.job_status) || typeof frame.outcome_uncertain !== 'boolean' || typeof frame.authority_revoked !== 'boolean' || typeof frame.no_execution_admitted !== 'boolean') return false;
        if (frame.no_execution_admitted && frame.authority_revoked && frame.job_status === 'failed') this.settled('stopped_before_admission', 'Stopped before admission; no operation from this request started.');
        else if (frame.outcome_uncertain) { this.phase = 'uncertain'; this.notice = 'Execution outcome remains uncertain; no automatic replay or reversal is confirmed.'; }
        else if (['completed','failed','expired','late'].includes(frame.job_status)) this.settled('already_terminal', 'Request is terminal; prior effects are not reversed.');
        else this.unknown();
        return true;
      }
      return false;
    }
    result(job, execution) {
      if (!incarnation(execution)) return;
      this.finished.add(execution); if (this.finished.size > 64) this.finished.delete(this.finished.values().next().value);
      // A normal result does not acknowledge a pending Stop or prove effect reversal.
      if (!this.blocked() && this.target?.job_id === job && this.target.execution_id === execution) this.settled('already_terminal', '');
    }
  }
  root.ExecutionStopClient = ExecutionStopClient;
  if (typeof module !== 'undefined') module.exports = {ExecutionStopClient};
})(typeof globalThis !== 'undefined' ? globalThis : this);
