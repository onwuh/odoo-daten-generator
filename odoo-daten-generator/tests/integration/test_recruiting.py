import sys
import os
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.recruiting import (
    get_departments,
    create_department,
    create_job,
    create_applicant,
    create_skill_type,
    create_skill,
    _create_applicants,
    _create_skills,
    create_recruiting_data,
)
from config import DemoCriteria, ModuleSelections, RecruitmentConfig, RunContext


def _make_rctx(num_jobs, num_candidates):
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit,
        module_selections=ModuleSelections(hr_recruitment=RecruitmentConfig(
            num_jobs=num_jobs, num_candidates=num_candidates,
            create_skills=False, num_skill_types=0, skills_per_type=0,
        )),
        industry="IT", language_name="German", language_code="de_DE", gemini_model_name="test",
    )


def run(client, ctx):
    """
    Populates: ctx.job_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    # hr.applicant does not exist as a model at all without hr_recruitment
    # installed (confirmed live, 2026-08-29: a bare "the model 'hr.applicant'
    # does not exist" 404 on demo-test5, where hr_recruitment is
    # state=uninstalled) — a genuinely different app-installation-state issue
    # from a field-schema mismatch, not something any field rename in
    # modules/recruiting.py could fix. hr.job itself DOES exist independently
    # (it's a base-hr model used for bare job-position tracking), but its
    # hr_recruitment-added fields — including payment_interval — do not,
    # which is what a live run against this instance actually surfaces first.
    # Steps 3/5/6 use hr.skill(.type)/hr.skill.level, which are NOT part of
    # hr_recruitment and stay unconditional. This is the same
    # ctx.installed_modules gate test_hr.py needed for its leave-related
    # steps in S10 Phase A, for the identical reason: these steps call the
    # low-level module functions directly, bypassing orchestrator.py's own
    # (correct) "hr_recruitment" in ctx.installed_modules gate.
    recruitment_installed = 'hr_recruitment' in ctx.installed_modules

    # Setup — resolve or create department
    dept_id = None
    try:
        depts = get_departments(client)
        if depts:
            dept_id = depts[0]["id"]
        else:
            dept_id = create_department(client, "Integration Test Abteilung")
    except Exception as e:
        results.append(("recruiting: SKIP — could not resolve department", False, str(e)))
        return False, results

    job_id = None

    # Step 1 — Create job (delete leftover from previous run first)
    if not recruitment_installed:
        results.append(("recruiting: create job + read-back name SKIP — hr_recruitment nicht installiert",
                        True, "skipped"))
    else:
        try:
            existing = client.search_read(
                'hr.job',
                [["name", "=", "Integration Test Stelle"], ["department_id", "=", dept_id]],
                fields=["id"],
                limit=1,
            )
            if existing:
                client.call_method('hr.job', 'unlink', ids=[existing[0]["id"]])
            job_id = create_job(
                client,
                "Integration Test Stelle",
                dept_id,
                target=1,
                description="Automatisch erstellte Teststelle",
            )
            assert isinstance(job_id, int) and job_id > 0
            rec = client.search_read(
                'hr.job',
                [["id", "=", job_id]],
                fields=["name"],
                limit=1,
            )
            assert rec and rec[0]["name"] == "Integration Test Stelle"
            ctx.job_ids.append(job_id)
            results.append(("recruiting: create job + read-back name", True, job_id))
        except Exception as e:
            results.append(("recruiting: create job + read-back name", False, str(e)))

    # Step 2 — Create applicant
    if not recruitment_installed:
        results.append(("recruiting: create applicant + read-back partner_name SKIP — hr_recruitment nicht installiert",
                        True, "skipped"))
    else:
        try:
            assert job_id, "No job created in step 1"
            applicant_id = create_applicant(
                client,
                job_id,
                "Max Bewerber",
                "bewerber@integration.example",
                "+49 000 0",
            )
            assert isinstance(applicant_id, int) and applicant_id > 0
            rec = client.search_read(
                'hr.applicant',
                [["id", "=", applicant_id]],
                fields=["partner_name"],
                limit=1,
            )
            assert rec and rec[0]["partner_name"] == "Max Bewerber"
            results.append(("recruiting: create applicant + read-back partner_name", True, applicant_id))
        except Exception as e:
            results.append(("recruiting: create applicant + read-back partner_name", False, str(e)))

    # Step 3 — create skill type + skill (live)
    try:
        skill_type_id = create_skill_type(client, "Integration Test Kompetenz")
        assert isinstance(skill_type_id, int) and skill_type_id > 0
        skill_id = create_skill(client, skill_type_id, "Integration Test Skill")
        assert isinstance(skill_id, int) and skill_id > 0
        rec = client.search_read(
            'hr.skill', [["id", "=", skill_id]], fields=["name", "skill_type_id"], limit=1,
        )
        assert rec, "Skill not found"
        st = rec[0]["skill_type_id"]
        st_id = st[0] if isinstance(st, (list, tuple)) else st
        assert st_id == skill_type_id
        results.append(("recruiting: create skill_type + skill + read-back", True, f"skill_id={skill_id}"))
    except Exception as e:
        results.append(("recruiting: create skill_type + skill + read-back", False, str(e)))

    # Step 4 — applicant email + phone read-back (live)
    if not recruitment_installed:
        results.append(("recruiting: applicant email_from + partner_phone read-back SKIP — "
                        "hr_recruitment nicht installiert", True, "skipped"))
    else:
        try:
            assert job_id, "No job created in step 1"
            applicant2_id = create_applicant(
                client, job_id,
                "Erika Bewerberin",
                "erika@integration.example",
                "+49 111 2222222",
            )
            assert isinstance(applicant2_id, int) and applicant2_id > 0
            rec = client.search_read(
                'hr.applicant',
                [["id", "=", applicant2_id]],
                fields=["partner_name", "email_from", "partner_phone"],
                limit=1,
            )
            assert rec, "Applicant not found"
            assert rec[0]["email_from"] == "erika@integration.example", \
                f"email_from mismatch: {rec[0].get('email_from')}"
            assert rec[0]["partner_phone"] == "+49 111 2222222", \
                f"partner_phone mismatch: {rec[0].get('partner_phone')}"
            results.append(("recruiting: applicant email_from + partner_phone read-back", True, applicant2_id))
        except Exception as e:
            results.append(("recruiting: applicant email_from + partner_phone read-back", False, str(e)))

    # Step 5 — empty job_ids guard (unit/mock)
    try:
        mock_client = MagicMock()
        from config import DemoCriteria, ModuleSelections, RunContext
        criteria = DemoCriteria(
            mode="both", industry="Test", num_companies=1,
            num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
            num_services=0, num_consumables=0, num_storables=0,
        )
        mock_ctx = RunContext(
            criteria=criteria, module_selections=ModuleSelections(),
            industry="Test", language_name="German", language_code="de_DE",
            gemini_model_name="test",
        )
        result = _create_applicants(mock_client, mock_ctx, {}, 5, [], [], {})
        assert result == [], f"Expected [], got {result}"
        mock_client.create.assert_not_called()
        results.append(("recruiting: _create_applicants empty job_ids → [] no crash", True, ""))
    except Exception as e:
        results.append(("recruiting: _create_applicants empty job_ids → [] no crash", False, str(e)))

    # Step 6 — B13: re-running _create_skills for an existing type must not duplicate levels or skills
    try:
        rec_data = {
            "skill_types": [{
                "name": "Integration Test Kompetenz B13",
                "skills": ["Skill A", "Skill B"],
                "levels": ["Grundlagen", "Fortgeschritten", "Experte"],
            }],
        }
        _create_skills(client, rec_data, num_skill_types=1, skills_per_type=2)
        skill_type_id = None
        st = client.search_read(
            'hr.skill.type', [["name", "=", "Integration Test Kompetenz B13"]],
            fields=["id"], limit=1,
        )
        assert st, "skill type not created on first run"
        skill_type_id = st[0]["id"]
        levels_after_first = client.search_read(
            'hr.skill.level', [["skill_type_id", "=", skill_type_id]], fields=["id"], limit=0,
        )
        count_first = len(levels_after_first)
        assert count_first > 0, "no levels created on first run"
        skills_after_first = client.search_read(
            'hr.skill', [["skill_type_id", "=", skill_type_id]], fields=["id"], limit=0,
        )
        skill_count_first = len(skills_after_first)
        assert skill_count_first > 0, "no skills present after first run"

        # Simulate a second generator run against the same DB state
        _create_skills(client, rec_data, num_skill_types=1, skills_per_type=2)
        levels_after_second = client.search_read(
            'hr.skill.level', [["skill_type_id", "=", skill_type_id]], fields=["id"], limit=0,
        )
        count_second = len(levels_after_second)
        assert count_second == count_first, (
            f"level count grew on re-run: {count_first} -> {count_second} (duplicates)"
        )
        skills_after_second = client.search_read(
            'hr.skill', [["skill_type_id", "=", skill_type_id]], fields=["id"], limit=0,
        )
        skill_count_second = len(skills_after_second)
        assert skill_count_second == skill_count_first, (
            f"skill count grew on re-run: {skill_count_first} -> {skill_count_second} (duplicates)"
        )
        results.append((
            "recruiting: repeat run does not duplicate skills/levels (B13)", True,
            f"skills={skill_count_second}, levels={count_second} (constant across 2 runs)",
        ))
    except Exception as e:
        results.append(("recruiting: repeat run does not duplicate skills/levels (B13)", False, str(e)))

    # Step 7 — D3: create_recruiting_data end-to-end (batched jobs + applicants),
    # gemini=None to prove it needs no LLM call for the batch path.
    # create_recruiting_data does not persist job_ids on ctx (pre-existing, not
    # a D3 concern), so new jobs are identified via a before/after id diff.
    if not recruitment_installed:
        results.append(("recruiting: create_recruiting_data end-to-end (D3 batch), read-back SKIP — "
                        "hr_recruitment nicht installiert", True, "skipped"))
    else:
        try:
            before_job_ids = {j["id"] for j in client.search_read('hr.job', [], fields=["id"], limit=0)}
            rctx = _make_rctx(num_jobs=2, num_candidates=3)
            create_recruiting_data(client, None, rctx)
            after_job_ids = {j["id"] for j in client.search_read('hr.job', [], fields=["id"], limit=0)}
            new_job_ids = list(after_job_ids - before_job_ids)
            assert len(new_job_ids) == 2, f"expected 2 new jobs, got {len(new_job_ids)}"
            applicants = client.search_read(
                'hr.applicant', [["job_id", "in", new_job_ids]],
                fields=["partner_name", "email_from", "partner_phone"], limit=0,
            )
            assert len(applicants) == 3, f"expected 3 applicants, got {len(applicants)}"
            assert all(a.get("email_from") and a.get("partner_phone") for a in applicants), \
                "applicant missing derived email/phone"
            results.append((
                "recruiting: create_recruiting_data end-to-end (D3 batch), read-back",
                True, f"{len(new_job_ids)} jobs, {len(applicants)} applicants",
            ))
        except Exception as e:
            results.append(("recruiting: create_recruiting_data end-to-end (D3 batch), read-back", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
