"""Server-side session store — credentials live here and nowhere else.

Auth is a single shared access code with no user identity (settled: Google
Workspace OAuth would need an OAuth client from company IT, which is vetoed).
No emails are collected, so the GDPR surface collapses to IPs. The trade-off is
no attribution, acceptable because the server holds no secrets of its own:
every credential is user-supplied per session and discarded on expiry.

Credentials are memory-only by construction — never written to disk, never put
into a database, never logged, never echoed back in a response. The only thing
that leaves this module is the live client/LLM objects the worker uses.
"""
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SESSION_COOKIE = "odgen_session"
CSRF_HEADER = "X-CSRF-Token"

DEFAULT_TTL_SECONDS = 8 * 60 * 60


def access_code() -> Optional[str]:
    code = os.environ.get("ODOO_GENERATOR_ACCESS_CODE") or ""
    return code or None


def check_access_code(supplied: Optional[str]) -> bool:
    """Constant-time comparison against the configured shared code.

    An unset code means the deployment has not been configured; refuse rather
    than fall open.
    """
    expected = access_code()
    if not expected:
        return False
    return secrets.compare_digest(str(supplied or ""), expected)


@dataclass
class Session:
    id: str
    csrf_token: str
    created_at: float
    last_seen: float
    # --- credentials & live handles: memory only ---
    base_url: Optional[str] = None
    database: Optional[str] = None
    odoo_key: Optional[str] = None
    llm_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_provider: Optional[str] = None
    # --- connect results (safe to expose) ---
    connect: Optional[Any] = None
    run_ids: List[str] = field(default_factory=list)

    @property
    def connected(self) -> bool:
        return bool(self.connect and self.connect.ok)

    def public_dict(self) -> Dict[str, Any]:
        """Never includes a credential. The API has no endpoint that returns one."""
        return {
            "session": self.id[:8],
            "connected": self.connected,
            "target": self.base_url,
            "database": self.database,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
        }


class SessionStore:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.ttl = ttl_seconds
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self) -> Session:
        now = time.time()
        session = Session(
            id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            last_seen=now,
        )
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: Optional[str]) -> Optional[Session]:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if time.time() - session.last_seen > self.ttl:
                self._forget(session_id)
                return None
            session.last_seen = time.time()
            return session

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._forget(session_id)

    def _forget(self, session_id: str) -> None:
        """Caller holds the lock. Overwrites credential fields before dropping."""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.odoo_key = None
            session.llm_key = None

    def sweep(self) -> int:
        now = time.time()
        with self._lock:
            stale = [sid for sid, s in self._sessions.items() if now - s.last_seen > self.ttl]
            for sid in stale:
                self._forget(sid)
        return len(stale)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)
