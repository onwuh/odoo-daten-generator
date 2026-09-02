"""Shared Odoo action helpers used by entry points (connect_service.py, web/) and
multiple domain modules.

Domain-specific helpers live in their respective modules:
  modules/crm.py, modules/sale.py, modules/accounting.py,
  modules/hr.py, modules/project.py, modules/mrp.py, modules/recruiting.py
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import data_factory
from odoo_repository import resolve_country_ids

logger = logging.getLogger(__name__)


def create_customer(client, customer_data: Dict[str, Any]) -> int:
    """Creates a new customer/partner and returns its ID."""
    logger.info(f"-> Creating Customer/Contact: {customer_data.get('name')}...")
    customer_id = client.create('res.partner', customer_data)
    logger.info(f"   ID: {customer_id}")
    return customer_id


def create_product(client, product_data: Dict[str, Any]) -> int:
    """Creates a new product and returns its ID.

    Shared by master_data and mrp modules.
    """
    logger.info(f"-> Creating Product: {product_data.get('name')}...")
    product_id = client.create('product.product', product_data)
    logger.info(f"   ID: {product_id}")
    return product_id


def create_employee(client, name: str) -> int:
    """Creates an employee and returns its ID.

    Shared by hr and project (fallback) modules.
    """
    logger.info(f"-> Creating Employee: {name}")
    return client.create('hr.employee', {"name": name})


def post_invoices(client, move_ids):
    """Posts account.move records (batch, falling back to per-id on failure)."""
    logger.info(f"-> Posting invoices: {move_ids}")
    try:
        client.call_method('account.move', 'action_post', ids=move_ids)
        return True
    except Exception as e:
        logger.warning(f"[post_invoices] Batch post failed ({e}), retrying individually...")
        success_count = 0
        for mid in move_ids:
            try:
                client.call_method('account.move', 'action_post', ids=[mid])
                success_count += 1
            except Exception as e2:
                logger.warning(f"[post_invoices] Failed to post move {mid}: {e2}")
        return success_count > 0


def create_suppliers(client, names) -> List[int]:
    """Creates supplier res.partner records with a full address (via
    data_factory.build_company), not just a bare name.

    Shared by accounting.py (standalone vendor bills) and purchase.py (POs) so
    a run with both active draws from the same ctx.supplier_ids pool.
    """
    country_map = resolve_country_ids(client, ["DE", "AT", "CH"])
    supplier_ids = []
    for sname in names:
        vals = data_factory.build_company(sname)
        country_code = vals.pop('country_code')
        if country_code in country_map:
            vals['country_id'] = country_map[country_code]
        vals['supplier_rank'] = 1
        sid = client.create('res.partner', vals)
        supplier_ids.append(sid)
    return supplier_ids


def get_default_warehouse(client, company_id) -> Optional[Dict[str, int]]:
    """Returns the company's stock location + incoming picking type, or None.

    Shared by purchase.py (PO picking_type_id) and inventory.py (quant
    location_id). Distinct from mrp.py's get_manufacturing_picking_type_id,
    which searches stock.picking.type directly for the mrp_operation code.
    """
    warehouses = client.search_read(
        'stock.warehouse', [["company_id", "=", company_id]],
        fields=["lot_stock_id", "in_type_id"], limit=1,
    )
    if not warehouses:
        return None
    wh = warehouses[0]
    stock_location_id = wh.get("lot_stock_id")
    if isinstance(stock_location_id, (list, tuple)):
        stock_location_id = stock_location_id[0]
    incoming_picking_type_id = wh.get("in_type_id")
    if isinstance(incoming_picking_type_id, (list, tuple)):
        incoming_picking_type_id = incoming_picking_type_id[0]
    if not stock_location_id or not incoming_picking_type_id:
        return None
    return {
        "stock_location_id": stock_location_id,
        "incoming_picking_type_id": incoming_picking_type_id,
    }


def get_installed_modules(client, wanted_modules: List[str]) -> Set[str]:
    """Returns a set of installed module technical names from wanted_modules."""
    records = client.search_read(
        'ir.module.module',
        [["name", "in", wanted_modules], ["state", "=", "installed"]],
        fields=["name", "state"],
        limit=0,
    )
    return set(r["name"] for r in records)


def get_enabled_features(client, installed_modules=None) -> Dict[str, bool]:
    """Probe for feature flags beyond module installation.

    Args:
        installed_modules: set of installed module names. Probes are skipped when
            the parent module is not in the set, saving unnecessary API calls.
    """
    installed = installed_modules or set()
    flags = {}

    # mrp_routings: can the pipeline actually CREATE work centers/orders? A read
    # probe here (the pre-S10 approach) answers a different question than the
    # one that matters — on demo-test5 it reported True while the "Work Orders"
    # setting checkbox was off, so the run started and was guaranteed to fail.
    # mrp.routing.workcenter is the model the routing path itself writes
    # (modules/mrp.py's create_bom_operation) and the more likely place for a
    # routings-specific ACL than the parent mrp.workcenter.
    if 'mrp' in installed:
        # bool(): has_create_access already returns a real bool, but this flags
        # dict is serialised to JSON (ConnectResult.as_public_dict) and read
        # back with `== True`/truthiness elsewhere — wrapping here means a
        # mocked/stubbed client in a test can never leave a non-bool truthy
        # object sitting in a "feature flag".
        flags['mrp_routings'] = bool(client.has_create_access('mrp.workcenter')
                                     and client.has_create_access('mrp.routing.workcenter'))

    # quality: can the pipeline create quality points? Same read-vs-write gap as
    # above. The existing READ probe on quality.alert.team/quality.point.test_type
    # inside modules/mrp.py stays — that module still needs to know whether those
    # reference records exist at all, which this create-access check does not
    # answer — this flag only replaces what used to gate the create step.
    if 'mrp' in installed or 'quality' in installed:
        flags['quality'] = bool(client.has_create_access('quality.point'))

    # crm_leads: "Use Leads" setting enabled in CRM? Not an access question —
    # it is an ir.config_parameter value, so the read probe stays as-is.
    if 'crm' in installed:
        try:
            params = client.search_read(
                'ir.config_parameter',
                [['key', '=', 'crm.use_lead']],
                fields=['value'],
                limit=1,
            )
            flags['crm_leads'] = bool(params and params[0].get('value') in ('1', 'True', 'true'))
        except Exception:
            flags['crm_leads'] = False

    return flags


# Modules whose write-relevant models have no ir.module.module entry of their
# own but gate a sub-behaviour of an installed module — probed and labelled,
# but never a progress row, never a ModuleSelections field, never entered into
# orchestrator.py's module_order. See run_config.GATE_ONLY_MODULES.
#
# hr_holidays/hr_work_entry: modules/hr.py's create_leave_data writes
# hr.leave/hr.leave.allocation/hr.work.entry.type as soon as 'hr' is installed,
# but those models ship with hr_holidays/hr_work_entry respectively — employees
# installed does not imply absences installed. This was previously probed not
# at all, which is why it failed loudly instead of skipping gracefully.
GATE_ONLY_PROBE_MODULES = ("hr_holidays", "hr_work_entry")

# Module key -> the models that module's code actually create()/create_batch()s.
# Curated against the real call sites in modules/*.py, the same way
# FIELD_COMPAT_WHITELIST below is curated rather than derived — deriving this
# automatically would mean parsing module source.
#
# Deliberately NOT probed, because their access follows their parent model and
# a separate ACL on them is not a documented Odoo pattern: mrp.bom.line,
# project.task.type, account.bank.statement.line, hr.department, hr.skill,
# hr.skill.level.
MODEL_ACCESS_PROBES: Dict[str, List[str]] = {
    "stammdaten": ["res.partner", "product.product"],  # always probed
    "crm": ["crm.lead", "mail.activity"],
    "sale": ["sale.order", "sale.advance.payment.inv"],
    "account": ["account.move", "account.journal", "account.bank.statement"],
    "hr": ["hr.employee"],
    "hr_holidays": ["hr.leave", "hr.leave.allocation"],
    "hr_work_entry": ["hr.work.entry.type"],
    "project": ["project.project", "project.task"],
    "hr_timesheet": ["account.analytic.line"],
    "mrp": ["mrp.bom", "mrp.production", "mrp.workcenter",
           "mrp.routing.workcenter", "quality.point"],
    "hr_recruitment": ["hr.job", "hr.applicant", "hr.skill.type"],
    "purchase": ["purchase.order"],
    "stock": ["stock.quant"],
    "hr_expense": ["hr.expense"],
    "documents": ["ir.attachment"],  # always probed (pseudo-module, see run_config)
}

# The one model per module whose create-access decides whether the whole
# module can run at all — used by run_config.effective_installed_modules to
# decide which modules get dropped from ctx.installed_modules. A module with
# several probed models can have a secondary one blocked without being
# useless (e.g. crm.lead writable but mail.activity not); only the primary
# model's access controls the all-or-nothing module gate.
PRIMARY_MODEL_PER_MODULE: Dict[str, str] = {
    "crm": "crm.lead",
    "sale": "sale.order",
    "account": "account.move",
    "hr": "hr.employee",
    "project": "project.project",
    "hr_timesheet": "account.analytic.line",
    "mrp": "mrp.bom",
    "hr_recruitment": "hr.applicant",
    "purchase": "purchase.order",
    "stock": "stock.quant",
    "hr_expense": "hr.expense",
}


def probe_model_access(client, installed_modules) -> Dict[str, bool]:
    """has_create_access() for every model the pipeline might write, once.

    Only models whose parent module key is installed are probed (or that carry
    no such gate at all — "stammdaten" and "documents"), mirroring
    get_enabled_features' existing installed-module gating. Duplicate models
    across module keys are probed once. A model whose probe raises is treated
    as an indeterminate "True" by has_create_access itself — this function
    does not add a second layer of exception handling on top.
    """
    installed = set(installed_modules or set())
    wanted: Set[str] = set()
    for module_key, models in MODEL_ACCESS_PROBES.items():
        if module_key in ("stammdaten", "documents") or module_key in installed:
            wanted.update(models)
    return {model: client.has_create_access(model) for model in sorted(wanted)}


def get_main_company_id(client) -> Optional[int]:
    """Returns the id of the main res.company (tries id=1 first, falls back
    to the first company found), or None.

    NOT the same as RunContext.company_ids, which despite its name holds
    res.partner ids (customer/company contacts created by master_data.py,
    e.g. sale.py uses ctx.company_ids[i] as a sale.order partner_id) — never
    a real res.company id. Use this helper wherever an actual res.company id
    is needed (e.g. stock.warehouse/purchase.order/stock.quant company_id).
    """
    try:
        companies = client.search_read('res.company', [["id", "=", 1]], fields=["id"], limit=1)
        if companies:
            return companies[0]["id"]
        companies = client.search_read('res.company', [], fields=["id"], limit=1)
        if companies:
            return companies[0]["id"]
    except Exception as e:
        logger.warning(f"-> Warning: Could not determine main company id: {e}")
    return None


def get_main_company_name(client) -> Optional[str]:
    """Get the name of the main company (company id=1) from Odoo."""
    try:
        companies = client.search_read(
            'res.company', [["id", "=", 1]], fields=["name", "partner_id"], limit=1,
        )
        if companies:
            name = companies[0].get("name")
            if name:
                return name
            partner_id = companies[0].get("partner_id")
            if isinstance(partner_id, (list, tuple)):
                partner_id = partner_id[0]
            if partner_id:
                partners = client.search_read(
                    'res.partner', [["id", "=", partner_id]], fields=["name"], limit=1,
                )
                if partners and partners[0].get("name"):
                    return partners[0]["name"]

        companies = client.search_read('res.company', [], fields=["name", "partner_id"], limit=1)
        if companies:
            name = companies[0].get("name")
            if name:
                return name
            partner_id = companies[0].get("partner_id")
            if isinstance(partner_id, (list, tuple)):
                partner_id = partner_id[0]
            if partner_id:
                partners = client.search_read(
                    'res.partner', [["id", "=", partner_id]], fields=["name"], limit=1,
                )
                if partners and partners[0].get("name"):
                    return partners[0]["name"]
    except Exception as e:
        logger.warning(f"-> Warning: Could not determine company name: {e}")
    return None


def get_main_company_info(client) -> Dict[str, Any]:
    """Address/VAT snapshot of the main res.company, for documents that need
    to print a "bill to" block — the vendor-bill PDF's recipient is this
    run's own company (see modules/documents.py).

    Best-effort: returns {} on total failure. Callers must degrade
    gracefully rather than assume street/zip/city are populated — on a
    freshly provisioned demo SaaS tenant they're typically empty strings
    (live-confirmed on demo-test5's id=1 company), not missing keys.
    """
    fields = ["name", "street", "street2", "zip", "city", "country_id", "vat"]
    try:
        companies = client.search_read('res.company', [["id", "=", 1]], fields=fields, limit=1)
        if not companies:
            companies = client.search_read('res.company', [], fields=fields, limit=1)
        if not companies:
            return {}
        comp = companies[0]
        country = comp.get("country_id")
        country_name = country[1] if isinstance(country, (list, tuple)) and len(country) > 1 else None
        return {
            "name": comp.get("name"),
            "street": comp.get("street") or "",
            "street2": comp.get("street2") or "",
            "zip": comp.get("zip") or "",
            "city": comp.get("city") or "",
            "country_name": country_name,
            "vat": comp.get("vat") or None,
        }
    except Exception as e:
        logger.warning(f"-> Warning: Could not fetch main company info: {e}")
        return {}


# R5/WP4 — the highest version this codebase has actually been run against
# end-to-end (scripts/check_compat.sh, ROADMAP.md §R5/WP5) and found clean.
# Bumped only by that deliberate, dev-side check — never by a user run. Two
# transitions verified so far: 19.2->19.4 (2026-08-04) and 19.4->19.5/V20-beta
# (2026-08-29, PR #20) — both clean (no field rename), so this still trails
# the highest version actually seen live.
LAST_VERIFIED_VERSION = "19.4"

# Versions found broken by a WP5 run, with a fix already landed for them —
# distinct from "never checked". Starts empty (same as WP3's FIELD_OVERRIDES
# registry): no version has needed one yet. Keyed by the same normalized
# 'MAJOR.MINOR' string get_server_version returns; value is a short
# human-readable note for the connect checklist, not machine-consumed.
KNOWN_BROKEN_VERSIONS: Dict[str, str] = {}


def classify_version_status(version: Optional[str]) -> str:
    """One of 'unknown' (couldn't detect a version at all), 'known_good'
    (matches LAST_VERIFIED_VERSION), 'known_broken_with_fix' (in
    KNOWN_BROKEN_VERSIONS), or 'untested' (a real version, just never run
    through WP5). Replaces the old binary "version detected or not" — a
    detected-but-untested version is a materially different risk than one
    that's actually been verified clean."""
    if not version:
        return "unknown"
    if version == LAST_VERIFIED_VERSION:
        return "known_good"
    if version in KNOWN_BROKEN_VERSIONS:
        return "known_broken_with_fix"
    return "untested"


def get_server_version(client) -> Optional[str]:
    """Returns the normalized 'MAJOR.MINOR' Odoo server version (e.g. '19.4'), or
    None if it can't be determined/parsed.

    Reads ir.module.module 'base' latest_version. Live-confirmed format on
    saas-19.4: 'saas~19.4.1.3' (a 'saas~' prefix, then a variable number of
    dot-separated segments — do not assume a fixed segment count). Self-hosted
    installs may report a plain 'MAJOR.MINOR' with no prefix; both are handled by
    stripping an optional 'saas~' prefix and taking the first two dot-segments.
    """
    try:
        records = client.search_read(
            'ir.module.module', [["name", "=", "base"]], fields=["latest_version"], limit=1,
        )
    except Exception as e:
        logger.warning(f"-> Warning: Could not determine Odoo server version: {e}")
        return None
    if not records:
        return None
    raw = records[0].get("latest_version")
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.split("~")[-1]  # strip an optional 'saas~' prefix
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    return f"{parts[0]}.{parts[1]}"


# Fixed whitelist of {model: (parent module key or None, [canonical field names
# the codebase writes/reads])}, curated (not auto-derived) from the actual
# client.create/create_batch/write call sites across modules/*.py — deriving it
# automatically would mean parsing module source, more machinery than this
# warning is worth. Prioritizes models/fields already documented as
# version-sensitive in CLAUDE.md's "Verified field gotchas".
#
# The module key exists so check_field_compatibility can skip a model whose app
# isn't installed. Before this, a fields_get on a missing model's model_method
# 404s through the full 3-path x 2-slash-variant fallback chain — up to 6 POSTs
# burned on nothing, per model, on every connect. That waste, not the new S10
# access probes, is why a sparsely-installed demo instance's connect step made
# far more than the "~15 requests" it looked like. None means "always check" —
# a core model with no ir.module.module entry of its own (res.partner,
# product.product, mail.activity, ir.attachment).
# Reconciled against field_manifest.json (S11/WP1, live capture 2026-09-02,
# demo-test5, saas-19.4) — every list below now matches what the codebase
# actually sends for models already tracked here. Fields the manifest showed
# but this whitelist didn't yet have are added; nothing is removed solely for
# being absent from one capture run (hr_holidays/hr_work_entry/hr_recruitment
# were uninstalled on demo-test5 at capture time, so hr.leave/hr.applicant/etc.
# legitimately produced no fields that run — install-state, not a real gap).
#
# The manifest also surfaced models this whitelist has never tracked at all
# (mrp.workcenter, mrp.bom.line, mrp.routing.workcenter, purchase.order,
# stock.quant, project.project, project.task.type, hr.skill, hr.skill.type,
# sale.advance.payment.inv, account.analytic.line, account.bank.statement.line)
# — deliberately not added here. Whitelist membership is a judgment call
# (worth the fields_get cost on every connect), not something to auto-expand
# from one capture run; left as a candidate list for a future pass instead.
FIELD_COMPAT_WHITELIST: Dict[str, Tuple[Optional[str], List[str]]] = {
    'res.partner': (None, ['name', 'is_company', 'street', 'zip', 'city', 'email', 'phone', 'website',
                    'country_id', 'parent_id', 'type', 'supplier_rank']),
    'product.product': (None, ['name', 'list_price', 'type', 'sale_ok', 'purchase_ok',
                        'invoice_policy', 'is_storable', 'service_tracking', 'service_type',
                        'standard_price', 'tracking', 'barcode']),
    'crm.lead': ('crm', ['type', 'partner_id', 'name', 'date_deadline', 'expected_revenue',
                 'stage_id', 'user_id']),
    'mail.activity': (None, ['res_id', 'res_model_id', 'activity_type_id', 'date_deadline', 'summary']),
    'sale.order': ('sale', ['partner_id', 'opportunity_id', 'order_line']),
    'account.move': ('account', ['move_type', 'partner_id', 'invoice_line_ids', 'invoice_date', 'ref']),
    # journal_id/balance_start unconfirmed by the 2026-09-02 capture — the run
    # only captured balance_end_real being written to account.bank.statement
    # itself (journal_id showed up on account.bank.statement.line instead).
    # Left as-is rather than removed on one run's evidence; worth a closer
    # look at accounting.py's bank-statement path in a future pass.
    'account.bank.statement': ('account', ['journal_id', 'balance_start', 'balance_end_real']),
    'hr.employee': ('hr', ['name']),
    # hr.leave/hr.leave.allocation/hr.work.entry.type ship with hr_holidays/
    # hr_work_entry, NOT with hr — the exact gap R10/A5 closes for the pipeline
    # itself; this whitelist has the same gap and gets the same fix.
    'hr.leave': ('hr_holidays', ['employee_id', 'work_entry_type_id', 'date_from', 'date_to',
                 'request_date_from', 'request_date_to']),
    'hr.leave.allocation': ('hr_holidays', ['employee_id', 'work_entry_type_id']),
    'hr.work.entry.type': ('hr_work_entry', ['name', 'code', 'count_as',
                            'requires_allocation', 'employee_requests']),
    # applicant_skill_ids deliberately absent: it ships with hr_recruitment_skills,
    # NOT hr_recruitment (found live 2026-09-02, S11/R5) — a whitelist entry
    # gated on hr_recruitment alone would false-positive-warn on every
    # instance with hr_recruitment installed but hr_recruitment_skills not
    # (this repo's own demo-test5 included), exactly the noise R5 exists to
    # avoid. modules/recruiting.py and modules/documents.py gate the field
    # itself on hr_recruitment_skills directly; tracking it here would need a
    # second, more granular whitelist shape this dict doesn't have yet.
    'hr.applicant': ('hr_recruitment', ['partner_name', 'email_from', 'partner_phone', 'job_id',
                      'schedule_pay', 'stage_id']),
    'hr.job.skill': ('hr_recruitment', ['skill_id', 'skill_type_id', 'skill_level_id']),
    'project.task': ('project', ['name', 'project_id', 'stage_id']),
    'mrp.production': ('mrp', ['product_id', 'date_start', 'bom_id', 'product_qty']),
    'mrp.bom': ('mrp', ['product_tmpl_id', 'type', 'product_qty', 'bom_line_ids', 'code', 'product_id']),
    'ir.attachment': (None, ['res_model', 'res_id', 'raw', 'mimetype', 'type', 'name']),
    'hr.expense': ('hr_expense', ['employee_id', 'product_id', 'name', 'payment_mode',
                   'total_amount', 'date', 'currency_id', 'approval_state']),
}


def check_field_compatibility(client, installed_modules=None,
                              whitelist: Optional[Dict[str, List[str]]] = None,
                              model_access: Optional[Dict[str, bool]] = None) -> List[str]:
    """Connect-time check: for each model in the whitelist, calls fields_get and
    warns about any field the codebase writes/reads that this instance doesn't
    have. A model that errors out entirely (e.g. its parent app isn't installed)
    is skipped silently — that's an install-state concern, not a version-
    compatibility one. Non-fatal: logs each warning and returns the list of
    warning strings; callers should not treat a non-empty result as fatal.

    `installed_modules` and `model_access` both gate the DEFAULT whitelist only
    (see FIELD_COMPAT_WHITELIST's comment for the installed-module case;
    `model_access` additionally skips a model that IS installed but whose
    create access `probe_model_access` found blocked — e.g. an app installed
    with a feature toggle off, the S10 mrp.workcenter case). Composing both
    means a field warning that survives is unambiguous: the model is
    installed, writable, and the field is still missing — a real version
    finding, never noise from install/access state (R5/WP2).

    An explicit `whitelist` (flat {model: [fields]}, as the unit tests pass) is
    checked unconditionally against neither gate — the caller supplied exactly
    the models it wants checked, and gating those would silently make such a
    test depend on installed_modules/model_access it never passed.
    """
    warnings: List[str] = []
    if whitelist is not None:
        entries = list(whitelist.items())
    else:
        installed = set(installed_modules or set())
        entries = [
            (model, fields) for model, (module_key, fields) in FIELD_COMPAT_WHITELIST.items()
            if module_key is None or module_key in installed
        ]
        if model_access is not None:
            entries = [(model, fields) for model, fields in entries if model_access.get(model, True)]

    for model, fields in entries:
        try:
            live_fields = client.model_method(model, 'fields_get', {'attributes': []})
        except Exception:
            continue
        if not isinstance(live_fields, dict):
            continue
        for field in fields:
            if field not in live_fields:
                msg = (f"Feld '{field}' auf Modell '{model}' existiert auf dieser "
                       f"Odoo-Instanz nicht — Code in modules/ prüfen.")
                warnings.append(msg)
                logger.warning(f"[version-check] {msg}")
    return warnings


def get_main_company_language(client) -> str:
    """Get the language of the main company, falling back to de_DE."""
    try:
        companies = client.search_read(
            'res.company', [["id", "=", 1]], fields=["partner_id"], limit=1,
        )
        if companies:
            partner_id = companies[0].get("partner_id")
            if isinstance(partner_id, (list, tuple)):
                partner_id = partner_id[0]
            if partner_id:
                partners = client.search_read(
                    'res.partner', [["id", "=", partner_id]], fields=["lang"], limit=1,
                )
                if partners and partners[0].get("lang"):
                    return partners[0]["lang"]

        companies = client.search_read('res.company', [], fields=["partner_id"], limit=1)
        if companies:
            partner_id = companies[0].get("partner_id")
            if isinstance(partner_id, (list, tuple)):
                partner_id = partner_id[0]
            if partner_id:
                partners = client.search_read(
                    'res.partner', [["id", "=", partner_id]], fields=["lang"], limit=1,
                )
                if partners and partners[0].get("lang"):
                    return partners[0]["lang"]

        for domain in [[["id", "=", 2], ["lang", "!=", False]], [["active", "=", True], ["lang", "!=", False]], [["lang", "!=", False]]]:
            users = client.search_read('res.users', domain, fields=["lang"], limit=1)
            if users and users[0].get("lang"):
                return users[0]["lang"]
    except Exception as e:
        logger.warning(f"-> Warning: Could not determine company language: {e}")
    return "de_DE"
