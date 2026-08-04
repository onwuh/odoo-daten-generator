import sys
import os
import datetime
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules.project import (
    create_project, create_task, create_project_stage,
    create_project_data, create_timesheet_data,
)


def _make_rctx(num_projects=2, tasks_per_project=3, hr_timesheet=0):
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit,
        module_selections=ModuleSelections(
            project=num_projects, tasks_per_project=tasks_per_project, hr_timesheet=hr_timesheet,
        ),
        industry="IT", language_name="German", language_code="de_DE", gemini_model_name="test",
    )


def run(client, ctx):
    """
    Populates: ctx.project_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    proj_id = None

    # Step 1 — Create project
    try:
        proj_id = create_project(client, "Integration Test Projekt")
        assert isinstance(proj_id, int) and proj_id > 0
        rec = client.search_read(
            'project.project',
            [["id", "=", proj_id]],
            fields=["name"],
            limit=1,
        )
        assert rec and rec[0]["name"] == "Integration Test Projekt"
        ctx.project_ids.append(proj_id)
        results.append(("project: create project + read-back name", True, proj_id))
    except Exception as e:
        results.append(("project: create project + read-back name", False, str(e)))

    # Step 2 — Create task
    try:
        assert proj_id, "No project created in step 1"
        task_id = create_task(client, proj_id, "Integration Test Aufgabe")
        assert isinstance(task_id, int) and task_id > 0
        rec = client.search_read(
            'project.task',
            [["id", "=", task_id]],
            fields=["project_id"],
            limit=1,
        )
        assert rec
        pid = rec[0]["project_id"]
        pid = pid[0] if isinstance(pid, (list, tuple)) else pid
        assert pid == proj_id
        results.append(("project: create task + read-back project_id", True, task_id))
    except Exception as e:
        results.append(("project: create task + read-back project_id", False, str(e)))

    # Step 3 — create project stage (live)
    stage_id = None
    try:
        existing_names = set()
        stage_id = create_project_stage(client, "Integration Test Stage", sequence=99, existing_names=existing_names)
        assert isinstance(stage_id, int) and stage_id > 0
        rec = client.search_read(
            'project.task.type', [["id", "=", stage_id]], fields=["name"], limit=1,
        )
        assert rec and rec[0]["name"] == "Integration Test Stage"
        assert "integration test stage" in existing_names
        results.append(("project: create_project_stage + read-back name", True, stage_id))
    except Exception as e:
        results.append(("project: create_project_stage + read-back name", False, str(e)))

    # Step 4 — stage deduplication (unit/mock)
    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"id": 55}]
        existing = {"duplicate stage"}
        result = create_project_stage(mock_client, "Duplicate Stage", existing_names=existing)
        mock_client.create.assert_not_called()
        assert result == 55
        results.append(("project: stage deduplication skips create", True, ""))
    except Exception as e:
        results.append(("project: stage deduplication skips create", False, str(e)))

    # Step 5 — D3: create_project_data end-to-end (batch projects + batch tasks),
    # gemini=None to prove stage assignment falls back without an LLM call.
    rctx = None
    try:
        rctx = _make_rctx(num_projects=2, tasks_per_project=3)
        create_project_data(client, None, rctx)
        assert len(rctx.project_ids) == 2, f"expected 2 projects, got {len(rctx.project_ids)}"
        tasks = client.search_read(
            'project.task', [["project_id", "in", rctx.project_ids]],
            fields=["project_id", "stage_id"], limit=0,
        )
        assert len(tasks) >= 2, f"expected at least 1 task per project, got {len(tasks)}"
        assert any(t.get("stage_id") for t in tasks), "no task got a stage_id assigned"
        results.append((
            "project: create_project_data end-to-end (D3 batch), read-back",
            True, f"{len(rctx.project_ids)} projects, {len(tasks)} tasks",
        ))
    except Exception as e:
        results.append(("project: create_project_data end-to-end (D3 batch), read-back", False, str(e)))

    # Step 6 — D3: create_timesheet_data end-to-end (batch timesheet lines)
    try:
        assert rctx is not None and rctx.project_ids, "step 5 must have created projects"
        rctx.module_selections.hr_timesheet = 4
        create_timesheet_data(client, None, rctx)
        lines = client.search_read(
            'account.analytic.line', [["project_id", "in", rctx.project_ids]],
            fields=["unit_amount", "employee_id"], limit=0,
        )
        assert len(lines) >= 4, f"expected >=4 timesheet lines, got {len(lines)}"
        results.append((
            "project: create_timesheet_data end-to-end (D3 batch), read-back",
            True, f"{len(lines)} lines",
        ))
    except Exception as e:
        results.append(("project: create_timesheet_data end-to-end (D3 batch), read-back", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
