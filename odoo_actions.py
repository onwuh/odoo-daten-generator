import random

def create_customer(client, customer_data):
    """Creates a new customer and returns its ID."""
    print(f"-> Creating Customer/Contact: {customer_data.get('name')}...")
    customer_id = client.create('res.partner', customer_data)
    print(f"   ID: {customer_id}")
    return customer_id

def create_product(client, product_data):
    """Creates a new product and returns its ID."""
    print(f"-> Creating Product: {product_data.get('name')}...")
    product_id = client.create('product.product', product_data)
    print(f"   ID: {product_id}")
    return product_id

def create_sale_order(client, order_data):
    """Creates a new sale order and returns its ID."""
    print("-> Creating Sale Order...")
    order_id = client.create('sale.order', order_data)
    print(f"   ID: {order_id}")
    return order_id

def get_country_id(client, country_code):
    """Finds the Odoo database ID for a given country code (e.g., 'DE')."""
    country_info = client.search_read(
        'res.country',
        [["code", "=", country_code.upper()]],
        fields=["id"],
        limit=1,
    )
    if country_info:
        return country_info[0]['id']
    return False # Return False if country not found

def get_installed_modules(client, wanted_modules):
    """Returns a set of installed module technical names from wanted_modules."""
    records = client.search_read(
        'ir.module.module',
        [["name", "in", wanted_modules], ["state", "=", "installed"]],
        fields=["name", "state"],
        limit=0,
    )
    return set(r["name"] for r in records)

def get_main_company_language(client):
    """Get the language of the main company (usually company_id=1), or API user's language as fallback."""
    try:
        # Try to get company with id=1 first (main company) and get its partner
        companies = client.search_read('res.company', [["id", "=", 1]], fields=["partner_id"], limit=1)
        if companies:
            partner_id = companies[0].get("partner_id")
            if partner_id and isinstance(partner_id, (list, tuple)):
                partner_id = partner_id[0]
            if partner_id:
                partners = client.search_read('res.partner', [["id", "=", partner_id]], fields=["lang"], limit=1)
                if partners and partners[0].get("lang"):
                    return partners[0]["lang"]
        
        # Fallback: get first company's partner
        companies = client.search_read('res.company', [], fields=["partner_id"], limit=1)
        if companies:
            partner_id = companies[0].get("partner_id")
            if partner_id and isinstance(partner_id, (list, tuple)):
                partner_id = partner_id[0]
            if partner_id:
                partners = client.search_read('res.partner', [["id", "=", partner_id]], fields=["lang"], limit=1)
                if partners and partners[0].get("lang"):
                    return partners[0]["lang"]
        
        # Fallback: get API user's language (try admin first, then any user with language set)
        users = client.search_read('res.users', [["id", "=", 2], ["lang", "!=", False]], fields=["lang"], limit=1)
        if users and users[0].get("lang"):
            return users[0]["lang"]
        # Try any active user with language set
        users = client.search_read('res.users', [["active", "=", True], ["lang", "!=", False]], fields=["lang"], limit=1)
        if users and users[0].get("lang"):
            return users[0]["lang"]
        # Last resort: any user with language
        users = client.search_read('res.users', [["lang", "!=", False]], fields=["lang"], limit=1)
        if users and users[0].get("lang"):
            return users[0]["lang"]
    except Exception as e:
        print(f"-> Warning: Could not determine company language: {e}")
    # Default fallback
    return "de_DE"

def create_opportunity(client, partner_id, name):
    print(f"-> Creating Opportunity for partner {partner_id}: {name}")
    values = {"type": "opportunity", "partner_id": partner_id, "name": name}
    return client.create('crm.lead', values)

def get_crm_stages(client, exclude_won=True):
    """Get CRM stages, optionally excluding 'won' stage."""
    # Get all stages first
    stages = client.search_read('crm.stage', [], fields=["id", "name"], limit=0)
    if exclude_won:
        # Filter out "Won" stage
        stages = [s for s in stages if s.get("name", "").lower() != "won"]
    return [s["id"] for s in stages]

def update_opportunity_stage(client, opportunity_id, stage_id):
    """Update opportunity stage."""
    return client.write('crm.lead', [opportunity_id], {"stage_id": stage_id})

def create_project_stage(client, project_id, name, sequence=10):
    """Create a stage for a project. In Odoo 19, stages are typically global."""
    # Create global stage (stages are shared across projects in Odoo 19)
    values = {"name": name, "sequence": sequence}
    return client.create('project.task.type', values)

def get_project_stages(client, project_id):
    """Get all stages for a project. In Odoo 19, stages are typically global."""
    # Get all stages (they're global in Odoo 19)
    stages = client.search_read('project.task.type', [], fields=["id", "name", "sequence"], limit=0)
    return sorted(stages, key=lambda x: x.get("sequence", 0))

def update_task_stage(client, task_id, stage_id):
    """Update task stage."""
    return client.write('project.task', [task_id], {"stage_id": stage_id})

def link_order_to_opportunity(client, order_id, opportunity_id):
    print(f"-> Linking Order {order_id} to Opportunity {opportunity_id}")
    return client.write('sale.order', [order_id], {"opportunity_id": opportunity_id})

def confirm_sale_orders(client, order_ids):
    # Prefer direct JSON-2 recordset call with ids
    print(f"-> Confirming orders: {order_ids}")
    try:
        client.call_method('sale.order', 'action_confirm', ids=order_ids)
        return True
    except Exception:
        # Fallback: confirm sequentially
        for oid in order_ids:
            print(f"-> Confirm order individually: {oid}")
            client.call_method('sale.order', 'action_confirm', ids=[oid])
        return True

def create_customer_invoice(client, partner_id, line_product_ids):
    print(f"-> Creating Invoice for partner {partner_id}")
    lines = []
    for pid in line_product_ids:
        lines.append((0, 0, {"product_id": pid, "quantity": 1}))
    values = {"move_type": "out_invoice", "partner_id": partner_id, "invoice_line_ids": lines}
    return client.create('account.move', values)

def post_invoices(client, move_ids):
    print(f"-> Posting invoices: {move_ids}")
    try:
        client.call_method('account.move', 'action_post', move_ids)
        return True
    except Exception:
        for mid in move_ids:
            client.call_method('account.move', 'action_post', [mid])
        return True

def create_employee(client, name):
    print(f"-> Creating Employee: {name}")
    return client.create('hr.employee', {"name": name})

def create_project(client, name):
    print(f"-> Creating Project: {name}")
    return client.create('project.project', {"name": name})

def create_timesheet(client, employee_id, project_id, hours, description, date_str):
    print(f"-> Creating Timesheet: {hours}h by emp {employee_id} on project {project_id}")
    values = {
        "name": description,
        "employee_id": employee_id,
        "project_id": project_id,
        "unit_amount": hours,
        "date": date_str,
    }
    return client.create('account.analytic.line', values)

def create_task(client, project_id, name, description=None):
    print(f"-> Creating Task in project {project_id}: {name}")
    values = {"name": name, "project_id": project_id}
    if description:
        values["description"] = description
    return client.create('project.task', values)

def create_invoices_from_orders(client, order_ids):
    print(f"-> Creating invoices from orders: {order_ids}")
    if not order_ids:
        return []
    created_invoice_ids = []
    # Read sale orders to get their data
    orders = client.search_read('sale.order', [["id", "in", order_ids]], fields=["partner_id", "order_line", "name"], limit=0)
    for order in orders:
        try:
            # Create invoice directly from order data
            invoice_lines = []
            line_ids = order.get("order_line", [])
            if isinstance(line_ids, list) and line_ids:
                line_data_list = client.search_read('sale.order.line', [["id", "in", line_ids]], fields=["product_id", "product_uom_qty", "price_unit"], limit=0)
                for ld in line_data_list:
                    # Handle partner_id/product_id as tuple (id, name) or just id
                    prod_id = ld.get("product_id")
                    if isinstance(prod_id, (list, tuple)) and len(prod_id) > 0:
                        prod_id = prod_id[0]
                    elif not prod_id:
                        continue
                    invoice_lines.append((0, 0, {
                        "product_id": prod_id,
                        "quantity": ld.get("product_uom_qty", 1),
                        "price_unit": ld.get("price_unit", 0),
                    }))
            if invoice_lines:
                partner_id = order.get("partner_id")
                if isinstance(partner_id, (list, tuple)) and len(partner_id) > 0:
                    partner_id = partner_id[0]
                invoice_vals = {
                    "move_type": "out_invoice",
                    "partner_id": partner_id,
                    "invoice_line_ids": invoice_lines,
                    "invoice_origin": order.get("name", ""),
                }
                inv_id = client.create('account.move', invoice_vals)
                created_invoice_ids.append(inv_id)
        except Exception as e:
            print(f"-> Failed to create invoice for order {order.get('id')}: {e}")
            continue
    if created_invoice_ids:
        post_invoices(client, created_invoice_ids)
    return created_invoice_ids

def create_vendor_bill(client, supplier_id, product_ids, description_prefix="Vendor Bill"):
    print(f"-> Creating Vendor Bill for supplier {supplier_id}")
    lines = []
    # Read product data to get prices
    products = client.search_read('product.product', [["id", "in", product_ids]], fields=["standard_price", "list_price"], limit=0)
    product_price_map = {}
    for p in products:
        prod_id = p.get("id")
        # Use standard_price (cost) for vendor bills, fallback to list_price * 0.6
        cost = p.get("standard_price", 0)
        if not cost or cost == 0:
            cost = (p.get("list_price", 0) or 50) * 0.6
        product_price_map[prod_id] = cost
    
    for pid in product_ids:
        # Get price from map, with fallback
        price = product_price_map.get(pid, random.uniform(10, 100))
        # Vary quantity (1-5 units)
        qty = random.randint(1, 5)
        lines.append((0, 0, {
            "product_id": pid,
            "quantity": qty,
            "price_unit": round(price, 2)
        }))
    values = {"move_type": "in_invoice", "partner_id": supplier_id, "invoice_line_ids": lines, "ref": description_prefix}
    bill_id = client.create('account.move', values)
    post_invoices(client, [bill_id])
    return bill_id

def create_bom(client, product_tmpl_id, product_id=None, quantity=1.0, code=None, bom_type="normal"):
    """Create a manufacturing BOM for a given product template (and optional variant)."""
    values = {
        "product_tmpl_id": product_tmpl_id,
        "type": bom_type,
        "product_qty": quantity,
    }
    if product_id:
        values["product_id"] = product_id
    if code:
        values["code"] = code
    print(f"-> Creating BOM for template {product_tmpl_id} (variant: {product_id})")
    return client.create('mrp.bom', values)

def create_bom_line(client, bom_id, product_id, quantity=1.0):
    """Create a BOM line referencing a component product."""
    values = {
        "bom_id": bom_id,
        "product_id": product_id,
        "product_qty": quantity,
    }
    print(f"->   Adding BOM line: product {product_id} x{quantity}")
    return client.create('mrp.bom.line', values)

def get_product_template_id(client, product_id):
    """Return the product template id for a given product variant."""
    record = client.search_read(
        'product.product',
        [["id", "=", product_id]],
        fields=["product_tmpl_id"],
        limit=1
    )
    if record:
        tmpl = record[0].get("product_tmpl_id")
        if isinstance(tmpl, (list, tuple)) and tmpl:
            return tmpl[0]
        return tmpl
    return None
