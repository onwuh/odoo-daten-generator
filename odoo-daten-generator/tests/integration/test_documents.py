import base64
import sys
import os
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules import accounting, recruiting, documents
from config import DemoCriteria, ModuleSelections, RunContext


def _make_rctx():
    crit = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=crit, module_selections=ModuleSelections(), industry="IT",
        language_name="German", language_code="de_DE", gemini_model_name="test",
    )


def run(client, ctx):
    """
    Consumes: ctx.partner_ids, ctx.product_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    if not ctx.partner_ids or not ctx.product_ids:
        results.append(("documents: SKIP — missing partner_ids or product_ids in ctx", False, "master_data must run first"))
        return False, results

    partner_id = ctx.partner_ids[0]
    product_id = ctx.product_ids[0]

    # Setup — seed a fresh RunContext with real vendor bills (P1 prerequisite)
    # and real applicants (P2 prerequisite), same pattern test_accounting.py /
    # test_recruiting.py use to exercise their own module end-to-end.
    rctx = _make_rctx()
    rctx.company_ids = [partner_id]
    rctx.product_ids = [product_id]
    rctx.component_ids = [product_id]
    rctx.installed_modules = set()  # force standalone invoice path in accounting
    rctx.module_selections.account = 2
    rctx.module_selections.account_bills = 2
    rctx.module_selections.hr_recruitment = {
        "num_jobs": 1, "num_candidates": 2,
        "create_skills": False, "num_skill_types": 0, "skills_per_type": 0,
    }

    try:
        accounting.create_accounting_data(client, None, rctx)
        assert len(rctx.bill_ids) == 2, f"expected 2 vendor bills, got {len(rctx.bill_ids)}"
        recruiting.create_recruiting_data(client, None, rctx)
        assert len(rctx.applicant_ids) == 2, (
            f"expected 2 applicants tracked on ctx.applicant_ids, got {len(rctx.applicant_ids)} "
            f"(recruiting.py must extend ctx.applicant_ids, not discard create_batch's return value)"
        )
        results.append((
            "documents: setup — 2 vendor bills + 2 applicants created, ids tracked on ctx",
            True, f"bill_ids={rctx.bill_ids}, applicant_ids={rctx.applicant_ids}",
        ))
    except Exception as e:
        results.append(("documents: setup — 2 vendor bills + 2 applicants created, ids tracked on ctx", False, str(e)))
        return False, results

    # Step 1 — P1: bill PDFs attached to account.move, read-back (Pattern 4).
    # gemini=None throughout: P1 needs no LLM call at all (design decision 1),
    # P2 must fall back gracefully without one (Pattern 2).
    try:
        rctx.module_selections.documents = {"bill_pdfs_enabled": True, "cv_pdfs_enabled": False}
        documents.create_documents(client, None, rctx)
        attachments = client.search_read(
            'ir.attachment',
            [["res_model", "=", "account.move"], ["res_id", "in", rctx.bill_ids]],
            fields=["res_model", "res_id", "mimetype", "type", "raw"], limit=0,
        )
        assert len(attachments) == 2, f"expected 2 bill PDF attachments, got {len(attachments)}"
        for att in attachments:
            assert att["mimetype"] == "application/pdf", att
            assert att["type"] == "binary", att
            assert att["raw"], "attachment has no data"
            decoded = base64.b64decode(att["raw"])
            assert decoded.startswith(b"%PDF"), "decoded attachment is not a PDF"
        results.append((
            "documents: P1 — bill PDF attachments created + read-back (Pattern 4)",
            True, f"{len(attachments)} attachments",
        ))
    except Exception as e:
        results.append(("documents: P1 — bill PDF attachments created + read-back (Pattern 4)", False, str(e)))

    # Step 2 — P2: CV PDFs attached to hr.applicant, read-back (Pattern 4).
    try:
        rctx.module_selections.documents = {"bill_pdfs_enabled": False, "cv_pdfs_enabled": True}
        documents.create_documents(client, None, rctx)
        attachments = client.search_read(
            'ir.attachment',
            [["res_model", "=", "hr.applicant"], ["res_id", "in", rctx.applicant_ids]],
            fields=["res_model", "res_id", "mimetype", "type", "raw"], limit=0,
        )
        assert len(attachments) == 2, f"expected 2 CV PDF attachments, got {len(attachments)}"
        for att in attachments:
            assert att["mimetype"] == "application/pdf", att
            assert att["type"] == "binary", att
            assert att["raw"], "attachment has no data"
            decoded = base64.b64decode(att["raw"])
            assert decoded.startswith(b"%PDF"), "decoded attachment is not a PDF"
        results.append((
            "documents: P2 — CV PDF attachments created + read-back, gemini=None fallback (Pattern 2+4)",
            True, f"{len(attachments)} attachments",
        ))
    except Exception as e:
        results.append(("documents: P2 — CV PDF attachments created + read-back, gemini=None fallback (Pattern 2+4)", False, str(e)))

    # Step 3 — Pattern 5: empty ctx.bill_ids/applicant_ids -> graceful skip,
    # no crash, no attachment/search_read calls at all.
    try:
        mock_client = MagicMock()
        empty_rctx = _make_rctx()
        empty_rctx.module_selections.documents = {"bill_pdfs_enabled": True, "cv_pdfs_enabled": True}
        documents.create_documents(mock_client, None, empty_rctx)
        mock_client.search_read.assert_not_called()
        mock_client.create_batch.assert_not_called()
        results.append(("documents: empty bill_ids/applicant_ids -> graceful skip, no crash (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("documents: empty bill_ids/applicant_ids -> graceful skip, no crash (Pattern 5)", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
