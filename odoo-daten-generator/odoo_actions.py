"""Shared Odoo action helpers used by entry points (connect.py, gui.py) and
multiple domain modules.

Domain-specific helpers live in their respective modules:
  modules/crm.py, modules/sale.py, modules/accounting.py,
  modules/hr.py, modules/project.py, modules/mrp.py, modules/recruiting.py
"""

import logging
from typing import Any, Dict, List, Optional, Set

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
