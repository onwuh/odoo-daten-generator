"""Unit tests for crash-risk guard patterns (no Odoo connection needed)."""
import datetime
import os
import random
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.accounting import _introduce_typo
from modules.crm import (
    _activity_deadline,
    _build_partner_pool,
    _post_chatter_messages,
    _fetch_sales_users,
    _normalize_message,
    _create_activities,
    _unique_titles,
)
from config import (
    ActivitiesConfig,
    ChatterConfig,
    DemoCriteria,
    ModuleSelections,
    RunContext,
)

_CHATTER_CFG = ChatterConfig(style="mixed", messages_per_opp=4)


def _make_ctx(**kwargs):
    criteria = DemoCriteria(
        mode="both", industry="Test", num_companies=1,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    sel_kwargs = dict(
        crm_chatter=_CHATTER_CFG,
        crm_activities=ActivitiesConfig(past_pct=30, today_pct=20),
    )
    sel_kwargs.update(kwargs.get("sel", {}))
    sel = ModuleSelections(**sel_kwargs)
    return RunContext(
        criteria=criteria, module_selections=sel,
        industry="Test", language_name="German", language_code="de_DE",
    )


def run():
    """Returns (all_passed, [(label, ok, detail), ...])"""
    results = []

    # ------------------------------------------------------------------
    # B1 — _introduce_typo boundary conditions
    # ------------------------------------------------------------------

    try:
        assert _introduce_typo("") == ""
        results.append(("_introduce_typo: empty string → no crash", True, ""))
    except Exception as e:
        results.append(("_introduce_typo: empty string → no crash", False, str(e)))

    try:
        assert _introduce_typo("X") == "X"
        results.append(("_introduce_typo: single char → returns unchanged", True, ""))
    except Exception as e:
        results.append(("_introduce_typo: single char → returns unchanged", False, str(e)))

    try:
        result = _introduce_typo("AB")
        assert isinstance(result, str) and len(result) == 2
        results.append(("_introduce_typo: two chars → no crash", True, result))
    except Exception as e:
        results.append(("_introduce_typo: two chars → no crash", False, str(e)))

    try:
        random.seed(0)
        result = _introduce_typo("Hello World")
        assert isinstance(result, str) and len(result) == len("Hello World")
        assert result != "Hello World", "Expected modification for long string"
        results.append(("_introduce_typo: long label → modified string", True, result))
    except Exception as e:
        results.append(("_introduce_typo: long label → modified string", False, str(e)))

    # ------------------------------------------------------------------
    # B2 — _activity_deadline distribution
    # ------------------------------------------------------------------

    try:
        random.seed(99)
        today = datetime.date.today().isoformat()
        dates = [_activity_deadline(100, 0) for _ in range(50)]
        assert all(d < today for d in dates), "past_pct=100 should yield all past dates"
        results.append(("_activity_deadline: past_pct=100 → all past", True, ""))
    except Exception as e:
        results.append(("_activity_deadline: past_pct=100 → all past", False, str(e)))

    try:
        random.seed(99)
        today = datetime.date.today().isoformat()
        dates = [_activity_deadline(0, 100) for _ in range(50)]
        assert all(d == today for d in dates), "today_pct=100 should yield all today"
        results.append(("_activity_deadline: today_pct=100 → all today", True, ""))
    except Exception as e:
        results.append(("_activity_deadline: today_pct=100 → all today", False, str(e)))

    try:
        random.seed(99)
        today = datetime.date.today().isoformat()
        dates = [_activity_deadline(0, 0) for _ in range(50)]
        assert all(d > today for d in dates), "past_pct=0, today_pct=0 → all future"
        results.append(("_activity_deadline: 0/0 pct → all future", True, ""))
    except Exception as e:
        results.append(("_activity_deadline: 0/0 pct → all future", False, str(e)))

    try:
        # Oversized past_pct: clamps into past bucket (all 150+ treated as past)
        today = datetime.date.today().isoformat()
        result = _activity_deadline(150, 0)
        assert isinstance(result, str)
        results.append(("_activity_deadline: past_pct=150 → no crash", True, result))
    except Exception as e:
        results.append(("_activity_deadline: past_pct=150 → no crash", False, str(e)))

    # ------------------------------------------------------------------
    # B3 — _build_partner_pool boundary conditions
    # ------------------------------------------------------------------

    try:
        result = _build_partner_pool([], 0)
        assert result == [], f"Expected [], got {result}"
        results.append(("_build_partner_pool: empty company_ids, 0 records → []", True, ""))
    except Exception as e:
        results.append(("_build_partner_pool: empty company_ids, 0 records → []", False, str(e)))

    try:
        result = _build_partner_pool([1, 2], 0)
        assert result == [], f"Expected [], got {result}"
        results.append(("_build_partner_pool: num_records=0 → []", True, ""))
    except Exception as e:
        results.append(("_build_partner_pool: num_records=0 → []", False, str(e)))

    try:
        random.seed(0)
        company_ids = [10, 20]
        result = _build_partner_pool(company_ids, 10)  # > 2*len = 4
        assert len(result) == 10
        assert all(c in company_ids for c in result)
        results.append(("_build_partner_pool: num_records > 2x → extras filled, no crash", True, ""))
    except Exception as e:
        results.append(("_build_partner_pool: num_records > 2x → extras filled, no crash", False, str(e)))

    # ------------------------------------------------------------------
    # B4 — _post_chatter_messages guards (new dict-based format)
    # ------------------------------------------------------------------

    def _make_opp_data(opp_id=1, name="Test Opp"):
        return [{"id": opp_id, "name": name, "partner_id": 10,
                 "partner_name": "Test GmbH", "salesperson": None}]

    # None response → no crash, no call_method
    try:
        mock_client = MagicMock()
        mock_gemini = MagicMock()
        mock_gemini.fetch_crm_chatter_messages.return_value = None
        ctx = _make_ctx()
        _post_chatter_messages(mock_client, mock_gemini, ctx, _make_opp_data())
        mock_client.call_method.assert_not_called()
        results.append(("_post_chatter_messages: None response → no call_method", True, ""))
    except Exception as e:
        results.append(("_post_chatter_messages: None response → no call_method", False, str(e)))

    # {} response → no call_method
    try:
        mock_client = MagicMock()
        mock_gemini = MagicMock()
        mock_gemini.fetch_crm_chatter_messages.return_value = {}
        ctx = _make_ctx()
        _post_chatter_messages(mock_client, mock_gemini, ctx, _make_opp_data())
        mock_client.call_method.assert_not_called()
        results.append(("_post_chatter_messages: {} response → no call_method", True, ""))
    except Exception as e:
        results.append(("_post_chatter_messages: {} response → no call_method", False, str(e)))

    # New dict format → call_method called
    try:
        mock_client = MagicMock()
        mock_gemini = MagicMock()
        mock_gemini.fetch_crm_chatter_messages.return_value = {
            "Test Opp": [{"type": "note", "speaker": "salesperson", "body": "hello"}]
        }
        ctx = _make_ctx()
        _post_chatter_messages(mock_client, mock_gemini, ctx, _make_opp_data())
        mock_client.call_method.assert_called_once()
        results.append(("_post_chatter_messages: dict format → call_method called once", True, ""))
    except Exception as e:
        results.append(("_post_chatter_messages: dict format → call_method called once", False, str(e)))

    # Legacy string format (old cache) → backward compat, no crash
    try:
        mock_client = MagicMock()
        mock_gemini = MagicMock()
        mock_gemini.fetch_crm_chatter_messages.return_value = {"Test Opp": ["legacy string note"]}
        ctx = _make_ctx()
        _post_chatter_messages(mock_client, mock_gemini, ctx, _make_opp_data())
        mock_client.call_method.assert_called_once()
        results.append(("_post_chatter_messages: legacy string format → backward compat", True, ""))
    except Exception as e:
        results.append(("_post_chatter_messages: legacy string format → backward compat", False, str(e)))

    # Empty opp_data pool → graceful skip
    try:
        mock_client = MagicMock()
        mock_gemini = MagicMock()
        ctx = _make_ctx()
        _post_chatter_messages(mock_client, mock_gemini, ctx, [])
        mock_client.call_method.assert_not_called()
        results.append(("_post_chatter_messages: empty opp_data → no call_method", True, ""))
    except Exception as e:
        results.append(("_post_chatter_messages: empty opp_data → no call_method", False, str(e)))

    # Disabled chatter (crm_chatter=None) → no LLM call, no message_post
    try:
        mock_client = MagicMock()
        mock_gemini = MagicMock()
        ctx = _make_ctx(sel={"crm_chatter": None})
        _post_chatter_messages(mock_client, mock_gemini, ctx, _make_opp_data())
        mock_gemini.fetch_crm_chatter_messages.assert_not_called()
        mock_client.call_method.assert_not_called()
        results.append(("_post_chatter_messages: crm_chatter=None → disabled, no LLM call", True, ""))
    except Exception as e:
        results.append(("_post_chatter_messages: crm_chatter=None → disabled, no LLM call", False, str(e)))

    # _normalize_message handles all input types
    try:
        assert _normalize_message("raw string") == {"type": "note", "speaker": "salesperson", "body": "raw string"}
        assert _normalize_message({"type": "email", "speaker": "customer", "body": "hi"}) == {"type": "email", "speaker": "customer", "body": "hi"}
        assert _normalize_message({"body": "only body"}) == {"type": "note", "speaker": "salesperson", "body": "only body"}
        results.append(("_normalize_message: handles string/dict/partial dict", True, ""))
    except Exception as e:
        results.append(("_normalize_message: handles string/dict/partial dict", False, str(e)))

    # _fetch_sales_users: API error → returns []
    try:
        mock_client = MagicMock()
        mock_client.search_read.side_effect = Exception("network error")
        result = _fetch_sales_users(mock_client)
        assert result == [], f"Expected [], got {result}"
        results.append(("_fetch_sales_users: API error → returns []", True, ""))
    except Exception as e:
        results.append(("_fetch_sales_users: API error → returns []", False, str(e)))

    # _fetch_sales_users: empty user list → returns []
    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = []
        result = _fetch_sales_users(mock_client)
        assert result == []
        results.append(("_fetch_sales_users: empty list → []", True, ""))
    except Exception as e:
        results.append(("_fetch_sales_users: empty list → []", False, str(e)))

    # _fetch_sales_users: users with partner_id tuple → unpacked correctly
    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = [
            {"id": 5, "name": "Anna Müller", "partner_id": [42, "Anna Müller"], "email": "anna@example.com"},
        ]
        result = _fetch_sales_users(mock_client)
        assert len(result) == 1
        assert result[0]["partner_id"] == 42
        assert result[0]["user_id"] == 5
        results.append(("_fetch_sales_users: partner_id tuple → unpacked correctly", True, ""))
    except Exception as e:
        results.append(("_fetch_sales_users: partner_id tuple → unpacked correctly", False, str(e)))

    # ------------------------------------------------------------------
    # B5 — _create_activities empty type_pool guard
    # ------------------------------------------------------------------

    try:
        mock_client = MagicMock()
        ctx = _make_ctx()
        with patch("modules.crm._get_crm_lead_model_id", return_value=42), \
             patch("modules.crm._get_activity_types", return_value=[]):
            _create_activities(mock_client, [1, 2], ctx)
        mock_client.create.assert_not_called()
        results.append(("_create_activities: empty type list → no create call", True, ""))
    except Exception as e:
        results.append(("_create_activities: empty type list → no create call", False, str(e)))

    try:
        mock_client = MagicMock()
        ctx = _make_ctx()
        with patch("modules.crm._get_crm_lead_model_id", return_value=None):
            _create_activities(mock_client, [1, 2], ctx)
        mock_client.create.assert_not_called()
        results.append(("_create_activities: model_id=None → no create call", True, ""))
    except Exception as e:
        results.append(("_create_activities: model_id=None → no create call", False, str(e)))

    # ------------------------------------------------------------------
    # B9 — _unique_titles: no duplicate titles within a batch
    # ------------------------------------------------------------------

    bank = ["Cloud-Migration Q3", "Wartungsvertrag Verlängerung", "System-Ablösung Legacy"]

    try:
        random.seed(1)
        titles = _unique_titles(bank, 3)
        assert len(titles) == 3 and len(set(titles)) == 3
        results.append(("_unique_titles: n == bank size → all unique, no suffix", True, str(titles)))
    except Exception as e:
        results.append(("_unique_titles: n == bank size → all unique, no suffix", False, str(e)))

    try:
        random.seed(2)
        titles = _unique_titles(bank, 10)  # overflow: 10 requested, bank has 3
        assert len(titles) == 10
        assert len(set(titles)) == 10, f"duplicates found: {titles}"
        results.append(("_unique_titles: overflow (n > bank) → still all unique", True, str(titles)))
    except Exception as e:
        results.append(("_unique_titles: overflow (n > bank) → still all unique", False, str(e)))

    try:
        titles = _unique_titles([], 3)
        assert len(titles) == 3 and len(set(titles)) == 3
        results.append(("_unique_titles: empty bank → falls back, still unique", True, str(titles)))
    except Exception as e:
        results.append(("_unique_titles: empty bank → falls back, still unique", False, str(e)))

    try:
        titles = _unique_titles(bank, 0)
        assert titles == []
        results.append(("_unique_titles: n=0 → []", True, ""))
    except Exception as e:
        results.append(("_unique_titles: n=0 → []", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
