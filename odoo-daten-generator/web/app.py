"""FastAPI application — replaces the retired CustomTkinter wizard.

Route surface mirrors the four steps of the old wizard:
    POST /api/auth            shared access code -> session cookie
    POST /api/connect         reachability probe -> checklist + feature flags
    POST /api/runs            enqueue a run -> 202 {run_id}
    GET  /api/runs/{id}       status, per-module progress, counters
    GET  /api/runs/{id}/events  SSE: log + module + status + end
    POST /api/runs/{id}/cleanup delete everything the run created (D7)
"""
import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

import connect_service
import llm_service
import run_config
import server_config
from logging_setup import configure_logging
from odoo_client import OdooJson2Client
from run_journal import (RunJournal, delete_run, journal_dir_writable,
                         prune_journals, retention_days)
from web import feedback, security
from web.jobs import AdmissionRefused, JobQueue, _bare_module_key
from web.session import CSRF_HEADER, SESSION_COOKIE, SessionStore, check_access_code
from web.sse import EventBroker

configure_logging()
logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# A cross-origin <form> POST cannot set a custom request header without a CORS
# preflight, and no CORS policy is configured — so requiring this header on every
# state-changing call blocks classic CSRF, including login CSRF where there is no
# session token to check yet.
REQUESTED_WITH_HEADER = "X-Requested-With"
REQUESTED_WITH_VALUE = "odoo-generator"

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)


def profile() -> str:
    return (os.environ.get("ODOO_GENERATOR_PROFILE") or "local").lower()


def cookie_secure(request: Optional[Request] = None) -> bool:
    """Whether the session cookie gets the Secure flag.

    Derived from the actual request scheme first, and only then from
    configuration. That ordering matters: put the app behind a TLS tunnel
    (`cloudflared`, a reverse proxy) while .env still says `local`, and a purely
    config-driven answer would ship the session cookie without Secure over a
    connection the browser considers https. uvicorn's `--proxy-headers` makes
    `request.url.scheme` reflect what the client actually used, so the transport
    decides and the setting can only turn it *on*, never off.
    """
    if request is not None and request.url.scheme == "https":
        return True
    raw = os.environ.get("ODOO_GENERATOR_COOKIE_SECURE")
    if raw is not None:
        return raw.lower() in ("1", "true", "yes")
    # Local profile is plain http on localhost, where a Secure cookie is dropped.
    return profile() == "server"


sessions = SessionStore()
broker = EventBroker()
jobs = JobQueue(broker)

# How often the janitor runs, and how long a finished run stays queryable.
SWEEP_INTERVAL_SECONDS = 300
FINISHED_RUN_TTL_SECONDS = 3600


async def _janitor() -> None:
    """Expire abandoned sessions, finished runs and stale run journals.

    None of this is cosmetic. Session credentials are only dropped on the next
    touch of that session, so without a periodic sweep an abandoned session keeps
    its Odoo and LLM keys in memory for the life of the process — which
    contradicts the "discarded on expiry" promise the UI makes. Run records and
    their event streams are likewise never released on their own.
    """
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            expired = sessions.sweep()
            pruned = jobs.prune(FINISHED_RUN_TTL_SECONDS)
            journals = await asyncio.to_thread(prune_journals)
            if expired or pruned or journals:
                logger.info(f"[web] Aufräumen: {expired} Sitzung(en), {pruned} Lauf/Läufe, "
                            f"{journals} Journal(e) entfernt")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a janitor must never take the app down
            logger.warning(f"[web] Aufräumen fehlgeschlagen: {exc}")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    jobs.start()
    logger.info(f"[web] Profil={profile()} Worker={jobs.workers} "
                f"Cookie-Secure={cookie_secure()} "
                f"Journal-Aufbewahrung={retention_days()}d")
    if server_config.enabled():
        pub = server_config.public_defaults()
        if pub["has_odoo_key"] or pub["has_llm_key"]:
            logger.warning(
                "[web] BETA: Server-Voreinstellungen aktiv — leere Felder werden aus "
                "config.ini gefüllt. Damit hält der Server eigene Zugangsdaten, und der "
                "Zugangscode ist das Einzige, was davor steht. "
                "Abschalten: ODOO_GENERATOR_CONFIG_DEFAULTS=off")
    if not os.environ.get("ODOO_GENERATOR_ACCESS_CODE"):
        logger.warning("[web] ODOO_GENERATOR_ACCESS_CODE ist nicht gesetzt — "
                       "jede Anmeldung wird abgelehnt.")
    if not os.environ.get("GITHUB_TOKEN"):
        logger.warning("[web] GITHUB_TOKEN ist nicht gesetzt — Feedback erstellt "
                       "keine GitHub-Issues (503).")
    # Probe the writable paths at startup. Both are configured by environment
    # variable and both fail late and confusingly when wrong: the cache surfaced
    # as a bare "[Errno 30] Read-only file system: '/data'" two minutes into a
    # run, and an unwritable journal directory disables cleanup without saying so.
    for label, problem, variable in (
        ("Seed-Cache", llm_service.cache_dir_writable(), "ODOO_GENERATOR_CACHE_DIR"),
        ("Run-Journal", journal_dir_writable(), "ODOO_GENERATOR_RUNS_DIR"),
    ):
        if problem:
            logger.error(f"[web] {label}-Verzeichnis nicht beschreibbar — {problem}. "
                         f"Prüfe {variable}. Die Container-Voreinstellung (/data/…) gilt "
                         f"nur im Container.")

    janitor = asyncio.create_task(_janitor())
    try:
        yield
    finally:
        janitor.cancel()
        jobs.stop()


app = FastAPI(title="Odoo Demodaten-Konsole", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=_lifespan)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if cookie_secure(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _require_requested_with(request: Request) -> None:
    if request.headers.get(REQUESTED_WITH_HEADER) != REQUESTED_WITH_VALUE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Ungültige Anfrage (fehlender Header).")


def get_session(request: Request):
    session = sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Nicht angemeldet.")
    return session


def get_session_csrf(request: Request):
    _require_requested_with(request)
    session = get_session(request)
    supplied = request.headers.get(CSRF_HEADER) or ""
    if not secrets.compare_digest(supplied, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="CSRF-Token fehlt oder ist ungültig.")
    return session


def _connected(session):
    if not session.connected:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Keine Odoo-Verbindung in dieser Sitzung.")
    return session


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    """Container liveness. Unauthenticated by necessity, so it names nothing:
    no target, no session count, no version."""
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/api/auth")
async def api_auth(request: Request, response: Response) -> Dict[str, Any]:
    _require_requested_with(request)
    body = await request.json()
    if not check_access_code(body.get("access_code")):
        # Same message either way — an unconfigured deployment and a wrong code
        # are not distinguishable to the caller.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Zugangscode ungültig.")
    session = sessions.create()
    response.set_cookie(
        SESSION_COOKIE, session.id,
        httponly=True, samesite="strict", secure=cookie_secure(request), path="/",
    )
    return {"ok": True, "csrf_token": session.csrf_token, **session.public_dict()}


@app.post("/api/logout")
def api_logout(request: Request, response: Response,
               session=Depends(get_session_csrf)) -> Dict[str, Any]:
    sessions.drop(session.id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/session")
def api_session(session=Depends(get_session)) -> Dict[str, Any]:
    return {"csrf_token": session.csrf_token, **session.public_dict()}


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------

@app.get("/api/defaults")
def api_defaults(session=Depends(get_session)) -> Dict[str, Any]:
    """Non-secret connection defaults so the UI can pre-fill and label the form.

    Reports *whether* a key default exists, never the key. Behind the access code
    because the demo hostname it carries is prospect-identifying.
    """
    return server_config.public_defaults()


@app.post("/api/connect")
async def api_connect(request: Request, session=Depends(get_session_csrf)) -> Dict[str, Any]:
    body = await request.json()

    # BETA: a blank field falls back to the operator's config.ini/environment.
    # Guard A still runs on the resolved URL, so a misconfigured default is
    # rejected exactly like a user-typed one — the fallback widens who may use
    # the key, never which hosts it may reach. See server_config for the
    # trade-off and the kill switch.
    resolved_url = server_config.apply("url", body.get("url"))
    try:
        base_url = security.validate_target_url(resolved_url)
        # S10/R10 (F2): the database field is no longer asked for in the UI —
        # on Odoo Online the database name IS the instance's subdomain label,
        # so it's derived from the URL Guard A already validated. The
        # server_config link stays in the chain (not just body -> derived):
        # without it, an operator's config.ini "db" value would become a
        # silently-ignored field, exactly the class of bug this sprint exists
        # to close. A body-supplied "db" still wins for self-hosted instances
        # or any database whose name doesn't match its subdomain.
        database = security.validate_database_name(
            server_config.apply("db", body.get("db"))
            or security.derive_database_name(base_url)
        )
    except security.TargetUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    odoo_key = server_config.apply("odoo_key", body.get("odoo_key"))
    llm_key = server_config.apply("llm_key", body.get("llm_key"))
    llm_model = server_config.apply("llm_model", body.get("llm_model"))
    if not odoo_key or not llm_key or not llm_model:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Odoo-Schlüssel, LLM-Schlüssel und LLM-Modell sind erforderlich "
                                   "— der Server hat dafür keine Voreinstellung.")

    result, _client, _llm = await asyncio.to_thread(
        connect_service.probe,
        base_url=base_url, database=database, odoo_key=odoo_key,
        llm_key=llm_key, llm_model=llm_model,
        llm_provider=body.get("llm_provider"),
    )

    if result.ok:
        # Credentials are held only here, in memory, for the session's lifetime.
        session.base_url = base_url
        session.database = database
        session.odoo_key = odoo_key
        session.llm_key = llm_key
        session.llm_model = llm_model
        session.llm_provider = result.llm_provider
        session.connect = result
    else:
        # A failed probe must not leave a half-usable session behind: /api/runs
        # gates on session.connected, and stale credentials would outlive a
        # connection the user was told had failed.
        session.connect = None
        session.base_url = session.database = None
        session.odoo_key = session.llm_key = None

    return result.as_public_dict()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@app.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
async def api_create_run(request: Request, session=Depends(get_session_csrf)) -> JSONResponse:
    _connected(session)
    body = await request.json()
    if "companies" in body:
        # S16/B4 (pre-merge cold review): the check below reads TOP-LEVEL
        # skip_master_data/use_existing — meaningless for this shape, where
        # both live per company block, and use_existing itself is superseded
        # by target.reuse_master_data (D11/D8b; build_context_list never
        # wires the old existing_partner_company_ids/existing_product_ids kwargs).
        # Without this, a block combining skip_master_data=True with a
        # target that isn't an existing company with reuse requested got
        # NO guard at all — silently ran with an empty pool instead of 400ing.
        for index, block in enumerate(body.get("companies") or []):
            if not isinstance(block, dict) or not block.get("skip_master_data"):
                continue
            target = block.get("target") or {}
            if not (isinstance(target, dict) and target.get("mode") == "existing"
                    and target.get("reuse_master_data")):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"companies[{index}]: 'skip_master_data' ohne bestehende Firma "
                           "mit 'Vorhandene Stammdaten wiederverwenden' ergibt einen Lauf "
                           "ohne Stammdaten — jedes Modul würde übersprungen.")
    elif body.get("skip_master_data") and not body.get("use_existing"):
        # The browser forces use_existing on when skip_master_data is ticked; a
        # direct API call could ask for neither new master data nor existing IDs,
        # which leaves every module with an empty pool and Pattern-5-skips the
        # whole run. Exactly the silent-disable class this sprint set out to close.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'skip_master_data' ohne 'use_existing' ergibt einen Lauf ohne "
                   "Stammdaten — jedes Modul würde übersprungen.")
    try:
        record = jobs.submit(session=session, payload=body)
    except run_config.ConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except AdmissionRefused as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED,
                        content={"run_id": record.run_id, **record.public_dict()})


@app.post("/api/preflight")
async def api_preflight(request: Request, session=Depends(get_session_csrf)) -> Dict[str, Any]:
    """Pre-flight summary: target, active modules, per-group record counts.

    Arithmetic from the config, not a dry run. Odoo's own automation creates more
    on top — the frontend names those separately rather than guessing counts.
    """
    _connected(session)
    body = await request.json()
    connect = session.connect
    # S16: mirrors JobQueue.submit()'s own dual-path bridge — "companies" in
    # the body means the new multi-company shape (D9/D11), its absence means
    # the legacy single-company shape, unchanged since before S16 existed.
    try:
        if "companies" in body:
            contexts_and_selected = run_config.build_context_list(
                body,
                language_name=connect.language_name,
                language_code=connect.language_code,
                installed_modules=connect.installed_modules,
                feature_flags=connect.feature_flags,
                model_access=connect.model_access,
            )
            labels = [
                (block.get("target") or {}).get("name") or f"Firma {i + 1}"
                for i, block in enumerate(body["companies"])
            ]
        else:
            ctx, selected = run_config.build_context(
                body,
                language_name=connect.language_name,
                language_code=connect.language_code,
                installed_modules=connect.installed_modules,
                feature_flags=connect.feature_flags,
                model_access=connect.model_access,
                existing_partner_company_ids=connect.existing_partner_company_ids,
                existing_product_ids=connect.existing_product_ids,
            )
            contexts_and_selected = [(ctx, selected)]
            labels = None
    except run_config.ConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    keys, counts = run_config.multi_company_preview(contexts_and_selected, labels)
    first_ctx = contexts_and_selected[0][0]
    return {
        "target": session.base_url,
        "database": session.database,
        "mode": first_ctx.criteria.mode,
        "industry": first_ctx.industry,
        "skip_master_data": first_ctx.skip_master_data,
        # S16: a multi-company key is "{index}:{module_code}" — MODULE_LABELS
        # only knows the bare module_code, so the prefix must come off
        # before the lookup (same fix as web/jobs.py's RunRecord.public_dict).
        "modules": [{"key": k, "label": run_config.MODULE_LABELS.get(_bare_module_key(k), k)}
                    for k in keys],
        "record_estimate": counts,
        "record_total": sum(counts.values()),
        "company_count": len(contexts_and_selected),
    }


def _own_run(run_id: str, session):
    record = jobs.get(run_id)
    if record is None or record.session_id != session.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden.")
    return record


def _feedback_run_context(run_id: Optional[str], session) -> Optional[Dict[str, Any]]:
    """Best-effort, ownership-checked run context for a feedback submission.

    Deliberately not _own_run: runs are pruned after FINISHED_RUN_TTL_SECONDS,
    and a stale or foreign run_id must not fail the feedback submission — it
    should just go in without context. Data-minimized: run_id/status/per-module
    status/error-count only, never target/database/error text/log content (see
    feedback._build_body) — not because it's redacted, but because it's
    unnecessary: the full run log is kept locally
    (run_journal.run_log_path/S11-D9) and retrievable by the run_id already
    carried here, so there is nothing to gain by also embedding it in a
    public GitHub issue. Keep this dict's key set exactly {run_id, status,
    modules, api_error_count} — a future edit that adds a "log" key here
    would defeat that property; tests/unit/test_web_feedback_unit.py asserts
    the exact key set for this reason.
    """
    if not run_id:
        return None
    record = jobs.get(run_id)
    if record is None or record.session_id != session.id:
        return None
    return {
        "run_id": record.run_id,
        "status": record.status,
        "modules": [{"key": m["key"], "status": m["status"]}
                    for m in record.public_dict()["modules"]],
        "api_error_count": len(record.api_errors),
    }


@app.get("/api/runs/{run_id}")
def api_run_status(run_id: str, session=Depends(get_session)) -> Dict[str, Any]:
    return _own_run(run_id, session).public_dict()


@app.get("/api/runs/{run_id}/events")
async def api_run_events(run_id: str, request: Request, session=Depends(get_session)):
    _own_run(run_id, session)  # ownership check: one session cannot read another's run
    stream = broker.get(run_id)
    if stream is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein Ereignisstrom.")

    try:
        cursor = int(request.headers.get("Last-Event-ID", "-1"))
    except ValueError:
        cursor = -1

    async def publisher():
        nonlocal cursor
        while True:
            if await request.is_disconnected():
                return
            for event in stream.since(cursor):
                cursor = event["id"]
                yield {"id": str(event["id"]), "event": event["type"],
                       "data": _json(event["data"])}
            if stream.closed and cursor >= stream.latest_id():
                return
            await asyncio.sleep(0.25)

    return EventSourceResponse(publisher())


@app.post("/api/runs/{run_id}/cleanup")
def api_run_cleanup(run_id: str, session=Depends(get_session_csrf)) -> Dict[str, Any]:
    """D7: unlink everything this run created, newest first."""
    _own_run(run_id, session)  # ownership check
    _connected(session)
    journal = RunJournal.load(run_id)
    if not journal.entries:
        return {"deleted": 0, "archived": 0, "failed": [], "skipped": 0, "total": 0}
    client = OdooJson2Client(session.base_url, session.database, session.odoo_key)
    return delete_run(client, journal)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

_FEEDBACK_MESSAGE_MAX = 4000
_FEEDBACK_RATE_LIMIT = 5
_FEEDBACK_RATE_WINDOW_SECONDS = 3600


def _check_feedback_rate_limit(session) -> None:
    """Per-session cap so one flaky auto-popup loop can't flood the repo."""
    now = time.time()
    session.feedback_timestamps = [
        t for t in session.feedback_timestamps if now - t < _FEEDBACK_RATE_WINDOW_SECONDS
    ]
    if len(session.feedback_timestamps) >= _FEEDBACK_RATE_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Zu viele Feedback-Einsendungen — bitte später erneut versuchen.")
    session.feedback_timestamps.append(now)


@app.post("/api/feedback", status_code=status.HTTP_201_CREATED)
async def api_feedback(request: Request, session=Depends(get_session_csrf)) -> Dict[str, Any]:
    """Creates a GitHub issue via a server-held PAT — no user GitHub login needed.

    No Depends(_connected): feedback ("the login screen is confusing") must
    work before any Odoo connection exists.
    """
    body = await request.json()
    category = body.get("category")
    if category not in feedback.CATEGORIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültige Kategorie.")
    message = (body.get("message") or "").strip()
    if not message or len(message) > _FEEDBACK_MESSAGE_MAX:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Nachricht muss zwischen 1 und {_FEEDBACK_MESSAGE_MAX} "
                                   "Zeichen lang sein.")
    _check_feedback_rate_limit(session)
    context = _feedback_run_context(body.get("run_id"), session)
    try:
        result = await asyncio.to_thread(
            feedback.create_github_issue, category, message, context)
    except feedback.GitHubConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except feedback.GitHubUpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
