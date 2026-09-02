"""Unit tests for run_journal.py (D7) and the atomic seed-cache write.

D7 is an S9 prerequisite rather than a nice-to-have: a run takes 2–5 minutes, so
a container restart lands mid-pipeline in a live demo database with no resume and
no record of what was already written.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import llm_service
import run_journal
import requests


def run():
    results = []

    # ------------------------------------------------------------------
    # Journal records every create and survives a process restart
    # ------------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            journal = run_journal.RunJournal("demo-20260828-1200-001", Path(tmp))
            journal.set_target("https://demo-x.odoo.com")
            journal.record("res.partner", [11, 12])
            journal.record("product.product", [21])
            assert journal.path.exists(), "Journal nicht persistiert"

            reloaded = run_journal.RunJournal.load("demo-20260828-1200-001", Path(tmp))
            assert reloaded.entries == [("res.partner", 11), ("res.partner", 12),
                                        ("product.product", 21)], reloaded.entries
            payload = json.loads(journal.path.read_text(encoding="utf-8"))
            assert payload["target"] == "https://demo-x.odoo.com"
            # The journal names the host, never a credential.
            assert "api_key" not in json.dumps(payload).lower()
        results.append(("Journal: Einträge überleben einen Neustart", True, ""))
    except Exception as e:
        results.append(("Journal: Einträge überleben einen Neustart", False, str(e)))

    try:
        for bad in ("", "../etc/passwd", "run id", "a/b"):
            try:
                run_journal.RunJournal(bad)
                raise AssertionError(f"akzeptiert: {bad!r}")
            except ValueError:
                pass
        results.append(("Journal: Run-ID wird validiert (kein Pfad-Ausbruch)", True, ""))
    except Exception as e:
        results.append(("Journal: Run-ID wird validiert (kein Pfad-Ausbruch)", False, str(e)))

    # ------------------------------------------------------------------
    # JournalingClient records ids without double-counting the batch fallback
    # ------------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            journal = run_journal.RunJournal("demo-batch", Path(tmp))
            client = run_journal.JournalingClient("https://demo-x.odoo.com", "db", "key",
                                                  journal=journal)
            with patch.object(run_journal.OdooJson2Client, "create", return_value=5), \
                 patch.object(run_journal.OdooJson2Client, "create_batch", return_value=[7, 8]):
                client.create("res.partner", {"name": "A"})
                client.create_batch("res.partner", [{"name": "B"}, {"name": "C"}])
            assert journal.entries == [("res.partner", 5), ("res.partner", 7), ("res.partner", 8)], \
                journal.entries
        results.append(("JournalingClient: create und create_batch werden erfasst", True, ""))
    except Exception as e:
        results.append(("JournalingClient: create und create_batch werden erfasst", False, str(e)))

    try:
        # create_batch's own fallback path calls create() per record, which the
        # override already journals — the ids must not be recorded twice.
        with tempfile.TemporaryDirectory() as tmp:
            journal = run_journal.RunJournal("demo-fallback", Path(tmp))
            client = run_journal.JournalingClient("https://demo-x.odoo.com", "db", "key",
                                                  journal=journal)

            def _batch_via_create(self, model, values_list, context=None):
                return [client.create(model, v) for v in values_list]

            with patch.object(run_journal.OdooJson2Client, "create", side_effect=[31, 32]), \
                 patch.object(run_journal.OdooJson2Client, "create_batch", _batch_via_create):
                ids = client.create_batch("res.partner", [{"name": "A"}, {"name": "B"}])
            assert ids == [31, 32], ids
            assert journal.entries == [("res.partner", 31), ("res.partner", 32)], journal.entries
        results.append(("JournalingClient: Batch-Fallback zählt IDs nicht doppelt", True, ""))
    except Exception as e:
        results.append(("JournalingClient: Batch-Fallback zählt IDs nicht doppelt", False, str(e)))

    # ------------------------------------------------------------------
    # Cleanup unlinks newest-first — the inverse of the pipeline order
    # ------------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            journal = run_journal.RunJournal("demo-cleanup", Path(tmp))
            journal.record("res.partner", [1, 2])
            journal.record("sale.order", [10])
            journal.record("account.move", [20])
            client = MagicMock()
            client.errors = []
            summary = run_journal.delete_run(client, journal)
            # Only the unlink calls carry the ordering claim; account.move is
            # additionally reset to draft and cancelled first.
            unlinked = [c.args[0] for c in client.call_method.call_args_list if c.args[1] == "unlink"]
            assert unlinked == ["account.move", "sale.order", "res.partner"], unlinked
            pre = [(c.args[0], c.args[1]) for c in client.call_method.call_args_list
                   if c.args[1] != "unlink"]
            assert pre == [("account.move", "button_draft"), ("account.move", "button_cancel"),
                           ("sale.order", "action_cancel")], pre
            assert summary["deleted"] == 4 and summary["total"] == 4, summary
            assert summary["skipped"] == 0, summary
        results.append(("Cleanup: löscht in umgekehrter Pipeline-Reihenfolge", True, ""))
    except Exception as e:
        results.append(("Cleanup: löscht in umgekehrter Pipeline-Reihenfolge", False, str(e)))

    try:
        # A demo database routinely refuses to unlink some records (a posted
        # invoice, for one). The rest must still go.
        with tempfile.TemporaryDirectory() as tmp:
            journal = run_journal.RunJournal("demo-partial", Path(tmp))
            journal.record("res.partner", [1])
            journal.record("account.move", [20])
            client = MagicMock()
            client.errors = []

            def _call(model, method, ids=None, **kwargs):
                if method == "unlink" and model == "account.move":
                    raise RuntimeError("posted move")
                return True

            client.call_method.side_effect = _call
            summary = run_journal.delete_run(client, journal)
            assert summary["deleted"] == 1, summary
            assert len(summary["failed"]) == 1, summary
            assert summary["failed"][0]["model"] == "account.move", summary
        results.append(("Cleanup: einzelner Fehlschlag stoppt den Rest nicht", True, ""))
    except Exception as e:
        results.append(("Cleanup: einzelner Fehlschlag stoppt den Rest nicht", False, str(e)))

    try:
        # Wizard records are journalled but never unlinked: the API-key user is
        # not allowed to delete them and Odoo collects them itself, so counting
        # them as failures would be noise.
        with tempfile.TemporaryDirectory() as tmp:
            journal = run_journal.RunJournal("demo-skip", Path(tmp))
            journal.record("sale.advance.payment.inv", [90, 91])
            journal.record("res.partner", [1])
            client = MagicMock()
            summary = run_journal.delete_run(client, journal)
            models = [call.args[0] for call in client.call_method.call_args_list]
            assert models == ["res.partner"], models
            assert summary["skipped"] == 2 and summary["deleted"] == 1, summary
        results.append(("Cleanup: Wizard-Datensätze werden übersprungen, nicht gemeldet", True, ""))
    except Exception as e:
        results.append(("Cleanup: Wizard-Datensätze werden übersprungen, nicht gemeldet", False, str(e)))

    try:
        # Odoo refuses to unlink a confirmed order until it is cancelled. Simulates
        # two errors landing in client.errors between the mark and the read (e.g.
        # from two different failed calls in the same cleanup pass) to check that
        # _first_new_error picks the meaningful one, not a later placeholder.
        with tempfile.TemporaryDirectory() as tmp:
            journal = run_journal.RunJournal("demo-cancel", Path(tmp))
            journal.record("sale.order", [55])
            client = MagicMock()
            client.errors = []

            def _call(model, method, ids=None, **kwargs):
                if method == "unlink":
                    client.errors.append({"error_body": "You can not delete a confirmed sales order."})
                    client.errors.append({"error_body": "<Antwortkörper unterdrückt: 12 Zeichen, text/html>"})
                    raise RuntimeError("422 Client Error: Unprocessable Content")
                return True

            client.call_method.side_effect = _call
            summary = run_journal.delete_run(client, journal)
            methods = [call.args[1] for call in client.call_method.call_args_list]
            assert methods == ["action_cancel", "unlink"], methods
            assert "confirmed sales order" in summary["failed"][0]["error"], summary
        results.append(("Cleanup: storniert zuerst und meldet den echten Grund", True, ""))
    except Exception as e:
        results.append(("Cleanup: storniert zuerst und meldet den echten Grund", False, str(e)))

    try:
        # S10/R10 end-to-end regression: the two tests above check
        # _first_new_error against a hand-built errors list. This one drives a
        # REAL OdooJson2Client (call_method now makes exactly one request per
        # call — the payload-format fallback chain is gone), so it also proves
        # odoo_client._record_failure/_select_attempt put the real message
        # where _first_new_error expects to find it — not just that
        # _first_new_error can find it if it's there.
        class _FakeResponse:
            def __init__(self, status_code, text=""):
                self.status_code = status_code
                self.text = text
                self.headers = {"Content-Type": "application/json"}

            def raise_for_status(self):
                if self.status_code >= 400:
                    err = requests.HTTPError(f"{self.status_code}")
                    err.response = self
                    raise err

            def json(self):
                return {}

        def _fake_post(url, json=None, timeout=None, allow_redirects=None):
            if url.endswith("/unlink"):
                return _FakeResponse(422, text=(
                    '{"error": {"data": {"message": '
                    '"You can not delete a confirmed sales order"}}}'
                ))
            # action_cancel: fails too, for a different, unrelated reason —
            # delete_run's best-effort contract must swallow this one.
            return _FakeResponse(422, text=(
                '{"error": {"data": {"message": "nothing to cancel"}}}'
            ))

        with tempfile.TemporaryDirectory() as tmp:
            journal = run_journal.RunJournal("demo-e2e", Path(tmp))
            journal.record("sale.order", [66])
            client = run_journal.JournalingClient("https://demo-x.odoo.com", "db", "key",
                                                  journal=journal)
            client.session.post = _fake_post
            # action_cancel (CANCEL_BEFORE_UNLINK) also fails — delete_run
            # swallows that per its own "best effort" contract and proceeds to
            # unlink, whose failure is the one that must surface.
            summary = run_journal.delete_run(client, journal)
            assert summary["deleted"] == 0 and len(summary["failed"]) == 1, summary
            assert "confirmed sales order" in summary["failed"][0]["error"], summary
            assert "nothing to cancel" not in summary["failed"][0]["error"], summary
        results.append(("Cleanup (E2E, echter Client): meldet die informative Meldung, nicht das letzte 422", True, ""))
    except Exception as e:
        results.append(("Cleanup (E2E, echter Client): meldet die informative Meldung, nicht das letzte 422", False, str(e)))

    # ------------------------------------------------------------------
    # An unwritable cache directory degrades to "no caching", never aborts a run
    # ------------------------------------------------------------------
    try:
        service = llm_service.LLMService.__new__(llm_service.LLMService)
        unwritable = Path("/data/does-not-exist-on-this-host/cache")
        with patch.object(llm_service, "_CACHE_DIR", unwritable), \
             patch.object(llm_service, "_CACHE_WRITE_FAILED", False):
            # Must not raise: the run has already paid for this data.
            service._cache_save("slug", {"names": ["a"]})
            service._cache_save("slug2", {"names": ["b"]})
            assert llm_service._CACHE_WRITE_FAILED is True, "Fehler nicht vermerkt"
            assert llm_service.cache_dir_writable() is not None, "Sonde meldet keinen Fehler"
        results.append(("Cache: unbeschreibbares Verzeichnis bricht den Lauf nicht ab", True, ""))
    except Exception as e:
        results.append(("Cache: unbeschreibbares Verzeichnis bricht den Lauf nicht ab", False, str(e)))

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(llm_service, "_CACHE_DIR", Path(tmp)):
                assert llm_service.cache_dir_writable() is None
                assert not list(Path(tmp).glob(".write-probe")), "Sonde nicht aufgeräumt"
            with patch.dict(os.environ, {"ODOO_GENERATOR_RUNS_DIR": tmp}):
                assert run_journal.journal_dir_writable() is None
            with patch.dict(os.environ, {"ODOO_GENERATOR_RUNS_DIR": "/data/nope/runs"}):
                assert run_journal.journal_dir_writable() is not None
        results.append(("Startsonde: erkennt beschreibbar und nicht beschreibbar", True, ""))
    except Exception as e:
        results.append(("Startsonde: erkennt beschreibbar und nicht beschreibbar", False, str(e)))

    # ------------------------------------------------------------------
    # Retention — a journal names the target host, which is prospect-identifying
    # ------------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            old_file = Path(tmp) / "demo-old.json"
            new_file = Path(tmp) / "demo-new.json"
            old_file.write_text('{"run_id": "demo-old", "records": []}', encoding="utf-8")
            new_file.write_text('{"run_id": "demo-new", "records": []}', encoding="utf-8")
            ancient = time.time() - 30 * 86400
            os.utime(old_file, (ancient, ancient))

            removed = run_journal.prune_journals(Path(tmp), days=7)
            assert removed == 1, removed
            assert not old_file.exists(), "altes Journal nicht entfernt"
            assert new_file.exists(), "aktuelles Journal fälschlich entfernt"

            # 0 disables pruning entirely rather than deleting everything.
            ancient2 = time.time() - 30 * 86400
            os.utime(new_file, (ancient2, ancient2))
            assert run_journal.prune_journals(Path(tmp), days=0) == 0
            assert new_file.exists()
        results.append(("Aufbewahrung: alte Journale werden entfernt, neue nicht", True, ""))
    except Exception as e:
        results.append(("Aufbewahrung: alte Journale werden entfernt, neue nicht", False, str(e)))

    # ------------------------------------------------------------------
    # S11/D9 — *.log (run_journal.run_log_path) shares the SAME retention
    # pass as *.json, not a second, easy-to-forget one. It's the MORE
    # identifying file of the two (full target URL logged per request, not
    # just once) — a prune pass that missed it would defeat the point.
    # ------------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            old_log = run_journal.run_log_path("demo-old", Path(tmp))
            new_log = run_journal.run_log_path("demo-new", Path(tmp))
            old_log.write_text("old log line\n", encoding="utf-8")
            new_log.write_text("new log line\n", encoding="utf-8")
            ancient = time.time() - 30 * 86400
            os.utime(old_log, (ancient, ancient))

            removed = run_journal.prune_journals(Path(tmp), days=7)
            assert removed == 1, removed
            assert not old_log.exists(), "altes Lauf-Log nicht entfernt"
            assert new_log.exists(), "aktuelles Lauf-Log fälschlich entfernt"
        results.append(("Aufbewahrung: alte Lauf-Logs (*.log) werden mit entfernt, neue nicht", True, ""))
    except Exception as e:
        results.append(("Aufbewahrung: alte Lauf-Logs (*.log) werden mit entfernt, neue nicht", False, str(e)))

    try:
        assert run_journal.run_log_path("demo-abc").name == "demo-abc.log"
        raised = False
        try:
            run_journal.run_log_path("../../etc/passwd")
        except ValueError:
            raised = True
        assert raised, "run_log_path must validate run_id like RunJournal does"
        results.append(("run_log_path: normale ID -> <id>.log, ungültige ID -> ValueError", True, ""))
    except Exception as e:
        results.append(("run_log_path: normale ID -> <id>.log, ungültige ID -> ValueError", False, str(e)))

    try:
        with patch.dict(os.environ, {"ODOO_GENERATOR_LOG_RETENTION_DAYS": "3"}):
            assert run_journal.retention_days() == 3
        with patch.dict(os.environ, {"ODOO_GENERATOR_LOG_RETENTION_DAYS": "unsinn"}):
            assert run_journal.retention_days() == 7
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ODOO_GENERATOR_LOG_RETENTION_DAYS", None)
            assert run_journal.retention_days() == 7
        results.append(("Aufbewahrung: ODOO_GENERATOR_LOG_RETENTION_DAYS wird gelesen", True, ""))
    except Exception as e:
        results.append(("Aufbewahrung: ODOO_GENERATOR_LOG_RETENTION_DAYS wird gelesen", False, str(e)))

    # ------------------------------------------------------------------
    # Cache atomicity — concurrent runs share one cache directory
    # ------------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            service = llm_service.LLMService.__new__(llm_service.LLMService)
            payload_a = {"names": ["a" * 200] * 200}
            payload_b = {"names": ["b" * 200] * 200}
            errors = []

            def writer(payload, times):
                try:
                    for _ in range(times):
                        service._cache_save("shared_slug", payload)
                except Exception as exc:
                    errors.append(exc)

            def reader(times):
                try:
                    for _ in range(times):
                        loaded = service._cache_load("shared_slug")
                        if loaded is not None:
                            # A truncated file would raise in json.load above;
                            # reaching here means the read saw a complete file.
                            assert loaded in (payload_a, payload_b), "Teilinhalt gelesen"
                except Exception as exc:
                    errors.append(exc)

            with patch.object(llm_service, "_CACHE_DIR", Path(tmp)):
                threads = [threading.Thread(target=writer, args=(payload_a, 40)),
                           threading.Thread(target=writer, args=(payload_b, 40)),
                           threading.Thread(target=reader, args=(80,)),
                           threading.Thread(target=reader, args=(80,))]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=20)
                leftovers = [p.name for p in Path(tmp).glob("*.tmp")]

            assert not errors, f"{len(errors)} Fehler, erster: {errors[0]!r}"
            assert not leftovers, f"Temp-Dateien übrig: {leftovers}"
        results.append(("Cache: paralleles Schreiben kürzt die Datei nie", True, ""))
    except Exception as e:
        results.append(("Cache: paralleles Schreiben kürzt die Datei nie", False, str(e)))

    try:
        # The container profile runs a read-only rootfs; the package-relative
        # default would make every run die on its first cache write.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"ODOO_GENERATOR_CACHE_DIR": tmp}):
                import importlib
                reloaded = importlib.reload(llm_service)
                assert str(reloaded._CACHE_DIR) == tmp, reloaded._CACHE_DIR
        # restore the module-level default for anything importing it later
        import importlib
        importlib.reload(llm_service)
        results.append(("Cache-Verzeichnis ist per Umgebungsvariable setzbar", True, ""))
    except Exception as e:
        results.append(("Cache-Verzeichnis ist per Umgebungsvariable setzbar", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
