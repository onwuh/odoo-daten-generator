"""
Offline unit test suite — no Odoo connection needed.

Usage:
    python3 tests/unit/unit_suite.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests.unit import test_leave_helpers, test_hr_unit, test_guard_patterns, test_llm_service, test_odoo_actions_unit, test_project_unit, test_data_factory, test_recruiting_unit, test_logging_setup, test_orchestrator_unit, test_master_data_unit, test_crm_batch_unit, test_project_batch_unit, test_recruiting_batch_unit, test_mrp_batch_unit, test_accounting_batch_unit, test_sale_unit, test_hr_batch_unit, test_odoo_client_unit, test_documents_unit, test_purchase_unit, test_inventory_unit

_MODULES = [
    ("leave_helpers",      test_leave_helpers.run),
    ("hr_unit",            test_hr_unit.run),
    ("guard_patterns",     test_guard_patterns.run),
    ("llm_service",        test_llm_service.run),
    ("odoo_actions_unit",  test_odoo_actions_unit.run),
    ("project_unit",       test_project_unit.run),
    ("data_factory",       test_data_factory.run),
    ("recruiting_unit",    test_recruiting_unit.run),
    ("logging_setup",      test_logging_setup.run),
    ("orchestrator_unit",  test_orchestrator_unit.run),
    ("master_data_unit",   test_master_data_unit.run),
    ("crm_batch_unit",     test_crm_batch_unit.run),
    ("project_batch_unit", test_project_batch_unit.run),
    ("recruiting_batch_unit", test_recruiting_batch_unit.run),
    ("mrp_batch_unit",     test_mrp_batch_unit.run),
    ("accounting_batch_unit", test_accounting_batch_unit.run),
    ("sale_unit",          test_sale_unit.run),
    ("hr_batch_unit",      test_hr_batch_unit.run),
    ("odoo_client_unit",   test_odoo_client_unit.run),
    ("documents_unit",     test_documents_unit.run),
    ("purchase_unit",      test_purchase_unit.run),
    ("inventory_unit",     test_inventory_unit.run),
]


def _render_summary(summary, section_title="UNIT TEST SUMMARY"):
    steps_total  = sum(len(steps) for _, _, steps in summary)
    steps_passed = sum(1 for _, _, steps in summary for _, ok, _ in steps if ok)
    steps_failed = steps_total - steps_passed

    col_w = 50
    line  = "─" * (col_w + 14)

    print(f"\n┌{line}┐")
    print(f"│{section_title:^{col_w + 14}}│")
    print(f"├{line}┤")

    for module_name, module_ok, steps in summary:
        print(f"├{line}┤")
        mod_status = "PASS" if module_ok else "FAIL"
        print(f"│  {'[' + module_name.upper() + ']':<{col_w}}  {mod_status:<8}  │")
        for label, ok, detail in steps:
            status = "OK  " if ok else "FAIL"
            detail_str = str(detail) if detail else ""
            if len(detail_str) > 30:
                detail_str = detail_str[:27] + "..."
            print(f"│    {label:<{col_w - 2}}  {status:<8}  {detail_str}")

    print(f"├{line}┤")
    all_ok = steps_failed == 0
    overall = "ALL PASS" if all_ok else f"{steps_failed} FAILED"
    print(f"│  {'Steps: ' + str(steps_passed) + '/' + str(steps_total) + ' passed':<{col_w}}  {overall:<12}│")
    print(f"└{line}┘")
    return all_ok


def run_unit_tests():
    """Run all unit modules. Returns (all_ok, summary list)."""
    summary = []
    for name, run_fn in _MODULES:
        print(f"\n{'='*60}")
        print(f"  UNIT MODULE: {name.upper()}")
        print(f"{'='*60}")
        try:
            all_ok, steps = run_fn()
        except Exception as e:
            steps = [(f"{name}: unhandled exception", False, str(e))]
            all_ok = False
        summary.append((name, all_ok, steps))
    return summary


def main():
    summary = run_unit_tests()
    all_ok = _render_summary(summary)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
