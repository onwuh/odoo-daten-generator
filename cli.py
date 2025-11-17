#!/usr/bin/env python3
"""
CLI-Einstiegspunkt für den Demo-Daten-Assistenten.
Verwendet die Modulstruktur odoo_demo_data_assistant.
"""
import sys
import os
import getpass
import configparser
import questionary

# Füge das aktuelle Verzeichnis zum Python-Pfad hinzu
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from odoo_demo_data_assistant.services import odoo_client, odoo_actions, gemini_client, wizard


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
    client = odoo_client.OdooJson2Client(url, db, api_key)
    print("✅ Odoo JSON 2 Client initialisiert.\n")
    
    return {
        "client": client,
        "gemini_model_name": gemini_model_name
    }


if __name__ == "__main__":
    # Hinweis: Die vollständige CLI-Implementierung würde hier die gesamte Logik aus connect.py enthalten
    # Für jetzt ist dies ein Platzhalter, der zeigt, wie die Modulstruktur verwendet wird
    print("🚀 Demo-Daten-Assistent (CLI)")
    print("⚠️  Hinweis: Die vollständige CLI-Implementierung wird in einem späteren Schritt hinzugefügt.")
    print("   Bitte verwenden Sie vorerst connect.py für die vollständige Funktionalität.")
    print("\nDie Modulstruktur ist bereit für die Integration in Odoo.")
