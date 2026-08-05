"""Unit tests for modules/project.py — D3 batch-creation call-count guard."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import project


def _make_ctx(num_projects=0, tasks_per_project=3, hr_timesheet=0):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=criteria,
        module_selections=ModuleSelections(project=num_projects, tasks_per_project=tasks_per_project,
                                            hr_timesheet=hr_timesheet),
        industry="IT", language_name="German", language_code="de", gemini_model_name="test",
    )


def _mock_client(search_read_return=None):
    client = MagicMock()
    client.search_read.return_value = search_read_return or []
    counter = {"n": 2000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    client.create_batch.side_effect = _create_batch
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # D3: create_project_data issues exactly 2 create_batch calls
    # (projects, then tasks) regardless of num_projects.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_projects=4, tasks_per_project=3)
        project.create_project_data(client, gemini=None, ctx=ctx)
        assert client.create_batch.call_count == 2, client.create_batch.call_count
        # project.project and project.task creation must go through create_batch,
        # not a per-record create() loop (stage creation/dedup legitimately still
        # uses client.create — that's out of D3's scope for this module).
        batched_models = [call.args[0] for call in client.create_batch.call_args_list]
        assert batched_models == ['project.project', 'project.task'], batched_models
        individually_created_models = [call.args[0] for call in client.create.call_args_list]
        assert 'project.project' not in individually_created_models
        assert 'project.task' not in individually_created_models
        assert len(ctx.project_ids) == 4, ctx.project_ids
        results.append((
            "create_project_data: projects+tasks via create_batch, not per-record create()",
            True, f"create_batch calls={client.create_batch.call_count}",
        ))
    except AssertionError as e:
        results.append(("create_project_data: projects+tasks via create_batch, not per-record create()", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 5: num_projects=0 -> skip gracefully, no create_batch call
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_projects=0)
        project.create_project_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        assert ctx.project_ids == []
        results.append(("create_project_data: num_projects=0 -> no create_batch call (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_project_data: num_projects=0 -> no create_batch call (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # D3: create_timesheet_data issues exactly 1 create_batch call
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(hr_timesheet=6)
        ctx.project_ids = [1, 2]
        ctx.employee_ids = [10, 11]
        project.create_timesheet_data(client, gemini=None, ctx=ctx)
        assert client.create_batch.call_count == 1, client.create_batch.call_count
        assert client.create.call_count == 0, "fell back to per-record create()"
        results.append((
            "create_timesheet_data: exactly 1 create_batch call",
            True, f"create_batch calls={client.create_batch.call_count}",
        ))
    except AssertionError as e:
        results.append(("create_timesheet_data: exactly 1 create_batch call", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 5: no project_ids -> skip gracefully, no create_batch call
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(hr_timesheet=6)
        ctx.project_ids = []
        project.create_timesheet_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_timesheet_data: no project_ids -> no create_batch call (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_timesheet_data: no project_ids -> no create_batch call (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # R8 Pattern 5 regression guard: no bulk project_ids at all, but real
    # order-linked billable tasks exist -> must NOT early-return (this is
    # exactly the bug the early-return condition used to have: a run with
    # Zeiterfassung+Verkauf but not Projekte would previously skip timesheets
    # entirely even though billable tasks existed).
    # ------------------------------------------------------------------
    try:
        billable = [
            {"id": 501, "task_id": [77, "Task A"], "project_id": [88, "Proj A"]},
            {"id": 502, "task_id": [78, "Task B"], "project_id": [88, "Proj A"]},
        ]
        client = _mock_client(search_read_return=billable)
        ctx = _make_ctx(hr_timesheet=1)
        ctx.project_ids = []
        ctx.employee_ids = [10, 11]
        ctx.confirmed_order_ids = [900]
        project.create_timesheet_data(client, gemini=None, ctx=ctx)
        assert client.create_batch.call_count == 1, client.create_batch.call_count
        vals_list = client.create_batch.call_args_list[0].args[1]
        assert len(vals_list) == 1, f"budget=1 should yield exactly 1 entry, got {vals_list}"
        assert vals_list[0]["so_line"] == 501, vals_list[0]
        assert vals_list[0]["task_id"] == 77, vals_list[0]
        assert vals_list[0]["project_id"] == 88, vals_list[0]
        results.append((
            "R8: billable order-linked tasks alone (no bulk project_ids) still create timesheets",
            True, f"vals={vals_list[0]}",
        ))
    except AssertionError as e:
        results.append(("R8: billable order-linked tasks alone (no bulk project_ids) still create timesheets", False, str(e)))

    # ------------------------------------------------------------------
    # R8: billable lines claim the budget first; remaining budget fills the
    # bulk pool — one combined create_batch call (Pattern 8 preserved).
    # ------------------------------------------------------------------
    try:
        billable = [
            {"id": 501, "task_id": [77, "Task A"], "project_id": [88, "Proj A"]},
            {"id": 502, "task_id": [78, "Task B"], "project_id": [88, "Proj A"]},
        ]
        client = _mock_client(search_read_return=billable)
        ctx = _make_ctx(hr_timesheet=5)
        ctx.project_ids = [1, 2]
        ctx.employee_ids = [10, 11]
        ctx.confirmed_order_ids = [900]
        project.create_timesheet_data(client, gemini=None, ctx=ctx)
        assert client.create_batch.call_count == 1, client.create_batch.call_count
        vals_list = client.create_batch.call_args_list[0].args[1]
        assert len(vals_list) == 5, f"budget=5 should yield exactly 5 entries total, got {len(vals_list)}"
        so_lines = [v.get("so_line") for v in vals_list[:2]]
        assert so_lines == [501, 502], f"first 2 entries should be the billable lines: {so_lines}"
        bulk_entries = vals_list[2:]
        assert all("so_line" not in v for v in bulk_entries), bulk_entries
        assert all(v.get("project_id") in (1, 2) for v in bulk_entries), bulk_entries
        results.append((
            "R8: billable lines claim budget first, remainder fills bulk pool (1 create_batch call)",
            True, f"total entries={len(vals_list)}",
        ))
    except AssertionError as e:
        results.append(("R8: billable lines claim budget first, remainder fills bulk pool (1 create_batch call)", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
