"""Deterministic assembly of master-data records from atomic name tokens.

No LLM access, no Odoo client dependency — pure functions, fully unit-testable
offline. This is the single source of address/pricing assembly logic (see
IMPLEMENTIERUNGSPLAN.md A1): callers pass in names, this module fills in
everything structural (addresses, emails, phones, prices) from static_data.py.
"""

import random
from typing import Any, Dict, List, Optional, Tuple

import static_data
import text_utils

_PRODUCT_TYPE_MAP = {
    'services': {'type': 'service'},
    'consumables': {'type': 'consu', 'is_storable': False},
    'storables': {'type': 'consu', 'is_storable': True},
}

_DEFAULT_COUNTRIES = ["DE", "AT", "CH"]
_DEFAULT_WEIGHTS = [70, 15, 15]


def _pick_country(target_countries: Optional[List[str]] = None) -> str:
    if target_countries:
        return random.choice(target_countries)
    return random.choices(_DEFAULT_COUNTRIES, weights=_DEFAULT_WEIGHTS)[0]


def _format_zip(city_entry: dict, country_code: str) -> str:
    zip_num = random.randint(city_entry["zip_min"], city_entry["zip_max"])
    return str(zip_num).zfill(static_data.ZIP_LEN[country_code])


def _random_address(target_countries: Optional[List[str]] = None) -> Tuple[Dict[str, str], dict]:
    """Returns (address_dict, city_entry) — city_entry carries area_code for phone gen."""
    country_code = _pick_country(target_countries)
    city_entry = random.choice(static_data.CITIES[country_code])
    address = {
        "street": f"{random.choice(static_data.STREET_NAMES)} {random.randint(1, 199)}",
        "zip": _format_zip(city_entry, country_code),
        "city": city_entry["city"],
        "country_code": country_code,
    }
    return address, city_entry


def build_company(name: str, target_countries: Optional[List[str]] = None) -> dict:
    """Returns name/street/zip/city/country_code/email/phone/website/is_company.
    Caller pops country_code and resolves it to country_id.

    target_countries: pool of country codes to draw from, default DACH
    (weighted toward DE, the primary market). Exposed as a parameter — not
    hardcoded — so a future multi-country feature can pass an explicit
    GUI-selected country list without changing this function's shape.
    """
    address, city_entry = _random_address(target_countries)
    country_code = address["country_code"]
    dial = static_data.COUNTRY_DIAL[country_code]
    slug = text_utils.slugify(name)
    return {
        "name": name,
        "street": address["street"],
        "zip": address["zip"],
        "city": address["city"],
        "country_code": country_code,
        "email": f"info@{slug}.example.com",
        "phone": f"+{dial} {city_entry['area_code']} {random.randint(1000000, 9999999)}",
        "website": f"https://www.{slug}.example.com",
        "is_company": True,
    }


def build_contacts(
    n_delivery: int, n_invoice: int, n_other: int, person_names: Optional[List[str]] = None
) -> List[dict]:
    """delivery: location label + full address, type='delivery'.
    invoice: label 'Rechnungsadresse' + full address, type='invoice'.
    contact (n_other): person name (cycled from person_names, empty pool ->
    synthetic 'Kontakt N' names) + email, type='contact' — always the literal
    string 'contact', never 'other' (res.partner.type has a distinct 'other'
    value; the old LLM prompt used to confuse the two for named contacts).
    """
    contacts = []
    for _ in range(n_delivery):
        address, city_entry = _random_address()
        label = f"{random.choice(static_data.LOCATION_LABELS)} " \
                f"{random.choice(['Nord', 'Süd', 'Ost', 'West', city_entry['city']])}"
        contacts.append({"name": label, "type": "delivery", **address})
    for _ in range(n_invoice):
        address, _city_entry = _random_address()
        contacts.append({"name": "Rechnungsadresse", "type": "invoice", **address})
    pool = person_names or []
    for i in range(n_other):
        name = pool[i % len(pool)] if pool else f"Kontakt {i + 1}"
        contacts.append({"name": name, "type": "contact", "email": text_utils.email_from_name(name)})
    return contacts


def price_for_product() -> Tuple[float, float]:
    """(list_price, standard_price) — the single source of pricing logic."""
    list_price = round(random.uniform(15, 500), 2)
    standard_price = round(list_price * random.uniform(0.4, 0.8), 2)
    return list_price, standard_price


def build_products(product_names: Dict[str, List[str]], descriptions: Optional[Dict[str, str]] = None) -> List[dict]:
    """product_names: {"services": [...], "consumables": [...], "storables": [...]}.
    Applies _PRODUCT_TYPE_MAP, calls price_for_product() per item, attaches a
    description if present. Returns vals dicts ready for create_batch.
    """
    descriptions = descriptions or {}
    all_vals = []
    for ptype, template in _PRODUCT_TYPE_MAP.items():
        for name in product_names.get(ptype, []):
            if not name:
                continue
            list_price, standard_price = price_for_product()
            vals: Dict[str, Any] = {
                **template,
                "name": name,
                "list_price": list_price,
                "standard_price": standard_price,
                "sale_ok": True,
                "purchase_ok": True,
            }
            if name in descriptions:
                vals["description"] = descriptions[name]
            all_vals.append(vals)
    return all_vals
