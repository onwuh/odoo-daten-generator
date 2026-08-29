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
# Vendor bills — S10/R10 (F4): layout variation so bills from different
# suppliers don't all look like they came out of the same template.
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
# Courier is a fixed-width font — noticeably wider per character than the
# other two at the same point size — so its table geometry is a separate
# profile, not a shared constant: a description column and truncation length
# calibrated for Helvetica overflows a Courier cell (roughly 90mm holds ~45
# Helvetica characters at 10pt but only ~38 Courier ones).
_TABLE_LAYOUT = {
    "Helvetica": {"widths": (90, 25, 35, 35), "desc_max": 45},
    "Times":     {"widths": (90, 25, 35, 35), "desc_max": 45},
    "Courier":   {"widths": (100, 20, 32, 33), "desc_max": 38},
}

# Five preset combinations, not a full cross product of every dimension —
# the point is "looks like five different invoicing systems", not maximum
# variety. Picked deterministically per supplier (see build_vendor_bill_pdf).
_VARIANTS = [
    {"font": "Helvetica", "header": "bold_block", "table": "full_grid",            "footer": "tax_iban"},
    {"font": "Times",     "header": "grey_band",  "table": "zebra",                "footer": "terms_bankline"},
    {"font": "Courier",   "header": "underline",  "table": "borderless_headerline", "footer": "page_number"},
    {"font": "Helvetica", "header": "right_meta", "table": "zebra",                "footer": "tax_iban"},
    {"font": "Times",     "header": "bold_block", "table": "full_grid",            "footer": "terms_bankline"},
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
    pdf.cell(0, 6, f"Datum: {bill_date or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
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
    pdf.cell(0, 6, f"Datum: {bill_date or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
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
    pdf.cell(0, 6, f"Datum: {bill_date or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
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
    pdf.cell(75, 6, f"Datum: {bill_date or ''}".strip(), align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(max(left_bottom_y, pdf.get_y()))
    pdf.ln(6)


_HEADERS = {
    "bold_block": _header_bold_block,
    "grey_band": _header_grey_band,
    "underline": _header_underline,
    "right_meta": _header_right_meta,
}


def _table_full_grid(pdf, font, widths, desc_max, lines, currency_symbol):
    pdf.set_font(font, "B", 10)
    for w, label, align in zip(widths, ("Beschreibung", "Menge", "Einzelpreis", "Gesamt"),
                               (None, "R", "R", "R")):
        pdf.cell(w, 7, label, border=1, align=align or "", new_x="RIGHT", new_y="TOP")
    pdf.ln(7)

    pdf.set_font(font, "", 10)
    total = 0.0
    if not lines:
        pdf.cell(sum(widths), 7, "Keine Positionen", border=1, new_x="LMARGIN", new_y="NEXT")
    for line in lines:
        qty = line.get("quantity") or 0
        price = line.get("price_unit") or 0
        amount = qty * price
        total += amount
        pdf.cell(widths[0], 7, str(line.get("description", ""))[:desc_max], border=1)
        pdf.cell(widths[1], 7, f"{qty:g}", border=1, align="R")
        pdf.cell(widths[2], 7, f"{price:,.2f} {currency_symbol}", border=1, align="R")
        pdf.cell(widths[3], 7, f"{amount:,.2f} {currency_symbol}", border=1, align="R",
                 new_x="LMARGIN", new_y="NEXT")
    return total


def _table_zebra(pdf, font, widths, desc_max, lines, currency_symbol):
    pdf.set_font(font, "B", 10)
    for w, label, align in zip(widths, ("Beschreibung", "Menge", "Einzelpreis", "Gesamt"),
                               (None, "R", "R", "R")):
        pdf.cell(w, 7, label, border="B", align=align or "", new_x="RIGHT", new_y="TOP")
    pdf.ln(7)

    pdf.set_font(font, "", 10)
    pdf.set_fill_color(244, 244, 244)
    total = 0.0
    if not lines:
        pdf.cell(sum(widths), 7, "Keine Positionen", new_x="LMARGIN", new_y="NEXT")
    for idx, line in enumerate(lines):
        qty = line.get("quantity") or 0
        price = line.get("price_unit") or 0
        amount = qty * price
        total += amount
        shade = idx % 2 == 1
        pdf.cell(widths[0], 7, str(line.get("description", ""))[:desc_max], fill=shade)
        pdf.cell(widths[1], 7, f"{qty:g}", align="R", fill=shade)
        pdf.cell(widths[2], 7, f"{price:,.2f} {currency_symbol}", align="R", fill=shade)
        pdf.cell(widths[3], 7, f"{amount:,.2f} {currency_symbol}", align="R", fill=shade,
                 new_x="LMARGIN", new_y="NEXT")
    return total


def _table_borderless_headerline(pdf, font, widths, desc_max, lines, currency_symbol):
    pdf.set_font(font, "B", 10)
    for w, label, align in zip(widths, ("Beschreibung", "Menge", "Einzelpreis", "Gesamt"),
                               (None, "R", "R", "R")):
        pdf.cell(w, 7, label, align=align or "", new_x="RIGHT", new_y="TOP")
    pdf.ln(7)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.l_margin + sum(widths), y)
    pdf.ln(2)

    pdf.set_font(font, "", 10)
    total = 0.0
    if not lines:
        pdf.cell(sum(widths), 7, "Keine Positionen", new_x="LMARGIN", new_y="NEXT")
    for line in lines:
        qty = line.get("quantity") or 0
        price = line.get("price_unit") or 0
        amount = qty * price
        total += amount
        pdf.cell(widths[0], 7, str(line.get("description", ""))[:desc_max])
        pdf.cell(widths[1], 7, f"{qty:g}", align="R")
        pdf.cell(widths[2], 7, f"{price:,.2f} {currency_symbol}", align="R")
        pdf.cell(widths[3], 7, f"{amount:,.2f} {currency_symbol}", align="R",
                 new_x="LMARGIN", new_y="NEXT")
    return total


_TABLES = {
    "full_grid": _table_full_grid,
    "zebra": _table_zebra,
    "borderless_headerline": _table_borderless_headerline,
}


def _footer_tax_iban(pdf, font, footer_info):
    pdf.set_font(font, "", 9)
    pdf.cell(0, 5, f"Steuernummer: {footer_info.get('tax_number', '-')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"IBAN: {footer_info.get('iban', '-')}", new_x="LMARGIN", new_y="NEXT")


def _footer_terms_bankline(pdf, font, footer_info):
    pdf.set_font(font, "", 9)
    days = footer_info.get("payment_terms_days")
    pdf.cell(0, 5, f"Zahlungsziel: {days} Tage netto" if days else "Zahlungsziel: 30 Tage netto",
            new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Bankverbindung: {footer_info.get('iban', '-')}", new_x="LMARGIN", new_y="NEXT")


def _footer_page_number(pdf, font, footer_info):
    # The page-level "Seite N" is drawn by _VendorBillPDF.footer() (enabled by
    # the caller); this still puts the one piece of per-invoice information a
    # page-number footer would otherwise have no room for.
    pdf.set_font(font, "", 9)
    pdf.cell(0, 5, f"Kundennummer: {footer_info.get('customer_number', '-')}",
            new_x="LMARGIN", new_y="NEXT")


_FOOTERS = {
    "tax_iban": _footer_tax_iban,
    "terms_bankline": _footer_terms_bankline,
    "page_number": _footer_page_number,
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
) -> bytes:
    """Renders a vendor-bill-style PDF and returns it as bytes.

    lines: list of {"description": str, "quantity": float, "price_unit": float}.
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

    footer_info: {"tax_number", "iban", "payment_terms_days", "customer_number"}
    — deterministic fake data for whichever footer fields the chosen variant
    shows (see data_factory.build_vendor_footer_info). None/missing keys fall
    back to placeholder text rather than raising, same as an empty `lines`.
    """
    idx = _variant_for(supplier_name) if variant is None else variant % len(_VARIANTS)
    spec = _VARIANTS[idx]
    footer_info = footer_info or {}

    pdf = _VendorBillPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.show_page_number = spec["footer"] == "page_number"
    pdf.add_page()

    font = spec["font"]
    _HEADERS[spec["header"]](pdf, font, supplier_name, supplier_address, bill_number, bill_date)

    layout = _TABLE_LAYOUT[font]
    total = _TABLES[spec["table"]](pdf, font, layout["widths"], layout["desc_max"], lines, currency_symbol)

    pdf.ln(4)
    pdf.set_font(font, "B", 10)
    pdf.cell(sum(layout["widths"][:-1]), 7, "Gesamtbetrag", align="R")
    pdf.cell(layout["widths"][-1], 7, f"{total:,.2f} {currency_symbol}", align="R",
            new_x="LMARGIN", new_y="NEXT")
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
