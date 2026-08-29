"""Unit tests for odoo_client.py.

Two generations of coverage, kept together deliberately:

1. B11 call_method fallback-3 regression guard (original intent, adapted).
   The old fallback 3 fired on ANY HTTPError from the call_kw/call/direct
   attempts, silently dropping ids/args/kwargs and posting an empty payload —
   an empty message_post(), an action_confirm() on nothing, masking the real
   error. That three-stage chain is gone (see the format-fallback-collapse
   plan): call_method now sends exactly one request, built from ids+kwargs,
   which already reduces to the empty-payload shape when both are absent —
   no separate fallback stage needed. The two invariants B11 actually cared
   about survive as cases 1 and 2 below:
     - non-empty ids/kwargs -> the real HTTPError propagates, no empty-payload
       call is ever sent
     - all-empty ids/kwargs -> the (only) request can still be the empty shape
       and succeed
   The `args` parameter itself is gone (0 real call sites ever used it, and
   JSON-2 has no positional-args concept) — a caller that still passes
   `args=...` now gets a TypeError at the call site instead of a silently
   swallowed HTTPError. Case 3 below documents that as the new guard.

2. One-request-per-operation regression guards for create/create_batch/write/
   search_read/call_method — added alongside the fallback-chain removal so a
   future change can't silently reintroduce a multi-attempt chain.
"""
import os
import sys

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from odoo_client import OdooJson2Client


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err

    def json(self):
        return self._json_data


def _default_responder(url, payload):
    # "fallback shape" = the shape call_method sends when ids and kwargs are
    # both absent: {} or just {"context": ...}
    is_fallback_shape = set(payload.keys()) <= {"context"}
    if is_fallback_shape:
        return _FakeResponse(200, json_data={"result": True})
    return _FakeResponse(404, text="not found")


def _make_client_with_fake_post(responder=None):
    """responder(url, payload) -> _FakeResponse. Defaults to B11's fake server."""
    client = OdooJson2Client("https://example.test", "db", "key")
    sent = []
    active_responder = responder or _default_responder

    def fake_post(url, json=None, timeout=None, allow_redirects=None):
        # Guard B: every POST must opt out of redirects. Asserting it here means
        # a call site added later without the kwarg fails this test rather than
        # quietly re-opening the SSRF hop.
        assert allow_redirects is False, f"allow_redirects={allow_redirects!r} statt False"
        payload = json or {}
        sent.append((url, payload))
        return active_responder(url, payload)

    client.session.post = fake_post
    return client, sent


def run():
    results = []

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

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
