from config import DemoCriteria, ModuleSelections, RunContext
from modules import purchase


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
    Consumes: ctx.partner_ids, ctx.product_ids (as a stand-in component pool —
    the outer TestContext has no dedicated component_ids field, same
    substitution test_documents.py uses for its own accounting.py setup step)
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    if not ctx.partner_ids or not ctx.product_ids:
        results.append(("purchase: SKIP — missing partner_ids or product_ids in ctx", False, "master_data must run first"))
        return False, results

    partner_id = ctx.partner_ids[0]
    product_id = ctx.product_ids[0]

    # Step 1 — end-to-end: 3 POs, 100% confirmed, bills created + posted.
    try:
        rctx = _make_rctx()
        rctx.company_ids = [partner_id]  # prerequisite proxy only — the module
        rctx.component_ids = [product_id]  # resolves the real res.company id itself
        rctx.module_selections.purchase = 3
        rctx.module_selections.purchase_confirm_pct = 100

        purchase.create_purchase_data(client, None, rctx)

        assert rctx.supplier_ids, "expected suppliers to have been created"
        pos = client.search_read(
            'purchase.order', [["partner_id", "in", rctx.supplier_ids]],
            fields=["state"], limit=0,
        )
        confirmed_pos = [p for p in pos if p["state"] == "purchase"]
        assert len(confirmed_pos) >= 3, f"expected >=3 confirmed POs, got {len(confirmed_pos)} of {len(pos)}"

        assert rctx.bill_ids, "expected at least 1 vendor bill in rctx.bill_ids"
        bills = client.search_read(
            'account.move', [["id", "in", rctx.bill_ids]],
            fields=["move_type", "state", "invoice_origin"], limit=0,
        )
        assert len(bills) == len(rctx.bill_ids), bills
        assert all(b["move_type"] == "in_invoice" and b["state"] == "posted" for b in bills), bills
        assert all(b["invoice_origin"] for b in bills), (
            f"bill missing invoice_origin (PO name) — not linked back to a PO: {bills}"
        )

        results.append((
            "purchase: end-to-end — POs confirmed, bills posted, read-back (Pattern 4)",
            True, f"{len(confirmed_pos)} confirmed POs, {len(bills)} bills",
        ))
    except Exception as e:
        results.append(("purchase: end-to-end — POs confirmed, bills posted, read-back (Pattern 4)", False, str(e)))

    # Step 2 — supplier pool sharing: a second call with the same rctx reuses
    # ctx.supplier_ids instead of creating a disjoint second set.
    try:
        assert rctx.supplier_ids, "step 1 must have populated rctx.supplier_ids"
        prior_suppliers = set(rctx.supplier_ids)
        rctx.module_selections.purchase = 1
        purchase.create_purchase_data(client, None, rctx)
        assert set(rctx.supplier_ids) == prior_suppliers, (
            f"supplier pool grew on reuse: {prior_suppliers} -> {set(rctx.supplier_ids)}"
        )
        results.append(("purchase: reuses ctx.supplier_ids across calls, no disjoint second set", True, ""))
    except Exception as e:
        results.append(("purchase: reuses ctx.supplier_ids across calls, no disjoint second set", False, str(e)))

    # Step 3 — Pattern 5: missing prerequisites (empty component_ids) -> graceful skip.
    try:
        skip_rctx = _make_rctx()
        skip_rctx.company_ids = [partner_id]
        skip_rctx.component_ids = []
        skip_rctx.module_selections.purchase = 5
        purchase.create_purchase_data(client, None, skip_rctx)
        assert skip_rctx.bill_ids == [], "should not have created bills with empty component_ids"
        results.append(("purchase: empty component_ids -> graceful skip (Pattern 5)", True, ""))
    except Exception as e:
        results.append(("purchase: empty component_ids -> graceful skip (Pattern 5)", False, str(e)))

    # Step 4 — S15/R20: analytic distribution live end-to-end on PO lines.
    # purchase_pct=100 -> every created PO line carries analytic_distribution
    # referencing one of the (newly created) cost-center accounts.
    try:
        rctx = _make_rctx()
        rctx.company_ids = [partner_id]
        rctx.component_ids = [product_id]
        rctx.module_selections.purchase = 2
        rctx.module_selections.purchase_confirm_pct = 0  # scoped to line creation, not confirm/bill
        rctx.module_selections.analytic = {
            "enabled": True, "sale_pct": 0, "purchase_pct": 100, "expense_pct": 0,
        }

        purchase.create_purchase_data(client, None, rctx)

        assert rctx.analytic_account_ids, "get_or_create_analytic_accounts left ctx.analytic_account_ids empty"
        pos = client.search_read(
            'purchase.order', [["partner_id", "in", rctx.supplier_ids]], fields=["order_line"], limit=0,
        )
        line_ids = [lid for po in pos for lid in po.get("order_line", [])]
        assert line_ids, "no PO lines found"
        lines = client.search_read(
            'purchase.order.line', [["id", "in", line_ids], ["analytic_distribution", "!=", False]],
            fields=["analytic_distribution"], limit=0,
        )
        assert len(lines) >= 2, f"expected at least 2 PO lines with a distribution, got {len(lines)}"
        for line in lines:
            keys = list(line["analytic_distribution"].keys())
            assert len(keys) == 1 and int(keys[0]) in rctx.analytic_account_ids, line

        results.append((
            "purchase: R20 analytic distribution live end-to-end — purchase_pct=100, read-back (Pattern 4)",
            True, f"{len(lines)} lines, accounts={rctx.analytic_account_ids}",
        ))
    except Exception as e:
        results.append((
            "purchase: R20 analytic distribution live end-to-end — purchase_pct=100, read-back (Pattern 4)",
            False, str(e),
        ))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
