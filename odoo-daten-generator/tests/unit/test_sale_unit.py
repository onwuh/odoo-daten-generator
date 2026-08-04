"""Unit tests for modules/sale.py — B8 (confirm count scales with order count)."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import sale


def _make_ctx(num_orders):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    ctx = RunContext(
        criteria=criteria, module_selections=ModuleSelections(sale=num_orders), industry="IT",
        language_name="German", language_code="de", gemini_model_name="test",
    )
    ctx.company_ids = [1, 2, 3]
    ctx.product_ids = [10, 11, 12]
    return ctx


def _mock_client():
    client = MagicMock()
    counter = {"n": 7000}

    def _create(model, vals, context=None):
        counter["n"] += 1
        return counter["n"]

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'product.product':
            return [{"id": pid} for pid in (10, 11, 12)]
        if model == 'sale.order':
            # confirm_sale_orders' read-back verification step
            return []
        return []

    client.create.side_effect = _create
    client.search_read.side_effect = _search_read
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # B8: confirmation count scales with order count, not fixed at 5.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=200)
        sale.create_sale_data(client, gemini=None, ctx=ctx)
        confirm_calls = [
            c for c in client.call_method.call_args_list if c.args[1] == 'action_confirm'
        ]
        assert confirm_calls, "action_confirm never called"
        confirmed_ids = confirm_calls[0].kwargs.get("ids", [])
        assert len(confirmed_ids) != 5, f"B8 regressed: still hardcoded to 5 (got {len(confirmed_ids)})"
        expected = max(1, round(200 * sale._DEFAULT_CONFIRM_PCT / 100))
        assert len(confirmed_ids) == expected, f"expected {expected} confirmed orders, got {len(confirmed_ids)}"
        results.append((
            "create_sale_data: 200 orders -> confirm count scales (not fixed 5)",
            True, f"confirmed={len(confirmed_ids)}/200",
        ))
    except AssertionError as e:
        results.append(("create_sale_data: 200 orders -> confirm count scales (not fixed 5)", False, str(e)))

    # ------------------------------------------------------------------
    # B8: small order counts still confirm at least 1, never 0.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=1)
        sale.create_sale_data(client, gemini=None, ctx=ctx)
        confirm_calls = [
            c for c in client.call_method.call_args_list if c.args[1] == 'action_confirm'
        ]
        confirmed_ids = confirm_calls[0].kwargs.get("ids", []) if confirm_calls else []
        assert len(confirmed_ids) == 1, f"expected 1 confirmed order, got {len(confirmed_ids)}"
        results.append(("create_sale_data: 1 order -> confirms exactly 1 (never 0)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: 1 order -> confirms exactly 1 (never 0)", False, str(e)))

    # ------------------------------------------------------------------
    # B14: orders link to an opportunity of the SAME partner, not by position.
    # Setup: order for partner 2 created first (positionally at index 0), but
    # the only opportunity belongs to partner 1 -> old zip() would have wrongly
    # linked them; correct behavior links partner-1's opportunity to nothing
    # (no partner-1 order exists) and leaves the partner-2 order unlinked.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=1)
        ctx.company_ids = [2]  # only company 2 gets an order this run
        ctx.opportunity_ids = [500]

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'product.product':
                return [{"id": pid} for pid in (10, 11, 12)]
            if model == 'crm.lead':
                return [{"id": 500, "partner_id": [1, "Partner 1"]}]  # belongs to partner 1, not 2
            return []
        client.search_read.side_effect = _search_read

        sale.create_sale_data(client, gemini=None, ctx=ctx)
        write_calls = [c for c in client.write.call_args_list if c.args[0] == 'sale.order']
        assert not write_calls, f"B14 regressed: linked mismatched-partner order/opportunity: {write_calls}"
        results.append(("create_sale_data: no cross-partner order/opportunity link (B14)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: no cross-partner order/opportunity link (B14)", False, str(e)))

    # ------------------------------------------------------------------
    # B14: correct match IS made when the partner does line up.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=1)
        ctx.company_ids = [1]
        ctx.opportunity_ids = [500]

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'product.product':
                return [{"id": pid} for pid in (10, 11, 12)]
            if model == 'crm.lead':
                return [{"id": 500, "partner_id": [1, "Partner 1"]}]
            return []
        client.search_read.side_effect = _search_read

        sale.create_sale_data(client, gemini=None, ctx=ctx)
        write_calls = [c for c in client.write.call_args_list if c.args[0] == 'sale.order']
        assert len(write_calls) == 1, f"expected 1 link write, got {len(write_calls)}"
        assert write_calls[0].args[2] == {"opportunity_id": 500}, write_calls[0].args
        results.append(("create_sale_data: same-partner order/opportunity gets linked (B14)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: same-partner order/opportunity gets linked (B14)", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
