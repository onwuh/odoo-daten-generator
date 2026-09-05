"""Durable-ish job queue: a run is enqueued, not awaited.

A full run takes 2–5 minutes, past every default gateway timeout, so
``POST /api/runs`` answers ``202 {run_id}`` and the work happens on a worker
thread. The work is I/O-bound (HTTP to Odoo, HTTP to the LLM), so threads are the
right shape and a Raspberry Pi 5 handles the expected ~5 concurrent runs.

Admission control has two levels: a fixed worker pool, so request N+1 queues
instead of spawning another run, and a per-session cap so one person cannot fill
every slot.
"""
import contextlib
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import odoo_actions
import orchestrator
import run_config
from config import RunContext
from connect_service import detect_provider, fetch_existing_company_data
from llm_service import LLMService
from logging_setup import run_log_capture
from run_journal import JournalingClient, RunJournal, default_journal_dir, run_log_path
from web.sse import EventBroker

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
# S16: at least one company succeeded and at least one failed entirely
# (company-level failure — a module failing within an otherwise-successful
# company does NOT cause this; that's still STATUS_DONE, same as today,
# since orchestrator._run_module already swallows per-module exceptions).
STATUS_PARTIAL = "partial"

MODULE_PENDING = "pending"
MODULE_RUNNING = "running"
MODULE_DONE = "done"
MODULE_FAILED = "failed"
# A module that returned without error but did no work because a write-access
# probe blocked it (S10/R10) — e.g. "documents" when ir.attachment isn't
# creatable. orchestrator.py hardcodes documents' is_installed=True (it isn't
# gated on ctx.installed_modules like every other module), so on_done(ok=True)
# fires unconditionally and the row would otherwise read "fertig" with nothing
# created. Set from ctx.skipped_modules AFTER orchestrator.run() returns —
# module code has no channel back to on_done() to say "I skipped", only to ctx.
MODULE_SKIPPED = "skipped"


def _bare_module_key(key: str) -> str:
    """S16: strips a multi-company "{index}:{module_code}" prefix, if
    present, so MODULE_LABELS (keyed by bare module_code) can find it.
    Single-company keys have no prefix and pass through unchanged."""
    idx, _, rest = key.partition(":")
    return rest if idx.isdigit() and rest else key


def _company_index(key: str) -> Optional[int]:
    """S16: the company-loop index a qualified module key belongs to, or
    None for a single-company (unqualified) key — lets a frontend group
    rows by company without re-deriving the qualification scheme itself."""
    idx, sep, rest = key.partition(":")
    return int(idx) if sep and idx.isdigit() and rest else None


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def worker_count() -> int:
    return _int_env("ODOO_GENERATOR_WORKERS", 6)


def per_session_limit() -> int:
    return _int_env("ODOO_GENERATOR_SESSION_RUN_LIMIT", 2)


def _open_run_log_handler(run_id: str) -> Optional[logging.Handler]:
    """S11/D9 — full per-run log, kept local (run_journal.run_log_path,
    same directory/env-override/retention as the run journal), never sent
    anywhere: a feedback issue carries only the run_id as a reference to look
    this up on the machine that ran it.

    Best-effort, same rule as RunJournal._persist: a run must never fail
    because its own log couldn't be written (unwritable ODOO_GENERATOR_RUNS_DIR,
    read-only-rootfs profile without the volume mounted, etc).
    """
    try:
        default_journal_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(run_log_path(run_id), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        return handler
    except OSError as exc:
        logger.warning(f"⚠️  Lauf-Log konnte nicht angelegt werden: {exc}")
        return None


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
                 # S16: a multi-company key is "{index}:{module_code}" —
                 # MODULE_LABELS only knows the bare module_code, so the
                 # prefix must come off before the lookup, or every row
                 # falls back to showing its own raw key instead of a label.
                 "label": run_config.MODULE_LABELS.get(_bare_module_key(key), key),
                 "status": self.modules.get(key, MODULE_PENDING),
                 "error": self.module_errors.get(key),
                 "company_index": _company_index(key)}
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

        # S16: `"companies" in payload` is the multi-company path (D9/D11) —
        # `targets` is None for the legacy single-company shape, a signal
        # _execute() uses to skip the whole per-company resolution loop
        # (D10-Korrektur/D14/D15/D8b/D12) entirely and run exactly as before
        # S16 existed. This dual path is a deliberate bridge, not a
        # permanent fork: kept only until the frontend (WP5) always sends
        # the new shape, at which point the legacy branch becomes dead code
        # worth deleting, not extending further.
        if "companies" in payload:
            contexts_and_selected = run_config.build_context_list(
                payload,
                language_name=connect.language_name,
                language_code=connect.language_code,
                llm_model_name=session.llm_model or "",
                installed_modules=connect.installed_modules,
                feature_flags=connect.feature_flags,
                model_access=connect.model_access,
            )
            targets = [block.get("target") or {} for block in payload["companies"]]
        else:
            ctx, selected = run_config.build_context(
                payload,
                language_name=connect.language_name,
                language_code=connect.language_code,
                llm_model_name=session.llm_model or "",
                installed_modules=connect.installed_modules,
                feature_flags=connect.feature_flags,
                model_access=connect.model_access,
                existing_company_ids=connect.existing_company_ids,
                existing_product_ids=connect.existing_product_ids,
            )
            contexts_and_selected = [(ctx, selected)]
            targets = None

        run_id = self._next_run_id()
        multi = targets is not None and len(contexts_and_selected) > 1
        module_order: List[str] = []
        modules: Dict[str, str] = {}
        record_estimate: Dict[str, int] = {}
        for index, (ctx, selected) in enumerate(contexts_and_selected):
            keys = run_config.active_progress_keys(ctx, selected)
            if multi:
                label = (targets[index].get("name") or f"Firma {index + 1}") if targets else None
                qualified_keys = [f"{index}:{key}" for key in keys]
                module_order.extend(qualified_keys)
                modules.update({k: MODULE_PENDING for k in qualified_keys})
                for est_label, value in run_config.estimate_record_counts(
                        ctx, selected, company_label=label).items():
                    # D12: "Kostenstellen" is run-wide, not per-company — kept
                    # only once (first occurrence), never summed across companies.
                    if est_label == "Kostenstellen" and est_label in record_estimate:
                        continue
                    record_estimate[est_label] = record_estimate.get(est_label, 0) + value \
                        if est_label != "Kostenstellen" else value
            else:
                module_order.extend(keys)
                modules.update({k: MODULE_PENDING for k in keys})
                record_estimate.update(run_config.estimate_record_counts(ctx, selected))

        record = RunRecord(
            run_id=run_id,
            session_id=session.id,
            target=session.base_url,
            module_order=module_order,
            modules=modules,
            record_estimate=record_estimate,
        )
        with self._lock:
            self._runs[run_id] = record
            self._jobs[run_id] = {
                "contexts": contexts_and_selected,
                "targets": targets,
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
                     if rec.status in (STATUS_DONE, STATUS_FAILED, STATUS_PARTIAL)
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

        # S11/D9 — best-effort second handler for the same run's records,
        # written to local disk instead of the SSE stream. None (nullcontext
        # below) if it couldn't be opened; _open_run_log_handler already
        # logged why and a missing log file must not stop the run.
        log_file_handler = _open_run_log_handler(run_id)
        file_capture = (run_log_capture(run_id, log_file_handler)
                        if log_file_handler is not None else contextlib.nullcontext())

        journal = RunJournal(run_id)
        journal.set_target(job["base_url"])
        client = None
        llm = None
        targets = job["targets"]
        # S16: which company-loop iterations failed entirely (target
        # resolution, or anything inside that company's orchestrator.run()
        # call raising past its own internal per-module handling). Declared
        # outside the try so the outer `finally` below can use it too, for
        # both the legacy (always empty) and multi-company paths.
        failed_indices: Set[int] = set()

        # run_log_capture binds the run id in THIS thread's context — a fresh
        # thread starts with an empty context rather than inheriting one, and
        # pool threads are reused, so binding anywhere else leaks between runs.
        with run_log_capture(run_id, handler), file_capture:
            try:
                client = JournalingClient(job["base_url"], job["database"], job["odoo_key"],
                                          journal=journal)
                provider = detect_provider(job["llm_key"], job["llm_provider"])
                llm = LLMService(job["llm_key"], job["llm_model"], provider)

                if targets is None:
                    # Legacy single-company path — unchanged from before S16
                    # existed. Kept only as a bridge until the frontend (WP5)
                    # always sends the "companies" payload shape.
                    ctx, _selected = job["contexts"][0]

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

                    orchestrator.run(client, llm, ctx,
                                     on_module_start=on_start, on_module_done=on_done)
                    # A module can return normally (on_done(ok=True) already fired)
                    # yet have done nothing, because a write-access probe blocked
                    # it partway through. ctx.skipped_modules is the only channel
                    # for that — module code has no way to tell on_done() apart
                    # from a genuine success, and orchestrator.py's on_done
                    # signature (name, ok) is locked.
                    for key in ctx.skipped_modules:
                        if record.modules.get(key) == MODULE_DONE:
                            record.modules[key] = MODULE_SKIPPED
                            self._publish(run_id, "module", {"key": key, "status": MODULE_SKIPPED})
                    record.status = STATUS_DONE
                else:
                    # S16 multi-company path — the full per-company loop
                    # (D10-Korrektur/D14/D15/D8b/D12), see ROADMAP.md's
                    # S16-NEU spike for the numbered step-by-step design.
                    contexts_and_selected = job["contexts"]
                    shared_analytic_cache: Optional[List[int]] = None

                    for index, (ctx, _selected) in enumerate(contexts_and_selected):
                        target = targets[index]

                        # D6: per-iteration closures, built fresh each time so
                        # every published/stored key is qualified with which
                        # company it belongs to — orchestrator.py's on_start/
                        # on_done(name, ok) signature itself stays untouched
                        # (documented locked, see below).
                        def on_start(name: str, _idx: int = index) -> None:
                            key = run_config.PROGRESS_KEY_MAP.get(name, name)
                            qualified = f"{_idx}:{key}"
                            if qualified in record.modules:
                                record.modules[qualified] = MODULE_RUNNING
                            self._publish(run_id, "module", {"key": qualified, "status": MODULE_RUNNING})

                        def on_done(name: str, ok: bool = True, _idx: int = index) -> None:
                            key = run_config.PROGRESS_KEY_MAP.get(name, name)
                            qualified = f"{_idx}:{key}"
                            status = MODULE_DONE if ok else MODULE_FAILED
                            if qualified in record.modules:
                                record.modules[qualified] = status
                            if not ok:
                                record.module_errors[qualified] = "Modul fehlgeschlagen — Details im Protokoll"
                            self._publish(run_id, "module",
                                          {"key": qualified, "status": status,
                                           "error": record.module_errors.get(qualified)})

                        try:
                            # Step 1 (D14): reset the shared client's default
                            # context — this iteration's own company create()
                            # below must never run under the PREVIOUS
                            # iteration's company scope.
                            client._default_context = None
                            # Step 2 (D10-Korrektur): resolve this iteration's
                            # target company — covers both "create new" and
                            # "use existing" branches.
                            company_id, was_created = odoo_actions.resolve_target_company(client, target)
                            ctx.res_company_ids = [company_id]
                            # Step 3 (D14): scope every subsequent write this
                            # iteration makes to the resolved company.
                            client._default_context = {
                                "allowed_company_ids": [company_id], "company_id": company_id,
                            }
                            # Step 4 (D15): a brand-new company has no
                            # warehouse yet (R17 finding) — purchase.py/
                            # inventory.py/mrp.py would otherwise silently
                            # no-op for it. An existing company presumably
                            # already has one. Gated on at least one of
                            # those three modules actually being installed
                            # for this connection — otherwise this company
                            # never touches stock at all, and creating one
                            # would just be a wasted call plus an orphan
                            # empty warehouse.
                            if was_created and ctx.installed_modules & {"purchase", "stock", "mrp"}:
                                odoo_actions.create_second_warehouse(client, company_id)
                            # Step 5 (D8b): reuse this company's own existing
                            # partners/products, if the block asked for it —
                            # harmless no-op for a brand-new company (nothing
                            # exists yet to find).
                            if target.get("reuse_master_data"):
                                partner_ids, product_ids = fetch_existing_company_data(client, company_id)
                                ctx.company_ids.extend(partner_ids)
                                ctx.product_ids.extend(product_ids)
                            # Step 6 (D12): seed this iteration's analytic-
                            # accounts cache from the run-wide shared cache.
                            # None on the first company that ever needs it —
                            # get_or_create_analytic_accounts treats that
                            # exactly like "never attempted", same as today.
                            ctx.analytic_account_ids = shared_analytic_cache
                            # Step 7: run this company's full pipeline.
                            orchestrator.run(client, llm, ctx,
                                             on_module_start=on_start, on_module_done=on_done)
                            for key in ctx.skipped_modules:
                                qualified = f"{index}:{key}"
                                if record.modules.get(qualified) == MODULE_DONE:
                                    record.modules[qualified] = MODULE_SKIPPED
                                    self._publish(run_id, "module",
                                                  {"key": qualified, "status": MODULE_SKIPPED})
                        except Exception as exc:
                            failed_indices.add(index)
                            logger.warning(f"⚠️  Firma {index + 1} fehlgeschlagen: {exc}")
                        finally:
                            # Step 8 (D12), None-guarded: an exception before
                            # step 6 ever ran (e.g. target-company resolution
                            # itself failed) leaves ctx.analytic_account_ids
                            # at its dataclass default None — an unconditional
                            # harvest would overwrite a real cache from an
                            # earlier company with None, and the next company
                            # would create a duplicate "Kostenstellen" plan,
                            # exactly what D12 exists to prevent.
                            if ctx.analytic_account_ids is not None:
                                shared_analytic_cache = ctx.analytic_account_ids

                    if not failed_indices:
                        record.status = STATUS_DONE
                    elif len(failed_indices) == len(contexts_and_selected):
                        record.status = STATUS_FAILED
                        record.error = (
                            f"{len(failed_indices)} von {len(contexts_and_selected)} "
                            f"Firmen fehlgeschlagen.")
                    else:
                        record.status = STATUS_PARTIAL
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
                if targets is None:
                    for key, status in record.modules.items():
                        if status in (MODULE_PENDING, MODULE_RUNNING):
                            record.modules[key] = (MODULE_FAILED if record.status == STATUS_FAILED
                                                   else MODULE_DONE)
                else:
                    # S16: a company in failed_indices gets its never-run
                    # rows marked FAILED, not DONE — this is exactly the
                    # per-company tracking STATUS_PARTIAL needs; without it
                    # every pending row of a failed company would silently
                    # read "fertig" once the run as a whole isn't STATUS_FAILED.
                    for key, status in record.modules.items():
                        if status in (MODULE_PENDING, MODULE_RUNNING):
                            idx_str = key.split(":", 1)[0]
                            company_failed = idx_str.isdigit() and int(idx_str) in failed_indices
                            record.modules[key] = MODULE_FAILED if company_failed else MODULE_DONE

        self._publish(run_id, "status", record.public_dict())
        self._publish(run_id, "end", {"run_id": run_id, "status": record.status})
        stream = self.broker.get(run_id)
        if stream:
            stream.close()
