"""Unit tests for LLMService helpers (no real API calls)."""
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch, call

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from llm_service import LLMService


def _make_svc():
    """Create LLMService instance without real API client."""
    svc = LLMService.__new__(LLMService)
    svc.provider = "groq"
    svc.model_name = "test-model"
    svc.total_calls = 0
    svc.total_tokens = 0
    svc._client = MagicMock()
    return svc


def run():
    """Returns (all_passed, [(label, ok, detail), ...])"""
    results = []

    # ------------------------------------------------------------------
    # D1 — _extract_json edge cases
    # ------------------------------------------------------------------

    try:
        svc = _make_svc()
        result = svc._extract_json("```json\n[1,2,3]\n```")
        assert json.loads(result) == [1, 2, 3], f"Got: {result}"
        results.append(("_extract_json: ```json fence → clean JSON", True, ""))
    except Exception as e:
        results.append(("_extract_json: ```json fence → clean JSON", False, str(e)))

    try:
        svc = _make_svc()
        result = svc._extract_json("```\n[1]\n```")
        assert json.loads(result) == [1], f"Got: {result}"
        results.append(("_extract_json: plain ``` fence → clean JSON", True, ""))
    except Exception as e:
        results.append(("_extract_json: plain ``` fence → clean JSON", False, str(e)))

    try:
        svc = _make_svc()
        result = svc._extract_json("[1,2,3]")
        assert json.loads(result) == [1, 2, 3], f"Got: {result}"
        results.append(("_extract_json: no fence → passthrough", True, ""))
    except Exception as e:
        results.append(("_extract_json: no fence → passthrough", False, str(e)))

    try:
        svc = _make_svc()
        result = svc._extract_json("no json here")
        # No brackets found → fallback returns the stripped string; json.loads will fail
        # but _extract_json itself must not crash
        assert isinstance(result, str)
        results.append(("_extract_json: no brackets → no crash", True, result[:30]))
    except Exception as e:
        results.append(("_extract_json: no brackets → no crash", False, str(e)))

    try:
        svc = _make_svc()
        # None input → _extract_json is only called with str from _call, but guard here
        try:
            result = svc._extract_json(None)
            # If it doesn't crash, fine; result may be empty or None
            results.append(("_extract_json: None input → no crash", True, str(result)[:20]))
        except (AttributeError, TypeError):
            # Acceptable: caller (_call_json) already guards text is non-None
            results.append(("_extract_json: None input → AttributeError is acceptable", True, "caller guards None"))
    except Exception as e:
        results.append(("_extract_json: None input → no crash", False, str(e)))

    try:
        svc = _make_svc()
        result = svc._extract_json("```json\n```")
        # Empty fence → returns empty or stripped content
        assert isinstance(result, str)
        results.append(("_extract_json: empty fence → no crash", True, repr(result)))
    except Exception as e:
        results.append(("_extract_json: empty fence → no crash", False, str(e)))

    # ------------------------------------------------------------------
    # D2 — Cache hit/miss
    # ------------------------------------------------------------------

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            import llm_service as _llm_mod
            original_cache_dir = _llm_mod._CACHE_DIR

            from pathlib import Path
            tmp_cache = Path(tmpdir)

            svc = _make_svc()

            # Patch _CACHE_DIR on the module and on the instance methods
            with patch.object(_llm_mod, "_CACHE_DIR", tmp_cache):
                # Simulate a cache key
                key = "test_cache_key"
                data = [{"name": "Test"}]

                # Miss: file doesn't exist
                assert svc._cache_load(key) is None

                # Save
                svc._cache_save(key, data)

                # Hit: file exists
                loaded = svc._cache_load(key)
                assert loaded == data, f"Cache mismatch: {loaded}"

        results.append(("cache: miss → None, save → hit returns correct data", True, ""))
    except Exception as e:
        results.append(("cache: miss → None, save → hit returns correct data", False, str(e)))

    # ------------------------------------------------------------------
    # D3 — Retry on transient error (503)
    # ------------------------------------------------------------------

    try:
        svc = _make_svc()
        call_count = [0]

        def fake_raw_call(prompt):
            call_count[0] += 1
            if call_count[0] < 2:
                raise Exception("503 Service Unavailable")
            return ('[{"ok": true}]', 10, 20)

        with patch.object(svc, "_raw_call", side_effect=fake_raw_call), \
             patch("time.sleep"):  # skip actual sleep
            result = svc._call("test prompt")

        assert call_count[0] == 2, f"Expected 2 calls, got {call_count[0]}"
        assert result is not None
        results.append(("retry: 503 on attempt 1 → retries, succeeds on attempt 2", True, ""))
    except Exception as e:
        results.append(("retry: 503 on attempt 1 → retries, succeeds on attempt 2", False, str(e)))

    try:
        svc = _make_svc()
        call_count = [0]

        def fake_raw_call_all_fail(prompt):
            call_count[0] += 1
            raise Exception("503 Service Unavailable")

        with patch.object(svc, "_raw_call", side_effect=fake_raw_call_all_fail), \
             patch("time.sleep"):
            result = svc._call("test prompt")

        assert result is None, f"Expected None, got {result}"
        assert call_count[0] == 3, f"Expected 3 attempts, got {call_count[0]}"
        results.append(("retry: 503 all 3 attempts → returns None", True, ""))
    except Exception as e:
        results.append(("retry: 503 all 3 attempts → returns None", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
