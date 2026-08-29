"""Feedback → GitHub issue creation, server-side, no user GitHub login needed.

Blocking (requests). Callers on the event loop must wrap create_github_issue in
asyncio.to_thread, the same convention web/app.py already uses for
connect_service.probe.
"""
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import requests

from odoo_client import _reject_redirect

logger = logging.getLogger(__name__)

GITHUB_OWNER = "pahuodoo"
GITHUB_REPO = "odoo-daten-generator"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_ISSUES_URL = f"{GITHUB_API_BASE}/issues"

_TIMEOUT_SECONDS = 10           # interactive user click, not a batch pipeline —
                                 # deliberately shorter than odoo_client's default
_ERROR_MESSAGE_LIMIT = 300
_TITLE_MESSAGE_LIMIT = 70

# Chosen to map 1:1 onto GitHub's own default repo labels (bug/enhancement),
# so issue creation never depends on a custom label existing in the repo.
CATEGORIES = {
    "bug": {"label": "bug", "title_tag": "[Bug]"},
    "idee_feature": {"label": "enhancement", "title_tag": "[Idee & Feature]"},
}


class GitHubConfigError(RuntimeError):
    """GITHUB_TOKEN is not set on this server — route maps this to 503."""


class GitHubUpstreamError(RuntimeError):
    """GitHub was unreachable or rejected the request — route maps this to 502."""


def _redact_github_error(response: "requests.Response") -> str:
    """Structured extraction of GitHub's {"message", "errors":[...]} shape only.

    Deliberately a local helper, not odoo_client._redact_error_body: that one
    only looks at a top-level "message" and drops "errors[]" — exactly the
    field that names a rejected label on a 422.
    """
    try:
        raw = response.text or ""
    except Exception:
        return "<Antwortkörper nicht lesbar>"
    if not raw.strip():
        return ""
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        parts = []
        message = parsed.get("message")
        if isinstance(message, str) and message.strip():
            parts.append(message.strip())
        errors = parsed.get("errors")
        if isinstance(errors, list):
            for item in errors:
                if isinstance(item, dict):
                    detail = item.get("message") or ", ".join(
                        str(v) for v in (item.get("field"), item.get("code")) if v)
                    if detail:
                        parts.append(str(detail))
                elif isinstance(item, str):
                    parts.append(item)
        if parts:
            return " | ".join(parts)[:_ERROR_MESSAGE_LIMIT]
    content_type = (response.headers.get("Content-Type") or "unbekannt").split(";")[0]
    return f"<Antwortkörper unterdrückt: {len(raw)} Zeichen, {content_type}>"


def _build_title(title_tag: str, message: str) -> str:
    stripped = message.strip()
    snippet = stripped.splitlines()[0] if stripped else ""
    if len(snippet) > _TITLE_MESSAGE_LIMIT:
        snippet = snippet[:_TITLE_MESSAGE_LIMIT] + "…"
    return f"{title_tag} {snippet}".strip()


def _build_body(message: str, context: Optional[Dict[str, Any]]) -> str:
    lines = [
        message.strip(), "", "---",
        f"Zeit (Server, UTC): {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}",
    ]
    # Data-minimized on purpose: run_id/status/module-status/error-count only —
    # never target/database/error text, which can carry a prospect's hostname
    # or Odoo error text into a GitHub issue.
    if context:
        run_id = context.get("run_id")
        if run_id:
            lines.append(f"Lauf: {run_id} ({context.get('status')})")
        modules = context.get("modules") or []
        if modules:
            summary = ", ".join(f"{m['key']}={m['status']}" for m in modules)
            lines.append(f"Module: {summary}")
        api_error_count = context.get("api_error_count")
        if api_error_count:
            lines.append(f"API-Fehler im Lauf: {api_error_count}")
    lines.append("")
    lines.append("_Über den Feedback-Button der Demodaten-Konsole erstellt._")
    return "\n".join(lines)


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "odoo-daten-generator-feedback",
        "Content-Type": "application/json",
    }


def _post_issue(token: str, payload: Dict[str, Any]) -> "requests.Response":
    try:
        response = requests.post(GITHUB_ISSUES_URL, json=payload, timeout=_TIMEOUT_SECONDS,
                                 allow_redirects=False, headers=_headers(token))
        _reject_redirect(response)
    except requests.RequestException as exc:
        raise GitHubUpstreamError(str(exc) or "GitHub war nicht erreichbar.") from exc
    return response


def create_github_issue(category: str, message: str,
                        context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create an issue in pahuodoo/odoo-daten-generator. Returns {"url", "number"}."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GitHubConfigError(
            "Feedback ist auf diesem Server nicht konfiguriert (kein GITHUB_TOKEN gesetzt).")

    meta = CATEGORIES[category]
    title = _build_title(meta["title_tag"], message)
    body = _build_body(message, context)

    response = _post_issue(token, {"title": title, "body": body, "labels": [meta["label"]]})

    # A 422 here is almost always the label not existing on this repo — retry
    # once without labels rather than fail feedback submission outright.
    first_detail = None
    if response.status_code == 422:
        first_detail = _redact_github_error(response)
        logger.warning(f"[feedback] GitHub lehnte Issue mit Label ab (422): {first_detail} "
                       "— Retry ohne Label.")
        response = _post_issue(token, {"title": title, "body": body})

    if response.status_code in (401, 403, 404):
        raise GitHubUpstreamError(
            "GitHub-Token ungültig oder ohne Schreibrechte für dieses Repository.")
    if response.status_code >= 400:
        # The earliest attempt with a real body names the actual reason —
        # mirrors odoo_client._select_attempt.
        detail = first_detail if first_detail is not None else _redact_github_error(response)
        raise GitHubUpstreamError(f"GitHub hat das Issue abgelehnt: {detail}")

    try:
        data = response.json()
    except ValueError as exc:
        raise GitHubUpstreamError("GitHub-Antwort nicht lesbar.") from exc

    return {"url": data.get("html_url"), "number": data.get("number")}
