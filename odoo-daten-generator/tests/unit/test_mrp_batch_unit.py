"""Unit tests for modules/mrp.py — D3 batch-creation call-count guard.

create_mrp_data must issue exactly 4 create_batch calls regardless of
num_products/components_per_bom/sub_boms_per_product: main products,
components, raw materials (for sub-BOMs), and BOMs (main + sub, with
bom_line_ids inlined) — never a per-record create() loop.
"""
import os
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import mrp


def _make_ctx(mrp_config):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=criteria, module_selections=ModuleSelections(mrp=mrp_config), industry="IT",
        language_name="German", language_code="de", gemini_model_name="test",
    )


def _workcenter_vals(client):
    """Flatten every create_batch('mrp.workcenter', vals_list) call into one list of vals."""
    vals = []
    for call in client.create_batch.call_args_list:
        if call.args[0] == 'mrp.workcenter':
            vals.extend(call.args[1])
    return vals


def _mock_client():
    client = MagicMock()
    counter = {"n": 3000, "tmpl": 9000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'product.product':
            # get_product_template_ids_bulk sends [["id", "in", [...]]] — one
            # row per requested id, each with its own synthetic template id.
            ids = next((clause[2] for clause in (domain or []) if clause[0] == "id" and clause[1] == "in"), [])
            rows = []
            for pid in ids:
                counter["tmpl"] += 1
                rows.append({"id": pid, "product_tmpl_id": [counter["tmpl"], "tmpl"]})
            return rows
        return []

    client.create_batch.side_effect = _create_batch
    client.search_read.side_effect = _search_read
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # D3: exactly 4 create_batch calls (main products, components, raw
    # materials, BOMs) regardless of product/component counts; no
    # individual create()/odoo_actions.create_product() fallback.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx({
            "num_products": 3, "components_per_bom": 2, "sub_boms_per_product": 1,
            # num_workcenters intentionally left forceable to >=1 by the still-open B15 bug
            # (max(1, ...)) — mrp_routings=False sidesteps that so this test stays scoped
            # to D3 (products/components/BOMs), not B15's separate workcenter concern.
            "num_workcenters": 0, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        ctx.feature_flags = {"mrp_routings": False}
        with patch("modules.mrp.odoo_actions.create_product") as mock_create_product:
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
            mock_create_product.assert_not_called()
        assert client.create_batch.call_count == 4, client.create_batch.call_count
        individually_created_models = [call.args[0] for call in client.create.call_args_list]
        assert 'product.product' not in individually_created_models, individually_created_models
        assert 'mrp.bom' not in individually_created_models, individually_created_models
        assert 'mrp.bom.line' not in individually_created_models, individually_created_models
        assert len(ctx.product_ids) == 3, ctx.product_ids
        # 3 products x 2 components = 6 components + 3 x 1 sub-bom x raw_count(2) = 6 raw -> 12 component_ids
        assert len(ctx.component_ids) == 12, ctx.component_ids
        results.append((
            "create_mrp_data: products/components/BOMs via create_batch, not per-record create()",
            True, f"create_batch calls={client.create_batch.call_count}, components={len(ctx.component_ids)}",
        ))
    except AssertionError as e:
        results.append(("create_mrp_data: products/components/BOMs via create_batch, not per-record create()", False, str(e)))

    # ------------------------------------------------------------------
    # R3 (S8): components/raw materials must be is_storable=True so they can
    # hold real stock.quant on-hand — call_args_list order confirmed above:
    # [0]=main products, [1]=components, [2]=raw materials, [3]=BOMs.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx({
            "num_products": 3, "components_per_bom": 2, "sub_boms_per_product": 1,
            "num_workcenters": 0, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        ctx.feature_flags = {"mrp_routings": False}
        with patch("modules.mrp.odoo_actions.create_product"):
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        component_vals = client.create_batch.call_args_list[1].args[1]
        assert component_vals and all(v.get("is_storable") is True for v in component_vals), component_vals
        raw_vals = client.create_batch.call_args_list[2].args[1]
        assert raw_vals and all(v.get("is_storable") is True for v in raw_vals), raw_vals
        results.append((
            "create_mrp_data: components + raw materials are is_storable=True (R3)",
            True, f"components={len(component_vals)}, raw={len(raw_vals)}",
        ))
    except AssertionError as e:
        results.append(("create_mrp_data: components + raw materials are is_storable=True (R3)", False, str(e)))

    # ------------------------------------------------------------------
    # D3: with sub_boms_per_product=0, still exactly 4 create_batch calls
    # (raw-material and would-be-empty batches are client-level no-ops, not
    # skipped mrp.py-level calls) and no sub-BOMs/raw materials created.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx({
            "num_products": 2, "components_per_bom": 3, "sub_boms_per_product": 0,
            "num_workcenters": 0, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        with patch("modules.mrp.odoo_actions.create_product"):
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        assert client.create_batch.call_count == 4, client.create_batch.call_count
        assert len(ctx.component_ids) == 6, ctx.component_ids  # 2 products x 3 components, no raw materials
        # raw_vals_list is empty here (sub_boms_per_product=0) — only check the
        # components batch; all(...) over an empty raw list would pass vacuously.
        component_vals = client.create_batch.call_args_list[1].args[1]
        assert component_vals and all(v.get("is_storable") is True for v in component_vals), component_vals
        results.append((
            "create_mrp_data: sub_boms_per_product=0 -> no raw materials, still 4 calls",
            True, f"components={len(ctx.component_ids)}",
        ))
    except AssertionError as e:
        results.append(("create_mrp_data: sub_boms_per_product=0 -> no raw materials, still 4 calls", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 5: num_products=0 -> early return, no create_batch calls at all
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx({"num_products": 0})
        mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        assert ctx.product_ids == []
        results.append(("create_mrp_data: num_products=0 -> no create_batch calls (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_mrp_data: num_products=0 -> no create_batch calls (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # B15: num_workcenters=0 must stay 0 (was max(1, ...), forcing >=1) even
    # when mrp_routings is explicitly enabled — no workcenter gets created.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx({
            "num_products": 1, "components_per_bom": 1, "sub_boms_per_product": 0,
            "num_workcenters": 0, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        ctx.feature_flags = {"mrp_routings": True}  # routings ON, but 0 workcenters requested
        with patch("modules.mrp.odoo_actions.create_product"):
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        workcenter_creates = _workcenter_vals(client)
        assert workcenter_creates == [], f"B15 regressed: created workcenters despite num_workcenters=0: {workcenter_creates}"
        results.append(("create_mrp_data: num_workcenters=0 -> no workcenters created (B15)", True, ""))
    except AssertionError as e:
        results.append(("create_mrp_data: num_workcenters=0 -> no workcenters created (B15)", False, str(e)))

    # ------------------------------------------------------------------
    # B15: missing mrp_routings key defaults to False (aligned with gui.py's
    # routings_on default) — must NOT create workcenters even with a
    # positive num_workcenters, matching the GUI's "off" default.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx({
            "num_products": 1, "components_per_bom": 1, "sub_boms_per_product": 0,
            "num_workcenters": 3, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        ctx.feature_flags = {}  # mrp_routings key absent entirely
        with patch("modules.mrp.odoo_actions.create_product"):
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        workcenter_creates = _workcenter_vals(client)
        assert workcenter_creates == [], (
            f"B15 default asymmetry: missing mrp_routings key created workcenters "
            f"(GUI defaults this to off): {workcenter_creates}"
        )
        results.append(("create_mrp_data: missing mrp_routings key defaults to off, matching gui.py (B15)", True, ""))
    except AssertionError as e:
        results.append(("create_mrp_data: missing mrp_routings key defaults to off, matching gui.py (B15)", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 (Pattern 3): mrp_routings=True + num_workcenters>0 is not
    # enough on its own any more — model_access={'mrp.workcenter': False}
    # (a real 403/no-rights-group case, not a settings-off case, which
    # feature_flags already covers) must still block work center creation.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx({
            "num_products": 1, "components_per_bom": 1, "sub_boms_per_product": 0,
            "num_workcenters": 3, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        ctx.feature_flags = {"mrp_routings": True}
        ctx.model_access = {"mrp.workcenter": False}
        with patch("modules.mrp.odoo_actions.create_product"):
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        workcenter_creates = _workcenter_vals(client)
        assert workcenter_creates == [], (
            f"model_access blocking mrp.workcenter must prevent workcenter creation: {workcenter_creates}"
        )
        results.append(("create_mrp_data: model_access blocks mrp.workcenter even with mrp_routings=True (Pattern 3)",
                        True, ""))
    except AssertionError as e:
        results.append(("create_mrp_data: model_access blocks mrp.workcenter even with mrp_routings=True (Pattern 3)",
                        False, str(e)))

    try:
        # The converse — an EMPTY model_access (nothing probed, e.g. the
        # module wasn't in installed_modules at connect time) must default
        # open and not itself block workcenter creation.
        client = _mock_client()
        ctx = _make_ctx({
            "num_products": 1, "components_per_bom": 1, "sub_boms_per_product": 0,
            "num_workcenters": 2, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        ctx.feature_flags = {"mrp_routings": True}
        ctx.model_access = {}
        with patch("modules.mrp.odoo_actions.create_product"):
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        workcenter_creates = _workcenter_vals(client)
        assert len(workcenter_creates) == 2, workcenter_creates
        results.append(("create_mrp_data: empty model_access defaults open, does not block (B1 guard)", True, ""))
    except AssertionError as e:
        results.append(("create_mrp_data: empty model_access defaults open, does not block (B1 guard)", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 (A6): company_id passed to mrp.workcenter must come from
    # odoo_actions.get_main_company_id(client) — a real res.company id — not
    # ctx.company_ids[0], which holds res.partner ids (customer contacts).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        client.search_read.side_effect = None

        def _search_read_with_company(model, domain=None, fields=None, limit=None, **kw):
            if model == 'product.product':
                ids = next((c[2] for c in (domain or []) if c[0] == "id" and c[1] == "in"), [])
                return [{"id": pid, "product_tmpl_id": [9001, "tmpl"]} for pid in ids]
            if model == 'res.company':
                return [{"id": 777}]  # the real res.company id
            return []

        client.search_read.side_effect = _search_read_with_company
        ctx = _make_ctx({
            "num_products": 1, "components_per_bom": 1, "sub_boms_per_product": 0,
            "num_workcenters": 1, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        ctx.feature_flags = {"mrp_routings": True}
        # A res.partner id, deliberately different from the res.company id
        # above — if the bug regressed, this is what would leak through.
        ctx.company_ids = [42]
        with patch("modules.mrp.odoo_actions.create_product"):
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        workcenter_creates = _workcenter_vals(client)
        assert workcenter_creates, "expected at least one workcenter create call"
        sent_company_id = workcenter_creates[0].get("company_id")
        assert sent_company_id == 777, (
            f"A6 regressed: expected the real res.company id (777) via "
            f"get_main_company_id, got {sent_company_id!r} (ctx.company_ids[0] would be 42)"
        )
        results.append(("create_mrp_data: mrp.workcenter.company_id uses get_main_company_id, not ctx.company_ids[0] (A6)",
                        True, f"company_id={sent_company_id}"))
    except AssertionError as e:
        results.append(("create_mrp_data: mrp.workcenter.company_id uses get_main_company_id, not ctx.company_ids[0] (A6)",
                        False, str(e)))

    # ------------------------------------------------------------------
    # Template-id lookups: N main products + M sub-BOM components -> exactly
    # 2 search_read('product.product', ...) calls total (one bulk lookup for
    # main products, one for sub-BOM components), not one per product.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx({
            "num_products": 3, "components_per_bom": 2, "sub_boms_per_product": 1,
            "num_workcenters": 0, "num_manufacturing_orders": 0, "create_quality_points": False,
        })
        ctx.feature_flags = {"mrp_routings": False}
        with patch("modules.mrp.odoo_actions.create_product"):
            mrp.create_mrp_data(client, gemini=None, ctx=ctx)
        tmpl_lookups = [c for c in client.search_read.call_args_list if c.args[0] == 'product.product']
        assert len(tmpl_lookups) == 2, tmpl_lookups
        results.append((
            "create_mrp_data: template-id lookups via 2 bulk search_read calls, not N per-product",
            True, f"search_read('product.product') calls={len(tmpl_lookups)}",
        ))
    except AssertionError as e:
        results.append(("create_mrp_data: template-id lookups via 2 bulk search_read calls, not N per-product",
                        False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
