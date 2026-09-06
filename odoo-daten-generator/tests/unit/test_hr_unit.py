"""Unit tests for create_leave_data() using mock client (no live Odoo)."""
import datetime
import os
import sys
import unittest.mock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import ModuleSelections, RunContext, DemoCriteria, TimeoffConfig
from modules.hr import create_leave_data, get_or_create_annual_leave_type


def _make_ctx(hr_timeoff, employee_ids=None, installed_modules=None) -> RunContext:
    criteria = DemoCriteria(
        mode="both", industry="Test", num_companies=1,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    sel = ModuleSelections()
    sel.hr_timeoff = hr_timeoff
    ctx = RunContext(
        criteria=criteria, module_selections=sel,
        industry="Test", language_name="Deutsch", language_code="de",
        gemini_model_name="",
        # S10/R10: create_leave_data now gates on hr_holidays/hr_work_entry
        # being installed (they, not hr, are where hr.leave/hr.work.entry.type
        # actually ship). Every positive case below exercises the leave logic
        # itself, so both are installed by default here; the one negative case
        # for the new gate passes installed_modules=set() explicitly.
        installed_modules=(installed_modules if installed_modules is not None
                           else {"hr_holidays", "hr_work_entry"}),
    )
    ctx.employee_ids = employee_ids or []
    return ctx


def _mock_client_base(leave_type_id=1):
    """Mock client that returns a leave type on search_read and [] for get_existing_leaves."""
    mock_client = unittest.mock.MagicMock()
    mock_client.search_read.return_value = [
        {"id": leave_type_id, "name": "Jahresurlaub", "requires_allocation": True}
    ]
    return mock_client


def run():
    """Returns (all_passed, [(label, ok, detail), ...])"""
    results = []

    # Step 6 — hr_timeoff=None (feature off) → no leave records created
    try:
        mock_client = unittest.mock.MagicMock()
        test_ctx = _make_ctx(None, employee_ids=[999])
        leave_ids = create_leave_data(mock_client, test_ctx)
        assert leave_ids == []
        mock_client.create.assert_not_called()
        results.append(("hr: create_leave_data hr_timeoff=None skips all", True, "no API calls"))
    except Exception as e:
        results.append(("hr: create_leave_data hr_timeoff=None skips all", False, str(e)))

    # Step 7 — entries_per_employee=3 → exactly 3 leave IDs per employee
    try:
        mock_client = _mock_client_base()
        create_id_counter = iter(range(1, 1000))
        mock_client.create.side_effect = lambda *a, **kw: next(create_id_counter)
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=3, avg_length_days=5,
                                           past_future_pct=50, timescale_days=180, validate_pct=0), employee_ids=[101, 102])
        leave_ids = create_leave_data(mock_client, test_ctx)
        assert len(leave_ids) == 6, f"expected 6 got {len(leave_ids)}"
        results.append(("hr: entries_per_employee=3 → 3 per employee", True, f"{len(leave_ids)} IDs"))
    except Exception as e:
        results.append(("hr: entries_per_employee=3 → 3 per employee", False, str(e)))

    # Step 8 — past_future_pct=0 → all leave dates in the past
    try:
        mock_client = _mock_client_base()
        created_vals = []

        def _capture_create(model, vals):
            if model == 'hr.leave':
                created_vals.append(vals)
            return len(created_vals) + 100

        mock_client.create.side_effect = _capture_create
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=2, avg_length_days=5,
                                           past_future_pct=0, timescale_days=180, validate_pct=0), employee_ids=[101])
        create_leave_data(mock_client, test_ctx)
        today_str = str(datetime.date.today())
        assert all(v["date_from"] < today_str for v in created_vals), \
            f"future date found: {[v['date_from'] for v in created_vals]}"
        results.append(("hr: past_future_pct=0 → all dates in past", True, f"{len(created_vals)} entries"))
    except Exception as e:
        results.append(("hr: past_future_pct=0 → all dates in past", False, str(e)))

    # Step 9 — past_future_pct=100 → all leave dates in the future
    try:
        mock_client = _mock_client_base()
        created_vals = []

        def _capture_create2(model, vals):
            if model == 'hr.leave':
                created_vals.append(vals)
            return len(created_vals) + 200

        mock_client.create.side_effect = _capture_create2
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=2, avg_length_days=5,
                                           past_future_pct=100, timescale_days=180, validate_pct=0), employee_ids=[101])
        create_leave_data(mock_client, test_ctx)
        today_str = str(datetime.date.today())
        assert all(v["date_from"] > today_str for v in created_vals), \
            f"past date found: {[v['date_from'] for v in created_vals]}"
        results.append(("hr: past_future_pct=100 → all dates in future", True, f"{len(created_vals)} entries"))
    except Exception as e:
        results.append(("hr: past_future_pct=100 → all dates in future", False, str(e)))

    # Step 10 — validate_pct=0 → action_approve never called
    try:
        mock_client = _mock_client_base()
        create_id_seq = iter(range(1, 1000))
        mock_client.create.side_effect = lambda *a, **kw: next(create_id_seq)
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=2, avg_length_days=5,
                                           past_future_pct=50, timescale_days=180, validate_pct=0), employee_ids=[101])
        create_leave_data(mock_client, test_ctx)
        leave_approve_calls = [
            c for c in mock_client.call_method.call_args_list
            if c.args[0] == 'hr.leave' and c.args[1] == 'action_approve'
        ]
        assert len(leave_approve_calls) == 0, f"unexpected calls: {leave_approve_calls}"
        results.append(("hr: validate_pct=0 → action_approve never called", True, ""))
    except Exception as e:
        results.append(("hr: validate_pct=0 → action_approve never called", False, str(e)))

    # Step 11 — validate_pct=100 → action_approve called for every leave ID
    try:
        mock_client = _mock_client_base()
        create_id_seq2 = iter(range(501, 1000))
        mock_client.create.side_effect = lambda *a, **kw: next(create_id_seq2)
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=2, avg_length_days=5,
                                           past_future_pct=50, timescale_days=180, validate_pct=100), employee_ids=[101])
        leave_ids = create_leave_data(mock_client, test_ctx)
        approve_calls = [
            c for c in mock_client.call_method.call_args_list
            if c.args[0] == 'hr.leave' and c.args[1] == 'action_approve'
        ]
        # Approval is batched into one call_method(ids=[...]) instead of one
        # call per leave — assert every leave id was covered, not call count.
        approved_ids = [lid for c in approve_calls for lid in c.kwargs.get('ids', [])]
        assert sorted(approved_ids) == sorted(leave_ids), \
            f"expected all of {leave_ids} approved, got {approved_ids}"
        results.append(("hr: validate_pct=100 → action_approve for every leave", True,
                        f"{len(approved_ids)} ids across {len(approve_calls)} call(s)"))
    except Exception as e:
        results.append(("hr: validate_pct=100 → action_approve for every leave", False, str(e)))

    # Step 12 — Overlap detection: no two leaves for same employee share date range
    try:
        mock_client = _mock_client_base()
        created_dates = []
        id_counter = iter(range(1, 1000))

        def _capture_overlap(model, vals):
            if model == 'hr.leave':
                created_dates.append((vals['date_from'][:10], vals['date_to'][:10]))
            return next(id_counter)

        mock_client.create.side_effect = _capture_overlap
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=4, avg_length_days=5,
                                           past_future_pct=50, timescale_days=365, validate_pct=0), employee_ids=[101])
        create_leave_data(mock_client, test_ctx)

        overlap_found = False
        for i, (s1, e1) in enumerate(created_dates):
            for j, (s2, e2) in enumerate(created_dates):
                if i != j and s1 <= e2 and e1 >= s2:
                    overlap_found = True
        assert not overlap_found, f"Overlapping dates: {created_dates}"
        results.append(("hr: overlap detection — no duplicate date ranges", True,
                        f"{len(created_dates)} leaves, no overlaps"))
    except Exception as e:
        results.append(("hr: overlap detection — no duplicate date ranges", False, str(e)))

    # Step 13 — Individual failure isolation: one failed create_leave_request doesn't abort batch
    try:
        mock_client = _mock_client_base()
        call_count = {"n": 0}

        def _fail_second(model, vals):
            if model == 'hr.leave':
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise RuntimeError("simulated 422 overlap error")
                return call_count["n"] + 100
            return call_count["n"] + 200

        mock_client.create.side_effect = _fail_second
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=3, avg_length_days=5,
                                           past_future_pct=0, timescale_days=365, validate_pct=0), employee_ids=[101])
        leave_ids = create_leave_data(mock_client, test_ctx)
        assert len(leave_ids) == 2, f"expected 2 successful (1 failed), got {len(leave_ids)}"
        results.append(("hr: failure isolation — one bad leave doesn't abort batch", True,
                        f"{len(leave_ids)} created, 1 skipped"))
    except Exception as e:
        results.append(("hr: failure isolation — one bad leave doesn't abort batch", False, str(e)))

    # Step 14 — get_existing_leaves pre-population: no new leave overlaps existing Odoo data
    try:
        mock_client = unittest.mock.MagicMock()
        # search_read returns leave type for get_or_create, and existing leaves for get_existing_leaves
        existing_leaves = [
            {"id": 10, "request_date_from": "2024-06-03", "request_date_to": "2024-06-07"},
            {"id": 11, "request_date_from": "2024-07-01", "request_date_to": "2024-07-05"},
        ]
        # search_read is called multiple times: first for leave type, then for existing leaves per emp
        def _search_read_side(model, domain, fields=None, limit=None):
            if model == 'hr.work.entry.type':
                return [{"id": 1, "name": "Jahresurlaub", "requires_allocation": True}]
            if model == 'hr.leave':
                return existing_leaves
            return []

        mock_client.search_read.side_effect = _search_read_side

        created_dates = []
        id_counter2 = iter(range(1, 1000))

        def _capture_new_leaves(model, vals):
            if model == 'hr.leave':
                created_dates.append((vals['date_from'][:10], vals['date_to'][:10]))
            return next(id_counter2)

        mock_client.create.side_effect = _capture_new_leaves

        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=3, avg_length_days=5,
                                           past_future_pct=0, timescale_days=365, validate_pct=0), employee_ids=[101])
        create_leave_data(mock_client, test_ctx)

        # Verify no new leave overlaps the existing ones
        blocked = [("2024-06-03", "2024-06-07"), ("2024-07-01", "2024-07-05")]
        overlap_with_existing = False
        for new_s, new_e in created_dates:
            for blk_s, blk_e in blocked:
                if new_s <= blk_e and new_e >= blk_s:
                    overlap_with_existing = True
        assert not overlap_with_existing, \
            f"New leave overlaps existing Odoo data: new={created_dates}, blocked={blocked}"
        results.append(("hr: get_existing_leaves blocks overlap with prior runs", True,
                        f"{len(created_dates)} new leaves, no overlap with 2 existing"))
    except Exception as e:
        results.append(("hr: get_existing_leaves blocks overlap with prior runs", False, str(e)))

    # ------------------------------------------------------------------
    # B17 — hr.work.entry.type.shortcut_behavior doesn't exist on saas-19.4;
    # get_or_create_annual_leave_type must not send it on create.
    # ------------------------------------------------------------------
    try:
        mock_client = unittest.mock.MagicMock()
        mock_client.search_read.return_value = []  # no existing leave type -> create() branch
        mock_client.create.return_value = 42
        get_or_create_annual_leave_type(mock_client)
        assert mock_client.create.called, "Expected create() to be called"
        sent_vals = mock_client.create.call_args[0][1]
        assert 'shortcut_behavior' not in sent_vals, (
            f"B17 regression: 'shortcut_behavior' sent again, vals={sent_vals}"
        )
        results.append(("B17: get_or_create_annual_leave_type omits shortcut_behavior", True, ""))
    except Exception as e:
        results.append(("B17: get_or_create_annual_leave_type omits shortcut_behavior", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 — Pattern 3/5: hr.leave/hr.work.entry.type ship with
    # hr_holidays/hr_work_entry, not with hr. Employees installed must not
    # imply absences installed; create_leave_data must skip gracefully
    # (no API calls at all) rather than fail loudly on every leave model.
    # ------------------------------------------------------------------
    try:
        mock_client = unittest.mock.MagicMock()
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=2, avg_length_days=5,
                                           past_future_pct=30, timescale_days=180, validate_pct=100), employee_ids=[101], installed_modules=set())
        leave_ids = create_leave_data(mock_client, test_ctx)
        assert leave_ids == [], f"expected graceful skip, got {leave_ids!r}"
        mock_client.create.assert_not_called()
        mock_client.create_batch.assert_not_called()
        mock_client.search_read.assert_not_called()
        results.append(("S10: create_leave_data skips gracefully without hr_holidays/hr_work_entry (Pattern 3)",
                        True, ""))
    except Exception as e:
        results.append(("S10: create_leave_data skips gracefully without hr_holidays/hr_work_entry (Pattern 3)",
                        False, str(e)))

    try:
        # Only one of the two missing must still block — both models are
        # needed (hr.leave.allocation via hr_holidays, hr.work.entry.type via
        # hr_work_entry).
        mock_client = unittest.mock.MagicMock()
        test_ctx = _make_ctx(TimeoffConfig(entries_per_employee=2, avg_length_days=5,
                                           past_future_pct=30, timescale_days=180, validate_pct=100), employee_ids=[101], installed_modules={"hr_holidays"})  # hr_work_entry missing
        leave_ids = create_leave_data(mock_client, test_ctx)
        assert leave_ids == [], f"expected graceful skip with only one of two installed, got {leave_ids!r}"
        mock_client.create.assert_not_called()
        results.append(("S10: create_leave_data needs BOTH hr_holidays and hr_work_entry", True, ""))
    except Exception as e:
        results.append(("S10: create_leave_data needs BOTH hr_holidays and hr_work_entry", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
