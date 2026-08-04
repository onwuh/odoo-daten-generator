"""Unit tests for modules/accounting.py — D3 batch-creation call-count guard.

The real N+1 D3 targets (per IMPLEMENTIERUNGSPLAN.md): standalone customer
invoices, invoices-from-orders, and vendor bills must each go through exactly
one create_batch call, and vendor-bill creation must be decoupled from
posting (create_batch once, then a single post_invoices/action_post call —
not one action_post per bill as create_vendor_bill does for single records).
"""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import accounting


def _make_ctx(num_invoices, installed_modules=None):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    ctx = RunContext(
        criteria=criteria, module_selections=ModuleSelections(account=num_invoices), industry="IT",
        language_name="German", language_code="de", gemini_model_name="test",
    )
    ctx.installed_modules = installed_modules or set()
    return ctx


def _mock_client(product_price_rows=None):
    client = MagicMock()
    counter = {"n": 6000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'product.product':
            return product_price_rows or []
        return []

    client.create_batch.side_effect = _create_batch
    client.search_read.side_effect = _search_read
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # D3: standalone customer invoices + vendor bills each via exactly 1
    # create_batch call (2 total for account.move); action_post issued once
    # per batch (not once per bill), via call_method not per-record create().
    # ------------------------------------------------------------------
    try:
        client = _mock_client(product_price_rows=[{"id": 1, "standard_price": 10.0, "list_price": 20.0}])
        ctx = _make_ctx(num_invoices=6)  # standalone path: 'sale' not installed
        ctx.company_ids = [1, 2]
        ctx.product_ids = [1]
        accounting.create_accounting_data(client, gemini=None, ctx=ctx)

        assert client.create_batch.call_count == 2, client.create_batch.call_count
        batched_models = [call.args[0] for call in client.create_batch.call_args_list]
        assert batched_models == ['account.move', 'account.move'], batched_models
        # _create_suppliers legitimately still creates res.partner individually
        # (out of D3 scope for this module) — only account.move must never go
        # through per-record create().
        individually_created_models = [call.args[0] for call in client.create.call_args_list]
        assert 'account.move' not in individually_created_models, individually_created_models

        assert len(ctx.invoice_ids) == 6, ctx.invoice_ids
        assert len(ctx.bill_ids) >= 1, ctx.bill_ids

        # action_post must be called once per batch (2 calls total), not once per record.
        post_calls = [c for c in client.call_method.call_args_list if c.args[1] == 'action_post']
        assert len(post_calls) == 2, f"expected 2 action_post calls (1 per batch), got {len(post_calls)}"

        results.append((
            "create_accounting_data: invoices+bills via create_batch, action_post once per batch",
            True, f"create_batch calls={client.create_batch.call_count}, action_post calls={len(post_calls)}",
        ))
    except AssertionError as e:
        results.append(("create_accounting_data: invoices+bills via create_batch, action_post once per batch", False, str(e)))

    # ------------------------------------------------------------------
    # D3: create_invoices_from_orders — exactly 1 create_batch call regardless
    # of order count.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        client.search_read.side_effect = None
        client.search_read.return_value = [
            {"id": 100, "partner_id": [1, "P"], "order_line": [1, 2], "name": "SO001"},
            {"id": 101, "partner_id": [2, "P2"], "order_line": [3], "name": "SO002"},
        ]

        def _sr(model, domain=None, fields=None, limit=None, **kw):
            if model == 'sale.order':
                return [
                    {"id": 100, "partner_id": [1, "P"], "order_line": [1, 2], "name": "SO001"},
                    {"id": 101, "partner_id": [2, "P2"], "order_line": [3], "name": "SO002"},
                ]
            if model == 'sale.order.line':
                return [
                    {"id": 1, "product_id": [10, "X"], "product_uom_qty": 2, "price_unit": 5.0},
                    {"id": 2, "product_id": [11, "Y"], "product_uom_qty": 1, "price_unit": 7.0},
                    {"id": 3, "product_id": [12, "Z"], "product_uom_qty": 3, "price_unit": 9.0},
                ]
            return []
        client.search_read.side_effect = _sr

        result_ids = accounting.create_invoices_from_orders(client, [100, 101])
        assert len(result_ids) == 2, result_ids
        assert client.create_batch.call_count == 1, client.create_batch.call_count
        assert client.create.call_count == 0, "fell back to per-record create()"
        results.append((
            "create_invoices_from_orders: exactly 1 create_batch call",
            True, f"create_batch calls={client.create_batch.call_count}",
        ))
    except AssertionError as e:
        results.append(("create_invoices_from_orders: exactly 1 create_batch call", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 1: create_invoices_from_orders([]) -> no create_batch call
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        result_ids = accounting.create_invoices_from_orders(client, [])
        assert result_ids == []
        client.create_batch.assert_not_called()
        results.append(("create_invoices_from_orders: empty order_ids -> [] no crash (Pattern 1)", True, ""))
    except AssertionError as e:
        results.append(("create_invoices_from_orders: empty order_ids -> [] no crash (Pattern 1)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 5: num_invoices=0 -> early return, no create_batch calls
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_invoices=0)
        accounting.create_accounting_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_accounting_data: num_invoices=0 -> no create_batch calls (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_accounting_data: num_invoices=0 -> no create_batch calls (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # B7: num_invoices=1 must not force 10 vendor bills (was max(10, ...)).
    # ------------------------------------------------------------------
    try:
        client = _mock_client(product_price_rows=[{"id": 1, "standard_price": 10.0, "list_price": 20.0}])
        ctx = _make_ctx(num_invoices=1)
        ctx.company_ids = [1]
        ctx.product_ids = [1]
        accounting.create_accounting_data(client, gemini=None, ctx=ctx)
        assert len(ctx.bill_ids) == 1, f"B7: expected 1 vendor bill for num_invoices=1, got {len(ctx.bill_ids)}"
        results.append(("B7: num_invoices=1 -> exactly 1 vendor bill, not forced 10", True, f"bills={len(ctx.bill_ids)}"))
    except AssertionError as e:
        results.append(("B7: num_invoices=1 -> exactly 1 vendor bill, not forced 10", False, str(e)))

    # ------------------------------------------------------------------
    # B10 Pattern 4: 'sale' genuinely installed but not selected this run
    # (ctx.confirmed_order_ids empty because sale never ran) -> standalone
    # invoice path still produces exactly num_invoices invoices, not zero
    # and not crashing on the stale "not installed" assumption.
    # ------------------------------------------------------------------
    try:
        client = _mock_client(product_price_rows=[{"id": 1, "standard_price": 10.0, "list_price": 20.0}])
        ctx = _make_ctx(num_invoices=3, installed_modules={"sale"})  # installed, but unselected this run
        ctx.company_ids = [1]
        ctx.product_ids = [1]
        ctx.confirmed_order_ids = []  # sale module didn't run -> nothing confirmed
        accounting.create_accounting_data(client, gemini=None, ctx=ctx)
        assert len(ctx.invoice_ids) == 3, (
            f"B10: 'sale' installed-but-unselected should still take the standalone "
            f"invoice path, expected 3 invoices, got {len(ctx.invoice_ids)}"
        )
        results.append((
            "create_accounting_data: sale installed-but-unselected -> standalone invoices (B10 Pattern 4)",
            True, f"invoices={len(ctx.invoice_ids)}",
        ))
    except AssertionError as e:
        results.append(("create_accounting_data: sale installed-but-unselected -> standalone invoices (B10 Pattern 4)", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
