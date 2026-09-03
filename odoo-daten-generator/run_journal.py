"""Run markers and cleanup (D7).

Generated demo data is indistinguishable from real data, and "delete everything
the last run created" is the most-asked-for function on a demo system. A web run
also takes 2–5 minutes: a container restart lands mid-pipeline in a live demo
database with no resume, and without a journal there is no record of what got
that far.

Approach: a per-run journal file holding every ``(model, id)`` pair the run
created. Model-independent — no tag field, no ``x_`` column, nothing that has to
exist on every model — and the orchestrator writes it as a side effect of the
creates it already makes.

This is **not** the recording client the S9 draft dropped. That one intercepted
*reads* to fake a dry run, which collapsed into every module's Pattern-5 skip
path. This only observes writes that really happened.
"""
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from odoo_client import OdooJson2Client

logger = logging.getLogger(__name__)

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def default_journal_dir() -> Path:
    """Where run journals live. Env-overridable for the read-only-rootfs profile."""
    return Path(os.environ.get("ODOO_GENERATOR_RUNS_DIR")
                or (Path(__file__).parent / "seeds" / "runs"))


def run_log_path(run_id: str, directory: Optional[Path] = None) -> Path:
    """Where a run's full local log lives (S11/D9) — same directory and
    run_id-keyed naming as the journal, so one retention pass and one env
    override (ODOO_GENERATOR_RUNS_DIR) cover both. Never sent anywhere; a
    feedback issue carries only the run_id as a reference to look this up.
    """
    if not _RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"Ungültige Run-ID: {run_id!r}")
    return (Path(directory) if directory else default_journal_dir()) / f"{run_id}.log"


class RunJournal:
    """Append-only record of what a run created, persisted after every write.

    Persisted eagerly rather than at the end: the whole point is to survive a
    process that dies mid-pipeline.
    """

    def __init__(self, run_id: str, directory: Optional[Path] = None):
        if not _RUN_ID_RE.match(run_id or ""):
            raise ValueError(f"Ungültige Run-ID: {run_id!r}")
        self.run_id = run_id
        self.directory = Path(directory) if directory else default_journal_dir()
        self.entries: List[Tuple[str, int]] = []
        self._lock = threading.Lock()
        self._target: Optional[str] = None

    @property
    def path(self) -> Path:
        return self.directory / f"{self.run_id}.json"

    def set_target(self, target: str) -> None:
        """Record which instance the run wrote to (host only — never credentials)."""
        self._target = target

    def record(self, model: str, ids) -> None:
        if not ids:
            return
        with self._lock:
            for rec_id in ids:
                try:
                    self.entries.append((model, int(rec_id)))
                except (TypeError, ValueError):
                    continue
        self._persist()

    def _persist(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "run_id": self.run_id,
                "target": self._target,
                "records": [{"model": m, "id": i} for m, i in self.entries],
            }
            fd, tmp = tempfile.mkstemp(dir=str(self.directory),
                                       prefix=f".{self.run_id}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as exc:
            # Journalling must never take a run down with it.
            logger.warning(f"⚠️  Run-Journal konnte nicht geschrieben werden: {exc}")

    @classmethod
    def load(cls, run_id: str, directory: Optional[Path] = None) -> "RunJournal":
        journal = cls(run_id, directory)
        if journal.path.exists():
            with journal.path.open(encoding="utf-8") as f:
                data = json.load(f)
            journal._target = data.get("target")
            journal.entries = [(r["model"], int(r["id"])) for r in data.get("records", [])]
        return journal


def retention_days() -> int:
    """How long run journals are kept. 0 disables pruning."""
    try:
        return max(0, int(os.environ.get("ODOO_GENERATOR_LOG_RETENTION_DAYS", "") or 7))
    except ValueError:
        return 7


def journal_dir_writable() -> Optional[str]:
    """Probe the run-journal directory. Returns an error string, or None if fine.

    A silently unwritable journal is worse than a loud one: the run works, but
    nothing is recorded, so nothing can be cleaned up afterwards.
    """
    directory = default_journal_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return None
    except OSError as exc:
        return f"{directory}: {exc}"


def prune_journals(directory: Optional[Path] = None, days: Optional[int] = None) -> int:
    """Delete run journals AND run logs older than the retention window.
    Returns the count.

    A journal records the target host, and `demo-<prospect>.odoo.com` is
    prospect-identifying. Retention is the control for that — there is no code
    fix for a hostname that has to be in the file for cleanup to work — so the
    window has to actually be enforced rather than merely documented.

    *.log (S11/D9, run_journal.run_log_path) is strictly MORE identifying than
    *.json — odoo_client._post logs the full target URL on every request, not
    just once — so it shares this same pass rather than getting its own,
    easy-to-forget retention path.
    """
    directory = Path(directory) if directory else default_journal_dir()
    window = retention_days() if days is None else days
    if window <= 0 or not directory.exists():
        return 0
    cutoff = time.time() - window * 86400
    removed = 0
    for pattern in ("*.json", "*.log"):
        for path in directory.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning(f"Run-Datei {path.name} nicht löschbar: {exc}")
    if removed:
        logger.info(f"{removed} Run-Journal(e)/-Log(s) älter als {window} Tage entfernt.")
    return removed


class JournalingClient(OdooJson2Client):
    """OdooJson2Client that records every id it creates into a RunJournal.

    Subclass rather than a patch: journaling is a separate concern from how
    `create`/`create_batch` build their request, and overriding them here
    means this file never needs to know that detail.
    """

    def __init__(self, *args, journal: RunJournal, **kwargs):
        super().__init__(*args, **kwargs)
        self.journal = journal

    def create(self, model: str, values: Dict[str, Any],
               context: Optional[Dict[str, Any]] = None) -> int:
        rec_id = super().create(model, values, context=context)
        self.journal.record(model, [rec_id])
        return rec_id

    def create_batch(self, model: str, values_list: List[Dict[str, Any]],
                     context: Optional[Dict[str, Any]] = None) -> List[int]:
        # create_batch falls back to per-record create() on a batch rejection,
        # which journals through the override above; recording the returned ids
        # here as well would double-count, so filter to what is not yet known.
        ids = super().create_batch(model, values_list, context=context)
        known = set(self.journal.entries)
        fresh = [i for i in ids if (model, int(i)) not in known]
        self.journal.record(model, fresh)
        return ids


# Transient wizard records. They are created by the pipeline (the invoicing
# wizard, for one), but they are not demo data, the API-key user is not allowed
# to unlink them, and Odoo garbage-collects them itself. Journalled for
# completeness, skipped on cleanup so they do not show up as false failures.
SKIP_ON_CLEANUP = {"sale.advance.payment.inv"}

# Odoo refuses to unlink these until they leave their posted/confirmed state, and
# says so in the error ("You must first cancel it", "restricted audit trail").
# Best-effort: try the documented transition, then unlink. Every call is guarded —
# a refusal here just means the unlink below reports the original reason.
CANCEL_BEFORE_UNLINK = {
    "sale.order": ["action_cancel"],
    "purchase.order": ["button_cancel"],
    "account.move": ["button_draft", "button_cancel"],
    # hr.expense: approved/posted/paid records reject unlink outright ("Sie
    # können gebuchte oder genehmigte Spesen nicht löschen") — unlike the
    # other three models here, this isn't a single-record refusal, it's
    # fatal for the whole batch: delete_run unlinks one model group in one
    # call, so ONE approved expense in the group fails deletion for every
    # expense in it (draft/submitted included), which in turn leaves
    # hr.employee referenced and blocks employee cleanup too. Live-verified
    # (S12/WP5): action_reset takes approval_state back to draft, after
    # which unlink succeeds.
    "hr.expense": ["action_reset"],
}

# S13/Befund 2: models whose unlink routinely fails not because of a
# cancellable state (CANCEL_BEFORE_UNLINK above) but because Odoo still
# considers them "in use" — a stock.warehouse/stock.location referenced by
# a stock.quant (and quants themselves are never cleanable at all, see
# below). Both have an `active` field; on unlink failure delete_run tries
# `write(active=False)` as a soft fallback before giving up, and reports
# those separately as "archived", never as "deleted".
#
# Asymmetric in practice, live-verified (S13/WP1, demo-test5): archiving a
# stock.warehouse succeeds even with contained stock — `active` is a
# warehouse-level flag independent of its locations' own state. Archiving a
# stock.location that still holds a quant is REFUSED by Odoo too ("Sie
# können die Standorte ... nicht deaktivieren, da sie immer noch Produkte
# enthalten") — the same referential state that blocks its unlink blocks
# its archive. Since stock.quant is never actually removed by this function
# (no delete access for the API user, see ODOO_GOTCHAS.md), a
# S13-created stock.location that received any stock this run will end up
# in `failed`, not `archived`, in the common case — the archive fallback
# only helps a location this run happened to leave empty. Kept anyway (the
# attempt is cheap and correct for that case); the code must not claim a
# symmetric guarantee between warehouse and location here.
#
# stock.lot has no `active` field at all (live-verified) — deliberately
# absent from this set. A tracked lot with a live-referencing quant is
# permanent residue; no fix in this sprint (Befund 2).
ARCHIVE_FALLBACK_MODELS = {"stock.warehouse", "stock.location"}


def _first_new_error(client: OdooJson2Client, mark: int) -> Optional[str]:
    """The first error recorded since `mark` — already redacted by odoo_client.

    A cleanup pass can fail several calls before `mark` is read; each failed
    call contributes exactly one entry to `client.errors` (odoo_client.py sends
    one request per logical operation), but only the earliest of those is the
    one that actually explains why cleanup stopped — e.g. "You can not delete a
    confirmed sales order" rather than a later, unrelated 404/422.
    """
    for entry in client.errors[mark:]:
        body = entry.get("error_body")
        if body and not body.startswith("<"):
            return body[:300]
    return None


def delete_run(client: OdooJson2Client, journal: RunJournal) -> Dict[str, Any]:
    """Unlink everything a run created, newest first.

    Reverse order matters: it is the inverse of the pipeline, so dependants go
    before the records they depend on. It is still only an approximation of a
    true dependency order, and Odoo enforces referential integrity on top —
    a product referenced by a posted invoice line cannot go while that invoice
    stands. Failures are therefore collected per model rather than aborting: a
    demo database routinely holds records Odoo refuses to remove, and everything
    else should still go.
    """
    grouped: Dict[str, List[int]] = {}
    order: List[str] = []
    for model, rec_id in reversed(journal.entries):
        if model in SKIP_ON_CLEANUP:
            continue
        if model not in grouped:
            grouped[model] = []
            order.append(model)
        grouped[model].append(rec_id)

    deleted, archived, failed, skipped = 0, 0, [], 0
    skipped = sum(1 for model, _ in journal.entries if model in SKIP_ON_CLEANUP)

    for model in order:
        ids = grouped[model]
        for method in CANCEL_BEFORE_UNLINK.get(model, []):
            try:
                client.call_method(model, method, ids=ids)
            except Exception as exc:
                logger.info(f"{model}.{method} vor dem Löschen nicht möglich: {exc}")
        mark = len(client.errors)
        try:
            client.call_method(model, "unlink", ids=ids)
            deleted += len(ids)
        except Exception as exc:
            reason = _first_new_error(client, mark) or str(exc)[:200]
            if model in ARCHIVE_FALLBACK_MODELS:
                try:
                    client.write(model, ids, {"active": False})
                    archived += len(ids)
                    logger.info(f"ℹ️  {len(ids)}× {model} nicht löschbar ({reason}) — archiviert.")
                    continue
                except Exception as exc2:
                    reason = _first_new_error(client, mark) or str(exc2)[:200]
            failed.append({"model": model, "count": len(ids), "error": reason})
            logger.warning(f"⚠️  Löschen von {len(ids)}× {model} fehlgeschlagen: {reason}")
    return {"deleted": deleted, "archived": archived, "failed": failed,
            "skipped": skipped, "total": len(journal.entries)}
