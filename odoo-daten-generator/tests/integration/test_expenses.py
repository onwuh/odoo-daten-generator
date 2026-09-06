from config import AnalyticConfig, DemoCriteria, ExpenseConfig, ModuleSelections, RunContext
from modules import expenses


def _make_rctx():
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit, module_selections=ModuleSelections(), industry="IT",
        language_name="German", language_code="de_DE",
    )


def run(client, ctx):
    """
    Consumes: ctx.employee_ids (test_hr must run first).
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    if not ctx.employee_ids:
        results.append(("expenses: SKIP — missing employee_ids in ctx", False, "test_hr must run first"))
        return False, results

    employee_id = ctx.employee_ids[0]

    # Step 1 — end-to-end: create_expense_data creates records with product_id
    # set (R19's live-found requirement), a configurable share reaches
    # approval_state='approved' via plain write() (S12/WP3, no action method).
    try:
        rctx = _make_rctx()
        rctx.employee_ids = [employee_id]
        rctx.module_selections.hr_expense = ExpenseConfig(count_per_employee=2, approved_pct=100)

        expenses.create_expense_data(client, None, rctx)

        created = client.search_read(
            'hr.expense', [["employee_id", "=", employee_id], ["name", "like", "Geschäftsreise"]],
            fields=["product_id", "approval_state", "total_amount"], limit=0,
        )
        assert len(created) >= 2, f"expected at least 2 expenses, got {len(created)}"
        assert all(rec.get("product_id") for rec in created), \
            f"an expense was created without product_id: {created}"
        assert all(rec.get("approval_state") == "approved" for rec in created), \
            f"approved_pct=100 but not all expenses reached 'approved': {created}"
        results.append((
            "expenses: end-to-end — product_id set, approved_pct=100 reaches 'approved' (Pattern 4)",
            True, f"{len(created)} expenses",
        ))
    except Exception as e:
        results.append(("expenses: end-to-end — product_id set, approved_pct=100 reaches 'approved' (Pattern 4)", False, str(e)))

    # Step 2 — approved_pct=0 -> created, but none reach 'submitted'/'approved'.
    try:
        rctx = _make_rctx()
        rctx.employee_ids = [employee_id]
        rctx.module_selections.hr_expense = ExpenseConfig(count_per_employee=1, approved_pct=0)

        expenses.create_expense_data(client, None, rctx)

        created = client.search_read(
            'hr.expense', [["employee_id", "=", employee_id], ["name", "like", "Geschäftsreise"],
             ["approval_state", "=", False]],
            fields=["id"], limit=0,
        )
        assert len(created) >= 1, f"expected at least 1 non-approved expense, got {len(created)}"
        results.append(("expenses: approved_pct=0 -> no approval_state set", True, f"{len(created)} draft expenses"))
    except Exception as e:
        results.append(("expenses: approved_pct=0 -> no approval_state set", False, str(e)))

    # Step 3 — Pattern 5: missing prerequisites (empty employee_ids) -> graceful skip.
    try:
        skip_rctx = _make_rctx()
        skip_rctx.employee_ids = []
        skip_rctx.module_selections.hr_expense = ExpenseConfig(count_per_employee=2,
                                                               approved_pct=50)
        before = client.search_read('hr.expense', [["employee_id", "=", employee_id]], fields=["id"], limit=0)
        expenses.create_expense_data(client, None, skip_rctx)
        after = client.search_read('hr.expense', [["employee_id", "=", employee_id]], fields=["id"], limit=0)
        assert len(after) == len(before), "empty employee_ids should not have created a new expense"
        results.append(("expenses: empty employee_ids -> graceful skip, no new expense (Pattern 5)", True, ""))
    except Exception as e:
        results.append(("expenses: empty employee_ids -> graceful skip, no new expense (Pattern 5)", False, str(e)))

    # Step 4 — S15/R20: analytic distribution live end-to-end. expense_pct=100
    # -> every created expense carries analytic_distribution referencing one
    # of the (newly created) cost-center accounts, read back in the live
    # {"<id>": 100.0} shape.
    try:
        rctx = _make_rctx()
        rctx.employee_ids = [employee_id]
        rctx.module_selections.hr_expense = ExpenseConfig(count_per_employee=2, approved_pct=0)
        rctx.module_selections.analytic = AnalyticConfig(sale_pct=0, purchase_pct=0,
                                                         expense_pct=100)

        expenses.create_expense_data(client, None, rctx)

        assert rctx.analytic_account_ids, "get_or_create_analytic_accounts left ctx.analytic_account_ids empty"
        accounts = client.search_read(
            'account.analytic.account', [["id", "in", rctx.analytic_account_ids]],
            fields=["name", "plan_id"], limit=0,
        )
        assert len(accounts) == len(rctx.analytic_account_ids), accounts
        plan_id = accounts[0]["plan_id"]
        plan_id = plan_id[0] if isinstance(plan_id, (list, tuple)) else plan_id  # Pattern 6
        plan = client.search_read('account.analytic.plan', [["id", "=", plan_id]], fields=["name"], limit=1)
        assert plan and plan[0]["name"] == "Kostenstellen", plan

        created = client.search_read(
            'hr.expense', [["employee_id", "=", employee_id], ["name", "like", "Geschäftsreise"],
             ["analytic_distribution", "!=", False]],
            fields=["analytic_distribution"], limit=0,
        )
        assert len(created) >= 2, f"expected at least 2 expenses with a distribution, got {len(created)}"
        for rec in created:
            keys = list(rec["analytic_distribution"].keys())
            assert len(keys) == 1 and int(keys[0]) in rctx.analytic_account_ids, rec

        results.append((
            "expenses: R20 analytic distribution live end-to-end — expense_pct=100, "
            "read-back on new cost-center plan (Pattern 4)",
            True, f"{len(created)} expenses, accounts={rctx.analytic_account_ids}",
        ))
    except Exception as e:
        results.append((
            "expenses: R20 analytic distribution live end-to-end — expense_pct=100, "
            "read-back on new cost-center plan (Pattern 4)",
            False, str(e),
        ))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
