import google.generativeai as genai
import json
import signal
from typing import Dict, Any, List

# Timeout handling for the API request
class TimeoutException(Exception): pass
def timeout_handler(signum, frame):
    raise TimeoutException("Die API-Anfrage hat das Zeitlimit überschritten.")


def build_prompt_from_criteria(criteria: Dict[str, Any]) -> str:
    """Creates the Gemini prompt from the collected criteria."""
    prompt = f"""
    Erstelle fiktive, aber realistische Demodaten für Odoo basierend auf diesen Kriterien:
    - Branche: {criteria['industry']}
    - Anzahl der Firmen: {criteria['num_companies']}
    - Pro Firma, erstelle:
        - {criteria['num_delivery_contacts']} Lieferadressen
        - {criteria['num_invoice_contacts']} Rechnungsadressen
        - {criteria['num_other_contacts']} sonstige Kontakte
    - Erstelle außerdem {criteria['num_services']} Dienstleistungen, {criteria['num_consumables']} Verbrauchsprodukte, und {criteria['num_storables']} lagerfähige Produkte.

    Gib NUR ein sauberes JSON-Objekt zurück.
    Das JSON muss die Struktur {{"companies": [...], "products":{{"services": [...], "consumables": [...], "storables": [...]}} }} haben.
    Jedes Firmen-Objekt in der 'companies'-Liste muss enthalten:
    {{
      "company_data": {{ "name": "...", "email": "...", "phone": "...", "street": "...", "city": "...", "zip": "...", "country_code": "DE" }},
      "contacts": [ /* eine Liste mit allen oben angeforderten Kontakt-Typen */ ]
    }}
    Jeder Kontakt in der 'contacts'-Liste muss "name" und "type": "delivery", "invoice" oder "other" enthalten.
    Jedes Produktobjekt sollte "name" und "list_price" enthalten.
    """
    return prompt


def fetch_creative_data(criteria: Dict[str, Any], gemini_model_name: str) -> Dict[str, Any] | None:
    """Builds the prompt, queries Gemini, and returns the parsed data."""
    prompt = build_prompt_from_criteria(criteria)
    
    print(f"Frage Gemini ({gemini_model_name}) nach kreativen Daten...")
    model = genai.GenerativeModel(gemini_model_name)
    
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        signal.alarm(120) 
        response = model.generate_content(prompt)
        signal.alarm(0) 
        
        json_text = response.text.strip().replace("```json", "").replace("```", "")
        creative_data = json.loads(json_text)
        print("✅ Kreative Daten von Gemini empfangen.")
        return creative_data
    except TimeoutException as e:
        print(f"❌ Zeitüberschreitung bei der Gemini-Anfrage: {e}")
        return None
    except json.JSONDecodeError:
        print("❌ Fehler: Gemini hat ungültiges JSON zurückgegeben.")
        return None