import ssl
import getpass
import os
import configparser
import questionary
import odoo_actions
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
        return

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

# ==============================================================================
#  HAUPT-SKRIPT
# ==============================================================================
if __name__ == "__main__":
    try:
        criteria = run_interactive_wizard()
        connections = setup_connections()
        
        creative_data = gemini_client.fetch_creative_data(
            criteria, 
            connections['gemini_model_name']
        )
        
        populate_odoo_with_data(
            creative_data,
            criteria,
            connections['client'],
        )
        
        print("\n" + "-"*30)
        print("✅ Alle Aktionen erfolgreich abgeschlossen!")

    except Exception as e:
        print(f"❌ Ein kritischer Fehler ist aufgetreten: {e}")