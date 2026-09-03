import contextlib
import json
import logging
import os
import random
import time
import unicodedata
import requests
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# Guard B (SSRF), server-side half. The other half is web/security.py's URL
# validation; these two constants and _redact_error_body() are what stops an
# allowed host from turning into a read/redirect primitive once the request is
# in flight.
#
# ALLOW_REDIRECTS: refused outright. Without this an allowed demo-*.odoo.com host
# can 302 the request onward to e.g. 169.254.169.254 while the URL validation
# still reads "passed" — the step that is usually skipped. Verified 2026-08-28
# against the live SaaS instance: none of the three call shapes below redirects,
# so a flat refusal costs nothing operationally.
_ALLOW_REDIRECTS = False

# Max characters of a *structured* Odoo error message kept for the run summary.
_ERROR_MESSAGE_LIMIT = 300

# Odoo SaaS rate-limits demo instances. Sustained write rate is about **1 req/s**;
# a token bucket absorbs a burst on top of that (measured 2026-08-28 against
# demo-pahu-test1.odoo.com: ~150 requests in ~15s before the bucket emptied).
# The answer is a bare HTML 429 with no Retry-After.
#
# The PRIMARY mitigation is not this backoff — it is batching, which is why
# create_batch exists, why D3 pushed it to every call site, and why test Pattern 8
# forbids per-record LLM calls in a loop. Never "fix" a 429 by adding retries to
# a loop that should have been one batched call.
#
# The backoff is the safety net for what batching cannot remove: a full run still
# makes several hundred calls. Without it every module after the ceiling fails in
# a way that reads like a code defect rather than a rate limit.
_RETRY_STATUSES = (429, 503)
_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 2.0

# D10 — proactive throttle. The retry/backoff above is reactive: it only
# kicks in after a request has already come back 429. Nothing before this
# stopped requests from bunching up faster than the documented ~1 req/s
# sustained ceiling in the first place, which is why three consecutive live
# suite runs each produced exactly one 429-retry-induced flake, in a
# different exact-POST-count assertion each time (2026-09-02, see D10 in
# ROADMAP.md). _send now sleeps just enough before each attempt to keep this
# client's own requests at least this far apart. Configurable via env var —
# not every target instance shares demo-test5's ceiling — read once per
# client at construction time (OdooJson2Client.__init__), not a frozen
# module constant: a test constructing its own client with
# min_request_interval=0 must not depend on when odoo_client itself first
# got imported relative to any env-var override.
_MIN_REQUEST_INTERVAL_ENV = "ODOO_GENERATOR_MIN_REQUEST_INTERVAL"


def _min_request_interval() -> float:
    try:
        return max(0.0, float(os.environ.get(_MIN_REQUEST_INTERVAL_ENV, "") or 1.0))
    except ValueError:
        return 1.0


# R5/WP1 — dynamic field manifest. FIELD_COMPAT_WHITELIST (odoo_actions.py) is
# hand-curated and already known-incomplete (e.g. res.partner's country_id/
# parent_id/type gap, found 2026-09-02). Rather than keep hand-editing it,
# capture what the codebase actually sends: set this env var and run
# tests/integration/test_suite.py once — every (model, field) pair that
# passes through create/create_batch/write/call_method gets recorded here,
# and test_suite.py dumps it to field_manifest.json on exit. Off by default
# (a plain run pays nothing — one bool check per call). The result is bounded
# by what the test suite actually exercises, not a claim of completeness.
_CAPTURE_FIELDS_ENV = "ODOO_GENERATOR_CAPTURE_FIELDS"
_capture_fields_enabled = os.environ.get(_CAPTURE_FIELDS_ENV) == "1"
_captured_fields: Dict[str, set] = {}


def _capture_fields(model: str, values: Any) -> None:
    if not _capture_fields_enabled or not isinstance(values, dict):
        return
    _captured_fields.setdefault(model, set()).update(values.keys())


def dump_captured_fields(path: str) -> None:
    """Write the (model -> sorted fields) manifest captured so far as JSON.
    Called by test_suite.py after a full run with ODOO_GENERATOR_CAPTURE_FIELDS=1
    set; a no-op call (empty manifest) if capture was never enabled."""
    manifest = {model: sorted(fields) for model, fields in _captured_fields.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


class RedirectRefused(requests.RequestException):
    """Raised when the target answers with a redirect (Guard B).

    raise_for_status() ignores 3xx, so without this an unfollowed redirect would
    fall through to response.json() and surface as an opaque JSONDecodeError
    instead of naming the actual security decision.
    """


def _reject_redirect(response: "requests.Response") -> None:
    if 300 <= response.status_code < 400:
        raise RedirectRefused(
            f"Ziel antwortete mit Weiterleitung {response.status_code} "
            f"(Location: {response.headers.get('Location', '?')}) — "
            "aus Sicherheitsgründen wird Weiterleitungen nicht gefolgt.",
            response=response,
        )


def _retry_delay(response: "requests.Response", attempt: int) -> float:
    """Honour Retry-After when present, else exponential backoff with jitter."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, min(60.0, float(header)))
        except ValueError:
            pass
    return _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)


def _printable(text: str) -> str:
    """Drop control and zero-width characters, then collapse whitespace.

    Odoo SaaS prefixes some error messages with runs of invisible characters
    (zero-width joiners, variation selectors, tag characters — a tracing
    watermark), which render as garbage in the log and the run summary while
    hiding the actual message. Anything left with no printable content falls
    through to the size/type placeholder.
    """
    stripped = "".join(
        ch for ch in text
        if ch.isprintable()
        and unicodedata.category(ch) != "Cf"          # format/zero-width joiners
        and not (0xFE00 <= ord(ch) <= 0xFE0F)          # variation selectors
        and not (0xE0100 <= ord(ch) <= 0xE01EF)        # variation selectors supplement
        and not (0xE0000 <= ord(ch) <= 0xE007F)        # tag characters
    )
    return " ".join(stripped.split())


def _redact_error_body(response: "requests.Response") -> str:
    """Reduce an HTTP error body to its structured Odoo message, or a placeholder.

    The raw body used to be logged and stored verbatim, and both copies reach a
    user-visible stream (the run log and GET /api/runs/{id}). That is a full read
    primitive for anything the server can reach. Only a recognised Odoo/JSON error
    message crosses the boundary now; any other payload is replaced by its size
    and content type, which is enough to debug "we got HTML back" without
    reproducing the HTML.
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
        message = None
        error = parsed.get("error")
        if isinstance(error, dict):
            data = error.get("data")
            if isinstance(data, dict):
                message = data.get("message")
            if not message:
                message = error.get("message")
        elif isinstance(error, str):
            message = error
        if not message:
            for key in ("message", "description", "detail"):
                value = parsed.get(key)
                if isinstance(value, str) and value:
                    message = value
                    break
        if isinstance(message, str) and message.strip():
            # Odoo's JSON/2 error object is
            # {"name", "message", "arguments", "timestamp", "context", "debug"} —
            # "debug" carries a full server-side traceback with file paths and is
            # deliberately dropped here; only "message" crosses the boundary.
            cleaned = _printable(message)
            if cleaned:
                return cleaned[:_ERROR_MESSAGE_LIMIT]
    content_type = (response.headers.get("Content-Type") or "unbekannt").split(";")[0]
    return f"<Antwortkörper unterdrückt: {len(raw)} Zeichen, {content_type}>"


# has_create_access answers a question with three possible outcomes, not two:
# yes, no, and "could not find out". Only these two statuses mean a definitive
# no. Everything else — a rate limit, a gateway error, a timeout — is unknown,
# and unknown must never be reported as "not allowed": callers disable modules on
# a False, so a 429 during probing would silently switch off half a run.
_ACCESS_DENIED_STATUSES = (403, 404)


def _select_attempt(attempts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the attempt that actually explains the failure.

    A logical operation now makes exactly one HTTP request in the common case
    — there is no cross-method payload-format chain left to pick between (see
    "JSON2 payload-format fallback chain — removed" in CLAUDE.md). This still
    matters for _post's own 401-retry dance, which can leave more than one
    attempt in a frame: the earliest one with a real (non-placeholder) body is
    the one that names the actual reason, not a later, less specific failure.
    """
    if not attempts:
        return None
    for attempt in attempts:
        body = attempt.get("body") or ""
        if body and not body.startswith("<"):
            return attempt
    return attempts[-1]


class OdooJson2Client:
    def __init__(self, base_url: str, database: str, api_key: str, user_agent: str = "odoo-daten-generator",
                 min_request_interval: Optional[float] = None) -> None:
        self.base_url = base_url.rstrip('/') + "/json/2"
        self.database = database
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "X-Odoo-Database": self.database,
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        # One entry per FAILED LOGICAL OPERATION (create, write, call_method …),
        # not per HTTP attempt — a single operation can still make more than
        # one HTTP attempt (the 401-retry dance in _post, or create_batch
        # falling back to per-record creates), and only the attempt that
        # actually explains the failure should end up here.
        self.errors: List[Dict[str, Any]] = []
        # Stack of in-flight logical operations; each frame collects the HTTP
        # attempts made inside it. A stack, not a single list, because
        # create_batch's fallback calls create() inside its own frame.
        self._attempt_frames: List[List[Dict[str, Any]]] = []
        # D10: last time this client actually sent a request (time.monotonic,
        # immune to wall-clock adjustments) — per-instance, not global/shared,
        # matching the single-client-per-run shape the rest of this class
        # already assumes.
        self._last_request_at: float = 0.0
        # Per-instance, not a frozen module constant read once at import —
        # lets a caller (chiefly tests exercising _send's retry loop with
        # time.sleep mocked to a no-op, where real time never advances
        # between attempts) pass 0 to disable throttling for just that
        # client, without a process-wide env var whose effect would depend
        # on import order. None (the default) reads the env var at
        # CONSTRUCTION time, not per-call — a real run's target ceiling
        # doesn't change mid-run.
        self._min_request_interval = (
            _min_request_interval() if min_request_interval is None else max(0.0, min_request_interval)
        )

    def _throttle(self) -> None:
        """D10: sleep just enough to keep this client's requests at least
        self._min_request_interval apart, proactively — unlike the
        retry/backoff below, this runs BEFORE a request ever goes out, so it
        can prevent a 429 instead of only reacting to one. Records the send
        time itself (not the time a response came back), since it's the
        request rate the target instance measures, not the round-trip.
        """
        if self._min_request_interval <= 0:
            return
        wait = self._min_request_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _send(self, url: str, payload: Dict[str, Any], timeout: int = 60) -> "requests.Response":
        """One POST, retried on a rate-limit/unavailable answer.

        Every session.post in this class goes through here, so the Guard B
        opt-out of redirects is stated once and cannot be forgotten at a call
        site added later.
        """
        response = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            self._throttle()
            response = self.session.post(url, json=payload, timeout=timeout,
                                         allow_redirects=_ALLOW_REDIRECTS)
            if response.status_code not in _RETRY_STATUSES or attempt == _MAX_ATTEMPTS:
                return response
            delay = _retry_delay(response, attempt)
            logger.warning(f"[HTTP] {response.status_code} — Wiederholung "
                           f"{attempt}/{_MAX_ATTEMPTS - 1} in {delay:.1f}s")
            time.sleep(delay)
        return response

    # -- failure bookkeeping ---------------------------------------------

    @contextlib.contextmanager
    def _record_failure(self, model: str, method: str) -> Iterator[List[Dict[str, Any]]]:
        """Scope one logical operation; on failure record exactly one error.

        The attempts are owned by this frame rather than carried on the raised
        exception: _post's own 401-retry dance can make several HTTP attempts
        before finally raising, and only the last of those would survive on
        the exception itself. Collecting here is what keeps
        run_journal._first_new_error able to name Odoo's real reason instead
        of a later, unrelated failure.
        """
        frame: List[Dict[str, Any]] = []
        self._attempt_frames.append(frame)
        errors_before = len(self.errors)
        try:
            yield frame
        except Exception as exc:
            # An inner frame (create_batch -> create) may already have reported
            # this failure; recording again would break "one entry per operation".
            if len(self.errors) == errors_before:
                self._append_error(model, method, frame, exc)
            raise
        finally:
            self._attempt_frames.pop()

    def _append_error(self, model: str, method: str,
                      attempts: List[Dict[str, Any]], exc: Exception) -> None:
        chosen = _select_attempt(attempts) or {}
        body = chosen.get("body")
        if not body:
            # No HTTP attempt to quote: a timeout, a refused redirect, a
            # connection error. These produced no error entry at all before.
            body = _printable(str(exc))[:_ERROR_MESSAGE_LIMIT]
        self.errors.append({
            "model": model,
            # The Odoo method name, not the HTTP verb it used to hold — the verb
            # is always POST and never told anyone anything.
            "method": method,
            "url": chosen.get("url"),
            "status_code": chosen.get("status_code"),
            "error_message": str(exc)[:_ERROR_MESSAGE_LIMIT],
            "error_body": body,
            "attempts": len(attempts),
        })

    def _note_attempt(self, url: str, status_code: Optional[int], message: str,
                      body: str, record_error: bool,
                      noted: List[Dict[str, Any]]) -> None:
        if not record_error or not self._attempt_frames:
            return
        attempt = {"url": url, "status_code": status_code,
                   "message": message, "body": body}
        self._attempt_frames[-1].append(attempt)
        noted.append(attempt)

    def _drop_attempts(self, noted: List[Dict[str, Any]]) -> None:
        """Forget attempts made by a _post that ultimately succeeded.

        Without this the 401 retry path leaves its failed first attempt in the
        frame, and a *later* failure in the same operation would be reported with
        that stale 401 as its reason.
        """
        if not noted or not self._attempt_frames:
            return
        stale = {id(a) for a in noted}
        frame = self._attempt_frames[-1]
        frame[:] = [a for a in frame if id(a) not in stale]
        noted.clear()

    def _raise_and_note(self, response: "requests.Response", url: str,
                        record_error: bool, noted: List[Dict[str, Any]]) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            error_body = ""
            try:
                # Redacted once, used for BOTH the log line and the durable copy
                # in the frame — get_errors() feeds the run-summary API, so
                # redacting only the log would leave the read primitive open
                # through a different pipe.
                error_body = _redact_error_body(response)
                if error_body:
                    if record_error:
                        logger.warning(f"[HTTP] Error Body: {error_body}")
                    else:
                        # Access probing expects 404s; a warning per probe would
                        # be pure noise in the run log.
                        logger.debug(f"[HTTP] Error Body: {error_body}")
            except Exception:
                pass
            self._note_attempt(url, response.status_code, str(exc),
                               error_body, record_error, noted)
            raise

    # -- transport --------------------------------------------------------

    def _post(self, path: str, payload: Dict[str, Any],
              record_error: bool = True) -> Any:
        url = f"{self.base_url}{path}"
        logger.info(f"[HTTP] POST {url}")
        logger.info(f"[HTTP] Payload keys: {list(payload.keys())}")
        noted: List[Dict[str, Any]] = []
        response = self._send(url, payload)
        _reject_redirect(response)
        try:
            self._raise_and_note(response, url, record_error, noted)
        except requests.HTTPError:
            if response.status_code == 401:
                # Retry without X-Odoo-Database (SaaS often infers DB from subdomain)
                orig_db = self.session.headers.pop("X-Odoo-Database", None)
                try:
                    resp2 = self._send(url, payload)
                    if resp2.status_code == 401 and orig_db:
                        # Retry with db query parameter
                        self.session.headers["X-Odoo-Database"] = orig_db
                        resp3 = self._send(f"{url}?db={self.database}", payload)
                        _reject_redirect(resp3)
                        self._raise_and_note(resp3, url, record_error, noted)
                        self._drop_attempts(noted)
                        logger.info(f"[HTTP] Success after db query param: {resp3.status_code}")
                        return resp3.json()
                    _reject_redirect(resp2)
                    self._raise_and_note(resp2, url, record_error, noted)
                    self._drop_attempts(noted)
                    logger.info(f"[HTTP] Success after removing X-Odoo-Database: {resp2.status_code}")
                    return resp2.json()
                finally:
                    # Always restore header so subsequent calls are not affected
                    if orig_db and "X-Odoo-Database" not in self.session.headers:
                        self.session.headers["X-Odoo-Database"] = orig_db
            raise
        # Some endpoints return JSON results directly, others wrap; assume JSON body is the result
        logger.info(f"[HTTP] {response.status_code} OK")
        return response.json()

    def model_method(self, model: str, method: str, payload: Dict[str, Any]) -> Any:
        with self._record_failure(model, method):
            return self._post(f"/{model}/{method}", payload)

    def search(self, model: str, domain: List[Any], context: Optional[Dict[str, Any]] = None) -> List[int]:
        payload: Dict[str, Any] = {"domain": domain}
        if context is not None:
            payload["context"] = context
        return self.model_method(model, "search", payload)

    def search_read(
        self,
        model: str,
        domain: List[Any],
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"domain": domain}
        if fields is not None:
            payload["fields"] = fields
        # NOTE: In the JSON 2 API, limit=0 means "return 0 records" (unlike the Odoo ORM
        # where limit=0 means "no limit"). We treat 0 as "omit the parameter" so the server
        # applies its own default (all records). Callers using limit=0 to mean "no limit"
        # therefore get the expected behaviour without any call-site changes.
        if limit is not None and limit != 0:
            payload["limit"] = limit
        if context is not None:
            payload["context"] = context
        return self.model_method(model, "search_read", payload)

    def create(self, model: str, values: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> int:
        _capture_fields(model, values)
        with self._record_failure(model, "create"):
            payload: Dict[str, Any] = {"vals_list": [values]}
            if context is not None:
                payload["context"] = context
            result = self._post(f"/{model}/create", payload)
            if isinstance(result, list):
                return result[0]
            return int(result)

    def create_batch(self, model: str, values_list: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None) -> List[int]:
        """Create multiple records in one API call using vals_list.

        Falls back to sequential individual creates if the server rejects batch mode.
        """
        if not values_list:
            return []
        if _capture_fields_enabled:
            for values in values_list:
                _capture_fields(model, values)
        payload: Dict[str, Any] = {"vals_list": values_list}
        if context is not None:
            payload["context"] = context
        with self._record_failure(model, "create_batch"):
            try:
                result = self._post(f"/{model}/create", payload)
                if isinstance(result, list):
                    return [int(r) for r in result]
                return [int(result)]
            except requests.HTTPError as e:
                # Only fall back on "this shape isn't accepted here" (404/422) —
                # a 429 that survived _send's retries must not turn one batched
                # call into N individual ones, exactly the anti-pattern batching
                # exists to avoid (CLAUDE.md: never "fix" a 429 by retrying in a
                # loop that should have been one batched call).
                if not (e.response is not None and e.response.status_code in (404, 422)):
                    raise
                # Fallback: create each record individually. Each self.create()
                # call opens its own _record_failure frame, so if a record fails
                # here it is already reported once — this outer frame's own
                # errors-unchanged check (see _record_failure) then skips
                # recording it a second time.
                logger.warning(f"[HTTP] Batch create failed for {model}, falling back to sequential creates")
                ids = []
                for values in values_list:
                    ids.append(self.create(model, values, context=context))
                return ids

    def write(self, model: str, ids: List[int], values: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> bool:
        _capture_fields(model, values)
        payload: Dict[str, Any] = {"ids": ids, "vals": values}
        if context is not None:
            payload["context"] = context
        with self._record_failure(model, "write"):
            return bool(self._post(f"/{model}/write", payload))

    def call_method(self, model: str, method: str, ids: Optional[List[int]] = None, kwargs: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None) -> Any:
        # JSON-2 dispatches by keyword only (matched to the target method's own
        # parameter names) plus the "ids" recordset marker — there is no
        # positional-args concept to send, which is why this no longer takes an
        # `args` parameter (B11's non-empty-args guard is now a TypeError at the
        # call site instead: see tests/unit/test_odoo_client_unit.py).
        # Keyed as "model#method", not just model: these are method kwargs
        # (e.g. message_post's body/subtype_id), not the model's own fields —
        # mixing them into the model's field-manifest entry would corrupt the
        # FIELD_COMPAT_WHITELIST comparison WP1 exists to sharpen.
        _capture_fields(f"{model}#{method}", kwargs)
        payload: Dict[str, Any] = dict(kwargs or {})
        if ids is not None:
            payload["ids"] = ids
        if context is not None:
            payload["context"] = context
        with self._record_failure(model, method):
            return self._post(f"/{model}/{method}", payload)

    def has_create_access(self, model: str) -> bool:
        """Whether the API-key user may create records on `model`.

        A single POST /{model}/has_access — deliberately NOT routed through
        model_method/call_method: those wrap every attempt in _record_failure,
        which always records a failure. Probing is expected to 404 for models
        that don't exist on this instance, and that must not clutter the error
        report the way a real operation's failure does — record_error=False on
        the direct _post call below is what suppresses it.

        Returns False only for a DEFINITIVE no: a real 403, or a 404 (the model
        does not exist here). Every other failure — 429, 5xx, timeout, a refused
        redirect — is unknown, not "not allowed", and returns True with a
        warning instead. Callers (odoo_actions.probe_model_access) disable a
        whole module on a False; treating "could not find out" as "not allowed"
        would let a rate limit hit during connect silently turn off modules that
        are actually fine — the exact silent-disable class this probe exists to
        close, reintroduced by the probe itself.
        """
        try:
            result = self._post(f"/{model}/has_access",
                                {"ids": [], "operation": "create"},
                                record_error=False)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in _ACCESS_DENIED_STATUSES:
                return False
            logger.warning(f"[access] has_access({model}, create) unklar "
                           f"(HTTP {status}): {exc}")
            return True
        except Exception as exc:
            logger.warning(f"[access] has_access({model}, create) unklar: {exc}")
            return True
        # Some endpoints wrap the boolean in {"result": ...} rather than
        # returning it bare (_post's own comment notes this ambiguity) — a bare
        # bool(result) would read a non-empty wrapper dict as truthy regardless
        # of its actual value, turning this probe into a no-op that looks like
        # it works.
        if isinstance(result, dict):
            return bool(result.get("result"))
        return result is True

    def get_errors(self) -> List[Dict[str, Any]]:
        """Return a list of all API errors that occurred during execution."""
        return self.errors.copy()
