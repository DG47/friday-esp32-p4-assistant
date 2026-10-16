"""Per-claim admission guard; revocation does not undo already-admitted effects."""
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar('friday_execution_authority', default=None)

class ExecutionRevoked(RuntimeError):
    pass

@contextmanager
def execution_scope(check):
    token = _current.set(check)
    try:
        yield
    finally:
        _current.reset(token)

def require_execution_authority():
    check = _current.get()
    if check is not None and not check():
        raise ExecutionRevoked('Execution authority revoked; outcome may require reconciliation')
