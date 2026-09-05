"""Unit tests for the feedback -> GitHub issue feature (web/feedback.py + POST /api/feedback).

No network: requests.post is mocked throughout, so these exercise the request
building, error redaction, 422-label-fallback retry, and the HTTP surface —
never a real GitHub call. See tests/integration/test_feedback.py for the live
token/repo/permission check.
"""
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("ODOO_GENERATOR_ACCESS_CODE", "unit-test-code")
# S11/D9: this file's run_id-context test lets a submitted run actually
# execute, which now unconditionally opens a per-run log file
# (run_journal.run_log_path) — see the matching comment in
# test_web_api_unit.py for the full reasoning.
os.environ.setdefault("ODOO_GENERATOR_RUNS_DIR", tempfile.mkdtemp(prefix="odoo_gen_test_runs_"))

import requests
from fastapi.testclient import TestClient

import connect_service
from web import app as web_app
from web import feedback

_HEADERS = {"X-Requested-With": "odoo-generator"}
_FAKE_TOKEN = "ghp_unitTestFakeToken0123456789"


def _response(status_code=201, text="", content_type="application/json",
              json_data=None, json_error=None, location=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    headers = {"Content-Type": content_type}
    if location:
        headers["Location"] = location
    resp.headers = headers
    if json_error is not None:
        resp.json.side_effect = json_error
    else:
        resp.json.return_value = json_data if json_data is not None else {}
    return resp


def _login(client):
    response = client.post("/api/auth", json={"access_code": "unit-test-code"}, headers=_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _auth_headers(csrf):
    headers = dict(_HEADERS)
    headers["X-CSRF-Token"] = csrf
    return headers


def _fake_connect_result():
    result = connect_service.ConnectResult()
    result.ok = True
    result.steps = []
    result.company_name = "Testfirma GmbH"
    result.language_code = "de_DE"
    result.language_name = "German"
    result.installed_modules = {"crm"}
    result.feature_flags = {}
    result.model_access = {}
    result.blocked_modules = set()
    result.odoo_version = "saas-19.4"
    result.existing_partner_company_ids = []
    result.existing_product_ids = []
    result.llm_provider = "groq"
    result.llm_model = "model"
    return result


def _connect(client, csrf):
    with patch.object(connect_service, "probe",
                      return_value=(_fake_connect_result(), MagicMock(), MagicMock())):
        return client.post("/api/connect", headers=_auth_headers(csrf), json={
            "url": "https://demo-unit-test.odoo.com",
            "odoo_key": "key", "llm_key": "gsk_key", "llm_model": "model",
        })


def run():
    results = []

    # ==================================================================
    # web/feedback.py — create_github_issue()
    # ==================================================================

    try:
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.return_value = _response(
                json_data={"html_url": "https://github.com/pahuodoo/odoo-daten-generator/issues/42",
                          "number": 42})
            result = feedback.create_github_issue("bug", "Es kracht beim Speichern.")
            assert result == {"url": "https://github.com/pahuodoo/odoo-daten-generator/issues/42",
                              "number": 42}, result
            kwargs = mock_post.call_args.kwargs
            assert kwargs["allow_redirects"] is False, kwargs
            assert kwargs["headers"]["Authorization"] == f"Bearer {_FAKE_TOKEN}", kwargs["headers"]
            assert kwargs["json"]["labels"] == ["bug"], kwargs["json"]
        results.append(("create_github_issue: Kategorie 'bug' -> Label 'bug', liefert url+number", True, ""))
    except Exception as e:
        results.append(("create_github_issue: Kategorie 'bug' -> Label 'bug', liefert url+number", False, str(e)))

    try:
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.return_value = _response(json_data={"html_url": "https://x/1", "number": 1})
            feedback.create_github_issue("idee_feature", "Wäre cool wenn...")
            assert mock_post.call_args.kwargs["json"]["labels"] == ["enhancement"], mock_post.call_args
        results.append(("create_github_issue: Kategorie 'idee_feature' -> Label 'enhancement'", True, ""))
    except Exception as e:
        results.append(("create_github_issue: Kategorie 'idee_feature' -> Label 'enhancement'", False, str(e)))

    try:
        # Empty string, not a pop: patch.dict restores whatever was there before
        # (including "unset") when the block exits either way, but an in-place
        # pop with no matching push would permanently drop a real ambient
        # GITHUB_TOKEN for the rest of this process if the environment has one.
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}), \
             patch.object(feedback.requests, "post") as mock_post:
            try:
                feedback.create_github_issue("bug", "x")
                raised = False
            except feedback.GitHubConfigError:
                raised = True
            assert raised, "GitHubConfigError nicht ausgelöst"
            mock_post.assert_not_called()
        results.append(("create_github_issue: GITHUB_TOKEN fehlt -> Config-Fehler, kein Aufruf", True, ""))
    except Exception as e:
        results.append(("create_github_issue: GITHUB_TOKEN fehlt -> Config-Fehler, kein Aufruf", False, str(e)))

    try:
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.side_effect = [
                _response(status_code=422, text='{"message":"Validation Failed",'
                                               '"errors":[{"field":"labels","code":"invalid"}]}'),
                _response(status_code=201, json_data={"html_url": "https://x/2", "number": 2}),
            ]
            result = feedback.create_github_issue("bug", "x")
            assert result["number"] == 2, result
            assert mock_post.call_count == 2, mock_post.call_count
            retry_payload = mock_post.call_args_list[1].kwargs["json"]
            assert "labels" not in retry_payload, retry_payload
        results.append(("create_github_issue: 422 (Label) -> Retry ohne 'labels', Erfolg", True, ""))
    except Exception as e:
        results.append(("create_github_issue: 422 (Label) -> Retry ohne 'labels', Erfolg", False, str(e)))

    try:
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.side_effect = [
                _response(status_code=422, text='{"message":"erste Ursache",'
                                               '"errors":[{"message":"Label unbekannt"}]}'),
                _response(status_code=422, text='{"message":"zweite, andere Ursache"}'),
            ]
            try:
                feedback.create_github_issue("bug", "x")
                message = None
            except feedback.GitHubUpstreamError as exc:
                message = str(exc)
            assert message and "Label unbekannt" in message, message
            assert message and "zweite, andere Ursache" not in message, message
        results.append(("create_github_issue: doppeltes 422 -> Fehler nennt ERSTEN Grund", True, ""))
    except Exception as e:
        results.append(("create_github_issue: doppeltes 422 -> Fehler nennt ERSTEN Grund", False, str(e)))

    try:
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.return_value = _response(status_code=500, text="<html>INTERNAL-SECRET</html>",
                                                content_type="text/html")
            try:
                feedback.create_github_issue("bug", "x")
                message = None
            except feedback.GitHubUpstreamError as exc:
                message = str(exc)
            assert message is not None, "keine Exception ausgelöst"
            assert "INTERNAL-SECRET" not in message, message
        results.append(("create_github_issue: unlesbarer Fehlerkörper -> kein Crash, kein Leak", True, ""))
    except Exception as e:
        results.append(("create_github_issue: unlesbarer Fehlerkörper -> kein Crash, kein Leak", False, str(e)))

    try:
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.return_value = _response(status_code=302, location="https://evil.example")
            try:
                feedback.create_github_issue("bug", "x")
                raised = False
            except feedback.GitHubUpstreamError:
                raised = True
            assert raised, "Weiterleitung nicht abgelehnt"
            assert mock_post.call_count == 1, "Weiterleitung darf keinen Retry auslösen"
        results.append(("create_github_issue: 3xx wird abgelehnt (_reject_redirect), kein .json()", True, ""))
    except Exception as e:
        results.append(("create_github_issue: 3xx wird abgelehnt (_reject_redirect), kein .json()", False, str(e)))

    try:
        messages = {}
        for code in (401, 403, 404):
            with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
                 patch.object(feedback.requests, "post") as mock_post:
                mock_post.return_value = _response(status_code=code, text="{}")
                try:
                    feedback.create_github_issue("bug", "x")
                except feedback.GitHubUpstreamError as exc:
                    messages[code] = str(exc)
        assert all("Schreibrechte" in m or "ungültig" in m for m in messages.values()), messages
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.return_value = _response(status_code=503, text="{}")
            try:
                feedback.create_github_issue("bug", "x")
                generic = None
            except feedback.GitHubUpstreamError as exc:
                generic = str(exc)
        assert generic and "Schreibrechte" not in generic, generic
        results.append(("create_github_issue: 401/403/404 -> eigene Meldung, unterscheidet sich vom generischen Fall", True, ""))
    except Exception as e:
        results.append(("create_github_issue: 401/403/404 -> eigene Meldung, unterscheidet sich vom generischen Fall", False, str(e)))

    try:
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError("boom")
            try:
                feedback.create_github_issue("bug", "x")
                raised = False
            except feedback.GitHubUpstreamError:
                raised = True
        assert raised, "ConnectionError nicht abgefangen"
        results.append(("create_github_issue: Verbindungsfehler -> GitHubUpstreamError, kein Crash", True, ""))
    except Exception as e:
        results.append(("create_github_issue: Verbindungsfehler -> GitHubUpstreamError, kein Crash", False, str(e)))

    try:
        with patch.dict(os.environ, {"GITHUB_TOKEN": _FAKE_TOKEN}), \
             patch.object(feedback.requests, "post") as mock_post:
            mock_post.return_value = _response(status_code=500, text="{}")
            try:
                feedback.create_github_issue("bug", "x")
                leaked = False
            except feedback.GitHubUpstreamError as exc:
                leaked = _FAKE_TOKEN in str(exc)
        assert not leaked, "Token in Fehlermeldung sichtbar"
        results.append(("create_github_issue: Token erscheint nie in einer Fehlermeldung", True, ""))
    except Exception as e:
        results.append(("create_github_issue: Token erscheint nie in einer Fehlermeldung", False, str(e)))

    # ==================================================================
    # POST /api/feedback
    # ==================================================================

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            no_token = client.post("/api/feedback", headers=_HEADERS,
                                   json={"category": "bug", "message": "x"})
            assert no_token.status_code == 403, no_token.status_code
            no_header = client.post("/api/feedback", headers={"X-CSRF-Token": csrf},
                                    json={"category": "bug", "message": "x"})
            assert no_header.status_code == 403, no_header.status_code
        results.append(("POST /api/feedback: ohne CSRF-Token bzw. Pflicht-Header 403", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: ohne CSRF-Token bzw. Pflicht-Header 403", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            response = client.post("/api/feedback", headers=_HEADERS,
                                   json={"category": "bug", "message": "x"})
            assert response.status_code == 401, response.status_code
        results.append(("POST /api/feedback: ohne Sitzung 401", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: ohne Sitzung 401", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(feedback, "create_github_issue") as mock_create:
                response = client.post("/api/feedback", headers=_auth_headers(csrf),
                                       json={"category": "not-a-real-category", "message": "x"})
                assert response.status_code == 400, response.status_code
                mock_create.assert_not_called()
        results.append(("POST /api/feedback: ungültige Kategorie -> 400, kein GitHub-Aufruf", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: ungültige Kategorie -> 400, kein GitHub-Aufruf", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(feedback, "create_github_issue") as mock_create:
                empty = client.post("/api/feedback", headers=_auth_headers(csrf),
                                    json={"category": "bug", "message": "   "})
                assert empty.status_code == 400, empty.status_code
                too_long = client.post("/api/feedback", headers=_auth_headers(csrf),
                                       json={"category": "bug", "message": "x" * 4001})
                assert too_long.status_code == 400, too_long.status_code
                mock_create.assert_not_called()
        results.append(("POST /api/feedback: leere/zu lange Nachricht -> 400, kein GitHub-Aufruf", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: leere/zu lange Nachricht -> 400, kein GitHub-Aufruf", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(feedback, "create_github_issue",
                              side_effect=feedback.GitHubConfigError("kein Token")):
                response = client.post("/api/feedback", headers=_auth_headers(csrf),
                                       json={"category": "bug", "message": "x"})
                assert response.status_code == 503, response.status_code
        results.append(("POST /api/feedback: GitHubConfigError -> 503", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: GitHubConfigError -> 503", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(feedback, "create_github_issue",
                              side_effect=feedback.GitHubUpstreamError("GitHub tot")):
                response = client.post("/api/feedback", headers=_auth_headers(csrf),
                                       json={"category": "bug", "message": "x"})
                assert response.status_code == 502, response.status_code
        results.append(("POST /api/feedback: GitHubUpstreamError -> 502", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: GitHubUpstreamError -> 502", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(feedback, "create_github_issue",
                              return_value={"url": "https://github.com/x/y/issues/1", "number": 1}):
                response = client.post("/api/feedback", headers=_auth_headers(csrf),
                                       json={"category": "idee_feature", "message": "Neue Idee"})
                assert response.status_code == 201, response.text
                assert response.json() == {"url": "https://github.com/x/y/issues/1", "number": 1}
        results.append(("POST /api/feedback: Erfolgspfad -> 201 mit url+number", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: Erfolgspfad -> 201 mit url+number", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            _connect(client, csrf)
            with patch("web.jobs.JournalingClient"), patch("web.jobs.LLMService"), \
                 patch("orchestrator.run"):
                created = client.post("/api/runs", headers=_auth_headers(csrf), json={
                    "mode": "both", "industry": "IT", "use_existing": False,
                    "skip_master_data": False,
                    "master_data": {"num_companies": 1, "num_delivery_contacts": 0,
                                    "num_invoice_contacts": 0, "num_other_contacts": 0,
                                    "num_services": 1, "num_consumables": 0, "num_storables": 1},
                    "modules": {"crm": {"enabled": True, "count": 1}},
                })
                assert created.status_code == 202, created.text
                run_id = created.json()["run_id"]
                time.sleep(0.4)
            captured = {}
            with patch.object(feedback, "create_github_issue",
                              side_effect=lambda cat, msg, context=None: captured.update(context=context)
                              or {"url": "https://x/1", "number": 1}):
                response = client.post("/api/feedback", headers=_auth_headers(csrf),
                                       json={"category": "bug", "message": "x", "run_id": run_id})
                assert response.status_code == 201, response.text
            ctx = captured["context"]
            assert ctx is not None and ctx["run_id"] == run_id, ctx
            assert "target" not in ctx and "database" not in ctx, ctx
            # S11/D9 — exact key set, not just "these two keys are absent":
            # the run log now lives locally (run_journal.run_log_path) and
            # is retrievable by run_id alone, so a future edit adding a
            # "log" key here would silently defeat that property. Locking
            # the key set is the regression guard for it.
            assert set(ctx.keys()) == {"run_id", "status", "modules", "api_error_count"}, ctx
        results.append(("POST /api/feedback: run_id-Kontext enthält run_id/status, nie target/database", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: run_id-Kontext enthält run_id/status, nie target/database", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            captured = {}
            with patch.object(feedback, "create_github_issue",
                              side_effect=lambda cat, msg, context=None: captured.update(context=context)
                              or {"url": "https://x/1", "number": 1}):
                response = client.post("/api/feedback", headers=_auth_headers(csrf),
                                       json={"category": "bug", "message": "x",
                                             "run_id": "demo-unknown-run"})
                assert response.status_code == 201, response.text
            assert captured["context"] is None, captured
        results.append(("POST /api/feedback: unbekannte run_id -> Kontext None, trotzdem 201", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: unbekannte run_id -> Kontext None, trotzdem 201", False, str(e)))

    try:
        with TestClient(web_app.app) as client:
            csrf = _login(client)
            with patch.object(feedback, "create_github_issue",
                              return_value={"url": "https://x/1", "number": 1}):
                for _ in range(5):
                    ok = client.post("/api/feedback", headers=_auth_headers(csrf),
                                     json={"category": "bug", "message": "x"})
                    assert ok.status_code == 201, ok.status_code
                blocked = client.post("/api/feedback", headers=_auth_headers(csrf),
                                      json={"category": "bug", "message": "x"})
                assert blocked.status_code == 429, blocked.status_code
        results.append(("POST /api/feedback: Rate-Limit greift bei der 6. Einsendung/Stunde", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: Rate-Limit greift bei der 6. Einsendung/Stunde", False, str(e)))

    try:
        import inspect
        source = inspect.getsource(web_app.api_feedback)
        assert "asyncio.to_thread" in source, \
            "create_github_issue muss über asyncio.to_thread laufen, sonst blockiert es den Event-Loop"
        results.append(("POST /api/feedback: create_github_issue läuft über asyncio.to_thread", True, ""))
    except Exception as e:
        results.append(("POST /api/feedback: create_github_issue läuft über asyncio.to_thread", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
