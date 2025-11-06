import ssl
import getpass
import sys

import os
import configparser
import questionary
import odoo_actions
import random
import gemini_client
from odoo_client import OdooJson2Client
## Updated Interactive Wizard
def run_interactive_wizard():
    """Führt den Benutzer durch eine Reihe von detaillierten Fragen."""
    print("🚀 Willkommen beim Odoo Demo-Daten Assistenten!")
    
    criteria = {}
    
    # Mode and Industry questions remain the same
    criteria['mode'] = questionary.select(
        "Was möchtest du tun?",
        choices=[
            "Nur Stammdaten anlegen (Kunden, Produkte)",
            "Stammdaten anlegen UND Bewegungsdaten (Angebote) erstellen"
        ]
    ).ask()
    criteria['industry'] = questionary.text(
        "Für welche Branche sollen die Daten sein? (z.B. 'IT-Dienstleistung')",
        default="IT-Dienstleistung"
    ).ask()

    print("\n--- KUNDEN-DEFINITION ---")
    criteria['num_companies'] = int(questionary.text(
        "Wie viele Haupt-Kunden (Firmen) sollen erstellt werden?",
        validate=lambda text: text.isdigit(), default="1"
    ).ask())
    
    # NEU: Granulare Abfrage der Kontakt-Typen
    criteria['num_delivery_contacts'] = int(questionary.text(
        "Anzahl der Lieferadressen pro Firma?",
        validate=lambda text: text.isdigit(), default="1"
    ).ask())
    criteria['num_invoice_contacts'] = int(questionary.text(
        "Anzahl der Rechnungsadressen pro Firma?",
        validate=lambda text: text.isdigit(), default="1"
    ).ask())
    criteria['num_other_contacts'] = int(questionary.text(
        "Anzahl sonstiger Ansprechpartner pro Firma?",
        validate=lambda text: text.isdigit(), default="1"
    ).ask())


    print("\n--- PRODUKT-DEFINITION ---")
    criteria['num_services'] = int(questionary.text(
        "Anzahl der Dienstleistungs-Produkte:",
        validate=lambda text: text.isdigit(), default="1"
    ).ask())
    
    criteria['num_consumables'] = int(questionary.text(
        "Anzahl der Verbrauchs-Produkte (nicht lagerfähig):",
        validate=lambda text: text.isdigit(), default="1"
    ).ask())
    
    criteria['num_storables'] = int(questionary.text(
        "Anzahl der lagerfähigen Produkte:",
        validate=lambda text: text.isdigit(), default="1"
    ).ask())
    
    return criteria

def setup_connections():
    """Prompts for credentials (with config/env defaults) and connects to Odoo (JSON 2 API)."""
    config = configparser.ConfigParser()
    if not config.read('config.ini'):
        print("Warnung: config.ini nicht gefunden. Es werden alle Zugangsdaten abgefragt.")
        odoo_config = {}
        gemini_config = {}
    else:
        odoo_config = config['odoo'] if 'odoo' in config else {}
        gemini_config = config['gemini'] if 'gemini' in config else {}

    # Defaults from config if available
    url_default = (odoo_config.get('url') if isinstance(odoo_config, dict) else odoo_config.get('url', '') ) or ''
    db_default = (odoo_config.get('db') if isinstance(odoo_config, dict) else odoo_config.get('db', '') ) or ''
    username_default = (odoo_config.get('username') if isinstance(odoo_config, dict) else odoo_config.get('username', '') ) or ''
    gemini_model_name = (gemini_config.get('model') if isinstance(gemini_config, dict) else gemini_config.get('model', 'gemini-1.5-flash')) or 'gemini-1.5-flash'

    # Prompt for Odoo connection parameters
    url = questionary.text("Odoo URL (z.B. https://my.odoo.com):", default=url_default).ask()
    db = questionary.text("Odoo Datenbankname:", default=db_default, validate=lambda t: len(t.strip()) > 0).ask()
    username = questionary.text("Odoo Benutzername/Email:", default=username_default, validate=lambda t: len(t.strip()) > 0).ask()
    
    # Prompt for Odoo API Key (mask input)
    env_odoo_key = os.environ.get("ODOO_API_KEY", "")
    print("Geben Sie den Odoo API Key ein (JSON 2 API).")
    if env_odoo_key:
        print("Hinweis: Es ist bereits ein ODOO_API_KEY in der Umgebung gesetzt; Eingabe überschreibt diesen.")
    api_key = ''
    while not api_key:
        api_key = getpass.getpass(f"API Key für Odoo-Benutzer '{username}': ")
        if not api_key and env_odoo_key:
            api_key = env_odoo_key
    
    # Prompt for Gemini API Key (mask input)
    env_gemini_key = os.environ.get("GEMINI_API_KEY", "")
    print("Geben Sie den Google Gemini API Key ein.")
    if env_gemini_key:
        print("Hinweis: Es ist bereits ein GEMINI_API_KEY in der Umgebung gesetzt; Eingabe überschreibt diesen.")
    gemini_api_key = ''
    while not gemini_api_key:
        gemini_api_key = getpass.getpass("GEMINI_API_KEY: ")
        if not gemini_api_key and env_gemini_key:
            gemini_api_key = env_gemini_key
    gemini_client.genai.configure(api_key=gemini_api_key)

    print("-" * 30)

    print("Verbinde mit Odoo (JSON 2 API)...")
    client = OdooJson2Client(url, db, api_key)
    print("✅ Odoo JSON 2 Client initialisiert.\n")

    return {
        "client": client,
        "gemini_model_name": gemini_model_name
    }

def populate_odoo_with_data(creative_data, criteria, client):
    """Iterates through the creative data and creates the entries in Odoo."""
    if not creative_data:
        print("Keine Daten zum Verarbeiten vorhanden.")
        return {"product_ids": [], "company_ids": [], "order_ids": []}

    all_product_ids = []
    created_company_ids = []

    print("\n--- Erstelle Produkte ---")
    product_map = {
        'services': {'type': 'service'},
        'consumables': {'type': 'consu', 'is_storable': False},
        'storables': {'type': 'consu', 'is_storable': True}
    }
    for product_type, template in product_map.items():
        for creative_product in creative_data.get('products', {}).get(product_type, []):
            final_product_data = template.copy()
            valid_creative_data = {k: v for k, v in creative_product.items() if v is not None}
            final_product_data.update(valid_creative_data)
            
            # Ensure realistic price fields
            if 'list_price' not in final_product_data:
                final_product_data['list_price'] = round(random.uniform(15, 500), 2)
            if 'standard_price' not in final_product_data:
                final_product_data['standard_price'] = round(final_product_data['list_price'] * random.uniform(0.4, 0.8), 2)
            if 'name' in final_product_data:
                new_id = odoo_actions.create_product(client, final_product_data)
                all_product_ids.append(new_id)

 # --- 2. KUNDEN & KONTAKTE ERSTELLEN ---
    print("\n--- Erstelle Kunden und Kontakte ---")
    for company_scenario in creative_data.get('companies', []):
        company_data = company_scenario.get('company_data', {})
        if not company_data.get('name'): continue
        
        # Process main company address
        valid_company_data = {k: v for k, v in company_data.items() if v is not None}
        # Remove VAT ID to avoid validation errors
        valid_company_data.pop('vat', None)
        valid_company_data.pop('vat_id', None)
        country_code = valid_company_data.pop('country_code', 'DE')
        country_id = odoo_actions.get_country_id(client, country_code)
        if country_id:
            valid_company_data['country_id'] = country_id
        
        valid_company_data['company_type'] = 'company'
        company_id = odoo_actions.create_customer(client, valid_company_data)
        created_company_ids.append(company_id)
        
        # Process sub-contacts
        for contact_data in company_scenario.get('contacts', []):
            valid_contact_data = {k: v for k, v in contact_data.items() if v is not None}
            # Remove VAT ID to avoid validation errors
            valid_contact_data.pop('vat', None)
            valid_contact_data.pop('vat_id', None)
            valid_contact_data['parent_id'] = company_id
            
            # NEU: Verarbeite die individuelle Adresse des Sub-Kontakts, falls vorhanden
            if 'country_code' in valid_contact_data:
                contact_country_code = valid_contact_data.pop('country_code', 'DE')
                contact_country_id = odoo_actions.get_country_id(client, contact_country_code)
                if contact_country_id:
                    valid_contact_data['country_id'] = contact_country_id
            
            odoo_actions.create_customer(client, valid_contact_data)

    if "Bewegungsdaten" in criteria['mode'] and created_company_ids and all_product_ids:
        print("\n--- Erstelle Angebote (Bewegungsdaten) ---")
        for company_id in created_company_ids:
            products_for_order = all_product_ids[:2]
            if not products_for_order:
                continue

            order_lines_payload = []
            for product_id in products_for_order:
                order_lines_payload.append((0, 0, {'product_id': product_id, 'product_uom_qty': 1}))
            
            order_payload = {'partner_id': company_id, 'order_line': order_lines_payload}
            odoo_actions.create_sale_order(client, order_payload)

    return {"product_ids": all_product_ids, "company_ids": created_company_ids, "order_ids": []}

def ask_module_selections(installed_modules):
    """Ask user which modules to create data for and how many - all at once."""
    module_names = {
        "crm": "CRM",
        "sale": "Sales",
        "account": "Accounting",
        "hr": "Employees",
        "project": "Project",
        "hr_timesheet": "Timesheet"
    }
    
    selections = {}
    print("\n--- MODUL-AUSWAHL ---")
    print("Für welche installierten Module sollen Demo-Daten erstellt werden?")
    print("Bitte geben Sie für jedes Modul an, ob Daten erstellt werden sollen und wie viele.\n")
    
    # Ask all questions in sequence - this happens in one function call
    module_order = ["crm", "sale", "account", "hr", "project", "hr_timesheet"]
    
    for module_code in module_order:
        if module_code in installed_modules:
            module_name = module_names.get(module_code, module_code.upper())
            
            # Ask if data should be created for this module
            create_data = questionary.confirm(
                f"Soll für {module_name} Demo-Daten erstellt werden?",
                default=False
            ).ask()
            
            if create_data:
                # Ask for count based on module type
                if module_code == "crm":
                    count = int(questionary.text(
                        f"Wie viele Opportunities für {module_name}? (empfohlen: 10)",
                        default="10",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    selections[module_code] = count
                elif module_code == "sale":
                    count = int(questionary.text(
                        f"Wie viele Verkaufsaufträge für {module_name}? (empfohlen: 10)",
                        default="10",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    selections[module_code] = count
                elif module_code == "account":
                    count = int(questionary.text(
                        f"Wie viele Rechnungen für {module_name}? (empfohlen: 10)",
                        default="10",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    selections[module_code] = count
                elif module_code == "hr":
                    count = int(questionary.text(
                        f"Wie viele Mitarbeiter für {module_name}? (empfohlen: 10)",
                        default="10",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    selections[module_code] = count
                elif module_code == "project":
                    count = int(questionary.text(
                        f"Wie viele Projekte für {module_name}? (empfohlen: 10)",
                        default="10",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    tasks_per = int(questionary.text(
                        f"Wie viele Aufgaben pro Projekt? (empfohlen: 10)",
                        default="10",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    selections[module_code] = count
                    selections["tasks_per_project"] = tasks_per
                elif module_code == "hr_timesheet":
                    count = int(questionary.text(
                        f"Wie viele Zeiteinträge für {module_name}? (empfohlen: 10)",
                        default="10",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    selections[module_code] = count
    
    return selections

def create_module_demo_data(client, created_ids, gemini_model_name=None, language_name="German", module_selections=None):
    desired_modules = ["crm", "sale", "account", "hr", "project", "hr_timesheet"]
    installed = odoo_actions.get_installed_modules(client, desired_modules)
    company_ids = created_ids.get("company_ids", [])
    product_ids = created_ids.get("product_ids", [])
    created_opportunity_ids = []
    created_order_ids = []
    confirmed_order_ids = []  # Track confirmed orders for invoice creation
    
    # Ensure at least one partner and two products exist for downstream demos
    if not company_ids:
        print("-> Creating fallback demo partner")
        env_companies = os.environ.get('NAMES_COMPANY', '')
        fallback_companies = [
            'ACME Consulting GmbH', 'FutureSoft AG', 'Innovativ Solutions GmbH',
            'DataWorks KG', 'NextGen Systems GmbH'
        ]
        company_bank = [c for c in env_companies.split('||') if c] or fallback_companies
        company_ids.append(odoo_actions.create_customer(client, {"name": random.choice(company_bank)}))
    while len(product_ids) < 2:
        idx = len(product_ids) + 1
        print(f"-> Creating fallback demo product")
        industry = os.environ.get('INDUSTRY', 'IT')
        env_products = os.environ.get('NAMES_PRODUCT', '')
        product_fallback_bank = {
            'IT': ['Cloud Service Paket', 'Supportvertrag Premium', 'SaaS Lizenz', 'Firewall Appliance', 'Backup Lösung'],
            'Fertigung': ['Schraubensatz M6', 'Hydraulikpumpe', 'Förderband Motor', 'Sensorik Kit', 'Wartungspaket'],
            'Handel': ['Kassensystem', 'Barcode Scanner', 'Regalmodul', 'Etikettendrucker', 'Verpackungseinheit']
        }
        names = [p for p in env_products.split('||') if p] or product_fallback_bank.get(industry, product_fallback_bank['IT'])
        product_ids.append(odoo_actions.create_product(client, {"name": random.choice(names), "type": "consu", "list_price": round(random.uniform(15, 500), 2)}))

    # Use module_selections if provided, otherwise use environment defaults
    if module_selections is None:
        module_selections = {}
    
    num_opps = module_selections.get("crm", 0)
    num_orders = module_selections.get("sale", 0)
    num_projects = module_selections.get("project", 0)
    tasks_per_project = module_selections.get("tasks_per_project", 10)
    num_invoices = module_selections.get("account", 0)
    num_employees = module_selections.get("hr", 0)
    num_timesheets = module_selections.get("hr_timesheet", 0)

    if "crm" in installed and num_opps > 0:
        print("\n--- CRM: Erstelle Opportunities ---")
        opp_verbs = ['Implementierung', 'Upgrade', 'Wartung', 'Beratung']
        opp_preps = ['für', 'bei']
        opp_domains = ['ERP', 'CRM', 'DMS']
        industry = os.environ.get('INDUSTRY', 'IT')
        env_opps = os.environ.get('NAMES_OPPORTUNITY', '')
        opp_titles_bank = [o for o in env_opps.split('||') if o]
        for i in range(num_opps):
            partner_ref = company_ids[i % len(company_ids)]
            opp_name = random.choice(opp_titles_bank) if opp_titles_bank else f"{random.choice(opp_verbs)} {random.choice(opp_preps)} {random.choice(opp_domains)} - {industry}"
            opp_id = odoo_actions.create_opportunity(client, partner_ref, opp_name)
            created_opportunity_ids.append(opp_id)
        
        # Distribute opportunities across stages (except won)
        print("\n--- CRM: Verteile Opportunities auf Phasen ---")
        crm_stages = odoo_actions.get_crm_stages(client, exclude_won=True)
        if crm_stages:
            for opp_id in created_opportunity_ids:
                stage_id = random.choice(crm_stages)
                odoo_actions.update_opportunity_stage(client, opp_id, stage_id)

    if "sale" in installed and num_orders > 0:
        print("\n--- SALES: Erstelle Verkaufsaufträge ---")
        for i in range(num_orders):
            cid = company_ids[i % len(company_ids)]
            lines = []
            chosen_products = random.sample(product_ids, k=min(len(product_ids), random.randint(1, min(3, len(product_ids)))))
            for pid in chosen_products:
                qty = random.randint(1, 5)
                lines.append((0, 0, {"product_id": pid, "product_uom_qty": qty}))
            oid = odoo_actions.create_sale_order(client, {"partner_id": cid, "order_line": lines})
            created_order_ids.append(oid)
        # Link first 10 opps to first 10 orders when available
        if created_opportunity_ids:
            for oid, opp_id in zip(created_order_ids, created_opportunity_ids):
                odoo_actions.link_order_to_opportunity(client, oid, opp_id)
        # Confirm first 5 orders
        if created_order_ids:
            orders_to_confirm = created_order_ids[: max(1, min(5, len(created_order_ids)))]
            print(f"-> Bestätige {len(orders_to_confirm)} von {len(created_order_ids)} Verkaufsaufträgen")
            odoo_actions.confirm_sale_orders(client, orders_to_confirm)
            
            # Verify that orders are actually confirmed before proceeding
            print("-> Verifiziere Bestätigung der Verkaufsaufträge...")
            confirmed_orders = client.search_read(
                'sale.order',
                [["id", "in", orders_to_confirm], ["state", "in", ["sale", "done"]]],
                fields=["id", "state", "name"],
                limit=0
            )
            verified_confirmed_ids = [order["id"] for order in confirmed_orders]
            confirmed_order_ids.extend(verified_confirmed_ids)
            
            if len(verified_confirmed_ids) > 0:
                order_names = [o.get('name', f"ID:{o.get('id')}") for o in confirmed_orders]
                print(f"-> ✅ {len(verified_confirmed_ids)} Verkaufsaufträge erfolgreich bestätigt: {order_names}")
            else:
                print(f"-> ⚠️  Warnung: Keine Verkaufsaufträge konnten bestätigt werden")
        
        # Move opportunities with confirmed orders to "won" stage
        if "crm" in installed and len(confirmed_order_ids) > 0 and created_opportunity_ids:
            print("\n--- CRM: Verschiebe Opportunities mit bestätigten Aufträgen auf 'Won' ---")
            all_stages = client.search_read('crm.stage', [], fields=["id", "name"], limit=0)
            won_stage = None
            for stage in all_stages:
                if stage.get("name", "").lower() == "won":
                    won_stage = stage
                    break
            if won_stage:
                won_stage_id = won_stage["id"]
                # Get opportunities linked to confirmed orders
                orders_with_opps = client.search_read('sale.order', [["id", "in", confirmed_order_ids]], fields=["opportunity_id"], limit=0)
                for order in orders_with_opps:
                    opp_id = order.get("opportunity_id")
                    if opp_id and isinstance(opp_id, (list, tuple)):
                        opp_id = opp_id[0]
                    if opp_id:
                        odoo_actions.update_opportunity_stage(client, opp_id, won_stage_id)

    # Create invoices from confirmed sale orders when Sales is installed and we have confirmed orders
    # This should happen regardless of whether user selected to create invoices in accounting module
    # Only proceed if we have verified confirmed orders
    if "account" in installed and "sale" in installed and len(confirmed_order_ids) > 0:
        print(f"\n--- ACCOUNTING: Erstelle Kundenrechnungen aus {len(confirmed_order_ids)} bestätigten Verkaufsaufträgen ---")
        print(f"-> Verwendete bestätigte Aufträge: {confirmed_order_ids}")
        odoo_actions.create_invoices_from_orders(client, confirmed_order_ids)
    elif "account" in installed and "sale" in installed:
        print(f"\n--- ACCOUNTING: Keine bestätigten Verkaufsaufträge verfügbar für Rechnungserstellung ---")
    
    # Create invoices from scratch only if we cannot use sales orders
    # (Sales not installed OR no confirmed orders available) AND user selected to create invoices
    if "account" in installed and num_invoices > 0:
        # Only create from scratch if we didn't already create from orders
        if not ("sale" in installed and len(confirmed_order_ids) > 0):
            if "sale" not in installed:
                print("\n--- ACCOUNTING: Erstelle Kundenrechnungen (Verkauf nicht installiert) ---")
            elif not confirmed_order_ids:
                print("\n--- ACCOUNTING: Erstelle Kundenrechnungen (keine bestätigten Aufträge verfügbar) ---")
            
            invoice_ids = []
            for i in range(num_invoices):
                cid = company_ids[i % len(company_ids)]
                # choose some products
                chosen = random.sample(product_ids, k=min(len(product_ids), random.randint(1, min(3, len(product_ids)))))
                inv_id = odoo_actions.create_customer_invoice(client, cid, chosen)
                invoice_ids.append(inv_id)
            odoo_actions.post_invoices(client, invoice_ids)

    if "hr" in installed and num_employees > 0:
        print("\n--- EMPLOYEES: Erstelle Mitarbeiter ---")
        env_employees = os.environ.get('NAMES_EMPLOYEE', '')
        employee_names = [e for e in env_employees.split('||') if e] or [
            'Anna Schmidt', 'Lukas Weber', 'Mia Fischer', 'Jonas Wagner', 'Lea Becker',
            'Paul Hoffmann', 'Nina Keller', 'Tim Schäfer', 'Laura Bauer', 'Felix Richter',
            'Sophie Wolf', 'Max König', 'Emma Hartmann', 'Ben Krämer', 'Lena Schuster'
        ]
        for i in range(num_employees):
            odoo_actions.create_employee(client, employee_names[i % len(employee_names)])

    project_ids = []
    project_task_map = {}  # Map project_id -> list of task_ids
    project_names_map = {}  # Map project_id -> project_name
    if "project" in installed and num_projects > 0:
        print("\n--- PROJECT: Erstelle Projekte und Aufgaben ---")
        project_types = ['Implementierung', 'Rollout', 'Pilot', 'Migration']
        industry = os.environ.get('INDUSTRY', 'IT')
        env_projects = os.environ.get('NAMES_PROJECT', '')
        project_name_bank = [p for p in env_projects.split('||') if p]
        for i in range(num_projects):
            pname = random.choice(project_name_bank) if project_name_bank else f"{random.choice(project_types)} {industry} Projekt"
            pid = odoo_actions.create_project(client, pname)
            project_ids.append(pid)
            project_names_map[pid] = pname
            project_task_map[pid] = []
            # Vary tasks count around tasks_per_project +- 2
            task_count = max(1, tasks_per_project + random.randint(-2, 3))
            for t in range(task_count):
                task_types = ['Analyse', 'Design', 'Entwicklung', 'Testing', 'Schulung']
                env_tasks = os.environ.get('NAMES_TASK', '')
                task_bank = [t for t in env_tasks.split('||') if t]
                tname = random.choice(task_bank) if task_bank else f"{random.choice(task_types)}"
                task_id = odoo_actions.create_task(client, pid, tname)
                project_task_map[pid].append(task_id)
        
        # Create stages for each project and distribute tasks
        print("\n--- PROJECT: Erstelle Phasen und verteile Aufgaben ---")
        # Fallback stage templates
        stage_templates = {
            'IT': ['Kickoff', 'Analyse & Planung', 'Entwicklung', 'Testing & QA', 'Deployment', 'Abnahme'],
            'Fertigung': ['Planung', 'Beschaffung', 'Produktion', 'Qualitätskontrolle', 'Montage', 'Abnahme'],
            'Handel': ['Planung', 'Beschaffung', 'Lagerung', 'Verkauf', 'Auslieferung', 'Nachbetreuung']
        }
        default_stages = ['Planung', 'Umsetzung', 'Testing', 'Review', 'Abnahme', 'Abschluss']
        fallback_stages = stage_templates.get(industry, default_stages)
        
        for pid in project_ids:
            # Try to get creative stage names from Gemini
            project_name = project_names_map.get(pid, "")
            gemini_stages = None
            if gemini_model_name:
                gemini_stages = gemini_client.fetch_project_stage_names(industry, project_name, gemini_model_name, language_name)
            
            # Use Gemini stages if available, otherwise use fallback
            if gemini_stages and len(gemini_stages) >= 4:
                available_stages = gemini_stages
            else:
                available_stages = fallback_stages
            
            # Create 4-6 stages per project
            num_stages = random.randint(4, 6)
            if len(available_stages) >= num_stages:
                selected_stages = random.sample(available_stages, k=num_stages)
            else:
                selected_stages = available_stages[:num_stages]
                # Fill with default if needed
                if len(selected_stages) < num_stages:
                    selected_stages.extend(default_stages[:num_stages - len(selected_stages)])
            
            stage_ids = []
            for seq, stage_name in enumerate(selected_stages[:num_stages], start=10):
                stage_id = odoo_actions.create_project_stage(client, pid, stage_name, sequence=seq * 10)
                stage_ids.append(stage_id)
            
            # Distribute tasks randomly across stages
            task_ids = project_task_map.get(pid, [])
            if task_ids and stage_ids:
                for task_id in task_ids:
                    stage_id = random.choice(stage_ids)
                    odoo_actions.update_task_stage(client, task_id, stage_id)

    if "hr_timesheet" in installed and project_ids and num_timesheets > 0:
        print("\n--- TIMESHEET: Erstelle Zeiteinträge ---")
        # Need employees to log timesheets; create fallback if none
        employees = client.search_read('hr.employee', [["active", "=", True]], fields=["id"], limit=10)
        employee_ids = [e['id'] for e in employees]
        while len(employee_ids) < 3:
            env_emp = os.environ.get('NAMES_EMPLOYEE', '')
            fallback_names = [e for e in env_emp.split('||') if e] or ['Tom Meier', 'Julia Brandt', 'Marcel Neumann', 'Clara Busch']
            employee_ids.append(odoo_actions.create_employee(client, fallback_names[(len(employee_ids)) % len(fallback_names)]))
        for i in range(num_timesheets):
            emp = employee_ids[i % len(employee_ids)]
            proj = project_ids[i % len(project_ids)] if project_ids else None
            if proj:
                odoo_actions.create_timesheet(client, emp, proj, hours=float(random.randint(1, 8)), description=f"Arbeitstag {i+1}", date_str="2025-01-01")

    # Create vendor invoices (bills)
    if "account" in installed and num_invoices > 0:
        print("\n--- ACCOUNTING: Erstelle Eingangsrechnungen ---")
        # Ensure a supplier
        supplier_names = [
            'Alpha Supplies GmbH', 'Global Parts AG', 'Logistik & Co. KG',
            'TechImport Ltd.', 'Bürobedarf Müller', 'Industriebedarf König'
        ]
        supplier_id = client.create('res.partner', {"name": random.choice(supplier_names), "supplier_rank": 1})
        for i in range(max(10, num_orders // 2)):
            chosen = random.sample(product_ids, k=min(len(product_ids), random.randint(1, min(3, len(product_ids)))))
            odoo_actions.create_vendor_bill(client, supplier_id, chosen, description_prefix=f"Vendor Bill {i+1}")

# ==============================================================================
#  HAUPT-SKRIPT
# ==============================================================================
if __name__ == "__main__":
    connections = None  # Initialize to ensure it's in scope for exception handler
    try:
        criteria = run_interactive_wizard()
        connections = setup_connections()
        
        os.environ["INDUSTRY"] = criteria.get('industry', 'IT')

        # Detect language from main company or API user
        print("\n--- Sprache erkennen ---")
        lang_code = odoo_actions.get_main_company_language(connections['client'])
        language_name = gemini_client.get_language_name(lang_code)
        print(f"✅ Erkannte Sprache: {lang_code} ({language_name})")
        os.environ["DETECTED_LANGUAGE"] = lang_code
        os.environ["LANGUAGE_NAME"] = language_name

        # Detect installed modules and ask user for selections
        print("\n--- Installierte Module erkennen ---")
        desired_modules = ["crm", "sale", "account", "hr", "project", "hr_timesheet"]
        installed_modules = odoo_actions.get_installed_modules(connections['client'], desired_modules)
        if installed_modules:
            print(f"✅ Gefundene installierte Module: {', '.join([m.upper() for m in installed_modules])}")
        else:
            print("⚠️  Keine der erwarteten Module sind installiert.")
        
        # Ask user which modules to create data for
        module_selections = ask_module_selections(installed_modules)
        
        if not module_selections:
            print("⚠️  Keine Module für Demo-Daten ausgewählt. Programm wird beendet.")
            sys.exit(0)

        creative_data = gemini_client.fetch_creative_data(
            criteria, 
            connections['gemini_model_name']
        )
        # Fetch Gemini-based name suggestions (with fallback below)
        name_suggestions = gemini_client.fetch_name_suggestions(
            criteria,
            connections['gemini_model_name'],
            language_name
        ) or {}
        # Stash into environment for easy access by helpers
        def _to_env_list(key, values):
            try:
                os.environ[key] = "||".join([v for v in values if isinstance(v, str) and v.strip()])
            except Exception:
                pass
        _to_env_list('NAMES_PRODUCT', name_suggestions.get('product_names', []))
        _to_env_list('NAMES_EMPLOYEE', name_suggestions.get('employee_names', []))
        _to_env_list('NAMES_COMPANY', name_suggestions.get('company_names', []))
        _to_env_list('NAMES_PROJECT', name_suggestions.get('project_names', []))
        _to_env_list('NAMES_TASK', name_suggestions.get('task_names', []))
        _to_env_list('NAMES_OPPORTUNITY', name_suggestions.get('opportunity_titles', []))
        
        created_ids = populate_odoo_with_data(
            creative_data, 
            criteria, 
            connections['client'], 
        )
        create_module_demo_data(connections['client'], created_ids, connections['gemini_model_name'], language_name, module_selections)
        
        # Display all API errors that occurred
        api_errors = connections['client'].get_errors()
        if api_errors:
            print("\n" + "="*70)
            print("⚠️  API-FEHLER ZUSAMMENFASSUNG")
            print("="*70)
            for idx, error in enumerate(api_errors, 1):
                print(f"\n[{idx}] Fehler:")
                print(f"    URL: {error.get('url', 'N/A')}")
                print(f"    Status Code: {error.get('status_code', 'N/A')}")
                print(f"    Fehlermeldung: {error.get('error_message', 'N/A')}")
                if error.get('error_body'):
                    print(f"    Fehlerdetails: {error.get('error_body', '')[:200]}")
                print(f"    Payload Keys: {', '.join(error.get('payload_keys', []))}")
            print("\n" + "="*70)
        else:
            print("\n✅ Keine API-Fehler aufgetreten!")
        
        print("\n" + "-"*30)
        print("✅ Alle Aktionen erfolgreich abgeschlossen!")

    except Exception as e:
        print(f"❌ Ein kritischer Fehler ist aufgetreten: {e}")
        
        # Display API errors even if program crashed
        try:
            if connections and connections.get('client'):
                api_errors = connections['client'].get_errors()
                if api_errors:
                    print("\n" + "="*70)
                    print("⚠️  API-FEHLER ZUSAMMENFASSUNG (vor dem Absturz)")
                    print("="*70)
                    for idx, error in enumerate(api_errors, 1):
                        print(f"\n[{idx}] Fehler:")
                        print(f"    URL: {error.get('url', 'N/A')}")
                        print(f"    Status Code: {error.get('status_code', 'N/A')}")
                        print(f"    Fehlermeldung: {error.get('error_message', 'N/A')}")
                        if error.get('error_body'):
                            print(f"    Fehlerdetails: {error.get('error_body', '')[:200]}")
                        print(f"    Payload Keys: {', '.join(error.get('payload_keys', []))}")
                    print("\n" + "="*70)
        except Exception:
            pass  # Ignore errors when trying to display errors