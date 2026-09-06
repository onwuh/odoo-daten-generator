"""Unit tests for modules/recruiting.py — D3 batch-creation call-count guard."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RecruitmentConfig, RunContext
from modules import recruiting


def _make_ctx(num_jobs, num_candidates):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=criteria,
        module_selections=ModuleSelections(hr_recruitment=RecruitmentConfig(
            num_jobs=num_jobs, num_candidates=num_candidates,
            create_skills=False, num_skill_types=0, skills_per_type=0,
        )),
        industry="IT", language_name="German", language_code="de", gemini_model_name="test",
    )


def _mock_client():
    client = MagicMock()
    counter = {"n": 5000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'hr.department':
            return [{"id": 1, "name": "Allgemein"}]
        return []  # no existing jobs/skills/stages

    client.create_batch.side_effect = _create_batch
    client.search_read.side_effect = _search_read
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # D3: jobs via exactly 1 create_batch call, applicants via exactly 1
    # create_batch call (2 total), never a per-record create() loop.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_jobs=3, num_candidates=5)
        recruiting.create_recruiting_data(client, gemini=None, ctx=ctx)
        assert client.create_batch.call_count == 2, client.create_batch.call_count
        batched_models = [call.args[0] for call in client.create_batch.call_args_list]
        assert batched_models == ['hr.job', 'hr.applicant'], batched_models
        individually_created_models = [call.args[0] for call in client.create.call_args_list]
        assert 'hr.job' not in individually_created_models
        assert 'hr.applicant' not in individually_created_models
        results.append((
            "create_recruiting_data: jobs+applicants via create_batch, not per-record create()",
            True, f"create_batch calls={client.create_batch.call_count}",
        ))
    except AssertionError as e:
        results.append(("create_recruiting_data: jobs+applicants via create_batch, not per-record create()", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 5: num_jobs=0 and num_candidates=0 -> early return, no calls
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_jobs=0, num_candidates=0)
        recruiting.create_recruiting_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        results.append(("create_recruiting_data: num_jobs=0/num_candidates=0 -> no create_batch call (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_recruiting_data: num_jobs=0/num_candidates=0 -> no create_batch call (Pattern 5)", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
