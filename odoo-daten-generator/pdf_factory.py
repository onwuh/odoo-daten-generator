"""Pure-Python PDF rendering for inbound demo documents (vendor bills, CVs).

No Odoo/network calls here — this module only turns already-known structured
data into PDF bytes. Uses fpdf2 (PyPI package name "fpdf2", import name
"fpdf") for pure-Python rendering with no system dependencies (cairo/pango),
keeping Windows compatibility.
"""

import zlib
from typing import Dict, List, Optional

from fpdf import FPDF


def _new_pdf() -> FPDF:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    return pdf


# ---------------------------------------------------------------------------
# Vendor bills — S10/R10 (F4) layout variation, extended for realism (a real
# German invoice needs a recipient block, net/tax/gross breakdown and a VAT
# ID, not just a supplier header + line-item table). The five variants below
# vary presentation only (font/header/table/footer style) — every one shows
# the same set of facts, since a "factually correct invoice" isn't optional
# in any of them.
# ---------------------------------------------------------------------------

class _VendorBillPDF(FPDF):
    """Only difference from a bare FPDF: an opt-in page-number footer, used
    by exactly one of the variants below. Everything else (build_cv_pdf
    included) keeps using the plain _new_pdf() and is unaffected."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.show_page_number = False

    def footer(self):
        if not self.show_page_number:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Seite {self.page_no()}", align="C")


# fpdf2's built-in core fonts (live-confirmed: fpdf.fonts.CORE_FONTS on the
# pinned version): helvetica/times/courier are the three usable for text.
# Column mm-widths are page geometry and don't depend on the font — only the
# character-truncation length per column does, since Courier (fixed-width) is
# noticeably wider per character than Helvetica/Times at the same point size
# (roughly 2.63mm/char vs 2.0mm/char at 10pt, live-confirmed against this
# table's own Beschreibung column).
_TABLE_WIDTHS = (9, 83, 20, 28, 14, 34)  # Pos., Beschreibung, Menge, Einzelpreis, USt%, Gesamt
_DESC_MAX = {"Helvetica": 41, "Times": 41, "Courier": 32}

# Five preset combinations, not a full cross product of every dimension —
# the point is "looks like five different invoicing systems", not maximum
# variety. Picked deterministically per supplier (see build_vendor_bill_pdf).
# Content shown is identical across all five (see module docstring above) —
# they differ only in font/header/table-border/footer-layout presentation.
_VARIANTS = [
    {"font": "Helvetica", "header": "bold_block", "table": "full_grid",            "footer": "stacked"},
    {"font": "Times",     "header": "grey_band",  "table": "zebra",                "footer": "two_col"},
    {"font": "Courier",   "header": "underline",  "table": "borderless_headerline", "footer": "boxed"},
    {"font": "Helvetica", "header": "right_meta", "table": "zebra",                "footer": "stacked"},
    {"font": "Times",     "header": "bold_block", "table": "full_grid",            "footer": "two_col"},
]


def _variant_for(supplier_name: str) -> int:
    """Deterministic per-supplier variant index.

    zlib.crc32, NOT Python's hash(): hash() is salted per-process via
    PYTHONHASHSEED, which would make "the same supplier always renders the
    same layout" true only within a single run and false across two runs of
    the same pipeline — exactly the determinism this function exists for.
    """
    return zlib.crc32((supplier_name or "").encode("utf-8")) % len(_VARIANTS)


def _header_bold_block(pdf, font, name, address, bill_number, bill_date):
    pdf.set_font(font, "B", 14)
    pdf.cell(0, 8, name or "Lieferant", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    for line in (address or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, f"Rechnung {bill_number or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, f"Rechnungsdatum: {bill_date or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)


def _header_grey_band(pdf, font, name, address, bill_number, bill_date):
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font(font, "B", 15)
    pdf.cell(0, 12, name or "Lieferant", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.ln(2)
    pdf.set_font(font, "", 10)
    for line in (address or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, f"Rechnung {bill_number or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, f"Rechnungsdatum: {bill_date or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)


def _header_underline(pdf, font, name, address, bill_number, bill_date):
    pdf.set_font(font, "B", 14)
    pdf.cell(0, 8, name or "Lieferant", new_x="LMARGIN", new_y="NEXT")
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)
    pdf.set_font(font, "", 10)
    for line in (address or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, f"Rechnung {bill_number or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, f"Rechnungsdatum: {bill_date or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)


def _header_right_meta(pdf, font, name, address, bill_number, bill_date):
    top_y = pdf.get_y()
    pdf.set_font(font, "B", 14)
    pdf.cell(110, 8, name or "Lieferant", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    for line in (address or "").splitlines():
        pdf.cell(110, 5, line, new_x="LMARGIN", new_y="NEXT")
    left_bottom_y = pdf.get_y()

    # Invoice number/date as a right-aligned block level with the name, not
    # stacked below it — the one header variant that uses horizontal space
    # instead of vertical space for the meta line.
    pdf.set_xy(pdf.l_margin + 110, top_y)
    pdf.set_font(font, "B", 11)
    pdf.cell(75, 7, f"Rechnung {bill_number or ''}".strip(), align="R", new_x="LEFT", new_y="NEXT")
    pdf.set_x(pdf.l_margin + 110)
    pdf.set_font(font, "", 10)
    pdf.cell(75, 6, f"Rechnungsdatum: {bill_date or ''}".strip(), align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(max(left_bottom_y, pdf.get_y()))
    pdf.ln(6)


_HEADERS = {
    "bold_block": _header_bold_block,
    "grey_band": _header_grey_band,
    "underline": _header_underline,
    "right_meta": _header_right_meta,
}


def _render_recipient_and_meta(pdf, font, buyer_name, buyer_address, due_date, bill_date):
    """Common to every variant: the invoice recipient's address block plus
    the two dates a German invoice needs beyond the invoice date itself —
    Leistungsdatum (service/delivery date) and the due date. Real vendor
    bills in this pipeline never carry a separate delivery date (see
    modules/documents.py), so "entspricht Rechnungsdatum" (matches the
    invoice date) is the standard, §14-UStG-compliant way to state that
    without inventing a fact the source record doesn't have.
    """
    pdf.set_font(font, "B", 11)
    pdf.cell(0, 6, buyer_name or "Kunde", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    for line in (buyer_address or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font(font, "", 9)
    service_note = "Leistungsdatum: entspricht Rechnungsdatum"
    if bill_date:
        service_note += f" ({bill_date})"
    pdf.cell(0, 5, service_note, new_x="LMARGIN", new_y="NEXT")
    if due_date:
        pdf.cell(0, 5, f"Fällig am: {due_date}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)


def _line_fields(line: dict):
    qty = line.get("quantity") or 0
    uom = line.get("uom") or "Stk."
    price_unit = line.get("price_unit") or 0
    tax_rate = line.get("tax_rate")
    amount_untaxed = line.get("amount_untaxed")
    if amount_untaxed is None:
        amount_untaxed = qty * price_unit
    return qty, uom, price_unit, tax_rate, amount_untaxed


_TABLE_HEADERS = ("Pos.", "Beschreibung", "Menge", "Einzelpreis", "USt%", "Gesamt")
_TABLE_ALIGNS = (None, None, "R", "R", "R", "R")


def _table_full_grid(pdf, font, widths, desc_max, lines, currency_symbol):
    pdf.set_font(font, "B", 10)
    for w, label, align in zip(widths, _TABLE_HEADERS, _TABLE_ALIGNS):
        pdf.cell(w, 7, label, border=1, align=align or "", new_x="RIGHT", new_y="TOP")
    pdf.ln(7)

    pdf.set_font(font, "", 10)
    total = 0.0
    if not lines:
        pdf.cell(sum(widths), 7, "Keine Positionen", border=1, new_x="LMARGIN", new_y="NEXT")
    for i, line in enumerate(lines, start=1):
        qty, uom, price_unit, tax_rate, amount_untaxed = _line_fields(line)
        total += amount_untaxed
        tax_label = f"{tax_rate:g}%" if tax_rate is not None else "-"
        pdf.cell(widths[0], 7, str(i), border=1, align="R")
        pdf.cell(widths[1], 7, str(line.get("description", ""))[:desc_max], border=1)
        pdf.cell(widths[2], 7, f"{qty:g} {uom}", border=1, align="R")
        pdf.cell(widths[3], 7, f"{price_unit:,.2f} {currency_symbol}", border=1, align="R")
        pdf.cell(widths[4], 7, tax_label, border=1, align="R")
        pdf.cell(widths[5], 7, f"{amount_untaxed:,.2f} {currency_symbol}", border=1, align="R",
                 new_x="LMARGIN", new_y="NEXT")
    return total


def _table_zebra(pdf, font, widths, desc_max, lines, currency_symbol):
    pdf.set_font(font, "B", 10)
    for w, label, align in zip(widths, _TABLE_HEADERS, _TABLE_ALIGNS):
        pdf.cell(w, 7, label, border="B", align=align or "", new_x="RIGHT", new_y="TOP")
    pdf.ln(7)

    pdf.set_font(font, "", 10)
    pdf.set_fill_color(244, 244, 244)
    total = 0.0
    if not lines:
        pdf.cell(sum(widths), 7, "Keine Positionen", new_x="LMARGIN", new_y="NEXT")
    for idx, line in enumerate(lines):
        qty, uom, price_unit, tax_rate, amount_untaxed = _line_fields(line)
        total += amount_untaxed
        tax_label = f"{tax_rate:g}%" if tax_rate is not None else "-"
        shade = idx % 2 == 1
        pdf.cell(widths[0], 7, str(idx + 1), align="R", fill=shade)
        pdf.cell(widths[1], 7, str(line.get("description", ""))[:desc_max], fill=shade)
        pdf.cell(widths[2], 7, f"{qty:g} {uom}", align="R", fill=shade)
        pdf.cell(widths[3], 7, f"{price_unit:,.2f} {currency_symbol}", align="R", fill=shade)
        pdf.cell(widths[4], 7, tax_label, align="R", fill=shade)
        pdf.cell(widths[5], 7, f"{amount_untaxed:,.2f} {currency_symbol}", align="R", fill=shade,
                 new_x="LMARGIN", new_y="NEXT")
    return total


def _table_borderless_headerline(pdf, font, widths, desc_max, lines, currency_symbol):
    pdf.set_font(font, "B", 10)
    for w, label, align in zip(widths, _TABLE_HEADERS, _TABLE_ALIGNS):
        pdf.cell(w, 7, label, align=align or "", new_x="RIGHT", new_y="TOP")
    pdf.ln(7)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.l_margin + sum(widths), y)
    pdf.ln(2)

    pdf.set_font(font, "", 10)
    total = 0.0
    if not lines:
        pdf.cell(sum(widths), 7, "Keine Positionen", new_x="LMARGIN", new_y="NEXT")
    for i, line in enumerate(lines, start=1):
        qty, uom, price_unit, tax_rate, amount_untaxed = _line_fields(line)
        total += amount_untaxed
        tax_label = f"{tax_rate:g}%" if tax_rate is not None else "-"
        pdf.cell(widths[0], 7, str(i), align="R")
        pdf.cell(widths[1], 7, str(line.get("description", ""))[:desc_max])
        pdf.cell(widths[2], 7, f"{qty:g} {uom}", align="R")
        pdf.cell(widths[3], 7, f"{price_unit:,.2f} {currency_symbol}", align="R")
        pdf.cell(widths[4], 7, tax_label, align="R")
        pdf.cell(widths[5], 7, f"{amount_untaxed:,.2f} {currency_symbol}", align="R",
                 new_x="LMARGIN", new_y="NEXT")
    return total


_TABLES = {
    "full_grid": _table_full_grid,
    "zebra": _table_zebra,
    "borderless_headerline": _table_borderless_headerline,
}


def _render_totals(pdf, font, widths, currency_symbol, totals):
    """Common to every variant: net subtotal, one line per VAT rate present
    on the bill, then the gross total — always the same authoritative
    amount_untaxed/amount_tax/amount_total the attached account.move itself
    carries (see modules/documents.py), never a value recomputed here, so
    the PDF can't disagree with the Odoo record it's attached to."""
    label_w = sum(widths[:-1])
    value_w = widths[-1]
    pdf.set_font(font, "", 10)
    pdf.cell(label_w, 7, "Zwischensumme (netto)", align="R")
    pdf.cell(value_w, 7, f"{totals.get('untaxed', 0):,.2f} {currency_symbol}", align="R",
             new_x="LMARGIN", new_y="NEXT")
    for tb in totals.get("tax_breakdown") or []:
        rate = tb.get("rate", 0)
        amount = tb.get("amount", 0)
        pdf.cell(label_w, 6, f"zzgl. USt {rate:g}%", align="R")
        pdf.cell(value_w, 6, f"{amount:,.2f} {currency_symbol}", align="R",
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font(font, "B", 11)
    pdf.cell(label_w, 8, "Rechnungsbetrag (brutto)", align="R")
    pdf.cell(value_w, 8, f"{totals.get('total', 0):,.2f} {currency_symbol}", align="R",
             new_x="LMARGIN", new_y="NEXT")


def _footer_stacked(pdf, font, footer_info):
    pdf.set_draw_color(180, 180, 180)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)
    pdf.set_font(font, "", 9)
    days = footer_info.get("payment_terms_days") or 30
    pdf.cell(0, 5, f"USt-IdNr.: {footer_info.get('tax_number', '-')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Bank: {footer_info.get('bank_name', '-')}   "
                   f"IBAN: {footer_info.get('iban', '-')}   BIC: {footer_info.get('bic', '-')}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Zahlungsbedingungen: {days} Tage netto   "
                   f"Kundennummer: {footer_info.get('customer_number', '-')}",
             new_x="LMARGIN", new_y="NEXT")


def _footer_two_col(pdf, font, footer_info):
    pdf.set_font(font, "", 9)
    left_w = 95
    top_y = pdf.get_y()
    for line in (
        f"Bank: {footer_info.get('bank_name', '-')}",
        f"IBAN: {footer_info.get('iban', '-')}",
        f"BIC: {footer_info.get('bic', '-')}",
    ):
        pdf.cell(left_w, 5, line, new_x="LMARGIN", new_y="NEXT")
    left_bottom_y = pdf.get_y()

    days = footer_info.get("payment_terms_days") or 30
    pdf.set_xy(pdf.l_margin + left_w, top_y)
    for line in (
        f"USt-IdNr.: {footer_info.get('tax_number', '-')}",
        f"Zahlungsziel: {days} Tage netto",
        f"Kundennummer: {footer_info.get('customer_number', '-')}",
    ):
        pdf.set_x(pdf.l_margin + left_w)
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(max(left_bottom_y, pdf.get_y()))


def _footer_boxed(pdf, font, footer_info):
    # The page-level "Seite N" is drawn separately by _VendorBillPDF.footer()
    # (enabled by the caller for this variant) — this is the per-invoice
    # content that goes inside the box.
    pdf.set_font(font, "", 9)
    days = footer_info.get("payment_terms_days") or 30
    x0, y0 = pdf.l_margin, pdf.get_y()
    for text in (
        f"USt-IdNr.: {footer_info.get('tax_number', '-')}",
        f"Bank: {footer_info.get('bank_name', '-')} | IBAN: {footer_info.get('iban', '-')} | "
        f"BIC: {footer_info.get('bic', '-')}",
        f"Zahlungsziel: {days} Tage netto | Kundennummer: {footer_info.get('customer_number', '-')}",
    ):
        pdf.cell(0, 5, text, new_x="LMARGIN", new_y="NEXT")
    y1 = pdf.get_y()
    pdf.set_draw_color(180, 180, 180)
    pdf.rect(x0 - 2, y0 - 2, (pdf.w - pdf.r_margin - x0) + 4, (y1 - y0) + 2, style="D")


_FOOTERS = {
    "stacked": _footer_stacked,
    "two_col": _footer_two_col,
    "boxed": _footer_boxed,
}


def build_vendor_bill_pdf(
    supplier_name: str,
    supplier_address: Optional[str],
    bill_number: Optional[str],
    bill_date: Optional[str],
    lines: List[dict],
    currency_symbol: str = "EUR",
    variant: Optional[int] = None,
    footer_info: Optional[Dict[str, object]] = None,
    buyer_name: Optional[str] = None,
    buyer_address: Optional[str] = None,
    due_date: Optional[str] = None,
    totals: Optional[Dict[str, object]] = None,
) -> bytes:
    """Renders a vendor-bill-style PDF and returns it as bytes.

    lines: list of {"description", "quantity", "uom", "price_unit",
    "tax_rate", "amount_untaxed"}. Only "description" is expected always —
    every other key degrades gracefully (missing "uom" -> "Stk.", missing
    "amount_untaxed" -> quantity*price_unit, missing "tax_rate" -> "-").
    Renders a placeholder row instead of raising when lines is empty.

    currency_symbol defaults to the "EUR" string, not "€" — fpdf2's built-in
    core fonts only support latin-1, which excludes the euro sign; a real
    Unicode font would need to be embedded (extra binary asset) just for one
    character, not worth it for a demo document.

    variant: which of the 5 layout presets to use (font/header/table/footer
    combination) — index into _VARIANTS. None (the default) derives it
    deterministically from supplier_name (see _variant_for), which is what
    every real call site should rely on: the same supplier renders the same
    layout every time, different suppliers render differently, and neither
    fact depends on the caller tracking variant numbers.

    footer_info: {"tax_number" (the supplier's USt-IdNr., despite the key
    name), "iban", "bic", "bank_name", "payment_terms_days",
    "customer_number"} — deterministic fake data for whichever supplier this
    is (see data_factory.build_vendor_footer_info). None/missing keys fall
    back to placeholder text rather than raising, same as an empty `lines`.

    buyer_name/buyer_address: the invoice recipient — real invoices always
    print a "bill to" block, and this is the one thing every previous
    version of this function omitted entirely. None falls back to a generic
    "Kunde" placeholder rather than raising.

    due_date: the payment due date shown near the top, independent of the
    "Zahlungsbedingungen: N Tage netto" wording in the footer.

    totals: {"untaxed", "tax", "total", "tax_breakdown": [{"rate", "amount"}]}
    — the authoritative net/tax/gross split, meant to be the same
    amount_untaxed/amount_tax/amount_total the underlying account.move
    itself carries so the rendered PDF can never disagree with the Odoo
    record it's attached to. None (the default, used by callers that don't
    have a real tax-aware document to render) falls back to summing the
    lines' own amounts with no VAT breakdown shown.
    """
    idx = _variant_for(supplier_name) if variant is None else variant % len(_VARIANTS)
    spec = _VARIANTS[idx]
    footer_info = footer_info or {}

    pdf = _VendorBillPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.show_page_number = spec["footer"] == "boxed"
    pdf.add_page()

    font = spec["font"]
    _HEADERS[spec["header"]](pdf, font, supplier_name, supplier_address, bill_number, bill_date)
    _render_recipient_and_meta(pdf, font, buyer_name, buyer_address, due_date, bill_date)

    desc_max = _DESC_MAX[font]
    line_total = _TABLES[spec["table"]](pdf, font, _TABLE_WIDTHS, desc_max, lines, currency_symbol)

    if totals is None:
        totals = {"untaxed": line_total, "tax": 0.0, "total": line_total, "tax_breakdown": []}

    pdf.ln(3)
    # Don't let the totals block get orphaned alone on a new page once the
    # extra recipient/meta/VAT content pushes a long line-item table close
    # to the bottom margin.
    if pdf.get_y() > pdf.h - pdf.b_margin - 30:
        pdf.add_page()
    _render_totals(pdf, font, _TABLE_WIDTHS, currency_symbol, totals)
    pdf.ln(6)

    _FOOTERS[spec["footer"]](pdf, font, footer_info)

    return bytes(pdf.output())


def build_cv_pdf(
    applicant_name: str,
    contact_info: Optional[str],
    skills: List[str],
    career_bullets: List[str],
) -> bytes:
    """Renders a simple CV-style PDF and returns it as bytes.

    Renders a placeholder instead of raising when skills/career_bullets is empty.
    """
    pdf = _new_pdf()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, applicant_name or "Bewerber", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for line in (contact_info or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Kompetenzen", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, ", ".join(skills) if skills else "-", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Werdegang", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for bullet in (career_bullets or ["-"]):
        # multi_cell defaults to new_x=RIGHT (cursor stays at the page's right
        # edge) — without an explicit LMARGIN reset, the *next* multi_cell call
        # in this loop computes zero remaining width and raises FPDFException.
        pdf.multi_cell(0, 6, f"- {bullet}", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
