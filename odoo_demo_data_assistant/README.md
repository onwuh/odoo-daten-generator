# Odoo Demo Data Assistant

Odoo-Modul zur Generierung umfangreicher Demo-Daten in Odoo.

## Installation

1. Kopieren Sie das Modul in Ihr Odoo-Addons-Verzeichnis:
   ```bash
   cp -r odoo_demo_data_assistant /path/to/odoo/addons/
   ```

2. Installieren Sie die Python-Abhängigkeiten:
   ```bash
   pip install google-generativeai questionary requests
   ```

3. Installieren Sie das Modul in Odoo:
   - Gehen Sie zu Apps → Aktualisieren → Demo Data Assistant → Installieren

## Struktur

```
odoo_demo_data_assistant/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   └── demo_data_assistant.py      # Odoo Model (für UI-Integration)
├── services/
│   ├── __init__.py
│   ├── odoo_client.py              # Odoo JSON 2 API Client
│   ├── odoo_actions.py              # Odoo-Aktionen (CRUD)
│   ├── gemini_client.py             # Gemini AI Integration
│   └── wizard.py                    # Hauptlogik für Daten-Generierung
└── static/
    └── description/
        └── icon.png                 # (optional) Modul-Icon
```

## Verwendung

### In Odoo

1. Öffnen Sie das Menü **Anpassungen → Demo Data Assistant** (oder suchen Sie nach "Demo Data Assistant").
2. Tragen Sie den Gemini API Key ein und konfigurieren Sie die gewünschten Parameter.
3. Klicken Sie auf **Demo-Daten erzeugen**. Nach erfolgreichem Lauf erhalten Sie eine Benachrichtigung mit einer Kurzzusammenfassung.

Der Wizard speichert den API Key optional verschlüsselt in den Systemparametern, damit Folgeausführungen schneller möglich sind.

### Über CLI

Parallel kann weiterhin `cli.py` oder `connect.py` genutzt werden, wenn eine Ausführung außerhalb von Odoo gewünscht ist.

## Architektur

- `services/wizard.py` – Enthält die Kernlogik zur Daten-Erzeugung.
- `services/odoo_actions.py` – Sammlung von CRUD-Helfern für das Odoo-Datenmodell.
- `services/env_client.py` – Adapter, der die Service-Layer-Funktionen direkt gegen das Odoo-ORM ausführbar macht.
- `services/gemini_client.py` – Anbindung an Google Gemini (Prompt-Aufrufe).

## Zukunft

- Erweiterung des Wizards um weitere Module/Optionen (z.B. Recruiting, MRP, Aktivitäten).
- Eigene Einstellungen im Backend (z.B. Menüpunkt in den technischen Einstellungen).
- Dedizierte Tests (Unit- & Integrationstests) für die wichtigsten Codepfade.
