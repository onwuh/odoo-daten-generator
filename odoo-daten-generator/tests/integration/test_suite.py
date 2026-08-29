"""
Integration test suite for odoo-daten-generator.

Usage:
    python3 tests/integration/test_suite.py

Config: tests/test_config.ini (gitignored) or falls back to config.ini.
No pytest, no mocking — runs against a real Odoo instance.
"""
import configparser
import sys
import os
from dataclasses import dataclass, field

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from odoo_client import OdooJson2Client
import odoo_actions
import run_config
from logging_setup import configure_logging
from web import security

configure_logging()

from tests.unit.unit_suite import run_unit_tests, _render_summary as _render_unit_summary

from tests.integration import (
    test_master_data,
    test_mrp,
    test_crm,
    test_sale,
    test_accounting,
    test_hr,
    test_project,
    test_recruiting,
    test_purchase,
    test_inventory,
    test_documents,
    test_run_journal,
    test_odoo_actions,
)


@dataclass
class TestContext:
    company_ids:       list = field(default_factory=list)
    product_ids:       list = field(default_factory=list)
    partner_ids:       list = field(default_factory=list)
    workcenter_ids:    list = field(default_factory=list)
    bom_ids:           list = field(default_factory=list)
    employee_ids:      list = field(default_factory=list)
    project_ids:       list = field(default_factory=list)
    order_ids:         list = field(default_factory=list)
    confirmed_order_ids: list = field(default_factory=list)
    job_ids:           list = field(default_factory=list)
    opportunity_ids:   list = field(default_factory=list)
    lead_ids:          list = field(default_factory=list)
    feature_flags:     dict = field(default_factory=dict)
    installed_modules: set  = field(default_factory=set)


_MODULES = [
    ("master_data", test_master_data.run),
    ("mrp",         test_mrp.run),
    ("crm",         test_crm.run),
    ("sale",        test_sale.run),
    ("hr",          test_hr.run),
    ("project",     test_project.run),
    ("accounting",  test_accounting.run),
    ("recruiting",  test_recruiting.run),
    ("purchase",    test_purchase.run),
    ("inventory",   test_inventory.run),
    ("documents",   test_documents.run),
    ("run_journal",  test_run_journal.run),
    ("odoo_actions", test_odoo_actions.run),
]


def _load_config():
    cfg = configparser.ConfigParser()
    # Try tests/test_config.ini first, fall back to root config.ini
    local = os.path.join(_ROOT, "tests", "test_config.ini")
    root  = os.path.join(_ROOT, "config.ini")
    if os.path.exists(local):
        cfg.read(local)
        print(f"[config] Using {local}")
    elif os.path.exists(root):
        cfg.read(root)
        print(f"[config] Using {root}")
    else:
        print("[config] ERROR: Neither tests/test_config.ini nor config.ini found.", file=sys.stderr)
        sys.exit(1)
    url = cfg["odoo"]["url"]
    # S10/R10 (F2): "db" is optional in config.ini.example now that the web
    # console derives it from the URL — this loader must not KeyError on a
    # config file that omits it, or config.ini.example's own comment is a lie.
    db = cfg["odoo"].get("db") or security.derive_database_name(url)
    return url, db, cfg["odoo"]["api_key"]


def _run_all(client, ctx):
    """Run all modules in order, collecting results."""
    summary = []  # list of (module_name, all_ok, steps)
    for name, run_fn in _MODULES:
        print(f"\n{'='*60}")
        print(f"  MODULE: {name.upper()}")
        print(f"{'='*60}")
        try:
            all_ok, steps = run_fn(client, ctx)
        except Exception as e:
            steps = [(f"{name}: unhandled exception", False, str(e))]
            all_ok = False
        summary.append((name, all_ok, steps))
    return summary


def _render_summary(summary):
    """Print a box-drawing summary table. Returns True if all modules passed."""
    steps_total  = sum(len(steps) for _, _, steps in summary)
    steps_passed = sum(1 for _, _, steps in summary for _, ok, _ in steps if ok)
    steps_failed = steps_total - steps_passed

    col_w = 50  # label column width
    line  = "─" * (col_w + 14)

    print(f"\n┌{line}┐")
    print(f"│{'INTEGRATION TEST SUMMARY':^{col_w + 14}}│")
    print(f"├{line}┤")
    print(f"│  {'Step':<{col_w}}  {'Result':<8}  {'Detail'} ")

    for module_name, module_ok, steps in summary:
        print(f"├{line}┤")
        mod_status = "PASS" if module_ok else "FAIL"
        print(f"│  {'[' + module_name.upper() + ']':<{col_w}}  {mod_status:<8}  │")
        for label, ok, detail in steps:
            status = "OK  " if ok else "FAIL"
            detail_str = str(detail) if detail and not ok else (str(detail) if detail else "")
            # Truncate detail for display
            if len(detail_str) > 30:
                detail_str = detail_str[:27] + "..."
            print(f"│    {label:<{col_w - 2}}  {status:<8}  {detail_str}")

    print(f"├{line}┤")
    all_ok = steps_failed == 0
    overall = "ALL PASS" if all_ok else f"{steps_failed} FAILED"
    print(f"│  {'Steps: ' + str(steps_passed) + '/' + str(steps_total) + ' passed':<{col_w}}  {overall:<12}│")
    print(f"└{line}┘")
    return all_ok


def main():
    # --- Unit tests (offline, no Odoo) ---
    print(f"\n{'='*60}")
    print("  UNIT TESTS (offline)")
    print(f"{'='*60}")
    unit_summary = run_unit_tests()
    unit_ok = _render_unit_summary(unit_summary, section_title="UNIT TEST SUMMARY")

    # --- Integration tests (live Odoo) ---
    print(f"\n{'='*60}")
    print("  INTEGRATION TESTS (live Odoo)")
    print(f"{'='*60}")
    url, db, api_key = _load_config()
    client = OdooJson2Client(url, db, api_key)
    ctx = TestContext()

    # Single source of truth, shared with the web layer: a module missing from
    # this list never enters ctx.installed_modules and is skipped forever. The
    # test harness keeping its own copy is how purchase/stock stayed unreachable
    # from real runs through all of S8.
    ctx.installed_modules = odoo_actions.get_installed_modules(client, run_config.WANTED_MODULES)
    print(f"[modules] Installed: {', '.join(sorted(ctx.installed_modules)) or '–'}")

    ctx.feature_flags = odoo_actions.get_enabled_features(client, ctx.installed_modules)
    active   = [k for k, v in ctx.feature_flags.items() if v]
    inactive = [k for k, v in ctx.feature_flags.items() if not v]
    print(f"\n[features] Active: {', '.join(active) or '–'}")
    if inactive:
        print(f"[features] Inactive (steps will be skipped): {', '.join(inactive)}")

    integ_summary = _run_all(client, ctx)
    integ_ok = _render_summary(integ_summary)

    all_ok = unit_ok and integ_ok
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
