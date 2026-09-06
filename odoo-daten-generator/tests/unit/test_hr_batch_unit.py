"""Unit tests for modules/hr.py — D3 batch-creation call-count guard (employees,
plus the leave-allocation/existing-leaves-read/approve batching added
alongside the JSON2 rate-limit sweep). Leave-request *creation* itself stays
individual, deliberately — see create_leave_data's own comment: overlap
collisions are a real per-record failure mode there, unlike allocations/
approvals, so that one path keeps its per-item isolation instead of batching."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext, TimeoffConfig
from modules import hr


def _make_ctx(num_employees=0, hr_timeoff=None, employee_ids=None):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    sel = ModuleSelections(hr=num_employees)
    if hr_timeoff is not None:
        sel.hr_timeoff = hr_timeoff
    ctx = RunContext(
        criteria=criteria, module_selections=sel, industry="IT",
        language_name="German", language_code="de",
        installed_modules={"hr_holidays", "hr_work_entry"},
    )
    if employee_ids is not None:
        ctx.employee_ids = employee_ids
    return ctx


def _mock_client():
    client = MagicMock()
    counter = {"n": 4000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        if model == 'hr.work.entry.type':
            return [{"id": 1, "name": "Jahresurlaub", "requires_allocation": True}]
        return []  # no existing leaves, no leaves already in 'validate' state

    def _create(model, values, context=None):
        counter["n"] += 1
        return counter["n"]

    client.create_batch.side_effect = _create_batch
    client.search_read.side_effect = _search_read
    client.create.side_effect = _create
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

    # ------------------------------------------------------------------
    # Leave allocations: N employees -> exactly 1 create_batch call (not one
    # create() per employee) and exactly 1 batched approve call.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(
            hr_timeoff=TimeoffConfig(entries_per_employee=0, validate_pct=0),
            employee_ids=[201, 202, 203, 204],
        )
        hr.create_leave_data(client, ctx)
        alloc_batches = [c for c in client.create_batch.call_args_list if c.args[0] == 'hr.leave.allocation']
        assert len(alloc_batches) == 1, alloc_batches
        assert len(alloc_batches[0].args[1]) == 4, alloc_batches[0].args[1]
        approve_calls = [
            c for c in client.call_method.call_args_list
            if c.args[0] == 'hr.leave.allocation' and c.args[1] == 'action_approve'
        ]
        assert len(approve_calls) == 1, approve_calls
        assert len(approve_calls[0].kwargs.get('ids', [])) == 4, approve_calls[0].kwargs
        results.append((
            "create_leave_data: allocations via 1 create_batch + 1 approve call for N employees",
            True, f"employees=4, create_batch calls={len(alloc_batches)}, approve calls={len(approve_calls)}",
        ))
    except AssertionError as e:
        results.append(("create_leave_data: allocations via 1 create_batch + 1 approve call for N employees",
                        False, str(e)))

    # ------------------------------------------------------------------
    # Existing-leaves lookup: N employees -> exactly 1 search_read('hr.leave',
    # ...) call (not one per employee).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(
            hr_timeoff=TimeoffConfig(entries_per_employee=1, validate_pct=0),
            employee_ids=[301, 302, 303],
        )
        hr.create_leave_data(client, ctx)
        leave_reads = [c for c in client.search_read.call_args_list if c.args[0] == 'hr.leave']
        # 1 bulk existing-leaves read; validate_pct=0 skips the state-check read.
        assert len(leave_reads) == 1, leave_reads
        assert leave_reads[0].args[1] == [["employee_id", "in", [301, 302, 303]], ["state", "!=", "refuse"]], \
            leave_reads[0].args[1]
        results.append((
            "create_leave_data: existing-leaves lookup via 1 search_read for N employees",
            True, f"employees=3, search_read('hr.leave') calls={len(leave_reads)}",
        ))
    except AssertionError as e:
        results.append(("create_leave_data: existing-leaves lookup via 1 search_read for N employees",
                        False, str(e)))

    # ------------------------------------------------------------------
    # Approve step: validate_pct=100 with multiple leaves across multiple
    # employees -> exactly 1 batched action_approve call on hr.leave, plus
    # exactly 1 bulk state-check search_read (not one state-check per leave).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(
            hr_timeoff=TimeoffConfig(entries_per_employee=2, avg_length_days=5, past_future_pct=50,
                                     timescale_days=180, validate_pct=100),
            employee_ids=[401, 402],
        )
        leave_ids = hr.create_leave_data(client, ctx)
        assert len(leave_ids) == 4, leave_ids  # 2 employees x 2 entries
        leave_reads = [c for c in client.search_read.call_args_list if c.args[0] == 'hr.leave']
        assert len(leave_reads) == 2, leave_reads  # 1 existing-leaves bulk read + 1 state-check
        approve_calls = [
            c for c in client.call_method.call_args_list
            if c.args[0] == 'hr.leave' and c.args[1] == 'action_approve'
        ]
        assert len(approve_calls) == 1, approve_calls
        assert sorted(approve_calls[0].kwargs.get('ids', [])) == sorted(leave_ids), approve_calls[0].kwargs
        results.append((
            "create_leave_data: leave approval via 1 bulk state-check + 1 batched approve call",
            True, f"leaves=4, search_read('hr.leave') calls={len(leave_reads)}, approve calls={len(approve_calls)}",
        ))
    except AssertionError as e:
        results.append(("create_leave_data: leave approval via 1 bulk state-check + 1 batched approve call",
                        False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
