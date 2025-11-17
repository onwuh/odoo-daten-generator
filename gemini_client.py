import google.generativeai as genai
import json
import signal
from typing import Dict, Any, List, Optional

def build_uom_assignment_prompt(product_name: str, product_type: str, industry: str, available_uoms: List[Dict[str, Any]], language: str = "German") -> str:
    """Build prompt for assigning logical UOMs to a product."""
    uom_list = []
    for uom in available_uoms:
        uom_name = uom.get("name", "")
        uom_id = uom.get("id")
        # Handle tuple/list format for ID
        if isinstance(uom_id, (list, tuple)) and len(uom_id) > 0:
            uom_id = uom_id[0]
        uom_list.append(f"- {uom_name} (ID: {uom_id})")
    
    uom_list_str = "\n".join(uom_list)
    
    return f"""
    Basierend auf der Branche "{industry}" und dem Produkttyp "{product_type}":
    
    Produktname: "{product_name}"
    
    Verfügbare Maßeinheiten (UOMs):
    {uom_list_str}
    
    Wähle die LOGISCHSTE Maßeinheit für dieses Produkt aus.
    
    Beispiele:
    - Kabel, Seile, Stoffe -> Meter (m) oder Kilometer (km)
    - Kleine Einzelteile -> Units (Stk) oder 6er Pack, 12er Pack
    - Flüssigkeiten -> Liter (L) oder Milliliter (mL)
    - Gewichte -> Kilogramm (kg) oder Gramm (g)
    - Software/Dienstleistungen -> Units (Stk)
    
    Gib NUR die UOM-ID zurück (nur die Zahl), die am besten passt.
    Falls keine passende UOM vorhanden ist, gib "0" zurück.
    Keine Erklärungen, nur die Zahl.
    """

def fetch_uom_assignment(product_name: str, product_type: str, industry: str, available_uoms: List[Dict[str, Any]], gemini_model_name: str, language: str = "German") -> Optional[int]:
    """Use Gemini to assign a logical UOM to a product."""
    if not gemini_model_name or not available_uoms:
        return None
    
    try:
        prompt = build_uom_assignment_prompt(product_name, product_type, industry, available_uoms, language)
        model = genai.GenerativeModel(gemini_model_name)
        
        # Set timeout
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(10)  # 10 second timeout
        
        try:
            response = model.generate_content(prompt)
            signal.alarm(0)  # Cancel timeout
            
            text = response.text.strip()
            # Try to extract just the number
            import re
            numbers = re.findall(r'\d+', text)
            if numbers:
                uom_id = int(numbers[0])
                # Verify it's a valid UOM ID (handle tuple/list format)
                for uom in available_uoms:
                    uom_id_from_db = uom.get("id")
                    if isinstance(uom_id_from_db, (list, tuple)) and len(uom_id_from_db) > 0:
                        uom_id_from_db = uom_id_from_db[0]
                    if uom_id_from_db == uom_id:
                        return uom_id
        except TimeoutException:
            signal.alarm(0)
            print(f"   ⚠️  Gemini timeout for UOM assignment")
        except Exception as e:
            signal.alarm(0)
            print(f"   ⚠️  Gemini error for UOM assignment: {e}")
    except Exception as e:
        print(f"   ⚠️  Error in UOM assignment: {e}")
    
    return None

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

    Für Produkte, beachte folgende Regeln:
    - Verwende nur gültige Felder: "name", "description", "list_price", "standard_price", "sale_ok", "purchase_ok"
    - Erstelle KEINE Felder wie: "uom", "detailed_type", "vat", "vat_id" - diese sind ungültig oder werden automatisch gesetzt.
    - Produkttypen werden automatisch basierend auf der Kategorie gesetzt.

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
      "opportunity_titles": [min 25 sales opportunity titles],
      "supplier_names": [min 15 supplier/vendor company names with legal form suffix like GmbH, AG, KG, Ltd.]
    }}
    - No code blocks, no backticks, no comments, only valid compact JSON.
    - Names must fit the given industry.
    - Supplier names should be realistic vendor/supplier company names for the industry.
    """

def build_recruiting_prompt(industry: str, num_jobs: int, num_candidates: int, num_skill_types: int, skills_per_type: int, language: str = "German") -> str:
    """Build prompt for generating recruiting data (jobs, candidates, skills)."""
    return f"""
    Based on the industry "{industry}", generate ONLY a JSON object with realistic {language} recruiting data:
    {{
      "job_titles": [exactly {num_jobs} job titles/positions],
      "candidate_names": [exactly {num_candidates} full person names],
      "candidate_emails": [exactly {num_candidates} email addresses using @example.com domain],
      "candidate_phones": [exactly {num_candidates} phone numbers in German format],
      "skill_types": [
        {{
          "name": "skill type name",
          "skills": [exactly {skills_per_type} skill names],
          "levels": [at least 3 level names that logically fit the skill type]
        }}
      ] (exactly {num_skill_types} skill types)
    }}
    
    Examples for skill types:
    - "Sprachen": skills: ["Englisch", "Französisch", "Deutsch"], levels: ["A1", "A2", "B1", "B2", "C1", "C2"]
    - "Programmiersprachen": skills: ["Python", "Java", "JavaScript"], levels: ["Anfänger", "Fortgeschritten", "Experte"]
    - "Soft Skills": skills: ["Kommunikation", "Teamarbeit", "Führung"], levels: ["Grundlagen", "Fortgeschritten", "Experte"]
    
    - No code blocks, no backticks, no comments, only valid compact JSON.
    - All data must be realistic and fit the industry "{industry}".
    - Skill types, skills, and levels must logically fit together.
    - Use {language} language.
    """

def fetch_recruiting_data(industry: str, num_jobs: int, num_candidates: int, num_skill_types: int, skills_per_type: int, gemini_model_name: str, language: str = "German") -> Dict[str, Any] | None:
    """Fetch recruiting data from Gemini."""
    prompt = build_recruiting_prompt(industry, num_jobs, num_candidates, num_skill_types, skills_per_type, language)
    print(f"Frage Gemini ({gemini_model_name}) nach Recruiting-Daten für {industry}...")
    model = genai.GenerativeModel(gemini_model_name)
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        signal.alarm(120)
        response = model.generate_content(prompt)
        signal.alarm(0)
        json_text = response.text.strip().replace("```json", "").replace("```", "")
        data = json.loads(json_text)
        print("✅ Recruiting-Daten von Gemini empfangen.")
        return data
    except TimeoutException as e:
        print(f"❌ Zeitüberschreitung bei der Gemini-Recruiting-Anfrage: {e}")
        return None
    except json.JSONDecodeError:
        print("❌ Fehler: Gemini hat ungültiges JSON zurückgegeben.")
        return None
    except Exception as e:
        print(f"❌ Fehler bei Recruiting-Daten: {e}")
        return None
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass

def build_job_summary_prompt(job_title: str, industry: str, language: str = "German") -> str:
    """Build prompt for generating job summary/description."""
    return f"""
    Generate a brief job summary (2-3 sentences) in {language} for the position "{job_title}" in the "{industry}" industry.
    Return ONLY the summary text, no JSON, no quotes, no code blocks.
    """

def fetch_job_summary(job_title: str, industry: str, gemini_model_name: str, language: str = "German") -> str | None:
    """Fetch job summary from Gemini."""
    prompt = build_job_summary_prompt(job_title, industry, language)
    model = genai.GenerativeModel(gemini_model_name)
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        signal.alarm(30)
        response = model.generate_content(prompt)
        signal.alarm(0)
        summary = response.text.strip().strip('"').strip("'")
        return summary
    except TimeoutException:
        return None
    except Exception:
        return None
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass

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

def determine_industry_from_company_name(company_name: str, gemini_model_name: str = "gemini-1.5-flash") -> str | None:
    """Use Gemini to determine the industry from a company name."""
    if not company_name:
        return None
    
    prompt = f"""
    Based on the company name "{company_name}", determine the most likely industry/sector.
    
    Return ONLY a single word or short phrase (2-3 words max) describing the industry in German.
    Examples: "IT", "Fertigung", "Handel", "Dienstleistung", "Medizin", "Bildung", "IT-Dienstleistung"
    
    Return ONLY the industry name, no explanation, no JSON, no quotes, just the text.
    """
    
    print(f"Frage Gemini ({gemini_model_name}) nach Branche für '{company_name}'...")
    model = genai.GenerativeModel(gemini_model_name)
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        signal.alarm(30)
        response = model.generate_content(prompt)
        signal.alarm(0)
        industry = response.text.strip().strip('"').strip("'").strip()
        print(f"✅ Erkannte Branche: {industry}")
        return industry
    except TimeoutException as e:
        print(f"❌ Zeitüberschreitung bei der Gemini-Branchenanfrage: {e}")
        return None
    except Exception as e:
        print(f"❌ Fehler bei Branchenbestimmung: {e}")
        return None
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass