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

### Als Odoo-Modul

Das Modul kann über Server-Aktionen oder eine zukünftige UI-Oberfläche verwendet werden.

### Als CLI-Tool

Für die Verwendung als CLI-Tool verwenden Sie weiterhin `connect.py` oder die zukünftige `cli.py`.

## Entwicklung

Die Modulstruktur ist bereit für die UI-Integration. Die Hauptlogik befindet sich in:
- `services/wizard.py` - Hauptlogik für Daten-Generierung
- `services/odoo_actions.py` - Odoo CRUD-Operationen
- `services/gemini_client.py` - Gemini AI Integration

## Nächste Schritte

1. UI-Integration: Erstellen von Views und Wizards für die Odoo-Oberfläche
2. Server-Aktionen: Bereitstellung von Server-Aktionen für die Daten-Generierung
3. Konfiguration: Hinzufügen von Konfigurationsmöglichkeiten im Odoo-Settings
