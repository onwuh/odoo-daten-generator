import json
import logging
import random
import time
import unicodedata
import requests
from typing import Any, Dict, List, Optional

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
# makes several hundred calls, and _post_with_variants multiplies each logical
# call by up to eight HTTP attempts. Without it every module after the ceiling
# fails in a way that reads like a code defect rather than a rate limit.
#
# Deliberately NOT part of the locked payload-format fallback chain below: it
# retries the same request unchanged and never alters the payload.
_RETRY_STATUSES = (429, 503)
_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 2.0


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


class OdooJson2Client:
    def __init__(self, base_url: str, database: str, api_key: str, user_agent: str = "odoo-daten-generator") -> None:
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
        self.errors: List[Dict[str, Any]] = []  # Track all API errors

    def _send(self, url: str, payload: Dict[str, Any], timeout: int = 60) -> "requests.Response":
        """One POST, retried on a rate-limit/unavailable answer.

        Every session.post in this class goes through here, so the Guard B
        opt-out of redirects is stated once and cannot be forgotten at a call
        site added later.
        """
        response = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            response = self.session.post(url, json=payload, timeout=timeout,
                                         allow_redirects=_ALLOW_REDIRECTS)
            if response.status_code not in _RETRY_STATUSES or attempt == _MAX_ATTEMPTS:
                return response
            delay = _retry_delay(response, attempt)
            logger.warning(f"[HTTP] {response.status_code} — Wiederholung "
                           f"{attempt}/{_MAX_ATTEMPTS - 1} in {delay:.1f}s")
            time.sleep(delay)
        return response

    def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        logger.info(f"[HTTP] POST {url}")
        logger.info(f"[HTTP] Payload keys: {list(payload.keys())}")
        response = self._send(url, payload)
        _reject_redirect(response)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            error_body = ""
            try:
                # Redacted once, used for BOTH the log line and the durable copy
                # in self.errors below — get_errors() feeds the run-summary API,
                # so redacting only the log would leave the read primitive open
                # through a different pipe.
                error_body = _redact_error_body(response)
                if error_body:
                    logger.warning(f"[HTTP] Error Body: {error_body}")
            except Exception:
                pass
            
            # Record the error
            error_info = {
                "url": url,
                "method": "POST",
                "status_code": response.status_code if response else None,
                "error_message": str(e),
                "error_body": error_body,
                "payload_keys": list(payload.keys())
            }
            self.errors.append(error_info)
            
            if response is not None and response.status_code == 401:
                # Retry without X-Odoo-Database (SaaS often infers DB from subdomain)
                orig_db = self.session.headers.pop("X-Odoo-Database", None)
                try:
                    resp2 = self._send(url, payload)
                    if resp2.status_code == 401 and orig_db:
                        # Retry with db query parameter
                        self.session.headers["X-Odoo-Database"] = orig_db
                        resp3 = self._send(f"{url}?db={self.database}", payload)
                        _reject_redirect(resp3)
                        resp3.raise_for_status()
                        if self.errors and self.errors[-1]["url"] == url:
                            self.errors.pop()
                        logger.info(f"[HTTP] Success after db query param: {resp3.status_code}")
                        return resp3.json()
                    _reject_redirect(resp2)
                    resp2.raise_for_status()
                    if self.errors and self.errors[-1]["url"] == url:
                        self.errors.pop()
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

    def _post_with_variants(self, paths: List[str], payload: Dict[str, Any]) -> Any:
        last_error: Optional[Exception] = None
        for p in paths:
            try:
                return self._post(p, payload)
            except requests.HTTPError as e:
                last_error = e
                # try with trailing slash variant too
                try:
                    if not p.endswith('/'):
                        return self._post(p + '/', payload)
                except requests.HTTPError as e2:
                    last_error = e2
                    continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("No paths provided for request")

    def model_method(self, model: str, method: str, payload: Dict[str, Any]) -> Any:
        # Prefer direct model path first (most endpoints exist there), then call_kw, then call
        return self._post_with_variants([
            f"/{model}/{method}",
            f"/call_kw/{model}/{method}",
            f"/call/{model}/{method}",
        ], payload)

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
        # Try documented JSON-2 example format first: vals_list
        vals_list_payload: Dict[str, Any] = {"vals_list": [values]}
        if context is not None:
            vals_list_payload["context"] = context
        try:
            result = self._post_with_variants([
                f"/{model}/create",
            ], vals_list_payload)
            if isinstance(result, list):
                return result[0]
            return int(result)
        except requests.HTTPError as e:
            # Fallback to call variants using args/kwargs
            if e.response is not None and e.response.status_code in (404, 422):
                call_payload: Dict[str, Any] = {"args": [values], "kwargs": {}}
                if context is not None:
                    call_payload["context"] = context
                try:
                    result2 = self._post_with_variants([
                        f"/call/{model}/create",
                        f"/call_kw/{model}/create",
                    ], call_payload)
                    if isinstance(result2, list):
                        return result2[0]
                    return int(result2)
                except requests.HTTPError as e2:
                    if e2.response is not None and e2.response.status_code in (404, 422):
                        # Last fallback to direct {values}
                        payload: Dict[str, Any] = {"values": values}
                        if context is not None:
                            payload["context"] = context
                        result3 = self._post_with_variants([
                            f"/{model}/create",
                        ], payload)
                        if isinstance(result3, list):
                            return result3[0]
                        return int(result3)
                    raise
            raise

    def create_batch(self, model: str, values_list: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None) -> List[int]:
        """Create multiple records in one API call using vals_list.

        Falls back to sequential individual creates if the server rejects batch mode.
        """
        if not values_list:
            return []
        payload: Dict[str, Any] = {"vals_list": values_list}
        if context is not None:
            payload["context"] = context
        try:
            result = self._post_with_variants([f"/{model}/create"], payload)
            if isinstance(result, list):
                return [int(r) for r in result]
            return [int(result)]
        except requests.HTTPError:
            # Fallback: create each record individually
            logger.warning(f"[HTTP] Batch create failed for {model}, falling back to sequential creates")
            ids = []
            for values in values_list:
                ids.append(self.create(model, values, context=context))
            return ids

    def write(self, model: str, ids: List[int], values: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> bool:
        # Direct JSON-2 expects 'vals' key
        payload: Dict[str, Any] = {"ids": ids, "vals": values}
        if context is not None:
            payload["context"] = context
        try:
            result = self._post_with_variants([
                f"/{model}/write",
            ], payload)
            return bool(result)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 422):
                # Fallback to call variants
                call_payload: Dict[str, Any] = {"args": [ids, values], "kwargs": {}}
                if context is not None:
                    call_payload["context"] = context
                result2 = self._post_with_variants([
                    f"/call_kw/{model}/write",
                    f"/call/{model}/write",
                ], call_payload)
                return bool(result2)
            raise

    def call_method(self, model: str, method: str, ids: Optional[List[int]] = None, args: Optional[List[Any]] = None, kwargs: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None) -> Any:
        # Helper to call model methods possibly on recordsets
        args = args or []
        kwargs = kwargs or {}
        # 1) Try direct endpoint with 'ids' payload (JSON-2 recordset pattern)
        if ids is not None:
            direct_payload: Dict[str, Any] = {"ids": ids}
            direct_payload.update(kwargs)
            if context is not None:
                direct_payload["context"] = context
            try:
                return self._post_with_variants([
                    f"/{model}/{method}",
                ], direct_payload)
            except requests.HTTPError as e:
                if not (e.response is not None and e.response.status_code in (404, 422)):
                    raise
        # 2) Try call_kw with args/kwargs
        call_payload: Dict[str, Any] = {"args": ([] if ids is None else [ids]) + args, "kwargs": kwargs}
        if context is not None:
            call_payload["context"] = context
        try:
            return self._post_with_variants([
                f"/call_kw/{model}/{method}",
                f"/call/{model}/{method}",
                f"/{model}/{method}",
            ], call_payload)
        except requests.HTTPError as e:
            # 3) Last fallback: direct without args/kwargs. Only safe when there
            # was never anything meaningful to send in the first place (B11) —
            # otherwise this silently drops ids/args/kwargs and fires an empty
            # call (e.g. message_post() with no message, action_confirm() on
            # nothing), masking the real error instead of surfacing it.
            if ids or args or kwargs:
                raise
            fallback_payload: Dict[str, Any] = {}
            if context is not None:
                fallback_payload["context"] = context
            return self._post_with_variants([
                f"/{model}/{method}",
            ], fallback_payload)

    def get_errors(self) -> List[Dict[str, Any]]:
        """Return a list of all API errors that occurred during execution."""
        return self.errors.copy()


