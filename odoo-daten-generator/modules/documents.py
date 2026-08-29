"""Documents module: PDF generation for inbound documents (R1/P1+P2).

P1 — vendor-bill PDFs attached to already-created account.move records.
P2 — applicant CV PDFs attached to already-created hr.applicant records.

No new LLM call for P1: the bill's supplier/line data already exists in Odoo
(created by modules/accounting.py) — this module reads it back and renders it.
P2 makes exactly one new batched LLM call for career bullet points; applicant
name/skills come from already-created hr.applicant/hr.applicant.skill records.

Runs last in the pipeline (orchestrator.py module_order), after both
accounting (ctx.bill_ids) and hr_recruitment (ctx.applicant_ids).

ir.attachment binary content field (verified live, saas-19.4): use "raw", not
"datas" — "datas" does not exist as an ir.attachment field on this version at
all (search_read raises "Invalid field 'datas'"), yet create() silently
accepts and drops it instead of raising (no error, no content stored). The
"db_datas" field does exist but writing to it directly is also silently
dropped (attachments are filestore-backed here, not DB-column-backed) — "raw"
is the only field that round-trips actual file content on create/read.
"""

import base64
import logging

import pdf_factory
from config import RunContext
from fallback_data import FALLBACK_CV_BULLETS

logger = logging.getLogger(__name__)


def _unwrap(val):
    """Many2one fields come back as [id, name] — unwrap to just the id."""
    if isinstance(val, (list, tuple)) and val:
        return val[0]
    return val


def _create_bill_pdfs(client, ctx: RunContext) -> None:
    if not ctx.module_selections.documents.get('bill_pdfs_enabled'):
        return
    if not ctx.bill_ids:
        logger.info("-> Keine Eingangsrechnungen vorhanden — PDF-Rechnungen übersprungen")
        return

    logger.info("\n--- DOCUMENTS: Erstelle PDF-Rechnungen für Eingangsrechnungen ---")
    bills = client.search_read(
        'account.move', [["id", "in", ctx.bill_ids]],
        fields=["id", "name", "ref", "invoice_date", "partner_id", "invoice_line_ids"],
        limit=0,
    )
    if not bills:
        return

    partner_ids = [_unwrap(b.get("partner_id")) for b in bills if b.get("partner_id")]
    partners = {}
    if partner_ids:
        partner_recs = client.search_read(
            'res.partner', [["id", "in", partner_ids]],
            fields=["id", "name", "street", "zip", "city"], limit=0,
        )
        partners = {r["id"]: r for r in partner_recs}

    # invoice_line_ids is a One2many — comes back as a plain list of ids,
    # unlike the Many2one fields above (no unwrap needed).
    all_line_ids = [lid for b in bills for lid in (b.get("invoice_line_ids") or [])]
    lines_by_id = {}
    if all_line_ids:
        line_recs = client.search_read(
            'account.move.line', [["id", "in", all_line_ids]],
            fields=["id", "name", "quantity", "price_unit", "product_id"], limit=0,
        )
        lines_by_id = {r["id"]: r for r in line_recs}

    attachment_vals_list = []
    for bill in bills:
        partner = partners.get(_unwrap(bill.get("partner_id")), {})
        supplier_name = partner.get("name") or "Lieferant"
        address_parts = [
            p for p in [
                partner.get("street"),
                f"{partner.get('zip') or ''} {partner.get('city') or ''}".strip(),
            ] if p
        ]
        supplier_address = "\n".join(address_parts)

        pdf_lines = []
        for line_id in bill.get("invoice_line_ids") or []:
            line = lines_by_id.get(line_id)
            if not line:
                continue
            product = line.get("product_id")
            product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else None
            description = line.get("name") or product_name or "Position"
            pdf_lines.append({
                "description": description,
                "quantity": line.get("quantity") or 0,
                "price_unit": line.get("price_unit") or 0,
            })

        pdf_bytes = pdf_factory.build_vendor_bill_pdf(
            supplier_name, supplier_address,
            bill.get("name") or bill.get("ref"), bill.get("invoice_date"),
            pdf_lines,
        )
        attachment_vals_list.append({
            "name": f"Rechnung_{bill.get('name') or bill['id']}.pdf",
            "res_model": "account.move",
            "res_id": bill["id"],
            "raw": base64.b64encode(pdf_bytes).decode("ascii"),
            "mimetype": "application/pdf",
            "type": "binary",
        })

    if attachment_vals_list:
        client.create_batch('ir.attachment', attachment_vals_list)
        logger.info(f"✅ {len(attachment_vals_list)} PDF-Rechnungen angehängt.")


def _create_cv_pdfs(client, gemini, ctx: RunContext) -> None:
    if not ctx.module_selections.documents.get('cv_pdfs_enabled'):
        return
    if not ctx.applicant_ids:
        logger.info("-> Keine Bewerber vorhanden — CV-PDFs übersprungen")
        return

    logger.info("\n--- DOCUMENTS: Erstelle CV-PDFs für Bewerber ---")
    applicants = client.search_read(
        'hr.applicant', [["id", "in", ctx.applicant_ids]],
        fields=["id", "partner_name", "email_from", "partner_phone", "applicant_skill_ids"],
        limit=0,
    )
    if not applicants:
        return

    # applicant_skill_ids is a One2many — plain list of ids, no unwrap needed.
    all_skill_line_ids = [sid for a in applicants for sid in (a.get("applicant_skill_ids") or [])]
    skill_name_by_line_id = {}
    if all_skill_line_ids:
        skill_lines = client.search_read(
            'hr.applicant.skill', [["id", "in", all_skill_line_ids]],
            fields=["id", "skill_id"], limit=0,
        )
        for sl in skill_lines:
            skill_val = sl.get("skill_id")
            if isinstance(skill_val, (list, tuple)) and len(skill_val) > 1:
                skill_name_by_line_id[sl["id"]] = skill_val[1]

    applicant_skills = {}
    applicants_for_llm = []
    for a in applicants:
        skills = [
            skill_name_by_line_id[lid] for lid in (a.get("applicant_skill_ids") or [])
            if lid in skill_name_by_line_id
        ]
        applicant_skills[a["id"]] = skills
        name = a.get("partner_name") or f"Bewerber {a['id']}"
        applicants_for_llm.append({"id": a["id"], "name": name, "skills": skills})

    bullets_by_id = {}
    if gemini:
        bullets_by_id = gemini.fetch_cv_bullet_points_batch(
            applicants_for_llm, ctx.industry, ctx.language_name
        ) or {}

    attachment_vals_list = []
    for a in applicants:
        applicant_id = a["id"]
        name = a.get("partner_name") or f"Bewerber {applicant_id}"
        contact_info = "\n".join(l for l in [a.get("email_from"), a.get("partner_phone")] if l)
        bullets = bullets_by_id.get(applicant_id) or FALLBACK_CV_BULLETS
        pdf_bytes = pdf_factory.build_cv_pdf(name, contact_info, applicant_skills.get(applicant_id, []), bullets)
        attachment_vals_list.append({
            "name": f"CV_{name}.pdf",
            "res_model": "hr.applicant",
            "res_id": applicant_id,
            "raw": base64.b64encode(pdf_bytes).decode("ascii"),
            "mimetype": "application/pdf",
            "type": "binary",
        })

    if attachment_vals_list:
        client.create_batch('ir.attachment', attachment_vals_list)
        logger.info(f"✅ {len(attachment_vals_list)} CV-PDFs angehängt.")


def create_documents(client, gemini, ctx: RunContext) -> None:
    """Creates PDF attachments for vendor bills (P1) and applicant CVs (P2).

    Each stage is independently guarded and wrapped so a failure in one
    cannot suppress the other.
    """
    doc_config = ctx.module_selections.documents
    if not isinstance(doc_config, dict) or not doc_config:
        return

    # documents is a pseudo-module (orchestrator.py hardcodes is_installed=True
    # for it, see the R10 comment there) with no ir.module.module entry to gate
    # on — ir.attachment write access is the only real precondition. Default
    # True: a model missing from ctx.model_access was never probed and must
    # not be treated as blocked (B1 error class).
    if not ctx.model_access.get('ir.attachment', True):
        logger.warning("⚠️  Dokumente übersprungen — keine Schreibrechte auf ir.attachment.")
        ctx.skipped_modules.add("documents")
        return

    try:
        _create_bill_pdfs(client, ctx)
    except Exception as e:
        logger.warning(f"⚠️  PDF-Rechnungen fehlgeschlagen: {e} — CV-PDFs werden trotzdem versucht.")

    try:
        _create_cv_pdfs(client, gemini, ctx)
    except Exception as e:
        logger.warning(f"⚠️  CV-PDFs fehlgeschlagen: {e}")
