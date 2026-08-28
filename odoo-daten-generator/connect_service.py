"""Connection probe — the checklist the retired `gui.py` screen 2 ran (D4).

Framework-free: takes credentials, returns a structured result. Each probe is
independent and non-fatal except the two that make a run impossible (Odoo
reachability and the LLM), mirroring the desktop wizard's "Weiter" gate.

Credentials are arguments, never module state — the web layer keeps them in a
memory-only session and this module never writes them anywhere.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import odoo_actions
from llm_service import LLMService, get_language_name
from odoo_client import OdooJson2Client
from run_config import MODULE_LABELS, WANTED_MODULES

logger = logging.getLogger(__name__)

# Checklist step ids, in display order. The frontend renders these labels.
STEP_LABELS = [
    ("odoo", "Odoo-Verbindung"),
    ("company", "Firmenname"),
    ("language", "Sprache"),
    ("modules", "Installierte Module"),
    ("version", "Odoo-Version"),
    ("existing", "Vorhandene Stammdaten"),
    ("llm", "LLM-Verbindung"),
]


@dataclass
class ProbeStep:
    key: str
    label: str
    ok: bool
    detail: str = ""


@dataclass
class ConnectResult:
    ok: bool = False
    steps: List[ProbeStep] = field(default_factory=list)
    company_name: Optional[str] = None
    language_code: str = "de_DE"
    language_name: str = "German"
    installed_modules: Set[str] = field(default_factory=set)
    feature_flags: Dict[str, bool] = field(default_factory=dict)
    odoo_version: Optional[str] = None
    field_warnings: List[str] = field(default_factory=list)
    existing_company_ids: List[int] = field(default_factory=list)
    existing_product_ids: List[int] = field(default_factory=list)
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None

    def as_public_dict(self) -> Dict[str, Any]:
        """JSON-safe view for the API. Carries no credentials by construction."""
        return {
            "ok": self.ok,
            "steps": [
                {"key": s.key, "label": s.label, "ok": s.ok, "detail": s.detail}
                for s in self.steps
            ],
            "company_name": self.company_name,
            "language_code": self.language_code,
            "language_name": self.language_name,
            "installed_modules": sorted(self.installed_modules),
            # Not cosmetic: without these the MRP work centers, BOM operations and
            # quality points are silently never generated (mrp.py:268/:347), and
            # CRM leads stay off. Same silent-disable class as the B1 bug.
            "feature_flags": self.feature_flags,
            "odoo_version": self.odoo_version,
            "field_warnings": self.field_warnings,
            "existing_companies": len(self.existing_company_ids),
            "existing_products": len(self.existing_product_ids),
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
        }


def fetch_existing_data(client) -> tuple:
    """Existing customer companies and active products already in the instance."""
    existing_companies = client.search_read(
        'res.partner',
        [["is_company", "=", True], ["customer_rank", ">", 0]],
        fields=["id"],
        limit=0,
    )
    existing_products = client.search_read(
        'product.product',
        [["active", "=", True]],
        fields=["id"],
        limit=500,
    )
    return (
        [r["id"] for r in existing_companies],
        [r["id"] for r in existing_products],
    )


def detect_provider(llm_key: str, explicit: Optional[str] = None) -> str:
    if explicit in ("groq", "gemini"):
        return explicit
    return "groq" if (llm_key or "").startswith("gsk_") else "gemini"


def probe(*, base_url: str, database: str, odoo_key: str,
          llm_key: str, llm_model: str, llm_provider: Optional[str] = None):
    """Run the full connect checklist.

    Returns ``(result, client, llm)``. ``client``/``llm`` are ``None`` when their
    probe failed; the caller must not store a session in that case.
    """
    result = ConnectResult()
    labels = dict(STEP_LABELS)
    client: Optional[OdooJson2Client] = None
    llm: Optional[LLMService] = None

    def step(key: str, ok: bool, detail: str = ""):
        result.steps.append(ProbeStep(key=key, label=labels[key], ok=ok, detail=detail))

    # -- Odoo connection (fatal) --
    odoo_ok = False
    try:
        client = OdooJson2Client(base_url, database, odoo_key)
        client.search_read("res.lang", [["active", "=", True]], fields=["id"], limit=1)
        odoo_ok = True
        step("odoo", True, "OK")
    except Exception as exc:
        client = None
        step("odoo", False, str(exc)[:200])

    if odoo_ok:
        # -- Company name --
        try:
            result.company_name = odoo_actions.get_main_company_name(client)
            step("company", True, result.company_name or "–")
        except Exception as exc:
            step("company", False, str(exc)[:200])

        # -- Language --
        try:
            code = odoo_actions.get_main_company_language(client)
            result.language_code = code
            result.language_name = get_language_name(code)
            step("language", True, f"{code} ({result.language_name})")
        except Exception as exc:
            step("language", False, str(exc)[:200])

        # -- Installed modules + feature flags --
        try:
            mods = odoo_actions.get_installed_modules(client, WANTED_MODULES)
            result.installed_modules = mods
            step("modules", True,
                 ", ".join(MODULE_LABELS.get(m, m) for m in sorted(mods)) or "–")
            try:
                # Must be given the installed set — without it every probe is
                # skipped and the result is {} (see CLAUDE.md field gotchas).
                result.feature_flags = odoo_actions.get_enabled_features(client, mods)
            except Exception as exc:
                logger.warning(f"Feature-Flags nicht ermittelbar: {exc}")
                result.feature_flags = {}
        except Exception as exc:
            step("modules", False, str(exc)[:200])

        # -- Server version (non-fatal) --
        try:
            version = odoo_actions.get_server_version(client)
            result.odoo_version = version
            if version:
                result.field_warnings = odoo_actions.check_field_compatibility(client) or []
                detail = version
                if result.field_warnings:
                    detail += f" · {len(result.field_warnings)} Feld-Warnung(en) siehe Log"
                step("version", True, detail)
            else:
                step("version", False, "unbekannt")
        except Exception as exc:
            step("version", False, str(exc)[:200])

        # -- Existing master data --
        try:
            c_ids, p_ids = fetch_existing_data(client)
            result.existing_company_ids = c_ids
            result.existing_product_ids = p_ids
            detail = (f"{len(c_ids)} Kunden, {len(p_ids)} Produkte"
                      if (c_ids or p_ids) else "Keine vorhanden")
            step("existing", True, detail)
        except Exception as exc:
            step("existing", False, str(exc)[:200])

    # -- LLM connection (fatal) --
    provider = detect_provider(llm_key, llm_provider)
    llm_ok = False
    try:
        llm = LLMService(llm_key, llm_model, provider)
        if not llm.ping():
            raise RuntimeError("Leere LLM-Antwort")
        # No industry auto-suggest here: reading the company name out of the
        # target database and putting it into an LLM prompt was the one path
        # that shipped pre-existing customer content to a third party. The
        # industry field stays, freely editable, defaulted client-side.
        result.llm_provider = provider
        result.llm_model = llm_model
        llm_ok = True
        step("llm", True, f"{provider} / {llm_model}")
    except Exception as exc:
        llm = None
        step("llm", False, str(exc)[:200])

    result.ok = odoo_ok and llm_ok
    return result, (client if odoo_ok else None), (llm if llm_ok else None)
