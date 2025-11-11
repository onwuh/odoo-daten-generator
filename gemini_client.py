import google.generativeai as genai
import json
import signal
from typing import Dict, Any, List, Optional

def get_language_name(lang_code: str) -> str:
    """Convert Odoo language code to language name for prompts."""
    lang_map = {
        'de_DE': 'German',
        'en_US': 'English',
        'fr_FR': 'French',
        'es_ES': 'Spanish',
        'it_IT': 'Italian',
        'nl_NL': 'Dutch',
        'pt_PT': 'Portuguese',
        'pl_PL': 'Polish',
        'cs_CZ': 'Czech',
        'ru_RU': 'Russian',
    }
    # Extract base language (e.g., de_DE -> de)
    base_lang = lang_code.split('_')[0].lower() if '_' in lang_code else lang_code.lower()
    # Map common bases
    base_map = {
        'de': 'German',
        'en': 'English',
        'fr': 'French',
        'es': 'Spanish',
        'it': 'Italian',
        'nl': 'Dutch',
        'pt': 'Portuguese',
        'pl': 'Polish',
        'cs': 'Czech',
        'ru': 'Russian',
    }
    return lang_map.get(lang_code, base_map.get(base_lang, 'German'))

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
    6.  WICHTIG: Erstelle KEINE USt-IdNr. (VAT ID / vat / vat_id Felder) - diese werden nicht benötigt.

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

def build_names_prompt(criteria: Dict[str, Any], language: str = "German") -> str:
    industry = criteria.get('industry', 'IT')
    return f"""
    Based on the industry "{industry}", generate ONLY a JSON object with arrays of realistic {language} names:
    {{
      "product_names": [min 25 strings],
      "employee_names": [min 40 full person names],
      "company_names": [min 25 company names],
      "project_names": [min 25 project titles],
      "task_names": [min 50 concise task names],
      "opportunity_titles": [min 25 sales opportunity titles]
    }}
    - No code blocks, no backticks, no comments, only valid compact JSON.
    - Names must fit the given industry.
    """

def fetch_name_suggestions(criteria: Dict[str, Any], gemini_model_name: str, language: str = "German") -> Dict[str, List[str]] | None:
    prompt = build_names_prompt(criteria, language)
    print(f"Frage Gemini ({gemini_model_name}) nach Namensvorschlägen...")
    model = genai.GenerativeModel(gemini_model_name)
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        signal.alarm(90)
        response = model.generate_content(prompt)
        signal.alarm(0)
        json_text = response.text.strip().replace("```json", "").replace("```", "")
        data = json.loads(json_text)
        print("✅ Namensvorschläge von Gemini empfangen.")
        return data
    except TimeoutException as e:
        print(f"❌ Zeitüberschreitung bei der Gemini-Namensanfrage: {e}")
        return None
    except json.JSONDecodeError:
        print("❌ Fehler: Gemini hat ungültiges JSON zurückgegeben.")
        return None
    except Exception as e:
        print(f"❌ Fehler bei Namensvorschlägen: {e}")
        return None
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass

def build_stage_names_prompt(industry: str, project_name: str = None, language: str = "German") -> str:
    """Build prompt for generating project stage names based on industry and project."""
    project_context = f" for project '{project_name}'" if project_name else ""
    return f"""
    Based on the industry "{industry}"{project_context}, generate ONLY a JSON array with 6-8 creative {language} project stage names.
    The stages should represent a logical workflow progression for this industry.
    Example format: ["Kickoff", "Analyse & Planung", "Entwicklung", "Testing", "Deployment", "Abnahme"]
    
    Return ONLY a JSON array like: ["Stage 1", "Stage 2", ...]
    - No code blocks, no backticks, no comments, only valid compact JSON array.
    - Names must be realistic and fit the industry context.
    - Use {language} language.
    """

def fetch_project_stage_names(industry: str, project_name: str = None, gemini_model_name: str = "gemini-1.5-flash", language: str = "German") -> List[str] | None:
    """Fetch creative project stage names from Gemini based on industry."""
    prompt = build_stage_names_prompt(industry, project_name, language)
    print(f"Frage Gemini ({gemini_model_name}) nach Projektphasen-Namen für {industry}...")
    model = genai.GenerativeModel(gemini_model_name)
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        signal.alarm(60)
        response = model.generate_content(prompt)
        signal.alarm(0)
        json_text = response.text.strip().replace("```json", "").replace("```", "")
        data = json.loads(json_text)
        if isinstance(data, list):
            print(f"✅ Projektphasen-Namen von Gemini empfangen: {len(data)} Phasen")
            return data
        else:
            print("❌ Gemini hat kein Array zurückgegeben.")
            return None
    except TimeoutException as e:
        print(f"❌ Zeitüberschreitung bei der Gemini-Phasenanfrage: {e}")
        return None
    except json.JSONDecodeError:
        print("❌ Fehler: Gemini hat ungültiges JSON zurückgegeben.")
        return None
    except Exception as e:
        print(f"❌ Fehler bei Phasennamen: {e}")
        return None
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass

def build_bom_component_prompt(main_product_name: str, count: int, industry: str | None = None, language: str = "German") -> str:
    """Build prompt to request BOM component names tailored to a product."""
    industry_context = f" in the {industry} industry" if industry else ""
    return f"""
    You act as a senior manufacturing engineer naming components for a bill of materials{industry_context}.
    The main product is "{main_product_name}".
    Provide ONLY a JSON array with exactly {count} distinct {language} component names.
    - Each component name must relate clearly to the main product (e.g. include functional hints, variants, or stages).
    - Names must be realistic manufacturing sub-assemblies or parts.
    - Avoid numbering unless it adds clarity; keep names concise (max 6 words).
    - Return strictly a JSON array like ["Component A", "Component B"] with {count} entries.
    - No code blocks, comments, prose, or trailing text.
    """

def fetch_bom_component_names(main_product_name: str, count: int, gemini_model_name: str, language: str = "German", industry: str | None = None) -> List[str] | None:
    """Fetch creative component names for a BOM from Gemini."""
    prompt = build_bom_component_prompt(main_product_name, count, industry, language)
    print(f"Frage Gemini ({gemini_model_name}) nach Komponenten-Namen für '{main_product_name}'...")
    model = genai.GenerativeModel(gemini_model_name)
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        signal.alarm(45)
        response = model.generate_content(prompt)
        signal.alarm(0)
        json_text = response.text.strip().replace("```json", "").replace("```", "")
        data = json.loads(json_text)
        if isinstance(data, list):
            print(f"✅ Komponenten-Namen von Gemini empfangen: {len(data)}")
            return data
        print("❌ Gemini hat kein Array für Komponenten zurückgegeben.")
        return None
    except TimeoutException as e:
        print(f"❌ Zeitüberschreitung bei Komponenten-Namen: {e}")
        return None
    except json.JSONDecodeError:
        print("❌ Fehler: Gemini hat ungültiges JSON für Komponenten zurückgegeben.")
        return None
    except Exception as e:
        print(f"❌ Fehler bei Komponenten-Namen: {e}")
        return None
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass