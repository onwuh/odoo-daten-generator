"""Unit tests for odoo_client.py — B11 call_method fallback-3 regression guard.

Fallback 3 used to fire on ANY HTTPError from the call_kw/call/direct attempts,
silently dropping ids/args/kwargs and posting an empty payload — an empty
message_post(), an action_confirm() on nothing, masking the real error.
Mandatory per the architect's approval conditions:
  - non-empty ids/args/kwargs -> the real HTTPError propagates, no empty-payload
    fallback call is ever sent
  - all-empty ids/args/kwargs -> fallback 3 still fires (and can still succeed)
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

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err

    def json(self):
        return self._json_data


def _make_client_with_fake_post():
    client = OdooJson2Client("https://example.test", "db", "key")
    sent = []

    def fake_post(url, json=None, timeout=None):
        payload = json or {}
        sent.append((url, payload))
        # "fallback shape" = the empty payload3 sends: {} or just {"context": ...}
        is_fallback_shape = set(payload.keys()) <= {"context"}
        if is_fallback_shape:
            return _FakeResponse(200, json_data={"result": True})
        return _FakeResponse(404, text="not found")

    client.session.post = fake_post
    return client, sent


def run():
    results = []

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

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
