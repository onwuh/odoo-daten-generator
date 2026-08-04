"""Unit tests for modules/hr.py — D3 batch-creation call-count guard (employees only;
leave-request creation stays out of D3 scope, see IMPLEMENTIERUNGSPLAN.md)."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import hr


def _make_ctx(num_employees):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=criteria, module_selections=ModuleSelections(hr=num_employees), industry="IT",
        language_name="German", language_code="de", gemini_model_name="test",
    )


def _mock_client():
    client = MagicMock()
    counter = {"n": 4000}

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

    try:
        client = _mock_client()
        ctx = _make_ctx(num_employees=6)
        hr.create_hr_data(client, gemini=None, ctx=ctx)
        assert client.create_batch.call_count == 1, client.create_batch.call_count
        assert client.create.call_count == 0, "fell back to per-record create()"
        assert len(ctx.employee_ids) == 6, ctx.employee_ids
        results.append((
            "create_hr_data: employees via exactly 1 create_batch call",
            True, f"create_batch calls={client.create_batch.call_count}",
        ))
    except AssertionError as e:
        results.append(("create_hr_data: employees via exactly 1 create_batch call", False, str(e)))

    try:
        client = _mock_client()
        ctx = _make_ctx(num_employees=0)
        hr.create_hr_data(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        assert ctx.employee_ids == []
        results.append(("create_hr_data: num_employees=0 -> no create_batch call (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_hr_data: num_employees=0 -> no create_batch call (Pattern 5)", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
