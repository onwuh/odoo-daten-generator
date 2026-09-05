"""Unit tests for modules/purchase.py (R2/S8).

Patterns covered: 1 (empty component_ids/supplier pool -> no create_batch),
3 (purchase=0 -> no API calls), 5 (missing prerequisites -> SKIP/no calls),
6 (many2one [id, name] tuple unpacking on PO-line read-back in the manual
bill-rebuild fallback). No Pattern 2/8 — no LLM calls in this module.
"""
import os
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import purchase


def _make_ctx(num_purchase=1, confirm_pct=70, component_ids=None, company_ids=None, supplier_ids=None,
              analytic=None):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    sel_kwargs = {"purchase": num_purchase, "purchase_confirm_pct": confirm_pct}
    if analytic is not None:
        sel_kwargs["analytic"] = analytic
    ctx = RunContext(
        criteria=criteria,
        module_selections=ModuleSelections(**sel_kwargs),
        industry="IT", language_name="German", language_code="de", gemini_model_name="test",
    )
    ctx.component_ids = component_ids if component_ids is not None else [1, 2, 3]
    ctx.partner_company_ids = company_ids if company_ids is not None else [10]
    ctx.supplier_ids = supplier_ids if supplier_ids is not None else []
    return ctx


def _mock_client(warehouse=True, product_price_rows=None):
    client = MagicMock()
    counter = {"n": 7000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'stock.warehouse':
            return [{"lot_stock_id": [1, "WH/Stock"], "in_type_id": [2, "WH/IN"]}] if warehouse else []
        if model == 'res.company':
            return [{"id": 10, "currency_id": [3, "EUR"]}]
        if model == 'product.product':
            return product_price_rows or []
        if model == 'purchase.order':
            return []
        return []

    client.create_batch.side_effect = _create_batch
    client.search_read.side_effect = _search_read
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # Pattern 3: purchase=0 -> no API calls at all.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_purchase=0)
        purchase.create_purchase_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        client.search_read.assert_not_called()
        results.append(("create_purchase_data: purchase=0 -> no calls (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_purchase_data: purchase=0 -> no calls (Pattern 3)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 1/5: empty component_ids -> no create_batch calls, graceful skip.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(component_ids=[])
        purchase.create_purchase_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_purchase_data: empty component_ids -> no calls (Pattern 1/5)", True, ""))
    except AssertionError as e:
        results.append(("create_purchase_data: empty component_ids -> no calls (Pattern 1/5)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 5: empty company_ids -> no create_batch calls, graceful skip.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(company_ids=[])
        purchase.create_purchase_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_purchase_data: empty company_ids -> no calls (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_purchase_data: empty company_ids -> no calls (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # No warehouse resolvable -> graceful skip, no create_batch.
    # ------------------------------------------------------------------
    try:
        client = _mock_client(warehouse=False)
        ctx = _make_ctx()
        purchase.create_purchase_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_purchase_data: no warehouse -> no calls, graceful skip", True, ""))
    except AssertionError as e:
        results.append(("create_purchase_data: no warehouse -> no calls, graceful skip", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 1: supplier pool ends up empty (create_suppliers returns [])
    # -> no purchase.order create_batch call.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx()
        with patch("modules.purchase.odoo_actions.create_suppliers", return_value=[]):
            purchase.create_purchase_data(client, gemini=None, ctx=ctx)
        po_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'purchase.order']
        assert po_batches == [], f"expected no purchase.order create_batch, got {po_batches}"
        results.append(("create_purchase_data: empty supplier pool -> no PO create_batch (Pattern 1)", True, ""))
    except AssertionError as e:
        results.append(("create_purchase_data: empty supplier pool -> no PO create_batch (Pattern 1)", False, str(e)))

    # ------------------------------------------------------------------
    # ctx.supplier_ids reused when already populated -> odoo_actions.create_suppliers
    # is never called (shared pool with accounting.py, no disjoint second set).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(supplier_ids=[555])
        with patch("modules.purchase.odoo_actions.create_suppliers") as mock_create_suppliers:
            purchase.create_purchase_data(client, gemini=None, ctx=ctx)
            mock_create_suppliers.assert_not_called()
        po_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'purchase.order']
        assert len(po_batches) == 1, po_batches
        partner_ids_used = {v["partner_id"] for v in po_batches[0].args[1]}
        assert partner_ids_used == {555}, partner_ids_used
        results.append(("create_purchase_data: reuses ctx.supplier_ids, no duplicate supplier creation", True, ""))
    except AssertionError as e:
        results.append(("create_purchase_data: reuses ctx.supplier_ids, no duplicate supplier creation", False, str(e)))

    # ------------------------------------------------------------------
    # Confirm dual-try: button_confirm fails, action_confirm succeeds (batch).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_purchase=2, confirm_pct=100, supplier_ids=[555])

        def _call_method(model, method, ids=None, args=None, kwargs=None, context=None):
            if model == 'purchase.order' and method == 'button_confirm':
                raise Exception("button_confirm not available")
            return True
        client.call_method.side_effect = _call_method

        purchase.create_purchase_data(client, gemini=None, ctx=ctx)
        methods_tried = [c.args[1] for c in client.call_method.call_args_list if c.args[0] == 'purchase.order']
        assert 'button_confirm' in methods_tried and 'action_confirm' in methods_tried, methods_tried
        results.append(("create_purchase_data: button_confirm fails -> action_confirm dual-try", True, f"{methods_tried}"))
    except AssertionError as e:
        results.append(("create_purchase_data: button_confirm fails -> action_confirm dual-try", False, str(e)))

    # ------------------------------------------------------------------
    # Bill creation: preferred action_create_invoice path used when it succeeds.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        po_ids = [1, 2]

        def _sr(model, domain=None, fields=None, limit=None, **kw):
            if model == 'purchase.order' and fields == ["invoice_ids"]:
                oid = domain[0][2]
                return [{"id": oid, "invoice_ids": [900 + oid]}]
            return []
        client.search_read.side_effect = _sr

        bill_ids, counts = purchase._create_bills_from_pos(client, po_ids)
        assert bill_ids == [901, 902], bill_ids
        assert counts == {"preferred": 2, "fallback": 0}, counts
        results.append(("_create_bills_from_pos: preferred action_create_invoice path", True, f"{bill_ids}"))
    except AssertionError as e:
        results.append(("_create_bills_from_pos: preferred action_create_invoice path", False, str(e)))

    # ------------------------------------------------------------------
    # Bill creation: action_create_invoice fails for one PO -> that PO falls
    # back to the manual account.move rebuild; the other PO's bill is
    # unaffected (per-order isolation, mirrors accounting.py's R8 shape).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()

        def _call_method(model, method, ids=None, args=None, kwargs=None, context=None):
            if model == 'purchase.order' and method == 'action_create_invoice' and ids == [2]:
                raise Exception("simulated: nothing to invoice")
            return True
        client.call_method.side_effect = _call_method

        def _sr(model, domain=None, fields=None, limit=None, **kw):
            if model == 'purchase.order' and fields == ["invoice_ids"]:
                oid = domain[0][2]
                return [{"id": oid, "invoice_ids": [900 + oid]}]
            if model == 'purchase.order' and fields and 'order_line' in fields:
                return [{"id": 2, "partner_id": [5, "P5"], "order_line": [30], "name": "P00002"}]
            if model == 'purchase.order.line':
                return [{"id": 30, "product_id": [12, "Z"], "product_qty": 3, "price_unit": 9.0}]
            return []
        client.search_read.side_effect = _sr

        bill_ids, counts = purchase._create_bills_from_pos(client, [1, 2])
        assert 901 in bill_ids, bill_ids
        assert counts["preferred"] == 1 and counts["fallback"] == 1, counts
        fallback_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'account.move']
        assert len(fallback_batches) == 1 and len(fallback_batches[0].args[1]) == 1, fallback_batches
        results.append(("_create_bills_from_pos: per-order isolation, manual fallback for failed PO only", True, f"{bill_ids}"))
    except AssertionError as e:
        results.append(("_create_bills_from_pos: per-order isolation, manual fallback for failed PO only", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 6: manual fallback (product_id as [id, name] tuple) is unpacked
    # correctly, not passed through as the raw tuple.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()

        def _sr(model, domain=None, fields=None, limit=None, **kw):
            if model == 'purchase.order':
                return [{"id": 9, "partner_id": [77, "Supplier X"], "order_line": [40], "name": "P00009"}]
            if model == 'purchase.order.line':
                return [{"id": 40, "product_id": [88, "Widget"], "product_qty": 4, "price_unit": 12.5}]
            return []
        client.search_read.side_effect = _sr

        bill_ids = purchase._create_bills_from_pos_manual(client, [9])
        assert len(bill_ids) == 1
        move_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'account.move']
        vals = move_batches[0].args[1][0]
        assert vals["partner_id"] == 77, f"partner_id tuple not unpacked: {vals['partner_id']!r}"
        line = vals["invoice_line_ids"][0][2]
        assert line["product_id"] == 88, f"product_id tuple not unpacked: {line['product_id']!r}"
        results.append(("_create_bills_from_pos_manual: partner_id/product_id tuples unpacked (Pattern 6)", True, ""))
    except AssertionError as e:
        results.append(("_create_bills_from_pos_manual: partner_id/product_id tuples unpacked (Pattern 6)", False, str(e)))

    # ==================================================================
    # S15/R20 — analytic distribution wiring
    # ==================================================================

    try:
        # Pattern 3: analytic disabled (default) -> helper never called, no
        # analytic_distribution in the created PO-line vals.
        client = _mock_client()
        ctx = _make_ctx(num_purchase=2, supplier_ids=[555])
        with patch("modules.purchase.odoo_actions.get_or_create_analytic_accounts") as mock_helper:
            purchase.create_purchase_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_not_called()
        po_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'purchase.order']
        for po_vals in po_batches[0].args[1]:
            for cmd in po_vals["order_line"]:
                assert "analytic_distribution" not in cmd[2], cmd
        results.append(("create_purchase_data: analytic disabled -> no helper call, no distribution (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_purchase_data: analytic disabled -> no helper call, no distribution (Pattern 3)", False, str(e)))

    try:
        # purchase_pct=0 with analytic enabled -> its own sub-off-switch.
        client = _mock_client()
        ctx = _make_ctx(num_purchase=2, supplier_ids=[555],
                        analytic={"enabled": True, "sale_pct": 50, "purchase_pct": 0, "expense_pct": 50})
        with patch("modules.purchase.odoo_actions.get_or_create_analytic_accounts") as mock_helper:
            purchase.create_purchase_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_not_called()
        results.append(("create_purchase_data: purchase_pct=0 -> no helper call (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_purchase_data: purchase_pct=0 -> no helper call (Pattern 3)", False, str(e)))

    try:
        # Happy path: the flat po_line_vals_list mutation (assign_analytic_
        # distribution) must show up INSIDE the (0,0,dict) tuples nested in
        # each order's order_line — same dict objects by reference.
        client = _mock_client()
        ctx = _make_ctx(num_purchase=3, supplier_ids=[555], component_ids=[1, 2, 3, 4, 5],
                        analytic={"enabled": True, "sale_pct": 0, "purchase_pct": 100, "expense_pct": 0})
        with patch("modules.purchase.odoo_actions.get_or_create_analytic_accounts",
                  return_value=[801, 802]) as mock_helper:
            purchase.create_purchase_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_called_once()
        po_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'purchase.order']
        assert len(po_batches) == 1, po_batches
        all_line_vals = [cmd[2] for po_vals in po_batches[0].args[1] for cmd in po_vals["order_line"]]
        assert all_line_vals, "expected at least one PO line"
        assert all("analytic_distribution" in v for v in all_line_vals), \
            "purchase_pct=100 must reach every created line"
        for v in all_line_vals:
            keys = list(v["analytic_distribution"].keys())
            assert len(keys) == 1 and int(keys[0]) in (801, 802), v
        results.append(("create_purchase_data: analytic enabled -> distribution reaches nested order_line tuples", True,
                        f"{len(all_line_vals)} lines"))
    except AssertionError as e:
        results.append(("create_purchase_data: analytic enabled -> distribution reaches nested order_line tuples", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
