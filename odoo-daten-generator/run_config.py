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
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from config import (
    ActivitiesConfig, AnalyticConfig, ChatterConfig, DemoCriteria, DocumentsConfig,
    ExpenseConfig, LostConfig, ModuleSelections, MrpConfig, RecruitmentConfig,
    RunContext, StockConfig, TimeoffConfig,
)
from odoo_actions import PRIMARY_MODEL_PER_MODULE

logger = logging.getLogger(__name__)

# Odoo modules probed via ir.module.module on connect.
#
# hr_holidays/hr_work_entry (S10/R10): hr.leave/hr.leave.allocation/
# hr.work.entry.type ship with these, NOT with hr — employees installed does
# not imply absences installed. Without a probe for them, modules/hr.py's
# create_leave_data fired as soon as 'hr' was installed and failed loudly on
# every one of those models when the leave apps weren't there. They are
# GATE_ONLY_MODULES below: probed and labelled, never a selectable module.
#
# hr_recruitment_skills (S11/R5, found live 2026-09-02 via WP1's manifest
# capture run on demo-test5): hr.applicant.applicant_skill_ids ships with
# THIS submodule, not with hr_recruitment itself — hr_recruitment and
# hr_skills can both be installed while this one stays off, exactly the same
# gap class as hr_holidays/hr_work_entry above. Confirmed live: fields_get on
# hr.applicant returns no skill-related field at all when it's uninstalled,
# and create_batch fails loudly ("Invalid field 'applicant_skill_ids'") the
# moment any candidate gets a skill sampled — modules/recruiting.py gates on
# it (see _applicant_skill_lines).
WANTED_MODULES = [
    "crm", "sale", "account", "hr", "project",
    "hr_timesheet", "mrp", "hr_recruitment",
    "purchase", "stock", "hr_holidays", "hr_work_entry",
    "hr_recruitment_skills", "hr_expense",
]

# "documents" is deliberately absent above: it is a pseudo-module. It attaches
# ir.attachment records, which are core Odoo, and "documents" also happens to be
# the technical name of Odoo's unrelated real Documents app — probing for it
# would gate PDF generation on an app nobody installed.
PSEUDO_MODULES = ["documents"]

# Probed (WANTED_MODULES) and labelled (MODULE_LABELS), but never a progress
# row, never a ModuleSelections field, never entered into MODULE_RUN_ORDER or
# orchestrator.py's module_order — they only gate a sub-behaviour of an
# already-installed module (modules/hr.py's create_leave_data). Kept as their
# own set, not folded into WANTED_MODULES's normal treatment, because every
# WANTED_MODULES entry is otherwise assumed to be a run_config-selectable
# module — see active_progress_keys and test_run_config_unit.py's invariant.
GATE_ONLY_MODULES = {"hr_holidays", "hr_work_entry", "hr_recruitment_skills"}

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
    "hr_holidays": "Abwesenheiten",
    "hr_work_entry": "Arbeitszeiterfassung",
    "hr_recruitment_skills": "Bewerber-Skills",
    "hr_expense": "Spesen",
    "documents": "Dokumente (PDFs)",
    "stammdaten": "Stammdaten",
}

# Progress rows, in the order orchestrator.py actually executes them.
# "stammdaten" is prepended by the caller unless skip_master_data is set.
# GATE_ONLY_MODULES are deliberately absent — they never run as their own
# step, see the comment there.
MODULE_RUN_ORDER = [
    "mrp", "crm", "sale", "hr", "project", "hr_timesheet",
    "account", "hr_recruitment", "purchase", "stock", "hr_expense", "documents",
]

# Maps orchestrator's on_module_start/on_module_done names onto progress-row keys.
# orchestrator passes the module_code for everything except master data, which it
# calls "Stammdaten".
PROGRESS_KEY_MAP = {"Stammdaten": "stammdaten"}

VALID_MODES = ("master", "both")
VALID_CHATTER_STYLES = ("notes_only", "mixed", "full_email")

# Consent for letting values read out of the target database reach an LLM prompt.
# Exactly one prompt is affected (modules/crm.py's chatter): the customer name and
# the salesperson name. Everything else the pipeline sends is LLM-invented or was
# created by this run; existing products are used as IDs only, never as text.
CONSENT_GRANTED = "granted"
CONSENT_DENIED = "denied"
VALID_CONSENT = (CONSENT_GRANTED, CONSENT_DENIED)

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


# S16, D11/Anforderungs-Punkt 4: DACH-only for the first cut (matches
# modules/master_data.py's own _TARGET_COUNTRIES and data_factory.py's
# _DEFAULT_COUNTRIES — kept as a separate constant here rather than an
# import, since this file is deliberately module-agnostic, framework-free).
_TARGET_COUNTRY_CODES = {"DE", "AT", "CH"}

# S16/D11: no company count is officially decided yet — this is a
# deliberately generous placeholder, not a load-bearing product decision.
# What matters architecturally (per the cold-review) is that the cap is
# enforced server-side, not left to a UI-only checkbox limit, since
# POST /api/runs is directly callable.
MAX_COMPANIES = 20


def _as_list(value: Any, label: str, *, min_len: int = 1, max_len: int = MAX_COMPANIES) -> List[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"'{label}' muss eine Liste sein.")
    if len(value) < min_len:
        raise ConfigError(f"'{label}' braucht mindestens {min_len} Eintrag/Einträge.")
    if len(value) > max_len:
        raise ConfigError(f"'{label}' erlaubt höchstens {max_len} Einträge.")
    return value


VALID_TARGET_MODES = ("new", "existing")


def _validate_target(target: Dict[str, Any], index: int) -> None:
    """S16/D11: full validation of one company's `target` block, at request
    time — a ConfigError here becomes an HTTP 400 on POST /api/runs, not a
    failure minutes into a run. Existing-company-id existence itself is NOT
    checked here (run_config.py makes no Odoo calls, D10-Korrektur) — that
    happens in web/jobs.py's per-company loop against the live instance.
    """
    label = f"companies[{index}].target"
    mode = target.get("mode")
    if mode not in VALID_TARGET_MODES:
        raise ConfigError(f"'{label}.mode' muss 'new' oder 'existing' sein.")
    if mode == "new":
        if not (target.get("name") or "").strip():
            raise ConfigError(f"'{label}.name' ist für eine neue Firma Pflicht.")
        country = target.get("country")
        if country not in _TARGET_COUNTRY_CODES:
            raise ConfigError(
                f"'{label}.country' muss eines von {sorted(_TARGET_COUNTRY_CODES)} sein.")
    else:
        _as_int(target.get("company_id"), f"{label}.company_id", 1, 2**31 - 1)


# ---------------------------------------------------------------------------
# Payload → dataclasses
# ---------------------------------------------------------------------------

def validate_consent(payload: Dict[str, Any], *, reuse_requested: bool = False) -> Optional[str]:
    """Check the existing-data consent answer.

    Including existing records means the chatter prompt can carry a real
    customer's name, so the answer must be an explicit yes or no — an unanswered
    question is refused rather than silently treated as either.

    S16/D11 Konsens-Entscheidung: `reuse_requested` is the multi-company
    equivalent of `use_existing` — True when at least one company block in
    the payload has `target.reuse_master_data=True` (checked once, by
    build_context_list, across the whole `companies` list, since this
    function only ever sees one block's flat dict). `existing_data_consent`
    itself stays a single top-level answer, not duplicated per company —
    the question is inherently global (same LLM, same chatter prompt rules
    regardless of which company's data is being reused).
    """
    consent = payload.get("existing_data_consent")
    if consent is not None and consent not in VALID_CONSENT:
        raise ConfigError(f"Unbekannte Einwilligung '{consent}'.")
    if (_as_bool(payload.get("use_existing")) or reuse_requested) and consent != CONSENT_GRANTED:
        raise ConfigError(
            "Vorhandene Daten einbeziehen erfordert eine Entscheidung: Ohne Zustimmung "
            "können Namen aus der Zieldatenbank nicht an den LLM-Anbieter gehen. "
            "Bitte zustimmen oder die Option abwählen.")
    return consent


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
            sel.crm_chatter = ChatterConfig(
                style=style,
                messages_per_opp=_as_int(chatter.get("messages_per_opp"),
                                         "crm.chatter.messages_per_opp", 1, 50, default=4),
                # Real customer/salesperson names only with explicit consent.
                use_db_names=payload.get("existing_data_consent") == CONSENT_GRANTED,
            )
        activities = _as_dict(crm.get("activities"), "crm.activities")
        if _enabled(activities):
            past = _as_pct(activities.get("past_pct"), "crm.activities.past_pct", 30)
            today = min(_as_pct(activities.get("today_pct"), "crm.activities.today_pct", 20),
                        100 - past)
            sel.crm_activities = ActivitiesConfig(past_pct=past, today_pct=today)
        # R11: crm_lost only settable when crm itself is enabled — a crm.py
        # sub-feature (like chatter/activities above), not its own module.
        # Set here, inside `if _enabled(crm)`, so "CRM installed but not
        # selected" can never leave crm_lost active with an empty
        # ctx.opportunity_ids (modules/crm.py's mark_lost_opportunities also
        # guards this independently, but this is the cleaner place to
        # prevent it).
        lost = _as_dict(crm.get("lost"), "crm.lost")
        if _enabled(lost):
            sel.crm_lost = LostConfig(pct=_as_pct(lost.get("pct"), "crm.lost.pct", 20))

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
            sel.hr_timeoff = TimeoffConfig(
                entries_per_employee=_as_int(timeoff.get("entries_per_employee"),
                                             "hr.timeoff.entries_per_employee", 1, 50, default=2),
                avg_length_days=_as_int(timeoff.get("avg_length_days"),
                                        "hr.timeoff.avg_length_days", 1, 60, default=5),
                past_future_pct=_as_pct(timeoff.get("past_future_pct"),
                                        "hr.timeoff.past_future_pct", 30),
                timescale_days=_as_int(timeoff.get("timescale_days"),
                                       "hr.timeoff.timescale_days", 1, 3650, default=180),
                validate_pct=_as_pct(timeoff.get("validate_pct"), "hr.timeoff.validate_pct", 100),
            )

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
        sel.mrp = MrpConfig(
            num_products=_as_int(mrp.get("num_products"), "mrp.num_products", 0, 200, default=3),
            components_per_bom=components,
            # Never more sub-BOMs than there are components to hang them off.
            sub_boms_per_product=min(
                _as_int(mrp.get("sub_boms_per_product"), "mrp.sub_boms_per_product", 0, 50, default=2),
                components,
            ),
            num_workcenters=_as_int(mrp.get("num_workcenters"), "mrp.num_workcenters", 0, 50, default=3),
            num_manufacturing_orders=_as_int(mrp.get("num_manufacturing_orders"),
                                             "mrp.num_manufacturing_orders", 0, 200, default=5),
            create_quality_points=_as_bool(mrp.get("create_quality_points")),
            quality_fail_pct=_as_pct(mrp.get("quality_fail_pct"), "mrp.quality_fail_pct", default=0),
        )

    recruitment = _as_dict(modules.get("hr_recruitment"), "modules.hr_recruitment")
    if _enabled(recruitment):
        selected.add("hr_recruitment")
        sel.hr_recruitment = RecruitmentConfig(
            num_jobs=_as_int(recruitment.get("num_jobs"), "hr_recruitment.num_jobs", 0, 200, default=5),
            num_candidates=_as_int(recruitment.get("num_candidates"),
                                   "hr_recruitment.num_candidates", 0, 500, default=15),
            create_skills=_as_bool(recruitment.get("create_skills"), default=True),
            num_skill_types=_as_int(recruitment.get("num_skill_types"),
                                    "hr_recruitment.num_skill_types", 0, 50, default=3),
            skills_per_type=_as_int(recruitment.get("skills_per_type"),
                                    "hr_recruitment.skills_per_type", 0, 50, default=4),
        )

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
        lot_pct = _as_pct(stock.get("tracking_lot_pct"), "stock.tracking_lot_pct", default=0)
        serial_pct = _as_pct(stock.get("tracking_serial_pct"), "stock.tracking_serial_pct", default=0)
        if lot_pct + serial_pct > 100:
            # Same clamp as data_factory.assign_tracking's own internal
            # guard (S13/S8) — defense in depth, not a substitute for it.
            serial_pct = 100 - lot_pct
        # S14/R12: independent of avg_qty (would degenerate to 0.0 on the
        # avg_qty=0 path otherwise, Befund 7) — minimum=1 on both so "max >
        # min" can't be satisfied by a meaningless min=0/max=1 pair either.
        # Cross-field relationship clamped here, not rejected — same
        # precedent as tracking_lot_pct+tracking_serial_pct above.
        orderpoint_min_qty = _as_int(
            stock.get("orderpoint_min_qty"), "stock.orderpoint_min_qty", 1, 100000, default=5)
        orderpoint_max_qty = _as_int(
            stock.get("orderpoint_max_qty"), "stock.orderpoint_max_qty", 1, 100000, default=20)
        if orderpoint_max_qty <= orderpoint_min_qty:
            orderpoint_max_qty = orderpoint_min_qty + 1
        sel.stock = StockConfig(
            avg_qty=_as_int(stock.get("avg_qty"), "stock.avg_qty", 0, 100000, default=50),
            sub_locations=_as_int(stock.get("sub_locations"), "stock.sub_locations", 0, 50, default=0),
            second_warehouse=_as_bool(stock.get("second_warehouse")),
            tracking_lot_pct=lot_pct,
            tracking_serial_pct=serial_pct,
            tracking_serial_max=_as_int(
                stock.get("tracking_serial_max"), "stock.tracking_serial_max", 1, 1000, default=10),
            orderpoints_pct=_as_pct(stock.get("orderpoints_pct"), "stock.orderpoints_pct", default=0),
            orderpoint_min_qty=orderpoint_min_qty,
            orderpoint_max_qty=orderpoint_max_qty,
        )

    hr_expense = _as_dict(modules.get("hr_expense"), "modules.hr_expense")
    if _enabled(hr_expense):
        selected.add("hr_expense")
        sel.hr_expense = ExpenseConfig(
            count_per_employee=_as_int(hr_expense.get("count_per_employee"),
                                       "hr_expense.count_per_employee", 0, 100, default=3),
            approved_pct=_as_pct(hr_expense.get("approved_pct"), "hr_expense.approved_pct", 70),
        )

    documents = _as_dict(modules.get("documents"), "modules.documents")
    if _enabled(documents):
        selected.add("documents")
        sel.documents = DocumentsConfig(
            bill_pdfs_enabled=_as_bool(documents.get("bill_pdfs"), default=True),
            cv_pdfs_enabled=_as_bool(documents.get("cv_pdfs"), default=True),
        )

    # S15/R20: not its own orchestrated module (no WANTED_MODULES/
    # MODULE_RUN_ORDER/orchestrator.py entry, no progress row — see
    # ROADMAP.md's S15 section) — added to `selected` anyway, same as
    # "documents", purely so estimate_record_counts below can gate its
    # preview line the same way every other selected feature does. Each
    # individual pct is still meaningless unless its OWN parent module
    # (sale/purchase/hr_expense) is also selected — checked separately at
    # each read site, not here.
    analytic = _as_dict(modules.get("analytic"), "modules.analytic")
    if _enabled(analytic):
        selected.add("analytic")
        sel.analytic = AnalyticConfig(
            sale_pct=_as_pct(analytic.get("sale_pct"), "analytic.sale_pct", 0),
            purchase_pct=_as_pct(analytic.get("purchase_pct"), "analytic.purchase_pct", 0),
            expense_pct=_as_pct(analytic.get("expense_pct"), "analytic.expense_pct", 0),
        )

    return sel, selected


def effective_installed_modules(installed: Set[str],
                                model_access: Dict[str, bool]) -> Tuple[Set[str], Set[str]]:
    """Split `installed` into (usable, blocked) using each module's primary
    write-access probe (odoo_actions.PRIMARY_MODEL_PER_MODULE).

    A module with no primary-model entry (documents' pseudo-module, or any
    future module key this mapping hasn't caught up with) is always usable —
    there's nothing to gate it on. A module WITH a primary entry that was
    never probed (missing from model_access) is also usable: `.get(model,
    True)` defaults open, the same B1-error-class guard as everywhere else
    model_access is read. Only an explicit False blocks a module.

    Single source of truth for this decision: called both here (to filter
    ctx.installed_modules so orchestrator.py needs no change at all) and from
    connect_service (so the API reports the SAME decision the run will make,
    rather than the frontend re-deriving its own answer from the raw probe
    dict and risking a different one for a module with a blocked secondary
    model but a writable primary one).
    """
    usable: Set[str] = set()
    blocked: Set[str] = set()
    for module in installed:
        primary = PRIMARY_MODEL_PER_MODULE.get(module)
        if primary is None or model_access.get(primary, True):
            usable.add(module)
        else:
            blocked.add(module)
    return usable, blocked


def build_context(payload: Dict[str, Any], *, language_name: str, language_code: str,
                  llm_model_name: str, installed_modules: Set[str],
                  feature_flags: Dict[str, bool],
                  model_access: Optional[Dict[str, bool]] = None,
                  existing_partner_company_ids=None, existing_product_ids=None) -> Tuple[RunContext, Set[str]]:
    """Assemble a RunContext from a validated request payload.

    `feature_flags` is not optional in practice: passing `{}` silently disables
    every MRP work center, BOM operation and quality point (mrp.py:268/:347) and
    CRM leads. The connect endpoint must supply the probed flags.

    `model_access` gates `installed_modules` down to modules the API-key user
    can actually write to (effective_installed_modules) — installed-but-
    unwritable modules never enter ctx.installed_modules, so orchestrator.py's
    existing `"mod" in ctx.installed_modules` gate skips them without any
    change to that locked file. `installed_modules` itself is not renamed
    because that field's name is what every module reads.
    """
    validate_consent(payload)
    criteria = build_criteria(payload)
    selections, selected = build_selections(payload)

    access = dict(model_access or {})
    usable_modules, blocked_modules = effective_installed_modules(
        set(installed_modules or set()), access)
    if blocked_modules:
        logger.warning(
            f"[access] Modul(e) ohne Schreibrechte, aus installed_modules entfernt: "
            f"{', '.join(sorted(blocked_modules))}")

    ctx = RunContext(
        criteria=criteria,
        module_selections=selections,
        industry=criteria.industry,
        language_name=language_name,
        language_code=language_code,
        gemini_model_name=llm_model_name,
        installed_modules=usable_modules,
        feature_flags=dict(feature_flags or {}),
        model_access=access,
    )

    if _as_bool(payload.get("use_existing")):
        ctx.partner_company_ids.extend(existing_partner_company_ids or [])
        ctx.product_ids.extend(existing_product_ids or [])

    ctx.skip_master_data = _as_bool(payload.get("skip_master_data"))
    return ctx, selected


def build_context_list(payload: Dict[str, Any], *, language_name: str, language_code: str,
                       llm_model_name: str, installed_modules: Set[str],
                       feature_flags: Dict[str, bool],
                       model_access: Optional[Dict[str, bool]] = None,
                       ) -> List[Tuple[RunContext, Set[str]]]:
    """S16/D11: assemble N independent RunContexts from a
    `{"companies": [...]}` payload — one call per list element, each
    element having exactly today's single-company payload shape (mode/
    industry/master_data/modules/skip_master_data) plus a `target` block
    (D9) saying which company it's for. `build_criteria`/`build_selections`
    are unchanged — this only adds a loop and the two things a single
    `build_context(block)` call structurally cannot do on its own:
    Consent-Injektion (a block never sees the top-level
    `existing_data_consent`) and cross-block validation (whether ANY
    company requests reuse, decided once, not per block).

    Deliberately Odoo-call-free (D10-Korrektur, matching this module's own
    "no Odoo calls" contract) — target-company resolution (create a new
    res.company, or resolve an existing one) happens later, in
    web/jobs.py's per-company loop, where a JournalingClient actually
    exists to journal a newly created company for cleanup. This function
    also does not merge existing-company data into ctx.partner_company_ids/
    product_ids — that "existing_partner_company_ids"/"existing_product_ids"
    mechanism on `build_context` is Firma-1-shaped and superseded here by
    D8b's per-company scoped fetch, called from the same later loop.

    Takes the same connection-level kwargs `build_context` does, minus
    `existing_partner_company_ids`/`existing_product_ids` (not applicable — see
    above) — one connect result applies identically to every company.
    """
    companies_raw = _as_list(payload.get("companies"), "companies")
    targets: List[Dict[str, Any]] = []
    for index, block in enumerate(companies_raw):
        target = _as_dict(block.get("target"), f"companies[{index}].target")
        _validate_target(target, index)
        targets.append(target)

    # Konsens-Entscheidung: consent is checked ONCE, across the whole list —
    # build_context's own internal validate_consent(block) call below only
    # ever sees one block and cannot answer "does ANY company reuse data".
    reuse_requested = any(t.get("reuse_master_data") for t in targets)
    validate_consent(payload, reuse_requested=reuse_requested)

    existing_consent = payload.get("existing_data_consent")
    results: List[Tuple[RunContext, Set[str]]] = []
    for block in companies_raw:
        # D11 Korrektur 5: existing_data_consent is top-level in the
        # payload, but build_selections (crm_chatter.use_db_names) reads it
        # off the per-block dict it's handed — inject it into every block
        # before delegating, or the consent gate silently never fires.
        block_payload = {**block, "existing_data_consent": existing_consent}
        results.append(build_context(
            block_payload,
            language_name=language_name, language_code=language_code,
            llm_model_name=llm_model_name, installed_modules=installed_modules,
            feature_flags=feature_flags, model_access=model_access,
        ))
    return results


def active_progress_keys(ctx: RunContext, selected: Set[str]) -> list:
    """Progress rows for one company's run, in execution order.

    Gated on installed AND selected (B10), except the "documents" pseudo-module
    which has no installed_modules entry and is gated on selection only.

    S16/D6: unchanged — no company-qualification parameter here, unlike
    estimate_record_counts. This returns bare module codes; the multi-company
    caller (web/jobs.py's per-company loop) calls this once per company and
    qualifies the returned keys itself (e.g. f"{index}:{key}") when building
    the combined, firmen-qualified progress list — qualification is a
    machine-key concern for that caller, not a label-text concern this
    function needs to know about.
    """
    keys = [] if ctx.skip_master_data else ["stammdaten"]
    for key in MODULE_RUN_ORDER:
        if key in PSEUDO_MODULES:
            if key in selected:
                keys.append(key)
        elif key in ctx.installed_modules and key in selected:
            keys.append(key)
    return keys


def estimate_record_counts(ctx: RunContext, selected: Set[str],
                           *, company_label: Optional[str] = None) -> Dict[str, int]:
    """Pre-flight summary numbers, derived arithmetically from the config.

    Deliberately not a dry run — these are the records this tool asks Odoo to
    create. Odoo's own automation creates more on top (a project and task per
    confirmed service order, invoices via the wizard, vendor bills from POs,
    applied stock moves); the UI names those separately rather than guessing.

    `company_label`, if given, qualifies every label for a multi-company
    preview (S16/D6) — see the qualification note at the return below.
    Omitted (the default): today's single-company behavior, unqualified.
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
            counts["Chatter-Nachrichten"] = sel.crm * int(sel.crm_chatter.messages_per_opp)
        if sel.crm_activities:
            counts["Aktivitäten"] = sel.crm
        if sel.crm_lost:
            # Upper bound, not exact: the actual share is applied only to
            # opportunities sale.py leaves unlinked, which this arithmetic
            # pre-flight can't know ahead of the run.
            counts["Verlorene Opportunities (max.)"] = round(sel.crm * sel.crm_lost.pct / 100)
    if "sale" in selected and sel.sale:
        counts["Aufträge"] = sel.sale
        if "analytic" in selected and sel.analytic and sel.analytic.sale_pct:
            # (ca.): only confirmed orders' lines are eligible (sale.py's
            # own post-confirm step, see modules/sale.py), and only those
            # still lacking a distribution — a service_tracking line
            # already carrying Odoo's own native one is skipped. 3 is
            # sale.py's own average of its 1-5 random draw, not a real count.
            num_confirmed = max(1, round(sel.sale * sel.sale_confirm_pct / 100))
            counts["Kostenrechnungs-Zeilen Verkauf (ca.)"] = round(
                num_confirmed * 3 * sel.analytic.sale_pct / 100)
    if "account" in selected:
        if sel.account:
            counts["Kundenrechnungen"] = sel.account
        if sel.account_bills:
            counts["Eingangsrechnungen"] = sel.account_bills
    if "hr" in selected and sel.hr:
        counts["Mitarbeiter"] = sel.hr
        if sel.hr_timeoff:
            counts["Urlaubsanträge"] = sel.hr * int(sel.hr_timeoff.entries_per_employee)
    if "project" in selected and sel.project:
        counts["Projekte"] = sel.project
        counts["Aufgaben"] = sel.project * sel.tasks_per_project
    if "hr_timesheet" in selected and sel.hr_timesheet:
        counts["Zeiteinträge"] = sel.hr_timesheet
    if "mrp" in selected and sel.mrp:
        counts["Fertigungsprodukte"] = int(sel.mrp.num_products)
        counts["Arbeitszentren"] = int(sel.mrp.num_workcenters)
        counts["Fertigungsaufträge"] = int(sel.mrp.num_manufacturing_orders)
        if sel.mrp.create_quality_points:
            # S14/R18: one quality.point per BOM (main + sub-BOMs, same
            # count mrp.py's own bom_vals_list produces) — exact, not "(ca.)",
            # since it doesn't depend on a random roll like the MO count
            # below does. quality.check needs a CONFIRMED MO to link to
            # (mrp.py's ~70% action_confirm roll), so that estimate stays
            # approximate like Nachbestellregeln above.
            num_products_mrp = int(sel.mrp.num_products)
            sub_boms = int(sel.mrp.sub_boms_per_product)
            counts["Qualitätsprüfpunkte"] = num_products_mrp * (1 + sub_boms)
            num_mo = int(sel.mrp.num_manufacturing_orders)
            if num_mo:
                counts["Qualitätsprüfungen (ca.)"] = round(num_mo * 0.7)
    if "hr_recruitment" in selected and sel.hr_recruitment:
        counts["Stellen"] = int(sel.hr_recruitment.num_jobs)
        counts["Bewerbungen"] = int(sel.hr_recruitment.num_candidates)
    if "purchase" in selected and sel.purchase:
        counts["Bestellungen"] = sel.purchase
        if "analytic" in selected and sel.analytic and sel.analytic.purchase_pct:
            # 2 is purchase.py's own average of its 1-3 random draw, not a real count.
            counts["Kostenrechnungs-Zeilen Einkauf (ca.)"] = round(
                sel.purchase * 2 * sel.analytic.purchase_pct / 100)
    if "stock" in selected and sel.stock:
        # S14: gated on avg_qty>0 — an orderpoints-only run (avg_qty=0,
        # orderpoints_pct>0) never seeds any stock quantities, and S14 turns
        # that from an edge case into a normal config path.
        if int(sel.stock.avg_qty) > 0:
            counts["Lagerbestände"] = c.num_storables or 0
        counts["Lagerplätze"] = int(sel.stock.sub_locations)
        if sel.stock.second_warehouse:
            counts["Zweites Lager"] = 1
        lot_pct = int(sel.stock.tracking_lot_pct)
        serial_pct = int(sel.stock.tracking_serial_pct)
        if lot_pct:
            counts["Chargen (Lot-Nummern)"] = round((c.num_storables or 0) * lot_pct / 100)
        if serial_pct:
            serial_max = int(sel.stock.tracking_serial_max)
            # Upper bound, not exact — N per serial product is random up to
            # serial_max (crm_lost's "(max.)" pattern above), soft-capped at
            # modules/inventory.py's own _MAX_SERIAL_RECORDS_PER_RUN (500,
            # not imported here to avoid reaching into a private module
            # constant — kept in sync by hand, both are S13 additions). The
            # min(..., 500) below is a display ceiling, not a hard guarantee:
            # inventory.py's soft-cap design never drops a serial product to
            # 0 once the budget is spent (it still gets 1 quant+lot), so the
            # real total can exceed 500 by up to the count of serial
            # products processed after exhaustion — this preview undercounts
            # in that edge case rather than lying about a smaller number.
            counts["Seriennummern (max.)"] = min(
                round((c.num_storables or 0) * serial_pct / 100) * serial_max, 500)
        orderpoints_pct = int(sel.stock.orderpoints_pct)
        if orderpoints_pct:
            # Approximate in both directions, not just one (unlike the lot/
            # serial rows above): undercounts because it only reads
            # num_storables, not the MRP components S14/Befund 3 also
            # targets (which aren't part of num_storables at all);
            # overcounts on a use_existing/skip_master_data run, where
            # new_product_ids stays empty and the real count is 0. "(ca.)"
            # signals both, deliberately not "(max.)" like the row above.
            counts["Nachbestellregeln (ca.)"] = round((c.num_storables or 0) * orderpoints_pct / 100)
    if "hr_expense" in selected and sel.hr_expense:
        counts["Spesen"] = sel.hr * int(sel.hr_expense.count_per_employee)
        if "analytic" in selected and sel.analytic and sel.analytic.expense_pct:
            counts["Kostenrechnungs-Zeilen Spesen (ca.)"] = round(
                counts["Spesen"] * sel.analytic.expense_pct / 100)
    if "analytic" in selected and sel.analytic is not None:
        # Fixed set (odoo_actions._ANALYTIC_COST_CENTER_NAMES), created once
        # per run regardless of how many of sale/purchase/hr_expense end up
        # using them.
        counts["Kostenstellen"] = 3

    counts = {label: value for label, value in counts.items() if value}
    if company_label:
        # S16/D6: qualify every label with which company it belongs to —
        # except "Kostenstellen" (D12): that's one shared plan for the
        # whole multi-company run, not one per company, so qualifying it
        # would misrepresent a total that doesn't scale with N. The caller
        # is expected to merge company-scoped previews across companies
        # and keep "Kostenstellen" only once (first occurrence), not summed.
        counts = {
            (label if label == "Kostenstellen" else f"{label} ({company_label})"): value
            for label, value in counts.items()
        }
    return counts


def multi_company_preview(contexts_and_selected: List[Tuple[RunContext, Set[str]]],
                          labels: Optional[List[str]] = None) -> Tuple[List[str], Dict[str, int]]:
    """S16/D6: shared by web/jobs.py's JobQueue.submit() and web/app.py's
    /api/preflight — builds the company-qualified progress-key list and the
    merged record-count preview for either a single-company run
    (`labels=None`, or exactly one context) or a multi-company one
    (`labels` has one entry per context, used to qualify both the module
    keys — "{index}:{key}" — and estimate_record_counts' labels).

    Kept as one function so the two callers cannot silently drift apart on
    the merge rule for a shared label like "Kostenstellen" (D12: run-wide,
    kept only once, never summed).

    S16/B1 (pre-merge cold review): key qualification and label qualification
    are DIFFERENT decisions, gated on different things — conflating them into
    one `multi` flag was the bug. web/jobs.py's `_execute()` dispatches to
    the qualified-key branch whenever `targets is not None` (i.e. whenever
    the caller sent the "companies" payload shape at all, regardless of
    company count — see its own `if targets is None:` check). Gating key
    qualification here on `len(...) > 1` instead meant a genuine 1-company
    "companies" payload got UNqualified keys from this function while
    _execute() published and looked up QUALIFIED ones — `record.modules`
    then never matched, so every module silently reported "done" at the end
    regardless of whether the run actually failed. Label qualification (the
    cosmetic "(Firma 1)" suffix on record-estimate rows) has no such
    constraint and can still skip itself for a single company.
    """
    qualify_keys = labels is not None
    qualify_labels = labels is not None and len(contexts_and_selected) > 1
    keys: List[str] = []
    estimate: Dict[str, int] = {}
    for index, (ctx, selected) in enumerate(contexts_and_selected):
        company_keys = active_progress_keys(ctx, selected)
        if qualify_keys:
            keys.extend(f"{index}:{key}" for key in company_keys)
        else:
            keys.extend(company_keys)
        if qualify_labels:
            company_estimate = estimate_record_counts(ctx, selected, company_label=labels[index])
            for est_label, value in company_estimate.items():
                if est_label == "Kostenstellen":
                    if est_label not in estimate:  # first occurrence wins, never summed
                        estimate[est_label] = value
                else:
                    estimate[est_label] = estimate.get(est_label, 0) + value
        else:
            estimate.update(estimate_record_counts(ctx, selected))
    return keys, estimate
