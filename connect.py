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
def run_interactive_wizard(default_industry=None):
    """Führt den Benutzer durch eine Reihe von detaillierten Fragen."""
    criteria = {}
    
    # Mode question
    criteria['mode'] = questionary.select(
        "Was möchtest du tun?",
        choices=[
            "Nur Stammdaten anlegen (Kunden, Produkte)",
            "Stammdaten anlegen UND Bewegungsdaten (Angebote) erstellen"
        ]
    ).ask()
    
    # Industry question with suggested default
    industry_default = default_industry or "IT-Dienstleistung"
    criteria['industry'] = questionary.text(
        "Für welche Branche sollen die Daten sein? (z.B. 'IT-Dienstleistung')",
        default=industry_default
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

def populate_odoo_with_data(creative_data, criteria, client, gemini_model_name=None, language_name="German", tracking_selections=None):
    """Iterates through the creative data and creates the entries in Odoo."""
    if not creative_data:
        print("Keine Daten zum Verarbeiten vorhanden.")
        return {"product_ids": [], "company_ids": [], "order_ids": []}

    all_product_ids = []
    created_company_ids = []
    
    # Setup product enhancements
    industry = criteria.get('industry', 'IT')
    
    # Create product categories
    category_ids = odoo_actions.create_product_categories(client, industry, language_name)
    
    # Get existing UOMs
    existing_uoms = odoo_actions.get_existing_uoms(client)
    print(f"-> Found {len(existing_uoms)} existing UOMs")
    
    # Get existing barcodes for uniqueness
    existing_barcodes = odoo_actions.get_existing_barcodes(client)
    print(f"-> Found {len(existing_barcodes)} existing barcodes")
    
    # Get existing default_codes for uniqueness
    existing_default_codes = odoo_actions.get_existing_default_codes(client)
    print(f"-> Found {len(existing_default_codes)} existing internal references")

    # Tracking setup
    use_tracking = tracking_selections and tracking_selections.get("use_tracking", False)
    lot_enabled = tracking_selections and tracking_selections.get("lot_enabled", False)
    serial_enabled = tracking_selections and tracking_selections.get("serial_enabled", False)
    
    # Track which tracking types we've created (for ensuring we have exactly one of each)
    tracking_created = {"lot": False, "serial": False}
    tracking_products = []  # Store products with tracking that are not in BOMs
    
    # Counter for storable products to assign tracking deterministically
    storable_counter = 0

    print("\n--- Erstelle Produkte ---")
    product_map = {
        'services': {'type': 'service'},
        'consumables': {'type': 'consu', 'is_storable': False},
        'storables': {'type': 'consu', 'is_storable': True}
    }
    # Invalid fields to filter out from Gemini-generated data
    invalid_product_fields = {'uom', 'vat', 'vat_id', 'detailed_type'}
    
    for product_type, template in product_map.items():
        for creative_product in creative_data.get('products', {}).get(product_type, []):
            final_product_data = template.copy()
            valid_creative_data = {k: v for k, v in creative_product.items() 
                                 if v is not None and k not in invalid_product_fields}
            final_product_data.update(valid_creative_data)
            
            # Ensure is_storable is set correctly based on product type
            if product_type == 'storables':
                final_product_data['is_storable'] = True
            elif product_type == 'consumables':
                final_product_data['is_storable'] = False
            
            # Assign tracking for storable products: exactly one serial, exactly one lot
            if use_tracking and product_type == 'storables' and final_product_data.get('is_storable'):
                if storable_counter == 0 and serial_enabled and not tracking_created['serial']:
                    # First storable product gets serial tracking
                    final_product_data['tracking'] = 'serial'
                    tracking_created['serial'] = True
                    tracking_products.append(('serial', None))  # Will be updated with product_id
                elif storable_counter == 1 and lot_enabled and not tracking_created['lot']:
                    # Second storable product gets lot tracking
                    final_product_data['tracking'] = 'lot'
                    tracking_created['lot'] = True
                    tracking_products.append(('lot', None))  # Will be updated with product_id
                else:
                    # All other storable products get no tracking
                    final_product_data['tracking'] = 'none'
                storable_counter += 1
            else:
                final_product_data['tracking'] = 'none'
            
            # Ensure realistic price fields
            if 'list_price' not in final_product_data:
                final_product_data['list_price'] = round(random.uniform(15, 500), 2)
            if 'standard_price' not in final_product_data:
                final_product_data['standard_price'] = round(final_product_data['list_price'] * random.uniform(0.4, 0.8), 2)
            
            # Add product enhancements
            product_name = final_product_data.get('name', '')
            
            # 1. Barcode (unique)
            if 'barcode' not in final_product_data:
                barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
                if barcode:
                    final_product_data['barcode'] = barcode
            
            # 2. Internal reference (default_code) - unique
            if 'default_code' not in final_product_data:
                default_code = odoo_actions.generate_unique_default_code(client, product_name, existing_default_codes)
                if default_code:
                    final_product_data['default_code'] = default_code
            
            # 3. Weight (realistic based on product type)
            if 'weight' not in final_product_data:
                if product_type == 'services':
                    final_product_data['weight'] = 0.0
                elif product_type == 'consumables':
                    # Light consumables: 0.1 - 2 kg
                    final_product_data['weight'] = round(random.uniform(0.1, 2.0), 3)
                else:  # storables
                    # Heavier storable items: 0.5 - 50 kg
                    final_product_data['weight'] = round(random.uniform(0.5, 50.0), 3)
            
            # 4. Product category (assign logically)
            if 'categ_id' not in final_product_data and category_ids:
                final_product_data['categ_id'] = random.choice(category_ids)
            
            # 5. UOM assignment (using Gemini)
            if 'uom_id' not in final_product_data and existing_uoms:
                uom_id = gemini_client.fetch_uom_assignment(
                    product_name,
                    template.get('type', 'consu'),
                    industry,
                    existing_uoms,
                    gemini_model_name,
                    language_name
                )
                if uom_id:
                    final_product_data['uom_id'] = uom_id
                else:
                    # Fallback: use first available UOM
                    if existing_uoms:
                        first_uom_id = existing_uoms[0].get("id")
                        if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                            first_uom_id = first_uom_id[0]
                        final_product_data['uom_id'] = first_uom_id
            
            if 'name' in final_product_data:
                new_id = odoo_actions.create_product(client, final_product_data)
                all_product_ids.append(new_id)
                # Update tracking_products list with actual product ID
                if use_tracking and final_product_data.get('tracking') in ['serial', 'lot']:
                    for idx, (track_type, prod_id) in enumerate(tracking_products):
                        if prod_id is None and track_type == final_product_data.get('tracking'):
                            tracking_products[idx] = (track_type, new_id)
                            break

    # Ensure we have at least one product with serial and one with lot (not in BOMs)
    if use_tracking:
        # Create additional products if needed
        if not tracking_created.get('serial') and serial_enabled:
            print("-> Creating additional product with serial number tracking")
            serial_product_data = {
                "name": f"{industry} Serienprodukt",
                "type": "consu",
                "is_storable": True,
                "tracking": "serial",
                "list_price": round(random.uniform(50, 300), 2),
                "standard_price": round(random.uniform(30, 200), 2)
            }
            # Add enhancements
            barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
            if barcode:
                serial_product_data['barcode'] = barcode
            default_code = odoo_actions.generate_unique_default_code(client, serial_product_data['name'], existing_default_codes)
            if default_code:
                serial_product_data['default_code'] = default_code
            serial_product_data['weight'] = round(random.uniform(0.5, 10.0), 3)
            if category_ids:
                serial_product_data['categ_id'] = random.choice(category_ids)
            if existing_uoms:
                first_uom_id = existing_uoms[0].get("id")
                if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                    first_uom_id = first_uom_id[0]
                serial_product_data['uom_id'] = first_uom_id
            serial_id = odoo_actions.create_product(client, serial_product_data)
            all_product_ids.append(serial_id)
            tracking_products.append(('serial', serial_id))
        
        if not tracking_created.get('lot') and lot_enabled:
            print("-> Creating additional product with lot number tracking")
            lot_product_data = {
                "name": f"{industry} Losprodukt",
                "type": "consu",
                "is_storable": True,
                "tracking": "lot",
                "list_price": round(random.uniform(50, 300), 2),
                "standard_price": round(random.uniform(30, 200), 2)
            }
            # Add enhancements
            barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
            if barcode:
                lot_product_data['barcode'] = barcode
            default_code = odoo_actions.generate_unique_default_code(client, lot_product_data['name'], existing_default_codes)
            if default_code:
                lot_product_data['default_code'] = default_code
            lot_product_data['weight'] = round(random.uniform(0.5, 10.0), 3)
            if category_ids:
                lot_product_data['categ_id'] = random.choice(category_ids)
            if existing_uoms:
                first_uom_id = existing_uoms[0].get("id")
                if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                    first_uom_id = first_uom_id[0]
                lot_product_data['uom_id'] = first_uom_id
            lot_id = odoo_actions.create_product(client, lot_product_data)
            all_product_ids.append(lot_id)
            tracking_products.append(('lot', lot_id))

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

    return {
        "product_ids": all_product_ids,
        "company_ids": created_company_ids,
        "order_ids": [],
        "tracking_products": tracking_products  # Products with tracking that are not in BOMs
    }

def ask_module_selections(installed_modules, client):
    """Ask user which modules to create data for and how many - all at once."""
    module_names = {
        "crm": "CRM",
        "sale": "Sales",
        "account": "Accounting",
        "hr": "Employees",
        "project": "Project",
        "hr_timesheet": "Timesheet",
        "mrp": "Manufacturing",
        "hr_recruitment": "Recruiting"
    }
    
    selections = {}
    print("\n--- MODUL-AUSWAHL ---")
    print("Für welche installierten Module sollen Demo-Daten erstellt werden?")
    print("Bitte geben Sie für jedes Modul an, ob Daten erstellt werden sollen und wie viele.\n")
    
    # Check tracking settings and ask user early
    tracking_settings = odoo_actions.check_tracking_settings(client)
    lot_available = tracking_settings.get("lot_enabled", False)
    serial_available = tracking_settings.get("serial_enabled", False)
    
    if lot_available or serial_available:
        print("\n--- LOS- UND SERIENNUMMERN ---")
        if lot_available:
            print("✅ Losnummern-Verfolgung ist verfügbar")
        if serial_available:
            print("✅ Seriennummern-Verfolgung ist verfügbar")
        
        use_tracking = questionary.confirm(
            "Sollen Produkte mit Los- oder Seriennummern-Verfolgung erstellt werden?",
            default=True
        ).ask()
        
        if use_tracking:
            selections["use_tracking"] = True
            selections["lot_enabled"] = lot_available
            selections["serial_enabled"] = serial_available
        else:
            selections["use_tracking"] = False
            selections["lot_enabled"] = False
            selections["serial_enabled"] = False
    else:
        print("\n--- LOS- UND SERIENNUMMERN ---")
        print("⚠️  Los- und Seriennummern-Verfolgung ist nicht verfügbar.")
        print("   Das Programm wird keine Produkte mit Tracking erstellen.")
        selections["use_tracking"] = False
        selections["lot_enabled"] = False
        selections["serial_enabled"] = False
    
    # Ask all questions in sequence - this happens in one function call
    module_order = ["crm", "sale", "account", "hr", "project", "hr_timesheet", "mrp", "hr_recruitment"]
    
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
                    # Ask if bank transactions should be created
                    create_bank_transactions = questionary.confirm(
                        "Sollen auch Banktransaktionen für die Eingangsrechnungen erstellt werden?",
                        default=True
                    ).ask()
                    selections["create_bank_transactions"] = create_bank_transactions
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
                elif module_code == "mrp":
                    num_products = int(questionary.text(
                        f"Wie viele Fertigungsprodukte für {module_name}? (empfohlen: 3)",
                        default="3",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    components_per_bom = int(questionary.text(
                        "Wie viele Komponenten sollen pro Stückliste angelegt werden? (inkl. möglicher Sub-Stücklisten, empfohlen: 4)",
                        default="4",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    while True:
                        sub_boms = int(questionary.text(
                            "Wie viele Komponenten pro Produkt sollen eigene Sub-Stücklisten erhalten?",
                            default=str(min(2, components_per_bom)),
                            validate=lambda t: t.isdigit()
                        ).ask())
                        if sub_boms <= components_per_bom:
                            break
                        print("⚠️  Hinweis: Die Anzahl der Sub-Stücklisten darf die Gesamtanzahl der Komponenten nicht überschreiten.")
                    selections[module_code] = {
                        "num_products": num_products,
                        "components_per_bom": components_per_bom,
                        "sub_boms_per_product": sub_boms
                    }
                elif module_code == "hr_recruitment":
                    num_jobs = int(questionary.text(
                        f"Wie viele Stellen für {module_name}? (empfohlen: 5)",
                        default="5",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    num_candidates = int(questionary.text(
                        f"Wie viele Kandidaten für {module_name}? (empfohlen: 15)",
                        default="15",
                        validate=lambda t: t.isdigit() and int(t) > 0
                    ).ask())
                    create_skills = questionary.confirm(
                        "Sollen Kompetenzen erstellt werden?",
                        default=True
                    ).ask()
                    num_skill_types = 0
                    skills_per_type = 0
                    if create_skills:
                        num_skill_types = int(questionary.text(
                            "Wie viele Kompetenzarten sollen erstellt werden? (empfohlen: 3)",
                            default="3",
                            validate=lambda t: t.isdigit() and int(t) > 0
                        ).ask())
                        skills_per_type = int(questionary.text(
                            "Wie viele Kompetenzen pro Kompetenzart? (empfohlen: 4)",
                            default="4",
                            validate=lambda t: t.isdigit() and int(t) > 0
                        ).ask())
                    selections[module_code] = {
                        "num_jobs": num_jobs,
                        "num_candidates": num_candidates,
                        "create_skills": create_skills,
                        "num_skill_types": num_skill_types,
                        "skills_per_type": skills_per_type
                    }
    
    # Ask if activities should be created
    print("\n--- AKTIVITÄTEN ---")
    create_activities = questionary.confirm(
        "Sollen Aktivitäten (Mail Activities) auf Datensätzen erstellt werden?",
        default=True
    ).ask()
    if create_activities:
        selections["create_activities"] = True
    
    return selections

def create_module_demo_data(client, created_ids, gemini_model_name=None, language_name="German", module_selections=None, category_ids=None, existing_uoms=None, existing_barcodes=None, existing_default_codes=None):
    desired_modules = ["crm", "sale", "account", "hr", "project", "hr_timesheet", "mrp", "hr_recruitment"]
    installed = odoo_actions.get_installed_modules(client, desired_modules)
    company_ids = created_ids.get("company_ids", [])
    product_ids = created_ids.get("product_ids", [])
    created_opportunity_ids = []
    created_order_ids = []
    confirmed_order_ids = []  # Track confirmed orders for invoice creation
    
    # Only create fallback data if modules actually need it (check module_selections)
    # Check if any module that needs partners/products is selected
    needs_partners = (
        module_selections and (
            module_selections.get("sale", 0) > 0 or
            module_selections.get("crm", 0) > 0 or
            module_selections.get("account", 0) > 0
        )
    )
    needs_products = (
        module_selections and (
            module_selections.get("sale", 0) > 0 or
            module_selections.get("account", 0) > 0
        )
    )
    
    # Ensure at least one partner exists only if needed
    if needs_partners and not company_ids:
        print("-> Creating fallback demo partner")
        env_companies = os.environ.get('NAMES_COMPANY', '')
        fallback_companies = [
            'ACME Consulting GmbH', 'FutureSoft AG', 'Innovativ Solutions GmbH',
            'DataWorks KG', 'NextGen Systems GmbH'
        ]
        company_bank = [c for c in env_companies.split('||') if c] or fallback_companies
        company_ids.append(odoo_actions.create_customer(client, {"name": random.choice(company_bank)}))
    
    # Ensure at least two products exist only if needed
    if needs_products:
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
            product_name = random.choice(names)
            fallback_product_data = {
                "name": product_name,
                "type": "consu",
                "is_storable": True,  # Fallback products are storable
                "list_price": round(random.uniform(15, 500), 2)
            }
            # Add enhancements
            if existing_barcodes is not None:
                barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
                if barcode:
                    fallback_product_data['barcode'] = barcode
            if existing_default_codes is not None:
                default_code = odoo_actions.generate_unique_default_code(client, product_name, existing_default_codes)
                if default_code:
                    fallback_product_data['default_code'] = default_code
            fallback_product_data['weight'] = round(random.uniform(0.1, 2.0), 3)
            if category_ids:
                fallback_product_data['categ_id'] = random.choice(category_ids)
            if existing_uoms:
                # Try Gemini assignment, fallback to first UOM
                uom_id = gemini_client.fetch_uom_assignment(
                    product_name, "consu", industry, existing_uoms, gemini_model_name, language_name
                ) if gemini_model_name else None
                if uom_id:
                    fallback_product_data['uom_id'] = uom_id
                else:
                    first_uom_id = existing_uoms[0].get("id")
                    if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                        first_uom_id = first_uom_id[0]
                    fallback_product_data['uom_id'] = first_uom_id
            product_ids.append(odoo_actions.create_product(client, fallback_product_data))

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
    mrp_config = module_selections.get("mrp", {})
    num_mrp_products = 0
    components_per_bom = 0
    sub_boms_per_product = 0
    if isinstance(mrp_config, dict):
        num_mrp_products = max(0, int(mrp_config.get("num_products", 0)))
        components_per_bom = max(1, int(mrp_config.get("components_per_bom", 1)))
        sub_boms_per_product = max(0, int(mrp_config.get("sub_boms_per_product", 0)))
        if sub_boms_per_product > components_per_bom:
            sub_boms_per_product = components_per_bom

    # ==============================================================================
    # CREATE MANUFACTURING DATA FIRST (before sales orders)
    # ==============================================================================
    if "mrp" in installed and num_mrp_products > 0:
        print("\n--- MANUFACTURING: Erstelle Fertigungsprodukte und Stücklisten ---")
        env_products = os.environ.get('NAMES_PRODUCT', '')
        product_name_bank = [p for p in env_products.split('||') if p]
        industry = os.environ.get('INDUSTRY', 'Fertigung')
        language = os.environ.get('LANGUAGE_NAME', language_name)
        created_bom_ids = []
        
        # Tracking setup for manufacturing
        use_tracking = tracking_selections and tracking_selections.get("use_tracking", False)
        serial_enabled = tracking_selections and tracking_selections.get("serial_enabled", False)
        serial_created_for_bom = False  # Track if we've created a serial product for BOMs

        for idx in range(num_mrp_products):
            base_name = None
            if product_name_bank:
                base_name = product_name_bank.pop(random.randrange(len(product_name_bank)))
            if not base_name:
                base_name = f"{industry} Baugruppe"
            main_product_name = f"{base_name} #{idx+1}" if base_name == base_name.strip() else base_name
            list_price = round(random.uniform(250, 1200), 2)
            standard_price = round(list_price * random.uniform(0.35, 0.65), 2)
            
            # Set tracking: exactly one product with serial number per BOM set
            tracking_value = "none"
            if use_tracking and serial_enabled and not serial_created_for_bom and idx == 0:
                # First product gets serial tracking
                tracking_value = "serial"
                serial_created_for_bom = True
            
            main_product_vals = {
                "name": main_product_name,
                "sale_ok": True,
                "purchase_ok": True,
                "list_price": list_price,
                "standard_price": standard_price,
                "tracking": tracking_value,
                "is_storable": True,  # Manufacturing products are storable
            }
            # Add enhancements
            if existing_barcodes is not None:
                barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
                if barcode:
                    main_product_vals['barcode'] = barcode
            if existing_default_codes is not None:
                default_code = odoo_actions.generate_unique_default_code(client, main_product_name, existing_default_codes)
                if default_code:
                    main_product_vals['default_code'] = default_code
            main_product_vals['weight'] = round(random.uniform(5.0, 50.0), 3)  # Heavier manufacturing products
            if category_ids:
                main_product_vals['categ_id'] = random.choice(category_ids)
            if existing_uoms:
                uom_id = gemini_client.fetch_uom_assignment(
                    main_product_name, "product", industry, existing_uoms, gemini_model_name, language
                ) if gemini_model_name else None
                if uom_id:
                    main_product_vals['uom_id'] = uom_id
                else:
                    first_uom_id = existing_uoms[0].get("id")
                    if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                        first_uom_id = first_uom_id[0]
                    main_product_vals['uom_id'] = first_uom_id
            main_product_id = odoo_actions.create_product(client, main_product_vals)
            product_ids.append(main_product_id)

            tmpl_id = odoo_actions.get_product_template_id(client, main_product_id)
            if not tmpl_id:
                print(f"⚠️  Konnte Template für Produkt {main_product_id} nicht ermitteln – überspringe Fertigungseintrag.")
                continue

            bom_code = f"BOM-{main_product_id}"
            bom_id = odoo_actions.create_bom(client, tmpl_id, product_id=main_product_id, quantity=1.0, code=bom_code)
            created_bom_ids.append(bom_id)

            component_count = max(components_per_bom, sub_boms_per_product or 0, 1)
            component_names: list[str] = []
            if gemini_model_name:
                gemini_components = gemini_client.fetch_bom_component_names(
                    main_product_name,
                    component_count,
                    gemini_model_name,
                    language,
                    industry
                )
                if gemini_components:
                    component_names.extend(gemini_components[:component_count])
            while len(component_names) < component_count:
                component_names.append(f"{main_product_name} Modul {len(component_names)+1}")

            for comp_idx, component_name in enumerate(component_names):
                comp_list_price = round(random.uniform(80, list_price * 0.6), 2)
                comp_standard_price = round(comp_list_price * random.uniform(0.4, 0.7), 2)
                component_vals = {
                    "name": component_name,
                    "sale_ok": False,
                    "purchase_ok": True,
                    "list_price": comp_list_price,
                    "standard_price": comp_standard_price,
                    "tracking": "none",
                    "is_storable": True,  # Components are storable
                }
                # Add enhancements
                if existing_barcodes is not None:
                    barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
                    if barcode:
                        component_vals['barcode'] = barcode
                if existing_default_codes is not None:
                    default_code = odoo_actions.generate_unique_default_code(client, component_name, existing_default_codes)
                    if default_code:
                        component_vals['default_code'] = default_code
                component_vals['weight'] = round(random.uniform(0.5, 10.0), 3)  # Medium weight components
                if category_ids:
                    component_vals['categ_id'] = random.choice(category_ids)
                if existing_uoms:
                    uom_id = gemini_client.fetch_uom_assignment(
                        component_name, "consu", industry, existing_uoms, gemini_model_name, language
                    ) if gemini_model_name else None
                    if uom_id:
                        component_vals['uom_id'] = uom_id
                    else:
                        first_uom_id = existing_uoms[0].get("id")
                        if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                            first_uom_id = first_uom_id[0]
                        component_vals['uom_id'] = first_uom_id
                component_product_id = odoo_actions.create_product(client, component_vals)
                product_ids.append(component_product_id)
                component_qty = max(1, random.randint(1, 4))
                odoo_actions.create_bom_line(client, bom_id, component_product_id, quantity=component_qty)

                if comp_idx < sub_boms_per_product:
                    component_template_id = odoo_actions.get_product_template_id(client, component_product_id)
                    if not component_template_id:
                        print(f"⚠️  Konnte Template für Komponente {component_product_id} nicht laden – Sub-Stückliste übersprungen.")
                        continue
                    sub_bom_code = f"SUB-{bom_id}-{comp_idx+1}"
                    sub_bom_id = odoo_actions.create_bom(
                        client,
                        component_template_id,
                        product_id=component_product_id,
                        quantity=1.0,
                        code=sub_bom_code
                    )
                    created_bom_ids.append(sub_bom_id)
                    raw_count = max(2, min(4, components_per_bom // 2 + 1))
                    for raw_idx in range(raw_count):
                        raw_name = f"{component_name} Rohteil {raw_idx+1}"
                        raw_list_price = round(max(15, comp_standard_price * random.uniform(0.4, 0.9)), 2)
                        raw_standard_price = round(raw_list_price * random.uniform(0.5, 0.85), 2)
                        raw_vals = {
                            "name": raw_name,
                            "sale_ok": False,
                            "purchase_ok": True,
                            "list_price": raw_list_price,
                            "standard_price": raw_standard_price,
                            "tracking": "none",
                            "is_storable": True,  # Raw materials are storable
                        }
                        # Add enhancements
                        if existing_barcodes is not None:
                            barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
                            if barcode:
                                raw_vals['barcode'] = barcode
                        if existing_default_codes is not None:
                            default_code = odoo_actions.generate_unique_default_code(client, raw_name, existing_default_codes)
                            if default_code:
                                raw_vals['default_code'] = default_code
                        raw_vals['weight'] = round(random.uniform(0.1, 5.0), 3)  # Light raw materials
                        if category_ids:
                            raw_vals['categ_id'] = random.choice(category_ids)
                        if existing_uoms:
                            uom_id = gemini_client.fetch_uom_assignment(
                                raw_name, "consu", industry, existing_uoms, gemini_model_name, language
                            ) if gemini_model_name else None
                            if uom_id:
                                raw_vals['uom_id'] = uom_id
                            else:
                                first_uom_id = existing_uoms[0].get("id")
                                if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                                    first_uom_id = first_uom_id[0]
                                raw_vals['uom_id'] = first_uom_id
                        raw_product_id = odoo_actions.create_product(client, raw_vals)
                        product_ids.append(raw_product_id)
                        raw_qty = round(random.uniform(1.0, 3.0), 2)
                        odoo_actions.create_bom_line(client, sub_bom_id, raw_product_id, quantity=raw_qty)

        print(f"✅ {len(created_bom_ids)} Stücklisten für {num_mrp_products} Fertigungsprodukte erstellt.")
        print(f"✅ Insgesamt {len([p for p in product_ids if p])} Produkte verfügbar (inkl. Manufacturing)")

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
        # Get all available products (including manufacturing products created above)
        # Filter to only include products that are sellable (sale_ok = True)
        all_available_products = client.search_read(
            'product.product',
            [["id", "in", product_ids], ["sale_ok", "=", True]],
            fields=["id"],
            limit=0
        )
        sellable_product_ids = [p.get("id") for p in all_available_products]
        
        # If no sellable products found, use all products (fallback)
        if not sellable_product_ids:
            sellable_product_ids = product_ids
            print("⚠️  Keine verkaufbaren Produkte gefunden, verwende alle Produkte")
        
        print(f"-> Verfügbare verkaufbare Produkte: {len(sellable_product_ids)}")
        
        for i in range(num_orders):
            cid = company_ids[i % len(company_ids)]
            lines = []
            # Choose from all sellable products (including manufacturing)
            num_products_in_order = random.randint(1, min(5, len(sellable_product_ids)))
            chosen_products = random.sample(sellable_product_ids, k=num_products_in_order)
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
    
    # Create bank transactions for all invoices (if user requested it)
    # This runs AFTER all invoices have been created and posted
    if "account" in installed and module_selections.get("create_bank_transactions", False):
        odoo_actions.create_bank_transactions_for_all_invoices(client)
    
    # Create recruiting data
    if "hr_recruitment" in installed and module_selections.get("hr_recruitment"):
        print("\n--- RECRUITING: Erstelle Recruiting-Daten ---")
        rec_config = module_selections.get("hr_recruitment", {})
        num_jobs = rec_config.get("num_jobs", 0)
        num_candidates = rec_config.get("num_candidates", 0)
        create_skills = rec_config.get("create_skills", False)
        num_skill_types = rec_config.get("num_skill_types", 0)
        skills_per_type = rec_config.get("skills_per_type", 0)
        
        if num_jobs > 0 or num_candidates > 0:
            industry = os.environ.get('INDUSTRY', 'IT')
            language_name = os.environ.get('LANGUAGE_NAME', 'German')
            
            # Fetch recruiting data from Gemini
            recruiting_data = gemini_client.fetch_recruiting_data(
                industry, num_jobs, num_candidates, num_skill_types, skills_per_type,
                gemini_model_name, language_name
            ) or {}
            
            all_skill_ids = []  # Store all created skill IDs
            
            # Create skills if requested
            if create_skills and num_skill_types > 0:
                print("\n--- RECRUITING: Erstelle Kompetenzen ---")
                existing_skill_types = odoo_actions.get_existing_skill_types(client)
                skill_types_data = recruiting_data.get("skill_types", [])
                
                for skill_type_data in skill_types_data[:num_skill_types]:
                    skill_type_name = skill_type_data.get("name", "")
                    if not skill_type_name:
                        continue
                    
                    # Check if skill type already exists
                    if skill_type_name.lower() in existing_skill_types:
                        print(f"-> Skill type '{skill_type_name}' already exists, skipping")
                        skill_type_id = existing_skill_types[skill_type_name.lower()]
                    else:
                        skill_type_id = odoo_actions.create_skill_type(client, skill_type_name)
                        existing_skill_types[skill_type_name.lower()] = skill_type_id
                    
                    # Create skills for this type
                    skills = skill_type_data.get("skills", [])[:skills_per_type]
                    for skill_name in skills:
                        skill_id = odoo_actions.create_skill(client, skill_type_id, skill_name)
                        all_skill_ids.append(skill_id)
                    
                    # Create levels for this type
                    levels = skill_type_data.get("levels", [])
                    if len(levels) < 3:
                        # Fallback levels if not enough provided
                        levels = ["Grundlagen", "Fortgeschritten", "Experte"]
                    
                    for idx, level_name in enumerate(levels[:max(3, len(levels))]):
                        level_progress = int((idx + 1) * 100 / max(3, len(levels)))
                        odoo_actions.create_skill_level(client, skill_type_id, level_name, level_progress)
            
            # Get all available skills (including existing ones)
            all_available_skills = client.search_read(
                'hr.skill',
                [],
                fields=["id"],
                limit=0
            )
            all_skill_ids = [s.get("id") for s in all_available_skills]
            
            # Get departments
            departments = odoo_actions.get_departments(client)
            if not departments:
                print("⚠️  Keine Abteilungen gefunden. Erstelle Fallback-Abteilung...")
                dept_id = odoo_actions.create_department(client, "Allgemein")
                departments = [{"id": dept_id, "name": "Allgemein"}]
            
            # Create jobs
            job_ids = []
            job_titles = recruiting_data.get("job_titles", [])[:num_jobs]
            if not job_titles:
                job_titles = [f"Stelle {i+1}" for i in range(num_jobs)]
            
            # Get existing job names per department to avoid duplicates
            existing_dept_job_names = odoo_actions.get_existing_job_names_per_department(client)
            
            # Track job names per department to ensure uniqueness (including new ones we create)
            dept_job_names = {}  # {dept_id: set of job names (lowercase)}
            
            # Initialize with existing job names
            for dept_id, existing_names in existing_dept_job_names.items():
                dept_job_names[dept_id] = existing_names.copy()
            
            for idx, job_title in enumerate(job_titles):
                # Distribute jobs across departments, ensuring unique names per department
                dept = departments[idx % len(departments)]
                dept_id = dept.get("id")
                
                # Initialize department tracking if needed
                if dept_id not in dept_job_names:
                    dept_job_names[dept_id] = set()
                
                # Make job name unique per department by adding suffix if needed
                # Compare lowercase to handle case-insensitive uniqueness
                unique_job_title = job_title
                suffix = 1
                while unique_job_title.lower() in dept_job_names[dept_id]:
                    unique_job_title = f"{job_title} ({suffix})"
                    suffix += 1
                dept_job_names[dept_id].add(unique_job_title.lower())
                
                # Get job summary from Gemini (use original title for context)
                job_summary = gemini_client.fetch_job_summary(job_title, industry, gemini_model_name, language_name)
                
                # Select random skills for this job (2-4 skills)
                job_skills = []
                if all_skill_ids:
                    num_job_skills = random.randint(2, min(4, len(all_skill_ids)))
                    job_skills = random.sample(all_skill_ids, num_job_skills)
                
                # Random target between 1 and 5
                target = random.randint(1, 5)
                
                job_id = odoo_actions.create_job(
                    client, unique_job_title, dept_id, target=target,
                    description=job_summary, job_skill_ids=job_skills
                )
                job_ids.append(job_id)
            
            # Create candidates and assign to jobs
            if num_candidates > 0 and job_ids:
                print("\n--- RECRUITING: Erstelle Bewerber ---")
                candidate_names = recruiting_data.get("candidate_names", [])[:num_candidates]
                candidate_emails = recruiting_data.get("candidate_emails", [])[:num_candidates]
                candidate_phones = recruiting_data.get("candidate_phones", [])[:num_candidates]
                
                # Fill missing data with fallbacks
                while len(candidate_names) < num_candidates:
                    candidate_names.append(f"Bewerber {len(candidate_names) + 1}")
                while len(candidate_emails) < num_candidates:
                    candidate_emails.append(f"bewerber{len(candidate_emails) + 1}@example.com")
                while len(candidate_phones) < num_candidates:
                    candidate_phones.append(f"+49 {random.randint(100, 999)} {random.randint(1000000, 9999999)}")
                
                # Get stages for each job
                job_stages_map = {}
                for job_id in job_ids:
                    stages = odoo_actions.get_job_stages(client, job_id)
                    job_stages_map[job_id] = stages if stages else []
                
                # Distribute candidates across jobs
                candidates_per_job = num_candidates // len(job_ids)
                remaining_candidates = num_candidates % len(job_ids)
                
                candidate_idx = 0
                for job_idx, job_id in enumerate(job_ids):
                    num_for_job = candidates_per_job + (1 if job_idx < remaining_candidates else 0)
                    stages = job_stages_map.get(job_id, [])
                    
                    for _ in range(num_for_job):
                        if candidate_idx >= num_candidates:
                            break
                        
                        name = candidate_names[candidate_idx]
                        email = candidate_emails[candidate_idx]
                        phone = candidate_phones[candidate_idx]
                        
                        # Select random skills for candidate (1-3 skills)
                        candidate_skills = []
                        if all_skill_ids:
                            num_candidate_skills = random.randint(1, min(3, len(all_skill_ids)))
                            candidate_skills = random.sample(all_skill_ids, num_candidate_skills)
                        
                        # Assign to random stage if available
                        stage_id = None
                        if stages:
                            stage = random.choice(stages)
                            stage_id = stage.get("id")
                        
                        odoo_actions.create_applicant(
                            client, job_id, name, email, phone,
                            skill_ids=candidate_skills, stage_id=stage_id
                        )
                        candidate_idx += 1
                
                print(f"✅ {num_candidates} Bewerber erstellt und auf {len(job_ids)} Stellen verteilt")

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

    # Manufacturing data is already created above (before sales orders)
    # This section is intentionally removed to avoid duplication

    # ==============================================================================
    # CREATE ACTIVITIES (as last step)
    # ==============================================================================
    if module_selections.get("create_activities", False):
        print("\n--- AKTIVITÄTEN: Erstelle Mail Activities ---")
        from datetime import datetime, timedelta
        
        # Get activity types
        activity_types = odoo_actions.get_activity_types(client)
        if not activity_types:
            print("⚠️  Keine Aktivitätstypen gefunden. Überspringe Aktivitäten-Erstellung.")
        else:
            # Get current user for activities
            current_user_id = odoo_actions.get_current_user_id(client)
            
            # Activity summaries based on record type
            activity_summaries = {
                'crm.lead': ['Follow-up Meeting', 'Angebot versenden', 'Kundentermin vereinbaren', 'Nachfassaktion'],
                'hr.applicant': ['Interview terminieren', 'Bewerbungsunterlagen prüfen', 'Referenzen einholen', 'Entscheidung treffen'],
                'project.task': ['Status-Update', 'Code Review', 'Testing durchführen', 'Dokumentation aktualisieren'],
                'res.partner': ['Kontaktaufnahme', 'Meeting vereinbaren', 'Angebot senden', 'Follow-up Call']
            }
            
            # Get today's date
            today = datetime.now().date()
            
            # 1. CRM Opportunities
            if "crm" in installed:
                opportunities = client.search_read(
                    'crm.lead',
                    [],
                    fields=["id", "name"],
                    limit=0
                )
                if opportunities:
                    print(f"-> Erstelle Aktivitäten für {len(opportunities)} Opportunities")
                    summaries = activity_summaries.get('crm.lead', ['Follow-up'])
                    for opp in opportunities:
                        opp_id = opp.get("id")
                        # Debug: Check what we got
                        if opp_id is None:
                            continue
                        if opp_id == 0:
                            continue
                        # Handle tuple/list format
                        if isinstance(opp_id, (list, tuple)) and len(opp_id) > 0:
                            opp_id = opp_id[0]
                        if not isinstance(opp_id, int) or opp_id <= 0:
                            continue
                        
                        # Create 1-3 activities per record with different activity types
                        num_activities = random.randint(1, 3)
                        used_activity_types = set()  # Track used types to ensure variety
                        
                        for _ in range(num_activities):
                            # Random deadline: past (max 5 days), today, or future (max 10 days)
                            days_offset = random.choice([
                                random.randint(-5, -1),  # Past
                                0,  # Today
                                random.randint(1, 10)  # Future
                            ])
                            deadline = today + timedelta(days=days_offset)
                            
                            # Select a different activity type if we have multiple
                            available_types = [at for at in activity_types if at.get("id") not in used_activity_types]
                            if not available_types:
                                # If all types used, reset and allow reuse
                                available_types = activity_types
                                used_activity_types.clear()
                            
                            activity_type = random.choice(available_types)
                            activity_type_id = activity_type.get("id")
                            if not activity_type_id:
                                continue
                            # Handle tuple/list format
                            if isinstance(activity_type_id, (list, tuple)) and len(activity_type_id) > 0:
                                activity_type_id = activity_type_id[0]
                            if not isinstance(activity_type_id, int) or activity_type_id <= 0:
                                continue
                            
                            used_activity_types.add(activity_type_id)
                            summary = random.choice(summaries)
                            
                            try:
                                odoo_actions.create_activity(
                                    client, 'crm.lead', opp_id,
                                    activity_type_id, summary,
                                    deadline.strftime("%Y-%m-%d"), user_id=current_user_id
                                )
                            except Exception as e:
                                print(f"   ⚠️  Fehler beim Erstellen der Aktivität für Opportunity {opp_id}: {e}")
            
            # 2. HR Applicants
            if "hr_recruitment" in installed:
                applicants = client.search_read(
                    'hr.applicant',
                    [],
                    fields=["id", "partner_name"],
                    limit=0
                )
                if applicants:
                    print(f"-> Erstelle Aktivitäten für {len(applicants)} Bewerber")
                    summaries = activity_summaries.get('hr.applicant', ['Follow-up'])
                    for applicant in applicants:
                        applicant_id = applicant.get("id")
                        if applicant_id is None or applicant_id == 0:
                            continue
                        # Handle tuple/list format
                        if isinstance(applicant_id, (list, tuple)) and len(applicant_id) > 0:
                            applicant_id = applicant_id[0]
                        if not isinstance(applicant_id, int) or applicant_id <= 0:
                            continue
                        
                        # Create 1-3 activities per record with different activity types
                        num_activities = random.randint(1, 3)
                        used_activity_types = set()
                        
                        for _ in range(num_activities):
                            days_offset = random.choice([
                                random.randint(-5, -1),
                                0,
                                random.randint(1, 10)
                            ])
                            deadline = today + timedelta(days=days_offset)
                            
                            # Select a different activity type if we have multiple
                            available_types = [at for at in activity_types if at.get("id") not in used_activity_types]
                            if not available_types:
                                available_types = activity_types
                                used_activity_types.clear()
                            
                            activity_type = random.choice(available_types)
                            activity_type_id = activity_type.get("id")
                            if not activity_type_id:
                                continue
                            # Handle tuple/list format
                            if isinstance(activity_type_id, (list, tuple)) and len(activity_type_id) > 0:
                                activity_type_id = activity_type_id[0]
                            if not isinstance(activity_type_id, int) or activity_type_id <= 0:
                                continue
                            
                            used_activity_types.add(activity_type_id)
                            summary = random.choice(summaries)
                            
                            try:
                                odoo_actions.create_activity(
                                    client, 'hr.applicant', applicant_id,
                                    activity_type_id, summary,
                                    deadline.strftime("%Y-%m-%d"), user_id=current_user_id
                                )
                            except Exception as e:
                                print(f"   ⚠️  Fehler beim Erstellen der Aktivität für Bewerber {applicant_id}: {e}")
            
            # 3. Project Tasks
            if "project" in installed:
                tasks = client.search_read(
                    'project.task',
                    [],
                    fields=["id", "name"],
                    limit=0
                )
                if tasks:
                    print(f"-> Erstelle Aktivitäten für {len(tasks)} Aufgaben")
                    summaries = activity_summaries.get('project.task', ['Follow-up'])
                    for task in tasks:
                        task_id = task.get("id")
                        if task_id is None or task_id == 0:
                            continue
                        # Handle tuple/list format
                        if isinstance(task_id, (list, tuple)) and len(task_id) > 0:
                            task_id = task_id[0]
                        if not isinstance(task_id, int) or task_id <= 0:
                            continue
                        
                        # Create 1-3 activities per record with different activity types
                        num_activities = random.randint(1, 3)
                        used_activity_types = set()
                        
                        for _ in range(num_activities):
                            days_offset = random.choice([
                                random.randint(-5, -1),
                                0,
                                random.randint(1, 10)
                            ])
                            deadline = today + timedelta(days=days_offset)
                            
                            # Select a different activity type if we have multiple
                            available_types = [at for at in activity_types if at.get("id") not in used_activity_types]
                            if not available_types:
                                available_types = activity_types
                                used_activity_types.clear()
                            
                            activity_type = random.choice(available_types)
                            activity_type_id = activity_type.get("id")
                            if not activity_type_id:
                                continue
                            # Handle tuple/list format
                            if isinstance(activity_type_id, (list, tuple)) and len(activity_type_id) > 0:
                                activity_type_id = activity_type_id[0]
                            if not isinstance(activity_type_id, int) or activity_type_id <= 0:
                                continue
                            
                            used_activity_types.add(activity_type_id)
                            summary = random.choice(summaries)
                            
                            try:
                                odoo_actions.create_activity(
                                    client, 'project.task', task_id,
                                    activity_type_id, summary,
                                    deadline.strftime("%Y-%m-%d"), user_id=current_user_id
                                )
                            except Exception as e:
                                print(f"   ⚠️  Fehler beim Erstellen der Aktivität für Aufgabe {task_id}: {e}")
            
            # 4. Partners (Contacts)
            partners = client.search_read(
                'res.partner',
                [["is_company", "=", False]],  # Only individual contacts, not companies
                fields=["id", "name"],
                limit=50  # Limit to avoid too many activities
            )
            if partners:
                print(f"-> Erstelle Aktivitäten für {len(partners)} Kontakte")
                summaries = activity_summaries.get('res.partner', ['Follow-up'])
                for partner in partners:
                    partner_id = partner.get("id")
                    if partner_id is None or partner_id == 0:
                        continue
                    # Handle tuple/list format
                    if isinstance(partner_id, (list, tuple)) and len(partner_id) > 0:
                        partner_id = partner_id[0]
                    if not isinstance(partner_id, int) or partner_id <= 0:
                        continue
                    
                    # Create 1-3 activities per record with different activity types
                    num_activities = random.randint(1, 3)
                    used_activity_types = set()
                    
                    for _ in range(num_activities):
                        days_offset = random.choice([
                            random.randint(-5, -1),
                            0,
                            random.randint(1, 10)
                        ])
                        deadline = today + timedelta(days=days_offset)
                        
                        # Select a different activity type if we have multiple
                        available_types = [at for at in activity_types if at.get("id") not in used_activity_types]
                        if not available_types:
                            available_types = activity_types
                            used_activity_types.clear()
                        
                        activity_type = random.choice(available_types)
                        activity_type_id = activity_type.get("id")
                        if not activity_type_id:
                            continue
                        # Handle tuple/list format
                        if isinstance(activity_type_id, (list, tuple)) and len(activity_type_id) > 0:
                            activity_type_id = activity_type_id[0]
                        if not isinstance(activity_type_id, int) or activity_type_id <= 0:
                            continue
                        
                        used_activity_types.add(activity_type_id)
                        summary = random.choice(summaries)
                        
                        try:
                            odoo_actions.create_activity(
                                client, 'res.partner', partner_id,
                                activity_type_id, summary,
                                deadline.strftime("%Y-%m-%d"), user_id=current_user_id
                            )
                        except Exception as e:
                            print(f"   ⚠️  Fehler beim Erstellen der Aktivität für Kontakt {partner_id}: {e}")
            
            print("✅ Aktivitäten-Erstellung abgeschlossen")

    # Create vendor invoices (bills)
    if "account" in installed and num_invoices > 0:
        print("\n--- ACCOUNTING: Erstelle Eingangsrechnungen ---")
        # Get supplier names from environment (generated by Gemini) or use fallback
        env_suppliers = os.environ.get('NAMES_SUPPLIER', '')
        supplier_names = [s for s in env_suppliers.split('||') if s] if env_suppliers else [
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
        # Setup connections first (needed to get company name)
        print("🚀 Willkommen beim Odoo Demo-Daten Assistenten!")
        connections = setup_connections()
        
        # Get main company name and determine industry
        print("\n--- Branche erkennen ---")
        company_name = odoo_actions.get_main_company_name(connections['client'])
        suggested_industry = None
        if company_name:
            print(f"✅ Gefundener Firmenname: {company_name}")
            suggested_industry = gemini_client.determine_industry_from_company_name(
                company_name, 
                connections['gemini_model_name']
            )
            if suggested_industry:
                print(f"✅ Vorgeschlagene Branche: {suggested_industry}")
            else:
                print("⚠️  Konnte Branche nicht automatisch bestimmen.")
        else:
            print("⚠️  Konnte Firmenname nicht ermitteln.")
        
        # Run wizard with suggested industry as default
        criteria = run_interactive_wizard(default_industry=suggested_industry)
        
        os.environ["INDUSTRY"] = criteria.get('industry', suggested_industry or 'IT')

        # Detect language from main company or API user
        print("\n--- Sprache erkennen ---")
        lang_code = odoo_actions.get_main_company_language(connections['client'])
        language_name = gemini_client.get_language_name(lang_code)
        print(f"✅ Erkannte Sprache: {lang_code} ({language_name})")
        os.environ["DETECTED_LANGUAGE"] = lang_code
        os.environ["LANGUAGE_NAME"] = language_name

        # Detect installed modules and ask user for selections
        print("\n--- Installierte Module erkennen ---")
        desired_modules = ["crm", "sale", "account", "hr", "project", "hr_timesheet", "mrp", "hr_recruitment"]
        installed_modules = odoo_actions.get_installed_modules(connections['client'], desired_modules)
        if installed_modules:
            print(f"✅ Gefundene installierte Module: {', '.join([m.upper() for m in installed_modules])}")
        else:
            print("⚠️  Keine der erwarteten Module sind installiert.")
        
        # Ask user which modules to create data for (includes tracking selection)
        module_selections = ask_module_selections(installed_modules, connections['client'])
        
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
        _to_env_list('NAMES_SUPPLIER', name_suggestions.get('supplier_names', []))
        
        # Extract tracking selections from module_selections (already asked in ask_module_selections)
        tracking_selections = {
            "use_tracking": module_selections.get("use_tracking", False),
            "lot_enabled": module_selections.get("lot_enabled", False),
            "serial_enabled": module_selections.get("serial_enabled", False)
        }
        
        created_ids = populate_odoo_with_data(
            creative_data, 
            criteria, 
            connections['client'],
            connections['gemini_model_name'],
            language_name,
            tracking_selections
        )
        
        # Get category_ids and UOMs for module demo data
        industry = criteria.get('industry', 'IT')
        category_ids = odoo_actions.create_product_categories(connections['client'], industry, language_name)
        existing_uoms = odoo_actions.get_existing_uoms(connections['client'])
        existing_barcodes = odoo_actions.get_existing_barcodes(connections['client'])
        existing_default_codes = odoo_actions.get_existing_default_codes(connections['client'])
        
        create_module_demo_data(
            connections['client'], 
            created_ids, 
            connections['gemini_model_name'], 
            language_name, 
            module_selections,
            category_ids,
            existing_uoms,
            existing_barcodes,
            existing_default_codes
        )
        
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