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

def get_main_company_name(client):
    """Get the name of the main company from the API user's company."""
    try:
        # Try to get company with id=1 first (main company)
        companies = client.search_read('res.company', [["id", "=", 1]], fields=["name", "partner_id"], limit=1)
        if companies:
            company_name = companies[0].get("name")
            if company_name:
                return company_name
            # If no name, try to get from partner
            partner_id = companies[0].get("partner_id")
            if partner_id and isinstance(partner_id, (list, tuple)):
                partner_id = partner_id[0]
            if partner_id:
                partners = client.search_read('res.partner', [["id", "=", partner_id]], fields=["name"], limit=1)
                if partners and partners[0].get("name"):
                    return partners[0]["name"]
        
        # Fallback: get first company's name
        companies = client.search_read('res.company', [], fields=["name", "partner_id"], limit=1)
        if companies:
            company_name = companies[0].get("name")
            if company_name:
                return company_name
            # Try partner
            partner_id = companies[0].get("partner_id")
            if partner_id and isinstance(partner_id, (list, tuple)):
                partner_id = partner_id[0]
            if partner_id:
                partners = client.search_read('res.partner', [["id", "=", partner_id]], fields=["name"], limit=1)
                if partners and partners[0].get("name"):
                    return partners[0]["name"]
    except Exception as e:
        print(f"-> Warning: Could not determine company name: {e}")
    return None

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

def get_or_create_bank_journal(client):
    """Get or create a bank journal for bank transactions."""
    # Try to find an existing bank journal
    journals = client.search_read(
        'account.journal',
        [["type", "=", "bank"]],
        fields=["id", "name"],
        limit=1
    )
    if journals:
        journal_id = journals[0].get("id")
        print(f"-> Using existing bank journal: {journals[0].get('name', 'Bank')} (ID: {journal_id})")
        return journal_id
    
    # Create a new bank journal if none exists
    print("-> Creating new bank journal...")
    journal_id = client.create('account.journal', {
        "name": "Bank",
        "type": "bank",
        "code": "BNK"
    })
    print(f"   Created bank journal ID: {journal_id}")
    return journal_id

def create_bank_transactions_for_all_invoices(client):
    """Create bank transactions (account.bank.statement.line) for all vendor bills and customer invoices.
    
    For vendor bills (in_invoice): Uses "name" field as label
    For customer invoices (out_invoice): Uses "ref" field as label
    20% of customer invoices will have deviations in label or amount.
    
    Args:
        client: OdooJson2Client instance
    
    Returns:
        List of created bank statement line IDs
    """
    print(f"\n--- ACCOUNTING: Erstelle Banktransaktionen für alle Rechnungen ---")
    
    # Get or create bank journal
    journal_id = get_or_create_bank_journal(client)
    
    # API query for vendor bills (in_invoice) - use "name" as label
    vendor_bills = client.search_read(
        'account.move',
        [["move_type", "=", "in_invoice"], ["state", "=", "posted"]],
        fields=["id", "amount_total", "name", "partner_id"],
        limit=0
    )
    
    # API query for customer invoices (out_invoice) - use "ref" as label
    customer_invoices = client.search_read(
        'account.move',
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
        fields=["id", "amount_total", "ref", "partner_id"],
        limit=0
    )
    
    total_invoices = len(vendor_bills) + len(customer_invoices)
    if total_invoices == 0:
        print("-> Keine gebuchten Rechnungen gefunden")
        return []
    
    print(f"-> Gefunden: {len(vendor_bills)} Eingangsrechnungen, {len(customer_invoices)} Ausgangsrechnungen")
    
    # Get or create a bank statement for these transactions
    statements = client.search_read(
        'account.bank.statement',
        [["journal_id", "=", journal_id]],
        fields=["id"],
        limit=1
    )
    
    statement_id = None
    if statements:
        statement_id = statements[0].get("id")
        print(f"-> Verwende vorhandenen Bank Statement: {statement_id}")
    else:
        # Create a new bank statement
        print("-> Erstelle neuen Bank Statement...")
        statement_id = client.create('account.bank.statement', {
            "journal_id": journal_id,
            "name": f"Bank Statement {random.randint(1000, 9999)}"
        })
        print(f"   Erstellt: Bank Statement ID {statement_id}")
    
    # Prepare all transactions in a list
    transactions_to_create = []
    
    # Process vendor bills (in_invoice) - all exact matches
    for bill in vendor_bills:
        bill_id = bill.get("id")
        amount_total = bill.get("amount_total", 0)
        label = bill.get("name", f"Bill {bill_id}")  # Use "name" for vendor bills
        partner_id = bill.get("partner_id")
        if isinstance(partner_id, (list, tuple)) and len(partner_id) > 0:
            partner_id = partner_id[0]
        
        transactions_to_create.append({
            "type": "in_invoice",
            "id": bill_id,
            "amount": -amount_total,  # Negative for outgoing payments
            "label": label,
            "partner_id": partner_id,
            "has_deviation": False  # No deviations for vendor bills
        })
    
    # Process customer invoices (out_invoice) - 20% with deviations
    num_out_invoices = len(customer_invoices)
    num_with_deviation = max(1, int(num_out_invoices * 0.2))  # 20% with deviation
    
    # Randomly select which customer invoices will have deviations
    deviation_indices = set(random.sample(range(num_out_invoices), num_with_deviation)) if num_out_invoices > 0 else set()
    
    for idx, invoice in enumerate(customer_invoices):
        invoice_id = invoice.get("id")
        amount_total = invoice.get("amount_total", 0)
        ref = invoice.get("ref") or f"Invoice {invoice_id}"  # Use "ref" for customer invoices
        partner_id = invoice.get("partner_id")
        if isinstance(partner_id, (list, tuple)) and len(partner_id) > 0:
            partner_id = partner_id[0]
        
        has_deviation = idx in deviation_indices
        
        if has_deviation:
            # 20%: Create deviation (either in amount or label)
            deviation_type = random.choice(["amount", "label"])
            
            if deviation_type == "amount":
                # Deviation in amount: vary by ±10-30%
                deviation_factor = random.uniform(0.7, 1.3)
                transaction_amount = round(amount_total * deviation_factor, 2)
                transaction_label = ref  # Label stays correct
            else:
                # Deviation in label: use different text
                transaction_amount = amount_total  # Amount stays correct
                transaction_label = f"{ref} (Abweichung)"  # Label differs
        else:
            # 80%: Match exactly
            transaction_amount = amount_total
            transaction_label = ref
        
        transactions_to_create.append({
            "type": "out_invoice",
            "id": invoice_id,
            "amount": transaction_amount,  # Positive for incoming payments
            "label": transaction_label,
            "partner_id": partner_id,
            "has_deviation": has_deviation,
            "original_amount": amount_total if has_deviation else None
        })
    
    # Shuffle the list to create transactions in random order
    random.shuffle(transactions_to_create)
    
    # Create bank statement lines
    created_line_ids = []
    num_exact = 0
    num_with_dev = 0
    
    for trans in transactions_to_create:
        line_values = {
            "statement_id": statement_id,
            "journal_id": journal_id,
            "payment_ref": trans["label"],
            "amount": trans["amount"],
            "partner_id": trans["partner_id"],
        }
        
        try:
            line_id = client.create('account.bank.statement.line', line_values)
            created_line_ids.append(line_id)
            
            if trans["has_deviation"]:
                num_with_dev += 1
                if trans.get("original_amount"):
                    print(f"-> Banktransaktion für Rechnung {trans['id']}: Abweichung (Betrag: {trans['amount']} vs {trans['original_amount']}, Label: {trans['label']})")
                else:
                    print(f"-> Banktransaktion für Rechnung {trans['id']}: Abweichung (Label: {trans['label']})")
            else:
                num_exact += 1
                print(f"-> Banktransaktion für Rechnung {trans['id']}: Exakt (Betrag: {trans['amount']}, Label: {trans['label']})")
        except Exception as e:
            print(f"   ⚠️  Fehler beim Erstellen der Banktransaktion für Rechnung {trans['id']}: {e}")
    
    print(f"✅ {len(created_line_ids)} Banktransaktionen erstellt ({num_exact} exakt, {num_with_dev} mit Abweichung)")
    return created_line_ids

# ==============================================================================
# RECRUITING FUNCTIONS
# ==============================================================================

def get_existing_skill_types(client):
    """Get all existing skill types (hr.skill.type) to avoid duplicates."""
    skill_types = client.search_read(
        'hr.skill.type',
        [],
        fields=["id", "name"],
        limit=0
    )
    return {st.get("name", "").lower(): st.get("id") for st in skill_types}

def create_skill_type(client, name):
    """Create a skill type (hr.skill.type)."""
    print(f"-> Creating skill type: {name}")
    skill_type_id = client.create('hr.skill.type', {"name": name})
    print(f"   Created skill type ID: {skill_type_id}")
    return skill_type_id

def create_skill(client, skill_type_id, name):
    """Create a skill (hr.skill)."""
    print(f"->   Creating skill: {name}")
    skill_id = client.create('hr.skill', {
        "name": name,
        "skill_type_id": skill_type_id
    })
    return skill_id

def create_skill_level(client, skill_type_id, name, level_progress=0):
    """Create a skill level (hr.skill.level)."""
    print(f"->     Creating level: {name}")
    level_id = client.create('hr.skill.level', {
        "name": name,
        "skill_type_id": skill_type_id,
        "level_progress": level_progress
    })
    return level_id

def get_departments(client):
    """Get all existing departments."""
    departments = client.search_read(
        'hr.department',
        [],
        fields=["id", "name"],
        limit=0
    )
    return departments

def get_existing_job_names_per_department(client):
    """Get all existing job names grouped by department."""
    jobs = client.search_read(
        'hr.job',
        [],
        fields=["id", "name", "department_id"],
        limit=0
    )
    dept_job_names = {}
    for job in jobs:
        dept_id = job.get("department_id")
        if isinstance(dept_id, (list, tuple)) and len(dept_id) > 0:
            dept_id = dept_id[0]
        elif dept_id is None:
            continue
        
        if dept_id not in dept_job_names:
            dept_job_names[dept_id] = set()
        job_name = job.get("name", "")
        if job_name:
            dept_job_names[dept_id].add(job_name.lower())
    return dept_job_names

def create_department(client, name):
    """Create a department (hr.department)."""
    print(f"-> Creating department: {name}")
    dept_id = client.create('hr.department', {"name": name})
    print(f"   Created department ID: {dept_id}")
    return dept_id

def get_job_stages(client, job_id=None):
    """Get all recruitment stages. Stages are global in Odoo, not job-specific."""
    stages = client.search_read(
        'hr.recruitment.stage',
        [],
        fields=["id", "name", "sequence"],
        limit=0
    )
    return sorted(stages, key=lambda x: x.get("sequence", 0))

def create_job(client, name, department_id, target=3, description=None, job_skill_ids=None):
    """Create a job (hr.job).
    
    Args:
        job_skill_ids: List of skill IDs to associate with the job (uses job_skill_ids field)
    """
    print(f"-> Creating job: {name}")
    values = {
        "name": name,
        "department_id": department_id,
        "no_of_recruitment": target,
    }
    if description:
        values["description"] = description
    if job_skill_ids:
        # Get skill data to retrieve skill_type_id for each skill
        skills = client.search_read(
            'hr.skill',
            [["id", "in", job_skill_ids]],
            fields=["id", "skill_type_id"],
            limit=0
        )
        # job_skill_ids is a one2many field, use (0, 0, {...}) format
        # Need skill_id, skill_type_id, and skill_level_id
        job_skill_lines = []
        for skill in skills:
            skill_id = skill.get("id")
            skill_type_id = skill.get("skill_type_id")
            if isinstance(skill_type_id, (list, tuple)) and len(skill_type_id) > 0:
                skill_type_id = skill_type_id[0]
            
            if skill_id and skill_type_id:
                # Get a random skill level for this skill type
                skill_levels = client.search_read(
                    'hr.skill.level',
                    [["skill_type_id", "=", skill_type_id]],
                    fields=["id"],
                    limit=0
                )
                skill_level_id = None
                if skill_levels:
                    # Pick a random level (prefer middle to high levels)
                    level = random.choice(skill_levels)
                    skill_level_id = level.get("id")
                
                if skill_level_id:
                    job_skill_lines.append((0, 0, {
                        "skill_id": skill_id,
                        "skill_type_id": skill_type_id,
                        "skill_level_id": skill_level_id
                    }))
                else:
                    # If no level found, still create the line without level
                    job_skill_lines.append((0, 0, {
                        "skill_id": skill_id,
                        "skill_type_id": skill_type_id
                    }))
        if job_skill_lines:
            values["job_skill_ids"] = job_skill_lines
    job_id = client.create('hr.job', values)
    print(f"   Created job ID: {job_id}")
    return job_id

def create_applicant(client, job_id, name, email, phone, skill_ids=None, stage_id=None):
    """Create an applicant (hr.applicant) with skills and skill levels.
    
    Args:
        skill_ids: List of skill IDs to associate with the applicant
    """
    print(f"-> Creating applicant: {name}")
    values = {
        "partner_name": name,  # Use partner_name instead of name
        "email_from": email,
        "partner_phone": phone,
        "job_id": job_id,
    }
    
    if skill_ids:
        # Get skill data to retrieve skill_type_id and assign levels
        skills = client.search_read(
            'hr.skill',
            [["id", "in", skill_ids]],
            fields=["id", "skill_type_id"],
            limit=0
        )
        
        # applicant_skill_ids is a one2many field, use (0, 0, {...}) format
        # Need skill_id, skill_type_id, and skill_level_id
        applicant_skill_lines = []
        for skill in skills:
            skill_id = skill.get("id")
            skill_type_id = skill.get("skill_type_id")
            if isinstance(skill_type_id, (list, tuple)) and len(skill_type_id) > 0:
                skill_type_id = skill_type_id[0]
            
            if skill_id and skill_type_id:
                # Get available skill levels for this skill type
                skill_levels = client.search_read(
                    'hr.skill.level',
                    [["skill_type_id", "=", skill_type_id]],
                    fields=["id", "level_progress"],
                    limit=0
                )
                
                skill_level_id = None
                if skill_levels:
                    # Pick a random level (weighted towards middle/high levels)
                    # Sort by progress and prefer levels in the middle to high range
                    sorted_levels = sorted(skill_levels, key=lambda x: x.get("level_progress", 0))
                    # 70% chance of middle/high level, 30% chance of any level
                    if random.random() < 0.7 and len(sorted_levels) > 2:
                        # Pick from middle 60% to top
                        start_idx = max(0, int(len(sorted_levels) * 0.4))
                        level = random.choice(sorted_levels[start_idx:])
                    else:
                        level = random.choice(skill_levels)
                    skill_level_id = level.get("id")
                
                if skill_level_id:
                    applicant_skill_lines.append((0, 0, {
                        "skill_id": skill_id,
                        "skill_type_id": skill_type_id,
                        "skill_level_id": skill_level_id
                    }))
                else:
                    # If no level found, still create the line without level
                    applicant_skill_lines.append((0, 0, {
                        "skill_id": skill_id,
                        "skill_type_id": skill_type_id
                    }))
        
        if applicant_skill_lines:
            values["applicant_skill_ids"] = applicant_skill_lines
    
    if stage_id:
        values["stage_id"] = stage_id
    applicant_id = client.create('hr.applicant', values)
    print(f"   Created applicant ID: {applicant_id}")
    return applicant_id

# ==============================================================================
# ACTIVITIES FUNCTIONS
# ==============================================================================

def get_activity_types(client):
    """Get all available activity types."""
    activity_types = client.search_read(
        'mail.activity.type',
        [],
        fields=["id", "name", "category"],
        limit=0
    )
    return activity_types

def create_activity(client, res_model, res_id, activity_type_id, summary, date_deadline, user_id=None):
    """Create a mail activity on a record using activity_schedule method.
    
    Args:
        res_model: Model name (e.g., 'crm.lead', 'hr.applicant', 'project.task', 'res.partner')
        res_id: Record ID (must be an integer, not None)
        activity_type_id: Activity type ID
        summary: Activity summary/description
        date_deadline: Deadline date (YYYY-MM-DD format)
        user_id: User ID (optional, defaults to current user)
    """
    # Ensure res_id is a valid integer
    if res_id is None:
        raise ValueError(f"res_id cannot be None for model {res_model}")
    
    # Handle tuple/list format from Odoo (id, name)
    if isinstance(res_id, (list, tuple)) and len(res_id) > 0:
        res_id = res_id[0]
    
    if not isinstance(res_id, int):
        try:
            res_id = int(res_id)
        except (ValueError, TypeError):
            raise ValueError(f"res_id must be an integer, got {type(res_id)}: {res_id}")
    
    # Ensure res_id is not 0 (invalid in Odoo)
    if res_id == 0:
        raise ValueError(f"res_id cannot be 0 for model {res_model}")
    
    # Ensure activity_type_id is valid
    if activity_type_id is None:
        raise ValueError("activity_type_id cannot be None")
    
    if isinstance(activity_type_id, (list, tuple)) and len(activity_type_id) > 0:
        activity_type_id = activity_type_id[0]
    
    if not isinstance(activity_type_id, int):
        try:
            activity_type_id = int(activity_type_id)
        except (ValueError, TypeError):
            raise ValueError(f"activity_type_id must be an integer, got {type(activity_type_id)}: {activity_type_id}")
    
    if activity_type_id == 0:
        raise ValueError("activity_type_id cannot be 0")
    
    # Use activity_schedule method on the model instead of direct create
    # This is the recommended way to create activities in Odoo
    # activity_schedule signature: activity_schedule(activity_type_id, summary=None, date_deadline=None, user_id=None)
    print(f"   [DEBUG] Creating activity via activity_schedule: res_model={res_model}, res_id={res_id}, activity_type_id={activity_type_id}, summary={summary}")
    
    # Prepare args and kwargs for activity_schedule
    # activity_type_id is the first positional argument
    args = [activity_type_id]
    kwargs = {}
    if summary:
        kwargs["summary"] = summary
    if date_deadline:
        kwargs["date_deadline"] = date_deadline
    if user_id:
        if isinstance(user_id, (list, tuple)) and len(user_id) > 0:
            user_id = user_id[0]
        kwargs["user_id"] = user_id
    
    print(f"   [DEBUG] Calling activity_schedule with args={args}, kwargs={kwargs}")
    
    # Call activity_schedule on the record
    try:
        result = client.call_method(res_model, "activity_schedule", [res_id], args, kwargs)
        print(f"   [DEBUG] Activity created successfully via activity_schedule")
        return result
    except Exception as e:
        print(f"   [DEBUG] activity_schedule failed, trying direct create as fallback: {e}")
        # Fallback to direct create if activity_schedule doesn't work
        values = {
            "res_model": res_model,
            "res_id": res_id,
            "activity_type_id": activity_type_id,
            "summary": summary,
            "date_deadline": date_deadline,
        }
        if user_id:
            values["user_id"] = user_id
        activity_id = client.create('mail.activity', values)
        print(f"   [DEBUG] Activity created successfully with ID: {activity_id}")
        return activity_id

# ==============================================================================
# PRODUCT ENHANCEMENT FUNCTIONS
# ==============================================================================

def get_existing_uoms(client):
    """Get all existing UOMs (Units of Measure) from the database."""
    uoms = client.search_read(
        'uom.uom',
        [],
        fields=["id", "name"],
        limit=0
    )
    return uoms

def get_existing_barcodes(client):
    """Get all existing barcodes from products to ensure uniqueness."""
    products = client.search_read(
        'product.product',
        [["barcode", "!=", False]],
        fields=["barcode"],
        limit=0
    )
    return set(p.get("barcode") for p in products if p.get("barcode"))

def get_existing_default_codes(client):
    """Get all existing default_codes (internal references) from products to ensure uniqueness."""
    products = client.search_read(
        'product.product',
        [["default_code", "!=", False]],
        fields=["default_code"],
        limit=0
    )
    return set(p.get("default_code") for p in products if p.get("default_code"))

def generate_unique_barcode(client, existing_barcodes=None):
    """Generate a unique EAN-13 barcode."""
    if existing_barcodes is None:
        existing_barcodes = get_existing_barcodes(client)
    
    max_attempts = 100
    for _ in range(max_attempts):
        # Generate EAN-13 barcode (13 digits)
        # First 12 digits (random, but valid format)
        barcode = ''.join([str(random.randint(0, 9)) for _ in range(12)])
        # Calculate check digit (EAN-13 algorithm)
        checksum = sum(int(barcode[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
        check_digit = (10 - (checksum % 10)) % 10
        barcode = barcode + str(check_digit)
        
        if barcode not in existing_barcodes:
            existing_barcodes.add(barcode)
            return barcode
    
    # Fallback: use timestamp-based barcode if all attempts fail
    import time
    timestamp_barcode = str(int(time.time() * 1000))[-13:].zfill(13)
    if timestamp_barcode not in existing_barcodes:
        existing_barcodes.add(timestamp_barcode)
        return timestamp_barcode
    return None

def extract_prefix_from_name(product_name):
    """Extract a 3-letter prefix from product name.
    
    Rules:
    - Take first 3 letters (uppercase, alphanumeric only)
    - If name has less than 3 letters, pad with 'X'
    - Remove special characters and spaces
    """
    import re
    # Remove special characters, keep only alphanumeric
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', product_name)
    # Take first 3 characters, convert to uppercase
    prefix = cleaned[:3].upper()
    # Pad with 'X' if less than 3 characters
    while len(prefix) < 3:
        prefix += 'X'
    return prefix[:3]

def generate_unique_default_code(client, product_name, existing_default_codes=None):
    """Generate a unique internal reference (default_code).
    
    Format: XXX-##### (3 letters from product name + 5-digit number)
    Example: KAB-12345 for "Kabel"
    """
    if existing_default_codes is None:
        existing_default_codes = get_existing_default_codes(client)
    
    prefix = extract_prefix_from_name(product_name)
    
    max_attempts = 1000
    for _ in range(max_attempts):
        # Generate 5-digit number
        number = str(random.randint(10000, 99999))
        default_code = f"{prefix}-{number}"
        
        if default_code not in existing_default_codes:
            existing_default_codes.add(default_code)
            return default_code
    
    # Fallback: use timestamp if all attempts fail
    import time
    timestamp_suffix = str(int(time.time() * 1000))[-5:].zfill(5)
    fallback_code = f"{prefix}-{timestamp_suffix}"
    if fallback_code not in existing_default_codes:
        existing_default_codes.add(fallback_code)
        return fallback_code
    return None

def create_product_categories(client, industry, language_name="German"):
    """Create at least 5 product categories based on industry."""
    print(f"-> Creating product categories for industry: {industry}")
    
    # Check existing categories
    existing_categories = client.search_read(
        'product.category',
        [],
        fields=["id", "name"],
        limit=0
    )
    existing_names = {cat.get("name") for cat in existing_categories if cat.get("name")}
    
    # Default categories based on industry
    default_categories = {
        'IT': ['Software', 'Hardware', 'Netzwerk', 'Dienstleistungen', 'Zubehör'],
        'Fertigung': ['Rohstoffe', 'Halbfabrikate', 'Fertigprodukte', 'Werkzeuge', 'Ersatzteile'],
        'Handel': ['Elektronik', 'Möbel', 'Kleidung', 'Lebensmittel', 'Haushaltswaren'],
    }
    
    categories = default_categories.get(industry, default_categories['IT'])
    category_ids = []
    
    for cat_name in categories:
        if cat_name not in existing_names:
            print(f"   Creating category: {cat_name}")
            cat_id = client.create('product.category', {"name": cat_name})
            category_ids.append(cat_id)
            existing_names.add(cat_name)
        else:
            # Find existing category
            for cat in existing_categories:
                if cat.get("name") == cat_name:
                    category_ids.append(cat.get("id"))
                    break
    
    print(f"✅ {len(category_ids)} product categories ready")
    return category_ids

def get_current_user_id(client):
    """Get the current API user ID."""
    try:
        # Try to get user from context or search for admin user
        users = client.search_read(
            'res.users',
            [["active", "=", True]],
            fields=["id"],
            limit=1
        )
        if users:
            return users[0].get("id")
    except Exception:
        pass
    return None

def check_tracking_settings(client):
    """Check if lot and serial number tracking is enabled in res.config.settings.
    
    Since res.config.settings is a transient model, we need to fetch all records
    and use the last entry.
    """
    try:
        # Try to read the group_stock_production_lot field from res.config.settings
        lot_enabled = False
        serial_enabled = False
        
        try:
            # Fetch all settings records (transient model, so we get all current settings)
            settings = client.search_read(
                'res.config.settings',
                [],
                fields=["group_stock_production_lot", "create_date", "write_date"],
                limit=0  # Get all records
            )
            
            if settings:
                # Use the last entry (most recent)
                # Sort by write_date or create_date if available, otherwise use last in list
                last_setting = None
                if len(settings) > 0:
                    # Try to find the most recent by write_date or create_date
                    settings_with_dates = [s for s in settings if s.get("write_date") or s.get("create_date")]
                    if settings_with_dates:
                        # Sort by write_date (most recent first), fallback to create_date
                        settings_with_dates.sort(
                            key=lambda x: x.get("write_date") or x.get("create_date") or "",
                            reverse=True
                        )
                        last_setting = settings_with_dates[0]
                    else:
                        # If no dates, just use the last one in the list
                        last_setting = settings[-1]
                
                if last_setting and last_setting.get("group_stock_production_lot"):
                    # If group_stock_production_lot is enabled, both lot and serial tracking are available
                    lot_enabled = True
                    serial_enabled = True
        except Exception as e:
            # If field doesn't exist or can't be read, fall back to model check
            print(f"   ⚠️  Konnte group_stock_production_lot nicht lesen: {e}")
            # Fallback: Check if stock.lot model exists
            try:
                lots = client.search_read('stock.lot', [], fields=["id"], limit=1)
                if lots is not None:
                    lot_enabled = True
                    serial_enabled = True
            except Exception:
                pass
        
        return {
            "lot_enabled": bool(lot_enabled),
            "serial_enabled": bool(serial_enabled)
        }
    except Exception as e:
        print(f"   ⚠️  Fehler beim Prüfen der Tracking-Einstellungen: {e}")
        # Return True as fallback (assume enabled) so user can still use tracking
        return {"lot_enabled": True, "serial_enabled": True}
