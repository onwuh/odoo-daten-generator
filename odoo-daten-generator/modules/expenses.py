"""Expenses module: hr.expense records per employee, a configurable
approved share (R19).

No LLM call — descriptions are a template over the searched expense
category name (LLM-minimalism: nothing here is creative text worth an
LLM round-trip). product_id must be set on every record: live-verified
(S12/WP3) that Odoo rejects both a plain approval_state write and the
action_submit/action_approve methods with "Select a product to proceed"
when it's missing, even though hr.expense.product_id is ORM-optional.
"""

import datetime
import logging
import random

import odoo_actions
from config import RunContext

logger = logging.getLogger(__name__)


def _unwrap(val):
    if isinstance(val, (list, tuple)) and val:
        return val[0]
    return val


def create_expense_data(client, gemini, ctx: RunContext) -> None:
    """Creates hr.expense records for every employee in ctx.employee_ids,
    then submits+approves a configurable share (write(approval_state=...) —
    live-verified S12/WP3, no action method needed)."""
    sel = ctx.module_selections.hr_expense
    if not sel:
        return
    if not ctx.employee_ids:
        logger.info("-> Keine Mitarbeiter vorhanden — Expenses übersprungen")
        return

    logger.info("\n--- EXPENSES: Erstelle Spesenabrechnungen ---")

    categories = client.search_read(
        'product.product', [["can_be_expensed", "=", True]], fields=["id", "name"], limit=0,
    )
    if not categories:
        logger.warning("⚠️  Keine Ausgabenkategorien (can_be_expensed) gefunden — Expenses übersprungen.")
        return

    currency_id = None
    company_id = odoo_actions.get_main_company_id(client)
    if company_id:
        company = client.search_read(
            'res.company', [["id", "=", company_id]], fields=["currency_id"], limit=1,
        )
        if company:
            currency_id = _unwrap(company[0].get("currency_id"))

    count_per_employee = sel.get("count_per_employee", 3)
    approved_pct = sel.get("approved_pct", 70)
    today_str = datetime.date.today().isoformat()

    vals_list = []
    for emp_id in ctx.employee_ids:
        for _ in range(count_per_employee):
            category = random.choice(categories)
            vals = {
                "employee_id": emp_id,
                "product_id": category["id"],
                "name": f"{category['name']} – Geschäftsreise",
                "payment_mode": "own_account",
                "total_amount": round(random.uniform(15, 250), 2),
                "date": today_str,
            }
            if currency_id:
                vals["currency_id"] = currency_id
            vals_list.append(vals)

    if not vals_list:
        return

    expense_ids = client.create_batch('hr.expense', vals_list)
    if not expense_ids:
        return

    num_approve = round(len(expense_ids) * approved_pct / 100)
    to_approve = random.sample(expense_ids, k=min(num_approve, len(expense_ids)))

    approved_count = 0
    if to_approve:
        try:
            client.write('hr.expense', to_approve, {"approval_state": "submitted"})
            client.write('hr.expense', to_approve, {"approval_state": "approved"})
            approved_count = len(to_approve)
        except Exception as e:
            logger.warning(f"-> Konnte approval_state nicht für alle {len(to_approve)} Spesen setzen: {e}")

    logger.info(f"✅ {len(expense_ids)} Spesenabrechnungen erstellt, {approved_count} genehmigt.")
