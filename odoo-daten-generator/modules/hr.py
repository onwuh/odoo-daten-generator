"""HR module: creates employee records, leave allocations, and vacation entries."""

import logging
import datetime
import random

import odoo_actions  # kept for create_employee (shared with project module)
from config import RunContext
from fallback_data import FALLBACK_EMPLOYEES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level HR leave helpers (previously in odoo_actions.py)
# ---------------------------------------------------------------------------

def get_or_create_annual_leave_type(client):
    results = client.search_read(
        'hr.work.entry.type',
        [['requires_allocation', '=', True]],
        fields=['id', 'name'],
        limit=1,
    )
    if results:
        logger.info(f"-> Using existing leave type: {results[0]['name']} (ID: {results[0]['id']})")
        return results[0]['id']
    logger.info("-> Creating annual leave type")
    return client.create('hr.work.entry.type', {
        'name': 'Jahresurlaub',
        'code': 'JURL',
        'count_as': 'leave',
        'shortcut_behavior': 'add',
        'requires_allocation': True,
        'employee_requests': True,
        'request_unit': 'day',
        'unit_of_measure': 'day',
        'leave_validation_type': 'manager',
    })


def create_leave_allocation(client, employee_id, work_entry_type_id, days, date_from, date_to):
    """date_from/date_to (date objects) must cover the full window leave requests
    will be scattered across — a fixed calendar year misses requests that land
    in a following year when timescale_days pushes far enough into the future (B5)."""
    logger.info(f"-> Creating leave allocation: {days} days for emp {employee_id} ({date_from} – {date_to})")
    alloc_id = client.create('hr.leave.allocation', {
        'name': f'Urlaub {date_from.year}-{date_to.year}' if date_from.year != date_to.year else f'Urlaub {date_from.year}',
        'employee_id': employee_id,
        'work_entry_type_id': work_entry_type_id,
        'number_of_days': days,
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
    })
    try:
        client.call_method('hr.leave.allocation', 'action_approve', ids=[alloc_id])
    except Exception as e:
        logger.warning(f"[leave_allocation] approve failed for {alloc_id}: {e}")
    return alloc_id


def create_leave_request(client, employee_id, work_entry_type_id, date_from_str, date_to_str):
    logger.info(f"-> Creating leave request for emp {employee_id}: {date_from_str} – {date_to_str}")
    # request_date_from/to are the date-only fields Odoo uses for overlap checks.
    # If omitted, Odoo defaults them to today, causing spurious overlap errors.
    request_date_from = date_from_str[:10]
    request_date_to = date_to_str[:10]
    try:
        leave_id = client.create('hr.leave', {
            'employee_id': employee_id,
            'work_entry_type_id': work_entry_type_id,
            'date_from': date_from_str,
            'date_to': date_to_str,
            'request_date_from': request_date_from,
            'request_date_to': request_date_to,
            'name': 'Urlaub',
        })
        return leave_id
    except Exception as e:
        logger.warning(f"[leave_request] create failed for emp {employee_id}: {e}")
        return None


def get_existing_leaves(client, emp_id: int) -> list:
    """Return list of (date_from, date_to) tuples for existing non-refused leaves."""
    try:
        records = client.search_read(
            'hr.leave',
            [["employee_id", "=", emp_id], ["state", "!=", "refuse"]],
            fields=["request_date_from", "request_date_to"],
            limit=0,
        )
        result = []
        for r in records:
            df = r.get("request_date_from")
            dt = r.get("request_date_to")
            if df and dt:
                result.append((
                    datetime.date.fromisoformat(df),
                    datetime.date.fromisoformat(dt),
                ))
        logger.info(f"   [timeoff] emp {emp_id}: {len(result)} existing leaves loaded")
        return result
    except Exception as e:
        logger.warning(f"[get_existing_leaves] failed for emp {emp_id}: {e}")
        return []


def validate_leave_request(client, leave_id: int) -> bool:
    """Call action_approve on a single hr.leave record.

    Some hr.work.entry.type configs (leave_validation_type e.g. 'both') auto-validate
    hr.leave on create for this API user — action_approve on an already-validated
    record raises UserError("You cannot approve this leave.") (verified live,
    saas-19.4). Check state first instead of calling unconditionally.
    """
    try:
        rec = client.search_read('hr.leave', [['id', '=', leave_id]], fields=['state'], limit=1)
        if rec and rec[0].get('state') == 'validate':
            return True
        client.call_method('hr.leave', 'action_approve', ids=[leave_id])
        return True
    except Exception as e:
        logger.warning(f"[leave_request] approve failed for {leave_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

def create_hr_data(client, gemini, ctx: RunContext) -> None:
    """Creates employees and stores their IDs in ctx.employee_ids."""
    num_employees = ctx.module_selections.hr
    if num_employees <= 0:
        return

    logger.info("\n--- EMPLOYEES: Erstelle Mitarbeiter ---")
    employee_names = ctx.name_banks.get('employee_names', []) or FALLBACK_EMPLOYEES

    employee_vals_list = [
        {"name": employee_names[i % len(employee_names)]}
        for i in range(num_employees)
    ]
    ctx.employee_ids.extend(client.create_batch('hr.employee', employee_vals_list))

    logger.info(f"✅ {len(ctx.employee_ids)} Mitarbeiter erstellt.")

    try:
        create_leave_data(client, ctx)
    except Exception as e:
        logger.warning(f"⚠️  Urlaubsdaten fehlgeschlagen: {e} — wird übersprungen.")


def create_leave_data(client, ctx: RunContext) -> list:
    """Creates annual leave allocations and vacation entries for employees.

    Behaviour is driven by ctx.module_selections.hr_timeoff:
        enabled (bool)            – skip entirely when False
        entries_per_employee (int)– exact number of hr.leave records per employee
        avg_length_days (int)     – average leave duration; actual ±50%, min 1
        past_future_pct (int 0-100) – % of requests placed in the future
        timescale_days (int)      – window size (days) for both past and future
        validate_pct (int 0-100)  – % of created leaves to auto-approve

    Returns list of created hr.leave IDs.
    """
    to_params = ctx.module_selections.hr_timeoff
    if not to_params.get("enabled"):
        return []
    if not ctx.employee_ids:
        return []

    entries_per_employee = int(to_params.get("entries_per_employee", 2))
    avg_length_days = int(to_params.get("avg_length_days", 5))
    past_future_pct = int(to_params.get("past_future_pct", 30))   # % future
    timescale_days = int(to_params.get("timescale_days", 180))
    validate_pct = int(to_params.get("validate_pct", 100))

    logger.info("\n--- TIMEOFF: Erstelle Urlaubsdaten ---")
    leave_type_id = get_or_create_annual_leave_type(client)
    today = datetime.date.today()

    # Allocation window must cover the full scatter window (past AND future),
    # not a fixed calendar year — timescale_days can push future leaves into
    # the following year(s), and a Jan1-Dec31 allocation would miss those (B5).
    alloc_date_from = today - datetime.timedelta(days=timescale_days)
    alloc_date_to = today + datetime.timedelta(days=timescale_days + 14)

    # Allocate enough days to cover all planned leave
    alloc_days = max(entries_per_employee * avg_length_days * 2, 30)
    for emp_id in ctx.employee_ids:
        create_leave_allocation(client, emp_id, leave_type_id, alloc_days, alloc_date_from, alloc_date_to)
    all_leave_ids = []
    scheduled: dict = {}  # emp_id -> [(start, end), ...]

    for emp_id in ctx.employee_ids:
        n_future = round(entries_per_employee * past_future_pct / 100)
        n_past = entries_per_employee - n_future
        scheduled.setdefault(emp_id, [])
        existing = get_existing_leaves(client, emp_id)
        scheduled[emp_id].extend(existing)

        for _ in range(n_past):
            length = max(1, round(avg_length_days * random.uniform(0.5, 1.5)))
            start = end = None
            for _attempt in range(10):
                candidate = _random_past_monday(today, avg_length_days, timescale_days)
                candidate_end = _end_friday(candidate, length)
                if not _overlaps(candidate, candidate_end, scheduled[emp_id]):
                    start, end = candidate, candidate_end
                    break
            if start is None:
                logger.info(f"[timeoff] No non-overlapping past slot for emp {emp_id}, skipping")
                continue
            try:
                leave_id = create_leave_request(
                    client, emp_id, leave_type_id,
                    f"{start} 08:00:00", f"{end} 17:00:00",
                )
            except Exception as e:
                logger.warning(f"[timeoff] leave creation error emp {emp_id}: {e}")
                leave_id = None
            scheduled[emp_id].append((start, end))
            if leave_id:
                all_leave_ids.append(leave_id)

        for _ in range(n_future):
            length = max(1, round(avg_length_days * random.uniform(0.5, 1.5)))
            start = end = None
            for _attempt in range(10):
                candidate = _random_future_monday(today, timescale_days)
                candidate_end = _end_friday(candidate, length)
                if not _overlaps(candidate, candidate_end, scheduled[emp_id]):
                    start, end = candidate, candidate_end
                    break
            if start is None:
                logger.info(f"[timeoff] No non-overlapping future slot for emp {emp_id}, skipping")
                continue
            try:
                leave_id = create_leave_request(
                    client, emp_id, leave_type_id,
                    f"{start} 08:00:00", f"{end} 17:00:00",
                )
            except Exception as e:
                logger.warning(f"[timeoff] leave creation error emp {emp_id}: {e}")
                leave_id = None
            scheduled[emp_id].append((start, end))
            if leave_id:
                all_leave_ids.append(leave_id)

    # Approve validate_pct% of created leaves
    if all_leave_ids and validate_pct > 0:
        if validate_pct >= 100:
            to_validate = all_leave_ids
        else:
            n = max(1, round(len(all_leave_ids) * validate_pct / 100))
            to_validate = random.sample(all_leave_ids, n)
        for lid in to_validate:
            validate_leave_request(client, lid)

    logger.info(f"✅ Urlaubsdaten fuer {len(ctx.employee_ids)} Mitarbeiter erstellt ({len(all_leave_ids)} Eintraege).")
    return all_leave_ids


def _overlaps(candidate_start, candidate_end, existing: list) -> bool:
    """Return True if (candidate_start, candidate_end) overlaps any range in existing."""
    for s, e in existing:
        if candidate_start <= e and candidate_end >= s:
            return True
    return False


def _next_monday(d: datetime.date) -> datetime.date:
    """Return d if it is Monday, otherwise the next Monday."""
    days_ahead = (7 - d.weekday()) % 7
    return d + datetime.timedelta(days=days_ahead)


def _end_friday(start: datetime.date, length_days: int) -> datetime.date:
    """Return Friday of the week that contains start + length_days - 1."""
    end_approx = start + datetime.timedelta(days=length_days - 1)
    days_to_friday = (4 - end_approx.weekday()) % 7
    return end_approx + datetime.timedelta(days=days_to_friday)


def _random_past_monday(today: datetime.date, avg_length_days: int, timescale_days: int) -> datetime.date:
    """Random Monday within [-timescale_days, -avg_length_days] from today."""
    window_near = max(avg_length_days, 8)
    window_far = max(timescale_days, window_near + 1)
    offset = random.randint(window_near, window_far)
    return _next_monday(today - datetime.timedelta(days=offset))


def _random_future_monday(today: datetime.date, timescale_days: int) -> datetime.date:
    """Random Monday within [1, timescale_days] days from today."""
    offset = random.randint(1, max(1, timescale_days))
    return _next_monday(today + datetime.timedelta(days=offset))
