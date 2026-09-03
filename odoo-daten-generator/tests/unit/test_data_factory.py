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

    # ------------------------------------------------------------------
    # R16 — assign_barcodes: EAN-13 checksum validity + collision dedup
    # ------------------------------------------------------------------

    def _ean13_valid(code: str) -> bool:
        if len(code) != 13 or not code.isdigit():
            return False
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(code[:12]))
        return str((10 - total % 10) % 10) == code[12]

    try:
        random.seed(1)
        vals_list = [{"name": f"P{i}"} for i in range(200)]
        existing = set()
        data_factory.assign_barcodes(vals_list, existing)
        codes = [v["barcode"] for v in vals_list]
        assert all(_ean13_valid(c) for c in codes), "invalid EAN-13 checksum found"
        assert len(set(codes)) == len(codes), "duplicate barcodes within one run"
        results.append(("R16: assign_barcodes produces valid, unique EAN-13 (n=200)", True, ""))
    except Exception as e:
        results.append(("R16: assign_barcodes produces valid, unique EAN-13 (n=200)", False, str(e)))

    try:
        random.seed(2)
        vals_list = [{"name": "P1"}]
        seeded_barcode = "1234567890128"  # valid EAN-13 checksum
        pre_existing = {seeded_barcode}
        data_factory.assign_barcodes(vals_list, pre_existing)
        assigned = vals_list[0]["barcode"]
        assert assigned != seeded_barcode, "assigned the pre-seeded barcode itself"
        assert pre_existing == {seeded_barcode, assigned}, "pre-seeded set not extended correctly"
        results.append(("R16: assign_barcodes respects pre-seeded existing_barcodes", True, ""))
    except Exception as e:
        results.append(("R16: assign_barcodes respects pre-seeded existing_barcodes", False, str(e)))

    try:
        # Pattern 1: empty vals_list is a no-op, no crash.
        existing = set()
        data_factory.assign_barcodes([], existing)
        assert existing == set()
        results.append(("Pattern 1: assign_barcodes empty vals_list, no crash", True, ""))
    except Exception as e:
        results.append(("Pattern 1: assign_barcodes empty vals_list, no crash", False, str(e)))

    # ------------------------------------------------------------------
    # S13/R13 — assign_tracking: distribution, storable-only, clamp
    # ------------------------------------------------------------------
    try:
        # Pattern 7: none/lot/serial all appear over enough samples.
        random.seed(3)
        vals_list = [{"name": f"S{i}", "is_storable": True} for i in range(200)]
        data_factory.assign_tracking(vals_list, lot_pct=30, serial_pct=30)
        trackings = [v.get("tracking", "none") for v in vals_list]
        assert any(t == "none" for t in trackings), "no 'none' sample"
        assert any(t == "lot" for t in trackings), "no 'lot' sample"
        assert any(t == "serial" for t in trackings), "no 'serial' sample"
        results.append(("Pattern 7: assign_tracking none/lot/serial distribution (n=200)", True, ""))
    except Exception as e:
        results.append(("Pattern 7: assign_tracking none/lot/serial distribution (n=200)", False, str(e)))

    try:
        # Only is_storable=True entries are touched — services (no key) and
        # consumables (is_storable=False) stay untouched (Odoo default 'none').
        random.seed(4)
        vals_list = [
            {"name": "Service"},  # no is_storable key at all
            {"name": "Consumable", "is_storable": False},
            {"name": "Storable", "is_storable": True},
        ]
        data_factory.assign_tracking(vals_list, lot_pct=100, serial_pct=0)
        assert "tracking" not in vals_list[0], vals_list[0]
        assert "tracking" not in vals_list[1], vals_list[1]
        assert vals_list[2]["tracking"] == "lot", vals_list[2]
        results.append(("assign_tracking only touches is_storable=True entries", True, ""))
    except Exception as e:
        results.append(("assign_tracking only touches is_storable=True entries", False, str(e)))

    try:
        # S8: clamps lot_pct+serial_pct at 100 internally rather than trusting
        # the caller — lot_pct=80, serial_pct=80 must never sum past 100.
        random.seed(5)
        vals_list = [{"name": f"S{i}", "is_storable": True} for i in range(300)]
        data_factory.assign_tracking(vals_list, lot_pct=80, serial_pct=80)
        assert all(v.get("tracking", "none") != "none" for v in vals_list), \
            "clamp left some untracked when lot+serial >= 100"
        results.append(("S8: assign_tracking clamps lot_pct+serial_pct at 100", True, ""))
    except Exception as e:
        results.append(("S8: assign_tracking clamps lot_pct+serial_pct at 100", False, str(e)))

    try:
        # Pattern 1: empty vals_list is a no-op, no crash.
        data_factory.assign_tracking([], lot_pct=50, serial_pct=50)
        results.append(("Pattern 1: assign_tracking empty vals_list, no crash", True, ""))
    except Exception as e:
        results.append(("Pattern 1: assign_tracking empty vals_list, no crash", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
