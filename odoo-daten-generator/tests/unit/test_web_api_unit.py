"""Unit tests for the S9 web layer: auth, CSRF, session isolation, admission control.

No network: connect_service.probe and orchestrator.run are replaced by fakes, so
these exercise the HTTP surface and the queue, not Odoo.
"""
import os
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("ODOO_GENERATOR_ACCESS_CODE", "unit-test-code")
# S11/D9: web.jobs._execute now unconditionally opens a per-run log file
# (run_journal.run_log_path) before doing any other work — several tests
# below let a submitted run actually execute, which would otherwise write
# into the real repo's odoo-daten-generator/seeds/runs/ directory. setdefault
# so a test that locally patches this env var (run_journal_unit.py's own
# tests do) still wins; this only supplies a safe default.
os.environ.setdefault("ODOO_GENERATOR_RUNS_DIR", tempfile.mkdtemp(prefix="odoo_gen_test_runs_"))

from fastapi.testclient import TestClient

import connect_service
from web import app as web_app
from web.jobs import AdmissionRefused, JobQueue, STATUS_DONE, STATUS_FAILED, STATUS_PARTIAL
from web.session import Session
from web.sse import EventBroker

_HEADERS = {"X-Requested-With": "odoo-generator"}


def _fake_connect_result():
    result = connect_service.ConnectResult()
    result.ok = True
    result.steps = [connect_service.ProbeStep(key=k, label=l, ok=True, detail="OK")
                    for k, l in connect_service.STEP_LABELS]
    result.company_name = "Testfirma GmbH"
    result.language_code = "de_DE"
    result.language_name = "German"
    result.installed_modules = {"crm", "sale", "purchase", "stock"}
    result.feature_flags = {"crm_leads": True, "mrp_routings": True, "quality": False}
    result.model_access = {"crm.lead": True, "sale.order": True,
                           "purchase.order": True, "stock.quant": True}
    result.blocked_modules = set()
    result.odoo_version = "saas-19.4"
    result.existing_company_ids = [1, 2, 3]
    result.existing_product_ids = [10, 11]
    result.real_companies = [{"id": 1, "name": "Testfirma GmbH"}]
    result.llm_provider = "groq"
    result.llm_model = "llama-3.3-70b-versatile"
    return result


def _login(client):
    response = client.post("/api/auth", json={"access_code": "unit-test-code"}, headers=_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _auth_headers(csrf):
    headers = dict(_HEADERS)
    headers["X-CSRF-Token"] = csrf
    return headers


def _connect(client, csrf):
    with patch.object(connect_service, "probe",
                      return_value=(_fake_connect_result(), MagicMock(), MagicMock())):
        return client.post("/api/connect", headers=_auth_headers(csrf), json={
            "url": "https://demo-unit-test.odoo.com",
            "db": "demo-unit-test",
            "odoo_key": "key",
            "llm_key": "gsk_key",
            "llm_model": "llama-3.3-70b-versatile",
        })


def _fake_session(session_id="s1"):
    session = Session(id=session_id, csrf_token="t", created_at=time.time(), last_seen=time.time())
    session.base_url = "https://demo-unit-test.odoo.com"
    session.database = "demo-unit-test"
    session.odoo_key = "key"
    session.llm_key = "gsk_key"
    session.llm_model = "model"
    session.llm_provider = "groq"
    session.connect = _fake_connect_result()
    return session


_PAYLOAD = {
    "mode": "both",
    "industry": "IT",
    "use_existing": False,
    "skip_master_data": False,
    "master_data": {"num_companies": 1, "num_delivery_contacts": 0, "num_invoice_contacts": 0,
                    "num_other_contacts": 0, "num_services": 1, "num_consumables": 0,
                    "num_storables": 1},
    "modules": {"crm": {"enabled": True, "count": 2}},
}


def _company_block(target):
    return {
        "target": target,
        "mode": "both", "industry": "IT", "skip_master_data": False,
        "master_data": {"num_companies": 1, "num_delivery_contacts": 0, "num_invoice_contacts": 0,
                        "num_other_contacts": 0, "num_services": 1, "num_consumables": 0,
                        "num_storables": 1},
        "modules": {"crm": {"enabled": True, "count": 2}},
    }


# S16: two companies, one new (gets a warehouse, D15) one existing (doesn't).
_MULTI_PAYLOAD = {
    "companies": [
        _company_block({"mode": "new", "name": "Firma A", "country": "DE"}),
        _company_block({"mode": "existing", "company_id": 1}),
    ],
}

# S16/B1: the "companies" shape with exactly ONE company — this is what the
# frontend actually sends for a plain single-company run (it always uses the
# new shape, never the legacy top-level one below). Distinct from _PAYLOAD:
# that one has no "companies" key at all and exercises the OTHER bridge path.
_SINGLE_COMPANY_PAYLOAD = {
    "companies": [
        _company_block({"mode": "new", "name": "Solo GmbH", "country": "DE"}),
    ],
}


def _fake_orchestrator_run_stammdaten(client, llm, ctx, on_module_start=None, on_module_done=None):
    if on_module_start:
        on_module_start("Stammdaten")
    if on_module_done:
        on_module_done("Stammdaten", ok=True)


def run():
    results = []

    # ------------------------------------------------------------------
    # Auth: wrong code refused, right code issues a session + CSRF token
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            bad = client.post("/api/auth", json={"access_code": "wrong"}, headers=_HEADERS)
            assert bad.status_code == 401, bad.status_code
            good = client.post("/api/auth", json={"access_code": "unit-test-code"}, headers=_HEADERS)
            assert good.status_code == 200, good.text
            assert good.json().get("csrf_token"), good.text
            # No credential is ever echoed back.
            assert "access_code" not in good.text
        results.append(("Auth: falscher Code 401, richtiger Code liefert CSRF-Token", True, ""))
    except Exception as e:
        results.append(("Auth: falscher Code 401, richtiger Code liefert CSRF-Token", False, str(e)))

    # ------------------------------------------------------------------
    # CSRF: POST /api/runs without token or without the custom header is refused
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            _connect(client, csrf)

            no_token = client.post("/api/runs", headers=_HEADERS, json=_PAYLOAD)
            assert no_token.status_code == 403, no_token.status_code
            wrong_token = client.post("/api/runs", headers=_auth_headers("bogus"), json=_PAYLOAD)
            assert wrong_token.status_code == 403, wrong_token.status_code
            # A cross-origin <form> POST cannot set X-Requested-With without a
            # CORS preflight, so its absence must also be refused.
            no_header = client.post("/api/runs", headers={"X-CSRF-Token": csrf}, json=_PAYLOAD)
            assert no_header.status_code == 403, no_header.status_code
        results.append(("CSRF: POST ohne Token bzw. ohne Pflicht-Header abgelehnt", True, ""))
    except Exception as e:
        results.append(("CSRF: POST ohne Token bzw. ohne Pflicht-Header abgelehnt", False, str(e)))

    # ------------------------------------------------------------------
    # Unauthenticated access is refused everywhere
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            assert client.get("/api/session").status_code == 401
            assert client.post("/api/connect", headers=_HEADERS, json={}).status_code == 401
            assert client.post("/api/runs", headers=_HEADERS, json={}).status_code == 401
        results.append(("Auth: API ohne Sitzung durchgehend 401", True, ""))
    except Exception as e:
        results.append(("Auth: API ohne Sitzung durchgehend 401", False, str(e)))

    # ------------------------------------------------------------------
    # Guard A/B rejection reaches the API as 400, before any request goes out
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(connect_service, "probe") as probe:
                response = client.post("/api/connect", headers=_auth_headers(csrf), json={
                    "url": "https://kunde-produktiv.odoo.com", "db": "prod",
                    "odoo_key": "k", "llm_key": "k", "llm_model": "m",
                })
                assert response.status_code == 400, response.status_code
                assert "demo-" in response.json()["detail"]
                probe.assert_not_called()
        results.append(("Guard A: falsches Ziel wird abgelehnt, ohne zu verbinden", True, ""))
    except Exception as e:
        results.append(("Guard A: falsches Ziel wird abgelehnt, ohne zu verbinden", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 (F2): no "db" supplied and no server default configured ->
    # /api/connect derives it from the URL and passes THAT to probe().
    # ------------------------------------------------------------------
    try:
        import server_config
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            # Force the middle link of the chain to contribute nothing, so a
            # pass here can only mean the derivation itself ran.
            with patch.object(server_config, "defaults", return_value={}), \
                 patch.object(connect_service, "probe",
                              return_value=(_fake_connect_result(), MagicMock(), MagicMock())) as probe:
                response = client.post("/api/connect", headers=_auth_headers(csrf), json={
                    "url": "https://demo-ableitung-test.odoo.com",
                    "odoo_key": "k", "llm_key": "k", "llm_model": "m",
                })
                assert response.status_code == 200, response.text
                assert probe.call_args.kwargs["database"] == "demo-ableitung-test", probe.call_args.kwargs
        results.append(("Connect: fehlendes db wird aus der URL abgeleitet", True, ""))
    except Exception as e:
        results.append(("Connect: fehlendes db wird aus der URL abgeleitet", False, str(e)))

    # ------------------------------------------------------------------
    # A supplied "db" still wins over the derivation — the self-hoster escape
    # hatch WP4 explicitly keeps.
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(connect_service, "probe",
                              return_value=(_fake_connect_result(), MagicMock(), MagicMock())) as probe:
                response = client.post("/api/connect", headers=_auth_headers(csrf), json={
                    "url": "https://demo-ableitung-test.odoo.com", "db": "andere-db",
                    "odoo_key": "k", "llm_key": "k", "llm_model": "m",
                })
                assert response.status_code == 200, response.text
                assert probe.call_args.kwargs["database"] == "andere-db", probe.call_args.kwargs
        results.append(("Connect: übergebenes db gewinnt gegen die Ableitung", True, ""))
    except Exception as e:
        results.append(("Connect: übergebenes db gewinnt gegen die Ableitung", False, str(e)))

    # ------------------------------------------------------------------
    # /api/connect returns feature_flags — a missing one silently disables all
    # MRP work centers, BOM operations and quality points (B1 bug class)
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            response = _connect(client, csrf)
            assert response.status_code == 200, response.text
            data = response.json()
            assert "feature_flags" in data, sorted(data)
            assert data["feature_flags"]["mrp_routings"] is True, data["feature_flags"]
            assert "purchase" in data["installed_modules"], data["installed_modules"]
            assert "stock" in data["installed_modules"], data["installed_modules"]
            # S10/R10: model_access (raw per-model probe) and blocked_modules
            # (effective_installed_modules' decision) must both surface — the
            # frontend module grid renders blocked_modules directly rather
            # than re-deriving it from model_access.
            assert data["model_access"]["crm.lead"] is True, data["model_access"]
            assert data["blocked_modules"] == [], data["blocked_modules"]
            # No credential is ever returned.
            assert "gsk_key" not in response.text
        results.append(("Connect: feature_flags + purchase/stock im Ergebnis", True, ""))
    except Exception as e:
        results.append(("Connect: feature_flags + purchase/stock im Ergebnis", False, str(e)))

    # ------------------------------------------------------------------
    # S16/D8a: /api/connect returns real_companies (res.company id+name),
    # separate from the existing_companies count (res.partner-based)
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            response = _connect(client, csrf)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["real_companies"] == [{"id": 1, "name": "Testfirma GmbH"}], data["real_companies"]
        results.append(("Connect: real_companies (D8a) im Ergebnis", True, ""))
    except Exception as e:
        results.append(("Connect: real_companies (D8a) im Ergebnis", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 — a blocked module's model_access reaches build_context via
    # /api/preflight, exactly as feature_flags already does: the same
    # silent-disable class (B1) applies to write access, not just to
    # installed-module state.
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            blocked_result = _fake_connect_result()
            blocked_result.installed_modules = {"crm", "sale"}
            blocked_result.model_access = {"crm.lead": False, "sale.order": True}
            blocked_result.blocked_modules = {"crm"}
            with patch.object(connect_service, "probe",
                              return_value=(blocked_result, MagicMock(), MagicMock())):
                client.post("/api/connect", headers=_auth_headers(csrf), json={
                    "url": "https://demo-unit-test.odoo.com", "db": "demo-unit-test",
                    "odoo_key": "key", "llm_key": "gsk_key", "llm_model": "m",
                })
            response = client.post("/api/preflight", headers=_auth_headers(csrf), json={
                "mode": "both", "modules": {"crm": {"enabled": True, "count": 5}},
            })
            assert response.status_code == 200, response.text
            modules = [m["key"] for m in response.json()["modules"]]
            assert "crm" not in modules, \
                f"crm has no write access and must not appear as an active module: {modules}"
        results.append(("Preflight: Modul ohne Schreibrechte erscheint nicht als aktiv", True, ""))
    except Exception as e:
        results.append(("Preflight: Modul ohne Schreibrechte erscheint nicht als aktiv", False, str(e)))

    # ------------------------------------------------------------------
    # S16: /api/preflight with a "companies" payload returns qualified
    # module keys + labels, merges the record estimate across companies
    # (Kostenstellen kept once, everything else summed), and reports
    # company_count — the same shape JobQueue.submit() builds internally.
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            _connect(client, csrf)
            response = client.post("/api/preflight", headers=_auth_headers(csrf), json=_MULTI_PAYLOAD)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["company_count"] == 2, data
            keys = [m["key"] for m in data["modules"]]
            assert any(k == "0:stammdaten" for k in keys), keys
            assert any(k == "1:stammdaten" for k in keys), keys
            labels_by_key = {m["key"]: m["label"] for m in data["modules"]}
            assert labels_by_key["0:stammdaten"] == "Stammdaten", labels_by_key
            assert "Kontakte (Firma A)" in data["record_estimate"] or \
                   any(k.startswith("Kontakte (") for k in data["record_estimate"]), data["record_estimate"]
        results.append(("Preflight: companies-Payload liefert firmen-qualifizierte Keys+Labels", True, ""))
    except AssertionError as e:
        results.append(("Preflight: companies-Payload liefert firmen-qualifizierte Keys+Labels", False, str(e)))

    # ------------------------------------------------------------------
    # Session isolation: one session cannot read another session's run
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client_a, TestClient(web_app.app) as client_b:
            csrf_a = _login(client_a)
            _connect(client_a, csrf_a)
            with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
                 patch("orchestrator.run"):
                created = client_a.post("/api/runs", headers=_auth_headers(csrf_a), json=_PAYLOAD)
                assert created.status_code == 202, created.text
                run_id = created.json()["run_id"]
                time.sleep(0.4)
            assert client_a.get(f"/api/runs/{run_id}").status_code == 200
            _login(client_b)
            foreign = client_b.get(f"/api/runs/{run_id}")
            assert foreign.status_code == 404, foreign.status_code
            events = client_b.get(f"/api/runs/{run_id}/events")
            assert events.status_code == 404, events.status_code
        results.append(("Session-Isolation: fremder Lauf ist nicht lesbar", True, ""))
    except Exception as e:
        results.append(("Session-Isolation: fremder Lauf ist nicht lesbar", False, str(e)))

    # ------------------------------------------------------------------
    # S11/D9 — a real run writes its own log to local disk (run_journal.
    # run_log_path), independent of the SSE stream. orchestrator.run's fake
    # emits one real log line through the SAME logger module code logs
    # through, so this proves the handler is actually wired and filtered to
    # this run — not just that a file happened to get created. Uses JobQueue
    # directly (not TestClient) and polls for STATUS_DONE, matching this
    # file's own established pattern for reliably waiting on a background run
    # (see the api_errors-redaction test below) rather than a flat sleep.
    # ------------------------------------------------------------------
    try:
        import logging as _logging
        import run_journal as _run_journal

        def _fake_orchestrator_run(client, llm, ctx, on_module_start=None, on_module_done=None):
            _logging.getLogger("modules.fake_for_test").info("S11/D9 marker line")

        broker = EventBroker()
        queue_obj = JobQueue(broker, workers=1)
        with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
             patch("orchestrator.run", side_effect=_fake_orchestrator_run):
            queue_obj.start()
            record = queue_obj.submit(session=_fake_session("s-runlog"), payload=_PAYLOAD)
            deadline = time.time() + 5
            while record.status != STATUS_DONE and time.time() < deadline:
                time.sleep(0.05)
            queue_obj.stop()
        assert record.status == STATUS_DONE, f"run never finished: {record.status}"
        log_path = _run_journal.run_log_path(record.run_id)
        assert log_path.exists(), f"expected a local run log at {log_path}"
        content = log_path.read_text(encoding="utf-8")
        assert "S11/D9 marker line" in content, f"run log missing expected content: {content!r}"
        results.append(("Lauf-Log: eigenes Log lokal geschrieben, unabhängig vom SSE-Stream", True, ""))
    except Exception as e:
        results.append(("Lauf-Log: eigenes Log lokal geschrieben, unabhängig vom SSE-Stream", False, str(e)))

    # ------------------------------------------------------------------
    # S11/D9 — best-effort: an unwritable ODOO_GENERATOR_RUNS_DIR must not
    # crash run creation (same rule as RunJournal._persist, "journalling must
    # never take a run down with it"). Point it at a path whose PARENT is a
    # plain file, so mkdir(parents=True) fails with ENOTDIR/ENOENT.
    # ------------------------------------------------------------------
    try:
        from web.jobs import _open_run_log_handler
        with tempfile.TemporaryDirectory() as tmp:
            blocker_file = os.path.join(tmp, "not-a-directory")
            with open(blocker_file, "w", encoding="utf-8") as f:
                f.write("x")
            bogus_runs_dir = os.path.join(blocker_file, "runs")  # parent is a FILE
            with patch.dict(os.environ, {"ODOO_GENERATOR_RUNS_DIR": bogus_runs_dir}):
                handler = _open_run_log_handler("does-not-matter")
                assert handler is None, f"expected None on an unwritable dir, got {handler!r}"
        results.append(("Lauf-Log: nicht beschreibbares Verzeichnis -> None, kein Crash", True, ""))
    except Exception as e:
        results.append(("Lauf-Log: nicht beschreibbares Verzeichnis -> None, kein Crash", False, str(e)))

    # ------------------------------------------------------------------
    # Admission control: a fixed worker pool queues instead of spawning, and a
    # single session cannot fill every slot
    # ------------------------------------------------------------------
    try:
        broker = EventBroker()
        queue_obj = JobQueue(broker, workers=2)
        gate = threading.Event()
        peak = {"current": 0, "max": 0}
        lock = threading.Lock()

        def _blocking_run(*_a, **_kw):
            with lock:
                peak["current"] += 1
                peak["max"] = max(peak["max"], peak["current"])
            gate.wait(timeout=5)
            with lock:
                peak["current"] -= 1

        with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
             patch("orchestrator.run", side_effect=_blocking_run), \
             patch("web.jobs.per_session_limit", return_value=10):
            queue_obj.start()
            sessions = [_fake_session(f"s{i}") for i in range(5)]
            for session in sessions:
                queue_obj.submit(session=session, payload=_PAYLOAD)
            time.sleep(0.6)
            running_now = peak["max"]
            gate.set()
            time.sleep(0.8)
            queue_obj.stop()

        assert running_now <= 2, f"{running_now} gleichzeitige Läufe bei 2 Workern"
        assert running_now == 2, f"Pool nicht ausgelastet: {running_now}"
        results.append(("Admission: 5 Anfragen, 2 Worker -> max. 2 gleichzeitig", True, f"max {running_now}"))
    except Exception as e:
        results.append(("Admission: 5 Anfragen, 2 Worker -> max. 2 gleichzeitig", False, str(e)))

    try:
        broker = EventBroker()
        queue_obj = JobQueue(broker, workers=1)
        gate = threading.Event()

        with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
             patch("orchestrator.run", side_effect=lambda *a, **k: gate.wait(timeout=5)), \
             patch("web.jobs.per_session_limit", return_value=2):
            queue_obj.start()
            session = _fake_session("s-limit")
            queue_obj.submit(session=session, payload=_PAYLOAD)
            queue_obj.submit(session=session, payload=_PAYLOAD)
            refused = False
            try:
                queue_obj.submit(session=session, payload=_PAYLOAD)
            except AdmissionRefused:
                refused = True
            gate.set()
            time.sleep(0.5)
            queue_obj.stop()
        assert refused, "dritter Lauf derselben Sitzung wurde nicht abgewiesen"
        results.append(("Admission: Sitzungslimit greift beim dritten Lauf", True, ""))
    except Exception as e:
        results.append(("Admission: Sitzungslimit greift beim dritten Lauf", False, str(e)))

    # ------------------------------------------------------------------
    # Error redaction survives the API boundary: GET /api/runs/{id} carries the
    # redacted body, never the raw one (§8.4 surfaces client.errors)
    # ------------------------------------------------------------------
    try:
        broker = EventBroker()
        queue_obj = JobQueue(broker, workers=1)
        leaked = "<html>INTERNAL-SECRET</html>"

        fake_client = MagicMock()
        fake_client.get_errors.return_value = [{
            "url": "https://demo-unit-test.odoo.com/json/2/res.partner/create",
            "status_code": 500, "error_message": "500 Server Error",
            "error_body": "<Antwortkörper unterdrückt: 26 Zeichen, text/html>",
            "payload_keys": ["vals_list"],
        }]

        with patch("web.jobs.JournalingClient", return_value=fake_client), \
             patch("web.jobs.LLMService"), patch("orchestrator.run"), \
             patch("web.jobs.per_session_limit", return_value=5):
            queue_obj.start()
            record = queue_obj.submit(session=_fake_session("s-err"), payload=_PAYLOAD)
            deadline = time.time() + 5
            while record.status != STATUS_DONE and time.time() < deadline:
                time.sleep(0.05)
            queue_obj.stop()

        public = record.public_dict()
        assert public["api_errors"], public
        assert leaked not in str(public), "roher Antwortkörper über die API sichtbar"
        assert "unterdrückt" in public["api_errors"][0]["error_body"]
        results.append(("Redaktion: Lauf-Zusammenfassung führt keinen rohen Körper", True, ""))
    except Exception as e:
        results.append(("Redaktion: Lauf-Zusammenfassung führt keinen rohen Körper", False, str(e)))

    # ------------------------------------------------------------------
    # Expiry actually happens: credentials are dropped by a sweep, not merely
    # on the next touch of the session
    # ------------------------------------------------------------------
    try:
        from web.session import SessionStore
        store = SessionStore(ttl_seconds=0)
        session = store.create()
        session.odoo_key = "secret-odoo"
        session.llm_key = "secret-llm"
        assert store.count() == 1
        time.sleep(0.01)
        assert store.sweep() == 1, "abgelaufene Sitzung nicht weggeräumt"
        assert store.count() == 0
        assert session.odoo_key is None and session.llm_key is None, \
            "Zugangsdaten nach dem Wegräumen noch im Objekt"
        results.append(("Sitzungen: sweep() entfernt Sitzung und löscht Zugangsdaten", True, ""))
    except Exception as e:
        results.append(("Sitzungen: sweep() entfernt Sitzung und löscht Zugangsdaten", False, str(e)))

    try:
        # Finished runs and their event streams must be released too, or both
        # dicts grow for the life of the process.
        broker = EventBroker()
        queue_obj = JobQueue(broker, workers=1)
        with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
             patch("orchestrator.run"), patch("web.jobs.per_session_limit", return_value=5):
            queue_obj.start()
            record = queue_obj.submit(session=_fake_session("s-prune"), payload=_PAYLOAD)
            deadline = time.time() + 5
            while record.status != STATUS_DONE and time.time() < deadline:
                time.sleep(0.05)
            queue_obj.stop()
        assert broker.get(record.run_id) is not None, "Stream vor dem Aufräumen weg"
        assert queue_obj.prune(max_age_seconds=0) == 1
        assert queue_obj.get(record.run_id) is None, "Lauf-Datensatz nicht entfernt"
        assert broker.get(record.run_id) is None, "Ereignisstrom nicht entfernt"
        results.append(("Läufe: prune() gibt Datensatz und Ereignisstrom frei", True, ""))
    except Exception as e:
        results.append(("Läufe: prune() gibt Datensatz und Ereignisstrom frei", False, str(e)))

    try:
        # The browser forces use_existing on when skip_master_data is ticked; the
        # API must not accept the combination that skips every module silently.
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            _connect(client, csrf)
            payload = dict(_PAYLOAD, skip_master_data=True, use_existing=False)
            response = client.post("/api/runs", headers=_auth_headers(csrf), json=payload)
            assert response.status_code == 400, response.status_code
            # use_existing implies the consent question, so the accepted case
            # must answer it too.
            ok_payload = dict(_PAYLOAD, skip_master_data=True, use_existing=True,
                              existing_data_consent="granted")
            with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
                 patch("orchestrator.run"):
                allowed = client.post("/api/runs", headers=_auth_headers(csrf), json=ok_payload)
                assert allowed.status_code == 202, allowed.text
                time.sleep(0.3)
        results.append(("Lauf: skip_master_data ohne use_existing wird abgelehnt", True, ""))
    except Exception as e:
        results.append(("Lauf: skip_master_data ohne use_existing wird abgelehnt", False, str(e)))

    try:
        # S16/B4 (pre-merge cold review): the top-level check above reads
        # keys that don't exist in the "companies" shape (skip_master_data/
        # use_existing live per block there) — it must not silently pass a
        # multi-company payload with the same empty-pool combination.
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            _connect(client, csrf)
            bad_block = _company_block({"mode": "new", "name": "Firma A", "country": "DE"})
            bad_block["skip_master_data"] = True
            response = client.post("/api/runs", headers=_auth_headers(csrf),
                                   json={"companies": [bad_block]})
            assert response.status_code == 400, response.status_code
            assert "companies[0]" in response.json()["detail"], response.text

            # Same skip_master_data=True is fine when the target is an
            # existing company that also asks to reuse its own data.
            good_block = _company_block({"mode": "existing", "company_id": 1,
                                         "reuse_master_data": True})
            good_block["skip_master_data"] = True
            with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
                 patch("orchestrator.run"), \
                 patch("web.jobs.odoo_actions.resolve_target_company", return_value=(1, False)):
                allowed = client.post("/api/runs", headers=_auth_headers(csrf),
                                      json={"existing_data_consent": "granted",
                                            "companies": [good_block]})
                assert allowed.status_code == 202, allowed.text
                time.sleep(0.3)
        results.append(("Lauf (S16/B4): skip_master_data pro Firma ohne reuse_master_data wird abgelehnt", True, ""))
    except Exception as e:
        results.append(("Lauf (S16/B4): skip_master_data pro Firma ohne reuse_master_data wird abgelehnt", False, str(e)))

    # ------------------------------------------------------------------
    # Consent is enforced by the API, not only by the browser
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            _connect(client, csrf)
            undecided = dict(_PAYLOAD, use_existing=True)
            response = client.post("/api/runs", headers=_auth_headers(csrf), json=undecided)
            assert response.status_code == 400, response.status_code
            assert "Zustimmung" in response.json()["detail"], response.text

            denied = dict(_PAYLOAD, use_existing=True, existing_data_consent="denied")
            assert client.post("/api/runs", headers=_auth_headers(csrf),
                               json=denied).status_code == 400

            granted = dict(_PAYLOAD, use_existing=True, existing_data_consent="granted")
            with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
                 patch("orchestrator.run"):
                allowed = client.post("/api/runs", headers=_auth_headers(csrf), json=granted)
                assert allowed.status_code == 202, allowed.text
                time.sleep(0.3)
        results.append(("Einwilligung: API verlangt sie unabhängig vom Browser", True, ""))
    except Exception as e:
        results.append(("Einwilligung: API verlangt sie unabhängig vom Browser", False, str(e)))

    # ------------------------------------------------------------------
    # BETA operator defaults: blank fields fall back to config.ini, and the
    # metadata endpoint never carries a key
    # ------------------------------------------------------------------
    try:
        import server_config
        fake = {"url": "https://demo-operator.odoo.com", "db": "demo-operator",
                "odoo_key": "operator-odoo-secret", "llm_key": "gsk_operator-llm-secret",
                "llm_model": "qwen/qwen3.8-27b"}
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(server_config, "defaults", return_value=fake), \
                 patch.object(connect_service, "probe",
                              return_value=(_fake_connect_result(), MagicMock(), MagicMock())) as probe:
                # Every field blank — the server fills them in.
                response = client.post("/api/connect", headers=_auth_headers(csrf), json={
                    "url": "", "db": "", "odoo_key": "", "llm_key": "", "llm_model": "",
                })
                assert response.status_code == 200, response.text
                kwargs = probe.call_args.kwargs
                assert kwargs["base_url"] == "https://demo-operator.odoo.com", kwargs
                assert kwargs["database"] == "demo-operator", kwargs
                assert kwargs["odoo_key"] == "operator-odoo-secret", kwargs
                assert kwargs["llm_key"] == "gsk_operator-llm-secret", kwargs
                # No secret is ever echoed back.
                assert "operator-odoo-secret" not in response.text
                assert "operator-llm-secret" not in response.text
        results.append(("Beta-Defaults: leere Felder werden serverseitig gefüllt", True, ""))
    except Exception as e:
        results.append(("Beta-Defaults: leere Felder werden serverseitig gefüllt", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(server_config, "defaults", return_value=fake), \
                 patch.object(connect_service, "probe",
                              return_value=(_fake_connect_result(), MagicMock(), MagicMock())) as probe:
                # A supplied value wins over the default.
                client.post("/api/connect", headers=_auth_headers(csrf), json={
                    "url": "https://demo-eigene.odoo.com", "db": "demo-eigene",
                    "odoo_key": "meine-eigene", "llm_key": "gsk_meine", "llm_model": "eigenes-modell",
                })
                kwargs = probe.call_args.kwargs
                assert kwargs["base_url"] == "https://demo-eigene.odoo.com", kwargs
                assert kwargs["odoo_key"] == "meine-eigene", kwargs
                assert kwargs["llm_model"] == "eigenes-modell", kwargs
        results.append(("Beta-Defaults: eigene Eingabe überschreibt die Voreinstellung", True, ""))
    except Exception as e:
        results.append(("Beta-Defaults: eigene Eingabe überschreibt die Voreinstellung", False, str(e)))

    try:
        # Guard A still applies to a configured default — the fallback widens who
        # may use the key, never which hosts it may reach.
        bad = dict(fake, url="https://kunde-produktiv.odoo.com")
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(server_config, "defaults", return_value=bad), \
                 patch.object(connect_service, "probe") as probe:
                response = client.post("/api/connect", headers=_auth_headers(csrf), json={})
                assert response.status_code == 400, response.status_code
                probe.assert_not_called()
        results.append(("Beta-Defaults: Guard A gilt auch für die Server-URL", True, ""))
    except Exception as e:
        results.append(("Beta-Defaults: Guard A gilt auch für die Server-URL", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(server_config, "defaults", return_value=fake):
                response = client.get("/api/defaults")
                assert response.status_code == 200, response.text
                data = response.json()
                assert data["has_odoo_key"] is True and data["has_llm_key"] is True, data
                assert data["url"] == fake["url"], data
                assert "operator-odoo-secret" not in response.text, "Schlüssel im Ergebnis!"
                assert "operator-llm-secret" not in response.text, "Schlüssel im Ergebnis!"
                assert "odoo_key" not in data and "llm_key" not in data, sorted(data)
                # S10/R10 (F2): "db" is no longer part of this endpoint's surface —
                # the frontend derives it from the URL instead of pre-filling a field.
                assert "db" not in data, sorted(data)
        # Unauthenticated callers get nothing — the demo hostname it carries is
        # prospect-identifying.
        with TestClient(web_app.app) as anon:
            assert anon.get("/api/defaults").status_code == 401
        results.append(("Beta-Defaults: /api/defaults meldet nur ob, nie welcher Schlüssel", True, ""))
    except Exception as e:
        results.append(("Beta-Defaults: /api/defaults meldet nur ob, nie welcher Schlüssel", False, str(e)))

    try:
        # The kill switch restores bring-your-own-credentials behaviour.
        with patch.dict(os.environ, {"ODOO_GENERATOR_CONFIG_DEFAULTS": "off"}):
            assert server_config.enabled() is False
            assert server_config.defaults() == {}
            assert server_config.apply("odoo_key", "") is None
            assert server_config.public_defaults()["has_odoo_key"] is False
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.dict(os.environ, {"ODOO_GENERATOR_CONFIG_DEFAULTS": "off"}), \
                 patch.object(connect_service, "probe") as probe:
                response = client.post("/api/connect", headers=_auth_headers(csrf), json={
                    "url": "https://demo-x.odoo.com", "db": "demo-x",
                    "odoo_key": "", "llm_key": "", "llm_model": "",
                })
                assert response.status_code == 400, response.status_code
                probe.assert_not_called()
        results.append(("Beta-Defaults: ODOO_GENERATOR_CONFIG_DEFAULTS=off schaltet ab", True, ""))
    except Exception as e:
        results.append(("Beta-Defaults: ODOO_GENERATOR_CONFIG_DEFAULTS=off schaltet ab", False, str(e)))

    # ------------------------------------------------------------------
    # The Secure flag follows the transport, not the configuration. Behind a TLS
    # tunnel with .env still saying "local", a config-only answer would ship the
    # session cookie unprotected over a connection the browser calls https.
    # ------------------------------------------------------------------
    try:
        from unittest.mock import Mock

        def _req(scheme):
            r = Mock()
            r.url = Mock(scheme=scheme)
            return r

        with patch.dict(os.environ, {"ODOO_GENERATOR_PROFILE": "local"}):
            os.environ.pop("ODOO_GENERATOR_COOKIE_SECURE", None)
            assert web_app.cookie_secure(_req("https")) is True, "https ohne Secure"
            assert web_app.cookie_secure(_req("http")) is False, "http mit Secure"
            assert web_app.cookie_secure(None) is False
        with patch.dict(os.environ, {"ODOO_GENERATOR_PROFILE": "server"}):
            os.environ.pop("ODOO_GENERATOR_COOKIE_SECURE", None)
            assert web_app.cookie_secure(_req("http")) is True, "server-Profil ignoriert"
        # The setting may turn it on, never off.
        with patch.dict(os.environ, {"ODOO_GENERATOR_COOKIE_SECURE": "false"}):
            assert web_app.cookie_secure(_req("https")) is True, "Konfiguration hebelt https aus"
        results.append(("Cookie: Secure folgt dem Transport, Konfiguration nur additiv", True, ""))
    except Exception as e:
        results.append(("Cookie: Secure folgt dem Transport, Konfiguration nur additiv", False, str(e)))

    try:
        # Real round-trip through the app, both schemes.
        with TestClient(web_app.app, base_url="https://testserver") as client:
            r = client.post("/api/auth", json={"access_code": "unit-test-code"}, headers=_HEADERS)
            assert "secure" in r.headers.get("set-cookie", "").lower(), r.headers.get("set-cookie")
        with TestClient(web_app.app, base_url="http://testserver") as client:
            r = client.post("/api/auth", json={"access_code": "unit-test-code"}, headers=_HEADERS)
            assert "secure" not in r.headers.get("set-cookie", "").lower(), r.headers.get("set-cookie")
        results.append(("Cookie: https setzt Secure, http nicht (echter Durchlauf)", True, ""))
    except Exception as e:
        results.append(("Cookie: https setzt Secure, http nicht (echter Durchlauf)", False, str(e)))

    # ------------------------------------------------------------------
    # Security headers: CSP with no inline scripts, plus the usual set
    # ------------------------------------------------------------------
    try:
        with TestClient(web_app.app) as client:
            response = client.get("/static/app.js")
            csp = response.headers.get("Content-Security-Policy", "")
            assert "default-src 'self'" in csp, csp
            assert "unsafe-inline" not in csp, csp
            assert response.headers.get("X-Content-Type-Options") == "nosniff"
            assert response.headers.get("X-Frame-Options") == "DENY"
        results.append(("CSP: default-src 'self', kein unsafe-inline", True, ""))
    except Exception as e:
        results.append(("CSP: default-src 'self', kein unsafe-inline", False, str(e)))

    # ------------------------------------------------------------------
    # S16/D8b+S1: fetch_existing_company_data filters by company_id OR
    # company-neutral (company_id=False) — NOT is_company/customer_rank like
    # fetch_existing_data (Firma-1-shaped), since a prior run's
    # master_data.py write (D8-Ergänzung) sets company_id on contacts too,
    # none of which carry customer_rank>0/is_company=True. S1 (pre-merge
    # cold review): a strict company_id=X domain missed almost all real
    # data — company-neutral records are shared across every company.
    # ------------------------------------------------------------------
    try:
        mock_client = MagicMock()
        mock_client.search_read.side_effect = [
            [{"id": 501}, {"id": 502}],  # res.partner
            [{"id": 601}],               # product.product
        ]
        partner_ids, product_ids = connect_service.fetch_existing_company_data(mock_client, 7)
        assert partner_ids == [501, 502], partner_ids
        assert product_ids == [601], product_ids
        expected_domain = ['|', ["company_id", "=", False], ["company_id", "=", 7]]
        partner_call, product_call = mock_client.search_read.call_args_list
        assert partner_call.args[0] == 'res.partner', partner_call
        assert partner_call.args[1] == expected_domain, partner_call
        assert product_call.args[0] == 'product.product', product_call
        assert product_call.args[1] == expected_domain, product_call
        results.append(("fetch_existing_company_data: filters both models by company_id or company-neutral", True, ""))
    except AssertionError as e:
        results.append(("fetch_existing_company_data: filters both models by company_id or company-neutral", False, str(e)))

    # ==================================================================
    # S16 — multi-company execution loop in web/jobs.py
    # ==================================================================

    def _run_multi(payload, resolve_side_effect, orchestrator_side_effect=_fake_orchestrator_run_stammdaten):
        broker = EventBroker()
        queue_obj = JobQueue(broker, workers=1)
        with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
             patch("orchestrator.run", side_effect=orchestrator_side_effect), \
             patch("web.jobs.odoo_actions.resolve_target_company", side_effect=resolve_side_effect) as mock_resolve, \
             patch("web.jobs.odoo_actions.create_second_warehouse") as mock_wh:
            queue_obj.start()
            record = queue_obj.submit(session=_fake_session("s-multi"), payload=payload)
            deadline = time.time() + 5
            while record.status not in (STATUS_DONE, STATUS_FAILED, STATUS_PARTIAL) and time.time() < deadline:
                time.sleep(0.05)
            queue_obj.stop()
        return record, mock_resolve, mock_wh

    try:
        record, mock_resolve, mock_wh = _run_multi(
            _MULTI_PAYLOAD, resolve_side_effect=[(101, True), (1, False)])
        assert record.status == STATUS_DONE, record.status
        assert any(k == "0:stammdaten" for k in record.modules), record.modules
        assert any(k == "1:stammdaten" for k in record.modules), record.modules
        assert record.modules["0:stammdaten"] == "done", record.modules
        assert record.modules["1:stammdaten"] == "done", record.modules
        results.append(("multi-company: 2 companies succeed -> STATUS_DONE, both qualified rows done", True, ""))
    except AssertionError as e:
        results.append(("multi-company: 2 companies succeed -> STATUS_DONE, both qualified rows done", False, str(e)))

    try:
        # S16/B1 (pre-merge cold review): a genuine ONE-company "companies"
        # payload must still get QUALIFIED keys ("0:stammdaten") — the bug
        # was multi_company_preview() qualifying keys only when count > 1,
        # while _execute() qualifies whenever `targets is not None`
        # (count-independent). Before the fix, record.modules held the
        # UNqualified key while the run published/looked up the qualified
        # one, so the module's status was never actually written — and the
        # finally-block reconciliation's `key.split(":", 1)[0].isdigit()`
        # check then forced it to "done" regardless of the real outcome.
        record, mock_resolve, mock_wh = _run_multi(
            _SINGLE_COMPANY_PAYLOAD, resolve_side_effect=[(201, True)])
        assert record.status == STATUS_DONE, record.status
        assert "0:stammdaten" in record.modules, record.modules
        assert "stammdaten" not in record.modules, record.modules
        assert record.modules["0:stammdaten"] == "done", record.modules
        results.append(("multi-company (B1): single-company \"companies\" payload gets qualified keys", True, ""))
    except AssertionError as e:
        results.append(("multi-company (B1): single-company \"companies\" payload gets qualified keys", False, str(e)))

    try:
        # Same shape, but the single company's own pipeline actually fails —
        # this is the exact case B1 mis-reported as "done".
        def _fake_orchestrator_run_fails(client, llm, ctx, on_module_start=None, on_module_done=None):
            if on_module_start:
                on_module_start("Stammdaten")
            raise RuntimeError("pipeline blew up")

        record, mock_resolve, mock_wh = _run_multi(
            _SINGLE_COMPANY_PAYLOAD, resolve_side_effect=[(201, True)],
            orchestrator_side_effect=_fake_orchestrator_run_fails)
        assert record.status == STATUS_FAILED, record.status
        assert record.modules["0:stammdaten"] == "failed", record.modules
        results.append(("multi-company (B1): single company's own pipeline failure reports failed, not done", True, ""))
    except AssertionError as e:
        results.append(("multi-company (B1): single company's own pipeline failure reports failed, not done", False, str(e)))

    try:
        # D15: create_second_warehouse fires ONLY for the newly created
        # company (index 0, was_created=True), never for the existing one.
        record, mock_resolve, mock_wh = _run_multi(
            _MULTI_PAYLOAD, resolve_side_effect=[(101, True), (1, False)])
        assert mock_wh.call_count == 1, mock_wh.call_args_list
        assert mock_wh.call_args_list[0].args[1] == 101, mock_wh.call_args_list
        results.append(("multi-company (D15): create_second_warehouse only for the new company", True, ""))
    except AssertionError as e:
        results.append(("multi-company (D15): create_second_warehouse only for the new company", False, str(e)))

    try:
        # A company whose target resolution itself fails (before
        # orchestrator.run ever starts) still counts as a failed company,
        # and the OTHER company still succeeds -> STATUS_PARTIAL.
        def _resolve_second_fails(client, target):
            if target.get("mode") == "existing":
                raise RuntimeError("company not found")
            return (101, True)

        record, mock_resolve, mock_wh = _run_multi(_MULTI_PAYLOAD, resolve_side_effect=_resolve_second_fails)
        assert record.status == STATUS_PARTIAL, record.status
        assert record.modules["0:stammdaten"] == "done", record.modules
        assert record.modules["1:stammdaten"] == "failed", record.modules
        results.append(("multi-company: one company's target resolution fails -> STATUS_PARTIAL, correct per-company rows", True, ""))
    except AssertionError as e:
        results.append(("multi-company: one company's target resolution fails -> STATUS_PARTIAL, correct per-company rows", False, str(e)))

    try:
        # Every company fails -> STATUS_FAILED, not STATUS_PARTIAL.
        record, mock_resolve, mock_wh = _run_multi(
            _MULTI_PAYLOAD, resolve_side_effect=RuntimeError("boom"))
        assert record.status == STATUS_FAILED, record.status
        assert record.modules["0:stammdaten"] == "failed", record.modules
        assert record.modules["1:stammdaten"] == "failed", record.modules
        results.append(("multi-company: all companies fail -> STATUS_FAILED, not STATUS_PARTIAL", True, ""))
    except AssertionError as e:
        results.append(("multi-company: all companies fail -> STATUS_FAILED, not STATUS_PARTIAL", False, str(e)))

    try:
        # D12 seed-and-harvest: the shared analytic cache set by company 0's
        # (fake) orchestrator.run call must be seeded into company 1's ctx
        # BEFORE its own orchestrator.run call — proves the cache actually
        # crosses the iteration boundary, not just that each ctx has its own.
        seen_seeds = []

        def _fake_run_records_seed(client, llm, ctx, on_module_start=None, on_module_done=None):
            seen_seeds.append(ctx.analytic_account_ids)
            if ctx.analytic_account_ids is None:
                ctx.analytic_account_ids = [701, 702, 703]  # simulate get_or_create_analytic_accounts
            if on_module_start:
                on_module_start("Stammdaten")
            if on_module_done:
                on_module_done("Stammdaten", ok=True)

        record, mock_resolve, mock_wh = _run_multi(
            _MULTI_PAYLOAD, resolve_side_effect=[(101, True), (1, False)],
            orchestrator_side_effect=_fake_run_records_seed)
        assert seen_seeds == [None, [701, 702, 703]], seen_seeds
        results.append(("multi-company (D12): analytic cache seeded from company 0 into company 1", True, ""))
    except AssertionError as e:
        results.append(("multi-company (D12): analytic cache seeded from company 0 into company 1", False, str(e)))

    try:
        # D12 None-guard: company 0's target resolution fails BEFORE the
        # analytic seed step ever runs — company 1 must still start from
        # None, not from an overwritten-with-None cache that erased a
        # (nonexistent, in this case) earlier real value.
        def _resolve_first_fails(client, target):
            if target.get("mode") == "new":
                raise RuntimeError("company create failed")
            return (1, False)

        seen_seeds = []

        def _fake_run_records_seed(client, llm, ctx, on_module_start=None, on_module_done=None):
            seen_seeds.append(ctx.analytic_account_ids)

        record, mock_resolve, mock_wh = _run_multi(
            _MULTI_PAYLOAD, resolve_side_effect=_resolve_first_fails,
            orchestrator_side_effect=_fake_run_records_seed)
        assert seen_seeds == [None], seen_seeds  # only company 1 ever reaches orchestrator.run
        assert record.status == STATUS_PARTIAL, record.status
        results.append(("multi-company (D12): failed company before seed step doesn't poison the shared cache", True, ""))
    except AssertionError as e:
        results.append(("multi-company (D12): failed company before seed step doesn't poison the shared cache", False, str(e)))

    try:
        # Legacy single-company payload (no "companies" key) still works
        # exactly as before S16 — the bridge path, N=1 regression criterion.
        record, mock_resolve, mock_wh = _run_multi(_PAYLOAD, resolve_side_effect=[])
        assert record.status == STATUS_DONE, record.status
        assert "stammdaten" in record.modules, record.modules  # unqualified, no "0:" prefix
        mock_resolve.assert_not_called()
        mock_wh.assert_not_called()
        results.append(("legacy single-company payload: unchanged, no S16 machinery invoked", True, ""))
    except AssertionError as e:
        results.append(("legacy single-company payload: unchanged, no S16 machinery invoked", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
