"""Orchestrates all demo data creation modules in dependency order.

Module execution order is important:
1. master_data  — products and partners (everything else depends on these)
2. mrp          — manufacturing products + BOMs (products feed into sale orders)
3. crm          — opportunities (linked to orders in sale module)
4. sale         — orders, confirmation, CRM won-stage update; confirming an
                   order with a service_tracking-tagged product also triggers
                   Odoo's native project/task creation (R8)
5. hr           — employees (project/timesheet need them)
6. project      — projects, tasks, stages (timesheets need project_ids)
7. hr_timesheet — timesheets; logs hours against order-linked tasks (R8),
                   which is what makes their delivered quantity non-zero
8. account      — invoices from orders (delivered-qty-aware, via the native
                   sale.advance.payment.inv wizard — R8), vendor bills, bank
                   transactions. Moved from position 4 to run after
                   hr_timesheet: a service line's invoiced quantity is driven
                   by qty_delivered, computed from timesheets that don't exist
                   yet if account ran earlier.
9. hr_recruitment — recruiting
10. purchase       — purchase orders + confirmation + vendor bills from
                      ctx.component_ids (R2); contributes to ctx.bill_ids,
                      which documents (P1) reads — must run before it
11. stock          — stock.quant on-hand seeding (R3); independent of
                      purchase (no shared state), placed after for narrative
                      order (procure → stock) only
12. hr_expense     — expense records per employee (R19); needs ctx.employee_ids
                      (hr) only, placed here (not right after hr) so it doesn't
                      disturb the existing hr→project→timesheet→account chain
13. documents      — PDF attachments for vendor bills (needs bill_ids) and
                      applicant CVs (needs applicant_ids); always runs last
"""

import logging
import data_factory
from config import RunContext
from llm_service import LLMService
from odoo_client import OdooJson2Client
from logging_setup import configure_logging
from modules import master_data, crm, sale, accounting, hr, project, mrp, recruiting, documents, purchase, inventory, expenses

configure_logging()
logger = logging.getLogger(__name__)


def run(client: OdooJson2Client, gemini: LLMService, ctx: RunContext,
        on_module_start=None, on_module_done=None) -> None:
    # --- Upfront Gemini calls (data needed before any Odoo writes) ---
    if not ctx.skip_master_data:
        logger.info("\n--- Generiere kreative Stammdaten ---")
        creative_atoms = gemini.fetch_creative_atoms(vars(ctx.criteria), ctx.language_name) or {}
    else:
        creative_atoms = {}
        logger.info("\n-> Stammdaten-Erstellung übersprungen (vorhandene Daten werden verwendet)")

    logger.info("\n--- Generiere Namensvorschläge ---")
    ctx.name_banks = gemini.fetch_name_suggestions(vars(ctx.criteria), ctx.language_name) or {}

    # --- Master data (no dependencies) ---
    if not ctx.skip_master_data:
        _run_module("Stammdaten", master_data.create_master_data, client, gemini, ctx,
                    extra_args=(creative_atoms,),
                    on_start=on_module_start, on_done=on_module_done)

    # Ensure fallback partners/products if master data failed or returned nothing
    _ensure_fallback_partners(client, ctx)
    _ensure_fallback_products(client, ctx)

    # --- Transactional modules in dependency order ---
    module_order = [
        ("mrp",            "mrp" in ctx.installed_modules,              mrp.create_mrp_data),
        ("crm",            "crm" in ctx.installed_modules,              crm.create_crm_data),
        ("sale",           "sale" in ctx.installed_modules,             sale.create_sale_data),
        ("hr",             "hr" in ctx.installed_modules,               hr.create_hr_data),
        ("project",        "project" in ctx.installed_modules,          project.create_project_data),
        ("hr_timesheet",   "hr_timesheet" in ctx.installed_modules,     project.create_timesheet_data),
        ("account",        "account" in ctx.installed_modules,          accounting.create_accounting_data),
        ("hr_recruitment", "hr_recruitment" in ctx.installed_modules,   recruiting.create_recruiting_data),
        ("purchase",       "purchase" in ctx.installed_modules,          purchase.create_purchase_data),
        ("stock",          "stock" in ctx.installed_modules,             inventory.create_inventory_data),
        ("hr_expense",     "hr_expense" in ctx.installed_modules,        expenses.create_expense_data),
        # "documents" is not a real Odoo-probed module — ir.attachment is core,
        # always available, hence hardcoded True (not gated on installed_modules,
        # which would incorrectly tie this to Odoo's unrelated real "Documents"
        # app). Runs last: depends on ctx.bill_ids (accounting) and
        # ctx.applicant_ids (hr_recruitment) already being populated.
        ("documents",      True,                                       documents.create_documents),
    ]

    for module_code, is_installed, handler in module_order:
        if not is_installed:
            continue
        sel = ctx.module_selections.get(module_code)
        # Skip modules the user didn't select (0 count or empty dict).
        # CRM is special: run if either opportunities OR leads were requested.
        if module_code == 'crm':
            if not (ctx.module_selections.crm > 0 or ctx.module_selections.leads > 0):
                continue
        elif not sel:
            continue
        _run_module(module_code, handler, client, gemini, ctx,
                    on_start=on_module_start, on_done=on_module_done)

    # Summary
    logger.info(f"\n[LLM] Gesamtanfragen: {gemini.total_calls}, Gesamttoken: {gemini.total_tokens}")


def _run_module(name, handler, client, gemini, ctx, extra_args=(), on_start=None, on_done=None):
    if on_start:
        on_start(name)
    try:
        handler(client, gemini, ctx, *extra_args)
        if on_done:
            on_done(name, ok=True)
    except Exception as exc:
        logger.warning(f"⚠️  Modul '{name}' fehlgeschlagen: {exc} — andere Module werden fortgesetzt.")
        if on_done:
            on_done(name, ok=False)


# ------------------------------------------------------------------
# Fallback helpers
# ------------------------------------------------------------------

def _ensure_fallback_partners(client, ctx: RunContext) -> None:
    """Create a single fallback company if none were created by master_data."""
    needs = (
        ctx.module_selections.sale > 0
        or ctx.module_selections.crm > 0
        or ctx.module_selections.leads > 0
        or ctx.module_selections.account > 0
    )
    if not needs or ctx.company_ids:
        return
    from fallback_data import FALLBACK_COMPANIES
    import random
    names = ctx.name_banks.get('company_names', []) or FALLBACK_COMPANIES
    logger.info("-> Erstelle Fallback-Partner")
    cid = client.create('res.partner', {"name": random.choice(names)})
    ctx.company_ids.append(cid)


def _ensure_fallback_products(client, ctx: RunContext) -> None:
    """Create fallback products if none were created and modules that need them are active."""
    needs = ctx.module_selections.sale > 0 or ctx.module_selections.account > 0
    if not needs:
        return
    from fallback_data import FALLBACK_PRODUCTS
    import random
    names = ctx.name_banks.get('product_names', [])
    fallback = FALLBACK_PRODUCTS.get(ctx.industry, FALLBACK_PRODUCTS['IT'])
    pool = names or fallback
    while len(ctx.product_ids) < 2:
        name = random.choice(pool)
        list_price, standard_price = data_factory.price_for_product()
        pid = client.create('product.product', {
            "name": name, "type": "consu",
            "list_price": list_price, "standard_price": standard_price,
        })
        ctx.product_ids.append(pid)
        logger.info(f"-> Fallback-Produkt erstellt: {name} (ID: {pid})")
