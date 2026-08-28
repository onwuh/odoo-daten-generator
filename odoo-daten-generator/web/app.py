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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

import connect_service
import run_config
from logging_setup import configure_logging
from odoo_client import OdooJson2Client
from run_journal import RunJournal, delete_run, prune_journals, retention_days
from web import security
from web.jobs import AdmissionRefused, JobQueue
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


def cookie_secure() -> bool:
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
    if not os.environ.get("ODOO_GENERATOR_ACCESS_CODE"):
        logger.warning("[web] ODOO_GENERATOR_ACCESS_CODE ist nicht gesetzt — "
                       "jede Anmeldung wird abgelehnt.")
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
    if cookie_secure():
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
        httponly=True, samesite="strict", secure=cookie_secure(), path="/",
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

@app.post("/api/connect")
async def api_connect(request: Request, session=Depends(get_session_csrf)) -> Dict[str, Any]:
    body = await request.json()
    try:
        # Guard A (wrong target) and Guard B (SSRF) both run here, before a single
        # request leaves the server. The frontend mirrors Guard A for fast
        # feedback; this is the only authority.
        base_url = security.validate_target_url(body.get("url"))
        database = security.validate_database_name(body.get("db"))
    except security.TargetUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    odoo_key = (body.get("odoo_key") or "").strip()
    llm_key = (body.get("llm_key") or "").strip()
    llm_model = (body.get("llm_model") or "").strip()
    if not odoo_key or not llm_key or not llm_model:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Odoo-Schlüssel, LLM-Schlüssel und LLM-Modell sind erforderlich.")

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
    if body.get("skip_master_data") and not body.get("use_existing"):
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
    try:
        ctx, selected = run_config.build_context(
            body,
            language_name=connect.language_name,
            language_code=connect.language_code,
            llm_model_name=session.llm_model or "",
            installed_modules=connect.installed_modules,
            feature_flags=connect.feature_flags,
            existing_company_ids=connect.existing_company_ids,
            existing_product_ids=connect.existing_product_ids,
        )
    except run_config.ConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    keys = run_config.active_progress_keys(ctx, selected)
    counts = run_config.estimate_record_counts(ctx, selected)
    return {
        "target": session.base_url,
        "database": session.database,
        "mode": ctx.criteria.mode,
        "industry": ctx.industry,
        "skip_master_data": ctx.skip_master_data,
        "modules": [{"key": k, "label": run_config.MODULE_LABELS.get(k, k)} for k in keys],
        "record_estimate": counts,
        "record_total": sum(counts.values()),
    }


def _own_run(run_id: str, session):
    record = jobs.get(run_id)
    if record is None or record.session_id != session.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden.")
    return record


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
        return {"deleted": 0, "failed": [], "skipped": 0, "total": 0}
    client = OdooJson2Client(session.base_url, session.database, session.odoo_key)
    return delete_run(client, journal)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
