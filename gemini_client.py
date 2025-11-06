import google.generativeai as genai
import json
import signal
from typing import Dict, Any, List

class TimeoutException(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutException("Gemini request timed out")

## Updated Prompt Builder
def build_prompt_from_criteria(criteria: Dict[str, Any]) -> str:
    """Erstellt den Gemini-Prompt mit detaillierten Anweisungen."""
    
    prompt = f"""
    Erstelle fiktive, realistische Demodaten für Odoo basierend auf diesen Kriterien:
    - Branche: {criteria['industry']}
    - Anzahl der Firmen: {criteria['num_companies']}

    Gib NUR ein sauberes JSON-Objekt zurück.
    Das JSON muss die Struktur {{"companies": [...], "products":{{...}} }} haben.

    Für jede Firma, beachte folgende Regeln:
    1.  Die Firma selbst ("company_data") muss eine vollständige Adresse haben.
    2.  Erstelle exakt {criteria['num_delivery_contacts']} Kontakte vom Typ 'delivery'.
        - Der 'name' dieser Kontakte soll ein Ort sein (z.B. "Lagerhalle West", "Hauptsitz Berlin").
        - Diese Kontakte MÜSSEN eine eigene, vollständige Adresse haben ("street", "city", "zip", "country_code").
    3.  Erstelle exakt {criteria['num_invoice_contacts']} Kontakte vom Typ 'invoice'.
        - Diese Kontakte sollen KEINEN 'name' haben, nur den Typ.
    4.  Erstelle exakt {criteria['num_other_contacts']} Kontakte vom Typ 'other'.
        - Der 'name' dieser Kontakte soll ein voller Personenname sein (z.B. "Sabine Schmidt").
    5.  Alle E-Mail-Adressen MÜSSEN die Domain "@example.com" verwenden.

    Erstelle außerdem die von mir angeforderte Anzahl an Produkten.
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
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass