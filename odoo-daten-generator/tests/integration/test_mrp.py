import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, MrpConfig, RunContext
from modules.mrp import (
    get_product_template_id,
    create_bom,
    create_bom_line,
    create_bom_operation,
    create_manufacturing_order,
    confirm_manufacturing_order,
    create_workcenter,
    create_mrp_data,
)


def _make_rctx(mrp_config):
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit, module_selections=ModuleSelections(mrp=mrp_config), industry="IT",
        language_name="German", language_code="de_DE", gemini_model_name="test",
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

    # Step 7 — D3: create_mrp_data end-to-end (batched products/components/BOMs
    # with bom_line_ids inlined), gemini=None to prove it needs no LLM call.
    # mrp_routings disabled to keep this test scoped to D3 (products/BOMs),
    # independent of the still-open B15 workcenter default.
    #
    # create_quality_points stays False (and num_manufacturing_orders 0) by
    # default — that path never runs here otherwise, which is exactly why
    # R18's test_report_type="none" bug went unnoticed (see ROADMAP.md).
    # S11/WP1 flips both under ODOO_GENERATOR_CAPTURE_FIELDS so a
    # manifest-capture run also sees quality.point/quality.check's real
    # fields — quality.check needs a confirmed MO to link to
    # (production_id), so num_manufacturing_orders must be >0 here too, not
    # just create_quality_points (S14/R18 fix, see ROADMAP.md's S14 WP-
    # Sequenz note on this exact gap).
    try:
        _capture = os.environ.get("ODOO_GENERATOR_CAPTURE_FIELDS") == "1"
        rctx = _make_rctx(MrpConfig(
            num_products=2, components_per_bom=2, sub_boms_per_product=1,
            num_workcenters=0, num_manufacturing_orders=2 if _capture else 0,
            create_quality_points=_capture, quality_fail_pct=50,
        ))
        rctx.feature_flags = {
            "mrp_routings": False,
            "quality": os.environ.get("ODOO_GENERATOR_CAPTURE_FIELDS") == "1",
        }
        create_mrp_data(client, None, rctx)
        assert len(rctx.product_ids) == 2, f"expected 2 main products, got {len(rctx.product_ids)}"
        # 2 products x 2 components + 2 x 1 sub-bom x raw_count(2) = 4 + 4 = 8
        assert len(rctx.component_ids) == 8, f"expected 8 components/raw materials, got {len(rctx.component_ids)}"

        boms = client.search_read(
            'mrp.bom', [["product_id", "in", rctx.product_ids + rctx.component_ids]],
            fields=["product_id", "bom_line_ids"], limit=0,
        )
        # 2 main BOMs + 2 sub-BOMs (one per product's first component) = 4
        assert len(boms) == 4, f"expected 4 BOMs (main+sub), got {len(boms)}"
        assert all(b.get("bom_line_ids") for b in boms), "a BOM was created with no inlined bom_line_ids"

        results.append((
            "mrp: create_mrp_data end-to-end (D3 batch, inlined bom_line_ids), read-back",
            True, f"{len(rctx.product_ids)} products, {len(boms)} BOMs",
        ))
    except Exception as e:
        results.append(("mrp: create_mrp_data end-to-end (D3 batch, inlined bom_line_ids), read-back", False, str(e)))

    # ------------------------------------------------------------------
    # Step 8 — S14/R18: Quality Points + Checks live end-to-end,
    # unconditional (not gated behind ODOO_GENERATOR_CAPTURE_FIELDS like
    # Step 7 above) — Step 7 leaving the flag off by default is exactly why
    # the test_report_type="none" bug went unnoticed for so long. Uses the
    # real ctx.feature_flags from test_suite.py's live probe, not a
    # hardcoded one; skips gracefully if this instance has no Quality app.
    # ------------------------------------------------------------------
    if not getattr(ctx, 'feature_flags', {}).get('quality', False):
        results.append(("mrp: R18 quality points+checks SKIP — quality feature not active", True, "skipped"))
    else:
        try:
            q_rctx = _make_rctx(MrpConfig(
                num_products=1, components_per_bom=1, sub_boms_per_product=0,
                num_workcenters=0, num_manufacturing_orders=6,
                create_quality_points=True, quality_fail_pct=50,
            ))
            q_rctx.feature_flags = {"mrp_routings": False, "quality": True}
            create_mrp_data(client, None, q_rctx)

            points = client.search_read(
                'quality.point', [["product_ids", "in", q_rctx.product_ids]],
                fields=["apply_to", "product_ids", "test_report_type", "picking_type_ids"], limit=0,
            )
            assert points, "no quality.point created"
            for p in points:
                assert p["apply_to"] == "products", p
                assert p["test_report_type"] == "pdf", p

            checks = client.search_read(
                'quality.check', [["product_id", "in", q_rctx.product_ids]],
                fields=["point_id", "production_id", "quality_state"], limit=0,
            )
            assert checks, "no quality.check created"
            for c in checks:
                assert c["quality_state"] in ("pass", "fail"), c
                pid = c["point_id"]
                pid = pid[0] if isinstance(pid, (list, tuple)) else pid  # Pattern 6
                assert pid, c
                prod = c["production_id"]
                prod = prod[0] if isinstance(prod, (list, tuple)) else prod  # Pattern 6
                assert prod, c

            results.append((
                "mrp: R18 quality points+checks live end-to-end — apply_to/test_report_type/"
                "quality_state read-back (Pattern 4/6)",
                True, f"{len(points)} points, {len(checks)} checks",
            ))
        except Exception as e:
            results.append((
                "mrp: R18 quality points+checks live end-to-end — apply_to/test_report_type/"
                "quality_state read-back (Pattern 4/6)",
                False, str(e),
            ))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
