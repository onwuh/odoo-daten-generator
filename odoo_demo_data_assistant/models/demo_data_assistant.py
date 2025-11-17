"""
Odoo Model für Demo-Daten-Assistenten.
Dieses Modell kann von Server-Aktionen oder der UI aufgerufen werden.
"""
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class DemoDataAssistant(models.TransientModel):
    """
    Transient Model für die Demo-Daten-Generierung.
    Kann von Server-Aktionen oder der UI aufgerufen werden.
    """
    _name = 'demo.data.assistant'
    _description = 'Demo Data Assistant'

    # TODO: In späteren Schritten werden hier Felder für die UI hinzugefügt
    # z.B. industry, num_companies, etc.

    def action_generate_demo_data(self):
        """
        Hauptmethode zur Generierung von Demo-Daten.
        Wird später mit UI-Feldern erweitert.
        """
        _logger.info("Demo-Daten-Generierung gestartet")
        
        # TODO: Implementierung folgt in späteren Schritten
        # Hier wird die Logik aus wizard.py integriert
        
        raise UserError("Diese Funktion wird in einem späteren Schritt implementiert. "
                       "Bitte verwenden Sie vorerst die CLI-Version.")
