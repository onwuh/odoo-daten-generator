"""Master data module: creates products and res.partner records.

Structure (addresses, contacts, prices) is assembled deterministically by
data_factory.py from name atoms — see ROADMAP.md A1. This module
no longer parses free-form LLM structure; it only supplies names.
"""

import logging
from typing import Any, Dict, List, Set

import data_factory
import fallback_data
from config import RunContext
from odoo_repository import resolve_country_ids

logger = logging.getLogger(__name__)

_TARGET_COUNTRIES = ["DE", "AT", "CH"]


def create_master_data(client, gemini, ctx: RunContext, atoms: Dict[str, Any]) -> None:
    """Creates products (from atoms + fallback) and companies/contacts (from
    ctx.name_banks + fallback). Writes ctx.product_ids and ctx.partner_company_ids.

    Company/contact creation no longer depends on the LLM atoms call
    succeeding — only product names/descriptions degrade to static fallbacks
    if atoms is empty.
    """
    country_map = resolve_country_ids(client, _TARGET_COUNTRIES)
    _create_products(client, atoms or {}, ctx)
    _create_partners(client, ctx, country_map)


# ------------------------------------------------------------------
# Products
# ------------------------------------------------------------------

def _create_products(client, atoms: Dict[str, Any], ctx: RunContext) -> None:
    logger.info("\n--- Erstelle Produkte ---")
    product_names = atoms.get('product_names', {})
    if not any(product_names.get(k) for k in ('services', 'consumables', 'storables')):
        fallback_pool = fallback_data.FALLBACK_PRODUCTS.get(ctx.industry, fallback_data.FALLBACK_PRODUCTS['IT'])
        counts = {
            'services': ctx.criteria.num_services,
            'consumables': ctx.criteria.num_consumables,
            'storables': ctx.criteria.num_storables,
        }
        product_names = _distribute_fallback_names(fallback_pool, counts)

    all_vals = data_factory.build_products(product_names, atoms.get('product_descriptions'))
    if not all_vals:
        logger.info("-> Keine Produkte zu erstellen.")
        return

    # S16/D8-Ergänzung: same reasoning as _create_partners above — D14's
    # Odoo-context injection does not default product.product's company_id
    # from context (live-confirmed), needs setting explicitly in vals.
    target_company_id = ctx.res_company_ids[0] if ctx.res_company_ids else None
    if target_company_id is not None:
        for vals in all_vals:
            vals['company_id'] = target_company_id

    # R16: EAN-13 barcodes, deduped against barcodes already on the target DB
    # (not just within this run) — a duplicate would fail product.product's
    # unique constraint and, per odoo_client.create_batch, is outside the
    # 404/422 fallback window, taking the whole batch down with it.
    existing = client.search_read(
        'product.product', [["barcode", "!=", False]], fields=["barcode"], limit=0,
    )
    existing_barcodes = {rec["barcode"] for rec in existing if rec.get("barcode")}
    data_factory.assign_barcodes(all_vals, existing_barcodes)

    # S13/R13: lot/serial tracking on a configurable share of storables.
    # Gated on avg_qty>0 (not just "stock dict non-empty") so a product never
    # ends up marked tracking='lot' with no stock step ever creating a
    # stock.lot for it — inventory.py's own early-return uses the same
    # avg_qty<=0 condition, see config.py's stock-dict comment. 'stock' in
    # installed_modules is a defensive second check — the frontend keeps a
    # not-installed module's card visible-but-disabled rather than hiding it
    # (static/app.js), so this can't rely on the card never being submitted.
    stock_config = ctx.module_selections.stock
    if (stock_config is not None and stock_config.avg_qty > 0
            and 'stock' in ctx.installed_modules):
        data_factory.assign_tracking(
            all_vals,
            stock_config.tracking_lot_pct,
            stock_config.tracking_serial_pct,
        )

    # R8: tag every service product so Odoo's own automation creates a
    # Project+Task on order confirmation and drives invoicing from delivered
    # (timesheet) quantity — gated on app installation, not per-run selection,
    # since 'task_in_project' isn't a legal service_tracking value without the
    # 'project' app (added by the sale_project integration module).
    if 'project' in ctx.installed_modules and 'hr_timesheet' in ctx.installed_modules:
        for vals in all_vals:
            if vals.get('type') == 'service':
                vals['service_tracking'] = 'task_in_project'
                vals['invoice_policy'] = 'delivery'
                vals['service_type'] = 'timesheet'

    # S13/WP5-review: the whole product batch (services+consumables+storables,
    # not just the tracked share) rides on this one call — if the target
    # instance rejects the tracking write (ACL/group restriction, untested
    # beyond demo-test5), don't let that take down the entire run's products.
    # Retry once with tracking stripped rather than propagating.
    try:
        ids = client.create_batch('product.product', all_vals)
    except Exception as e:
        if any('tracking' in vals for vals in all_vals):
            logger.warning(
                f"⚠️  Produkt-Batch mit Tracking-Feldern fehlgeschlagen ({e}) — "
                f"erneuter Versuch ohne 'tracking'.")
            for vals in all_vals:
                vals.pop('tracking', None)
            ids = client.create_batch('product.product', all_vals)
        else:
            raise
    ctx.product_ids.extend(ids)
    # S13/Befund 4: ids this run actually created here — inventory.py's
    # lot/serial branch reads this to never touch a use_existing customer's
    # pre-existing product or an mrp.py finished-good/component, both of
    # which also land in ctx.product_ids but never here.
    ctx.new_product_ids.extend(ids)
    logger.info(f"✅ {len(ids)} Produkte erstellt.")


def _distribute_fallback_names(pool: List[str], counts: Dict[str, int]) -> Dict[str, List[str]]:
    """Cycles a flat fallback name pool into per-category lists, suffixing on
    wraparound so names stay distinguishable. Pattern-1 guarded: empty pool
    still returns the right shape (empty lists), no crash."""
    result: Dict[str, List[str]] = {}
    idx = 0
    for category, count in counts.items():
        names = []
        for i in range(count):
            if not pool:
                names.append(f"{category.capitalize()} {i + 1}")
                continue
            name = pool[idx % len(pool)]
            if idx >= len(pool):
                name = f"{name} ({idx // len(pool) + 1})"
            names.append(name)
            idx += 1
        result[category] = names
    return result


# ------------------------------------------------------------------
# Partners (companies + contacts)
# ------------------------------------------------------------------

def _unique_name(pool: List[str], idx: int, used: Set[str]) -> str:
    """Pattern-1 guarded: empty pool -> synthetic name, no crash.
    Cycles pool by idx; on repeat (pool shorter than requested count),
    appends a numeric suffix so `used` never gets a duplicate."""
    if not pool:
        return f"Firma {idx + 1}"
    name = pool[idx % len(pool)]
    if name in used:
        name = f"{name} ({idx // len(pool) + 1})"
    used.add(name)
    return name


def _create_partners(client, ctx: RunContext, country_map: Dict[str, int]) -> None:
    logger.info("\n--- Erstelle Kunden und Kontakte ---")
    company_pool = ctx.name_banks.get('company_names') or fallback_data.FALLBACK_COMPANIES
    person_pool = ctx.name_banks.get('employee_names') or fallback_data.FALLBACK_EMPLOYEES
    used_names: Set[str] = set()

    # S16/D8-Ergänzung: the real res.company this run's partners belong to —
    # NOT to be confused with the company_id loop variable below (a
    # res.partner id, the customer/company contact just created). D14's
    # Odoo-context injection does NOT make res.partner default this field
    # from the context (live-confirmed, unlike sale.order/crm.lead) — has to
    # be set explicitly in vals. None (single-company run, or ctx never
    # resolved a target company) leaves it unset, today's behavior.
    target_company_id = ctx.res_company_ids[0] if ctx.res_company_ids else None

    # Pass 1: build + batch-create all companies (D3 — was 1 create() call per company).
    company_names: List[str] = []
    company_vals_list: List[Dict[str, Any]] = []
    for idx in range(ctx.criteria.num_companies):
        name = _unique_name(company_pool, idx, used_names)
        vals = data_factory.build_company(name, target_countries=_TARGET_COUNTRIES)
        country_code = vals.pop('country_code')
        if country_code in country_map:
            vals['country_id'] = country_map[country_code]
        if target_company_id is not None:
            vals['company_id'] = target_company_id
        company_names.append(name)
        company_vals_list.append(vals)

    company_ids = client.create_batch('res.partner', company_vals_list)
    ctx.partner_company_ids.extend(company_ids)
    for name, company_id in zip(company_names, company_ids):
        logger.info(f"   Partner erstellt: {name} (ID: {company_id})")

    # Pass 2: build + batch-create all contacts across all companies in one call
    # (contact IDs are never referenced downstream — safe to not track them individually).
    all_contact_vals: List[Dict[str, Any]] = []
    for company_id in company_ids:
        contacts = data_factory.build_contacts(
            ctx.criteria.num_delivery_contacts,
            ctx.criteria.num_invoice_contacts,
            ctx.criteria.num_other_contacts,
            person_names=person_pool,
        )
        for cvals in contacts:
            cvals['parent_id'] = company_id
            contact_cc = cvals.pop('country_code', None)
            if contact_cc and contact_cc in country_map:
                cvals['country_id'] = country_map[contact_cc]
            if target_company_id is not None:
                cvals['company_id'] = target_company_id
            all_contact_vals.append(cvals)

    client.create_batch('res.partner', all_contact_vals)
