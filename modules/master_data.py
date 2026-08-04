"""Master data module: creates products and res.partner records.

Structure (addresses, contacts, prices) is assembled deterministically by
data_factory.py from name atoms — see IMPLEMENTIERUNGSPLAN.md A1. This module
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
    ctx.name_banks + fallback). Writes ctx.product_ids and ctx.company_ids.

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

    ids = client.create_batch('product.product', all_vals)
    ctx.product_ids.extend(ids)
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

    # Pass 1: build + batch-create all companies (D3 — was 1 create() call per company).
    company_names: List[str] = []
    company_vals_list: List[Dict[str, Any]] = []
    for idx in range(ctx.criteria.num_companies):
        name = _unique_name(company_pool, idx, used_names)
        vals = data_factory.build_company(name, target_countries=_TARGET_COUNTRIES)
        country_code = vals.pop('country_code')
        if country_code in country_map:
            vals['country_id'] = country_map[country_code]
        company_names.append(name)
        company_vals_list.append(vals)

    company_ids = client.create_batch('res.partner', company_vals_list)
    ctx.company_ids.extend(company_ids)
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
            all_contact_vals.append(cvals)

    client.create_batch('res.partner', all_contact_vals)
