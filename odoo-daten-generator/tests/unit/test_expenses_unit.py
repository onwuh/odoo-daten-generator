"""Unit tests for modules/expenses.py (R19/S12-WP2).

Patterns covered: 1 (empty can_be_expensed pool -> no create_batch), 3
(hr_expense={} -> no API calls), 5 (empty employee_ids -> no calls), 7
(approved_pct distribution over many employees). No Pattern 2/8 — no LLM
calls in this module (LLM-minimalism: template description, no creative
text worth a round-trip).
"""
import os
import random
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import expenses


def _make_ctx(employee_ids=None, hr_expense=None, analytic=None):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    sel_kwargs = {}
    if hr_expense is not None:
        sel_kwargs["hr_expense"] = hr_expense
    if analytic is not None:
        sel_kwargs["analytic"] = analytic
    ctx = RunContext(
        criteria=criteria,
        module_selections=ModuleSelections(**sel_kwargs),
        industry="IT", language_name="German", language_code="de", gemini_model_name="test",
    )
    ctx.employee_ids = employee_ids if employee_ids is not None else [1, 2]
    return ctx


def _mock_client(categories=None, company_id=10):
    client = MagicMock()
    counter = {"n": 8000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'product.product':
            return categories if categories is not None else [{"id": 500, "name": "Reisekosten"}]
        if model == 'res.company':
            return [{"id": company_id, "currency_id": [3, "EUR"]}]
        return []

    client.create_batch.side_effect = _create_batch
    client.search_read.side_effect = _search_read
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # Pattern 3: hr_expense={} (default) -> no API calls at all.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx()
        expenses.create_expense_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        client.search_read.assert_not_called()
        results.append(("create_expense_data: hr_expense={} -> no calls (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_expense_data: hr_expense={} -> no calls (Pattern 3)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 5: empty employee_ids -> no calls, graceful skip.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(employee_ids=[], hr_expense={"count_per_employee": 3, "approved_pct": 70})
        expenses.create_expense_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_expense_data: empty employee_ids -> no calls (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_expense_data: empty employee_ids -> no calls (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 1: empty can_be_expensed pool -> no create_batch, graceful skip.
    # ------------------------------------------------------------------
    try:
        client = _mock_client(categories=[])
        ctx = _make_ctx(hr_expense={"count_per_employee": 3, "approved_pct": 70})
        expenses.create_expense_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_expense_data: empty can_be_expensed pool -> no calls (Pattern 1)", True, ""))
    except AssertionError as e:
        results.append(("create_expense_data: empty can_be_expensed pool -> no calls (Pattern 1)", False, str(e)))

    # ------------------------------------------------------------------
    # Every created record carries product_id — live-found (S12/WP3):
    # Odoo rejects the approval-state write/action methods without it.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(employee_ids=[1, 2, 3], hr_expense={"count_per_employee": 2, "approved_pct": 0})
        expenses.create_expense_data(client, gemini=None, ctx=ctx)
        batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'hr.expense']
        assert len(batches) == 1, batches
        vals_list = batches[0].args[1]
        assert len(vals_list) == 6, f"expected 3 employees * 2 count_per_employee, got {len(vals_list)}"
        assert all(v.get("product_id") == 500 for v in vals_list), vals_list
        assert all(v.get("employee_id") in (1, 2, 3) for v in vals_list), vals_list
        results.append(("create_expense_data: count_per_employee scaling + product_id always set", True, f"{len(vals_list)} records"))
    except AssertionError as e:
        results.append(("create_expense_data: count_per_employee scaling + product_id always set", False, str(e)))

    # ------------------------------------------------------------------
    # approved_pct=0 -> no approval_state writes at all.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(employee_ids=[1], hr_expense={"count_per_employee": 4, "approved_pct": 0})
        expenses.create_expense_data(client, gemini=None, ctx=ctx)
        client.write.assert_not_called()
        results.append(("create_expense_data: approved_pct=0 -> no write calls (Pattern 3-adjacent)", True, ""))
    except AssertionError as e:
        results.append(("create_expense_data: approved_pct=0 -> no write calls (Pattern 3-adjacent)", False, str(e)))

    # ------------------------------------------------------------------
    # approved_pct=100 -> every created expense gets submitted then approved,
    # both as batched writes (2 calls total, not 2*N).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(employee_ids=[1, 2], hr_expense={"count_per_employee": 3, "approved_pct": 100})
        expenses.create_expense_data(client, gemini=None, ctx=ctx)
        write_calls = client.write.call_args_list
        assert len(write_calls) == 2, f"expected exactly 2 batched writes, got {len(write_calls)}"
        assert write_calls[0].args[2] == {"approval_state": "submitted"}, write_calls[0]
        assert write_calls[1].args[2] == {"approval_state": "approved"}, write_calls[1]
        assert len(write_calls[0].args[1]) == 6 == len(write_calls[1].args[1]), write_calls
        results.append(("create_expense_data: approved_pct=100 -> 2 batched writes (submitted, approved), not 2*N", True, ""))
    except AssertionError as e:
        results.append(("create_expense_data: approved_pct=100 -> 2 batched writes (submitted, approved), not 2*N", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 7: approved_pct distribution over many employees (n=100), seeded.
    # ------------------------------------------------------------------
    try:
        random.seed(42)
        client = _mock_client()
        ctx = _make_ctx(employee_ids=list(range(100)), hr_expense={"count_per_employee": 1, "approved_pct": 40})
        expenses.create_expense_data(client, gemini=None, ctx=ctx)
        write_calls = client.write.call_args_list
        assert len(write_calls) == 2, write_calls
        approved_count = len(write_calls[0].args[1])
        assert 25 <= approved_count <= 55, f"approved_count={approved_count} far from expected ~40% of 100"
        results.append(("Pattern 7: approved_pct=40 over n=100 lands near 40%", True, f"{approved_count}/100"))
    except AssertionError as e:
        results.append(("Pattern 7: approved_pct=40 over n=100 lands near 40%", False, str(e)))

    # ==================================================================
    # S15/R20 — analytic distribution wiring
    # ==================================================================

    try:
        # Pattern 3: analytic disabled (default) -> helper never called, no
        # analytic_distribution in the created vals.
        client = _mock_client()
        ctx = _make_ctx(employee_ids=[1, 2], hr_expense={"count_per_employee": 2, "approved_pct": 0})
        with patch("modules.expenses.odoo_actions.get_or_create_analytic_accounts") as mock_helper:
            expenses.create_expense_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_not_called()
        batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'hr.expense']
        vals_list = batches[0].args[1]
        assert all("analytic_distribution" not in v for v in vals_list), vals_list
        results.append(("create_expense_data: analytic disabled -> no helper call, no distribution (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_expense_data: analytic disabled -> no helper call, no distribution (Pattern 3)", False, str(e)))

    try:
        # expense_pct=0 with analytic enabled -> same as disabled (its own
        # sub-off-switch, not just the shared enabled flag).
        client = _mock_client()
        ctx = _make_ctx(employee_ids=[1, 2], hr_expense={"count_per_employee": 2, "approved_pct": 0},
                        analytic={"enabled": True, "sale_pct": 50, "purchase_pct": 50, "expense_pct": 0})
        with patch("modules.expenses.odoo_actions.get_or_create_analytic_accounts") as mock_helper:
            expenses.create_expense_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_not_called()
        results.append(("create_expense_data: expense_pct=0 -> no helper call (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_expense_data: expense_pct=0 -> no helper call (Pattern 3)", False, str(e)))

    try:
        # Happy path: analytic enabled + expense_pct>0 -> helper called once,
        # assign_analytic_distribution's effect visible in the create_batch vals.
        client = _mock_client()
        ctx = _make_ctx(employee_ids=list(range(20)), hr_expense={"count_per_employee": 1, "approved_pct": 0},
                        analytic={"enabled": True, "sale_pct": 0, "purchase_pct": 0, "expense_pct": 100})
        with patch("modules.expenses.odoo_actions.get_or_create_analytic_accounts",
                  return_value=[701, 702]) as mock_helper:
            expenses.create_expense_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_called_once()
        batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'hr.expense']
        vals_list = batches[0].args[1]
        assert len(vals_list) == 20, vals_list
        assert all("analytic_distribution" in v for v in vals_list), \
            "expense_pct=100 must reach every created expense"
        for v in vals_list:
            keys = list(v["analytic_distribution"].keys())
            assert len(keys) == 1 and int(keys[0]) in (701, 702), v
        results.append(("create_expense_data: analytic enabled -> helper called once, distribution reaches vals", True, ""))
    except AssertionError as e:
        results.append(("create_expense_data: analytic enabled -> helper called once, distribution reaches vals", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
