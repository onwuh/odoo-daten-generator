"""Unit tests for the S9 security layer: Guard A, Guard B, and error redaction.

Guard A (wrong target) and Guard B (SSRF) share one host regex but exist for
different reasons; the matrix below exercises both. Redaction is tested at the
call sites that matter — the log line AND the durable copy in `client.errors`,
because the run-summary API reads the latter.
"""
import os
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import odoo_client
from web import security


_REJECTED = [
    ("nicht-demo-Host", "https://kunde-produktiv.odoo.com"),
    ("fremde Domain", "https://demo-x.evil.example"),
    ("http statt https", "http://demo-x.odoo.com"),
    ("userinfo eingebettet", "https://demo-ok.odoo.com@evil.example/"),
    ("userinfo mit Passwort", "https://user:pw@demo-x.odoo.com"),
    ("Port-Override", "https://demo-x.odoo.com:8080/"),
    ("Port 443 explizit", "https://demo-x.odoo.com:443"),
    ("Link-local-Adresse", "https://169.254.169.254"),
    ("localhost", "https://localhost"),
    ("Pfad angehängt", "https://demo-x.odoo.com/web/login"),
    ("Query angehängt", "https://demo-x.odoo.com/?db=other"),
    ("Zeilenumbruch", "https://demo-x.odoo.com\r\nX-Injected: 1"),
    ("kyrillisches Homograph", "https://demo-х.odoo.com"),
    ("Präfix statt Subdomain", "https://notdemo-x.odoo.com"),
    ("leer", ""),
    ("None", None),
]

_ACCEPTED = [
    ("Standardform", "https://demo-pahu-test1.odoo.com", "https://demo-pahu-test1.odoo.com"),
    ("mit Schrägstrich", "https://demo-kunde.odoo.com/", "https://demo-kunde.odoo.com"),
    ("Großschreibung", "HTTPS://DEMO-Kunde.ODOO.COM", "https://demo-kunde.odoo.com"),
    ("Leerzeichen außen", "  https://demo-a1.odoo.com  ", "https://demo-a1.odoo.com"),
]


def _response(text, status_code=500, content_type="application/json"):
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    resp.headers = {"Content-Type": content_type}
    return resp


def run():
    results = []

    # ------------------------------------------------------------------
    # Guard A + Guard B rejection matrix
    # ------------------------------------------------------------------
    for label, url in _REJECTED:
        try:
            accepted = security.validate_target_url(url)
            results.append((f"Guard: abgelehnt — {label}", False, f"akzeptiert als {accepted}"))
        except security.TargetUrlError:
            results.append((f"Guard: abgelehnt — {label}", True, ""))
        except Exception as e:
            results.append((f"Guard: abgelehnt — {label}", False, f"falscher Fehlertyp: {e!r}"))

    for label, url, expected in _ACCEPTED:
        try:
            got = security.validate_target_url(url)
            assert got == expected, f"{got!r} != {expected!r}"
            results.append((f"Guard: akzeptiert — {label}", True, got))
        except Exception as e:
            results.append((f"Guard: akzeptiert — {label}", False, str(e)))

    # ------------------------------------------------------------------
    # Database name validation (goes into a request header)
    # ------------------------------------------------------------------
    try:
        assert security.validate_database_name("demo-pahu-test1") == "demo-pahu-test1"
        for bad in ("db\r\nX-Injected: 1", "", "  ", "db with spaces", "a" * 200):
            try:
                security.validate_database_name(bad)
                raise AssertionError(f"akzeptiert: {bad!r}")
            except security.TargetUrlError:
                pass
        results.append(("DB-Name: CRLF/Leerzeichen abgelehnt", True, ""))
    except Exception as e:
        results.append(("DB-Name: CRLF/Leerzeichen abgelehnt", False, str(e)))

    # ------------------------------------------------------------------
    # Guard B: redirects are refused rather than followed
    # ------------------------------------------------------------------
    try:
        assert odoo_client._ALLOW_REDIRECTS is False
        src = open(os.path.join(_ROOT, "odoo_client.py"), encoding="utf-8").read()
        # Exactly one session.post in the whole client, inside _send(), so the
        # opt-out is stated once and a call site added later cannot forget it.
        assert src.count("self.session.post(") == 1, \
            f"{src.count('self.session.post(')} session.post-Stellen statt einer in _send()"
        assert "allow_redirects=_ALLOW_REDIRECTS" in src
        assert "self.session.allow_redirects" not in src, \
            "Session-Attribut statt Per-Request-Kwarg (requests.Session hat kein allow_redirects)"
        # All three logical call sites — including the SaaS ?db= fallback the
        # architecture lock exists to protect — route through it.
        assert src.count("self._send(") == 3, \
            f"{src.count('self._send(')} statt 3 Aufrufstellen über _send()"
        results.append(("Guard B: genau ein POST-Pfad, allow_redirects=False", True, ""))
    except Exception as e:
        results.append(("Guard B: genau ein POST-Pfad, allow_redirects=False", False, str(e)))

    # ------------------------------------------------------------------
    # Rate-limit backoff — the demo SaaS instance answers 429 under load
    # ------------------------------------------------------------------
    try:
        client = odoo_client.OdooJson2Client("https://demo-x.odoo.com", "db", "key")
        calls = {"n": 0}

        def _post(url, json=None, timeout=None, allow_redirects=None):
            assert allow_redirects is False
            calls["n"] += 1
            if calls["n"] < 3:
                return _response("<html>429</html>", status_code=429, content_type="text/html")
            return _response('{"ok": true}', status_code=200)

        client.session.post = _post
        with patch.object(odoo_client.time, "sleep") as slept:
            response = client._send("https://demo-x.odoo.com/json/2/x/y", {})
        assert response.status_code == 200, response.status_code
        assert calls["n"] == 3, calls
        assert slept.call_count == 2, slept.call_count
        results.append(("Rate-Limit: 429 wird mit Backoff wiederholt", True, f"{calls['n']} Versuche"))
    except Exception as e:
        results.append(("Rate-Limit: 429 wird mit Backoff wiederholt", False, str(e)))

    try:
        client = odoo_client.OdooJson2Client("https://demo-x.odoo.com", "db", "key")
        calls = {"n": 0}

        def _always_429(url, json=None, timeout=None, allow_redirects=None):
            calls["n"] += 1
            return _response("<html>429</html>", status_code=429, content_type="text/html")

        client.session.post = _always_429
        with patch.object(odoo_client.time, "sleep"):
            response = client._send("https://demo-x.odoo.com/json/2/x/y", {})
        # Bounded: it gives up and hands the 429 back rather than retrying forever.
        assert calls["n"] == odoo_client._MAX_ATTEMPTS, calls
        assert response.status_code == 429
        results.append(("Rate-Limit: Wiederholungen sind begrenzt", True, f"{calls['n']} Versuche"))
    except Exception as e:
        results.append(("Rate-Limit: Wiederholungen sind begrenzt", False, str(e)))

    try:
        # Retry-After wins over the backoff curve when the server sends one.
        resp = _response("", status_code=429)
        resp.headers = {"Retry-After": "7"}
        assert odoo_client._retry_delay(resp, 1) == 7.0
        resp.headers = {"Retry-After": "nonsense"}
        assert odoo_client._retry_delay(resp, 1) >= odoo_client._BACKOFF_BASE_SECONDS
        # Backoff grows with the attempt number.
        resp.headers = {}
        assert odoo_client._retry_delay(resp, 3) > odoo_client._retry_delay(resp, 1)
        results.append(("Rate-Limit: Retry-After schlägt Backoff-Kurve", True, ""))
    except Exception as e:
        results.append(("Rate-Limit: Retry-After schlägt Backoff-Kurve", False, str(e)))

    try:
        raised = False
        try:
            odoo_client._reject_redirect(_response("", status_code=302))
        except odoo_client.RedirectRefused:
            raised = True
        assert raised, "302 wurde nicht abgelehnt"
        # 2xx and 4xx must pass through untouched.
        odoo_client._reject_redirect(_response("{}", status_code=200))
        odoo_client._reject_redirect(_response("{}", status_code=404))
        results.append(("Guard B: 3xx wird als Weiterleitung abgelehnt", True, ""))
    except Exception as e:
        results.append(("Guard B: 3xx wird als Weiterleitung abgelehnt", False, str(e)))

    # ------------------------------------------------------------------
    # Error-body redaction — the SSRF read primitive
    # ------------------------------------------------------------------
    try:
        secret = "<html><body>AWS_SECRET_ACCESS_KEY=abcdef</body></html>"
        redacted = odoo_client._redact_error_body(_response(secret, content_type="text/html"))
        assert "AWS_SECRET" not in redacted, redacted
        assert "unterdrückt" in redacted, redacted
        results.append(("Redaktion: Nicht-JSON-Körper wird nicht durchgereicht", True, redacted[:40]))
    except Exception as e:
        results.append(("Redaktion: Nicht-JSON-Körper wird nicht durchgereicht", False, str(e)))

    try:
        odoo_error = '{"error": {"data": {"message": "Invalid field \'foo\' on \'res.partner\'"}}}'
        redacted = odoo_client._redact_error_body(_response(odoo_error))
        assert "Invalid field" in redacted, redacted
        results.append(("Redaktion: strukturierte Odoo-Meldung bleibt erhalten", True, redacted[:40]))
    except Exception as e:
        results.append(("Redaktion: strukturierte Odoo-Meldung bleibt erhalten", False, str(e)))

    try:
        long_json = '{"message": "' + ("x" * 2000) + '"}'
        redacted = odoo_client._redact_error_body(_response(long_json))
        assert len(redacted) <= odoo_client._ERROR_MESSAGE_LIMIT, len(redacted)
        results.append(("Redaktion: Meldung wird gekürzt", True, f"{len(redacted)} Zeichen"))
    except Exception as e:
        results.append(("Redaktion: Meldung wird gekürzt", False, str(e)))

    try:
        src = open(os.path.join(_ROOT, "odoo_client.py"), encoding="utf-8").read()
        assert "response.text[:500]" not in src, "roher Antwortkörper wird noch gespeichert"
        assert '"error_body": error_body' in src, "error_body-Feld fehlt"
        results.append(("Redaktion: keine rohe response.text-Kopie mehr in self.errors", True, ""))
    except Exception as e:
        results.append(("Redaktion: keine rohe response.text-Kopie mehr in self.errors", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
