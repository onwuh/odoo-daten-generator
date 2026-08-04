"""Shared logging configuration — replaces the print()/sys.stdout-redirect approach.

Console output always goes through a StreamHandler (configured once, idempotent).
The GUI additionally attaches/detaches a QueueLogHandler per run to mirror log
records into its log textbox.
"""
import logging
import queue

_CONFIGURED = False


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


class QueueLogHandler(logging.Handler):
    """Mirrors formatted log records into a queue.Queue for GUI polling."""

    def __init__(self, log_queue: "queue.Queue"):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            self.handleError(record)
