"""Unit tests for modules/inventory.py (R3/S8).

Patterns covered: 1 (empty storable pool -> no create_batch), 3 (stock={} or
stock={"avg_qty": 0} -> no API calls), 5 (missing company_ids -> SKIP).
No Pattern 2/6/8 — no LLM calls, no many2one fields read back in this module.
"""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import inventory


def _make_ctx(stock_sel=None, company_ids=None, product_ids=None, component_ids=None):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    ctx = RunContext(
        criteria=criteria,
        module_selections=ModuleSelections(stock=stock_sel if stock_sel is not None else {"avg_qty": 20}),
        industry="IT", language_name="German", language_code="de", gemini_model_name="test",
    )
    ctx.company_ids = company_ids if company_ids is not None else [10]
    ctx.product_ids = product_ids if product_ids is not None else [1, 2]
    ctx.component_ids = component_ids if component_ids is not None else [3, 4]
    return ctx


def _mock_client(warehouse=True, storable_ids=None):
    client = MagicMock()
    counter = {"n": 8000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'res.company':
            return [{"id": 1}]
        if model == 'stock.warehouse':
            return [{"lot_stock_id": [1, "WH/Stock"], "in_type_id": [2, "WH/IN"]}] if warehouse else []
        if model == 'product.product':
            ids = storable_ids if storable_ids is not None else [1, 2, 3, 4]
            return [{"id": pid} for pid in ids]
        return []

    client.create_batch.side_effect = _create_batch
    client.search_read.side_effect = _search_read
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # Pattern 3: stock={} (default/disabled) -> no API calls at all.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(stock_sel={})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        client.search_read.assert_not_called()
        results.append(("create_inventory_data: stock={} -> no calls (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_inventory_data: stock={} -> no calls (Pattern 3)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 3: stock={"avg_qty": 0} (non-empty dict, zero qty) -> still a
    # full no-op — guard must check avg_qty, not just dict truthiness.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(stock_sel={"avg_qty": 0})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        client.search_read.assert_not_called()
        results.append(("create_inventory_data: stock={'avg_qty': 0} -> no calls (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_inventory_data: stock={'avg_qty': 0} -> no calls (Pattern 3)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 5: empty company_ids -> no create_batch calls, graceful skip.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(company_ids=[])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_inventory_data: empty company_ids -> no calls (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_inventory_data: empty company_ids -> no calls (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # No warehouse resolvable -> graceful skip, no create_batch.
    # ------------------------------------------------------------------
    try:
        client = _mock_client(warehouse=False)
        ctx = _make_ctx()
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_inventory_data: no warehouse -> no calls, graceful skip", True, ""))
    except AssertionError as e:
        results.append(("create_inventory_data: no warehouse -> no calls, graceful skip", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 1: no product_ids/component_ids at all -> no product.product
    # search_read, no create_batch (candidate pool empty before the query).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(product_ids=[], component_ids=[])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        product_searches = [c for c in client.search_read.call_args_list if c.args[0] == 'product.product']
        assert product_searches == [], product_searches
        results.append(("create_inventory_data: empty product/component pool -> no calls (Pattern 1)", True, ""))
    except AssertionError as e:
        results.append(("create_inventory_data: empty product/component pool -> no calls (Pattern 1)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 1: candidate pool non-empty but none is_storable -> no create_batch.
    # ------------------------------------------------------------------
    try:
        client = _mock_client(storable_ids=[])
        ctx = _make_ctx()
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_inventory_data: no storable products -> no calls (Pattern 1)", True, ""))
    except AssertionError as e:
        results.append(("create_inventory_data: no storable products -> no calls (Pattern 1)", False, str(e)))

    # ------------------------------------------------------------------
    # Happy path: one stock.quant per storable product, inventory_quantity
    # within [avg*0.5, avg*1.5], action_apply_inventory attempted.
    # ------------------------------------------------------------------
    try:
        client = _mock_client(storable_ids=[1, 2, 3])
        ctx = _make_ctx(stock_sel={"avg_qty": 20})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        assert len(quant_batches) == 1, quant_batches
        vals_list = quant_batches[0].args[1]
        assert len(vals_list) == 3, vals_list
        for v in vals_list:
            assert v["location_id"] == 1, v
            assert 10 <= v["inventory_quantity"] <= 30, v
            assert "company_id" in v and "in_date" in v, v
        apply_calls = [c for c in client.call_method.call_args_list if c.args[1] == 'action_apply_inventory']
        assert len(apply_calls) == 1, apply_calls
        results.append(("create_inventory_data: happy path, quants built + apply attempted", True, f"{len(vals_list)} quants"))
    except AssertionError as e:
        results.append(("create_inventory_data: happy path, quants built + apply attempted", False, str(e)))

    # ------------------------------------------------------------------
    # action_apply_inventory failing is non-fatal: quants stay created, module
    # doesn't raise.
    # ------------------------------------------------------------------
    try:
        client = _mock_client(storable_ids=[1])
        client.call_method.side_effect = Exception("simulated apply failure")
        ctx = _make_ctx(stock_sel={"avg_qty": 5})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)  # must not raise
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        assert len(quant_batches) == 1 and len(quant_batches[0].args[1]) == 1, quant_batches
        results.append(("create_inventory_data: action_apply_inventory failure is non-fatal", True, ""))
    except Exception as e:
        results.append(("create_inventory_data: action_apply_inventory failure is non-fatal", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
