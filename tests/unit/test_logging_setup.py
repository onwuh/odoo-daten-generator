"""Unit tests for logging_setup.py — D2 logging infrastructure (no Odoo connection needed)."""
import logging
import os
import queue
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import logging_setup


def run():
    results = []

    # ------------------------------------------------------------------
    # QueueLogHandler puts the formatted message into the queue
    # ------------------------------------------------------------------
    try:
        q = queue.Queue()
        handler = logging_setup.QueueLogHandler(q)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test_logging_setup.queue")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False
        try:
            logger.info("hello from test")
            msg = q.get_nowait()
            assert msg == "hello from test", msg
            results.append(("QueueLogHandler: formatted message lands in queue", True, msg))
        finally:
            logger.removeHandler(handler)
    except Exception as e:
        results.append(("QueueLogHandler: formatted message lands in queue", False, str(e)))

    # ------------------------------------------------------------------
    # QueueLogHandler never raises even if the queue put fails (handleError path)
    # ------------------------------------------------------------------
    try:
        class _BrokenQueue:
            def put(self, *_a, **_kw):
                raise RuntimeError("queue is broken")

        handler = logging_setup.QueueLogHandler(_BrokenQueue())
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
        handler.emit(record)  # must not raise
        results.append(("QueueLogHandler: broken queue does not raise", True, ""))
    except Exception as e:
        results.append(("QueueLogHandler: broken queue does not raise", False, str(e)))

    # ------------------------------------------------------------------
    # configure_logging() is idempotent — calling twice adds exactly one StreamHandler
    # ------------------------------------------------------------------
    try:
        root = logging.getLogger()
        logging_setup.configure_logging()  # first call in this process may or may not be the very first ever
        after_first = len(root.handlers)
        logging_setup.configure_logging()  # second call must be a no-op
        after_second = len(root.handlers)
        assert after_second == after_first, f"handler count changed: {after_first} -> {after_second}"
        results.append(("configure_logging: idempotent, no duplicate handlers", True, f"{after_first}->{after_second}"))
    except Exception as e:
        results.append(("configure_logging: idempotent, no duplicate handlers", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
