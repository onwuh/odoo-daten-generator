"""
Odoo Model & Wizard für den Demo-Daten-Assistenten.
"""
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import wizard as wizard_service
from ..services import gemini_client, odoo_actions, env_client

_logger = logging.getLogger(__name__)


class DemoDataAssistantWizard(models.TransientModel):
    """Transient Wizard, der alle Eingaben für die Demo-Daten-Erzeugung sammelt."""

    _name = "demo.data.assistant.wizard"
    _description = "Demo Data Assistant Wizard"

    # ------------------------------------------------------------------
    # Gemini / Konnektivität
    # ------------------------------------------------------------------
    gemini_api_key = fields.Char(
        string="Gemini API Key",
        required=True,
        help="API-Schlüssel für Google Gemini (1.5 Flash empfohlen).",
        password=True,
        default=lambda self: self._default_gemini_api_key(),
    )
    store_gemini_api_key = fields.Boolean(
        string="API Key dauerhaft speichern",
        help="Speichert den Schlüssel verschlüsselt in den Systemparametern."
    )
    gemini_model_name = fields.Char(
        string="Gemini Modell",
        required=True,
        default=lambda self: self._default_gemini_model(),
    )

    # ------------------------------------------------------------------
    # Basis-Konfiguration
    # ------------------------------------------------------------------
    mode = fields.Selection(
        [
            ("master_only", "Nur Stammdaten (Partner & Produkte)"),
            ("master_and_moves", "Stamm- und Bewegungsdaten"),
        ],
        string="Modus",
        default="master_and_moves",
        required=True,
    )
    industry = fields.Char(
        string="Branche",
        required=True,
        default="IT-Dienstleistung",
    )
    num_companies = fields.Integer(string="Hauptkunden", default=1)
    num_delivery_contacts = fields.Integer(string="Lieferadressen je Kunde", default=1)
    num_invoice_contacts = fields.Integer(string="Rechnungsadressen je Kunde", default=1)
    num_other_contacts = fields.Integer(string="Weitere Ansprechpartner je Kunde", default=1)
    num_services = fields.Integer(string="Dienstleistungs-Produkte", default=1)
    num_consumables = fields.Integer(string="Verbrauchs-Produkte", default=1)
    num_storables = fields.Integer(string="Lagerfähige Produkte", default=1)

    # Tracking-Optionen
    use_tracking = fields.Boolean(string="Produkte mit Nummernverfolgung erstellen", default=False)
    tracking_lot_enabled = fields.Boolean(string="Losnummern", default=False)
    tracking_serial_enabled = fields.Boolean(string="Seriennummern", default=False)

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------
    @api.model
    def _default_gemini_api_key(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "odoo_demo_data_assistant.gemini_api_key", ""
        )

    @api.model
    def _default_gemini_model(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "odoo_demo_data_assistant.gemini_model_name", "gemini-1.5-flash"
        )

    def _get_env_client(self):
        return env_client.OdooEnvClient(self.env)

    def _build_tracking_payload(self):
        return {
            "use_tracking": self.use_tracking,
            "lot_enabled": self.tracking_lot_enabled,
            "serial_enabled": self.tracking_serial_enabled,
        }

    def _build_criteria(self):
        """Abbildung der Wizard-Felder auf die Datenstruktur aus dem CLI."""
        return {
            "mode": "Stammdaten anlegen UND Bewegungsdaten (Angebote) erstellen"
            if self.mode == "master_and_moves"
            else "Nur Stammdaten anlegen (Kunden, Produkte)",
            "industry": self.industry or "IT-Dienstleistung",
            "num_companies": self.num_companies,
            "num_delivery_contacts": self.num_delivery_contacts,
            "num_invoice_contacts": self.num_invoice_contacts,
            "num_other_contacts": self.num_other_contacts,
            "num_services": self.num_services,
            "num_consumables": self.num_consumables,
            "num_storables": self.num_storables,
        }

    def _store_params_if_needed(self):
        if not self.store_gemini_api_key:
            return
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("odoo_demo_data_assistant.gemini_api_key", self.gemini_api_key)
        param.set_param("odoo_demo_data_assistant.gemini_model_name", self.gemini_model_name)

    # ------------------------------------------------------------------
    # Aktion
    # ------------------------------------------------------------------
    def action_generate_demo_data(self):
        self.ensure_one()

        api_key = self.gemini_api_key.strip()
        if not api_key:
            raise UserError("Bitte einen gültigen Gemini API Key angeben.")

        self._store_params_if_needed()

        gemini_client.genai.configure(api_key=api_key)

        client = self._get_env_client()

        lang_code = odoo_actions.get_main_company_language(client)
        language_name = gemini_client.get_language_name(lang_code)

        criteria = self._build_criteria()
        tracking = self._build_tracking_payload()

        creative_data = gemini_client.fetch_creative_data(criteria, self.gemini_model_name)
        if not creative_data:
            raise UserError("Gemini hat keine Daten zurückgegeben. Bitte Eingaben prüfen.")

        result = wizard_service.populate_odoo_with_data(
            creative_data=creative_data,
            criteria=criteria,
            client=client,
            gemini_model_name=self.gemini_model_name,
            language_name=language_name,
            tracking_selections=tracking,
        )

        message = self._format_result_message(result)
        _logger.info("Demo-Daten-Assistent erfolgreich abgeschlossen: %s", message)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Demo-Daten erstellt",
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------
    def _format_result_message(self, result):
        products = len(result.get("product_ids", []))
        partners = len(result.get("company_ids", []))
        orders = len(result.get("order_ids", []))
        return (
            f"{products} Produkte • "
            f"{partners} Kunden • "
            f"{orders} Bewegungsdatensätze"
        )
