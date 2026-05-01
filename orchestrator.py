"""Orchestrates all demo data creation modules in dependency order.

Module execution order is important:
1. master_data  — products and partners (everything else depends on these)
2. mrp          — manufacturing products + BOMs (products feed into sale orders)
3. crm          — opportunities (linked to orders in sale module)
4. sale         — orders, confirmation, CRM won-stage update
5. account      — invoices from orders, vendor bills, bank transactions
6. hr           — employees (project/timesheet need them)
7. project      — projects, tasks, stages (timesheets need project_ids)
8. hr_timesheet — timesheets
9. hr_recruitment — recruiting
"""

from config import RunContext
from llm_service import LLMService
from odoo_client import OdooJson2Client
from modules import master_data, crm, sale, accounting, hr, project, mrp, recruiting


def run(client: OdooJson2Client, gemini: LLMService, ctx: RunContext) -> None:
    # --- Upfront Gemini calls (data needed before any Odoo writes) ---
    if not ctx.skip_master_data:
        print("\n--- Generiere kreative Stammdaten ---")
        creative_data = gemini.fetch_creative_data(vars(ctx.criteria)) or {}
    else:
        creative_data = {}
        print("\n-> Stammdaten-Erstellung übersprungen (vorhandene Daten werden verwendet)")

    print("\n--- Generiere Namensvorschläge ---")
    ctx.name_banks = gemini.fetch_name_suggestions(vars(ctx.criteria), ctx.language_name) or {}

    # --- Master data (no dependencies) ---
    if not ctx.skip_master_data:
        _run_module("Stammdaten", master_data.create_master_data, client, gemini, ctx,
                    extra_args=(creative_data,))

    # Ensure fallback partners/products if master data failed or returned nothing
    _ensure_fallback_partners(client, ctx)
    _ensure_fallback_products(client, ctx)

    # --- Transactional modules in dependency order ---
    module_order = [
        ("mrp",            "mrp" in ctx.installed_modules,              mrp.create_mrp_data),
        ("crm",            "crm" in ctx.installed_modules,              crm.create_crm_data),
        ("sale",           "sale" in ctx.installed_modules,             sale.create_sale_data),
        ("account",        "account" in ctx.installed_modules,          accounting.create_accounting_data),
        ("hr",             "hr" in ctx.installed_modules,               hr.create_hr_data),
        ("project",        "project" in ctx.installed_modules,          project.create_project_data),
        ("hr_timesheet",   "hr_timesheet" in ctx.installed_modules,     project.create_timesheet_data),
        ("hr_recruitment", "hr_recruitment" in ctx.installed_modules,   recruiting.create_recruiting_data),
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
        _run_module(module_code, handler, client, gemini, ctx)

    # Summary
    print(f"\n[LLM] Gesamtanfragen: {gemini.total_calls}, Gesamttoken: {gemini.total_tokens}")


def _run_module(name, handler, client, gemini, ctx, extra_args=()):
    try:
        handler(client, gemini, ctx, *extra_args)
    except Exception as exc:
        print(f"⚠️  Modul '{name}' fehlgeschlagen: {exc} — andere Module werden fortgesetzt.")


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
    print("-> Erstelle Fallback-Partner")
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
        pid = client.create('product.product', {
            "name": name, "type": "consu",
            "list_price": round(random.uniform(15, 500), 2),
        })
        ctx.product_ids.append(pid)
        print(f"-> Fallback-Produkt erstellt: {name} (ID: {pid})")
