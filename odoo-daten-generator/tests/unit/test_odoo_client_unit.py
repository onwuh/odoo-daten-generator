"""Unit tests for odoo_client.py.

Three things are covered here, kept together deliberately:

1. B11 call_method fallback-3 regression guard (adapted). The old fallback 3
   fired on ANY HTTPError from the call_kw/call/direct attempts, silently
   dropping ids/args/kwargs and posting an empty payload — an empty
   message_post(), an action_confirm() on nothing, masking the real error.
   That three-stage chain is gone (the format-fallback-collapse work):
   call_method now sends exactly one request, built from ids+kwargs, which
   already reduces to the empty-payload shape when both are absent — no
   separate fallback stage needed. The two invariants B11 actually cared
   about survive as the first two cases below:
     - non-empty ids/kwargs -> the real HTTPError propagates, no empty-payload
       call is ever sent
     - all-empty ids/kwargs -> the (only) request can still be the empty shape
       and succeed
   The `args` parameter itself is gone (0 real call sites ever used it, and
   JSON-2 has no positional-args concept) — a caller that still passes
   `args=...` now gets a TypeError at the call site instead of a silently
   swallowed HTTPError.

2. One-request-per-operation regression guards for create/create_batch/write/
   search_read/call_method, so a future change can't silently reintroduce a
   multi-attempt chain.

3. S10/R10 error-bookkeeping (WP1 has_create_access, WP2 de-noising). Errors
   are recorded once per failed LOGICAL OPERATION via a `_record_failure`
   frame, not once per HTTP attempt — relevant now mainly for `_post`'s own
   401-retry dance, which can still make more than one HTTP attempt inside a
   single operation.

All tests share the same fake-session harness, and the harness's
allow_redirects assertion (Guard B) applies uniformly — a call site added
later that forgets `allow_redirects=False` fails every test in this file,
not just the one that exercises it.
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


def _default_responder(url, payload):
    # "fallback shape" = the shape call_method sends when ids and kwargs are
    # both absent: {} or just {"context": ...}
    is_fallback_shape = set(payload.keys()) <= {"context"}
    if is_fallback_shape:
        return _FakeResponse(200, json_data={"result": True})
    return _FakeResponse(404, text="not found")


def _make_client_with_fake_post(responder=None):
    """responder(url, payload) -> _FakeResponse. Defaults to B11's fake server."""
    # D10: min_request_interval=0 — these tests fake session.post directly and
    # often mock time.sleep to a no-op to assert retry/backoff behavior, so
    # real time never advances between attempts; the proactive throttle would
    # otherwise see "no time passed" and sleep on every attempt too.
    client = OdooJson2Client("https://example.test", "db", "key", min_request_interval=0)
    sent = []
    active_responder = responder or _default_responder

    def fake_post(url, json=None, timeout=None, allow_redirects=None):
        # Guard B: every POST must opt out of redirects. Asserting it here means
        # a call site added later without the kwarg fails this test rather than
        # quietly re-opening the SSRF hop. Applies to every test in this file.
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
    # Non-empty ids: the single request fails -> the HTTPError must
    # propagate. No second, empty-payload request may follow it.
    # ------------------------------------------------------------------
    try:
        client, sent = _make_client_with_fake_post()
        raised = False
        try:
            client.call_method('crm.lead', 'message_post', ids=[42], kwargs={"body": "hi"})
        except requests.HTTPError:
            raised = True
        assert raised, "expected HTTPError to propagate for non-empty ids, but call_method returned normally"
        assert len(sent) == 1, f"expected exactly one request, got {len(sent)}: {sent}"
        fallback_shaped = [p for _, p in sent if set(p.keys()) <= {"context"}]
        assert not fallback_shaped, f"empty-payload request was sent despite non-empty ids: {fallback_shaped}"
        results.append((
            "call_method: non-empty ids -> error propagates, exactly one request, none empty-payload",
            True, f"{len(sent)} request made",
        ))
    except AssertionError as e:
        results.append(("call_method: non-empty ids -> error propagates, exactly one request, none empty-payload", False, str(e)))

    # ------------------------------------------------------------------
    # All-empty ids/kwargs: the one request IS the empty shape, and can
    # succeed — this used to require a dedicated "fallback 3"; now it's
    # just what building payload = kwargs + optional ids naturally produces.
    # ------------------------------------------------------------------
    try:
        client, sent = _make_client_with_fake_post()
        result = client.call_method('res.partner', 'some_method')
        assert result == {"result": True}, f"expected the empty-shape request to succeed, got {result}"
        assert len(sent) == 1, f"expected exactly one request, got {len(sent)}: {sent}"
        fallback_shaped = [p for _, p in sent if set(p.keys()) <= {"context"}]
        assert fallback_shaped, "the all-empty case did not produce the empty-payload shape"
        results.append((
            "call_method: all-empty ids/kwargs -> the one request is empty-shaped and succeeds",
            True, f"{len(sent)} request made",
        ))
    except AssertionError as e:
        results.append(("call_method: all-empty ids/kwargs -> the one request is empty-shaped and succeeds", False, str(e)))

    # ------------------------------------------------------------------
    # args is gone: a caller that still passes it gets a TypeError at the
    # call site, not a silently-swallowed HTTPError from a dead fallback
    # path. This is B11's old "never silently drop meaningful data into an
    # empty call" guarantee, now enforced by Python's own argument binding
    # instead of by call_method's code.
    # ------------------------------------------------------------------
    try:
        client, sent = _make_client_with_fake_post()
        raised = False
        try:
            client.call_method('res.partner', 'some_method', args=[123])
        except TypeError:
            raised = True
        assert raised, "expected TypeError: call_method no longer accepts 'args'"
        assert not sent, f"no request should have been attempted before the TypeError: {sent}"
        results.append(("call_method: args= raises TypeError, no request attempted", True, ""))
    except AssertionError as e:
        results.append(("call_method: args= raises TypeError, no request attempted", False, str(e)))

    # ==================================================================
    # One request per operation — regression guard against the removed
    # payload-format fallback chain silently coming back.
    # ==================================================================

    # ------------------------------------------------------------------
    # create: exactly one POST, {"vals_list": [values]}.
    # ------------------------------------------------------------------
    try:
        def create_responder(url, payload):
            assert payload.get("vals_list") == [{"name": "Test GmbH"}], payload
            return _FakeResponse(200, json_data=[7])

        client, sent = _make_client_with_fake_post(create_responder)
        rec_id = client.create('res.partner', {"name": "Test GmbH"})
        assert rec_id == 7, rec_id
        assert len(sent) == 1, f"expected exactly one POST, got {len(sent)}: {sent}"
        results.append(("create: exactly one POST with vals_list", True, ""))
    except AssertionError as e:
        results.append(("create: exactly one POST with vals_list", False, str(e)))

    # ------------------------------------------------------------------
    # create_batch: exactly one POST, {"vals_list": [...]}.
    # ------------------------------------------------------------------
    try:
        def batch_responder(url, payload):
            assert payload.get("vals_list") == [{"name": "A"}, {"name": "B"}], payload
            return _FakeResponse(200, json_data=[1, 2])

        client, sent = _make_client_with_fake_post(batch_responder)
        ids = client.create_batch('res.partner', [{"name": "A"}, {"name": "B"}])
        assert ids == [1, 2], ids
        assert len(sent) == 1, f"expected exactly one POST, got {len(sent)}: {sent}"
        results.append(("create_batch: exactly one POST with vals_list", True, ""))
    except AssertionError as e:
        results.append(("create_batch: exactly one POST with vals_list", False, str(e)))

    # ------------------------------------------------------------------
    # write: exactly one POST, {"ids": [...], "vals": {...}}.
    # ------------------------------------------------------------------
    try:
        def write_responder(url, payload):
            assert payload == {"ids": [5], "vals": {"name": "Neu"}}, payload
            return _FakeResponse(200, json_data=True)

        client, sent = _make_client_with_fake_post(write_responder)
        result = client.write('res.partner', [5], {"name": "Neu"})
        assert result is True, result
        assert len(sent) == 1, f"expected exactly one POST, got {len(sent)}: {sent}"
        results.append(("write: exactly one POST with ids+vals", True, ""))
    except AssertionError as e:
        results.append(("write: exactly one POST with ids+vals", False, str(e)))

    # ------------------------------------------------------------------
    # search_read: exactly one POST, direct /{model}/search_read path
    # (no call_kw/call variants attempted first).
    # ------------------------------------------------------------------
    try:
        def search_read_responder(url, payload):
            assert url.endswith("/res.partner/search_read"), url
            assert payload.get("domain") == [], payload
            return _FakeResponse(200, json_data=[{"id": 1}])

        client, sent = _make_client_with_fake_post(search_read_responder)
        result = client.search_read('res.partner', [], fields=["id"])
        assert result == [{"id": 1}], result
        assert len(sent) == 1, f"expected exactly one POST, got {len(sent)}: {sent}"
        results.append(("search_read: exactly one POST, direct path", True, ""))
    except AssertionError as e:
        results.append(("search_read: exactly one POST, direct path", False, str(e)))

    # ------------------------------------------------------------------
    # call_method: exactly one POST, direct /{model}/{method} path with
    # ids+kwargs flattened together (no call_kw/call variants attempted).
    # ------------------------------------------------------------------
    try:
        def call_method_responder(url, payload):
            assert url.endswith("/sale.order/action_confirm"), url
            assert payload == {"ids": [9], "extra": "x"}, payload
            return _FakeResponse(200, json_data=True)

        client, sent = _make_client_with_fake_post(call_method_responder)
        result = client.call_method('sale.order', 'action_confirm', ids=[9], kwargs={"extra": "x"})
        assert result is True, result
        assert len(sent) == 1, f"expected exactly one POST, got {len(sent)}: {sent}"
        results.append(("call_method: exactly one POST, ids+kwargs flattened", True, ""))
    except AssertionError as e:
        results.append(("call_method: exactly one POST, ids+kwargs flattened", False, str(e)))

    # ==================================================================
    # Generation 2 — S10/R10 error bookkeeping
    # ==================================================================

    # ------------------------------------------------------------------
    # create: on failure, exactly one error entry with the real structured
    # message. Used to test a 3-stage payload-format chain (routing-hint
    # 404s on stages 1/3, the informative message on stage 2) — that chain
    # is gone, create() now makes exactly one request, so there is nothing
    # left to pick between; this checks the single attempt is still
    # recorded correctly.
    # ------------------------------------------------------------------
    try:
        def failing_create_responder(url, payload):
            return _FakeResponse(422, text=_odoo_error_body(
                "You can not delete a confirmed sales order"))

        client, sent = _make_client_with_fake_post(failing_create_responder)
        raised = False
        try:
            client.create('res.partner', {"name": "Test GmbH"})
        except requests.HTTPError:
            raised = True
        assert raised, "expected create() to raise on failure"
        assert len(sent) == 1, f"expected exactly one POST, got {len(sent)}: {sent}"
        assert len(client.errors) == 1, f"expected exactly 1 error entry, got {len(client.errors)}: {client.errors}"
        entry = client.errors[0]
        assert entry["model"] == "res.partner", entry
        assert entry["method"] == "create", entry
        assert "You can not delete" in (entry["error_body"] or ""), entry
        assert entry["attempts"] == 1, entry
        results.append((
            "create: on failure, exactly one error entry with the real message",
            True, f"body={entry['error_body']!r}",
        ))
    except AssertionError as e:
        results.append(("create: on failure, exactly one error entry with the real message", False, str(e)))

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
        def batch_fallback_responder(url, payload):
            vals_list = payload.get("vals_list") or []
            if len(vals_list) > 1:
                # The whole-batch attempt: reject with a retryable shape (422)
                # so create_batch falls into its per-record fallback.
                return _FakeResponse(422, text="")
            # Per-record create(): let the record named "Boom GmbH" fail
            # everywhere, everyone else succeeds.
            values = vals_list[0] if vals_list else None
            if values and values.get("name") == "Boom GmbH":
                return _FakeResponse(422, text="")
            return _FakeResponse(200, json_data=[7])

        client, sent = _make_client_with_fake_post(batch_fallback_responder)
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

    # ==================================================================
    # S11/R5 WP1 — dynamic field manifest capture. Env-gated: off by default
    # (no bookkeeping on a normal run), records every field sent through
    # create/create_batch/write/call_method when enabled. call_method's
    # kwargs land under a separate "model#method" key — they're method
    # parameters (e.g. message_post's body), not the model's own fields, and
    # mixing them in would corrupt the FIELD_COMPAT_WHITELIST comparison
    # WP1 exists to sharpen.
    # ==================================================================
    try:
        orig_enabled = odoo_client._capture_fields_enabled
        orig_captured = odoo_client._captured_fields
        odoo_client._capture_fields_enabled = False
        odoo_client._captured_fields = {}
        try:
            client, _sent = _make_client_with_fake_post(lambda url, payload: _FakeResponse(200, json_data=1))
            client.create('res.partner', {"name": "Test", "email": "a@b.test"})
            assert odoo_client._captured_fields == {}, \
                f"capture must stay a no-op when disabled: {odoo_client._captured_fields}"
            results.append(("field capture: disabled by default -> no-op", True, ""))
        finally:
            odoo_client._capture_fields_enabled = orig_enabled
            odoo_client._captured_fields = orig_captured
    except AssertionError as e:
        results.append(("field capture: disabled by default -> no-op", False, str(e)))

    try:
        orig_enabled = odoo_client._capture_fields_enabled
        orig_captured = odoo_client._captured_fields
        odoo_client._capture_fields_enabled = True
        odoo_client._captured_fields = {}
        try:
            client, _sent = _make_client_with_fake_post(lambda url, payload: _FakeResponse(200, json_data=1))
            client.create('res.partner', {"name": "Test", "email": "a@b.test"})
            client.write('res.partner', [1], {"phone": "+49 123"})
            client.create_batch('product.product', [{"list_price": 9.0}, {"name": "P"}])
            client.call_method('crm.lead', 'message_post', ids=[1], kwargs={"body": "hi"})
            captured = odoo_client._captured_fields
            assert captured.get('res.partner') == {"name", "email", "phone"}, captured.get('res.partner')
            assert captured.get('product.product') == {"list_price", "name"}, captured.get('product.product')
            assert captured.get('crm.lead#message_post') == {"body"}, captured.get('crm.lead#message_post')
            assert 'crm.lead' not in captured, "method kwargs must not land under the bare model key"
            results.append((
                "field capture: enabled -> records create/write/create_batch/call_method fields",
                True, "",
            ))
        finally:
            odoo_client._capture_fields_enabled = orig_enabled
            odoo_client._captured_fields = orig_captured
    except AssertionError as e:
        results.append((
            "field capture: enabled -> records create/write/create_batch/call_method fields",
            False, str(e),
        ))

    try:
        import tempfile
        orig_captured = odoo_client._captured_fields
        odoo_client._captured_fields = {"res.partner": {"name", "email"}}
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                tmp_path = tmp.name
            odoo_client.dump_captured_fields(tmp_path)
            with open(tmp_path, encoding="utf-8") as f:
                manifest = json_module.load(f)
            assert manifest == {"res.partner": ["email", "name"]}, manifest
            results.append(("field capture: dump_captured_fields writes sorted JSON manifest", True, ""))
        finally:
            odoo_client._captured_fields = orig_captured
            if tmp_path:
                os.remove(tmp_path)
    except AssertionError as e:
        results.append(("field capture: dump_captured_fields writes sorted JSON manifest", False, str(e)))

    # ==================================================================
    # D10 — proactive throttle (_throttle / min_request_interval)
    # ==================================================================

    # A fresh client's first _throttle() call must not sleep — there is no
    # prior request to space out from (Pattern 1-style empty-state guard).
    try:
        client = OdooJson2Client("https://example.test", "db", "key", min_request_interval=1.0)
        orig_sleep = odoo_client.time.sleep
        slept = []
        odoo_client.time.sleep = lambda s: slept.append(s)
        try:
            client._throttle()
        finally:
            odoo_client.time.sleep = orig_sleep
        assert slept == [], slept
        results.append(("_throttle: first call on a fresh client never sleeps", True, ""))
    except AssertionError as e:
        results.append(("_throttle: first call on a fresh client never sleeps", False, str(e)))

    # Two calls in quick succession: the second must sleep roughly the
    # configured interval, since (mocked) time hasn't actually advanced.
    try:
        client = OdooJson2Client("https://example.test", "db", "key", min_request_interval=1.0)
        orig_sleep = odoo_client.time.sleep
        slept = []
        odoo_client.time.sleep = lambda s: slept.append(s)
        try:
            client._throttle()
            client._throttle()
        finally:
            odoo_client.time.sleep = orig_sleep
        assert len(slept) == 1, slept
        assert 0.9 <= slept[0] <= 1.0, slept
        results.append(("_throttle: back-to-back calls sleep ~the configured interval", True, f"{slept}"))
    except AssertionError as e:
        results.append(("_throttle: back-to-back calls sleep ~the configured interval", False, str(e)))

    # min_request_interval=0 disables it outright — no sleep, no matter how
    # close together the calls are.
    try:
        client = OdooJson2Client("https://example.test", "db", "key", min_request_interval=0)
        orig_sleep = odoo_client.time.sleep
        slept = []
        odoo_client.time.sleep = lambda s: slept.append(s)
        try:
            client._throttle()
            client._throttle()
            client._throttle()
        finally:
            odoo_client.time.sleep = orig_sleep
        assert slept == [], slept
        results.append(("_throttle: min_request_interval=0 disables throttling entirely (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("_throttle: min_request_interval=0 disables throttling entirely (Pattern 3)", False, str(e)))

    # Explicit negative value clamps to 0 rather than trusting the caller —
    # same defensive posture as data_factory's own pct clamps.
    try:
        client = OdooJson2Client("https://example.test", "db", "key", min_request_interval=-5)
        assert client._min_request_interval == 0, client._min_request_interval
        results.append(("OdooJson2Client: negative min_request_interval clamps to 0", True, ""))
    except AssertionError as e:
        results.append(("OdooJson2Client: negative min_request_interval clamps to 0", False, str(e)))

    # min_request_interval=None (the default) reads the env var at
    # construction time — an explicit value must win, an unset/invalid env
    # var must fall back to the documented ~1 req/s default.
    try:
        orig_env = os.environ.get(odoo_client._MIN_REQUEST_INTERVAL_ENV)
        try:
            os.environ["ODOO_GENERATOR_MIN_REQUEST_INTERVAL"] = "2.5"
            client_env = OdooJson2Client("https://example.test", "db", "key")
            assert client_env._min_request_interval == 2.5, client_env._min_request_interval

            client_explicit = OdooJson2Client("https://example.test", "db", "key", min_request_interval=0.3)
            assert client_explicit._min_request_interval == 0.3, client_explicit._min_request_interval

            os.environ["ODOO_GENERATOR_MIN_REQUEST_INTERVAL"] = "not-a-number"
            client_invalid = OdooJson2Client("https://example.test", "db", "key")
            assert client_invalid._min_request_interval == 1.0, client_invalid._min_request_interval

            del os.environ["ODOO_GENERATOR_MIN_REQUEST_INTERVAL"]
            client_unset = OdooJson2Client("https://example.test", "db", "key")
            assert client_unset._min_request_interval == 1.0, client_unset._min_request_interval
        finally:
            if orig_env is None:
                os.environ.pop("ODOO_GENERATOR_MIN_REQUEST_INTERVAL", None)
            else:
                os.environ["ODOO_GENERATOR_MIN_REQUEST_INTERVAL"] = orig_env
        results.append(("OdooJson2Client: min_request_interval=None reads env var at construction, explicit wins", True, ""))
    except AssertionError as e:
        results.append(("OdooJson2Client: min_request_interval=None reads env var at construction, explicit wins", False, str(e)))

    # End-to-end: _send itself calls _throttle before every attempt,
    # including retries — not just the first.
    try:
        client, sent = _make_client_with_fake_post()
        client._min_request_interval = 1.0
        throttle_calls = []
        orig_throttle = client._throttle
        client._throttle = lambda: (throttle_calls.append(1), orig_throttle())[-1]
        orig_sleep = odoo_client.time.sleep
        odoo_client.time.sleep = lambda *_a, **_k: None
        try:
            client._send("https://example.test/json/2/res.partner/search_read", {})
        finally:
            odoo_client.time.sleep = orig_sleep
        assert len(throttle_calls) == 1, throttle_calls  # one attempt, one throttle check
        results.append(("_send: calls _throttle before sending", True, ""))
    except AssertionError as e:
        results.append(("_send: calls _throttle before sending", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
