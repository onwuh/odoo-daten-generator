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
from run_config import MODULE_LABELS, WANTED_MODULES, effective_installed_modules

logger = logging.getLogger(__name__)

# Checklist step ids, in display order. The frontend renders these labels.
# "access" sits right after "modules": it answers a question "Installierte
# Module" cannot — a module can be installed and still be unwritable for this
# API key (a settings checkbox off, a missing rights group). On demo-test5
# the pre-S10 read-only probe reported mrp_routings=True with the "Work
# Orders" setting off, so the run started and was guaranteed to fail.
STEP_LABELS = [
    ("odoo", "Odoo-Verbindung"),
    ("company", "Firmenname"),
    ("language", "Sprache"),
    ("modules", "Installierte Module"),
    ("access", "Schreibrechte"),
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
    # Raw per-model create-access results (odoo_actions.probe_model_access).
    model_access: Dict[str, bool] = field(default_factory=dict)
    # Subset of installed_modules whose PRIMARY model (odoo_actions.
    # PRIMARY_MODEL_PER_MODULE) is not creatable — computed by the SAME
    # run_config.effective_installed_modules a run itself uses, so the
    # frontend renders exactly the decision the run will make rather than
    # re-deriving its own answer from the raw model_access dict.
    blocked_modules: Set[str] = field(default_factory=set)
    odoo_version: Optional[str] = None
    # R5/WP4 — one of "unknown"/"known_good"/"known_broken_with_fix"/"untested"
    # (odoo_actions.classify_version_status). Replaces the old binary "version
    # detected or not": a detected-but-never-run-through-WP5 version is a
    # materially different risk than one actually verified clean.
    version_status: str = "unknown"
    field_warnings: List[str] = field(default_factory=list)
    existing_company_ids: List[int] = field(default_factory=list)
    existing_product_ids: List[int] = field(default_factory=list)
    # S16/D8a: real res.company records, for the Firmenauswahl "existing
    # company" picker — disjoint from existing_company_ids above (res.partner).
    real_companies: List[Dict[str, Any]] = field(default_factory=list)
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    # S10/R10 (F2): the resolved database name — surfaced so the frontend can
    # show it even though there's no longer an input field for it (WP4 derives
    # it from the URL rather than asking).
    database: Optional[str] = None

    def as_public_dict(self) -> Dict[str, Any]:
        """JSON-safe view for the API. Carries no credentials by construction."""
        return {
            "ok": self.ok,
            "steps": [
                {"key": s.key, "label": s.label, "ok": s.ok, "detail": s.detail}
                for s in self.steps
            ],
            "database": self.database,
            "company_name": self.company_name,
            "language_code": self.language_code,
            "language_name": self.language_name,
            "installed_modules": sorted(self.installed_modules),
            # Not cosmetic: without these the MRP work centers, BOM operations and
            # quality points are silently never generated (mrp.py:268/:347), and
            # CRM leads stay off. Same silent-disable class as the B1 bug.
            "feature_flags": self.feature_flags,
            "model_access": self.model_access,
            "blocked_modules": sorted(self.blocked_modules),
            "odoo_version": self.odoo_version,
            "version_status": self.version_status,
            "field_warnings": self.field_warnings,
            "existing_companies": len(self.existing_company_ids),
            "existing_products": len(self.existing_product_ids),
            "real_companies": self.real_companies,
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


def fetch_existing_company_data(client, company_id: int) -> tuple:
    """S16/D8b: partners/products already scoped to a specific res.company —
    used when a company block requests target.reuse_master_data=True
    against an existing target.company_id. Called from web/jobs.py's
    per-company loop (after resolving that company's id), never from
    build_context_list (run_config.py is deliberately Odoo-call-free,
    D10-Korrektur).

    Filters by company_id OR company-neutral (company_id=False), unlike
    fetch_existing_data above (which filters res.partner on
    is_company+customer_rank — right for "find Firma 1's own customer
    companies", wrong here: a prior run's master_data.py write
    (D8-Ergänzung) sets company_id on every partner it creates for a given
    company, contacts included, none of which necessarily carry
    customer_rank>0 or is_company=True).

    S16/S1 (pre-merge cold review): a strict `company_id = X` domain missed
    almost everything — company-neutral records (company_id=False, shared
    across every company; live-confirmed on demo-test5 as the overwhelming
    majority: 1440 company-neutral products vs. 0 scoped to company 1) are
    real, usable existing data for ANY company's reuse, not just records
    this pipeline itself scoped to that one company.
    """
    existing_partners = client.search_read(
        'res.partner',
        ['|', ["company_id", "=", False], ["company_id", "=", company_id]],
        fields=["id"], limit=0,
    )
    existing_products = client.search_read(
        'product.product',
        ['|', ["company_id", "=", False], ["company_id", "=", company_id]],
        fields=["id"], limit=500,
    )
    return (
        [r["id"] for r in existing_partners],
        [r["id"] for r in existing_products],
    )


def fetch_real_companies(client) -> List[Dict[str, Any]]:
    """S16/D8a: real res.company records for the Firmenauswahl "existing
    company" picker. Distinct from fetch_existing_data's "existing_companies"
    (res.partner customer contacts, not res.company records) — that fetch
    answers "does this instance already have customer data", this one answers
    "which company can a block target with mode=existing".
    """
    companies = client.search_read('res.company', [], fields=["id", "name"], limit=0)
    return [{"id": r["id"], "name": r["name"]} for r in companies]


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
    result = ConnectResult(database=database)
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
        mods: Set[str] = set()
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

        # -- Write access (R10/WP1) --
        # A model existing and being readable does not mean this API key can
        # CREATE records on it — the gap that let a run start on demo-test5
        # with mrp_routings reporting True while the setting was off. Uses
        # the SAME effective_installed_modules a run itself uses (see
        # run_config), so this step and the run agree on which modules are
        # actually usable.
        try:
            result.model_access = odoo_actions.probe_model_access(client, mods)
            _usable, result.blocked_modules = effective_installed_modules(mods, result.model_access)
            if result.blocked_modules:
                labels = ", ".join(MODULE_LABELS.get(m, m) for m in sorted(result.blocked_modules))
                step("access", False, f"Keine Schreibrechte: {labels}")
            else:
                step("access", True, "OK")
        except Exception as exc:
            logger.warning(f"Schreibrechte nicht ermittelbar: {exc}")
            step("access", False, str(exc)[:200])

        # -- Server version (non-fatal) --
        try:
            version = odoo_actions.get_server_version(client)
            result.odoo_version = version
            result.version_status = odoo_actions.classify_version_status(version)
            if version:
                result.field_warnings = odoo_actions.check_field_compatibility(
                    client, installed_modules=mods, model_access=result.model_access) or []
                # R5/WP4 — three distinguishable states instead of the old
                # "version string present" binary: bekannt-gut / bekannt-
                # defekt-mit-Fix / ungetestet-vorsichtig-fortfahren.
                status_label = {
                    "known_good": "geprüft",
                    "known_broken_with_fix": "bekannte Probleme, Fix aktiv",
                    "untested": "ungetestet",
                }.get(result.version_status, "")
                detail = f"{version} ({status_label})" if status_label else version
                if result.field_warnings:
                    detail += f" · {len(result.field_warnings)} Feld-Warnung(en) siehe Log"
                step("version", True, detail)
            else:
                # Non-fatal by design (S5): the version string is cosmetic and
                # check_field_compatibility already degrades gracefully without
                # it. Reporting this red would (once WP5's all-green gate
                # exists) dead-end the whole UI over a string this tool never
                # needed to run.
                step("version", True, "unbekannt")
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

        # -- Real companies for Firmenauswahl (S16/D8a, non-fatal) --
        try:
            result.real_companies = fetch_real_companies(client)
        except Exception as exc:
            logger.warning(f"res.company-Liste nicht ermittelbar: {exc}")
            result.real_companies = []

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
