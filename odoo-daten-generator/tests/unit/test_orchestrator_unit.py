"""Unit tests for orchestrator.py — D1 progress-callback mechanism (no monkeypatching)."""
import os
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import orchestrator
from config import DemoCriteria, ModuleSelections, RunContext


def _make_ctx(installed_modules, module_selections=None, skip_master_data=False):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=1,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=1, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=criteria,
        module_selections=module_selections or ModuleSelections(),
        industry="IT", language_name="German", language_code="de",
        gemini_model_name="test-model",
        installed_modules=installed_modules,
        skip_master_data=skip_master_data,
    )


def run():
    results = []

    # ------------------------------------------------------------------
    # D1a: _run_module fires on_start/on_done(ok=True) around a successful handler
    # ------------------------------------------------------------------
    try:
        events = []
        handler = MagicMock()
        orchestrator._run_module(
            "TestModule", handler, client=MagicMock(), gemini=MagicMock(), ctx=MagicMock(),
            on_start=lambda name: events.append(("start", name)),
            on_done=lambda name, ok=True: events.append(("done", name, ok)),
        )
        assert events == [("start", "TestModule"), ("done", "TestModule", True)], events
        handler.assert_called_once()
        results.append(("_run_module: success path fires start then done(ok=True)", True, str(events)))
    except AssertionError as e:
        results.append(("_run_module: success path fires start then done(ok=True)", False, str(e)))

    # ------------------------------------------------------------------
    # D1b: _run_module fires on_done(ok=False) when handler raises, and swallows the exception
    # ------------------------------------------------------------------
    try:
        events = []
        handler = MagicMock(side_effect=RuntimeError("boom"))
        orchestrator._run_module(
            "FailingModule", handler, client=MagicMock(), gemini=MagicMock(), ctx=MagicMock(),
            on_start=lambda name: events.append(("start", name)),
            on_done=lambda name, ok=True: events.append(("done", name, ok)),
        )
        assert events == [("start", "FailingModule"), ("done", "FailingModule", False)], events
        results.append(("_run_module: exception path fires done(ok=False), no raise", True, str(events)))
    except AssertionError as e:
        results.append(("_run_module: exception path fires done(ok=False), no raise", False, str(e)))
    except Exception as e:
        results.append(("_run_module: exception path fires done(ok=False), no raise", False, f"unexpected raise: {e}"))

    # ------------------------------------------------------------------
    # D1c: _run_module works with no callbacks supplied (defaults None) — no crash
    # ------------------------------------------------------------------
    try:
        handler = MagicMock()
        orchestrator._run_module("NoCallbacks", handler, client=MagicMock(), gemini=MagicMock(), ctx=MagicMock())
        handler.assert_called_once()
        results.append(("_run_module: no callbacks supplied → no crash", True, ""))
    except Exception as e:
        results.append(("_run_module: no callbacks supplied → no crash", False, str(e)))

    # ------------------------------------------------------------------
    # D1d: orchestrator.run() threads on_module_start/on_module_done through BOTH
    # call sites — the master_data special-case (line ~36) AND the transactional
    # module loop (line ~66). Regression guard for the peer-review finding that
    # only one call site might get wired up.
    # ------------------------------------------------------------------
    try:
        events = []
        gemini = MagicMock()
        gemini.fetch_creative_atoms.return_value = {}
        gemini.fetch_name_suggestions.return_value = {}
        gemini.total_calls = 0
        gemini.total_tokens = 0
        ctx = _make_ctx(installed_modules={"mrp"}, module_selections=ModuleSelections(mrp={"num_products": 1}))

        with patch("modules.master_data.create_master_data") as mock_master, \
             patch("modules.mrp.create_mrp_data") as mock_mrp:
            orchestrator.run(
                client=MagicMock(), gemini=gemini, ctx=ctx,
                on_module_start=lambda name: events.append(("start", name)),
                on_module_done=lambda name, ok=True: events.append(("done", name, ok)),
            )
            mock_master.assert_called_once()
            mock_mrp.assert_called_once()

        assert ("start", "Stammdaten") in events, events
        assert ("done", "Stammdaten", True) in events, events
        assert ("start", "mrp") in events, events
        assert ("done", "mrp", True) in events, events
        results.append(("run(): callbacks fire for both master_data AND module-loop call sites", True, str(events)))
    except AssertionError as e:
        results.append(("run(): callbacks fire for both master_data AND module-loop call sites", False, str(e)))
    except Exception as e:
        results.append(("run(): callbacks fire for both master_data AND module-loop call sites", False, f"unexpected raise: {e}"))

    # ------------------------------------------------------------------
    # D1e: run() with no callbacks supplied at all (GUI removed, direct call) → no crash
    # ------------------------------------------------------------------
    try:
        gemini = MagicMock()
        gemini.fetch_creative_atoms.return_value = {}
        gemini.fetch_name_suggestions.return_value = {}
        gemini.total_calls = 0
        gemini.total_tokens = 0
        ctx = _make_ctx(installed_modules=set(), skip_master_data=True)
        orchestrator.run(client=MagicMock(), gemini=gemini, ctx=ctx)
        results.append(("run(): no callbacks supplied → no crash", True, ""))
    except Exception as e:
        results.append(("run(): no callbacks supplied → no crash", False, str(e)))

    # ------------------------------------------------------------------
    # B10 Pattern 3: installed_modules a strict superset of what's selected
    # (module_selections) -> only selected modules run. This is the invariant
    # gui.py's RunContext construction relies on (installed_modules is now the
    # true Odoo-probed set, which can be larger than what the user picked in
    # the GUI).
    # ------------------------------------------------------------------
    try:
        events = []
        gemini = MagicMock()
        gemini.fetch_creative_atoms.return_value = {}
        gemini.fetch_name_suggestions.return_value = {}
        gemini.total_calls = 0
        gemini.total_tokens = 0
        # installed: mrp, crm, sale (true Odoo state) — selected: only sale
        ctx = _make_ctx(
            installed_modules={"mrp", "crm", "sale"},
            module_selections=ModuleSelections(sale=3),  # crm=0, mrp={} -> not selected
            skip_master_data=True,
        )
        with patch("modules.mrp.create_mrp_data") as mock_mrp, \
             patch("modules.crm.create_crm_data") as mock_crm, \
             patch("modules.sale.create_sale_data") as mock_sale:
            orchestrator.run(
                client=MagicMock(), gemini=gemini, ctx=ctx,
                on_module_start=lambda name: events.append(name),
            )
            mock_mrp.assert_not_called()
            mock_crm.assert_not_called()
            mock_sale.assert_called_once()
        assert events == ["sale"], events
        results.append((
            "run(): installed ⊋ selected -> only selected modules run (B10 Pattern 3)",
            True, f"ran={events}",
        ))
    except AssertionError as e:
        results.append(("run(): installed ⊋ selected -> only selected modules run (B10 Pattern 3)", False, str(e)))

    # ------------------------------------------------------------------
    # R8: module_order pins 'account' to run AFTER hr/project/hr_timesheet
    # (moved from position 4 to 7) — a service line's invoiced quantity
    # depends on qty_delivered, which is computed from timesheets that must
    # already exist by the time accounting runs. Regression guard against a
    # future edit silently reverting the order.
    # ------------------------------------------------------------------
    try:
        events = []
        gemini = MagicMock()
        gemini.fetch_creative_atoms.return_value = {}
        gemini.fetch_name_suggestions.return_value = {}
        gemini.total_calls = 0
        gemini.total_tokens = 0
        ctx = _make_ctx(
            installed_modules={"mrp", "crm", "sale", "hr", "project", "hr_timesheet",
                                "account", "hr_recruitment"},
            module_selections=ModuleSelections(
                sale=1, hr=1, project=1, hr_timesheet=1, account=1, hr_recruitment={"num_jobs": 1},
            ),
            skip_master_data=True,
        )
        with patch("modules.mrp.create_mrp_data"), patch("modules.crm.create_crm_data"), \
             patch("modules.sale.create_sale_data"), patch("modules.hr.create_hr_data"), \
             patch("modules.project.create_project_data"), \
             patch("modules.project.create_timesheet_data"), \
             patch("modules.accounting.create_accounting_data"), \
             patch("modules.recruiting.create_recruiting_data"):
            orchestrator.run(
                client=MagicMock(), gemini=gemini, ctx=ctx,
                on_module_start=lambda name: events.append(name),
            )
        assert events.index("account") > events.index("hr_timesheet") > events.index("project") > events.index("hr"), events
        results.append(("R8: module_order — account runs after hr/project/hr_timesheet", True, str(events)))
    except AssertionError as e:
        results.append(("R8: module_order — account runs after hr/project/hr_timesheet", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
