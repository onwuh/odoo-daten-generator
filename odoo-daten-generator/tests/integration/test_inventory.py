from config import DemoCriteria, ModuleSelections, RunContext
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
        rctx.company_ids = [partner_id]
        rctx.product_ids = [product_id]
        rctx.module_selections.stock = {"avg_qty": 20}

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
        skip_rctx.company_ids = []
        skip_rctx.product_ids = [product_id]
        skip_rctx.module_selections.stock = {"avg_qty": 20}
        before = client.search_read('stock.quant', [["product_id", "=", product_id]], fields=["id"], limit=0)
        inventory.create_inventory_data(client, None, skip_rctx)
        after = client.search_read('stock.quant', [["product_id", "=", product_id]], fields=["id"], limit=0)
        assert len(after) == len(before), "empty company_ids should not have created a new quant"
        results.append(("inventory: empty company_ids -> graceful skip, no new quant (Pattern 5)", True, ""))
    except Exception as e:
        results.append(("inventory: empty company_ids -> graceful skip, no new quant (Pattern 5)", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
