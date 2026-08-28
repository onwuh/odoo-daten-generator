"""Shared logging configuration — replaces the print()/sys.stdout-redirect approach.

Console output always goes through a StreamHandler (configured once, idempotent).
Callers that want to mirror a *single run's* log records somewhere else
(the web layer's SSE stream, the retired GUI's log textbox) attach their own
handler through :func:`run_log_capture`.

Run scoping (S9)
----------------
Every module logs through ``logging.getLogger(__name__)``, so runs cannot be
separated by logger *name* — and ``configure_logging()`` attaches its handler to
the **root** logger at import time of ``orchestrator.py``. With several runs in
flight at once, a per-run handler on the root logger therefore sees every other
run's records too.

The separation is done by a :class:`contextvars.ContextVar` holding the active
run id plus a :class:`RunIdFilter` on each run's handler. The contextvar must be
set *inside* the worker at entry: a fresh ``threading.Thread`` starts with an
empty context rather than inheriting its parent's, and pool threads are reused,
so setting it anywhere else leaks or loses the binding.
"""
import contextvars
import logging
import queue
from contextlib import contextmanager
from typing import Optional

_CONFIGURED = False

#: Active run id for the current thread/task. ``None`` outside a run.
current_run_id: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "odoo_generator_run_id", default=None
)


def configure_logging(level=logging.INFO) -> None:
    """Attach a console StreamHandler to the root logger, once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


class RunIdFilter(logging.Filter):
    """Passes only records emitted while ``run_id`` is the active run.

    Attached to a per-run handler on the root logger, this is what keeps two
    concurrent runs' log streams from cross-contaminating.
    """

    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        return current_run_id.get() == self.run_id


class QueueLogHandler(logging.Handler):
    """Mirrors formatted log records into a queue.Queue for polling consumers."""

    def __init__(self, log_queue: "queue.Queue"):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            self.handleError(record)


def bind_run(run_id: str) -> "contextvars.Token":
    """Mark the calling thread as belonging to ``run_id``. Call at worker entry."""
    return current_run_id.set(run_id)


def unbind_run(token: "contextvars.Token") -> None:
    try:
        current_run_id.reset(token)
    except ValueError:
        # Token created in a different context (thread reuse) — clearing is enough.
        current_run_id.set(None)


@contextmanager
def run_log_capture(run_id: str, handler: logging.Handler, level=logging.INFO):
    """Route this run's log records into ``handler`` for the duration of the block.

    Binds the run id in the current context, attaches the handler to the root
    logger behind a :class:`RunIdFilter`, and removes it again on exit — including
    on exception, so a failed run never leaves a handler behind.
    """
    handler.addFilter(RunIdFilter(run_id))
    handler.setLevel(level)
    if handler.formatter is None:
        handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    token = bind_run(run_id)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        unbind_run(token)
