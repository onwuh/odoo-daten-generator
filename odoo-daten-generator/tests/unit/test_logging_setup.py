"""Unit tests for logging_setup.py — D2 logging infrastructure + S9 run scoping.

The old idempotence test counted root handlers, which asserted the *design* S9
replaces: one process-wide root StreamHandler plus per-run handlers on the same
root logger, which cross-contaminate as soon as two runs overlap. It is rewritten
here rather than supplemented — the separation is now a contextvar plus a filter,
and that is what has to be asserted.
"""
import logging
import os
import queue
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import logging_setup


class _CollectingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))


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
    # QueueLogHandler never raises even if the queue put fails
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
    # configure_logging() attaches the console handler exactly once
    # ------------------------------------------------------------------
    try:
        logging_setup.configure_logging()
        assert logging_setup._CONFIGURED is True
        before = len(logging.getLogger().handlers)
        logging_setup.configure_logging()
        after = len(logging.getLogger().handlers)
        assert after == before, f"{before} -> {after}"
        results.append(("configure_logging: second call is a no-op", True, f"{before}->{after}"))
    except Exception as e:
        results.append(("configure_logging: second call is a no-op", False, str(e)))

    # ------------------------------------------------------------------
    # run_log_capture routes only this run's records, and removes itself
    # ------------------------------------------------------------------
    try:
        handler = _CollectingHandler()
        module_logger = logging.getLogger("modules.fake")
        root_before = len(logging.getLogger().handlers)
        with logging_setup.run_log_capture("run-a", handler):
            module_logger.info("inside run-a")
        module_logger.info("outside any run")
        root_after = len(logging.getLogger().handlers)
        assert handler.messages == ["inside run-a"], handler.messages
        assert root_after == root_before, f"Handler nicht entfernt: {root_before} -> {root_after}"
        assert logging_setup.current_run_id.get() is None, "Run-ID nicht zurückgesetzt"
        results.append(("run_log_capture: captures inside, releases after", True, ""))
    except Exception as e:
        results.append(("run_log_capture: captures inside, releases after", False, str(e)))

    # ------------------------------------------------------------------
    # The handler is removed even when the run raises
    # ------------------------------------------------------------------
    try:
        handler = _CollectingHandler()
        root_before = len(logging.getLogger().handlers)
        try:
            with logging_setup.run_log_capture("run-boom", handler):
                raise RuntimeError("module exploded")
        except RuntimeError:
            pass
        assert len(logging.getLogger().handlers) == root_before, "Handler nach Ausnahme geblieben"
        results.append(("run_log_capture: handler removed on exception", True, ""))
    except Exception as e:
        results.append(("run_log_capture: handler removed on exception", False, str(e)))

    # ------------------------------------------------------------------
    # Session isolation: two concurrent runs never see each other's records.
    # Every module logs through logging.getLogger(__name__), so runs cannot be
    # separated by logger name — this is the test that would fail against the
    # pre-S9 root-handler design.
    # ------------------------------------------------------------------
    try:
        handler_a, handler_b = _CollectingHandler(), _CollectingHandler()
        barrier = threading.Barrier(2)
        shared_logger = logging.getLogger("modules.shared")

        def worker(run_id, handler, lines):
            # Bind INSIDE the thread: a fresh thread starts with an empty context
            # rather than inheriting its parent's.
            with logging_setup.run_log_capture(run_id, handler):
                barrier.wait(timeout=5)
                for i in range(lines):
                    shared_logger.info(f"{run_id}-line-{i}")
                    time.sleep(0.001)

        t_a = threading.Thread(target=worker, args=("run-a", handler_a, 20))
        t_b = threading.Thread(target=worker, args=("run-b", handler_b, 20))
        t_a.start(); t_b.start()
        t_a.join(timeout=10); t_b.join(timeout=10)

        assert len(handler_a.messages) == 20, handler_a.messages
        assert len(handler_b.messages) == 20, handler_b.messages
        assert all(m.startswith("run-a-") for m in handler_a.messages), handler_a.messages[:3]
        assert all(m.startswith("run-b-") for m in handler_b.messages), handler_b.messages[:3]
        results.append(("Session-Isolation: parallele Läufe teilen keine Log-Records", True, "20/20"))
    except Exception as e:
        results.append(("Session-Isolation: parallele Läufe teilen keine Log-Records", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
