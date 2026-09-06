import datetime
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import odoo_actions  # kept for create_employee (shared utility)
from config import DemoCriteria, ModuleSelections, RunContext
from modules.hr import (
    get_or_create_annual_leave_type,
    create_leave_allocation,
    create_leave_request,
    validate_leave_request,
    create_hr_data,
)


def _make_rctx(num_employees):
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit, module_selections=ModuleSelections(hr=num_employees), industry="IT",
        language_name="German", language_code="de_DE",
    )


def run(client, ctx):
    """
    Populates: ctx.employee_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []
    leave_type_id = None
    emp_id = None

    # Step 1 — Create employee
    try:
        emp_id = odoo_actions.create_employee(client, "Integration Test Mitarbeiter")
        assert isinstance(emp_id, int) and emp_id > 0
        rec = client.search_read(
            'hr.employee',
            [["id", "=", emp_id]],
            fields=["name"],
            limit=1,
        )
        assert rec and rec[0]["name"] == "Integration Test Mitarbeiter"
        ctx.employee_ids.append(emp_id)
        results.append(("hr: create employee + read-back name", True, emp_id))
    except Exception as e:
        results.append(("hr: create employee + read-back name", False, str(e)))

    # S10/R10: hr.work.entry.type/hr.leave/hr.leave.allocation ship with
    # hr_work_entry/hr_holidays, NOT with hr — the exact gap this sprint's F6
    # traced (get_or_create_annual_leave_type and the helpers below have no
    # ctx-based gate of their own, by design; see modules/hr.py's
    # create_leave_data for where that gate actually lives). This test file
    # calls the low-level helpers directly, so IT must skip gracefully here
    # instead of reporting a live 404 as a code failure when a target
    # instance — like the one this was first caught against — genuinely
    # doesn't have these apps installed (Pattern 5).
    leave_apps_installed = ('hr_holidays' in ctx.installed_modules
                            and 'hr_work_entry' in ctx.installed_modules)

    # Step 2 — Get or create annual leave type
    if leave_apps_installed:
        try:
            leave_type_id = get_or_create_annual_leave_type(client)
            assert isinstance(leave_type_id, int) and leave_type_id > 0
            results.append(("hr: get_or_create_annual_leave_type", True, leave_type_id))
        except Exception as e:
            results.append(("hr: get_or_create_annual_leave_type", False, str(e)))
    else:
        results.append(("hr: get_or_create_annual_leave_type SKIP — hr_holidays/hr_work_entry nicht installiert",
                        True, "skipped"))

    # Step 3 — Create leave allocation + validate
    if not leave_apps_installed:
        results.append(("hr: create_leave_allocation + validate SKIP — hr_holidays/hr_work_entry nicht installiert",
                        True, "skipped"))
    elif emp_id and leave_type_id:
        try:
            today = datetime.date.today()
            date_from = datetime.date(today.year, 1, 1)
            date_to = datetime.date(today.year, 12, 31)
            alloc_id = create_leave_allocation(client, emp_id, leave_type_id, 30, date_from, date_to)
            assert isinstance(alloc_id, int) and alloc_id > 0
            rec = client.search_read(
                'hr.leave.allocation',
                [["id", "=", alloc_id]],
                fields=["state", "number_of_days"],
                limit=1,
            )
            assert rec
            results.append(("hr: create_leave_allocation + validate", True, f"id={alloc_id} state={rec[0]['state']}"))
        except Exception as e:
            results.append(("hr: create_leave_allocation + validate", False, str(e)))
    else:
        results.append(("hr: create_leave_allocation + validate", False, "skipped - missing emp_id or leave_type_id"))

    # Step 4 — Create leave request (no auto-approve)
    if not leave_apps_installed:
        results.append(("hr: create_leave_request SKIP — hr_holidays/hr_work_entry nicht installiert",
                        True, "skipped"))
    elif emp_id and leave_type_id:
        try:
            today = datetime.date.today()
            days_to_monday = (7 - today.weekday()) % 7 or 7
            monday = today + datetime.timedelta(days=days_to_monday + 14)
            friday = monday + datetime.timedelta(days=4)
            leave_id = create_leave_request(
                client, emp_id, leave_type_id,
                f"{monday} 08:00:00", f"{friday} 17:00:00",
            )
            assert isinstance(leave_id, int) and leave_id > 0
            results.append(("hr: create_leave_request", True, f"id={leave_id} {monday}-{friday}"))
        except Exception as e:
            results.append(("hr: create_leave_request", False, str(e)))
    else:
        results.append(("hr: create_leave_request", False, "skipped - missing emp_id or leave_type_id"))

    # Step 5 — validate_leave_request
    if not leave_apps_installed:
        results.append(("hr: validate_leave_request SKIP — hr_holidays/hr_work_entry nicht installiert",
                        True, "skipped"))
    elif emp_id and leave_type_id:
        try:
            today = datetime.date.today()
            days_to_monday = (7 - today.weekday()) % 7 or 7
            monday = today + datetime.timedelta(days=days_to_monday + 28)
            friday = monday + datetime.timedelta(days=4)
            leave_id = create_leave_request(
                client, emp_id, leave_type_id,
                f"{monday} 08:00:00", f"{friday} 17:00:00",
            )
            ok = validate_leave_request(client, leave_id)
            assert ok is True
            results.append(("hr: validate_leave_request", True, f"leave_id={leave_id}"))
        except Exception as e:
            results.append(("hr: validate_leave_request", False, str(e)))
    else:
        results.append(("hr: validate_leave_request", False, "skipped"))

    # Step 6 — B5: leave allocation window must cover requests crossing into next year.
    # Deterministic reproduction: mirror create_leave_data's alloc-window formula for
    # timescale_days=400, then request a leave 200 days out (guaranteed past Dec 31
    # from any point in the year) and confirm it approves cleanly.
    if not leave_apps_installed:
        results.append(("hr: B5 — leave beyond current year approved SKIP — hr_holidays/hr_work_entry nicht installiert",
                        True, "skipped"))
    else:
        try:
            b5_emp_id = odoo_actions.create_employee(client, "Integration Test B5 Mitarbeiter")
            assert isinstance(b5_emp_id, int) and b5_emp_id > 0

            today = datetime.date.today()
            timescale_days = 400
            alloc_date_from = today - datetime.timedelta(days=timescale_days)
            alloc_date_to = today + datetime.timedelta(days=timescale_days + 14)
            create_leave_allocation(client, b5_emp_id, leave_type_id, 30, alloc_date_from, alloc_date_to)

            far_point = today + datetime.timedelta(days=200)
            days_to_monday = (7 - far_point.weekday()) % 7
            far_monday = far_point + datetime.timedelta(days=days_to_monday)
            far_friday = far_monday + datetime.timedelta(days=4)
            assert far_monday.year > today.year, (
                f"test setup error: {far_monday} did not cross into next year from {today}"
            )

            leave_id = create_leave_request(
                client, b5_emp_id, leave_type_id,
                f"{far_monday} 08:00:00", f"{far_friday} 17:00:00",
            )
            assert isinstance(leave_id, int) and leave_id > 0, "leave creation failed (allocation window too narrow?)"
            ok = validate_leave_request(client, leave_id)
            assert ok is True, "action_approve failed for leave crossing year boundary"
            results.append((
                "hr: B5 — leave beyond current year approved", True,
                f"leave_id={leave_id} {far_monday}-{far_friday}",
            ))
        except Exception as e:
            results.append(("hr: B5 — leave beyond current year approved", False, str(e)))

    # Step — D3: create_hr_data employee batch, end-to-end, read-back
    try:
        rctx = _make_rctx(num_employees=3)
        rctx.name_banks = {"employee_names": ["D3 Test Eins", "D3 Test Zwei", "D3 Test Drei"]}
        create_hr_data(client, None, rctx)
        assert len(rctx.employee_ids) == 3, f"expected 3 employees, got {len(rctx.employee_ids)}"
        recs = client.search_read(
            'hr.employee', [["id", "in", rctx.employee_ids]], fields=["name"], limit=0,
        )
        names = {r["name"] for r in recs}
        assert names == {"D3 Test Eins", "D3 Test Zwei", "D3 Test Drei"}, names
        results.append((
            "hr: create_hr_data employee batch (D3), read-back",
            True, f"{len(rctx.employee_ids)} employees",
        ))
    except Exception as e:
        results.append(("hr: create_hr_data employee batch (D3), read-back", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
