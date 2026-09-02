"""Unit tests for A2 (recruiting email/phone determinism)."""
import os
import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import text_utils
from modules.recruiting import _random_phone_de, _create_applicants, _applicant_skill_lines


def run():
    """Returns (all_passed, [(label, ok, detail), ...])"""
    results = []

    # ------------------------------------------------------------------
    # text_utils (recruiting-facing usage)
    # ------------------------------------------------------------------

    try:
        cases = {
            "Hans Müller": "hans.mueller@example.com",
            "Björn Groß": "bjoern.gross@example.com",
            "Bewerber 3": "bewerber.3@example.com",
        }
        for name, expected in cases.items():
            got = text_utils.email_from_name(name)
            assert got == expected, f"{name!r} -> {got!r}, expected {expected!r}"
        results.append(("text_utils: email_from_name umlaut cases", True, ""))
    except Exception as e:
        results.append(("text_utils: email_from_name umlaut cases", False, str(e)))

    # ------------------------------------------------------------------
    # _random_phone_de
    # ------------------------------------------------------------------

    try:
        pattern = re.compile(r"^\+49 1[5-7]\d \d{7}$")
        for _ in range(50):
            phone = _random_phone_de()
            assert pattern.match(phone), f"phone {phone!r} doesn't match expected format"
            prefix = int(phone.split(" ")[1])
            assert 150 <= prefix <= 179, f"prefix {prefix} out of range 150-179"
        results.append(("_random_phone_de: format + prefix range (n=50)", True, ""))
    except Exception as e:
        results.append(("_random_phone_de: format + prefix range (n=50)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 2 — _create_applicants with recruiting_data missing
    # candidate_emails/candidate_phones entirely, non-empty job_ids
    # ------------------------------------------------------------------

    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = []  # no stages, no skills
        recruiting_data = {"candidate_names": ["Hans Müller", "Anna Schmidt"]}  # no emails/phones keys

        created_vals = []

        def _fake_create_batch(model, values_list, context=None):
            if model == "hr.applicant":
                created_vals.extend(values_list)
            return list(range(1, len(values_list) + 1))

        mock_client.create_batch.side_effect = _fake_create_batch

        # ctx.applicant_ids must be populated (config.py RunContext field, added
        # alongside S6/P2 CV-PDF work) — a bare None ctx would no longer work
        # here since _create_applicants now writes back to it. ctx.installed_modules
        # likewise required since S11/R5 (hr_recruitment_skills gate) — all_skill_ids=[]
        # below means it never actually changes this test's outcome, but the
        # attribute access itself would still crash without it.
        ctx = SimpleNamespace(applicant_ids=[], installed_modules=set())
        returned_ids = _create_applicants(
            mock_client, ctx=ctx, recruiting_data=recruiting_data, num_candidates=2,
            job_ids=[42], all_skill_ids=[], skill_levels_map={},
        )

        assert len(created_vals) == 2, f"expected 2 applicants created, got {len(created_vals)}"
        assert created_vals[0]["email_from"] == "hans.mueller@example.com"
        assert created_vals[1]["email_from"] == "anna.schmidt@example.com"
        assert all(v["partner_phone"].startswith("+49 1") for v in created_vals)
        assert returned_ids == ctx.applicant_ids == [1, 2], (
            f"expected _create_applicants to return and store [1, 2], got returned={returned_ids}, ctx={ctx.applicant_ids}"
        )
        results.append(("Pattern 2: _create_applicants missing candidate_emails/phones -> derived, no crash", True, ""))
    except Exception as e:
        results.append(("Pattern 2: _create_applicants missing candidate_emails/phones -> derived, no crash", False, str(e)))

    # ------------------------------------------------------------------
    # S11/R5 — applicant_skill_ids ships with hr_recruitment_skills, not
    # hr_recruitment (found live 2026-09-02: fields_get on hr.applicant has
    # no skill field at all when it's uninstalled, create_batch fails loudly
    # the moment a skill line is sent). skills_supported=False must return []
    # WITHOUT even querying hr.skill/hr.skill.level — real skill_ids given.
    # ------------------------------------------------------------------
    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"id": 9, "skill_type_id": [1, "Python"]}]
        lines = _applicant_skill_lines(mock_client, skill_ids=[9], skills_supported=False)
        assert lines == [], f"expected no skill lines when unsupported, got {lines!r}"
        assert mock_client.search_read.call_count == 0, (
            "skills_supported=False must skip the hr.skill lookup entirely, "
            f"got {mock_client.search_read.call_count} calls"
        )
        results.append(("_applicant_skill_lines: skills_supported=False -> [] with no lookup", True, ""))
    except Exception as e:
        results.append(("_applicant_skill_lines: skills_supported=False -> [] with no lookup", False, str(e)))

    try:
        # Default (skills_supported=True, the pre-S11 behaviour) is unchanged.
        mock_client = MagicMock()
        mock_client.search_read.side_effect = [
            [{"id": 9, "skill_type_id": [1, "Python"]}],   # hr.skill lookup
            [{"id": 77, "level_progress": 50}],             # hr.skill.level lookup
        ]
        lines = _applicant_skill_lines(mock_client, skill_ids=[9])
        assert len(lines) == 1 and lines[0][2]["skill_id"] == 9, f"expected one skill line, got {lines!r}"
        results.append(("_applicant_skill_lines: skills_supported defaults True, unchanged behaviour", True, ""))
    except Exception as e:
        results.append(("_applicant_skill_lines: skills_supported defaults True, unchanged behaviour", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
