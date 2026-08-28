"""Shared Odoo action helpers used by entry points (connect.py, gui.py) and
multiple domain modules.

Domain-specific helpers live in their respective modules:
  modules/crm.py, modules/sale.py, modules/accounting.py,
  modules/hr.py, modules/project.py, modules/mrp.py, modules/recruiting.py
"""

import logging
from typing import Any, Dict, List, Optional, Set

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

    # mrp_routings: Work Centers + Work Orders accessible?
    if 'mrp' in installed:
        try:
            client.search_read('mrp.workcenter', [], fields=['id'], limit=1)
            flags['mrp_routings'] = True
        except Exception:
            flags['mrp_routings'] = False

    # quality: quality module accessible?
    if 'mrp' in installed or 'quality' in installed:
        try:
            client.search_read('quality.alert.team', [], fields=['id'], limit=1)
            flags['quality'] = True
        except Exception:
            flags['quality'] = False

    # crm_leads: "Use Leads" setting enabled in CRM?
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


# Fixed whitelist of {model: [canonical field names the codebase writes/reads]},
# curated (not auto-derived) from the actual client.create/create_batch/write call
# sites across modules/*.py — deriving it automatically would mean parsing module
# source, more machinery than this warning is worth. Prioritizes models/fields
# already documented as version-sensitive in CLAUDE.md's "Verified field gotchas".
FIELD_COMPAT_WHITELIST: Dict[str, List[str]] = {
    'res.partner': ['name', 'is_company', 'street', 'zip', 'city', 'email', 'phone', 'website'],
    'product.product': ['name', 'list_price', 'type', 'sale_ok', 'purchase_ok'],
    'crm.lead': ['type', 'partner_id', 'name'],
    'mail.activity': ['res_id', 'res_model_id', 'activity_type_id', 'date_deadline'],
    'sale.order': ['partner_id'],
    'account.move': ['move_type', 'partner_id', 'invoice_line_ids', 'invoice_date'],
    'account.bank.statement': ['journal_id', 'balance_start', 'balance_end_real'],
    'hr.employee': ['name'],
    'hr.leave': ['employee_id', 'work_entry_type_id', 'date_from', 'date_to',
                 'request_date_from', 'request_date_to'],
    'hr.leave.allocation': ['employee_id', 'work_entry_type_id'],
    'hr.work.entry.type': ['name', 'code', 'count_as', 'shortcut_behavior',
                            'requires_allocation', 'employee_requests'],
    'hr.applicant': ['partner_name', 'email_from', 'partner_phone', 'job_id',
                      'schedule_pay', 'applicant_skill_ids'],
    'hr.job.skill': ['skill_id', 'skill_type_id', 'skill_level_id'],
    'project.task': ['name', 'project_id'],
    'mrp.production': ['product_id', 'date_start'],
    'mrp.bom': ['product_tmpl_id', 'type', 'product_qty'],
    'ir.attachment': ['res_model', 'res_id', 'raw', 'mimetype', 'type'],
}


def check_field_compatibility(client, whitelist: Optional[Dict[str, List[str]]] = None) -> List[str]:
    """Connect-time check: for each model in the whitelist, calls fields_get and
    warns about any field the codebase writes/reads that this instance doesn't
    have. A model that errors out entirely (e.g. its parent app isn't installed)
    is skipped silently — that's an install-state concern, not a version-
    compatibility one. Non-fatal: logs each warning and returns the list of
    warning strings; callers should not treat a non-empty result as fatal.
    """
    whitelist = FIELD_COMPAT_WHITELIST if whitelist is None else whitelist
    warnings: List[str] = []
    for model, fields in whitelist.items():
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
