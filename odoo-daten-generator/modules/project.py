"""Project module: creates projects, tasks, stages (batch Gemini call), and timesheets."""

import logging
import datetime
import random

import odoo_actions  # kept for create_employee (shared with hr module)
from config import RunContext
from fallback_data import FALLBACK_PROJECT_STAGES, FALLBACK_TASK_NAMES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level project helpers (previously in odoo_actions.py)
# ---------------------------------------------------------------------------

def create_project(client, name):
    logger.info(f"-> Creating Project: {name}")
    return client.create('project.project', {"name": name})


def create_task(client, project_id, name, description=None):
    logger.info(f"-> Creating Task in project {project_id}: {name}")
    values = {"name": name, "project_id": project_id}
    if description:
        values["description"] = description
    return client.create('project.task', values)


def create_project_stage(client, name, sequence=10, existing_names: set = None):
    """Create a global project stage, skipping if a stage with that name already exists."""
    if existing_names is not None and name.lower() in existing_names:
        # Look up existing stage ID by name
        stages = client.search_read(
            'project.task.type', [["name", "=", name]], fields=["id"], limit=1,
        )
        if stages:
            return stages[0]["id"]
    values = {"name": name, "sequence": sequence}
    stage_id = client.create('project.task.type', values)
    if existing_names is not None:
        existing_names.add(name.lower())
    return stage_id


def _select_ordered_stages(stages: list, num_stages: int) -> list:
    """Pick a subset of stages, preserving the source order (the LLM's workflow
    progression), instead of random.sample's shuffled order."""
    if len(stages) >= num_stages:
        idx = sorted(random.sample(range(len(stages)), k=num_stages))
        return [stages[i] for i in idx]
    return stages[:num_stages]


def update_task_stage(client, task_id, stage_id):
    return client.write('project.task', [task_id], {"stage_id": stage_id})


def create_timesheet(client, employee_id, project_id, hours, description, date_str):
    logger.info(f"-> Creating Timesheet: {hours}h by emp {employee_id} on project {project_id}")
    values = {
        "name": description,
        "employee_id": employee_id,
        "project_id": project_id,
        "unit_amount": hours,
        "date": date_str,
    }
    return client.create('account.analytic.line', values)


def create_project_data(client, gemini, ctx: RunContext) -> None:
    """Creates projects, tasks, and stages. Uses a single Gemini call for all stage names."""
    num_projects = ctx.module_selections.project
    tasks_per_project = ctx.module_selections.tasks_per_project
    if num_projects <= 0:
        return

    logger.info("\n--- PROJECT: Erstelle Projekte und Aufgaben ---")
    project_name_bank = list(ctx.name_banks.get('project_names', []))
    task_name_bank = ctx.name_banks.get('task_names', []) or FALLBACK_TASK_NAMES
    project_types = ['Implementierung', 'Rollout', 'Pilot', 'Migration']
    industry = ctx.industry

    # Pass 1: batch-create all projects (D3 — was 1 create() call per project).
    project_names = []
    for i in range(num_projects):
        pname = (
            project_name_bank.pop(random.randrange(len(project_name_bank)))
            if project_name_bank
            else f"{random.choice(project_types)} {industry} Projekt"
        )
        project_names.append(pname)

    project_ids = client.create_batch('project.project', [{"name": n} for n in project_names])
    ctx.project_ids.extend(project_ids)
    project_names_map = dict(zip(project_ids, project_names))

    # Pass 2: batch-create all tasks across all projects in one call, then
    # slice the returned ids back per project (create_batch preserves order).
    task_vals_list = []
    task_counts_by_project = []  # [(project_id, count), ...] same order as project_ids
    for pid in project_ids:
        task_count = max(1, tasks_per_project + random.randint(-2, 3))
        task_counts_by_project.append((pid, task_count))
        for _ in range(task_count):
            tname = random.choice(task_name_bank)
            task_vals_list.append({"name": tname, "project_id": pid})

    all_task_ids = client.create_batch('project.task', task_vals_list)
    project_task_map = {}
    idx = 0
    for pid, count in task_counts_by_project:
        project_task_map[pid] = all_task_ids[idx:idx + count]
        idx += count

    # Batch Gemini call for all project stages
    all_project_names = list(project_names_map.values())
    gemini_stages_map = {}
    if gemini and all_project_names:
        gemini_stages_map = gemini.fetch_all_project_stages(
            all_project_names, industry, ctx.language_name
        )

    fallback_stages = FALLBACK_PROJECT_STAGES.get(industry, FALLBACK_PROJECT_STAGES['default'])

    # Pre-fetch existing stage names once to avoid duplicates across all projects
    existing_stage_records = client.search_read(
        'project.task.type', [], fields=["name"], limit=0,
    )
    existing_stage_names = {r["name"].lower() for r in existing_stage_records}

    logger.info("--- PROJECT: Erstelle Phasen und verteile Aufgaben ---")
    for pid in ctx.project_ids:
        project_name = project_names_map[pid]
        stages = gemini_stages_map.get(project_name, [])
        if not stages or len(stages) < 4:
            stages = fallback_stages

        num_stages = random.randint(4, 6)
        selected = _select_ordered_stages(stages, num_stages)
        if len(selected) < num_stages:
            default = FALLBACK_PROJECT_STAGES['default']
            selected.extend(default[:num_stages - len(selected)])

        stage_ids = [
            create_project_stage(client, sname, sequence=seq * 10, existing_names=existing_stage_names)
            for seq, sname in enumerate(selected[:num_stages], start=1)
        ]

        for task_id in project_task_map.get(pid, []):
            update_task_stage(client, task_id, random.choice(stage_ids))

    logger.info(f"✅ {len(ctx.project_ids)} Projekte mit Aufgaben und Phasen erstellt.")


def create_timesheet_data(client, gemini, ctx: RunContext) -> None:
    """Creates timesheet entries. Needs at least one employee and either a
    bulk project or a real order-linked billable task (R8)."""
    num_timesheets = ctx.module_selections.hr_timesheet
    if num_timesheets <= 0:
        return

    # R8: sale.order.line's auto-created task_id (via service_tracking, set
    # on order confirmation) is what makes qty_delivered non-zero once a
    # timesheet is logged against it with so_line set — these lines claim the
    # budget first, driving real invoicing for as many service lines as
    # possible; any remaining budget fills the existing bulk-project pool.
    billable_lines = []
    if ctx.confirmed_order_ids:
        billable_lines = client.search_read(
            'sale.order.line',
            [['order_id', 'in', ctx.confirmed_order_ids], ['task_id', '!=', False]],
            fields=['task_id', 'project_id'], limit=0,
        )

    if not ctx.project_ids and not billable_lines:
        return

    logger.info("\n--- TIMESHEET: Erstelle Zeiteinträge ---")
    # Use employees created this run; fall back to querying Odoo only if none
    employee_ids = list(ctx.employee_ids)
    if not employee_ids:
        from fallback_data import FALLBACK_EMPLOYEES
        employees = client.search_read('hr.employee', [["active", "=", True]], fields=["id"], limit=0)
        employee_ids = [e['id'] for e in employees]
        fallback_names = ctx.name_banks.get('employee_names', []) or FALLBACK_EMPLOYEES
        while len(employee_ids) < 3:
            name = fallback_names[len(employee_ids) % len(fallback_names)]
            employee_ids.append(odoo_actions.create_employee(client, name))

    today = datetime.date.today()

    def _next_date():
        offset = random.randint(1, 180)
        d = today - datetime.timedelta(days=offset)
        # Shift to nearest weekday (Mon=0 … Fri=4)
        if d.weekday() >= 5:
            d -= datetime.timedelta(days=d.weekday() - 4)
        return d

    def _unwrap(v):
        return v[0] if isinstance(v, (list, tuple)) else v

    timesheet_vals_list = []
    remaining = num_timesheets

    # Billable order-linked tasks claim the budget first.
    for line in billable_lines:
        if remaining <= 0:
            break
        emp = employee_ids[len(timesheet_vals_list) % len(employee_ids)]
        timesheet_vals_list.append({
            "name": f"Projektarbeit {len(timesheet_vals_list) + 1}",
            "employee_id": emp,
            "task_id": _unwrap(line.get("task_id")),
            "project_id": _unwrap(line.get("project_id")),
            "so_line": line["id"],
            "unit_amount": float(random.randint(2, 8)),
            "date": _next_date().isoformat(),
        })
        remaining -= 1

    # Remaining budget fills the existing random-bulk-project pool, unchanged.
    if ctx.project_ids:
        for i in range(remaining):
            emp = employee_ids[len(timesheet_vals_list) % len(employee_ids)]
            proj = ctx.project_ids[i % len(ctx.project_ids)]
            timesheet_vals_list.append({
                "name": f"Arbeitstag {len(timesheet_vals_list) + 1}",
                "employee_id": emp,
                "project_id": proj,
                "unit_amount": float(random.randint(1, 8)),
                "date": _next_date().isoformat(),
            })

    if timesheet_vals_list:
        client.create_batch('account.analytic.line', timesheet_vals_list)
        logger.info(f"✅ {len(timesheet_vals_list)} Zeiteinträge erstellt.")
