"""Master data module: creates products and res.partner records from Gemini creative data."""

import random
from typing import Any, Dict

from config import RunContext
from odoo_repository import resolve_country_ids

_INVALID_PRODUCT_FIELDS = {'uom', 'vat', 'vat_id', 'detailed_type'}


def create_master_data(client, gemini, ctx: RunContext, creative_data: Dict[str, Any]) -> None:
    """Batch-creates products and companies/contacts from Gemini creative data.

    Writes ctx.product_ids and ctx.company_ids.
    """
    if not creative_data:
        print("Keine kreativen Daten vorhanden — Stammdaten werden übersprungen.")
        return
    _create_products(client, creative_data, ctx)
    _create_partners(client, creative_data, ctx)


# ------------------------------------------------------------------
# Products
# ------------------------------------------------------------------

_PRODUCT_TYPE_MAP = {
    'services': {'type': 'service'},
    'consumables': {'type': 'consu', 'is_storable': False},
    'storables': {'type': 'consu', 'is_storable': True},
}


def _create_products(client, creative_data: Dict[str, Any], ctx: RunContext) -> None:
    print("\n--- Erstelle Produkte ---")
    all_vals = []
    for product_type, template in _PRODUCT_TYPE_MAP.items():
        for creative_product in creative_data.get('products', {}).get(product_type, []):
            if not creative_product.get('name'):
                continue
            vals = template.copy()
            vals.update({
                k: v for k, v in creative_product.items()
                if v is not None and k not in _INVALID_PRODUCT_FIELDS
            })
            if 'list_price' not in vals:
                vals['list_price'] = round(random.uniform(15, 500), 2)
            if 'standard_price' not in vals:
                vals['standard_price'] = round(vals['list_price'] * random.uniform(0.4, 0.8), 2)
            all_vals.append(vals)

    if not all_vals:
        print("-> Keine Produkte in Gemini-Daten gefunden.")
        return

    ids = client.create_batch('product.product', all_vals)
    ctx.product_ids.extend(ids)
    print(f"✅ {len(ids)} Produkte erstellt.")


# ------------------------------------------------------------------
# Partners (companies + contacts)
# ------------------------------------------------------------------

def _collect_country_codes(creative_data: Dict[str, Any]):
    codes = []
    for scenario in creative_data.get('companies', []):
        cd = scenario.get('company_data', {})
        if cd.get('country_code'):
            codes.append(cd['country_code'])
        for contact in scenario.get('contacts', []):
            if contact.get('country_code'):
                codes.append(contact['country_code'])
    return codes


def _create_partners(client, creative_data: Dict[str, Any], ctx: RunContext) -> None:
    print("\n--- Erstelle Kunden und Kontakte ---")
    country_codes = _collect_country_codes(creative_data)
    country_map = resolve_country_ids(client, country_codes)

    for scenario in creative_data.get('companies', []):
        company_data = scenario.get('company_data', {})
        if not company_data.get('name'):
            continue

        vals = {k: v for k, v in company_data.items() if v is not None}
        vals.pop('vat', None)
        vals.pop('vat_id', None)
        vals.pop('country', None)
        vals.pop('company_id', None)
        country_code = vals.pop('country_code', 'DE')
        if country_code.upper() in country_map:
            vals['country_id'] = country_map[country_code.upper()]
        vals['is_company'] = True
        company_id = client.create('res.partner', vals)
        ctx.company_ids.append(company_id)
        print(f"   Partner erstellt: {company_data.get('name')} (ID: {company_id})")

        for contact_data in scenario.get('contacts', []):
            cvals = {k: v for k, v in contact_data.items() if v is not None}
            cvals.pop('vat', None)
            cvals.pop('vat_id', None)
            cvals.pop('country', None)
            cvals.pop('company_id', None)
            cvals['parent_id'] = company_id
            # Defensive: LLM sometimes returns type='other' for person contacts.
            # Named contacts are people → type must be 'contact', not 'other'.
            if cvals.get('name') and cvals.get('type') == 'other':
                cvals['type'] = 'contact'
            contact_cc = cvals.pop('country_code', None)
            if contact_cc and contact_cc.upper() in country_map:
                cvals['country_id'] = country_map[contact_cc.upper()]
            client.create('res.partner', cvals)
