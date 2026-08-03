"""
LLMService — single class for all LLM interactions.

Provider selection (in priority order):
  1. Groq (via openai-compatible SDK) — primary
  2. Google Gemini (via google-genai SDK) — fallback

Key features:
- Cross-platform timeout via ThreadPoolExecutor (no signal.alarm())
- Batch calls: one call for all BOM components, project stages, job summaries
- Robust JSON extraction with regex fallback
- JSONDecodeError handling on every call
- Token usage logging
- Disk caching for seed data (seeds/cache/)
"""

import hashlib
import json
import re
import time
import concurrent.futures
from pathlib import Path
from typing import Any, Dict, List, Optional

_CACHE_DIR = Path(__file__).parent / "seeds" / "cache"
_PROMPT_VERSION = "v3"  # bump when prompt wording changes to bust stale cache entries


_LANG_MAP: Dict[str, str] = {
    'de_DE': 'German', 'en_US': 'English', 'fr_FR': 'French',
    'es_ES': 'Spanish', 'it_IT': 'Italian', 'nl_NL': 'Dutch',
    'pt_PT': 'Portuguese', 'pl_PL': 'Polish', 'cs_CZ': 'Czech', 'ru_RU': 'Russian',
}
_BASE_LANG_MAP: Dict[str, str] = {
    'de': 'German', 'en': 'English', 'fr': 'French', 'es': 'Spanish',
    'it': 'Italian', 'nl': 'Dutch', 'pt': 'Portuguese', 'pl': 'Polish',
    'cs': 'Czech', 'ru': 'Russian',
}


def get_language_name(lang_code: str) -> str:
    """Convert an Odoo language code (e.g. 'de_DE') to a language name for prompts."""
    base = lang_code.split('_')[0].lower() if '_' in lang_code else lang_code.lower()
    return _LANG_MAP.get(lang_code, _BASE_LANG_MAP.get(base, 'German'))


# Timeouts are intentionally excluded: a timeout means the provider is slow →
# return None immediately so the caller falls through to the Gemini fallback.
_RETRYABLE_HINTS = ("503", "unavailable", "high demand", "try again", "rate_limit", "rate limit")


class LLMService:
    def __init__(self, api_key: str, model_name: str, provider: str = "groq") -> None:
        self.provider = provider
        self.model_name = model_name
        self.total_calls = 0
        self.total_tokens = 0
        if provider == "groq":
            from openai import OpenAI
            self._client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)
        else:  # gemini fallback
            from google import genai as _genai
            self._client = _genai.Client(api_key=api_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_json(self, text: str) -> str:
        """Robustly extract JSON from a response that may contain markdown fences."""
        text = text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()
        if text and text[0] not in ('{', '['):
            match = re.search(r'[\[{].*[\]}]', text, re.DOTALL)
            if match:
                text = match.group()
        return text

    def _raw_call(self, prompt: str):
        """Single provider-specific LLM call. Returns (text, in_tokens, out_tokens). Raises on failure."""
        if self.provider == "groq":
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.choices[0].message.content
            in_tok = response.usage.prompt_tokens or 0
            out_tok = response.usage.completion_tokens or 0
        else:  # gemini
            response = self._client.models.generate_content(
                model=self.model_name, contents=prompt
            )
            text = response.text
            meta = response.usage_metadata
            in_tok = (meta.prompt_token_count or 0) if meta else 0
            out_tok = (meta.candidates_token_count or 0) if meta else 0
        return text, in_tok, out_tok

    def _call(self, prompt: str, timeout: int = 120) -> Optional[str]:
        """Call the LLM with a cross-platform timeout and up to 3 attempts on transient errors."""
        self.total_calls += 1
        msg = ""
        for attempt in range(1, 4):
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(self._raw_call, prompt)
            try:
                text, in_tok, out_tok = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                msg = f"timed out after {timeout}s"
            except Exception as e:
                msg = str(e)
            else:
                self.total_tokens += in_tok + out_tok
                print(f"[{self.provider.upper()}] {in_tok} in + {out_tok} out = "
                      f"{in_tok + out_tok} tokens (Gesamtlauf: {self.total_tokens}, "
                      f"Anfragen: {self.total_calls})")
                return text
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            retryable = any(hint in msg.lower() for hint in _RETRYABLE_HINTS)
            if retryable and attempt < 3:
                print(f"❌ LLM request failed (attempt {attempt}/3): {msg} — retry in 20s...")
                time.sleep(20)
            else:
                print(f"❌ LLM request failed: {msg}")
                return None
        return None

    def _call_json(self, prompt: str, timeout: int = 120) -> Optional[Any]:
        """Call the LLM and parse the response as JSON. Returns None on any failure."""
        text = self._call(prompt, timeout)
        if not text:
            return None
        json_text = self._extract_json(text)
        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            print(f"❌ JSON parsing failed: {e}\nRaw (first 300 chars): {json_text[:300]}")
            return None

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _slug(*parts: str) -> str:
        return "_".join(re.sub(r'[^\w]', '_', str(p)).lower().strip('_') for p in parts)

    @staticmethod
    def _hash(items) -> str:
        return hashlib.md5("|".join(sorted(str(i) for i in items)).encode()).hexdigest()[:8]

    def _cache_load(self, key: str) -> Optional[Any]:
        path = _CACHE_DIR / f"{key}.json"
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        return None

    def _cache_save(self, key: str, data: Any) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with (_CACHE_DIR / f"{key}.json").open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def determine_industry_from_company_name(self, company_name: str) -> Optional[str]:
        prompt = f"""
Based on the company name "{company_name}", determine the most likely industry/sector.
Return ONLY a single word or short phrase (2-3 words max) describing the industry in German.
Examples: "IT", "Fertigung", "Handel", "Dienstleistung", "Medizin", "Bildung", "IT-Dienstleistung"
Return ONLY the industry name, no explanation, no JSON, no quotes, just the text.
"""
        print(f"Frage LLM ({self.provider}/{self.model_name}) nach Branche für '{company_name}'...")
        text = self._call(prompt, timeout=30)
        if not text:
            return None
        industry = text.strip().strip('"').strip("'")
        print(f"✅ Erkannte Branche: {industry}")
        return industry

    def fetch_creative_data(self, criteria: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate companies + products JSON for master data creation."""
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
4.  Erstelle exakt {criteria['num_other_contacts']} Kontakte vom Typ 'contact'.
    - Der 'name' dieser Kontakte soll ein voller Personenname sein (z.B. "Sabine Schmidt").
5.  Alle E-Mail-Adressen MÜSSEN die Domain "@example.com" verwenden.
6.  WICHTIG: Erstelle KEINE USt-IdNr. (VAT ID / vat / vat_id Felder) - diese werden nicht benötigt.

Für Produkte, beachte folgende Regeln:
- Verwende nur gültige Felder: "name", "description", "list_price", "standard_price", "sale_ok", "purchase_ok"
- Erstelle KEINE Felder wie: "uom", "detailed_type", "vat", "vat_id" - diese sind ungültig.
- Produkttypen werden automatisch basierend auf der Kategorie gesetzt.
- {criteria['num_services']} Dienstleistungs-Produkte (unter "services")
- {criteria['num_consumables']} Verbrauchs-Produkte (unter "consumables")
- {criteria['num_storables']} lagerfähige Produkte (unter "storables")

Erstelle exakt {criteria['num_companies']} Einträge im "companies"-Array.

Beispiel-Struktur (exakt so einhalten):
{{
  "companies": [
    {{
      "company_data": {{"name": "...", "street": "...", "city": "...", "zip": "...", "country_code": "DE", "email": "...", "phone": "...", "website": "..."}},
      "contacts": [
        {{"name": "Lager Nord", "type": "delivery", "street": "...", "city": "...", "zip": "...", "country_code": "DE"}},
        {{"type": "invoice", "street": "...", "city": "...", "zip": "...", "country_code": "DE"}},
        {{"name": "Max Mustermann", "type": "contact", "email": "max@example.com"}}
      ]
    }}
  ],
  "products": {{
    "services": [{{"name": "...", "list_price": 0.0}}],
    "consumables": [{{"name": "...", "list_price": 0.0}}],
    "storables": [{{"name": "...", "list_price": 0.0}}]
  }}
}}
"""
        print(f"Frage LLM ({self.provider}/{self.model_name}) nach kreativen Daten...")
        data = self._call_json(prompt, timeout=120)
        if data:
            print("✅ Kreative Daten vom LLM empfangen.")
        return data

    def fetch_name_suggestions(
        self, criteria: Dict[str, Any], language: str = "German"
    ) -> Optional[Dict[str, List[str]]]:
        """Generate name banks (products, employees, companies, etc.) for the given industry."""
        industry = criteria.get('industry', 'IT')
        cache_key = self._slug(industry, language, "name_suggestions", _PROMPT_VERSION)
        if (cached := self._cache_load(cache_key)) is not None:
            print(f"✅ Namensvorschläge aus Cache geladen ({cache_key}.json).")
            return cached

        prompt = f"""
Based on the industry "{industry}", generate ONLY a JSON object with arrays of realistic {language} names:
{{
  "product_names": [min 25 strings],
  "employee_names": [min 40 full person names],
  "company_names": [min 25 company names],
  "project_names": [min 25 project titles],
  "task_names": [min 50 concise task names],
  "opportunity_titles": [min 25 sales opportunity titles — deal-specific phrases, e.g. "ERP-Einführung Müller AG", "Cloud-Migration Q3", "Wartungsvertrag Verlängerung". Never use the word "Opportunity" or "Gelegenheit". Mix formats: project-style, deal-description, company-reference.],
  "supplier_names": [min 15 supplier/vendor company names with legal form suffix like GmbH, AG, KG, Ltd.]
}}
- No code blocks, no backticks, no comments, only valid compact JSON.
- Names must fit the given industry.
- Supplier names should be realistic vendor/supplier company names for the industry.
"""
        print(f"Frage LLM ({self.provider}/{self.model_name}) nach Namensvorschlägen...")
        data = self._call_json(prompt, timeout=90)
        if data:
            print("✅ Namensvorschläge vom LLM empfangen.")
            self._cache_save(cache_key, data)
        return data

    def fetch_recruiting_data(
        self,
        industry: str,
        num_jobs: int,
        num_candidates: int,
        num_skill_types: int,
        skills_per_type: int,
        language: str = "German",
    ) -> Optional[Dict[str, Any]]:
        prompt = f"""
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
        print(f"Frage LLM ({self.provider}/{self.model_name}) nach Recruiting-Daten für {industry}...")
        data = self._call_json(prompt, timeout=120)
        if data:
            print("✅ Recruiting-Daten vom LLM empfangen.")
        return data

    def fetch_job_summaries_batch(
        self, job_titles: List[str], industry: str, language: str = "German"
    ) -> Dict[str, str]:
        """Fetch 2-3 sentence job descriptions for all job titles in a single LLM call."""
        if not job_titles:
            return {}
        cache_key = self._slug(industry, language, self._hash(job_titles), "job_summaries")
        if (cached := self._cache_load(cache_key)) is not None:
            print(f"✅ Job-Beschreibungen aus Cache geladen ({cache_key}.json).")
            return cached

        titles_json = json.dumps(job_titles, ensure_ascii=False)
        prompt = f"""
Given industry "{industry}", generate a JSON object where each key is a job title
and each value is a 2-3 sentence {language} job description/summary.
Job titles: {titles_json}
Return ONLY valid JSON, no markdown, no code blocks:
{{
  "Job Title 1": "summary text...",
  "Job Title 2": "summary text..."
}}
"""
        print(f"Frage LLM ({self.provider}/{self.model_name}) nach Job-Beschreibungen ({len(job_titles)} Stellen)...")
        data = self._call_json(prompt, timeout=120)
        if isinstance(data, dict):
            print(f"✅ Job-Beschreibungen vom LLM empfangen: {len(data)} Stellen")
            self._cache_save(cache_key, data)
            return data
        return {}

    def fetch_all_project_stages(
        self, project_names: List[str], industry: str, language: str = "German"
    ) -> Dict[str, List[str]]:
        """Fetch stage names for all projects in a single LLM call.

        Returns a dict of {project_name: [stage_name, ...]} with 6-8 stages per project.
        """
        if not project_names:
            return {}
        num_projects = len(project_names)

        prompt = f"""You are a project manager in the "{industry}" industry.
Generate {num_projects} sets of 6-8 realistic {language} project stage names
representing logical workflow progressions typical for this industry.
Return ONLY valid JSON, no markdown, no code blocks:
{{
  "set_1": ["Stage 1", "Stage 2", ...],
  "set_2": ["Phase 1", "Phase 2", ...]
}}"""
        print(f"Frage LLM ({self.provider}/{self.model_name}) nach Projektphasen ({num_projects} Projekte)...")
        data = self._call_json(prompt, timeout=120)
        if isinstance(data, dict) and data:
            print(f"✅ Projektphasen vom LLM empfangen: {len(data)} Sets")
            sets = list(data.values())
            return {name: sets[i % len(sets)] for i, name in enumerate(project_names)}
        return {}

    def fetch_workcenter_data(
        self, industry: str, language: str, num_workcenters: int
    ) -> Dict[str, Dict]:
        """Returns {station_name: {description: str, operations: [str, str, str]}}"""
        prompt = f"""You are generating realistic manufacturing demo data for a {industry} company.
Return a JSON object with exactly {num_workcenters} work centers.
Each key is a machine/station name (NOT a job title — e.g. "Schweissanlage", "CNC-Fraese", "Montagelinie 1").
Each value has:
  "description": one sentence describing what this station does
  "operations": list of exactly 3 process step names performed at this station
Language: {language}. Return clean JSON only, no markdown fences."""

        data = self._call_json(prompt, timeout=60)
        if isinstance(data, dict) and len(data) >= 1:
            return data
        return {}

    def fetch_crm_chatter_messages(
        self,
        opportunities: List[Dict[str, str]],
        industry: str,
        language: str = "German",
        style: str = "mixed",
        messages_per_opp: int = 4,
    ) -> Dict[str, List[Dict]]:
        """Fetch realistic chatter messages per opportunity in one batch call.

        Args:
            opportunities: list of {"title": ..., "customer": ..., "salesperson": ...}
                           — one entry per opportunity, so each gets its own
                           customer/salesperson names in the generated messages
                           instead of one name reused across the whole batch (B9)
            industry: industry name for context
            language: language for generated text
            style: "notes_only" | "mixed" | "full_email"
            messages_per_opp: how many messages to generate per opportunity (2-8)

        Returns dict {opportunity_title: [{"type": "email"|"note", "speaker": "customer"|"salesperson", "body": str}, ...]}.
        Legacy string-list format (from old cache) is handled by the caller.
        """
        if not opportunities:
            return {}

        messages_per_opp = max(2, min(8, messages_per_opp))
        opportunity_titles = [o["title"] for o in opportunities]
        opps_json = json.dumps([
            {
                "title": o["title"],
                "customer": o.get("customer") or "the customer",
                "salesperson": o.get("salesperson") or "the sales rep",
            }
            for o in opportunities
        ], ensure_ascii=False)

        if style == "notes_only":
            type_instruction = (
                f'All messages must have "type": "note" and "speaker": "salesperson". '
                f'Keep each note to 1-3 sentences, internal sales tone.'
            )
            example_types = '"type": "note", "speaker": "salesperson"'
        elif style == "full_email":
            type_instruction = (
                f'All messages must be emails ("type": "email"). Alternate between '
                f'"speaker": "customer" (inbound) and "speaker": "salesperson" (outbound). '
                f'Start with customer reaching out. Emails should have a proper greeting, '
                f'a substantive body (2-4 sentences), and a closing line.'
            )
            example_types = '"type": "email", "speaker": "customer"'
        else:  # mixed
            type_instruction = (
                f'Mix emails and internal notes. Emails ("type": "email") can have '
                f'"speaker": "customer" (inbound) or "speaker": "salesperson" (outbound). '
                f'Notes ("type": "note") must always have "speaker": "salesperson" (internal only). '
                f'Emails should be substantive (2-4 sentences with greeting and closing). '
                f'Notes are short internal observations (1-2 sentences).'
            )
            example_types = '"type": "email", "speaker": "customer"'

        arc = (
            "Follow a realistic sales arc across the messages: "
            "initial contact → qualification → demo/proposal → negotiation → next step/close."
        )

        prompt = f"""You are generating realistic CRM chatter data for a demo in the "{industry}" industry.
Language: {language}.

For each opportunity below, generate exactly {messages_per_opp} messages.
Each opportunity has its own "customer" and "salesperson" name — use exactly that
opportunity's names in greetings and sign-offs for its messages (do not mix names
across opportunities).
{type_instruction}
{arc}
Be specific to the opportunity title. Use realistic details and amounts.

Opportunities: {opps_json}

Return ONLY valid JSON, no markdown, no code blocks, keyed by "title":
{{
  "Opportunity Title 1": [
    {{{example_types}, "body": "..."}},
    {{"type": "note", "speaker": "salesperson", "body": "..."}}
  ]
}}"""
        print(f"Frage LLM ({self.provider}/{self.model_name}) nach Chatter-Nachrichten ({len(opportunity_titles)} Opps, style={style})...")
        data = self._call_json(prompt, timeout=180)
        if isinstance(data, dict):
            print(f"✅ Chatter-Nachrichten vom LLM empfangen: {len(data)} Einträge")
            return data
        return {}

    def fetch_all_bom_components(
        self, products: Dict[str, int], industry: str, language: str = "German"
    ) -> Dict[str, List[str]]:
        """Fetch BOM component names for multiple products in a single LLM call.

        Args:
            products: dict of {product_name: num_components_needed}
        Returns:
            dict of {product_name: [component_name, ...]}
        """
        if not products:
            return {}
        num_products = len(products)
        components_per_bom = next(iter(products.values())) if products else 0

        prompt = f"""You are a senior manufacturing engineer in the "{industry}" industry.
Generate {num_products} sets of {components_per_bom} realistic {language} component names
for typical manufactured products in this industry.
Return ONLY valid JSON, no markdown, no code blocks:
{{
  "set_1": ["Component A", "Component B", ...],
  "set_2": ["Part X", "Part Y", ...]
}}
Rules:
- Names must be realistic manufacturing sub-assemblies or parts.
- Keep names concise (max 6 words).
- Provide exactly {components_per_bom} components per set."""
        print(f"Frage LLM ({self.provider}/{self.model_name}) nach BOM-Komponenten ({num_products} Produkte)...")
        data = self._call_json(prompt, timeout=120)
        if isinstance(data, dict) and data:
            print(f"✅ BOM-Komponenten vom LLM empfangen: {len(data)} Sets")
            sets = list(data.values())
            return {name: sets[i % len(sets)] for i, name in enumerate(products.keys())}
        return {}
