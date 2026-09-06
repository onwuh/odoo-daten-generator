"""Unit tests for modules/documents.py and pdf_factory.py (S6/R1 P1+P2).

Patterns covered: 1 (empty-pool guard in pdf_factory), 2 (LLM None/gemini=None
guard -> fallback bullets), 3 (empty documents dict -> no ir.attachment
create_batch call), 6 (Many2one [id, name] tuple unpacking for partner_id/
product_id/skill_id), 8 (fetch_cv_bullet_points_batch called exactly once,
not per applicant).
"""
import os
import sys
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import data_factory
import pdf_factory
from config import DemoCriteria, DocumentsConfig, ModuleSelections, RunContext
from fallback_data import FALLBACK_CV_BULLETS
from modules import documents


def _make_ctx(documents_sel=None, bill_ids=None, applicant_ids=None, model_access=None):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=0,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=0, num_consumables=0, num_storables=0,
    )
    ctx = RunContext(
        criteria=criteria,
        module_selections=ModuleSelections(documents=documents_sel),
        industry="IT", language_name="German", language_code="de",
        model_access=model_access if model_access is not None else {},
    )
    ctx.bill_ids = bill_ids or []
    ctx.applicant_ids = applicant_ids or []
    return ctx


def _mock_client():
    client = MagicMock()
    counter = {"n": 9000}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    client.create_batch.side_effect = _create_batch
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # Pattern 1: pdf_factory must not raise on empty lines/skills/bullets,
    # and must still return a well-formed (non-empty, PDF-header) byte string.
    # ------------------------------------------------------------------
    try:
        empty_bill = pdf_factory.build_vendor_bill_pdf("Lieferant", "", None, None, [])
        empty_cv = pdf_factory.build_cv_pdf("Bewerber", "", [], [])
        for label, b in [("bill", empty_bill), ("cv", empty_cv)]:
            assert isinstance(b, (bytes, bytearray)), f"{label}: not bytes"
            assert bytes(b).startswith(b"%PDF"), f"{label}: missing PDF header"
            assert len(b) > 0, f"{label}: empty output"
        results.append(("pdf_factory: empty lines/skills/bullets -> no crash, valid PDF bytes (Pattern 1)", True, ""))
    except Exception as e:
        results.append(("pdf_factory: empty lines/skills/bullets -> no crash, valid PDF bytes (Pattern 1)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 3: empty ModuleSelections.documents dict -> create_documents
    # is a full no-op, even with non-empty bill_ids/applicant_ids.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(documents_sel=None, bill_ids=[1], applicant_ids=[2])
        documents.create_documents(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        client.search_read.assert_not_called()
        results.append(("create_documents: empty documents dict -> no calls at all (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("create_documents: empty documents dict -> no calls at all (Pattern 3)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 6: account.move.partner_id and account.move.line.product_id
    # come back as [id, name] tuples — _create_bill_pdfs must unpack them
    # (supplier name resolved via the partner lookup, line description
    # falls back to the product's display name when line.name is blank)
    # rather than passing the raw tuple through to pdf_factory.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'account.move':
                return [{
                    "id": 501, "name": "BILL/001", "ref": "BILL/001",
                    "invoice_date": "2026-08-01",
                    "partner_id": [77, "Acme Lieferant GmbH"],
                    "invoice_line_ids": [901],
                }]
            if model == 'res.partner':
                return [{"id": 77, "name": "Acme Lieferant GmbH", "street": "Teststr. 1", "zip": "12345", "city": "Teststadt"}]
            if model == 'account.move.line':
                return [{
                    "id": 901, "name": False, "quantity": 2, "price_unit": 15.0,
                    "product_id": [55, "Testprodukt XY"],
                }]
            return []
        client.search_read.side_effect = _search_read

        with patch.object(pdf_factory, "build_vendor_bill_pdf", return_value=b"%PDF-fake") as mock_build:
            ctx = _make_ctx(documents_sel=DocumentsConfig(bill_pdfs_enabled=True), bill_ids=[501])
            documents.create_documents(client, gemini=None, ctx=ctx)

        assert mock_build.call_count == 1, mock_build.call_count
        call_kwargs = mock_build.call_args
        supplier_name_arg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("supplier_name")
        lines_arg = call_kwargs.args[4] if len(call_kwargs.args) > 4 else call_kwargs.kwargs.get("lines")
        assert supplier_name_arg == "Acme Lieferant GmbH", f"partner_id tuple not unpacked: {supplier_name_arg!r}"
        assert isinstance(lines_arg, list) and len(lines_arg) == 1, lines_arg
        assert lines_arg[0]["description"] == "Testprodukt XY", (
            f"product_id tuple not unpacked for line fallback description: {lines_arg[0]!r}"
        )
        assert isinstance(lines_arg[0]["description"], str), "description must be a string, not the raw tuple"

        attachment_calls = [c for c in client.create_batch.call_args_list if c.args[0] == 'ir.attachment']
        assert len(attachment_calls) == 1, attachment_calls
        vals = attachment_calls[0].args[1][0]
        assert vals["res_model"] == "account.move" and vals["res_id"] == 501
        assert vals["mimetype"] == "application/pdf"
        # "datas" is not a real ir.attachment field on this Odoo version — it
        # silently no-ops on create instead of raising (live-verified). "raw"
        # is the field that actually persists file content.
        assert "raw" in vals, "attachment vals must use 'raw', not 'datas' — see CLAUDE.md gotcha"
        assert "datas" not in vals

        results.append((
            "_create_bill_pdfs: partner_id/product_id [id,name] tuples unpacked correctly (Pattern 6)",
            True, f"supplier={supplier_name_arg!r}, description={lines_arg[0]['description']!r}",
        ))
    except AssertionError as e:
        results.append(("_create_bill_pdfs: partner_id/product_id [id,name] tuples unpacked correctly (Pattern 6)", False, str(e)))

    # ------------------------------------------------------------------
    # Recipient block + net/tax/gross totals: the PDF's "bill to" is this
    # run's own res.company, and its total must be the account.move's own
    # amount_untaxed/amount_tax/amount_total — never a value recomputed from
    # the lines — so the rendered PDF can't disagree with the Odoo record.
    # Also covers the fallback synthetic address for a blank company record
    # (the common case on a freshly provisioned demo SaaS tenant).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'account.move':
                return [{
                    "id": 501, "name": "BILL/001", "invoice_date": "2026-08-01",
                    "invoice_date_due": "2026-08-15", "currency_id": [1, "EUR"],
                    "amount_untaxed": 100.0, "amount_tax": 19.0, "amount_total": 119.0,
                    "partner_id": [77, "Acme Lieferant GmbH"],
                    "invoice_line_ids": [901],
                }]
            if model == 'res.partner':
                return [{"id": 77, "name": "Acme Lieferant GmbH", "street": "Teststr. 1",
                         "zip": "12345", "city": "Teststadt"}]
            if model == 'account.move.line':
                return [{
                    "id": 901, "name": "Beratung", "quantity": 2, "price_unit": 50.0,
                    "price_subtotal": 100.0, "price_total": 119.0,
                    "product_id": [55, "Beratungsleistung"], "product_uom_id": [1, "Stunden"],
                    "tax_ids": [25],
                }]
            if model == 'account.tax':
                return [{"id": 25, "amount": 19.0}]
            if model == 'res.company':
                return []  # blank address -> must fall back to a synthetic one
            return []
        client.search_read.side_effect = _search_read

        with patch.object(pdf_factory, "build_vendor_bill_pdf", return_value=b"%PDF-fake") as mock_build:
            ctx = _make_ctx(documents_sel=DocumentsConfig(bill_pdfs_enabled=True), bill_ids=[501])
            documents.create_documents(client, gemini=None, ctx=ctx)

        assert mock_build.call_count == 1, mock_build.call_count
        kwargs = mock_build.call_args.kwargs
        assert kwargs.get("buyer_name") == "Kunde", (
            f"res.company returned nothing -> buyer_name should fall back to 'Kunde': {kwargs.get('buyer_name')!r}"
        )
        assert kwargs.get("buyer_address"), "blank company address must still fall back to a synthetic one, not empty"
        assert kwargs.get("due_date") == "2026-08-15", kwargs.get("due_date")
        totals = kwargs.get("totals")
        assert totals == {
            "untaxed": 100.0, "tax": 19.0, "total": 119.0,
            "tax_breakdown": [{"rate": 19.0, "base": 100.0, "amount": 19.0}],
        }, totals
        lines_arg = mock_build.call_args.args[4]
        assert lines_arg[0]["tax_rate"] == 19.0, lines_arg
        assert lines_arg[0]["uom"] == "Stunden", lines_arg
        assert lines_arg[0]["amount_untaxed"] == 100.0, lines_arg

        results.append((
            "_create_bill_pdfs: recipient block + authoritative net/tax/gross totals passed through",
            True, f"totals={totals}",
        ))
    except AssertionError as e:
        results.append(("_create_bill_pdfs: recipient block + authoritative net/tax/gross totals passed through",
                        False, str(e)))

    # ------------------------------------------------------------------
    # The converse of the above: a real, populated res.company address must
    # be used as-is, not overridden by the synthetic fallback.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'account.move':
                return [{
                    "id": 502, "name": "BILL/002", "invoice_date": "2026-08-01",
                    "invoice_date_due": "2026-08-01", "currency_id": [1, "EUR"],
                    "amount_untaxed": 10.0, "amount_tax": 0.0, "amount_total": 10.0,
                    "partner_id": [77, "Acme Lieferant GmbH"], "invoice_line_ids": [],
                }]
            if model == 'res.partner':
                return [{"id": 77, "name": "Acme Lieferant GmbH", "street": "", "zip": "", "city": ""}]
            if model == 'res.company':
                return [{"id": 1, "name": "Meine Firma GmbH", "street": "Hauptstr. 5", "street2": False,
                         "zip": "10115", "city": "Berlin", "country_id": [57, "Germany"], "vat": "DE123456789"}]
            return []
        client.search_read.side_effect = _search_read

        with patch.object(pdf_factory, "build_vendor_bill_pdf", return_value=b"%PDF-fake") as mock_build:
            ctx = _make_ctx(documents_sel=DocumentsConfig(bill_pdfs_enabled=True), bill_ids=[502])
            documents.create_documents(client, gemini=None, ctx=ctx)

        kwargs = mock_build.call_args.kwargs
        assert kwargs.get("buyer_name") == "Meine Firma GmbH", kwargs.get("buyer_name")
        assert kwargs.get("buyer_address") == "Hauptstr. 5\n10115 Berlin\nGermany", (
            f"real company address must be used verbatim: {kwargs.get('buyer_address')!r}"
        )
        results.append(("_create_bill_pdfs: real res.company address used as-is, no synthetic fallback",
                        True, kwargs.get("buyer_address")))
    except AssertionError as e:
        results.append(("_create_bill_pdfs: real res.company address used as-is, no synthetic fallback",
                        False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 6 (continued) + Pattern 8: hr.applicant.skill.skill_id comes
    # back as [id, name] — must unpack to the skill name, not the tuple —
    # and fetch_cv_bullet_points_batch must be called exactly once across
    # N applicants, never once per applicant.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'hr.applicant':
                return [
                    {"id": 601, "partner_name": "Max Mustermann", "email_from": "max@x.example",
                     "partner_phone": "+49 1", "applicant_skill_ids": [1001]},
                    {"id": 602, "partner_name": "Erika Beispiel", "email_from": "erika@x.example",
                     "partner_phone": "+49 2", "applicant_skill_ids": [1002]},
                ]
            if model == 'hr.applicant.skill':
                return [
                    {"id": 1001, "skill_id": [55, "Python"]},
                    {"id": 1002, "skill_id": [56, "Odoo"]},
                ]
            return []
        client.search_read.side_effect = _search_read

        gemini = MagicMock()
        gemini.fetch_cv_bullet_points_batch.return_value = {
            601: ["Bullet A", "Bullet B"], 602: ["Bullet C"],
        }

        with patch.object(pdf_factory, "build_cv_pdf", return_value=b"%PDF-fake") as mock_build_cv:
            ctx = _make_ctx(documents_sel=DocumentsConfig(cv_pdfs_enabled=True), applicant_ids=[601, 602])
            documents.create_documents(client, gemini=gemini, ctx=ctx)

        assert gemini.fetch_cv_bullet_points_batch.call_count == 1, (
            f"expected exactly 1 batched LLM call for 2 applicants, got {gemini.fetch_cv_bullet_points_batch.call_count}"
        )
        assert mock_build_cv.call_count == 2, mock_build_cv.call_count
        skills_arg_601 = mock_build_cv.call_args_list[0].args[2]
        assert skills_arg_601 == ["Python"], f"skill_id tuple not unpacked: {skills_arg_601!r}"

        attachment_calls = [c for c in client.create_batch.call_args_list if c.args[0] == 'ir.attachment']
        assert len(attachment_calls) == 1 and len(attachment_calls[0].args[1]) == 2

        results.append((
            "_create_cv_pdfs: skill_id tuples unpacked + fetch_cv_bullet_points_batch called once (Pattern 6+8)",
            True, f"skills={skills_arg_601}",
        ))
    except AssertionError as e:
        results.append(("_create_cv_pdfs: skill_id tuples unpacked + fetch_cv_bullet_points_batch called once (Pattern 6+8)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 2: gemini=None and gemini returning None/{} must not crash —
    # fallback bullets from fallback_data.FALLBACK_CV_BULLETS are used.
    # ------------------------------------------------------------------
    try:
        client = _mock_client()

        def _search_read(model, domain=None, fields=None, limit=None, **kw):
            if model == 'hr.applicant':
                return [{"id": 701, "partner_name": "Fallback Bewerber", "email_from": "f@x.example",
                         "partner_phone": "+49 9", "applicant_skill_ids": []}]
            return []
        client.search_read.side_effect = _search_read

        captured = {}

        def _fake_build_cv(name, contact_info, skills, career_bullets):
            captured["bullets"] = career_bullets
            return b"%PDF-fake"

        with patch.object(pdf_factory, "build_cv_pdf", side_effect=_fake_build_cv):
            ctx = _make_ctx(documents_sel=DocumentsConfig(cv_pdfs_enabled=True), applicant_ids=[701])
            # gemini=None entirely (unconfigured LLM service)
            documents.create_documents(client, gemini=None, ctx=ctx)
        assert captured["bullets"] == FALLBACK_CV_BULLETS, captured["bullets"]

        gemini_empty = MagicMock()
        gemini_empty.fetch_cv_bullet_points_batch.return_value = None
        with patch.object(pdf_factory, "build_cv_pdf", side_effect=_fake_build_cv):
            ctx2 = _make_ctx(documents_sel=DocumentsConfig(cv_pdfs_enabled=True), applicant_ids=[701])
            documents.create_documents(client, gemini=gemini_empty, ctx=ctx2)
        assert captured["bullets"] == FALLBACK_CV_BULLETS, captured["bullets"]

        results.append((
            "_create_cv_pdfs: gemini=None and gemini returning None -> fallback bullets, no crash (Pattern 2)",
            True, "",
        ))
    except (AssertionError, Exception) as e:
        results.append(("_create_cv_pdfs: gemini=None and gemini returning None -> fallback bullets, no crash (Pattern 2)", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 (Pattern 3): model_access={'ir.attachment': False} must block
    # create_documents entirely — "documents" is a pseudo-module with no
    # ir.module.module entry (orchestrator.py hardcodes is_installed=True),
    # so this write-access probe is its only real precondition. Must also
    # mark ctx.skipped_modules so the progress row can read "übersprungen"
    # instead of "fertig" (web/jobs.py has no other way to tell the two apart,
    # since on_done(ok=True) already fired by the time this matters).
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(
            documents_sel=DocumentsConfig(bill_pdfs_enabled=True, cv_pdfs_enabled=True),
            bill_ids=[1], applicant_ids=[2],
            model_access={"ir.attachment": False},
        )
        documents.create_documents(client, gemini=None, ctx=ctx)
        client.create_batch.assert_not_called()
        client.search_read.assert_not_called()
        assert "documents" in ctx.skipped_modules, ctx.skipped_modules
        results.append(("create_documents: model_access blocks ir.attachment -> no calls, marked skipped (Pattern 3)",
                        True, ""))
    except AssertionError as e:
        results.append(("create_documents: model_access blocks ir.attachment -> no calls, marked skipped (Pattern 3)",
                        False, str(e)))

    try:
        # The converse — an EMPTY model_access (never probed) must default
        # open (B1 guard), same as everywhere else this dict is read.
        client = _mock_client()
        ctx = _make_ctx(documents_sel=DocumentsConfig(bill_pdfs_enabled=False, cv_pdfs_enabled=False),
                        model_access={})
        documents.create_documents(client, gemini=None, ctx=ctx)
        assert "documents" not in ctx.skipped_modules, ctx.skipped_modules
        results.append(("create_documents: empty model_access defaults open, not marked skipped (B1 guard)", True, ""))
    except AssertionError as e:
        results.append(("create_documents: empty model_access defaults open, not marked skipped (B1 guard)", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 (F4): every vendor-bill layout variant renders valid, non-empty
    # PDF bytes — the point of the sprint's "%PDF"/multi_cell-crash class of
    # bug is that a variant can look fine in one case and raise in another
    # (a font whose column width doesn't fit, a footer field that KeyErrors).
    # ------------------------------------------------------------------
    try:
        for idx in range(len(pdf_factory._VARIANTS)):
            pdf_bytes = pdf_factory.build_vendor_bill_pdf(
                "Variantentest GmbH", "Teststr. 1\n12345 Teststadt", "R-1", "2026-01-01",
                [{"description": "Ein ziemlich langes Beispiel für eine Positionsbeschreibung",
                  "quantity": 2, "price_unit": 19.5}],
                variant=idx,
                footer_info=data_factory.build_vendor_footer_info("Variantentest GmbH"),
            )
            assert isinstance(pdf_bytes, (bytes, bytearray)), f"variant {idx}: not bytes"
            assert bytes(pdf_bytes).startswith(b"%PDF"), f"variant {idx}: missing PDF header"
            assert len(pdf_bytes) > 0, f"variant {idx}: empty output"
        results.append(("pdf_factory: every vendor-bill variant renders valid, non-empty PDF bytes (F4)",
                        True, f"{len(pdf_factory._VARIANTS)} Varianten"))
    except AssertionError as e:
        results.append(("pdf_factory: every vendor-bill variant renders valid, non-empty PDF bytes (F4)", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 (F4): same supplier -> same variant, every time — not
    # random.seed() based (see pdf_factory._variant_for's own docstring for
    # why: a global reseed would contaminate every later random draw in the
    # same process, e.g. this module's own CV-PDF generation).
    # ------------------------------------------------------------------
    try:
        name = "Wiederholbarkeits GmbH"
        first = pdf_factory._variant_for(name)
        for _ in range(5):
            assert pdf_factory._variant_for(name) == first, "variant selection is not deterministic"
        # Different names may (and, across a big enough sample, will) land on
        # different variants — not asserted as inequality here since a
        # collision for any TWO specific names is possible, just unlikely.
        results.append(("pdf_factory: same supplier -> same variant, repeatably (F4)", True, f"variant={first}"))
    except AssertionError as e:
        results.append(("pdf_factory: same supplier -> same variant, repeatably (F4)", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 (F4): the module-global `random` sequence must survive a
    # vendor-bill render untouched — the exact cross-contamination bug a
    # random.seed()-based implementation would introduce.
    # ------------------------------------------------------------------
    try:
        import random
        random.seed(12345)
        before = [random.random() for _ in range(5)]
        random.seed(12345)
        pdf_factory.build_vendor_bill_pdf(
            "Seeding Test AG", "", "R-2", "2026-01-01", [],
            footer_info=data_factory.build_vendor_footer_info("Seeding Test AG"),
        )
        after = [random.random() for _ in range(5)]
        assert before == after, (
            f"build_vendor_bill_pdf perturbed the global random sequence: {before} != {after}"
        )
        results.append(("pdf_factory: rendering a bill does not perturb the global random sequence (F4)", True, ""))
    except AssertionError as e:
        results.append(("pdf_factory: rendering a bill does not perturb the global random sequence (F4)", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 (F4): data_factory.build_vendor_footer_info is itself
    # deterministic per name and does not raise on an empty/None name.
    # ------------------------------------------------------------------
    try:
        info1 = data_factory.build_vendor_footer_info("Determinismus GmbH")
        info2 = data_factory.build_vendor_footer_info("Determinismus GmbH")
        assert info1 == info2, (info1, info2)
        assert set(info1.keys()) == {
            "tax_number", "iban", "bic", "bank_name", "payment_terms_days", "customer_number",
            "skonto_percent", "skonto_days",
        }, info1
        empty = data_factory.build_vendor_footer_info("")
        assert set(empty.keys()) == set(info1.keys()), empty
        results.append(("data_factory.build_vendor_footer_info: deterministic, no crash on empty name (F4)",
                        True, f"{info1}"))
    except AssertionError as e:
        results.append(("data_factory.build_vendor_footer_info: deterministic, no crash on empty name (F4)",
                        False, str(e)))

    # ------------------------------------------------------------------
    # data_factory.build_recipient_fallback_address: same determinism
    # contract as build_vendor_footer_info (same name -> same address,
    # empty name doesn't crash) — used when the run's own res.company has
    # no address configured (see _create_bill_pdfs).
    # ------------------------------------------------------------------
    try:
        addr1 = data_factory.build_recipient_fallback_address("Meine Firma GmbH")
        addr2 = data_factory.build_recipient_fallback_address("Meine Firma GmbH")
        assert addr1 == addr2, (addr1, addr2)
        assert set(addr1.keys()) == {"street", "zip", "city"}, addr1
        empty = data_factory.build_recipient_fallback_address("")
        assert set(empty.keys()) == set(addr1.keys()), empty
        results.append(("data_factory.build_recipient_fallback_address: deterministic, no crash on empty name",
                        True, f"{addr1}"))
    except AssertionError as e:
        results.append(("data_factory.build_recipient_fallback_address: deterministic, no crash on empty name",
                        False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
