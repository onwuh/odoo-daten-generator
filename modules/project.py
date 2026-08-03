"""Project module: creates projects, tasks, stages (batch Gemini call), and timesheets."""

import datetime
import random

import odoo_actions  # kept for create_employee (shared with hr module)
from config import RunContext
from fallback_data import FALLBACK_PROJECT_STAGES, FALLBACK_TASK_NAMES


# ---------------------------------------------------------------------------
# Low-level project helpers (previously in odoo_actions.py)
# ---------------------------------------------------------------------------

def create_project(client, name):
    print(f"-> Creating Project: {name}")
    return client.create('project.project', {"name": name})


def create_task(client, project_id, name, description=None):
    print(f"-> Creating Task in project {project_id}: {name}")
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
    print(f"-> Creating Timesheet: {hours}h by emp {employee_id} on project {project_id}")
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

    print("\n--- PROJECT: Erstelle Projekte und Aufgaben ---")
    project_name_bank = list(ctx.name_banks.get('project_names', []))
    task_name_bank = ctx.name_banks.get('task_names', []) or FALLBACK_TASK_NAMES
    project_types = ['Implementierung', 'Rollout', 'Pilot', 'Migration']
    industry = ctx.industry

    # Map project_id → name and project_id → [task_id, ...]
    project_names_map = {}
    project_task_map = {}

    for i in range(num_projects):
        pname = (
            project_name_bank.pop(random.randrange(len(project_name_bank)))
            if project_name_bank
            else f"{random.choice(project_types)} {industry} Projekt"
        )
        pid = create_project(client, pname)
        ctx.project_ids.append(pid)
        project_names_map[pid] = pname
        project_task_map[pid] = []

        task_count = max(1, tasks_per_project + random.randint(-2, 3))
        for _ in range(task_count):
            tname = random.choice(task_name_bank)
            task_id = create_task(client, pid, tname)
            project_task_map[pid].append(task_id)

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

    print("--- PROJECT: Erstelle Phasen und verteile Aufgaben ---")
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

    print(f"✅ {len(ctx.project_ids)} Projekte mit Aufgaben und Phasen erstellt.")


def create_timesheet_data(client, gemini, ctx: RunContext) -> None:
    """Creates timesheet entries. Needs at least one employee and one project."""
    num_timesheets = ctx.module_selections.hr_timesheet
    if num_timesheets <= 0 or not ctx.project_ids:
        return

    print("\n--- TIMESHEET: Erstelle Zeiteinträge ---")
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
    for i in range(num_timesheets):
        emp = employee_ids[i % len(employee_ids)]
        proj = ctx.project_ids[i % len(ctx.project_ids)]
        offset = random.randint(1, 180)
        entry_date = today - datetime.timedelta(days=offset)
        # Shift to nearest weekday (Mon=0 … Fri=4)
        if entry_date.weekday() >= 5:
            entry_date -= datetime.timedelta(days=entry_date.weekday() - 4)
        create_timesheet(
            client, emp, proj,
            hours=float(random.randint(1, 8)),
            description=f"Arbeitstag {i + 1}",
            date_str=entry_date.isoformat(),
        )

    print(f"✅ {num_timesheets} Zeiteinträge erstellt.")
