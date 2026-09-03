"""Deterministic assembly of master-data records from atomic name tokens.

No LLM access, no Odoo client dependency — pure functions, fully unit-testable
offline. This is the single source of address/pricing assembly logic (see
ROADMAP.md A1): callers pass in names, this module fills in
everything structural (addresses, emails, phones, prices) from static_data.py.
"""

import logging
import random
import zlib
from typing import Any, Dict, List, Optional, Set, Tuple

import static_data
import text_utils

logger = logging.getLogger(__name__)

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


_FAKE_BANKS = [
    ("Deutsche Bank", "DEUTDEFF"),
    ("Commerzbank", "COBADEFF"),
    ("Sparkasse", "GENODEF1"),
    ("Volksbank Raiffeisenbank", "GENODED1"),
    ("Postbank", "PBNKDEFF"),
    ("DKB", "BYLADEM1"),
]


def build_vendor_footer_info(supplier_name: str) -> Dict[str, Any]:
    """Deterministic fake footer data for a vendor-bill PDF (VAT ID, bank
    details, payment terms, customer number) — S10/R10 (F4), extended for a
    realistic invoice footer. No LLM call: these fields exist to make
    different suppliers' bills look distinct, not to be individually
    meaningful. "tax_number" is a USt-IdNr. despite the key name (DE + 9
    digits is that format, not the separate local Steuernummer) — pdf_factory
    labels it accordingly. "skonto_percent"/"skonto_days" are an early-payment
    discount clause (e.g. "2% Skonto bei Zahlung innerhalb 7 Tagen") — a
    detail real invoicing-software exports carry that a bare "N Tage netto"
    line doesn't; skonto_days is always drawn from a pool below every
    possible payment_terms_days value so the discount window is always
    shorter than the plain due date, as it has to be to make sense.

    Uses a LOCAL random.Random instance seeded from the supplier name's
    CRC32, never the module-global `random` this file uses everywhere else:
    reseeding the global generator here (random.seed(...)) would make every
    draw AFTER this call reproduce a fixed sequence too — including this same
    pipeline's later, genuinely-random master-data/product draws — a
    cross-contamination bug that would stay invisible until someone reorders
    the pipeline. The XOR constant just decorrelates this seed from
    pdf_factory's own per-supplier variant-selection seed, which is also
    derived from the same name; the two don't need to move independently for
    correctness, but there's no reason to hand them the exact same stream.
    """
    rng = random.Random(zlib.crc32((supplier_name or "").encode("utf-8")) ^ 0x1BADB002)
    bank_name, bic = rng.choice(_FAKE_BANKS)
    return {
        "tax_number": f"DE{rng.randint(100000000, 999999999)}",
        "iban": (f"DE{rng.randint(10, 99)} {rng.randint(10000000, 99999999):08d} "
                f"{rng.randint(1000000000, 9999999999):010d}"),
        "bic": bic,
        "bank_name": bank_name,
        "payment_terms_days": rng.choice([14, 21, 30, 45]),
        "customer_number": f"K-{rng.randint(10000, 99999)}",
        "skonto_percent": rng.choice([2, 3, 5]),
        "skonto_days": rng.choice([7, 10]),
    }


def build_recipient_fallback_address(company_name: str) -> Dict[str, str]:
    """Deterministic fake DE street/zip/city for the invoice recipient block
    when the real res.company record has no address configured.

    Vendor-bill PDFs are addressed to this run's own company — but on a
    freshly provisioned demo SaaS tenant that company record is typically
    still blank (street/zip/city all empty strings, live-confirmed on
    demo-test5's default "id=1" company), so without this the recipient
    block would either be omitted or print nothing useful. Same LOCAL-rng
    pattern as build_vendor_footer_info, seeded from the company name with a
    different XOR constant so the two don't draw the same stream.
    """
    rng = random.Random(zlib.crc32((company_name or "").encode("utf-8")) ^ 0x5A17ADDA)
    city_entry = rng.choice(static_data.CITIES["DE"])
    zip_code = str(rng.randint(city_entry["zip_min"], city_entry["zip_max"])).zfill(static_data.ZIP_LEN["DE"])
    return {
        "street": f"{rng.choice(static_data.STREET_NAMES)} {rng.randint(1, 199)}",
        "zip": zip_code,
        "city": city_entry["city"],
    }


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


def _ean13_check_digit(digits12: str) -> str:
    """Standard EAN-13 check digit: positions 1,3,5.. (1-indexed, odd) weight 1,
    positions 2,4,6.. weight 3, check = (10 - sum % 10) % 10."""
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits12))
    return str((10 - total % 10) % 10)


def _random_ean13() -> str:
    digits12 = "".join(str(random.randint(0, 9)) for _ in range(12))
    return digits12 + _ean13_check_digit(digits12)


def assign_barcodes(vals_list: List[dict], existing_barcodes: Set[str], max_attempts: int = 20) -> None:
    """Mutates each dict in vals_list in place, adding a unique 'barcode' (EAN-13).

    existing_barcodes should be pre-seeded by the caller with barcodes already
    present in the target Odoo DB (R16: collisions must be avoided across runs,
    not just within one) — this function also adds every barcode it assigns, so
    within-run duplicates are impossible too. Pattern 1: an empty vals_list is a
    no-op.
    """
    for vals in vals_list:
        for _ in range(max_attempts):
            candidate = _random_ean13()
            if candidate not in existing_barcodes:
                existing_barcodes.add(candidate)
                vals["barcode"] = candidate
                break
        else:
            logger.warning("EAN-13-Kollisionslimit erreicht, überspringe Barcode für ein Produkt.")


def assign_tracking(vals_list: List[dict], lot_pct: int, serial_pct: int) -> None:
    """Mutates each dict in vals_list in place, adding a 'tracking' key
    ('lot'/'serial'/'none') to storables (S13/R13). Wirkt nur auf Einträge
    mit vals['is_storable'] is True — die _PRODUCT_TYPE_MAP-Kennzeichnung,
    die build_products beim Vals-Aufbau setzt (siehe oben). Services/
    Consumables bleiben unangetastet (kein 'tracking'-Key -> Odoo-Default
    'none').

    Clamps its own percentages rather than trusting the caller (lot_pct +
    serial_pct capped at 100) — this is a public function Pattern-7 tests
    call directly, same defensive posture as assign_barcodes' own
    max_attempts guard above. Pattern 1: an empty vals_list is a no-op.
    """
    lot_pct = max(0, min(100, lot_pct))
    serial_pct = max(0, min(100 - lot_pct, serial_pct))
    for vals in vals_list:
        if vals.get('is_storable') is not True:
            continue
        roll = random.uniform(0, 100)
        if roll < lot_pct:
            vals['tracking'] = 'lot'
        elif roll < lot_pct + serial_pct:
            vals['tracking'] = 'serial'


def assign_quality_state(vals_list: List[dict], fail_pct: int) -> None:
    """Mutates each dict in vals_list in place, adding a 'quality_state' key
    ('fail'/'pass') to quality.check vals (S14/R18). Every entry is touched
    unconditionally, unlike assign_tracking's is_storable filter — there is
    no analogous "not applicable" subset among quality.check vals.

    Clamps fail_pct rather than trusting the caller, same defensive posture
    as assign_tracking. Pattern 1: an empty vals_list is a no-op.
    """
    fail_pct = max(0, min(100, fail_pct))
    for vals in vals_list:
        vals['quality_state'] = 'fail' if random.uniform(0, 100) < fail_pct else 'pass'
