import datetime
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import odoo_actions  # kept for create_employee (shared utility)
from modules.hr import (
    get_or_create_annual_leave_type,
    create_leave_allocation,
    create_leave_request,
    validate_leave_request,
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

    # Step 2 — Get or create annual leave type
    try:
        leave_type_id = get_or_create_annual_leave_type(client)
        assert isinstance(leave_type_id, int) and leave_type_id > 0
        results.append(("hr: get_or_create_annual_leave_type", True, leave_type_id))
    except Exception as e:
        results.append(("hr: get_or_create_annual_leave_type", False, str(e)))

    # Step 3 — Create leave allocation + validate
    if emp_id and leave_type_id:
        try:
            year = datetime.date.today().year
            alloc_id = create_leave_allocation(client, emp_id, leave_type_id, 30, year)
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
    if emp_id and leave_type_id:
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
    if emp_id and leave_type_id:
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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
