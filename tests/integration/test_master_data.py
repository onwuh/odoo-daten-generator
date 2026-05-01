import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import odoo_actions


def run(client, ctx):
    """
    Populates: ctx.company_ids, ctx.product_ids, ctx.partner_ids
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
        ctx.company_ids.append(partner_id)
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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
