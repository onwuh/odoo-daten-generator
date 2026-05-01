import sys
import os
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.sale import create_sale_order, confirm_sale_orders, link_order_to_opportunity, _move_won_opportunities


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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
