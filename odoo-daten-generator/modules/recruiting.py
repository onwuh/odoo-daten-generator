"""Recruiting module: creates skill types/levels, jobs, and applicants.

Key improvements:
- Single Gemini call for all job summaries (batch)
- Pre-fetched skill levels map to avoid N+1 queries in create_job / create_applicant
"""

import logging
import random

import text_utils
from config import RunContext
from odoo_repository import fetch_skill_levels_map

logger = logging.getLogger(__name__)


def _random_phone_de() -> str:
    return f"+49 {random.randint(150, 179)} {random.randint(1000000, 9999999)}"


# ---------------------------------------------------------------------------
# Low-level recruiting helpers (previously in odoo_actions.py)
# ---------------------------------------------------------------------------

def get_existing_skill_types(client):
    """Get all existing skill types to avoid duplicates."""
    skill_types = client.search_read('hr.skill.type', [], fields=["id", "name"], limit=0)
    return {st.get("name", "").lower(): st.get("id") for st in skill_types}


def get_existing_skills(client):
    """Get all existing skills to avoid duplicates on re-run (B13).

    Returns {(skill_type_id, name_lower): skill_id}.
    """
    skills = client.search_read('hr.skill', [], fields=["id", "name", "skill_type_id"], limit=0)
    result = {}
    for s in skills:
        st_id = s.get("skill_type_id")
        if isinstance(st_id, (list, tuple)) and st_id:
            st_id = st_id[0]
        name = (s.get("name") or "").lower()
        if st_id and name:
            result[(st_id, name)] = s.get("id")
    return result


def create_skill_type(client, name):
    logger.info(f"-> Creating skill type: {name}")
    skill_type_id = client.create('hr.skill.type', {"name": name})
    logger.info(f"   Created skill type ID: {skill_type_id}")
    return skill_type_id


def create_skill(client, skill_type_id, name):
    logger.info(f"->   Creating skill: {name}")
    return client.create('hr.skill', {"name": name, "skill_type_id": skill_type_id})


def create_skill_level(client, skill_type_id, name, level_progress=0):
    logger.info(f"->     Creating level: {name}")
    return client.create('hr.skill.level', {
        "name": name, "skill_type_id": skill_type_id, "level_progress": level_progress,
    })


def get_departments(client):
    return client.search_read('hr.department', [], fields=["id", "name"], limit=0)


def create_department(client, name):
    logger.info(f"-> Creating department: {name}")
    dept_id = client.create('hr.department', {"name": name})
    logger.info(f"   Created department ID: {dept_id}")
    return dept_id


def get_job_stages(client):
    """Get all recruitment stages (global in Odoo)."""
    stages = client.search_read(
        'hr.recruitment.stage', [], fields=["id", "name", "sequence"], limit=0,
    )
    return sorted(stages, key=lambda x: x.get("sequence", 0))


def get_existing_job_names_per_department(client):
    jobs = client.search_read('hr.job', [], fields=["id", "name", "department_id"], limit=0)
    dept_job_names = {}
    for job in jobs:
        dept_id = job.get("department_id")
        if isinstance(dept_id, (list, tuple)) and len(dept_id) > 0:
            dept_id = dept_id[0]
        elif dept_id is None:
            continue
        if dept_id not in dept_job_names:
            dept_job_names[dept_id] = set()
        job_name = job.get("name", "")
        if job_name:
            dept_job_names[dept_id].add(job_name.lower())
    return dept_job_names


def _job_skill_lines(client, job_skill_ids, skill_levels_map=None):
    """Build the (0,0,{...}) command list for hr.job.job_skill_ids."""
    if not job_skill_ids:
        return []
    skills = client.search_read(
        'hr.skill', [["id", "in", job_skill_ids]], fields=["id", "skill_type_id"], limit=0,
    )
    job_skill_lines = []
    for skill in skills:
        skill_id = skill.get("id")
        skill_type_id = skill.get("skill_type_id")
        if isinstance(skill_type_id, (list, tuple)) and len(skill_type_id) > 0:
            skill_type_id = skill_type_id[0]
        if skill_id and skill_type_id:
            if skill_levels_map is not None:
                level_entries = skill_levels_map.get(skill_type_id, [])
            else:
                raw = client.search_read(
                    'hr.skill.level', [["skill_type_id", "=", skill_type_id]],
                    fields=["id"], limit=0,
                )
                level_entries = [{"id": r["id"], "level_progress": 0} for r in raw]
            if not level_entries:
                # hr.job.skill requires skill_level_id (verified live, saas-19.4:
                # "Missing required field 'Skill Level'") — a skill type with no
                # hr.skill.level records can't be attached to a job at all.
                continue
            skill_level_id = random.choice(level_entries)["id"]
            line = {"skill_id": skill_id, "skill_type_id": skill_type_id, "skill_level_id": skill_level_id}
            job_skill_lines.append((0, 0, line))
    return job_skill_lines


def create_job(client, name, department_id, target=3, description=None, job_skill_ids=None, skill_levels_map=None):
    """Create a job (hr.job) with optional skills."""
    logger.info(f"-> Creating job: {name}")
    values = {
        "name": name,
        "department_id": department_id,
        "no_of_recruitment": target,
        "payment_interval": "monthly",
    }
    if description:
        values["description"] = description
    job_skill_lines = _job_skill_lines(client, job_skill_ids, skill_levels_map)
    if job_skill_lines:
        values["job_skill_ids"] = job_skill_lines
    job_id = client.create('hr.job', values)
    logger.info(f"   Created job ID: {job_id}")
    return job_id


def _applicant_skill_lines(client, skill_ids, skill_levels_map=None):
    """Build the (0,0,{...}) command list for hr.applicant.applicant_skill_ids."""
    if not skill_ids:
        return []
    skills = client.search_read(
        'hr.skill', [["id", "in", skill_ids]], fields=["id", "skill_type_id"], limit=0,
    )
    applicant_skill_lines = []
    for skill in skills:
        skill_id = skill.get("id")
        skill_type_id = skill.get("skill_type_id")
        if isinstance(skill_type_id, (list, tuple)) and len(skill_type_id) > 0:
            skill_type_id = skill_type_id[0]
        if skill_id and skill_type_id:
            if skill_levels_map is not None:
                level_entries = skill_levels_map.get(skill_type_id, [])
            else:
                raw = client.search_read(
                    'hr.skill.level', [["skill_type_id", "=", skill_type_id]],
                    fields=["id", "level_progress"], limit=0,
                )
                level_entries = [{"id": r["id"], "level_progress": r.get("level_progress", 0)} for r in raw]
            if not level_entries:
                # hr.applicant.skill requires skill_level_id (same constraint as
                # hr.job.skill, verified live, saas-19.4) — a skill type with no
                # hr.skill.level records can't be attached to an applicant at all.
                continue
            sorted_levels = sorted(level_entries, key=lambda x: x.get("level_progress", 0))
            if random.random() < 0.7 and len(sorted_levels) > 2:
                start_idx = max(0, int(len(sorted_levels) * 0.4))
                level = random.choice(sorted_levels[start_idx:])
            else:
                level = random.choice(level_entries)
            skill_level_id = level["id"]
            line = {"skill_id": skill_id, "skill_type_id": skill_type_id, "skill_level_id": skill_level_id}
            applicant_skill_lines.append((0, 0, line))
    return applicant_skill_lines


def create_applicant(client, job_id, name, email, phone, skill_ids=None, stage_id=None, skill_levels_map=None):
    """Create an applicant (hr.applicant) with skills."""
    logger.info(f"-> Creating applicant: {name}")
    values = {
        "partner_name": name,
        "email_from": email,
        "partner_phone": phone,
        "job_id": job_id,
        "schedule_pay": "monthly",
    }
    applicant_skill_lines = _applicant_skill_lines(client, skill_ids, skill_levels_map)
    if applicant_skill_lines:
        values["applicant_skill_ids"] = applicant_skill_lines
    if stage_id:
        values["stage_id"] = stage_id
    applicant_id = client.create('hr.applicant', values)
    logger.info(f"   Created applicant ID: {applicant_id}")
    return applicant_id


def create_recruiting_data(client, gemini, ctx: RunContext) -> None:
    """Creates skill taxonomy, jobs (with descriptions), and applicants."""
    rec_config = ctx.module_selections.hr_recruitment
    if not isinstance(rec_config, dict):
        return
    num_jobs = rec_config.get("num_jobs", 0)
    num_candidates = rec_config.get("num_candidates", 0)
    create_skills = rec_config.get("create_skills", False)
    num_skill_types = rec_config.get("num_skill_types", 0)
    skills_per_type = rec_config.get("skills_per_type", 0)

    if num_jobs <= 0 and num_candidates <= 0:
        return

    logger.info("\n--- RECRUITING: Erstelle Recruiting-Daten ---")
    industry = ctx.industry

    # Fetch all recruiting data in one Gemini call
    recruiting_data = {}
    if gemini:
        recruiting_data = gemini.fetch_recruiting_data(
            industry, num_jobs, num_candidates, num_skill_types, skills_per_type,
            ctx.language_name
        ) or {}

    # Create skills
    all_skill_ids = []
    if create_skills and num_skill_types > 0:
        all_skill_ids = _create_skills(client, recruiting_data, num_skill_types, skills_per_type)

    # If no new skills were created, use any existing skills
    if not all_skill_ids:
        existing = client.search_read('hr.skill', [], fields=["id"], limit=0)
        all_skill_ids = [s["id"] for s in existing]

    # Pre-fetch skill levels map once (avoids N+1 in create_job / create_applicant)
    skill_levels_map = fetch_skill_levels_map(client) if all_skill_ids else {}

    # Ensure at least one department exists
    departments = get_departments(client)
    if not departments:
        dept_id = create_department(client, "Allgemein")
        departments = [{"id": dept_id, "name": "Allgemein"}]

    # Create jobs
    job_ids = _create_jobs(
        client, gemini, ctx, recruiting_data, num_jobs, departments, all_skill_ids, skill_levels_map
    )

    # Create applicants
    if num_candidates > 0 and job_ids:
        _create_applicants(
            client, ctx, recruiting_data, num_candidates, job_ids, all_skill_ids, skill_levels_map
        )


# ------------------------------------------------------------------
# Skills
# ------------------------------------------------------------------

def _create_skills(client, recruiting_data: dict, num_skill_types: int, skills_per_type: int):
    logger.info("\n--- RECRUITING: Erstelle Kompetenzen ---")
    all_skill_ids = []
    existing_skill_types = get_existing_skill_types(client)
    existing_skills = get_existing_skills(client)

    for skill_type_data in recruiting_data.get("skill_types", [])[:num_skill_types]:
        skill_type_name = skill_type_data.get("name", "")
        if not skill_type_name:
            continue

        if skill_type_name.lower() in existing_skill_types:
            logger.info(f"-> Kompetenzart '{skill_type_name}' existiert bereits, überspringe")
            skill_type_id = existing_skill_types[skill_type_name.lower()]
        else:
            skill_type_id = create_skill_type(client, skill_type_name)
            existing_skill_types[skill_type_name.lower()] = skill_type_id

            # Levels only for newly-created types — an existing type already has
            # levels; re-creating them here would duplicate on every run (B13).
            levels = skill_type_data.get("levels", [])
            if len(levels) < 3:
                levels = ["Grundlagen", "Fortgeschritten", "Experte"]
            for i, level_name in enumerate(levels):
                progress = int((i + 1) * 100 / len(levels))
                create_skill_level(client, skill_type_id, level_name, progress)

        for skill_name in skill_type_data.get("skills", [])[:skills_per_type]:
            skill_key = (skill_type_id, skill_name.lower())
            if skill_key in existing_skills:
                logger.info(f"->   Kompetenz '{skill_name}' existiert bereits, überspringe")
                sid = existing_skills[skill_key]
            else:
                sid = create_skill(client, skill_type_id, skill_name)
                existing_skills[skill_key] = sid
            all_skill_ids.append(sid)

    return all_skill_ids


# ------------------------------------------------------------------
# Jobs
# ------------------------------------------------------------------

def _create_jobs(client, gemini, ctx, recruiting_data, num_jobs, departments, all_skill_ids, skill_levels_map):
    if not departments:
        logger.warning("⚠️  Keine Abteilungen — Jobs übersprungen.")
        return []
    job_titles = recruiting_data.get("job_titles", [])[:num_jobs]
    if not job_titles:
        job_titles = [f"Stelle {i + 1}" for i in range(num_jobs)]

    # Batch-fetch all job summaries in one Gemini call
    job_summaries = {}
    if gemini and job_titles:
        job_summaries = gemini.fetch_job_summaries_batch(job_titles, ctx.industry, ctx.language_name)

    existing_dept_job_names = get_existing_job_names_per_department(client)
    dept_job_names = {k: v.copy() for k, v in existing_dept_job_names.items()}

    job_vals_list = []
    for idx, job_title in enumerate(job_titles):
        dept = departments[idx % len(departments)]
        dept_id = dept["id"]
        dept_job_names.setdefault(dept_id, set())

        unique_title = job_title
        suffix = 1
        while unique_title.lower() in dept_job_names[dept_id]:
            unique_title = f"{job_title} ({suffix})"
            suffix += 1
        dept_job_names[dept_id].add(unique_title.lower())

        job_skills = []
        if all_skill_ids:
            n = random.randint(1, min(4, len(all_skill_ids)))
            job_skills = random.sample(all_skill_ids, n)

        values = {
            "name": unique_title,
            "department_id": dept_id,
            "no_of_recruitment": random.randint(1, 5),
            "payment_interval": "monthly",
        }
        description = job_summaries.get(job_title)
        if description:
            values["description"] = description
        job_skill_lines = _job_skill_lines(client, job_skills, skill_levels_map)
        if job_skill_lines:
            values["job_skill_ids"] = job_skill_lines
        job_vals_list.append(values)

    job_ids = client.create_batch('hr.job', job_vals_list)
    logger.info(f"✅ {len(job_ids)} Stellen erstellt.")
    return job_ids


# ------------------------------------------------------------------
# Applicants
# ------------------------------------------------------------------

def _create_applicants(client, ctx, recruiting_data, num_candidates, job_ids, all_skill_ids, skill_levels_map):
    logger.info("\n--- RECRUITING: Erstelle Bewerber ---")
    if not job_ids:
        logger.warning("⚠️  Keine Jobs vorhanden — Bewerbungen übersprungen.")
        return []
    names = recruiting_data.get("candidate_names", [])[:num_candidates]

    # Fill missing names with fallbacks
    while len(names) < num_candidates:
        names.append(f"Bewerber {len(names) + 1}")

    # Emails/phones are derived deterministically from the name — never
    # requested from the LLM (IMPLEMENTIERUNGSPLAN.md A2).
    emails = [text_utils.email_from_name(n) for n in names]
    phones = [_random_phone_de() for _ in names]

    # Pre-fetch stages once
    stages = get_job_stages(client)

    candidates_per_job = num_candidates // len(job_ids)
    remaining = num_candidates % len(job_ids)
    candidate_idx = 0

    applicant_vals_list = []
    for job_idx, job_id in enumerate(job_ids):
        count = candidates_per_job + (1 if job_idx < remaining else 0)
        for _ in range(count):
            if candidate_idx >= num_candidates:
                break
            candidate_skills = []
            if all_skill_ids:
                n = random.randint(1, min(3, len(all_skill_ids)))
                candidate_skills = random.sample(all_skill_ids, n)
            stage_id = random.choice(stages)["id"] if stages else None
            values = {
                "partner_name": names[candidate_idx],
                "email_from": emails[candidate_idx],
                "partner_phone": phones[candidate_idx],
                "job_id": job_id,
                "schedule_pay": "monthly",
            }
            applicant_skill_lines = _applicant_skill_lines(client, candidate_skills, skill_levels_map)
            if applicant_skill_lines:
                values["applicant_skill_ids"] = applicant_skill_lines
            if stage_id:
                values["stage_id"] = stage_id
            applicant_vals_list.append(values)
            candidate_idx += 1

    client.create_batch('hr.applicant', applicant_vals_list)
    logger.info(f"✅ {num_candidates} Bewerber erstellt und auf {len(job_ids)} Stellen verteilt.")
