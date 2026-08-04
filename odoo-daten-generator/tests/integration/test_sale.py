import sys
import os
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.sale import (
    create_sale_order, confirm_sale_orders, link_order_to_opportunity,
    _move_won_opportunities, create_sale_data,
)
from modules.crm import create_opportunity
from config import DemoCriteria, ModuleSelections, RunContext


def _make_rctx(num_orders):
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit, module_selections=ModuleSelections(sale=num_orders), industry="IT",
        language_name="German", language_code="de_DE", gemini_model_name="test",
    )


def run(client, ctx):
    """
    Consumes: ctx.product_ids, ctx.partner_ids
    Populates: ctx.order_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    if not ctx.product_ids or not ctx.partner_ids:
        results.append(("sale: SKIP — missing product_ids or partner_ids in ctx", False, "master_data must run first"))
        return False, results

    partner_id = ctx.partner_ids[0]
    product_id = ctx.product_ids[0]
    order_id = None

    # Step 1 — Create sale order (draft)
    try:
        order_id = create_sale_order(client, {
            "partner_id": partner_id,
            "order_line": [(0, 0, {
                "product_id": product_id,
                "product_uom_qty": 2,
                "price_unit": 49.99,
            })],
        })
        assert isinstance(order_id, int) and order_id > 0
        rec = client.search_read(
            'sale.order',
            [["id", "=", order_id]],
            fields=["state"],
            limit=1,
        )
        assert rec and rec[0]["state"] == "draft"
        ctx.order_ids.append(order_id)
        results.append(("sale: create sale order (state=draft)", True, order_id))
    except Exception as e:
        results.append(("sale: create sale order (state=draft)", False, str(e)))
        order_id = None

    # Step 2 — Confirm sale order
    try:
        assert order_id, "No order created in step 1"
        confirm_sale_orders(client, [order_id])
        rec = client.search_read(
            'sale.order',
            [["id", "=", order_id]],
            fields=["state"],
            limit=1,
        )
        assert rec
        state = rec[0]["state"]
        assert state in ("sale", "done"), f"Unexpected state: {state}"
        results.append(("sale: confirm sale order", True, f"state={state}"))
    except Exception as e:
        results.append(("sale: confirm sale order", False, str(e)))

    # Step 3 — link order to opportunity (live)
    opp_id = ctx.opportunity_ids[0] if ctx.opportunity_ids else None
    if opp_id and order_id:
        try:
            link_order_to_opportunity(client, order_id, opp_id)
            rec = client.search_read(
                'sale.order', [["id", "=", order_id]], fields=["opportunity_id"], limit=1,
            )
            assert rec, "Order not found after link"
            val = rec[0].get("opportunity_id")
            linked_id = val[0] if isinstance(val, (list, tuple)) else val
            assert linked_id == opp_id, f"Expected opp_id={opp_id}, got {linked_id}"
            results.append(("sale: link_order_to_opportunity + read-back", True, f"opp_id={opp_id}"))
        except Exception as e:
            results.append(("sale: link_order_to_opportunity + read-back", False, str(e)))
    else:
        results.append(("sale: link_order_to_opportunity SKIP — no opportunity or order", True, "skipped"))

    # Step 4 — _move_won_opportunities (mock)
    try:
        mock_client = MagicMock()
        # Simulate a confirmed order with an opportunity link
        mock_client.search_read.side_effect = [
            # first call: crm.stage search → returns Won stage
            [{"id": 99, "name": "Won"}],
            # second call: sale.order search → returns order with opportunity_id
            [{"id": order_id or 1, "opportunity_id": [opp_id or 1, "Test Opp"]}],
        ]
        from config import DemoCriteria, ModuleSelections, RunContext
        criteria = DemoCriteria(
            mode="both", industry="Test", num_companies=1,
            num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
            num_services=0, num_consumables=0, num_storables=0,
        )
        mock_ctx = RunContext(
            criteria=criteria, module_selections=ModuleSelections(),
            industry="Test", language_name="German", language_code="de_DE",
            gemini_model_name="test",
        )
        mock_ctx.confirmed_order_ids = [order_id or 1]
        mock_ctx.opportunity_ids = [opp_id or 1]
        _move_won_opportunities(mock_client, mock_ctx)
        mock_client.write.assert_called_once_with('crm.lead', [opp_id or 1], {"stage_id": 99})
        results.append(("sale: _move_won_opportunities writes Won stage_id", True, "stage_id=99"))
    except Exception as e:
        results.append(("sale: _move_won_opportunities writes Won stage_id", False, str(e)))

    # Step 5 — B8: create_sale_data end-to-end, confirm count scales with
    # order count (was hardcoded to a fixed 5), read-back.
    try:
        rctx = _make_rctx(num_orders=10)
        rctx.company_ids = [partner_id]
        rctx.product_ids = ctx.product_ids
        create_sale_data(client, None, rctx)
        assert len(rctx.order_ids) == 10, f"expected 10 orders, got {len(rctx.order_ids)}"
        expected_confirmed = max(1, round(10 * rctx.module_selections.sale_confirm_pct / 100))
        assert expected_confirmed != 5, "test setup coincidentally matches the old hardcoded 5"
        assert len(rctx.confirmed_order_ids) == expected_confirmed, (
            f"expected {expected_confirmed} confirmed orders, got {len(rctx.confirmed_order_ids)}"
        )
        confirmed = client.search_read(
            'sale.order', [["id", "in", rctx.confirmed_order_ids]], fields=["state"], limit=0,
        )
        assert all(o["state"] in ("sale", "done") for o in confirmed), confirmed
        results.append((
            "sale: create_sale_data end-to-end (B8 scaling), read-back",
            True, f"{len(rctx.order_ids)} orders, {len(rctx.confirmed_order_ids)} confirmed",
        ))
    except Exception as e:
        results.append(("sale: create_sale_data end-to-end (B8 scaling), read-back", False, str(e)))

    # Step 6 — B14: orders link to the opportunity of the SAME partner, not by
    # position. Two partners, one opportunity each, opportunity list built in
    # the OPPOSITE order from the company list — a positional zip() would
    # cross-link them; the partner-matched fix must not.
    try:
        partner_a = partner_id
        partner_b = client.create('res.partner', {"name": "Integration Test B14 Partner B"})
        opp_a = create_opportunity(client, partner_a, "B14 Opp A")
        opp_b = create_opportunity(client, partner_b, "B14 Opp B")

        rctx = _make_rctx(num_orders=2)
        rctx.company_ids = [partner_a, partner_b]
        rctx.product_ids = ctx.product_ids
        rctx.opportunity_ids = [opp_b, opp_a]  # deliberately reversed vs. company order

        create_sale_data(client, None, rctx)
        assert len(rctx.order_ids) == 2, f"expected 2 orders, got {len(rctx.order_ids)}"

        orders = client.search_read(
            'sale.order', [["id", "in", rctx.order_ids]],
            fields=["partner_id", "opportunity_id"], limit=0,
        )
        assert len(orders) == 2, orders

        def _unwrap(v):
            return v[0] if isinstance(v, (list, tuple)) else v

        expected = {partner_a: opp_a, partner_b: opp_b}
        for order in orders:
            order_partner = _unwrap(order["partner_id"])
            order_opp = _unwrap(order["opportunity_id"])
            assert order_opp == expected[order_partner], (
                f"order for partner {order_partner} linked to opp {order_opp}, "
                f"expected {expected[order_partner]} (cross-partner link — B14 regressed)"
            )
        results.append(("sale: create_sale_data links orders to same-partner opportunity (B14)", True, "2/2 matched"))
    except Exception as e:
        results.append(("sale: create_sale_data links orders to same-partner opportunity (B14)", False, str(e)))

    # Step 7 — B8: sale_confirm_pct=50 (non-default) is honored end-to-end,
    # read-back on confirmed order state. Distinct from Step 5, which
    # validates the unmodified 65% default path.
    try:
        rctx = _make_rctx(num_orders=10)
        rctx.module_selections.sale_confirm_pct = 50
        rctx.company_ids = [partner_id]
        rctx.product_ids = ctx.product_ids
        create_sale_data(client, None, rctx)
        assert len(rctx.order_ids) == 10, f"expected 10 orders, got {len(rctx.order_ids)}"
        assert len(rctx.confirmed_order_ids) == 5, (
            f"expected 5 confirmed orders (50% of 10), got {len(rctx.confirmed_order_ids)}"
        )
        confirmed = client.search_read(
            'sale.order', [["id", "in", rctx.confirmed_order_ids]], fields=["state"], limit=0,
        )
        assert all(o["state"] in ("sale", "done") for o in confirmed), confirmed
        results.append((
            "sale: create_sale_data honors sale_confirm_pct=50, read-back",
            True, f"{len(rctx.confirmed_order_ids)}/10 confirmed",
        ))
    except Exception as e:
        results.append(("sale: create_sale_data honors sale_confirm_pct=50, read-back", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
