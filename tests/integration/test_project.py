import sys
import os
import datetime
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.project import create_project, create_task, create_project_stage


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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
