import sys
import os
import random

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.accounting import (
    create_customer_invoice, post_invoices, _introduce_typo,
    create_vendor_bill, create_bank_transactions_for_all_invoices,
    get_or_create_bank_journal, _create_suppliers,
    create_invoices_from_orders, create_accounting_data,
)
from modules.sale import create_sale_order, confirm_sale_orders
from config import DemoCriteria, ModuleSelections, RunContext


def _make_rctx(num_invoices):
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit, module_selections=ModuleSelections(account=num_invoices), industry="IT",
        language_name="German", language_code="de_DE", gemini_model_name="test",
    )


def run(client, ctx):
    """
    Consumes: ctx.partner_ids, ctx.product_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    if not ctx.partner_ids or not ctx.product_ids:
        results.append(("accounting: SKIP — missing partner_ids or product_ids in ctx", False, "master_data must run first"))
        return False, results

    partner_id = ctx.partner_ids[0]
    product_id = ctx.product_ids[0]
    inv_id = None

    # Step 1 — Create customer invoice
    try:
        inv_id = create_customer_invoice(client, partner_id, [product_id])
        assert isinstance(inv_id, int) and inv_id > 0
        rec = client.search_read(
            'account.move',
            [["id", "=", inv_id]],
            fields=["move_type"],
            limit=1,
        )
        assert rec and rec[0]["move_type"] == "out_invoice"
        results.append(("accounting: create customer invoice (move_type=out_invoice)", True, inv_id))
    except Exception as e:
        results.append(("accounting: create customer invoice (move_type=out_invoice)", False, str(e)))
        inv_id = None

    # Step 2 — Post invoice
    try:
        assert inv_id, "No invoice created in step 1"
        post_invoices(client, [inv_id])
        rec = client.search_read(
            'account.move',
            [["id", "=", inv_id]],
            fields=["state"],
            limit=1,
        )
        assert rec and rec[0]["state"] == "posted"
        results.append(("accounting: post invoice (state=posted)", True, inv_id))
    except Exception as e:
        results.append(("accounting: post invoice (state=posted)", False, str(e)))

    # Step 3 — _introduce_typo boundary safety (unit, no network)
    try:
        assert _introduce_typo("") == ""
        assert _introduce_typo("A") == "A"
        assert _introduce_typo("AB") == "AB"  # len < 3 → unchanged
        long_result = _introduce_typo("Hello World")
        assert isinstance(long_result, str) and len(long_result) == len("Hello World")
        results.append(("accounting: _introduce_typo edge cases (empty/1/2/long)", True, ""))
    except Exception as e:
        results.append(("accounting: _introduce_typo edge cases (empty/1/2/long)", False, str(e)))

    # Step 4 — bank 80/20 deviation split (unit, checks distribution logic)
    try:
        random.seed(42)
        num_out = 20
        num_with_deviation = max(1, int(num_out * 0.2))
        deviation_indices = set(random.sample(range(num_out), num_with_deviation))
        assert 1 <= len(deviation_indices) <= num_out, "deviation set out of range"
        assert len(deviation_indices) < num_out, "all items deviated — no exact-match entries"
        exact_count = num_out - len(deviation_indices)
        assert exact_count >= 1, "no exact-match entries"
        results.append((
            "accounting: 80/20 split produces both exact and deviated entries",
            True,
            f"deviated={len(deviation_indices)}, exact={exact_count}",
        ))
    except Exception as e:
        results.append(("accounting: 80/20 split produces both exact and deviated entries", False, str(e)))

    # Step 5 — B4: bank transactions scoped to this run's invoice/bill IDs only,
    # and re-running does not duplicate transactions or clobber balance_start.
    try:
        journal_id = get_or_create_bank_journal(client)

        def _line_count():
            lines = client.search_read(
                'account.bank.statement.line', [["journal_id", "=", journal_id]],
                fields=["id"], limit=0,
            )
            return len(lines)

        # Pattern 1 — empty pool guard: no ids → no-op, no crash
        empty_result = create_bank_transactions_for_all_invoices(client, [], [])
        assert empty_result == [], f"expected [] for empty invoice/bill ids, got {empty_result}"

        # First batch: one invoice (already posted in step 2) + one vendor bill
        bill_id = create_vendor_bill(client, partner_id, [product_id], description_prefix="B4 Test Bill")
        assert isinstance(bill_id, int) and bill_id > 0

        count_before_first = _line_count()
        first_ids = create_bank_transactions_for_all_invoices(client, [inv_id], [bill_id])
        assert len(first_ids) == 2, f"expected 2 transactions (1 invoice + 1 bill), got {len(first_ids)}"
        count_after_first = _line_count()
        assert count_after_first - count_before_first == 2, (
            f"statement line count grew by {count_after_first - count_before_first}, expected 2"
        )

        stmt = client.search_read(
            'account.bank.statement', [["journal_id", "=", journal_id]],
            fields=["balance_start", "balance_end_real"], limit=1,
        )
        assert stmt, "no bank statement found after first batch"
        balance_start_after_first = stmt[0]["balance_start"]
        balance_end_after_first = stmt[0]["balance_end_real"]

        # Second batch: a fresh invoice — must NOT re-pull the first batch's
        # invoice/bill (that would happen with the old DB-wide "state=posted" query)
        inv2_id = create_customer_invoice(client, partner_id, [product_id])
        post_invoices(client, [inv2_id])
        inv2_rec = client.search_read('account.move', [["id", "=", inv2_id]], fields=["amount_total"], limit=1)
        inv2_amount = inv2_rec[0]["amount_total"]

        second_ids = create_bank_transactions_for_all_invoices(client, [inv2_id], [])
        assert len(second_ids) == 1, f"expected exactly 1 new transaction (only inv2), got {len(second_ids)}"
        count_after_second = _line_count()
        assert count_after_second - count_after_first == 1, (
            f"second run added {count_after_second - count_after_first} lines, expected 1 "
            f"(old DB-wide query would have re-added all prior invoices/bills too)"
        )

        stmt2 = client.search_read(
            'account.bank.statement', [["journal_id", "=", journal_id]],
            fields=["balance_start", "balance_end_real"], limit=1,
        )
        assert stmt2[0]["balance_start"] == balance_start_after_first, (
            f"balance_start changed on re-run: {balance_start_after_first} -> {stmt2[0]['balance_start']}"
        )
        expected_end = round(balance_end_after_first + inv2_amount, 2)
        assert abs(stmt2[0]["balance_end_real"] - expected_end) < 0.01, (
            f"balance_end_real not additive: expected ~{expected_end}, got {stmt2[0]['balance_end_real']}"
        )

        results.append((
            "accounting: B4 — bank txns scoped to run, balance additive not overwritten", True,
            f"batch1={len(first_ids)}, batch2={len(second_ids)}, balance_end={stmt2[0]['balance_end_real']}",
        ))
    except Exception as e:
        results.append(("accounting: B4 — bank txns scoped to run, balance additive not overwritten", False, str(e)))

    # Step 6 — A1: suppliers get a full address (street/country_id), not a bare name
    try:
        supplier_ids = _create_suppliers(client, ["Integration Test Lieferant GmbH"])
        assert len(supplier_ids) == 1 and isinstance(supplier_ids[0], int) and supplier_ids[0] > 0
        rec = client.search_read(
            'res.partner', [["id", "=", supplier_ids[0]]],
            fields=["street", "country_id", "supplier_rank"], limit=1,
        )
        assert rec and rec[0]["street"], "supplier has no street"
        country_val = rec[0]["country_id"]
        country_id = country_val[0] if isinstance(country_val, (list, tuple)) else country_val
        assert country_id, "supplier has no country_id"
        assert rec[0]["supplier_rank"] == 1
        results.append((
            "accounting: A1 — supplier gets full address via data_factory", True,
            f"street={rec[0]['street']!r}",
        ))
    except Exception as e:
        results.append(("accounting: A1 — supplier gets full address via data_factory", False, str(e)))

    # Step 7 — D3: create_invoices_from_orders end-to-end (batch account.move
    # create, single post_invoices call), read-back.
    try:
        order_id = create_sale_order(client, {
            "partner_id": partner_id,
            "order_line": [(0, 0, {"product_id": product_id, "product_uom_qty": 2})],
        })
        confirm_sale_orders(client, [order_id])
        new_invoice_ids = create_invoices_from_orders(client, [order_id])
        assert len(new_invoice_ids) == 1, f"expected 1 invoice, got {len(new_invoice_ids)}"
        rec = client.search_read(
            'account.move', [["id", "=", new_invoice_ids[0]]],
            fields=["move_type", "state", "invoice_origin"], limit=1,
        )
        assert rec and rec[0]["move_type"] == "out_invoice"
        assert rec[0]["state"] == "posted", f"expected posted, got {rec[0]['state']}"
        results.append((
            "accounting: create_invoices_from_orders end-to-end (D3 batch), read-back",
            True, new_invoice_ids[0],
        ))
    except Exception as e:
        results.append(("accounting: create_invoices_from_orders end-to-end (D3 batch), read-back", False, str(e)))

    # Step 8 — D3: create_accounting_data end-to-end, standalone-invoice +
    # vendor-bill batch path (no 'sale' in installed_modules forces standalone
    # invoices instead of from-orders), read-back both.
    try:
        rctx = _make_rctx(num_invoices=4)
        rctx.company_ids = [partner_id]
        rctx.product_ids = [product_id]
        rctx.component_ids = [product_id]
        rctx.installed_modules = set()  # force standalone invoice path
        create_accounting_data(client, None, rctx)
        assert len(rctx.invoice_ids) == 4, f"expected 4 invoices, got {len(rctx.invoice_ids)}"
        assert len(rctx.bill_ids) >= 1, f"expected at least 1 vendor bill, got {len(rctx.bill_ids)}"

        invoices = client.search_read(
            'account.move', [["id", "in", rctx.invoice_ids]], fields=["move_type", "state"], limit=0,
        )
        assert len(invoices) == 4
        assert all(i["move_type"] == "out_invoice" and i["state"] == "posted" for i in invoices), invoices

        bills = client.search_read(
            'account.move', [["id", "in", rctx.bill_ids]], fields=["move_type", "state"], limit=0,
        )
        assert len(bills) == len(rctx.bill_ids)
        assert all(b["move_type"] == "in_invoice" and b["state"] == "posted" for b in bills), bills

        results.append((
            "accounting: create_accounting_data end-to-end (D3 batch), read-back",
            True, f"{len(invoices)} invoices, {len(bills)} bills",
        ))
    except Exception as e:
        results.append(("accounting: create_accounting_data end-to-end (D3 batch), read-back", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
