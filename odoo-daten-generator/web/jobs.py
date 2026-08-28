"""Durable-ish job queue: a run is enqueued, not awaited.

A full run takes 2–5 minutes, past every default gateway timeout, so
``POST /api/runs`` answers ``202 {run_id}`` and the work happens on a worker
thread. The work is I/O-bound (HTTP to Odoo, HTTP to the LLM), so threads are the
right shape and a Raspberry Pi 5 handles the expected ~5 concurrent runs.

Admission control has two levels: a fixed worker pool, so request N+1 queues
instead of spawning another run, and a per-session cap so one person cannot fill
every slot.
"""
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import orchestrator
import run_config
from connect_service import detect_provider
from llm_service import LLMService
from logging_setup import run_log_capture
from run_journal import JournalingClient, RunJournal
from web.sse import EventBroker

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

MODULE_PENDING = "pending"
MODULE_RUNNING = "running"
MODULE_DONE = "done"
MODULE_FAILED = "failed"


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def worker_count() -> int:
    return _int_env("ODOO_GENERATOR_WORKERS", 6)


def per_session_limit() -> int:
    return _int_env("ODOO_GENERATOR_SESSION_RUN_LIMIT", 2)


class AdmissionRefused(RuntimeError):
    """Raised when a session has too many runs in flight."""


@dataclass
class RunRecord:
    run_id: str
    session_id: str
    target: Optional[str] = None
    status: str = STATUS_QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    modules: "Dict[str, str]" = field(default_factory=dict)
    module_order: List[str] = field(default_factory=list)
    module_errors: Dict[str, str] = field(default_factory=dict)
    llm_calls: int = 0
    llm_tokens: int = 0
    api_errors: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    record_estimate: Dict[str, int] = field(default_factory=dict)
    journal_records: int = 0

    def public_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "target": self.target,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "modules": [
                {"key": key,
                 "label": run_config.MODULE_LABELS.get(key, key),
                 "status": self.modules.get(key, MODULE_PENDING),
                 "error": self.module_errors.get(key)}
                for key in self.module_order
            ],
            "llm_calls": self.llm_calls,
            "llm_tokens": self.llm_tokens,
            # Bodies are already redacted in odoo_client._redact_error_body — the
            # run summary is exactly the second pipe that would otherwise carry an
            # unredacted response body across the API boundary.
            "api_errors": self.api_errors,
            "error": self.error,
            "record_estimate": self.record_estimate,
            "journal_records": self.journal_records,
        }


class JobQueue:
    def __init__(self, broker: EventBroker, workers: Optional[int] = None):
        self.broker = broker
        self.workers = workers if workers is not None else worker_count()
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._runs: Dict[str, RunRecord] = {}
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._threads: List[threading.Thread] = []
        self._started = False
        self._counter = 0

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self.workers):
            t = threading.Thread(target=self._worker_loop, name=f"run-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        for _ in self._threads:
            self._queue.put(None)
        self._started = False

    # -- submission -----------------------------------------------------
    def _next_run_id(self) -> str:
        with self._lock:
            self._counter += 1
            suffix = self._counter
        return f"demo-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix:03d}"

    def active_for_session(self, session_id: str) -> int:
        with self._lock:
            return sum(1 for r in self._runs.values()
                       if r.session_id == session_id
                       and r.status in (STATUS_QUEUED, STATUS_RUNNING))

    def submit(self, *, session, payload: Dict[str, Any]) -> RunRecord:
        limit = per_session_limit()
        if self.active_for_session(session.id) >= limit:
            raise AdmissionRefused(
                f"Es laufen bereits {limit} Läufe in dieser Sitzung. "
                "Bitte auf deren Ende warten."
            )

        connect = session.connect
        ctx, selected = run_config.build_context(
            payload,
            language_name=connect.language_name,
            language_code=connect.language_code,
            llm_model_name=session.llm_model or "",
            installed_modules=connect.installed_modules,
            feature_flags=connect.feature_flags,
            existing_company_ids=connect.existing_company_ids,
            existing_product_ids=connect.existing_product_ids,
        )

        run_id = self._next_run_id()
        keys = run_config.active_progress_keys(ctx, selected)
        record = RunRecord(
            run_id=run_id,
            session_id=session.id,
            target=session.base_url,
            module_order=keys,
            modules={k: MODULE_PENDING for k in keys},
            record_estimate=run_config.estimate_record_counts(ctx, selected),
        )
        with self._lock:
            self._runs[run_id] = record
            self._jobs[run_id] = {
                "ctx": ctx,
                "base_url": session.base_url,
                "database": session.database,
                "odoo_key": session.odoo_key,
                "llm_key": session.llm_key,
                "llm_model": session.llm_model,
                "llm_provider": session.llm_provider,
            }
        self.broker.create(run_id)
        session.run_ids.append(run_id)
        self._publish(run_id, "status", record.public_dict())
        self._queue.put(run_id)
        return record

    # -- inspection -----------------------------------------------------
    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            return self._runs.get(run_id)

    def for_session(self, session_id: str) -> List[RunRecord]:
        with self._lock:
            return [r for r in self._runs.values() if r.session_id == session_id]

    def prune(self, max_age_seconds: float = 3600.0) -> int:
        """Drop finished run records (and their event streams) past max_age.

        Without this both dicts grow for the life of the process: `_runs` is
        never pruned and `_execute` closes a stream but never forgets it, so up
        to MAX_EVENTS events per run stay resident. Invisible on a laptop,
        an unbounded leak on a long-lived server process.
        """
        cutoff = time.time() - max_age_seconds
        with self._lock:
            stale = [rid for rid, rec in self._runs.items()
                     if rec.status in (STATUS_DONE, STATUS_FAILED)
                     and (rec.finished_at or rec.created_at) < cutoff]
            for run_id in stale:
                self._runs.pop(run_id, None)
                self._jobs.pop(run_id, None)
        for run_id in stale:
            self.broker.forget(run_id)
        return len(stale)

    # -- internals ------------------------------------------------------
    def _publish(self, run_id: str, event_type: str, data: Any) -> None:
        stream = self.broker.get(run_id)
        if stream:
            stream.publish(event_type, data)

    def _worker_loop(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                return
            try:
                self._execute(run_id)
            except Exception as exc:  # never let a worker thread die
                logger.exception(f"Worker-Fehler in Lauf {run_id}: {exc}")
            finally:
                self._queue.task_done()

    def _execute(self, run_id: str) -> None:
        record = self.get(run_id)
        with self._lock:
            job = self._jobs.pop(run_id, None)
        if record is None or job is None:
            return

        record.status = STATUS_RUNNING
        record.started_at = time.time()
        self._publish(run_id, "status", record.public_dict())

        class _StreamHandler(logging.Handler):
            def __init__(self, publish):
                super().__init__()
                self._publish = publish

            def emit(self, rec: logging.LogRecord) -> None:
                try:
                    self._publish("log", {"level": rec.levelname, "message": self.format(rec)})
                except Exception:
                    self.handleError(rec)

        handler = _StreamHandler(lambda t, d: self._publish(run_id, t, d))
        handler.setFormatter(logging.Formatter("%(message)s"))

        journal = RunJournal(run_id)
        journal.set_target(job["base_url"])
        client = None
        llm = None

        # run_log_capture binds the run id in THIS thread's context — a fresh
        # thread starts with an empty context rather than inheriting one, and
        # pool threads are reused, so binding anywhere else leaks between runs.
        with run_log_capture(run_id, handler):
            try:
                client = JournalingClient(job["base_url"], job["database"], job["odoo_key"],
                                          journal=journal)
                provider = detect_provider(job["llm_key"], job["llm_provider"])
                llm = LLMService(job["llm_key"], job["llm_model"], provider)

                def on_start(name: str) -> None:
                    key = run_config.PROGRESS_KEY_MAP.get(name, name)
                    if key in record.modules:
                        record.modules[key] = MODULE_RUNNING
                    self._publish(run_id, "module", {"key": key, "status": MODULE_RUNNING})

                def on_done(name: str, ok: bool = True) -> None:
                    key = run_config.PROGRESS_KEY_MAP.get(name, name)
                    status = MODULE_DONE if ok else MODULE_FAILED
                    if key in record.modules:
                        record.modules[key] = status
                    if not ok:
                        # orchestrator._run_module swallows the exception into a
                        # log line. In the desktop app that scrolled past; here
                        # the module row has to show it.
                        record.module_errors[key] = "Modul fehlgeschlagen — Details im Protokoll"
                    self._publish(run_id, "module",
                                  {"key": key, "status": status,
                                   "error": record.module_errors.get(key)})

                orchestrator.run(client, llm, job["ctx"],
                                 on_module_start=on_start, on_module_done=on_done)
                record.status = STATUS_DONE
            except Exception as exc:
                record.status = STATUS_FAILED
                record.error = str(exc)[:500]
                logger.error(f"Kritischer Fehler: {exc}")
            finally:
                record.finished_at = time.time()
                if llm is not None:
                    record.llm_calls = llm.total_calls
                    record.llm_tokens = llm.total_tokens
                if client is not None:
                    record.api_errors = client.get_errors()
                record.journal_records = len(journal.entries)
                for key, status in record.modules.items():
                    if status in (MODULE_PENDING, MODULE_RUNNING):
                        record.modules[key] = (MODULE_FAILED if record.status == STATUS_FAILED
                                               else MODULE_DONE)

        self._publish(run_id, "status", record.public_dict())
        self._publish(run_id, "end", {"run_id": run_id, "status": record.status})
        stream = self.broker.get(run_id)
        if stream:
            stream.close()
