"""Module catalogue and request-payload → config-dataclass assembly (D4).

Extracted out of the retired `gui.py` so the same mapping serves the web layer,
the tests, and any future caller. Everything here is framework-free: no FastAPI,
no Tk, no Odoo calls.

WANTED_MODULES is the single list of Odoo modules probed on connect. It must
carry `purchase` and `stock` — S8 shipped both modules backend-only (S9 was
already scheduled to delete the GUI), and a module missing from this list never
enters `ctx.installed_modules`, so `orchestrator.py` skips it forever with no
error. Same silent-disable class as the historical B1 bug.
"""
from typing import Any, Dict, Optional, Set, Tuple

from config import DemoCriteria, ModuleSelections, RunContext

# Odoo modules probed via ir.module.module on connect.
WANTED_MODULES = [
    "crm", "sale", "account", "hr", "project",
    "hr_timesheet", "mrp", "hr_recruitment",
    "purchase", "stock",
]

# "documents" is deliberately absent above: it is a pseudo-module. It attaches
# ir.attachment records, which are core Odoo, and "documents" also happens to be
# the technical name of Odoo's unrelated real Documents app — probing for it
# would gate PDF generation on an app nobody installed.
PSEUDO_MODULES = ["documents"]

MODULE_LABELS = {
    "crm": "CRM",
    "sale": "Verkauf",
    "account": "Buchhaltung",
    "hr": "Personal",
    "project": "Projekte",
    "hr_timesheet": "Zeiterfassung",
    "mrp": "Fertigung",
    "hr_recruitment": "Recruiting",
    "purchase": "Einkauf",
    "stock": "Lager",
    "documents": "Dokumente (PDFs)",
    "stammdaten": "Stammdaten",
}

# Progress rows, in the order orchestrator.py actually executes them.
# "stammdaten" is prepended by the caller unless skip_master_data is set.
MODULE_RUN_ORDER = [
    "mrp", "crm", "sale", "hr", "project", "hr_timesheet",
    "account", "hr_recruitment", "purchase", "stock", "documents",
]

# Maps orchestrator's on_module_start/on_module_done names onto progress-row keys.
# orchestrator passes the module_code for everything except master data, which it
# calls "Stammdaten".
PROGRESS_KEY_MAP = {"Stammdaten": "stammdaten"}

VALID_MODES = ("master", "both")
VALID_CHATTER_STYLES = ("notes_only", "mixed", "full_email")

DEFAULT_INDUSTRY = "IT-Dienstleistung"


class ConfigError(ValueError):
    """Raised when a run request payload cannot be turned into a valid config."""


# ---------------------------------------------------------------------------
# Coercion helpers — a JSON body is untrusted input, not a Python dict literal
# ---------------------------------------------------------------------------

def _as_dict(value: Any, label: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{label}' muss ein Objekt sein.")
    return value


def _as_int(value: Any, label: str, minimum: int = 0, maximum: int = 100000,
            default: Optional[int] = None) -> int:
    if value is None:
        if default is None:
            raise ConfigError(f"'{label}' fehlt.")
        return default
    if isinstance(value, bool):
        raise ConfigError(f"'{label}' muss eine Zahl sein.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{label}' muss eine Zahl sein.")
    if number < minimum or number > maximum:
        raise ConfigError(f"'{label}' muss zwischen {minimum} und {maximum} liegen.")
    return number


def _as_pct(value: Any, label: str, default: int) -> int:
    return _as_int(value, label, minimum=0, maximum=100, default=default)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _enabled(block: Dict[str, Any]) -> bool:
    """A module block counts as active only if explicitly enabled."""
    return bool(block) and _as_bool(block.get("enabled"), default=False)


# ---------------------------------------------------------------------------
# Payload → dataclasses
# ---------------------------------------------------------------------------

def build_criteria(payload: Dict[str, Any]) -> DemoCriteria:
    mode = payload.get("mode", "master")
    if mode not in VALID_MODES:
        raise ConfigError(f"Unbekannter Modus '{mode}'.")
    industry = (payload.get("industry") or "").strip() or DEFAULT_INDUSTRY
    md = _as_dict(payload.get("master_data"), "master_data")
    return DemoCriteria(
        mode=mode,
        industry=industry,
        num_companies=_as_int(md.get("num_companies"), "num_companies", 0, 500, default=3),
        num_delivery_contacts=_as_int(md.get("num_delivery_contacts"), "num_delivery_contacts", 0, 50, default=1),
        num_invoice_contacts=_as_int(md.get("num_invoice_contacts"), "num_invoice_contacts", 0, 50, default=1),
        num_other_contacts=_as_int(md.get("num_other_contacts"), "num_other_contacts", 0, 50, default=1),
        num_services=_as_int(md.get("num_services"), "num_services", 0, 500, default=5),
        num_consumables=_as_int(md.get("num_consumables"), "num_consumables", 0, 500, default=3),
        num_storables=_as_int(md.get("num_storables"), "num_storables", 0, 500, default=3),
    )


def build_selections(payload: Dict[str, Any]) -> Tuple[ModuleSelections, Set[str]]:
    """Return (selections, selected_module_keys).

    In "master" mode no transactional module is selected at all, mirroring the
    desktop wizard: the module section was hidden and every selection stayed at
    its zero default.
    """
    sel = ModuleSelections()
    selected: Set[str] = set()
    if payload.get("mode") != "both":
        return sel, selected

    modules = _as_dict(payload.get("modules"), "modules")

    crm = _as_dict(modules.get("crm"), "modules.crm")
    if _enabled(crm):
        selected.add("crm")
        sel.crm = _as_int(crm.get("count"), "crm.count", 0, 1000, default=0)
        sel.leads = _as_int(crm.get("leads"), "crm.leads", 0, 1000, default=0)
        chatter = _as_dict(crm.get("chatter"), "crm.chatter")
        if _enabled(chatter):
            style = chatter.get("style", "mixed")
            if style not in VALID_CHATTER_STYLES:
                raise ConfigError(f"Unbekannter Chatter-Stil '{style}'.")
            sel.crm_chatter = {
                "enabled": True,
                "style": style,
                "messages_per_opp": _as_int(chatter.get("messages_per_opp"),
                                            "crm.chatter.messages_per_opp", 1, 50, default=4),
            }
        activities = _as_dict(crm.get("activities"), "crm.activities")
        if _enabled(activities):
            past = _as_pct(activities.get("past_pct"), "crm.activities.past_pct", 30)
            today = min(_as_pct(activities.get("today_pct"), "crm.activities.today_pct", 20),
                        100 - past)
            sel.crm_activities = {"enabled": True, "past_pct": past, "today_pct": today}

    sale = _as_dict(modules.get("sale"), "modules.sale")
    if _enabled(sale):
        selected.add("sale")
        sel.sale = _as_int(sale.get("count"), "sale.count", 0, 1000, default=0)
        sel.sale_confirm_pct = _as_pct(sale.get("confirm_pct"), "sale.confirm_pct", 65)

    account = _as_dict(modules.get("account"), "modules.account")
    if _enabled(account):
        selected.add("account")
        sel.account = _as_int(account.get("count"), "account.count", 0, 1000, default=0)
        sel.account_bills = _as_int(account.get("bills"), "account.bills", 0, 1000, default=0)
        sel.create_bank_transactions = _as_bool(account.get("bank_transactions"))

    hr = _as_dict(modules.get("hr"), "modules.hr")
    if _enabled(hr):
        selected.add("hr")
        sel.hr = _as_int(hr.get("count"), "hr.count", 0, 1000, default=0)
        timeoff = _as_dict(hr.get("timeoff"), "hr.timeoff")
        if _enabled(timeoff):
            sel.hr_timeoff = {
                "enabled": True,
                "entries_per_employee": _as_int(timeoff.get("entries_per_employee"),
                                                "hr.timeoff.entries_per_employee", 1, 50, default=2),
                "avg_length_days": _as_int(timeoff.get("avg_length_days"),
                                           "hr.timeoff.avg_length_days", 1, 60, default=5),
                "past_future_pct": _as_pct(timeoff.get("past_future_pct"),
                                           "hr.timeoff.past_future_pct", 30),
                "timescale_days": _as_int(timeoff.get("timescale_days"),
                                          "hr.timeoff.timescale_days", 1, 3650, default=180),
                "validate_pct": _as_pct(timeoff.get("validate_pct"), "hr.timeoff.validate_pct", 100),
            }

    project = _as_dict(modules.get("project"), "modules.project")
    if _enabled(project):
        selected.add("project")
        sel.project = _as_int(project.get("count"), "project.count", 0, 500, default=0)
        sel.tasks_per_project = _as_int(project.get("tasks_per_project"),
                                        "project.tasks_per_project", 0, 200, default=10)

    timesheet = _as_dict(modules.get("hr_timesheet"), "modules.hr_timesheet")
    if _enabled(timesheet):
        selected.add("hr_timesheet")
        sel.hr_timesheet = _as_int(timesheet.get("count"), "hr_timesheet.count", 0, 5000, default=0)

    mrp = _as_dict(modules.get("mrp"), "modules.mrp")
    if _enabled(mrp):
        selected.add("mrp")
        components = _as_int(mrp.get("components_per_bom"), "mrp.components_per_bom", 0, 50, default=4)
        sel.mrp = {
            "num_products": _as_int(mrp.get("num_products"), "mrp.num_products", 0, 200, default=3),
            "components_per_bom": components,
            # Never more sub-BOMs than there are components to hang them off.
            "sub_boms_per_product": min(
                _as_int(mrp.get("sub_boms_per_product"), "mrp.sub_boms_per_product", 0, 50, default=2),
                components,
            ),
            "num_workcenters": _as_int(mrp.get("num_workcenters"), "mrp.num_workcenters", 0, 50, default=3),
            "num_manufacturing_orders": _as_int(mrp.get("num_manufacturing_orders"),
                                                "mrp.num_manufacturing_orders", 0, 200, default=5),
            "create_quality_points": _as_bool(mrp.get("create_quality_points")),
        }

    recruitment = _as_dict(modules.get("hr_recruitment"), "modules.hr_recruitment")
    if _enabled(recruitment):
        selected.add("hr_recruitment")
        sel.hr_recruitment = {
            "num_jobs": _as_int(recruitment.get("num_jobs"), "hr_recruitment.num_jobs", 0, 200, default=5),
            "num_candidates": _as_int(recruitment.get("num_candidates"),
                                      "hr_recruitment.num_candidates", 0, 500, default=15),
            "create_skills": _as_bool(recruitment.get("create_skills"), default=True),
            "num_skill_types": _as_int(recruitment.get("num_skill_types"),
                                       "hr_recruitment.num_skill_types", 0, 50, default=3),
            "skills_per_type": _as_int(recruitment.get("skills_per_type"),
                                       "hr_recruitment.skills_per_type", 0, 50, default=4),
        }

    purchase = _as_dict(modules.get("purchase"), "modules.purchase")
    if _enabled(purchase):
        selected.add("purchase")
        sel.purchase = _as_int(purchase.get("count"), "purchase.count", 0, 500, default=0)
        sel.purchase_confirm_pct = _as_pct(purchase.get("confirm_pct"), "purchase.confirm_pct", 70)

    stock = _as_dict(modules.get("stock"), "modules.stock")
    if _enabled(stock):
        selected.add("stock")
        # dict-shaped, not a bare int: orchestrator's module_code "stock" is
        # looked up via getattr(ModuleSelections, "stock") — see config.py.
        sel.stock = {"avg_qty": _as_int(stock.get("avg_qty"), "stock.avg_qty", 0, 100000, default=50)}

    documents = _as_dict(modules.get("documents"), "modules.documents")
    if _enabled(documents):
        selected.add("documents")
        sel.documents = {
            "bill_pdfs_enabled": _as_bool(documents.get("bill_pdfs"), default=True),
            "cv_pdfs_enabled": _as_bool(documents.get("cv_pdfs"), default=True),
        }

    return sel, selected


def build_context(payload: Dict[str, Any], *, language_name: str, language_code: str,
                  llm_model_name: str, installed_modules: Set[str],
                  feature_flags: Dict[str, bool],
                  existing_company_ids=None, existing_product_ids=None) -> Tuple[RunContext, Set[str]]:
    """Assemble a RunContext from a validated request payload.

    `feature_flags` is not optional in practice: passing `{}` silently disables
    every MRP work center, BOM operation and quality point (mrp.py:268/:347) and
    CRM leads. The connect endpoint must supply the probed flags.
    """
    criteria = build_criteria(payload)
    selections, selected = build_selections(payload)

    ctx = RunContext(
        criteria=criteria,
        module_selections=selections,
        industry=criteria.industry,
        language_name=language_name,
        language_code=language_code,
        gemini_model_name=llm_model_name,
        installed_modules=set(installed_modules or set()),
        feature_flags=dict(feature_flags or {}),
    )

    if _as_bool(payload.get("use_existing")):
        ctx.company_ids.extend(existing_company_ids or [])
        ctx.product_ids.extend(existing_product_ids or [])

    ctx.skip_master_data = _as_bool(payload.get("skip_master_data"))
    return ctx, selected


def active_progress_keys(ctx: RunContext, selected: Set[str]) -> list:
    """Progress rows for a run, in execution order.

    Gated on installed AND selected (B10), except the "documents" pseudo-module
    which has no installed_modules entry and is gated on selection only.
    """
    keys = [] if ctx.skip_master_data else ["stammdaten"]
    for key in MODULE_RUN_ORDER:
        if key in PSEUDO_MODULES:
            if key in selected:
                keys.append(key)
        elif key in ctx.installed_modules and key in selected:
            keys.append(key)
    return keys


def estimate_record_counts(ctx: RunContext, selected: Set[str]) -> Dict[str, int]:
    """Pre-flight summary numbers, derived arithmetically from the config.

    Deliberately not a dry run — these are the records this tool asks Odoo to
    create. Odoo's own automation creates more on top (a project and task per
    confirmed service order, invoices via the wizard, vendor bills from POs,
    applied stock moves); the UI names those separately rather than guessing.
    """
    c = ctx.criteria
    sel = ctx.module_selections
    counts: Dict[str, int] = {}

    if not ctx.skip_master_data:
        per_company = 1 + c.num_delivery_contacts + c.num_invoice_contacts + c.num_other_contacts
        counts["Kontakte"] = c.num_companies * per_company
        counts["Produkte"] = c.num_services + c.num_consumables + c.num_storables

    if "crm" in selected:
        if sel.crm:
            counts["Opportunities"] = sel.crm
        if sel.leads:
            counts["Leads"] = sel.leads
        if sel.crm_chatter:
            counts["Chatter-Nachrichten"] = sel.crm * int(sel.crm_chatter.get("messages_per_opp", 0))
        if sel.crm_activities:
            counts["Aktivitäten"] = sel.crm
    if "sale" in selected and sel.sale:
        counts["Aufträge"] = sel.sale
    if "account" in selected:
        if sel.account:
            counts["Kundenrechnungen"] = sel.account
        if sel.account_bills:
            counts["Eingangsrechnungen"] = sel.account_bills
    if "hr" in selected and sel.hr:
        counts["Mitarbeiter"] = sel.hr
        if sel.hr_timeoff:
            counts["Urlaubsanträge"] = sel.hr * int(sel.hr_timeoff.get("entries_per_employee", 0))
    if "project" in selected and sel.project:
        counts["Projekte"] = sel.project
        counts["Aufgaben"] = sel.project * sel.tasks_per_project
    if "hr_timesheet" in selected and sel.hr_timesheet:
        counts["Zeiteinträge"] = sel.hr_timesheet
    if "mrp" in selected and sel.mrp:
        counts["Fertigungsprodukte"] = int(sel.mrp.get("num_products", 0))
        counts["Arbeitszentren"] = int(sel.mrp.get("num_workcenters", 0))
        counts["Fertigungsaufträge"] = int(sel.mrp.get("num_manufacturing_orders", 0))
    if "hr_recruitment" in selected and sel.hr_recruitment:
        counts["Stellen"] = int(sel.hr_recruitment.get("num_jobs", 0))
        counts["Bewerbungen"] = int(sel.hr_recruitment.get("num_candidates", 0))
    if "purchase" in selected and sel.purchase:
        counts["Bestellungen"] = sel.purchase
    if "stock" in selected and sel.stock:
        counts["Lagerbestände"] = c.num_storables or 0

    return {label: value for label, value in counts.items() if value}
