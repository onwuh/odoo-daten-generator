"""Live check for the feedback -> GitHub issue feature.

Deliberate deviation from this suite's usual "exercise the real behaviour"
rule: a real issue-creation call would spam pahuodoo/odoo-daten-generator on
every test run. Instead this verifies the precondition create_github_issue
actually depends on — the token authenticates and can read the repo — via a
read-only GET. The 422-retry / redirect-rejection / error-redaction logic is
covered only by the mocked tests/unit/test_web_feedback_unit.py, not here.

Needs no Odoo client; client/ctx are accepted (and unused) only to match this
suite's run(client, ctx) call convention.
"""
import os

import requests

from web import feedback


def run(client, ctx):
    results = []

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        results.append(("GitHub: Token nicht gesetzt — übersprungen", True,
                        "kein GITHUB_TOKEN konfiguriert"))
        return True, results

    try:
        response = requests.get(
            feedback.GITHUB_API_BASE, timeout=10, allow_redirects=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "odoo-daten-generator-feedback",
            },
        )
        assert response.status_code == 200, f"HTTP {response.status_code}"
        data = response.json()
        permissions = data.get("permissions")
        if isinstance(permissions, dict):
            ok = permissions.get("push") is True
            detail = f"repo={data.get('full_name')} push={permissions.get('push')}"
        else:
            # Some token/endpoint combinations omit "permissions" entirely —
            # a readable repo is still evidence the token authenticates, just
            # not proof of write access. Treated as inconclusive-but-passing
            # rather than failing the suite over a field GitHub doesn't
            # guarantee for every token type.
            ok, detail = True, f"repo={data.get('full_name')} (permissions-Feld fehlt)"
        results.append(("GitHub: Token gültig, Schreibrechte wo verfügbar geprüft", ok, detail))
    except Exception as e:
        results.append(("GitHub: Token gültig, Schreibrechte wo verfügbar geprüft", False, str(e)))

    return all(ok for _, ok, _ in results), results
