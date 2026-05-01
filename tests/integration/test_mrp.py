import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import odoo_actions  # kept for create_product (shared utility)
from modules.mrp import (
    get_product_template_id,
    create_bom,
    create_bom_line,
    create_bom_operation,
    create_manufacturing_order,
    confirm_manufacturing_order,
    create_workcenter,
)


def run(client, ctx):
    """
    Consumes: ctx.product_ids
    Populates: ctx.workcenter_ids, ctx.bom_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    if not ctx.product_ids:
        results.append(("mrp: SKIP — no product_ids in ctx", False, "master_data must run first"))
        return False, results

    mrp_routings_on = getattr(ctx, 'feature_flags', {}).get('mrp_routings', True)

    # Step 1 — Work Center
    wc_id = None
    if not mrp_routings_on:
        results.append(("mrp: create workcenter + read-back costs_hour", True, "SKIP — mrp_routings not enabled"))
    else:
        try:
            wc_id = create_workcenter(client, {
                "name": "Integration Test Arbeitsplatz",
                "costs_hour": 75.0,
                "time_efficiency": 100.0,
            })
            assert isinstance(wc_id, int) and wc_id > 0
            rec = client.search_read(
                'mrp.workcenter',
                [["id", "=", wc_id]],
                fields=["costs_hour"],
                limit=1,
            )
            assert rec and abs(rec[0]["costs_hour"] - 75.0) < 0.01
            ctx.workcenter_ids.append(wc_id)
            results.append(("mrp: create workcenter + read-back costs_hour", True, wc_id))
        except Exception as e:
            results.append(("mrp: create workcenter + read-back costs_hour", False, str(e)))

    # Step 2 — BOM (use first product as finished good)
    bom_id = None
    fin_product_id = ctx.product_ids[0]
    try:
        tmpl_id = get_product_template_id(client, fin_product_id)
        assert tmpl_id, "Could not resolve product_tmpl_id"
        bom_id = create_bom(client, tmpl_id, product_id=fin_product_id, quantity=1.0)
        assert isinstance(bom_id, int) and bom_id > 0
        ctx.bom_ids.append(bom_id)
        results.append(("mrp: create BOM", True, bom_id))
    except Exception as e:
        results.append(("mrp: create BOM", False, str(e)))

    # Step 3 — BOM line (use last product as component to avoid self-reference)
    try:
        assert bom_id, "No BOM created in step 2"
        component_id = ctx.product_ids[-1]
        line_id = create_bom_line(client, bom_id, component_id, 2.0)
        assert isinstance(line_id, int) and line_id > 0
        results.append(("mrp: create BOM line", True, line_id))
    except Exception as e:
        results.append(("mrp: create BOM line", False, str(e)))

    # Step 4 — BOM Operation
    if not mrp_routings_on:
        results.append(("mrp: create BOM operation + read-back workcenter_id", True, "SKIP — mrp_routings not enabled"))
    else:
        try:
            assert bom_id, "No BOM created in step 2"
            assert wc_id, "No workcenter created in step 1"
            op_id = create_bom_operation(client, {
                "name": "Integration Test Operation",
                "bom_id": bom_id,
                "workcenter_id": wc_id,
                "sequence": 10,
                "time_cycle_manual": 30.0,
            })
            assert isinstance(op_id, int) and op_id > 0
            rec = client.search_read(
                'mrp.routing.workcenter',
                [["id", "=", op_id]],
                fields=["workcenter_id"],
                limit=1,
            )
            assert rec
            wc_val = rec[0]["workcenter_id"]
            wc_val = wc_val[0] if isinstance(wc_val, (list, tuple)) else wc_val
            assert wc_val == wc_id
            results.append(("mrp: create BOM operation + read-back workcenter_id", True, op_id))
        except Exception as e:
            results.append(("mrp: create BOM operation + read-back workcenter_id", False, str(e)))

    # Step 5 — Manufacturing Order
    mo_id = None
    try:
        assert bom_id, "No BOM created in step 2"
        mo_id = create_manufacturing_order(client, {
            "product_id": fin_product_id,
            "product_qty": 5.0,
            "bom_id": bom_id,
        })
        assert isinstance(mo_id, int) and mo_id > 0
        results.append(("mrp: create manufacturing order", True, mo_id))
    except Exception as e:
        results.append(("mrp: create manufacturing order", False, str(e)))
        mo_id = None

    # Step 6 — Confirm MO
    try:
        assert mo_id, "No MO created in step 5"
        ok = confirm_manufacturing_order(client, mo_id)
        assert ok
        rec = client.search_read(
            'mrp.production',
            [["id", "=", mo_id]],
            fields=["state"],
            limit=1,
        )
        assert rec
        state = rec[0]["state"]
        assert state in ("confirmed", "progress"), f"Unexpected state: {state}"
        results.append(("mrp: confirm manufacturing order", True, f"state={state}"))
    except Exception as e:
        results.append(("mrp: confirm manufacturing order", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
