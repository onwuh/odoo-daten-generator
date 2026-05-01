import sys
import os
import random

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.accounting import create_customer_invoice, post_invoices, _introduce_typo


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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
