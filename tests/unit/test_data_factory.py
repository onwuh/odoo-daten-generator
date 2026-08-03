"""Unit tests for data_factory.py / static_data.py / text_utils.py (no Odoo, no LLM)."""
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import data_factory
import static_data
import text_utils

_COMPANY_FIELDS = {
    "name", "street", "zip", "city", "country_code", "email", "phone", "website", "is_company",
}


def run():
    """Returns (all_passed, [(label, ok, detail), ...])"""
    results = []

    # ------------------------------------------------------------------
    # text_utils
    # ------------------------------------------------------------------

    try:
        cases = {
            "Hans Müller": "hans.mueller@example.com",
            "Björn Groß": "bjoern.gross@example.com",
            "Bewerber 3": "bewerber.3@example.com",
        }
        for name, expected in cases.items():
            got = text_utils.email_from_name(name)
            assert got == expected, f"{name!r} -> {got!r}, expected {expected!r}"
        results.append(("text_utils: email_from_name umlaut cases", True, ""))
    except Exception as e:
        results.append(("text_utils: email_from_name umlaut cases", False, str(e)))

    # ------------------------------------------------------------------
    # build_company
    # ------------------------------------------------------------------

    try:
        vals = data_factory.build_company("Testfirma GmbH")
        assert set(vals.keys()) == _COMPANY_FIELDS, f"Got keys: {set(vals.keys())}"
        cc = vals["country_code"]
        entry = next(c for c in static_data.CITIES[cc] if c["city"] == vals["city"])
        assert len(vals["zip"]) == static_data.ZIP_LEN[cc], f"zip {vals['zip']!r} wrong length for {cc}"
        assert entry["zip_min"] <= int(vals["zip"]) <= entry["zip_max"], \
            f"zip {vals['zip']} out of range for {vals['city']}"
        assert vals["is_company"] is True
        results.append(("build_company: field whitelist + zip matches city", True, ""))
    except Exception as e:
        results.append(("build_company: field whitelist + zip matches city", False, str(e)))

    # ------------------------------------------------------------------
    # build_contacts
    # ------------------------------------------------------------------

    try:
        contacts = data_factory.build_contacts(2, 1, 3, person_names=["Anna Schmidt", "Lukas Weber"])
        assert len(contacts) == 6, f"Expected 6 contacts, got {len(contacts)}"
        delivery = [c for c in contacts if c["type"] == "delivery"]
        invoice = [c for c in contacts if c["type"] == "invoice"]
        contact = [c for c in contacts if c["type"] == "contact"]
        assert len(delivery) == 2 and len(invoice) == 1 and len(contact) == 3
        assert all({"street", "zip", "city", "country_code"} <= set(c.keys()) for c in delivery)
        assert all({"street", "zip", "city", "country_code"} <= set(c.keys()) for c in invoice)
        assert all(c["name"] == "Rechnungsadresse" for c in invoice)
        assert all({"name", "email"} <= set(c.keys()) for c in contact)
        results.append(("build_contacts: count breakdown + field shapes", True, ""))
    except Exception as e:
        results.append(("build_contacts: count breakdown + field shapes", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 1 — empty person_names pool, no crash
    # ------------------------------------------------------------------

    try:
        contacts = data_factory.build_contacts(0, 0, 2, person_names=[])
        assert len(contacts) == 2
        assert contacts[0]["name"] == "Kontakt 1" and contacts[1]["name"] == "Kontakt 2"
        results.append(("Pattern 1: build_contacts empty pool -> synthetic names, no crash", True, ""))
    except Exception as e:
        results.append(("Pattern 1: build_contacts empty pool -> synthetic names, no crash", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 7 — distribution over 100 samples
    # ------------------------------------------------------------------

    try:
        random.seed(42)
        prices = [data_factory.price_for_product() for _ in range(100)]
        assert all(std < lp for lp, std in prices), "standard_price must always be < list_price"
        assert all(0.4 * lp - 0.01 <= std <= 0.8 * lp + 0.01 for lp, std in prices), "ratio out of [0.4, 0.8]"
        list_prices = [lp for lp, _ in prices]
        assert max(list_prices) - min(list_prices) > 50, "list_price spread looks degenerate"
        results.append(("Pattern 7: price_for_product distribution (n=100)", True, ""))
    except Exception as e:
        results.append(("Pattern 7: price_for_product distribution (n=100)", False, str(e)))

    try:
        random.seed(42)
        cities = {data_factory.build_company("X")["city"] for _ in range(100)}
        assert len(cities) >= 5, f"Expected >=5 distinct cities, got {len(cities)}: {cities}"
        results.append(("Pattern 7: build_company city distribution (n=100)", True, f"{len(cities)} distinct"))
    except Exception as e:
        results.append(("Pattern 7: build_company city distribution (n=100)", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
