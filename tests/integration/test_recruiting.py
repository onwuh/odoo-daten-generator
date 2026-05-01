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
)


def run(client, ctx):
    """
    Populates: ctx.job_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
