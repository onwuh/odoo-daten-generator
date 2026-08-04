"""Unit tests for pure helper functions in modules/hr.py."""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.hr import _overlaps, _next_monday, _end_friday, _random_past_monday, _random_future_monday


def run():
    """Returns (all_passed, [(label, ok, detail), ...])"""
    results = []

    # _overlaps: non-overlapping
    try:
        existing = [("2024-01-01", "2024-01-05"), ("2024-02-01", "2024-02-07")]
        assert not _overlaps("2024-01-08", "2024-01-12", existing)
        results.append(("_overlaps: non-overlapping ranges", True, ""))
    except Exception as e:
        results.append(("_overlaps: non-overlapping ranges", False, str(e)))

    # _overlaps: adjacent (touching boundary = overlap)
    try:
        existing = [("2024-01-01", "2024-01-05")]
        assert _overlaps("2024-01-05", "2024-01-10", existing)
        results.append(("_overlaps: adjacent boundary is overlap", True, ""))
    except Exception as e:
        results.append(("_overlaps: adjacent boundary is overlap", False, str(e)))

    # _overlaps: contained
    try:
        existing = [("2024-01-01", "2024-01-10")]
        assert _overlaps("2024-01-03", "2024-01-07", existing)
        results.append(("_overlaps: contained range overlaps", True, ""))
    except Exception as e:
        results.append(("_overlaps: contained range overlaps", False, str(e)))

    # _overlaps: identical
    try:
        existing = [("2024-03-01", "2024-03-05")]
        assert _overlaps("2024-03-01", "2024-03-05", existing)
        results.append(("_overlaps: identical range overlaps", True, ""))
    except Exception as e:
        results.append(("_overlaps: identical range overlaps", False, str(e)))

    # _overlaps: empty list never overlaps
    try:
        assert not _overlaps("2024-01-01", "2024-01-05", [])
        results.append(("_overlaps: empty list never overlaps", True, ""))
    except Exception as e:
        results.append(("_overlaps: empty list never overlaps", False, str(e)))

    # _next_monday: already Monday
    try:
        monday = datetime.date(2024, 4, 1)  # April 1 2024 = Monday
        assert monday.weekday() == 0
        result = _next_monday(monday)
        assert result == monday, f"Expected same day, got {result}"
        results.append(("_next_monday: Monday stays Monday", True, ""))
    except Exception as e:
        results.append(("_next_monday: Monday stays Monday", False, str(e)))

    # _next_monday: Wednesday → next Monday
    try:
        wed = datetime.date(2024, 4, 3)  # Wednesday
        result = _next_monday(wed)
        assert result == datetime.date(2024, 4, 8), f"Expected 2024-04-08, got {result}"
        assert result.weekday() == 0
        results.append(("_next_monday: Wednesday → next Monday", True, ""))
    except Exception as e:
        results.append(("_next_monday: Wednesday → next Monday", False, str(e)))

    # _next_monday: Sunday → next Monday
    try:
        sun = datetime.date(2024, 4, 7)  # Sunday
        result = _next_monday(sun)
        assert result == datetime.date(2024, 4, 8), f"Expected 2024-04-08, got {result}"
        assert result.weekday() == 0
        results.append(("_next_monday: Sunday → next Monday", True, ""))
    except Exception as e:
        results.append(("_next_monday: Sunday → next Monday", False, str(e)))

    # _end_friday: length=1 → Friday of same week
    try:
        monday = datetime.date(2024, 4, 1)
        result = _end_friday(monday, 1)
        assert result.weekday() == 4, f"Expected Friday (4), got weekday {result.weekday()}"
        assert result == datetime.date(2024, 4, 5)
        results.append(("_end_friday: length=1 → same week Friday", True, ""))
    except Exception as e:
        results.append(("_end_friday: length=1 → same week Friday", False, str(e)))

    # _end_friday: length=7 → crosses to next week, lands on Friday
    try:
        monday = datetime.date(2024, 4, 1)
        result = _end_friday(monday, 7)
        assert result.weekday() == 4, f"Expected Friday (4), got weekday {result.weekday()}"
        results.append(("_end_friday: length=7 → Friday in next week", True, ""))
    except Exception as e:
        results.append(("_end_friday: length=7 → Friday in next week", False, str(e)))

    # _end_friday: length=10 → always ends on Friday
    try:
        monday = datetime.date(2024, 4, 1)
        result = _end_friday(monday, 10)
        assert result.weekday() == 4, f"Expected Friday, got weekday {result.weekday()}"
        results.append(("_end_friday: length=10 → ends on Friday", True, ""))
    except Exception as e:
        results.append(("_end_friday: length=10 → ends on Friday", False, str(e)))

    # _random_past_monday: result is Monday, always < today
    try:
        today = datetime.date.today()
        for _ in range(20):
            result = _random_past_monday(today, 5, 180)
            assert result.weekday() == 0, f"Not Monday: {result} (weekday={result.weekday()})"
            assert result < today, f"Not in past: {result}"
        results.append(("_random_past_monday: always Monday, always < today", True, "20 samples"))
    except Exception as e:
        results.append(("_random_past_monday: always Monday, always < today", False, str(e)))

    # _random_future_monday: result is Monday, always > today
    try:
        today = datetime.date.today()
        for _ in range(20):
            result = _random_future_monday(today, 180)
            assert result.weekday() == 0, f"Not Monday: {result} (weekday={result.weekday()})"
            assert result > today, f"Not in future: {result}"
        results.append(("_random_future_monday: always Monday, always > today", True, "20 samples"))
    except Exception as e:
        results.append(("_random_future_monday: always Monday, always > today", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
