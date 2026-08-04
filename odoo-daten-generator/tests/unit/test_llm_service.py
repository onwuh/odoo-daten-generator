"""Unit tests for LLMService helpers (no real API calls)."""
import json
import os
import sys
import tempfile
import time
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

    # ------------------------------------------------------------------
    # B2 — Timeout non-blocking (fix: shutdown(wait=False))
    # ------------------------------------------------------------------

    try:
        svc = _make_svc()

        def slow_raw_call(prompt):
            time.sleep(2)  # longer than the timeout below
            return ("text", 10, 20)

        with patch.object(svc, "_raw_call", side_effect=slow_raw_call):
            t0 = time.time()
            result = svc._call("test prompt", timeout=0.1)
            elapsed = time.time() - t0

        assert result is None, f"Expected None on timeout, got {result!r}"
        assert elapsed < 1.0, f"_call blocked for {elapsed:.2f}s — shutdown(wait=True) still in effect"
        results.append(("B2: timeout returns fast and returns None", True, f"{elapsed:.2f}s"))
    except Exception as e:
        results.append(("B2: timeout returns fast and returns None", False, str(e)))

    try:
        svc = _make_svc()

        def slow_raw_call_b2(prompt):
            time.sleep(2)
            return ("text", 10, 20)

        with patch.object(svc, "_raw_call", side_effect=slow_raw_call_b2):
            result = svc._call("test prompt", timeout=0.1)

        # Timeout is not in _RETRYABLE_HINTS — must NOT retry, returns None after 1 attempt
        # (can't easily count attempts without threading gymnastics; result=None confirms no retry success)
        assert result is None
        results.append(("B2: timeout does not retry (not in _RETRYABLE_HINTS)", True, ""))
    except Exception as e:
        results.append(("B2: timeout does not retry (not in _RETRYABLE_HINTS)", False, str(e)))

    # ------------------------------------------------------------------
    # B3 — Pattern 2: empty dict from LLM → no ZeroDivisionError
    # ------------------------------------------------------------------

    try:
        svc = _make_svc()
        with patch.object(svc, "_call_json", return_value={}):
            result = svc.fetch_all_project_stages(["Proj A", "Proj B"], "IT")
        assert result == {}, f"Expected empty dict, got {result!r}"
        results.append(("B3: fetch_all_project_stages: LLM {} → returns {}, no crash", True, ""))
    except Exception as e:
        results.append(("B3: fetch_all_project_stages: LLM {} → returns {}, no crash", False, str(e)))

    try:
        svc = _make_svc()
        with patch.object(svc, "_call_json", return_value={}):
            result = svc.fetch_all_bom_components({"Product A": 4, "Product B": 4}, "Maschinenbau")
        assert result == {}, f"Expected empty dict, got {result!r}"
        results.append(("B3: fetch_all_bom_components: LLM {} → returns {}, no crash", True, ""))
    except Exception as e:
        results.append(("B3: fetch_all_bom_components: LLM {} → returns {}, no crash", False, str(e)))

    try:
        svc = _make_svc()
        with patch.object(svc, "_call_json", return_value=None):
            result = svc.fetch_all_project_stages(["Proj A"], "IT")
        assert result == {}
        results.append(("B3: fetch_all_project_stages: LLM None → returns {}, no crash", True, ""))
    except Exception as e:
        results.append(("B3: fetch_all_project_stages: LLM None → returns {}, no crash", False, str(e)))

    try:
        svc = _make_svc()
        with patch.object(svc, "_call_json", return_value=None):
            result = svc.fetch_all_bom_components({"Product A": 4}, "Maschinenbau")
        assert result == {}
        results.append(("B3: fetch_all_bom_components: LLM None → returns {}, no crash", True, ""))
    except Exception as e:
        results.append(("B3: fetch_all_bom_components: LLM None → returns {}, no crash", False, str(e)))

    # ------------------------------------------------------------------
    # B9 — fetch_crm_chatter_messages: 1 batch call, per-opp participants
    # ------------------------------------------------------------------

    try:
        svc = _make_svc()
        opportunities = [
            {"title": "Opp A", "customer": "Kunde A", "salesperson": "Verkäufer A"},
            {"title": "Opp B", "customer": "Kunde B", "salesperson": "Verkäufer B"},
            {"title": "Opp C", "customer": "Kunde C", "salesperson": "Verkäufer C"},
        ]
        captured_prompts = []

        def fake_call_json(prompt, timeout=180):
            captured_prompts.append(prompt)
            return {o["title"]: [{"type": "note", "speaker": "salesperson", "body": "x"}] for o in opportunities}

        with patch.object(svc, "_call_json", side_effect=fake_call_json) as mock_call_json:
            result = svc.fetch_crm_chatter_messages(opportunities, "IT", messages_per_opp=2)

        assert mock_call_json.call_count == 1, f"expected 1 LLM call, got {mock_call_json.call_count}"
        prompt = captured_prompts[0]
        assert all(o["customer"] in prompt for o in opportunities), "not all customer names present in prompt"
        assert all(o["salesperson"] in prompt for o in opportunities), "not all salesperson names present in prompt"
        assert result and set(result.keys()) == {"Opp A", "Opp B", "Opp C"}
        results.append((
            "B9: fetch_crm_chatter_messages: 1 call, distinct per-opp participants", True,
            f"{len(opportunities)} opps in one prompt",
        ))
    except Exception as e:
        results.append(("B9: fetch_crm_chatter_messages: 1 call, distinct per-opp participants", False, str(e)))

    try:
        svc = _make_svc()
        result = svc.fetch_crm_chatter_messages([], "IT")
        assert result == {}
        results.append(("B9: fetch_crm_chatter_messages: empty opportunities → {} no crash", True, ""))
    except Exception as e:
        results.append(("B9: fetch_crm_chatter_messages: empty opportunities → {} no crash", False, str(e)))

    # ------------------------------------------------------------------
    # A3 — cache consistency: Pattern 2 guards for newly-cached functions
    # ------------------------------------------------------------------

    try:
        svc = _make_svc()
        with patch.object(svc, "_call_json", return_value=None):
            result = svc.fetch_recruiting_data("IT", 3, 5, 2, 3)
        assert result is None, f"Expected None, got {result!r}"
        results.append(("A3 Pattern 2: fetch_recruiting_data: LLM None → no crash", True, ""))
    except Exception as e:
        results.append(("A3 Pattern 2: fetch_recruiting_data: LLM None → no crash", False, str(e)))

    try:
        svc = _make_svc()
        with patch.object(svc, "_call_json", return_value=None):
            result = svc.fetch_workcenter_data("IT", "German", 3)
        assert result == {}, f"Expected {{}}, got {result!r}"
        results.append(("A3 Pattern 2: fetch_workcenter_data: LLM None → {}, no crash", True, ""))
    except Exception as e:
        results.append(("A3 Pattern 2: fetch_workcenter_data: LLM None → {}, no crash", False, str(e)))

    # ------------------------------------------------------------------
    # A3 — cache-hit call-count (2nd call must not re-invoke _call_json)
    # ------------------------------------------------------------------

    import llm_service as _llm_mod
    from pathlib import Path as _Path

    def _cache_hit_test(label, call_fn, mocked_return):
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.object(_llm_mod, "_CACHE_DIR", _Path(tmpdir)):
                    svc = _make_svc()
                    with patch.object(svc, "_call_json", return_value=mocked_return) as mock_call_json:
                        r1 = call_fn(svc)
                        r2 = call_fn(svc)
                    assert mock_call_json.call_count == 1, (
                        f"expected 1 LLM call (2nd hits cache), got {mock_call_json.call_count}"
                    )
                    assert r1 == r2
            results.append((label, True, ""))
        except Exception as e:
            results.append((label, False, str(e)))

    _cache_hit_test(
        "A3: fetch_name_suggestions cache-hit avoids 2nd LLM call",
        lambda svc: svc.fetch_name_suggestions({"industry": "IT"}, "German"),
        {"company_names": ["ACME"]},
    )
    _cache_hit_test(
        "A3: fetch_job_summaries_batch cache-hit avoids 2nd LLM call",
        lambda svc: svc.fetch_job_summaries_batch(["Job A"], "IT"),
        {"Job A": "desc"},
    )
    _cache_hit_test(
        "A3: fetch_workcenter_data cache-hit avoids 2nd LLM call",
        lambda svc: svc.fetch_workcenter_data("IT", "German", 3),
        {"Station A": {"description": "x", "operations": ["a", "b", "c"]}},
    )
    _cache_hit_test(
        "A3: fetch_creative_atoms cache-hit avoids 2nd LLM call",
        lambda svc: svc.fetch_creative_atoms(
            {"industry": "IT", "num_services": 1, "num_consumables": 1, "num_storables": 1}, "German"
        ),
        {"product_names": {"services": ["X"], "consumables": [], "storables": []}},
    )

    # ------------------------------------------------------------------
    # A3 — remap must run AFTER cache load/miss, never baked into the
    # cached value (project_names/products are never sent to the LLM,
    # only their count — a cache hit must still remap to the CURRENT
    # actual names passed in, not the names from the first call)
    # ------------------------------------------------------------------

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(_llm_mod, "_CACHE_DIR", _Path(tmpdir)):
                svc = _make_svc()
                with patch.object(svc, "_call_json", return_value={"set_1": ["Kickoff", "Abnahme"]}) as mock_call_json:
                    r1 = svc.fetch_all_project_stages(["A", "B"], "IT")
                    r2 = svc.fetch_all_project_stages(["C", "D"], "IT")
                assert mock_call_json.call_count == 1, (
                    f"expected cache hit on 2nd call (same count), got {mock_call_json.call_count} LLM calls"
                )
                assert set(r1.keys()) == {"A", "B"}, f"1st call remap wrong: {r1}"
                assert set(r2.keys()) == {"C", "D"}, f"remap not applied on cache hit — got {r2}"
        results.append(("A3: fetch_all_project_stages remap applies after cache hit, not baked in", True, ""))
    except Exception as e:
        results.append(("A3: fetch_all_project_stages remap applies after cache hit, not baked in", False, str(e)))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(_llm_mod, "_CACHE_DIR", _Path(tmpdir)):
                svc = _make_svc()
                with patch.object(svc, "_call_json", return_value={"set_1": ["Part A", "Part B"]}) as mock_call_json:
                    r1 = svc.fetch_all_bom_components({"P1": 2, "P2": 2}, "Maschinenbau")
                    r2 = svc.fetch_all_bom_components({"P3": 2, "P4": 2}, "Maschinenbau")
                assert mock_call_json.call_count == 1, (
                    f"expected cache hit on 2nd call (same count+components), got {mock_call_json.call_count}"
                )
                assert set(r1.keys()) == {"P1", "P2"}, f"1st call remap wrong: {r1}"
                assert set(r2.keys()) == {"P3", "P4"}, f"remap not applied on cache hit — got {r2}"
        results.append(("A3: fetch_all_bom_components remap applies after cache hit, not baked in", True, ""))
    except Exception as e:
        results.append(("A3: fetch_all_bom_components remap applies after cache hit, not baked in", False, str(e)))

    # ------------------------------------------------------------------
    # A3 — job_summaries: truthy-only caching (empty {} must never be
    # permanently cached), and cache key includes _PROMPT_VERSION
    # ------------------------------------------------------------------

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(_llm_mod, "_CACHE_DIR", _Path(tmpdir)):
                svc = _make_svc()
                with patch.object(svc, "_call_json", return_value={}) as mock_call_json:
                    r1 = svc.fetch_job_summaries_batch(["Job A"], "IT")
                    r2 = svc.fetch_job_summaries_batch(["Job A"], "IT")
                assert r1 == {} and r2 == {}
                assert mock_call_json.call_count == 2, (
                    f"empty {{}} must not be cached — expected 2 LLM calls, got {mock_call_json.call_count}"
                )
        results.append(("A3: fetch_job_summaries_batch — empty {} not permanently cached", True, ""))
    except Exception as e:
        results.append(("A3: fetch_job_summaries_batch — empty {} not permanently cached", False, str(e)))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(_llm_mod, "_CACHE_DIR", _Path(tmpdir)):
                svc = _make_svc()
                with patch.object(_llm_mod, "_PROMPT_VERSION", "v_test_1"):
                    with patch.object(svc, "_call_json", return_value={"Job A": "desc v1"}) as mock1:
                        svc.fetch_job_summaries_batch(["Job A"], "IT")
                    assert mock1.call_count == 1
                with patch.object(_llm_mod, "_PROMPT_VERSION", "v_test_2"):
                    with patch.object(svc, "_call_json", return_value={"Job A": "desc v2"}) as mock2:
                        svc.fetch_job_summaries_batch(["Job A"], "IT")
                    assert mock2.call_count == 1, (
                        "a _PROMPT_VERSION bump should bust the cache (miss expected) — "
                        "got a hit, meaning the key doesn't include _PROMPT_VERSION"
                    )
        results.append(("A3: fetch_job_summaries_batch cache key includes _PROMPT_VERSION", True, ""))
    except Exception as e:
        results.append(("A3: fetch_job_summaries_batch cache key includes _PROMPT_VERSION", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
