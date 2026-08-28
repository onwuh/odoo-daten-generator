"""Unit tests for the S9 web layer: auth, CSRF, session isolation, admission control.

No network: connect_service.probe and orchestrator.run are replaced by fakes, so
these exercise the HTTP surface and the queue, not Odoo.
"""
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("ODOO_GENERATOR_ACCESS_CODE", "unit-test-code")

from fastapi.testclient import TestClient

import connect_service
from web import app as web_app
from web.jobs import AdmissionRefused, JobQueue, STATUS_DONE
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
    result.odoo_version = "saas-19.4"
    result.existing_company_ids = [1, 2, 3]
    result.existing_product_ids = [10, 11]
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
            # No credential is ever returned.
            assert "gsk_key" not in response.text
        results.append(("Connect: feature_flags + purchase/stock im Ergebnis", True, ""))
    except Exception as e:
        results.append(("Connect: feature_flags + purchase/stock im Ergebnis", False, str(e)))

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
            csrf_b = _login(client_b)
            foreign = client_b.get(f"/api/runs/{run_id}")
            assert foreign.status_code == 404, foreign.status_code
            events = client_b.get(f"/api/runs/{run_id}/events")
            assert events.status_code == 404, events.status_code
        results.append(("Session-Isolation: fremder Lauf ist nicht lesbar", True, ""))
    except Exception as e:
        results.append(("Session-Isolation: fremder Lauf ist nicht lesbar", False, str(e)))

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
            ok_payload = dict(_PAYLOAD, skip_master_data=True, use_existing=True)
            with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
                 patch("orchestrator.run"):
                allowed = client.post("/api/runs", headers=_auth_headers(csrf), json=ok_payload)
                assert allowed.status_code == 202, allowed.text
                time.sleep(0.3)
        results.append(("Lauf: skip_master_data ohne use_existing wird abgelehnt", True, ""))
    except Exception as e:
        results.append(("Lauf: skip_master_data ohne use_existing wird abgelehnt", False, str(e)))

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

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
