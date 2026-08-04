"""Unit tests for modules/project.py helpers (no Odoo connection needed)."""
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.project import _select_ordered_stages


def run():
    """Returns (all_passed, [(label, ok, detail), ...])"""
    results = []

    # ------------------------------------------------------------------
    # B6 — subset selection must preserve source (workflow) order
    # ------------------------------------------------------------------

    stages = ["Kickoff", "Planung", "Umsetzung", "Test", "Abnahme", "Deployment"]
    random.seed(42)
    try:
        for _ in range(50):
            selected = _select_ordered_stages(stages, num_stages=4)
            source_positions = [stages.index(s) for s in selected]
            assert source_positions == sorted(source_positions), (
                f"order scrambled: {selected}"
            )
        results.append((
            "_select_ordered_stages: 50 samples preserve source order", True, "seed=42",
        ))
    except AssertionError as e:
        results.append(("_select_ordered_stages: 50 samples preserve source order", False, str(e)))

    try:
        selected = _select_ordered_stages(stages, num_stages=4)
        assert len(selected) == 4
        assert len(set(selected)) == 4  # no duplicates
        results.append(("_select_ordered_stages: returns k distinct items", True, str(selected)))
    except AssertionError as e:
        results.append(("_select_ordered_stages: returns k distinct items", False, str(e)))

    try:
        short = ["Kickoff", "Abnahme"]
        selected = _select_ordered_stages(short, num_stages=4)
        assert selected == short[:4]
        results.append(("_select_ordered_stages: fewer stages than k → truncated source order", True, str(selected)))
    except AssertionError as e:
        results.append(("_select_ordered_stages: fewer stages than k → truncated source order", False, str(e)))

    # Pattern 1: empty pool guard
    try:
        selected = _select_ordered_stages([], num_stages=4)
        assert selected == []
        results.append(("_select_ordered_stages: empty pool → [] no crash", True, ""))
    except Exception as e:
        results.append(("_select_ordered_stages: empty pool → [] no crash", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
