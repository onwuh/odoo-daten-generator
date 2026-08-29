"""Unit tests for odoo_client.py.

Two generations of coverage in this file, kept together deliberately:

1. B11 call_method fallback-3 regression guard (original, unchanged).
   Fallback 3 used to fire on ANY HTTPError from the call_kw/call/direct attempts,
   silently dropping ids/args/kwargs and posting an empty payload — an empty
   message_post(), an action_confirm() on nothing, masking the real error.
   Mandatory per the architect's approval conditions:
     - non-empty ids/args/kwargs -> the real HTTPError propagates, no empty-payload
       fallback call is ever sent
     - all-empty ids/args/kwargs -> fallback 3 still fires (and can still succeed)

2. S10/R10 error-bookkeeping rewrite (WP1 has_create_access, WP2 de-noising).
   `self.errors` used to grow by one entry per HTTP attempt — up to eight per
   logical operation, since the payload-format fallback chain multiplies each
   call. A live run found 8 of 14 reported errors were planned 404 probing, not
   real failures. Errors are now recorded once per failed LOGICAL OPERATION, via
   a `_record_failure` frame, carrying the most informative attempt rather than
   the last (least informative) one that happens to propagate.

Both generations share the same fake-session harness, and the harness's
allow_redirects assertion (Guard B) applies uniformly — a call site added by
either generation that forgets `allow_redirects=False` fails every test in this
file, not just the one that exercises it.
"""
import json as json_module
import os
import sys

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import odoo_client
from odoo_client import OdooJson2Client


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers if headers is not None else {"Content-Type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err

    def json(self):
        return self._json_data


def _odoo_error_body(message: str) -> str:
    return json_module.dumps({"error": {"data": {"message": message}}})


def _routing_hint_body(model: str, method: str) -> str:
    # The real 404 body the JSON/2 router sends for an unknown path shape —
    # planned probing, never the reason an operation failed.
    return json_module.dumps({
        "error": {"message": f"404 Not Found. Did you mean POST /json/2/{model}/{method} ?"}
    })


def _make_client_with_fake_post(responder=None):
    """responder(url, payload) -> _FakeResponse, or None to use the B11 default."""
    client = OdooJson2Client("https://example.test", "db", "key")
    sent = []

    def default_responder(url, payload):
        # "fallback shape" = the empty payload3 sends: {} or just {"context": ...}
        is_fallback_shape = set(payload.keys()) <= {"context"}
        if is_fallback_shape:
            return _FakeResponse(200, json_data={"result": True})
        return _FakeResponse(404, text="not found")

    active_responder = responder or default_responder

    def fake_post(url, json=None, timeout=None, allow_redirects=None):
        # Guard B: every POST must opt out of redirects. Asserting it here means
        # a call site added later without the kwarg fails this test rather than
        # quietly re-opening the SSRF hop. Applies to every test in this file,
        # old and new alike.
        assert allow_redirects is False, f"allow_redirects={allow_redirects!r} statt False"
        payload = json or {}
        sent.append((url, payload))
        return active_responder(url, payload)

    client.session.post = fake_post
    return client, sent


def run():
    results = []

    # ==================================================================
    # Generation 1 — B11 call_method fallback-3 regression guard
    # ==================================================================

    # ------------------------------------------------------------------
    # Non-empty ids: every real attempt fails -> the HTTPError must
    # propagate, and fallback 3's empty-payload shape must never be sent.
    # ------------------------------------------------------------------
    try:
        client, sent = _make_client_with_fake_post()
        raised = False
        try:
            client.call_method('crm.lead', 'message_post', ids=[42], kwargs={"body": "hi"})
        except requests.HTTPError:
            raised = True
        assert raised, "expected HTTPError to propagate for non-empty ids, but call_method returned normally"
        fallback_shaped = [p for _, p in sent if set(p.keys()) <= {"context"}]
        assert not fallback_shaped, f"B11 regressed: empty-payload fallback was sent despite non-empty ids: {fallback_shaped}"
        results.append((
            "call_method: non-empty ids -> error propagates, no empty-payload fallback",
            True, f"{len(sent)} attempts made, none empty-payload",
        ))
    except AssertionError as e:
        results.append(("call_method: non-empty ids -> error propagates, no empty-payload fallback", False, str(e)))

    # ------------------------------------------------------------------
    # Non-empty args (no ids) -> same guarantee.
    # ------------------------------------------------------------------
    try:
        client, sent = _make_client_with_fake_post()
        raised = False
        try:
            client.call_method('res.partner', 'some_method', args=[123])
        except requests.HTTPError:
            raised = True
        assert raised, "expected HTTPError to propagate for non-empty args"
        fallback_shaped = [p for _, p in sent if set(p.keys()) <= {"context"}]
        assert not fallback_shaped, f"B11 regressed: empty-payload fallback sent despite non-empty args: {fallback_shaped}"
        results.append(("call_method: non-empty args (no ids) -> error propagates, no empty-payload fallback", True, ""))
    except AssertionError as e:
        results.append(("call_method: non-empty args (no ids) -> error propagates, no empty-payload fallback", False, str(e)))

    # ------------------------------------------------------------------
    # All-empty ids/args/kwargs: fallback 3 must still fire (and can succeed) —
    # this is the one legitimate use of the empty-payload fallback.
    # ------------------------------------------------------------------
    try:
        client, sent = _make_client_with_fake_post()
        result = client.call_method('res.partner', 'some_method')
        assert result == {"result": True}, f"expected fallback 3 to succeed, got {result}"
        fallback_shaped = [p for _, p in sent if set(p.keys()) <= {"context"}]
        assert fallback_shaped, "fallback 3 never fired for the legitimate all-empty case"
        results.append((
            "call_method: all-empty ids/args/kwargs -> fallback 3 still fires and succeeds",
            True, f"{len(sent)} attempts made, fallback fired",
        ))
    except AssertionError as e:
        results.append(("call_method: all-empty ids/args/kwargs -> fallback 3 still fires and succeeds", False, str(e)))

    # ==================================================================
    # Generation 2 — S10/R10 error bookkeeping
    # ==================================================================

    # ------------------------------------------------------------------
    # One errors entry per failed operation; the first structured Odoo
    # message wins over the last, uninformative one; routing-hint 404s
    # are never selected; attempts is counted correctly.
    # ------------------------------------------------------------------
    try:
        def create_responder(url, payload):
            if "vals_list" in payload or "values" in payload:
                # Stages 1 and 3 both post to /{model}/create — planned probing.
                return _FakeResponse(404, text=_routing_hint_body("res.partner", "create"))
            if "args" in payload:
                # Stage 2: call/call_kw with args/kwargs — the informative one.
                return _FakeResponse(422, text=_odoo_error_body(
                    "You can not delete a confirmed sales order"))
            return _FakeResponse(422, text="")

        client, sent = _make_client_with_fake_post(create_responder)
        raised = False
        try:
            client.create('res.partner', {"name": "Test GmbH"})
        except requests.HTTPError:
            raised = True
        assert raised, "expected create() to raise when every variant fails"
        assert len(client.errors) == 1, f"expected exactly 1 error entry, got {len(client.errors)}: {client.errors}"
        entry = client.errors[0]
        assert entry["model"] == "res.partner", entry
        assert entry["method"] == "create", entry
        assert "You can not delete" in (entry["error_body"] or ""), \
            f"expected the informative message to win over the final placeholder, got: {entry['error_body']!r}"
        assert "Did you mean" not in (entry["error_body"] or ""), \
            f"a routing-hint 404 must never be selected as the reason: {entry['error_body']!r}"
        assert entry["attempts"] == len(sent), \
            f"attempts ({entry['attempts']}) must equal HTTP calls made ({len(sent)})"
        results.append((
            "create: one error per operation, informative message wins over final 422",
            True, f"{entry['attempts']} attempts, body={entry['error_body']!r}",
        ))
    except AssertionError as e:
        results.append(("create: one error per operation, informative message wins over final 422", False, str(e)))

    # ------------------------------------------------------------------
    # Success leaves no error entry — including the 401 retry path, which
    # used to leave a stale entry behind if it wasn't the very last one.
    # ------------------------------------------------------------------
    try:
        calls = {"n": 0}

        def unauthorized_then_ok(url, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResponse(401, text="unauthorized")
            return _FakeResponse(200, json_data=[{"id": 1}])

        client, sent = _make_client_with_fake_post(unauthorized_then_ok)
        result = client.search_read('res.partner', [], fields=["id"])
        assert result == [{"id": 1}], result
        assert client.errors == [], f"expected no error entry after eventual success, got: {client.errors}"
        results.append(("search_read: 401-then-success leaves no error entry", True, f"{calls['n']} attempts"))
    except AssertionError as e:
        results.append(("search_read: 401-then-success leaves no error entry", False, str(e)))

    # ------------------------------------------------------------------
    # create_batch's per-record fallback: one failing record among several
    # must produce exactly ONE error entry, not one from the batch frame
    # and a second from the inner create() frame.
    # ------------------------------------------------------------------
    try:
        def batch_responder(url, payload):
            if "vals_list" in payload and isinstance(payload["vals_list"], list) and len(payload["vals_list"]) > 1:
                # The whole-batch attempt: reject with a retryable shape (422)
                # so create_batch falls into its per-record fallback.
                return _FakeResponse(422, text="")
            # Per-record create(): let the record named "Boom GmbH" fail
            # everywhere, everyone else succeeds on the first (vals_list) shape.
            values = None
            if "vals_list" in payload and payload["vals_list"]:
                values = payload["vals_list"][0]
            elif "values" in payload:
                values = payload["values"]
            elif "args" in payload and payload["args"]:
                values = payload["args"][0]
            if values and values.get("name") == "Boom GmbH":
                return _FakeResponse(422, text="")
            return _FakeResponse(200, json_data=[7])

        client, sent = _make_client_with_fake_post(batch_responder)
        raised = False
        try:
            client.create_batch('res.partner', [
                {"name": "Erste GmbH"},
                {"name": "Boom GmbH"},
                {"name": "Dritte GmbH"},
            ])
        except requests.HTTPError:
            raised = True
        assert raised, "expected create_batch to propagate the failing record's error"
        assert len(client.errors) == 1, \
            f"expected exactly 1 error entry (no double-recording), got {len(client.errors)}: {client.errors}"
        results.append(("create_batch: failing record in per-record fallback records exactly once", True, ""))
    except AssertionError as e:
        results.append(("create_batch: failing record in per-record fallback records exactly once", False, str(e)))

    # ------------------------------------------------------------------
    # A 429 surviving create_batch's whole-batch attempt must NOT trigger
    # the per-record fallback — that would turn one batched call into N,
    # the exact anti-pattern batching exists to avoid.
    # ------------------------------------------------------------------
    try:
        def rate_limited_responder(url, payload):
            return _FakeResponse(429, text="rate limited")

        client, sent = _make_client_with_fake_post(rate_limited_responder)
        orig_sleep = odoo_client.time.sleep
        odoo_client.time.sleep = lambda *_a, **_k: None
        try:
            raised = False
            try:
                client.create_batch('res.partner', [{"name": "A"}, {"name": "B"}])
            except requests.HTTPError:
                raised = True
        finally:
            odoo_client.time.sleep = orig_sleep
        assert raised, "expected the 429 to propagate rather than be swallowed"
        per_record_attempts = [p for _, p in sent if isinstance(p.get("vals_list"), list)
                               and len(p["vals_list"]) == 1]
        assert not per_record_attempts, \
            f"429 must not trigger per-record fallback: {per_record_attempts}"
        results.append(("create_batch: 429 does not trigger per-record fallback", True, ""))
    except AssertionError as e:
        results.append(("create_batch: 429 does not trigger per-record fallback", False, str(e)))

    # ------------------------------------------------------------------
    # A refused redirect (Guard B) produces exactly one error entry with
    # status_code=None — no HTTP attempt was ever noted for it, since
    # _reject_redirect fires before the body is even inspected.
    # ------------------------------------------------------------------
    try:
        def redirecting_responder(url, payload):
            return _FakeResponse(302, text="", headers={"Location": "http://169.254.169.254/"})

        client, sent = _make_client_with_fake_post(redirecting_responder)
        raised = False
        try:
            client.model_method('res.partner', 'search_read', {"domain": []})
        except odoo_client.RedirectRefused:
            raised = True
        assert raised, "expected RedirectRefused to propagate"
        assert len(client.errors) == 1, f"expected exactly 1 error entry, got {client.errors}"
        assert client.errors[0]["status_code"] is None, client.errors[0]
        results.append(("model_method: refused redirect records one entry with status_code=None", True, ""))
    except AssertionError as e:
        results.append(("model_method: refused redirect records one entry with status_code=None", False, str(e)))

    # ------------------------------------------------------------------
    # A timeout (no HTTP response at all) produces one error entry with
    # status_code=None. Before this rewrite it produced no entry at all.
    # ------------------------------------------------------------------
    try:
        client, sent = _make_client_with_fake_post()

        def timeout_post(url, json=None, timeout=None, allow_redirects=None):
            assert allow_redirects is False
            raise requests.exceptions.Timeout("timed out")

        client.session.post = timeout_post
        raised = False
        try:
            client.model_method('res.partner', 'search_read', {"domain": []})
        except requests.exceptions.Timeout:
            raised = True
        assert raised, "expected Timeout to propagate"
        assert len(client.errors) == 1, f"expected exactly 1 error entry, got {client.errors}"
        assert client.errors[0]["status_code"] is None, client.errors[0]
        assert "timed out" in client.errors[0]["error_body"], client.errors[0]
        results.append(("model_method: timeout records one entry with status_code=None", True, ""))
    except AssertionError as e:
        results.append(("model_method: timeout records one entry with status_code=None", False, str(e)))

    # ==================================================================
    # has_create_access
    # ==================================================================

    # ------------------------------------------------------------------
    # Happy path: exactly one POST, true -> True.
    # ------------------------------------------------------------------
    try:
        def ok_responder(url, payload):
            assert payload == {"ids": [], "operation": "create"}, payload
            return _FakeResponse(200, json_data=True)

        client, sent = _make_client_with_fake_post(ok_responder)
        result = client.has_create_access('sale.order')
        assert result is True, result
        assert len(sent) == 1, f"expected exactly one POST, got {len(sent)}: {sent}"
        assert client.errors == [], f"a successful probe must not record an error: {client.errors}"
        results.append(("has_create_access: bare true -> True, exactly one POST", True, ""))
    except AssertionError as e:
        results.append(("has_create_access: bare true -> True, exactly one POST", False, str(e)))

    # ------------------------------------------------------------------
    # Wrapped-false shape must not be read as truthy: bool({"result": False})
    # is True in plain Python, which is exactly the trap this guards against.
    # ------------------------------------------------------------------
    try:
        def wrapped_false_responder(url, payload):
            return _FakeResponse(200, json_data={"result": False})

        client, sent = _make_client_with_fake_post(wrapped_false_responder)
        result = client.has_create_access('mrp.workcenter')
        assert result is False, f"expected False for a wrapped {{'result': False}}, got {result!r}"
        results.append(("has_create_access: wrapped {result: false} -> False, not truthy-dict", True, ""))
    except AssertionError as e:
        results.append(("has_create_access: wrapped {result: false} -> False, not truthy-dict", False, str(e)))

    # ------------------------------------------------------------------
    # A model that doesn't exist on this instance: 404 -> definitive False.
    # ------------------------------------------------------------------
    try:
        def not_found_responder(url, payload):
            return _FakeResponse(404, text="not found")

        client, sent = _make_client_with_fake_post(not_found_responder)
        result = client.has_create_access('does.not.exist')
        assert result is False, result
        assert len(sent) == 1, f"expected no variant fallback for has_access, got {len(sent)}: {sent}"
        results.append(("has_create_access: nonexistent model (404) -> False, no variant fallback", True, ""))
    except AssertionError as e:
        results.append(("has_create_access: nonexistent model (404) -> False, no variant fallback", False, str(e)))

    # ------------------------------------------------------------------
    # An indeterminate failure (429) must return True, not False: a rate
    # limit hit while probing must never look like "not allowed" and
    # silently disable a module that is actually fine.
    # ------------------------------------------------------------------
    try:
        def rate_limited_probe(url, payload):
            return _FakeResponse(429, text="rate limited")

        client, sent = _make_client_with_fake_post(rate_limited_probe)
        orig_sleep = odoo_client.time.sleep
        odoo_client.time.sleep = lambda *_a, **_k: None
        try:
            result = client.has_create_access('purchase.order')
        finally:
            odoo_client.time.sleep = orig_sleep
        assert result is True, f"a 429 during probing must read as 'unknown', not 'denied': got {result!r}"
        results.append(("has_create_access: 429 (rate limit) -> True (unknown, not denied)", True, ""))
    except AssertionError as e:
        results.append(("has_create_access: 429 (rate limit) -> True (unknown, not denied)", False, str(e)))

    # ------------------------------------------------------------------
    # Same for a timeout — no response at all.
    # ------------------------------------------------------------------
    try:
        client, sent = _make_client_with_fake_post()

        def timeout_probe(url, json=None, timeout=None, allow_redirects=None):
            assert allow_redirects is False
            raise requests.exceptions.Timeout("timed out")

        client.session.post = timeout_probe
        result = client.has_create_access('stock.quant')
        assert result is True, f"a timeout during probing must read as 'unknown', not 'denied': got {result!r}"
        assert client.errors == [], \
            f"has_create_access probes with record_error=False and must never leave an error entry: {client.errors}"
        results.append(("has_create_access: timeout -> True (unknown, not denied), no error entry", True, ""))
    except AssertionError as e:
        results.append(("has_create_access: timeout -> True (unknown, not denied), no error entry", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
