from config import DemoCriteria, ModuleSelections, RunContext, StockConfig
from modules import inventory


def _make_rctx():
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit, module_selections=ModuleSelections(), industry="IT",
        language_name="German", language_code="de_DE", gemini_model_name="test",
    )


def run(client, ctx):
    """
    Consumes: ctx.partner_ids (prerequisite proxy only — the module resolves
    the real res.company id itself, not from ctx)
    Creates its own fresh storable product rather than reusing ctx.product_ids,
    so the stock.quant read-back below can't pick up quant history from an
    earlier run's re-use of the same product (Odoo reconciles repeated counts
    on the same product/location with adjustment quants — confirmed live
    during development — which would make a naive "quantity == requested"
    assertion flaky across repeated suite runs).
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    if not ctx.partner_ids:
        results.append(("inventory: SKIP — missing partner_ids in ctx", False, "master_data must run first"))
        return False, results

    partner_id = ctx.partner_ids[0]

    # Step 1 — end-to-end: fresh storable product, quant seeded + applied,
    # quantity > 0 post-apply (the real "stock exists" acceptance criterion —
    # asserting only inventory_quantity would pass even if the apply call
    # silently failed, since inventory_quantity round-trips on create alone).
    try:
        product_id = client.create('product.product', {
            "name": "Inventory Test Storable", "type": "consu",
            "is_storable": True, "sale_ok": False, "purchase_ok": True,
        })

        rctx = _make_rctx()
        rctx.partner_company_ids = [partner_id]
        rctx.product_ids = [product_id]
        rctx.module_selections.stock = StockConfig(avg_qty=20)

        inventory.create_inventory_data(client, None, rctx)

        # action_apply_inventory books a matching negative quant at Odoo's
        # virtual "Inventory adjustment" location as the double-entry
        # counterpart to the real stock quant (live-verified, standard Odoo
        # accounting) — filter to the positive, physical on-hand row rather
        # than assuming exactly one quant row total.
        quants = client.search_read(
            'stock.quant', [["product_id", "=", product_id], ["quantity", ">", 0]],
            fields=["quantity", "inventory_quantity"], limit=0,
        )
        assert len(quants) == 1, f"expected exactly 1 positive on-hand quant, got {len(quants)}"
        assert quants[0]["quantity"] > 0, (
            f"quant.quantity not > 0 after action_apply_inventory — apply may have "
            f"silently failed: {quants[0]}"
        )

        results.append((
            "inventory: end-to-end — quant seeded + applied, quantity > 0 (Pattern 4)",
            True, f"quantity={quants[0]['quantity']}",
        ))
    except Exception as e:
        results.append(("inventory: end-to-end — quant seeded + applied, quantity > 0 (Pattern 4)", False, str(e)))

    # Step 2 — Pattern 5: missing prerequisites (empty company_ids) -> graceful skip.
    try:
        skip_rctx = _make_rctx()
        skip_rctx.partner_company_ids = []
        skip_rctx.product_ids = [product_id]
        skip_rctx.module_selections.stock = StockConfig(avg_qty=20)
        before = client.search_read('stock.quant', [["product_id", "=", product_id]], fields=["id"], limit=0)
        inventory.create_inventory_data(client, None, skip_rctx)
        after = client.search_read('stock.quant', [["product_id", "=", product_id]], fields=["id"], limit=0)
        assert len(after) == len(before), "empty company_ids should not have created a new quant"
        results.append(("inventory: empty company_ids -> graceful skip, no new quant (Pattern 5)", True, ""))
    except Exception as e:
        results.append(("inventory: empty company_ids -> graceful skip, no new quant (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # Step 3 — S13/R15+R16: sub_locations live end-to-end. Read-back
    # complete_name/location_id/barcode (Pattern 4), and confirm the
    # round-robin actually distributes quants across the new locations,
    # not just the warehouse root.
    # ------------------------------------------------------------------
    try:
        rctx = _make_rctx()
        rctx.partner_company_ids = [partner_id]
        product_ids = []
        for i in range(4):
            product_ids.append(client.create('product.product', {
                "name": f"S13 Sub-Location Test {i}", "type": "consu",
                "is_storable": True, "sale_ok": False, "purchase_ok": True,
            }))
        rctx.product_ids = product_ids
        rctx.new_product_ids = []  # none tracked, only sub-locations under test here
        rctx.module_selections.stock = StockConfig(avg_qty=20, sub_locations=2)

        inventory.create_inventory_data(client, None, rctx)

        quants = client.search_read(
            'stock.quant', [["product_id", "in", product_ids], ["quantity", ">", 0]],
            fields=["location_id"], limit=0,
        )
        used_location_ids = set()
        for q in quants:
            loc = q["location_id"]
            used_location_ids.add(loc[0] if isinstance(loc, (list, tuple)) else loc)
        assert len(used_location_ids) >= 2, (
            f"expected quants spread over >=2 locations (warehouse root + sub-locations), "
            f"got {used_location_ids}")

        new_locations = client.search_read(
            'stock.location', [["id", "in", list(used_location_ids)], ["usage", "=", "internal"]],
            fields=["complete_name", "location_id", "barcode"], limit=0,
        )
        sub_locs = [l for l in new_locations if l.get("barcode")]
        assert sub_locs, f"expected at least one sub-location with a barcode among {new_locations}"
        for l in sub_locs:
            assert l.get("complete_name"), l
            assert l.get("location_id"), l

        results.append((
            "inventory: sub_locations live end-to-end — quants spread, "
            "read-back complete_name/location_id/barcode (Pattern 4)",
            True, f"locations used={used_location_ids}",
        ))
    except Exception as e:
        results.append((
            "inventory: sub_locations live end-to-end — quants spread, "
            "read-back complete_name/location_id/barcode (Pattern 4)",
            False, str(e),
        ))

    # ------------------------------------------------------------------
    # Step 4 — S13/R14: second_warehouse live end-to-end. Read-back
    # code/lot_stock_id (Pattern 4).
    # ------------------------------------------------------------------
    try:
        rctx = _make_rctx()
        rctx.partner_company_ids = [partner_id]
        product_id = client.create('product.product', {
            "name": "S13 Second Warehouse Test", "type": "consu",
            "is_storable": True, "sale_ok": False, "purchase_ok": True,
        })
        rctx.product_ids = [product_id]
        rctx.new_product_ids = []
        rctx.module_selections.stock = StockConfig(avg_qty=20, second_warehouse=True)

        # A name-`like` search alone can match "Lager 2 (NNNN)" residue left
        # over from an earlier run on this shared demo tenant and pass
        # without this run having created anything — exclude ids that
        # already existed before this call runs.
        pre_existing_ids = {
            w["id"] for w in client.search_read(
                'stock.warehouse', [["name", "like", "Lager 2"]], fields=["id"], limit=0,
            )
        }

        inventory.create_inventory_data(client, None, rctx)

        warehouses = client.search_read(
            'stock.warehouse', [["name", "like", "Lager 2"]],
            fields=["code", "lot_stock_id"], limit=0,
        )
        new_warehouses = [w for w in warehouses if w["id"] not in pre_existing_ids]
        assert new_warehouses, f"no NEW second warehouse found (pre-existing: {pre_existing_ids})"
        wh = new_warehouses[0]
        assert wh.get("code"), wh
        assert wh.get("lot_stock_id"), wh

        results.append((
            "inventory: second_warehouse live end-to-end — read-back code/lot_stock_id (Pattern 4)",
            True, f"warehouse={wh}",
        ))
    except Exception as e:
        results.append((
            "inventory: second_warehouse live end-to-end — read-back code/lot_stock_id (Pattern 4)",
            False, str(e),
        ))

    # ------------------------------------------------------------------
    # Step 5 — S13/R13: lot- and serial-tracking live end-to-end. Read-back
    # stock.lot.product_id/name (Pattern 4) and the m2o tuple shape on
    # stock.quant.lot_id (Pattern 6).
    # ------------------------------------------------------------------
    try:
        rctx = _make_rctx()
        rctx.partner_company_ids = [partner_id]
        lot_product_id = client.create('product.product', {
            "name": "S13 Lot Tracking Test", "type": "consu",
            "is_storable": True, "sale_ok": False, "purchase_ok": True,
            "tracking": "lot",
        })
        serial_product_id = client.create('product.product', {
            "name": "S13 Serial Tracking Test", "type": "consu",
            "is_storable": True, "sale_ok": False, "purchase_ok": True,
            "tracking": "serial",
        })
        rctx.product_ids = [lot_product_id, serial_product_id]
        rctx.new_product_ids = [lot_product_id, serial_product_id]  # Befund 4: only tracked if "new"
        rctx.module_selections.stock = StockConfig(avg_qty=20, tracking_lot_pct=100,
                                                   tracking_serial_pct=0, tracking_serial_max=3)

        inventory.create_inventory_data(client, None, rctx)

        lots = client.search_read(
            'stock.lot', [["product_id", "in", [lot_product_id, serial_product_id]]],
            fields=["product_id", "name"], limit=0,
        )
        assert lots, "no stock.lot records created"
        for lot in lots:
            assert lot.get("name", "").startswith("LOT-"), lot
            pid = lot["product_id"]
            pid = pid[0] if isinstance(pid, (list, tuple)) else pid  # Pattern 6
            assert pid in (lot_product_id, serial_product_id), lot

        lot_quants = client.search_read(
            'stock.quant', [["product_id", "=", lot_product_id], ["lot_id", "!=", False]],
            fields=["lot_id", "quantity"], limit=0,
        )
        assert lot_quants, "no lot-tracked quant found for the lot-tracking product"
        lid = lot_quants[0]["lot_id"]
        assert isinstance(lid, (list, tuple)) and len(lid) == 2, f"lot_id not an m2o tuple: {lid!r}"

        results.append((
            "inventory: lot/serial tracking live end-to-end — stock.lot read-back, "
            "lot_id m2o tuple (Pattern 4/6)",
            True, f"{len(lots)} lots created",
        ))
    except Exception as e:
        results.append((
            "inventory: lot/serial tracking live end-to-end — stock.lot read-back, "
            "lot_id m2o tuple (Pattern 4/6)",
            False, str(e),
        ))

    # ------------------------------------------------------------------
    # Step 6 — S14/R12: orderpoints (stock.warehouse.orderpoint) live
    # end-to-end, avg_qty=0 (orderpoint-only run, Befund 6) so no quant
    # noise is mixed in. Read-back product_min_qty/product_max_qty/
    # location_id/warehouse_id (Pattern 4) — warehouse_id must come back
    # populated even though the code never sets it (Odoo derives it from
    # location_id).
    # ------------------------------------------------------------------
    try:
        rctx = _make_rctx()
        rctx.partner_company_ids = [partner_id]
        op_product_id = client.create('product.product', {
            "name": "S14 Orderpoint Test", "type": "consu",
            "is_storable": True, "sale_ok": False, "purchase_ok": True,
        })
        rctx.product_ids = [op_product_id]
        rctx.new_product_ids = [op_product_id]
        rctx.module_selections.stock = StockConfig(avg_qty=0, orderpoints_pct=100,
                                                   orderpoint_min_qty=8, orderpoint_max_qty=30)

        inventory.create_inventory_data(client, None, rctx)

        quants = client.search_read(
            'stock.quant', [["product_id", "=", op_product_id]], fields=["id"], limit=0,
        )
        assert not quants, f"avg_qty=0 must not seed any quant, found {quants}"

        orderpoints = client.search_read(
            'stock.warehouse.orderpoint', [["product_id", "=", op_product_id]],
            fields=["product_min_qty", "product_max_qty", "location_id", "warehouse_id", "trigger"],
            limit=0,
        )
        assert len(orderpoints) == 1, f"expected exactly 1 orderpoint, got {orderpoints}"
        op = orderpoints[0]
        assert op["product_min_qty"] == 8 and op["product_max_qty"] == 30, op
        assert op.get("location_id"), op
        assert op.get("warehouse_id"), "warehouse_id not derived by Odoo from location_id"

        results.append((
            "inventory: R12 orderpoints live end-to-end — avg_qty=0, "
            "read-back min/max/location/warehouse (Pattern 4)",
            True, f"orderpoint={op}",
        ))
    except Exception as e:
        results.append((
            "inventory: R12 orderpoints live end-to-end — avg_qty=0, "
            "read-back min/max/location/warehouse (Pattern 4)",
            False, str(e),
        ))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
