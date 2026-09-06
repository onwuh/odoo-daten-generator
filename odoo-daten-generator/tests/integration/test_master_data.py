import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import odoo_actions
from config import DemoCriteria, ModuleSelections, RunContext
from modules import master_data


def _unwrap(val):
    return val[0] if isinstance(val, (list, tuple)) else val


def _make_rctx(num_companies=1):
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=num_companies,
        num_delivery_contacts=1, num_invoice_contacts=1, num_other_contacts=1,
        num_services=1, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit, module_selections=ModuleSelections(), industry="IT",
        language_name="German", language_code="de_DE", gemini_model_name="test",
    )


def run(client, ctx):
    """
    Populates: ctx.partner_company_ids, ctx.product_ids, ctx.partner_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    # Step 1 — Company/Partner (res.partner as company)
    try:
        partner_id = client.create('res.partner', {
            "name": "Integration Test GmbH",
            "is_company": True,
        })
        assert isinstance(partner_id, int) and partner_id > 0
        ctx.partner_company_ids.append(partner_id)
        results.append(("master_data: create company partner", True, partner_id))
    except Exception as e:
        results.append(("master_data: create company partner", False, str(e)))

    # Step 2 — Product
    try:
        product_id = odoo_actions.create_product(client, {
            "name": "Integration Test Produkt",
            "type": "consu",
            "list_price": 99.99,
        })
        assert isinstance(product_id, int) and product_id > 0
        rec = client.search_read(
            'product.product',
            [["id", "=", product_id]],
            fields=["list_price"],
            limit=1,
        )
        assert rec and abs(rec[0]["list_price"] - 99.99) < 0.01
        ctx.product_ids.append(product_id)
        results.append(("master_data: create product + read-back list_price", True, product_id))
    except Exception as e:
        results.append(("master_data: create product + read-back list_price", False, str(e)))

    # Step 3 — Customer contact
    try:
        customer_id = odoo_actions.create_customer(client, {
            "name": "Max Mustermann Test",
            "email": "test@integration.example",
            "is_company": False,
        })
        assert isinstance(customer_id, int) and customer_id > 0
        rec = client.search_read(
            'res.partner',
            [["id", "=", customer_id]],
            fields=["email"],
            limit=1,
        )
        assert rec and rec[0]["email"] == "test@integration.example"
        ctx.partner_ids.append(customer_id)
        results.append(("master_data: create customer + read-back email", True, customer_id))
    except Exception as e:
        results.append(("master_data: create customer + read-back email", False, str(e)))

    # Step 4 — A1: create_master_data end-to-end (data_factory assembly), LLM-independent.
    # gemini=None proves company/contact structure no longer needs the LLM call to succeed.
    try:
        rctx = _make_rctx()
        rctx.name_banks = {
            "company_names": ["A1 Testfirma GmbH"],
            "employee_names": ["Erika A1 Musterfrau"],
        }
        atoms = {"product_names": {"services": ["A1 Testservice"]}, "product_descriptions": {}}
        master_data.create_master_data(client, None, rctx, atoms)
        assert len(rctx.partner_company_ids) == 1, f"expected 1 company, got {len(rctx.partner_company_ids)}"
        assert len(rctx.product_ids) == 1, f"expected 1 product, got {len(rctx.product_ids)}"

        company_id = rctx.partner_company_ids[0]
        rec = client.search_read(
            'res.partner', [["id", "=", company_id]],
            fields=["street", "zip", "city", "country_id"], limit=1,
        )
        assert rec and rec[0]["street"] and rec[0]["zip"] and rec[0]["city"], "company missing address fields"
        assert _unwrap(rec[0]["country_id"]), "company missing country_id"

        contacts = client.search_read(
            'res.partner', [["parent_id", "=", company_id]],
            fields=["name", "type", "street", "email"], limit=0,
        )
        assert len(contacts) == 3, f"expected 3 contacts, got {len(contacts)}"
        by_type = {c["type"]: c for c in contacts}
        assert by_type["delivery"]["street"], "delivery contact missing street"
        assert by_type["invoice"]["name"] == "Rechnungsadresse"
        assert by_type["contact"]["email"], "person contact missing email"

        results.append(("master_data: create_master_data end-to-end (A1), read-back", True, company_id))
    except Exception as e:
        results.append(("master_data: create_master_data end-to-end (A1), read-back", False, str(e)))

    # Step 5 — A1 Pattern 2: empty atoms + empty name_banks -> full fallback chain, no crash
    try:
        rctx = _make_rctx()
        rctx.name_banks = {}
        master_data.create_master_data(client, None, rctx, {})
        assert len(rctx.partner_company_ids) == 1, f"expected 1 fallback company, got {len(rctx.partner_company_ids)}"
        assert len(rctx.product_ids) == 1, f"expected 1 fallback product, got {len(rctx.product_ids)}"
        results.append(("master_data: Pattern 2 — empty atoms/name_banks -> fallback chain, no crash", True, ""))
    except Exception as e:
        results.append(("master_data: Pattern 2 — empty atoms/name_banks -> fallback chain, no crash", False, str(e)))

    # Step 6 — R8: service products get service_tracking/invoice_policy/
    # service_type tagged when project+hr_timesheet are installed, so Odoo's
    # own automation creates a Project+Task on order confirmation later
    # (verified in modules/sale.py's integration test).
    try:
        rctx = _make_rctx()
        rctx.installed_modules = {"project", "hr_timesheet"}
        atoms = {"product_names": {"services": ["R8 Testservice"]}, "product_descriptions": {}}
        master_data._create_products(client, atoms, rctx)
        assert len(rctx.product_ids) == 1, f"expected 1 product, got {len(rctx.product_ids)}"
        rec = client.search_read(
            'product.product', [["id", "=", rctx.product_ids[0]]],
            fields=["service_tracking", "invoice_policy", "service_type"], limit=1,
        )
        assert rec, "product not found after create"
        assert rec[0]["service_tracking"] == "task_in_project", rec[0]
        assert rec[0]["invoice_policy"] == "delivery", rec[0]
        assert rec[0]["service_type"] == "timesheet", rec[0]
        results.append(("master_data: R8 — service product tagged for native automation", True, rctx.product_ids[0]))
    except Exception as e:
        results.append(("master_data: R8 — service product tagged for native automation", False, str(e)))

    # Step 7 — R16: products created via _create_products get a unique,
    # valid EAN-13 barcode, deduped against barcodes already on this DB.
    try:
        rctx = _make_rctx()
        atoms = {"product_names": {"services": ["R16 Testservice A", "R16 Testservice B"]},
                 "product_descriptions": {}}
        master_data._create_products(client, atoms, rctx)
        assert len(rctx.product_ids) == 2, f"expected 2 products, got {len(rctx.product_ids)}"
        rec = client.search_read(
            'product.product', [["id", "in", rctx.product_ids]],
            fields=["barcode"], limit=0,
        )
        codes = [r["barcode"] for r in rec]
        assert all(codes), f"missing barcode on at least one product: {rec}"
        assert len(set(codes)) == len(codes), f"duplicate barcodes assigned: {codes}"
        for code in codes:
            assert len(code) == 13 and code.isdigit(), f"not a 13-digit code: {code!r}"
            total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(code[:12]))
            assert str((10 - total % 10) % 10) == code[12], f"bad EAN-13 checksum: {code!r}"
        results.append(("master_data: R16 — products get unique, valid EAN-13 barcodes", True, codes))
    except Exception as e:
        results.append(("master_data: R16 — products get unique, valid EAN-13 barcodes", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
