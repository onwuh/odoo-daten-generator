"""Unit tests for modules/inventory.py (R3/S8, extended S13/R13-R15).

Patterns covered: 1 (empty storable pool / location pool never empty -> no
create_batch), 3 (stock={} or a feature flag off -> no matching API calls),
5 (missing prerequisites -> SKIP), 6 (lot_id m2o tuple read-back N/A here —
this module only writes lot_id, never reads it back), 7 (round-robin
location distribution is deterministic, not randomised — no seed needed),
8 (stock.lot/stock.quant batch-call-count checks).
"""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import inventory


def _make_ctx(stock_sel=None, company_ids=None, product_ids=None, component_ids=None,
              new_product_ids=None, feature_flags=None):
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
    ctx.new_product_ids = new_product_ids if new_product_ids is not None else list(ctx.product_ids)
    ctx.feature_flags = feature_flags if feature_flags is not None else {}
    return ctx


def _mock_client(warehouse=True, storable_products=None, second_warehouse_location_id=88,
                  lot_name_conflict=False):
    """storable_products: list of dicts, e.g. [{"id": 1, "tracking": "lot"}] —
    if None, defaults to plain {"id": pid} entries for [1, 2, 3, 4]."""
    client = MagicMock()
    counter = {"n": 8000, "wh": 500}

    def _create(model, values, context=None):
        if model == 'stock.warehouse':
            counter["wh"] += 1
            return counter["wh"]
        counter["n"] += 1
        return counter["n"]

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
            # get_default_warehouse filters on company_id; create_second_warehouse's
            # read-back filters on id — distinguish by domain shape.
            is_id_lookup = bool(domain) and any(
                isinstance(d, (list, tuple)) and d and d[0] == "id" for d in domain)
            if is_id_lookup:
                return [{"lot_stock_id": [second_warehouse_location_id, "WH2/Stock"]}]
            return [{"lot_stock_id": [1, "WH/Stock"], "in_type_id": [2, "WH/IN"]}] if warehouse else []
        if model == 'product.product':
            if storable_products is not None:
                return storable_products
            return [{"id": pid} for pid in [1, 2, 3, 4]]
        if model == 'stock.location':
            return []  # no pre-existing barcodes
        return []

    client.create.side_effect = _create
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
    # Pattern 3: stock={"avg_qty": 0} (non-empty dict, zero qty, no other
    # S13 keys set) -> still a full no-op — this is the exact case B1's
    # fix had to keep working: the early-return checks all three trigger
    # keys (avg_qty, sub_locations, second_warehouse), and with only
    # avg_qty present at 0 and the other two absent (-> their .get(...,
    # 0/False) defaults), the condition is exactly as true as before B1.
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
    # No warehouse resolvable -> graceful skip, no create_batch. Also true
    # with sub_locations>0 requested (S9/Pattern 5 extension, S13) — the
    # feature can't run without warehouse 1 either.
    # ------------------------------------------------------------------
    try:
        client = _mock_client(warehouse=False)
        ctx = _make_ctx()
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_inventory_data: no warehouse -> no calls, graceful skip", True, ""))
    except AssertionError as e:
        results.append(("create_inventory_data: no warehouse -> no calls, graceful skip", False, str(e)))

    try:
        client = _mock_client(warehouse=False)
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "sub_locations": 3})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        loc_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.location']
        assert loc_batches == [], loc_batches
        results.append(("create_inventory_data: no warehouse -> sub_locations skipped too (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_inventory_data: no warehouse -> sub_locations skipped too (Pattern 5)", False, str(e)))

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
        client = _mock_client(storable_products=[])
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
        client = _mock_client(storable_products=[{"id": 1}, {"id": 2}, {"id": 3}])
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
        client = _mock_client(storable_products=[{"id": 1}])
        client.call_method.side_effect = Exception("simulated apply failure")
        ctx = _make_ctx(stock_sel={"avg_qty": 5})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)  # must not raise
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        assert len(quant_batches) == 1 and len(quant_batches[0].args[1]) == 1, quant_batches
        results.append(("create_inventory_data: action_apply_inventory failure is non-fatal", True, ""))
    except Exception as e:
        results.append(("create_inventory_data: action_apply_inventory failure is non-fatal", False, str(e)))

    # ==================================================================
    # S13/WP2 — R15 Sub-Locations
    # ==================================================================

    # B1: sub_locations>0 with avg_qty=0 must still reach get_default_warehouse
    # (search_read fires) and create the locations — the early-return no
    # longer gates on avg_qty alone.
    try:
        client = _mock_client(storable_products=[{"id": 1}])
        ctx = _make_ctx(stock_sel={"avg_qty": 0, "sub_locations": 2})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        assert client.search_read.call_count > 0, "expected search_read to fire (warehouse lookup)"
        loc_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.location']
        assert len(loc_batches) == 1 and len(loc_batches[0].args[1]) == 2, loc_batches
        for v in loc_batches[0].args[1]:
            assert v["usage"] == "internal" and v["location_id"] == 1 and "barcode" in v, v
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        assert quant_batches == [], "avg_qty=0 must still skip quant seeding"
        results.append(("S13/B1: sub_locations>0 with avg_qty=0 -> locations created, no quants", True, ""))
    except AssertionError as e:
        results.append(("S13/B1: sub_locations>0 with avg_qty=0 -> locations created, no quants", False, str(e)))

    # Pattern 3: sub_locations=0 (default) -> no stock.location create_batch.
    try:
        client = _mock_client(storable_products=[{"id": 1}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        loc_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.location']
        assert loc_batches == [], loc_batches
        results.append(("S13: sub_locations=0 -> no stock.location calls (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("S13: sub_locations=0 -> no stock.location calls (Pattern 3)", False, str(e)))

    # Pattern 1: the location pool the round-robin draws from is never empty
    # by construction (warehouse root is always in it once reached) —
    # verified here by confirming an even, deterministic cycle across it.
    try:
        client = _mock_client(storable_products=[{"id": i} for i in range(1, 7)])
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "sub_locations": 2})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        vals_list = quant_batches[0].args[1]
        seen_locations = [v["location_id"] for v in vals_list]
        # Pool is [warehouse_root=1, subloc_a, subloc_b] (3 entries) for 6
        # products -> round-robin visits each exactly twice.
        assert len(set(seen_locations)) == 3, seen_locations
        from collections import Counter
        assert set(Counter(seen_locations).values()) == {2}, seen_locations
        results.append(("S13/Pattern1: location pool round-robin covers all locations evenly", True, f"{seen_locations}"))
    except AssertionError as e:
        results.append(("S13/Pattern1: location pool round-robin covers all locations evenly", False, str(e)))

    # ==================================================================
    # S13/WP3 — R14 Multi-Warehouse
    # ==================================================================

    # Pattern 3: second_warehouse=False (default) -> no stock.warehouse create.
    try:
        client = _mock_client(storable_products=[{"id": 1}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        wh_creates = [c for c in client.create.call_args_list if c.args[0] == 'stock.warehouse']
        assert wh_creates == [], wh_creates
        results.append(("S13: second_warehouse=False -> no stock.warehouse.create (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("S13: second_warehouse=False -> no stock.warehouse.create (Pattern 3)", False, str(e)))

    # S-C: second warehouse is created even when the DEFAULT warehouse can't
    # be found afterwards — it only needs company_id, not warehouse 1.
    try:
        client = _mock_client(warehouse=False)
        ctx = _make_ctx(stock_sel={"avg_qty": 0, "second_warehouse": True})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        wh_creates = [c for c in client.create.call_args_list if c.args[0] == 'stock.warehouse']
        assert len(wh_creates) == 1, wh_creates
        assert wh_creates[0].args[1]["company_id"] == 1, wh_creates[0].args[1]
        results.append(("S13/S-C: second warehouse created even with no default warehouse", True, ""))
    except AssertionError as e:
        results.append(("S13/S-C: second warehouse created even with no default warehouse", False, str(e)))

    # Happy path: second warehouse's stock location joins the pool alongside
    # warehouse 1's — with 2 storables the round-robin hits both exactly once.
    try:
        client = _mock_client(storable_products=[{"id": 1}, {"id": 2}], second_warehouse_location_id=77)
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "second_warehouse": True})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        locations = sorted(v["location_id"] for v in quant_batches[0].args[1])
        assert locations == [1, 77], locations
        results.append(("S13: second warehouse's location joins the round-robin pool", True, f"{locations}"))
    except AssertionError as e:
        results.append(("S13: second warehouse's location joins the round-robin pool", False, str(e)))

    # ==================================================================
    # S13/WP4 — R13 Lot-/Serial-Tracking
    # ==================================================================

    # Pattern 1/happy: a 'lot'-tracked product (in new_product_ids) gets
    # exactly 1 stock.lot and its quant carries that lot_id.
    try:
        client = _mock_client(storable_products=[{"id": 1, "tracking": "lot"}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20}, product_ids=[1], new_product_ids=[1])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        lot_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.lot']
        assert len(lot_batches) == 1 and len(lot_batches[0].args[1]) == 1, lot_batches
        lot_vals = lot_batches[0].args[1][0]
        assert lot_vals["product_id"] == 1 and lot_vals["name"] == "LOT-1-0000", lot_vals
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        quant_vals = quant_batches[0].args[1]
        assert len(quant_vals) == 1, quant_vals
        assert "lot_id" in quant_vals[0], quant_vals[0]
        results.append(("S13/R13: tracking='lot' -> 1 stock.lot, quant carries lot_id (Pattern 8)", True, ""))
    except AssertionError as e:
        results.append(("S13/R13: tracking='lot' -> 1 stock.lot, quant carries lot_id (Pattern 8)", False, str(e)))

    # A 'serial'-tracked product gets N quants of qty 1, each its own lot,
    # via exactly one stock.lot batch call (Pattern 8) — never N individual
    # stock.lot.create() calls.
    try:
        client = _mock_client(storable_products=[{"id": 1, "tracking": "serial"}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "tracking_serial_max": 5},
                        product_ids=[1], new_product_ids=[1])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        lot_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.lot']
        assert len(lot_batches) == 1, lot_batches  # Pattern 8: one batch call, not N
        n = len(lot_batches[0].args[1])
        assert 1 <= n <= 5, n
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        quant_vals = quant_batches[0].args[1]
        assert len(quant_vals) == n, (n, quant_vals)
        for v in quant_vals:
            assert v["inventory_quantity"] == 1, v
            assert "lot_id" in v, v
        lot_ids_used = {v["lot_id"] for v in quant_vals}
        assert len(lot_ids_used) == n, "each serial quant must get its own distinct lot"
        results.append(("S13/R13: tracking='serial' -> N qty-1 quants, own lots, one batch call (Pattern 8)", True, f"n={n}"))
    except AssertionError as e:
        results.append(("S13/R13: tracking='serial' -> N qty-1 quants, own lots, one batch call (Pattern 8)", False, str(e)))

    # Befund 4: tracking='lot' but the product is NOT in new_product_ids
    # (e.g. a use_existing customer product that happens to carry real
    # tracking) -> untouched bulk-quant path, no stock.lot at all.
    try:
        client = _mock_client(storable_products=[{"id": 1, "tracking": "lot"}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20}, product_ids=[1], new_product_ids=[])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        lot_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.lot']
        assert lot_batches == [], lot_batches
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        quant_vals = quant_batches[0].args[1]
        assert len(quant_vals) == 1 and "lot_id" not in quant_vals[0], quant_vals
        results.append(("S13/Befund 4: tracking='lot' but not in new_product_ids -> untouched bulk quant", True, ""))
    except AssertionError as e:
        results.append(("S13/Befund 4: tracking='lot' but not in new_product_ids -> untouched bulk quant", False, str(e)))

    # Pattern 3: tracking='none' (the default/Odoo default) -> no stock.lot
    # call at all, same as before S13.
    try:
        client = _mock_client(storable_products=[{"id": 1, "tracking": "none"}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20}, product_ids=[1], new_product_ids=[1])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        lot_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.lot']
        assert lot_batches == [], lot_batches
        results.append(("S13: tracking='none' -> no stock.lot call (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("S13: tracking='none' -> no stock.lot call (Pattern 3)", False, str(e)))

    # B-B: run-wide serial budget exhaustion degrades to the minimum valid
    # serial representation (1 quant qty 1 + 1 lot per product), never to
    # the bulk 'none' path (which would be invalid for a product Odoo
    # already thinks is tracking='serial').
    try:
        client = _mock_client(storable_products=[{"id": i, "tracking": "serial"} for i in range(1, 6)])
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "tracking_serial_max": 50},
                        product_ids=list(range(1, 6)), new_product_ids=list(range(1, 6)))
        original_cap = inventory._MAX_SERIAL_RECORDS_PER_RUN
        inventory._MAX_SERIAL_RECORDS_PER_RUN = 3  # force exhaustion well before 5 products x up-to-50 each
        try:
            inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        finally:
            inventory._MAX_SERIAL_RECORDS_PER_RUN = original_cap
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        quant_vals = quant_batches[0].args[1]
        # every single quant must still be qty 1 with its own lot_id -- none
        # ever falls back to a bulk quant with qty > 1 / no lot_id.
        for v in quant_vals:
            assert v["inventory_quantity"] == 1, v
            assert "lot_id" in v, v
        # at least one product must have been squeezed down to the minimum (1).
        by_product = {}
        for v in quant_vals:
            by_product.setdefault(v["product_id"], 0)
            by_product[v["product_id"]] += 1
        assert any(n == 1 for n in by_product.values()), by_product
        results.append(("S13/B-B: serial budget exhaustion degrades to 1 quant+1 lot, never to bulk", True, f"{by_product}"))
    except AssertionError as e:
        results.append(("S13/B-B: serial budget exhaustion degrades to 1 quant+1 lot, never to bulk", False, str(e)))

    # ==================================================================
    # S13/WP5-review: Befund 3 coverage gap — a feature flag explicitly
    # False must still let sub-locations/lots be CREATED (only a log hint
    # differs), never skip creation. _make_ctx defaults feature_flags to
    # {}, so .get(key, True) always resolved True in every test above —
    # none of them actually exercised the False branch this design decision
    # is about.
    # ==================================================================

    try:
        client = _mock_client(storable_products=[{"id": 1}])
        ctx = _make_ctx(stock_sel={"avg_qty": 0, "sub_locations": 2},
                         feature_flags={"stock_multi_locations": False})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        loc_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.location']
        assert len(loc_batches) == 1 and len(loc_batches[0].args[1]) == 2, (
            "stock_multi_locations=False must not skip sub-location creation")
        results.append(("S13/Befund3: stock_multi_locations=False -> sub-locations still created (hint only)", True, ""))
    except AssertionError as e:
        results.append(("S13/Befund3: stock_multi_locations=False -> sub-locations still created (hint only)", False, str(e)))

    try:
        client = _mock_client(storable_products=[{"id": 1, "tracking": "lot"}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20}, product_ids=[1], new_product_ids=[1],
                         feature_flags={"stock_lots": False})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        lot_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.lot']
        assert len(lot_batches) == 1 and len(lot_batches[0].args[1]) == 1, (
            "stock_lots=False must not skip lot creation")
        results.append(("S13/Befund3: stock_lots=False -> lot still created (hint only)", True, ""))
    except AssertionError as e:
        results.append(("S13/Befund3: stock_lots=False -> lot still created (hint only)", False, str(e)))

    # ==================================================================
    # S13/WP5-review: a blocked stock.lot create must degrade the affected
    # products to plain untracked bulk quants, not crash the whole module
    # (the already-queued "none"-tracking quants must still go through).
    # ==================================================================
    try:
        client = _mock_client(storable_products=[{"id": 1, "tracking": "lot"}, {"id": 2, "tracking": "none"}])
        base_batch = client.create_batch.side_effect

        def _create_batch_lot_blocked(model, values_list, context=None):
            if model == 'stock.lot':
                raise Exception("no create rights on stock.lot")
            return base_batch(model, values_list, context=context)

        client.create_batch.side_effect = _create_batch_lot_blocked
        ctx = _make_ctx(stock_sel={"avg_qty": 20}, product_ids=[1, 2], new_product_ids=[1, 2])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)  # must not raise
        lot_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.lot']
        assert len(lot_batches) == 1, "expected exactly one (failed) stock.lot attempt"
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        assert len(quant_batches) == 1, quant_batches
        quant_vals = quant_batches[0].args[1]
        assert len(quant_vals) == 2, quant_vals
        assert all("lot_id" not in v for v in quant_vals), (
            "blocked lot create must degrade every quant to plain bulk, none carrying a stale lot_id")
        results.append(("S13/WP5-review: blocked stock.lot create degrades to bulk quants, no crash", True, ""))
    except Exception as e:
        results.append(("S13/WP5-review: blocked stock.lot create degrades to bulk quants, no crash", False, str(e)))

    # ==================================================================
    # S14/WP2 — R12 Nachbestellregeln (stock.warehouse.orderpoint)
    # ==================================================================

    # Pattern 3: orderpoints_pct=0 (default/absent) -> no orderpoint batch.
    try:
        client = _mock_client(storable_products=[{"id": 1}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20})
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        op_batches = [c for c in client.create_batch.call_args_list
                      if c.args[0] == 'stock.warehouse.orderpoint']
        assert op_batches == [], op_batches
        results.append(("S14/R12: orderpoints_pct=0 -> no orderpoint calls (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("S14/R12: orderpoints_pct=0 -> no orderpoint calls (Pattern 3)", False, str(e)))

    # Befund 6/Pattern 5: an orderpoint-only run (avg_qty=0, empty
    # company_ids) must still create orderpoints — company_ids only gates
    # the quant/tracking branch, never orderpoints (company_id comes from
    # get_main_company_id, not ctx.company_ids).
    try:
        client = _mock_client(storable_products=[{"id": 1}])
        ctx = _make_ctx(stock_sel={"avg_qty": 0, "orderpoints_pct": 100},
                         company_ids=[], product_ids=[1], component_ids=[],
                         new_product_ids=[1])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        op_batches = [c for c in client.create_batch.call_args_list
                      if c.args[0] == 'stock.warehouse.orderpoint']
        assert len(op_batches) == 1 and len(op_batches[0].args[1]) == 1, op_batches
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        assert quant_batches == [], "avg_qty=0 must still skip quant seeding"
        results.append(("S14/Befund6: orderpoint-only run (avg_qty=0, empty company_ids) still creates orderpoint", True, ""))
    except AssertionError as e:
        results.append(("S14/Befund6: orderpoint-only run (avg_qty=0, empty company_ids) still creates orderpoint", False, str(e)))

    # Happy path + field shape: orderpoints_pct=100 -> every eligible
    # product gets exactly one orderpoint, with min/max qty from config,
    # no warehouse_id (Odoo derives it), no name (auto-fill), one batch
    # call (Pattern 8).
    try:
        client = _mock_client(storable_products=[{"id": 1}, {"id": 2}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "orderpoints_pct": 100,
                                    "orderpoint_min_qty": 8, "orderpoint_max_qty": 30},
                         product_ids=[1, 2], component_ids=[], new_product_ids=[1, 2])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        op_batches = [c for c in client.create_batch.call_args_list
                      if c.args[0] == 'stock.warehouse.orderpoint']
        assert len(op_batches) == 1, op_batches  # Pattern 8: one batch call
        vals_list = op_batches[0].args[1]
        assert len(vals_list) == 2, vals_list
        for v in vals_list:
            assert v["product_min_qty"] == 8 and v["product_max_qty"] == 30, v
            assert "warehouse_id" not in v, v
            assert "name" not in v, v
            assert v["trigger"] == "manual" and "company_id" in v and "location_id" in v, v
        results.append(("S14/R12: orderpoints_pct=100 -> one orderpoint per eligible product, correct fields (Pattern 8)", True, ""))
    except AssertionError as e:
        results.append(("S14/R12: orderpoints_pct=100 -> one orderpoint per eligible product, correct fields (Pattern 8)", False, str(e)))

    # Eligibility: only new_product_ids | component_ids are ever candidates
    # for an orderpoint — a pre-existing (use_existing) product must never
    # get one, even at orderpoints_pct=100 (collision-safety against the
    # live-confirmed (product, warehouse, location) uniqueness constraint).
    try:
        client = _mock_client(storable_products=[{"id": 1}, {"id": 2}, {"id": 3}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "orderpoints_pct": 100},
                         product_ids=[1, 2], component_ids=[3], new_product_ids=[1])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        op_batches = [c for c in client.create_batch.call_args_list
                      if c.args[0] == 'stock.warehouse.orderpoint']
        vals_list = op_batches[0].args[1]
        op_pids = {v["product_id"] for v in vals_list}
        assert op_pids == {1, 3}, op_pids  # 2 is pre-existing (use_existing), excluded
        results.append(("S14/R12: orderpoint eligibility restricted to new_product_ids|component_ids", True, f"{op_pids}"))
    except AssertionError as e:
        results.append(("S14/R12: orderpoint eligibility restricted to new_product_ids|component_ids", False, str(e)))

    # Independence (Befund 5): quant/lot tail and orderpoint batch are two
    # equally-ranked, unrelated blocks — a run with both avg_qty>0 and
    # orderpoints_pct>0 must produce both a stock.quant and a
    # stock.warehouse.orderpoint batch call.
    try:
        client = _mock_client(storable_products=[{"id": 1}])
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "orderpoints_pct": 100},
                         product_ids=[1], component_ids=[], new_product_ids=[1])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        op_batches = [c for c in client.create_batch.call_args_list
                      if c.args[0] == 'stock.warehouse.orderpoint']
        assert len(quant_batches) == 1 and len(op_batches) == 1, (quant_batches, op_batches)
        results.append(("S14/Befund5: quant seeding and orderpoints run independently in the same call", True, ""))
    except AssertionError as e:
        results.append(("S14/Befund5: quant seeding and orderpoints run independently in the same call", False, str(e)))

    # A failed stock.warehouse.orderpoint create_batch must not affect the
    # quants already created in the block above — non-fatal, no raise.
    try:
        client = _mock_client(storable_products=[{"id": 1}])
        base_batch = client.create_batch.side_effect

        def _create_batch_op_blocked(model, values_list, context=None):
            if model == 'stock.warehouse.orderpoint':
                raise Exception("no create rights on stock.warehouse.orderpoint")
            return base_batch(model, values_list, context=context)

        client.create_batch.side_effect = _create_batch_op_blocked
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "orderpoints_pct": 100},
                         product_ids=[1], component_ids=[], new_product_ids=[1])
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)  # must not raise
        quant_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'stock.quant']
        assert len(quant_batches) == 1 and len(quant_batches[0].args[1]) == 1, quant_batches
        results.append(("S14/Befund3: blocked orderpoint create is non-fatal, quants unaffected", True, ""))
    except Exception as e:
        results.append(("S14/Befund3: blocked orderpoint create is non-fatal, quants unaffected", False, str(e)))

    # Pattern 7: orderpoints_pct distribution — at a mid-range pct across
    # many eligible products, both created and skipped outcomes occur.
    try:
        random_module = __import__("random")
        random_module.seed(42)
        client = _mock_client(storable_products=[{"id": i} for i in range(1, 101)])
        ctx = _make_ctx(stock_sel={"avg_qty": 20, "orderpoints_pct": 50},
                        product_ids=list(range(1, 101)), component_ids=[],
                        new_product_ids=list(range(1, 101)))
        inventory.create_inventory_data(client, gemini=None, ctx=ctx)
        op_batches = [c for c in client.create_batch.call_args_list
                      if c.args[0] == 'stock.warehouse.orderpoint']
        n = len(op_batches[0].args[1]) if op_batches else 0
        assert 0 < n < 100, n
        results.append(("S14/Pattern7: orderpoints_pct=50 across 100 products -> partial coverage", True, f"n={n}"))
    except AssertionError as e:
        results.append(("S14/Pattern7: orderpoints_pct=50 across 100 products -> partial coverage", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
