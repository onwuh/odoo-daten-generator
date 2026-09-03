"""Unit tests for modules/sale.py — B8 (confirm count scales with order count)."""
import os
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import sale


def _make_ctx(num_orders, analytic=None):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    sel_kwargs = {"sale": num_orders}
    if analytic is not None:
        sel_kwargs["analytic"] = analytic
    ctx = RunContext(
        criteria=criteria, module_selections=ModuleSelections(**sel_kwargs), industry="IT",
        language_name="German", language_code="de", gemini_model_name="test",
    )
    ctx.company_ids = [1, 2, 3]
    ctx.product_ids = [10, 11, 12]
    return ctx


def _mock_client():
    client = MagicMock()
    counter = {"n": 7000}

    def _create(model, vals, context=None):
        counter["n"] += 1
        return counter["n"]

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'product.product':
            return [{"id": pid} for pid in (10, 11, 12)]
        if model == 'sale.order':
            # confirm_sale_orders' read-back verification step
            return []
        return []

    client.create.side_effect = _create
    client.search_read.side_effect = _search_read
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # B8: confirmation count scales with order count, not fixed at 5.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=200)
        sale.create_sale_data(client, gemini=None, ctx=ctx)
        confirm_calls = [
            c for c in client.call_method.call_args_list if c.args[1] == 'action_confirm'
        ]
        assert confirm_calls, "action_confirm never called"
        confirmed_ids = confirm_calls[0].kwargs.get("ids", [])
        assert len(confirmed_ids) != 5, f"B8 regressed: still hardcoded to 5 (got {len(confirmed_ids)})"
        expected = max(1, round(200 * ctx.module_selections.sale_confirm_pct / 100))
        assert len(confirmed_ids) == expected, f"expected {expected} confirmed orders, got {len(confirmed_ids)}"
        results.append((
            "create_sale_data: 200 orders -> confirm count scales (not fixed 5)",
            True, f"confirmed={len(confirmed_ids)}/200",
        ))
    except AssertionError as e:
        results.append(("create_sale_data: 200 orders -> confirm count scales (not fixed 5)", False, str(e)))

    # ------------------------------------------------------------------
    # B8: sale_confirm_pct is honored when explicitly set to a non-default
    # value — 50% of 10 orders must confirm exactly 5, computed independently
    # of the field the code itself reads (guards against a self-referential
    # test that would pass even if the field were ignored).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=10)
        ctx.module_selections.sale_confirm_pct = 50
        sale.create_sale_data(client, gemini=None, ctx=ctx)
        confirm_calls = [
            c for c in client.call_method.call_args_list if c.args[1] == 'action_confirm'
        ]
        confirmed_ids = confirm_calls[0].kwargs.get("ids", []) if confirm_calls else []
        assert len(confirmed_ids) == 5, f"expected 5 confirmed orders (50% of 10), got {len(confirmed_ids)}"
        results.append(("create_sale_data: sale_confirm_pct=50 -> confirms exactly 5 of 10", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: sale_confirm_pct=50 -> confirms exactly 5 of 10", False, str(e)))

    # ------------------------------------------------------------------
    # B8: small order counts still confirm at least 1, never 0.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=1)
        sale.create_sale_data(client, gemini=None, ctx=ctx)
        confirm_calls = [
            c for c in client.call_method.call_args_list if c.args[1] == 'action_confirm'
        ]
        confirmed_ids = confirm_calls[0].kwargs.get("ids", []) if confirm_calls else []
        assert len(confirmed_ids) == 1, f"expected 1 confirmed order, got {len(confirmed_ids)}"
        results.append(("create_sale_data: 1 order -> confirms exactly 1 (never 0)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: 1 order -> confirms exactly 1 (never 0)", False, str(e)))

    # ------------------------------------------------------------------
    # B14: orders link to an opportunity of the SAME partner, not by position.
    # Setup: order for partner 2 created first (positionally at index 0), but
    # the only opportunity belongs to partner 1 -> old zip() would have wrongly
    # linked them; correct behavior links partner-1's opportunity to nothing
    # (no partner-1 order exists) and leaves the partner-2 order unlinked.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=1)
        ctx.company_ids = [2]  # only company 2 gets an order this run
        ctx.opportunity_ids = [500]

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'product.product':
                return [{"id": pid} for pid in (10, 11, 12)]
            if model == 'crm.lead':
                return [{"id": 500, "partner_id": [1, "Partner 1"]}]  # belongs to partner 1, not 2
            return []
        client.search_read.side_effect = _search_read

        sale.create_sale_data(client, gemini=None, ctx=ctx)
        write_calls = [c for c in client.write.call_args_list if c.args[0] == 'sale.order']
        assert not write_calls, f"B14 regressed: linked mismatched-partner order/opportunity: {write_calls}"
        # R11: an opportunity that was NOT linked must not show up in
        # ctx.linked_opportunity_ids — mark_lost_opportunities (crm.py, runs
        # after sale.py) treats anything missing from this list as eligible
        # for "lost", so a false entry here would make it permanently
        # unmarkable-lost.
        assert ctx.linked_opportunity_ids == [], ctx.linked_opportunity_ids
        results.append(("create_sale_data: no cross-partner order/opportunity link (B14)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: no cross-partner order/opportunity link (B14)", False, str(e)))

    # ------------------------------------------------------------------
    # B14: correct match IS made when the partner does line up.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_orders=1)
        ctx.company_ids = [1]
        ctx.opportunity_ids = [500]

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'product.product':
                return [{"id": pid} for pid in (10, 11, 12)]
            if model == 'crm.lead':
                return [{"id": 500, "partner_id": [1, "Partner 1"]}]
            return []
        client.search_read.side_effect = _search_read

        sale.create_sale_data(client, gemini=None, ctx=ctx)
        write_calls = [c for c in client.write.call_args_list if c.args[0] == 'sale.order']
        assert len(write_calls) == 1, f"expected 1 link write, got {len(write_calls)}"
        assert write_calls[0].args[2] == {"opportunity_id": 500}, write_calls[0].args
        # R11's ctx.linked_opportunity_ids is the ONLY thing that keeps
        # mark_lost_opportunities from marking a Won-staged opportunity lost
        # — this is the real production code path, not a hand-set list.
        assert ctx.linked_opportunity_ids == [500], ctx.linked_opportunity_ids
        results.append(("create_sale_data: same-partner order/opportunity gets linked (B14)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: same-partner order/opportunity gets linked (B14)", False, str(e)))

    # ==================================================================
    # S15/R20 — analytic distribution wiring (post-confirm read-then-write)
    # ==================================================================

    def _mock_client_confirmed(eligible_line_ids=None):
        """Order creation + confirm succeed for every order; sale.order
        search_read (confirm_sale_orders' own verification step) echoes
        every created order back as confirmed regardless of domain, so
        ctx.confirmed_order_ids is populated for the analytic step below to
        read. sale.order.line search_read returns eligible_line_ids (or a
        default set) as the "still empty" eligible pool."""
        client = MagicMock()
        counter = {"n": 7000}
        created_order_ids = []

        def _create(model, vals, context=None):
            counter["n"] += 1
            oid = counter["n"]
            if model == 'sale.order':
                created_order_ids.append(oid)
            return oid

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'product.product':
                return [{"id": pid} for pid in (10, 11, 12)]
            if model == 'sale.order':
                return [{"id": oid, "name": f"S{oid}"} for oid in created_order_ids]
            if model == 'sale.order.line':
                ids = eligible_line_ids if eligible_line_ids is not None else [9001, 9002, 9003, 9004]
                return [{"id": lid} for lid in ids]
            return []

        client.create.side_effect = _create
        client.search_read.side_effect = _search_read
        return client

    try:
        # Pattern 3: analytic disabled (default) -> no eligibility search,
        # no write, helper never called.
        client = _mock_client_confirmed()
        ctx = _make_ctx(num_orders=5)
        with patch("modules.sale.odoo_actions.get_or_create_analytic_accounts") as mock_helper:
            sale.create_sale_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_not_called()
        sol_reads = [c for c in client.search_read.call_args_list if c.args[0] == 'sale.order.line']
        assert sol_reads == [], sol_reads
        write_calls = [c for c in client.write.call_args_list if c.args[0] == 'sale.order.line']
        assert write_calls == [], write_calls
        results.append(("create_sale_data: analytic disabled -> no eligibility read, no write (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: analytic disabled -> no eligibility read, no write (Pattern 3)", False, str(e)))

    try:
        # sale_pct=0 with analytic enabled -> its own sub-off-switch.
        client = _mock_client_confirmed()
        ctx = _make_ctx(num_orders=5, analytic={"enabled": True, "sale_pct": 0, "purchase_pct": 50, "expense_pct": 50})
        with patch("modules.sale.odoo_actions.get_or_create_analytic_accounts") as mock_helper:
            sale.create_sale_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_not_called()
        results.append(("create_sale_data: sale_pct=0 -> no helper call (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: sale_pct=0 -> no helper call (Pattern 3)", False, str(e)))

    try:
        # No cost centers available (helper returns []) -> no eligibility
        # read, no write attempted.
        client = _mock_client_confirmed()
        ctx = _make_ctx(num_orders=5, analytic={"enabled": True, "sale_pct": 100, "purchase_pct": 0, "expense_pct": 0})
        with patch("modules.sale.odoo_actions.get_or_create_analytic_accounts", return_value=[]):
            sale.create_sale_data(client, gemini=None, ctx=ctx)
        sol_reads = [c for c in client.search_read.call_args_list if c.args[0] == 'sale.order.line']
        assert sol_reads == [], sol_reads
        results.append(("create_sale_data: no cost centers -> no eligibility read (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: no cost centers -> no eligibility read (Pattern 5)", False, str(e)))

    try:
        # No eligible lines (all already carry a value) -> no write attempted.
        client = _mock_client_confirmed(eligible_line_ids=[])
        ctx = _make_ctx(num_orders=5, analytic={"enabled": True, "sale_pct": 100, "purchase_pct": 0, "expense_pct": 0})
        with patch("modules.sale.odoo_actions.get_or_create_analytic_accounts", return_value=[901]):
            sale.create_sale_data(client, gemini=None, ctx=ctx)
        write_calls = [c for c in client.write.call_args_list if c.args[0] == 'sale.order.line']
        assert write_calls == [], write_calls
        results.append(("create_sale_data: no eligible lines -> no write (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_sale_data: no eligible lines -> no write (Pattern 5)", False, str(e)))

    try:
        # Happy path: sale_pct=100 -> every eligible line picked, grouped by
        # cost center, one write() call per distinct group (Pattern 8), the
        # eligibility domain filters on analytic_distribution=False, and the
        # written value is the live-confirmed {"<id>": 100.0} shape.
        client = _mock_client_confirmed(eligible_line_ids=[9001, 9002, 9003, 9004])
        ctx = _make_ctx(num_orders=5, analytic={"enabled": True, "sale_pct": 100, "purchase_pct": 0, "expense_pct": 0})
        with patch("modules.sale.odoo_actions.get_or_create_analytic_accounts",
                  return_value=[901, 902]) as mock_helper:
            sale.create_sale_data(client, gemini=None, ctx=ctx)
            mock_helper.assert_called_once()
        sol_reads = [c for c in client.search_read.call_args_list if c.args[0] == 'sale.order.line']
        assert len(sol_reads) == 1, sol_reads
        domain = sol_reads[0].args[1]
        assert ["analytic_distribution", "=", False] in domain, domain
        write_calls = [c for c in client.write.call_args_list if c.args[0] == 'sale.order.line']
        assert 1 <= len(write_calls) <= 2, write_calls  # grouped by cost center, few calls
        all_written_ids = []
        for call in write_calls:
            ids, vals = call.args[1], call.args[2]
            keys = list(vals["analytic_distribution"].keys())
            assert len(keys) == 1 and int(keys[0]) in (901, 902), vals
            assert vals["analytic_distribution"][keys[0]] == 100.0, vals
            all_written_ids.extend(ids)
        assert sorted(all_written_ids) == [9001, 9002, 9003, 9004], all_written_ids
        results.append((
            "create_sale_data: sale_pct=100 -> every eligible line written, grouped by cost center (Pattern 8)",
            True, f"{len(write_calls)} write calls",
        ))
    except AssertionError as e:
        results.append((
            "create_sale_data: sale_pct=100 -> every eligible line written, grouped by cost center (Pattern 8)",
            False, str(e),
        ))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
