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
# Vendor bills — modeled on real invoice templates (corporate ERP export,
# freelancer letter-style, bold branded caps layout, generic SaaS export,
# legacy mainframe dump) so a demo bill looks like it came from one of five
# different real invoicing systems. No logos (can't embed one meaningfully
# without a real brand), no custom fonts (fpdf2 core fonts only — Helvetica/
# Times/Courier), but font pairing + accent color + structure carries the
# "different software" impression on its own.
#
# All five renderers consume the exact same content — recipient block,
# Leistungsdatum/Fälligkeit, line items with Pos./Menge/Einzelpreis/USt%/
# Gesamt, net -> per-rate VAT -> gross totals, and a footer with USt-IdNr./
# IBAN/BIC/bank/payment terms/customer number. They differ only in how that
# content is laid out and styled, never in what facts they show — a missing
# VAT ID or IBAN on one variant would make that invoice invalid.
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


_VARIANTS = ["corporate_grid", "letter_freelance", "bold_branded", "grey_zebra", "courier_erp"]


def _variant_for(supplier_name: str) -> int:
    """Deterministic per-supplier variant index.

    zlib.crc32, NOT Python's hash(): hash() is salted per-process via
    PYTHONHASHSEED, which would make "the same supplier always renders the
    same layout" true only within a single run and false across two runs of
    the same pipeline — exactly the determinism this function exists for.
    """
    return zlib.crc32((supplier_name or "").encode("utf-8")) % len(_VARIANTS)


def _money(amount: float, currency_symbol: str) -> str:
    return f"{amount:,.2f} {currency_symbol}"


def _first_line(text: Optional[str]) -> str:
    if not text:
        return ""
    return text.splitlines()[0]


def _normalize_lines(lines: List[dict]) -> List[dict]:
    """description/uom/tax_rate/amount_untaxed all degrade gracefully:
    missing "uom" -> "Stk.", missing "amount_untaxed" -> quantity*price_unit,
    missing "tax_rate" -> None (rendered as "-")."""
    normalized = []
    for i, line in enumerate(lines, start=1):
        qty = line.get("quantity") or 0
        price_unit = line.get("price_unit") or 0
        amount_untaxed = line.get("amount_untaxed")
        if amount_untaxed is None:
            amount_untaxed = qty * price_unit
        normalized.append({
            "pos": i,
            "description": str(line.get("description") or ""),
            "qty": qty,
            "uom": line.get("uom") or "Stk.",
            "price_unit": price_unit,
            "tax_rate": line.get("tax_rate"),
            "amount_untaxed": amount_untaxed,
        })
    return normalized


def _fallback_totals(lines: List[dict]) -> dict:
    total = sum(l["amount_untaxed"] for l in lines)
    return {"untaxed": total, "tax": 0.0, "total": total, "tax_breakdown": []}


def _avoid_orphan(pdf, needed_mm: float = 35) -> None:
    """Force a page break before a block that must not be split — used
    before the totals block and before a letter's closing paragraph, so a
    long line-item table can't leave just the total (or just the
    signature) stranded alone on the next page."""
    if pdf.get_y() > pdf.h - pdf.b_margin - needed_mm:
        pdf.add_page()


# ---------------------------------------------------------------- variant 0
# Corporate ERP export (Helvetica, teal accent): dense two-column header
# (recipient left, Rechnungs-Nr./Datum/Fällig/Kundennummer/Leistungsdatum
# grid right), bordered+tinted-header line table, boxed double-rule totals,
# one dense legal/bank footer line including a Skonto (early-payment
# discount) clause — the level of detail a real accounting-software export
# carries.
_ACCENT_CORPORATE = (25, 92, 87)
_ACCENT_CORPORATE_TINT = (223, 236, 235)


def _render_corporate_grid(pdf, d: dict) -> None:
    font = "Helvetica"
    pdf.set_font(font, "", 8)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 4, f"{d['supplier_name']}  ·  {_first_line(d['supplier_address'])}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    top_y = pdf.get_y()
    left_w = 95
    pdf.set_font(font, "B", 11)
    pdf.cell(left_w, 6, d["buyer_name"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    for line in (d["buyer_address"] or "").splitlines():
        pdf.cell(left_w, 5, line, new_x="LMARGIN", new_y="NEXT")
    left_bottom = pdf.get_y()

    meta_rows = [
        ("Rechnungs-Nr.:", d["bill_number"] or "-"),
        ("Rechnungsdatum:", d["bill_date"] or "-"),
        ("Fällig am:", d["due_date"] or "-"),
        ("Kundennummer:", d["footer_info"].get("customer_number", "-")),
        ("Leistungsdatum:", "entspricht Rechnungsdatum"),
    ]
    label_w = 38
    for i, (label, value) in enumerate(meta_rows):
        pdf.set_xy(pdf.l_margin + left_w, top_y + i * 5)
        pdf.set_font(font, "B", 9)
        pdf.cell(label_w, 5, label)
        pdf.set_font(font, "", 9)
        pdf.cell(0, 5, str(value))
    pdf.set_y(max(left_bottom, top_y + len(meta_rows) * 5))
    pdf.ln(6)

    pdf.set_text_color(*_ACCENT_CORPORATE)
    pdf.set_font(font, "B", 16)
    pdf.cell(0, 9, "Rechnung", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_ACCENT_CORPORATE)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    widths = (9, 83, 20, 27, 14, 34)  # sums to 187 of ~190mm usable
    desc_max = 41
    pdf.set_font(font, "B", 9.5)
    pdf.set_fill_color(*_ACCENT_CORPORATE_TINT)
    for w, label, align in zip(widths, ("Pos.", "Beschreibung", "Menge", "Einzelpreis", "USt%", "Gesamt"),
                               (None, None, "R", "R", "R", "R")):
        pdf.cell(w, 7, label, border=1, align=align or "", fill=True, new_x="RIGHT", new_y="TOP")
    pdf.ln(7)
    pdf.set_font(font, "", 9.5)
    if not d["lines"]:
        pdf.cell(sum(widths), 7, "Keine Positionen", border=1, new_x="LMARGIN", new_y="NEXT")
    for l in d["lines"]:
        tax_label = f"{l['tax_rate']:g}%" if l["tax_rate"] is not None else "-"
        pdf.cell(widths[0], 7, str(l["pos"]), border=1, align="R")
        pdf.cell(widths[1], 7, l["description"][:desc_max], border=1)
        pdf.cell(widths[2], 7, f"{l['qty']:g} {l['uom']}", border=1, align="R")
        pdf.cell(widths[3], 7, _money(l["price_unit"], d["currency_symbol"]), border=1, align="R")
        pdf.cell(widths[4], 7, tax_label, border=1, align="R")
        pdf.cell(widths[5], 7, _money(l["amount_untaxed"], d["currency_symbol"]), border=1, align="R",
                 new_x="LMARGIN", new_y="NEXT")

    _avoid_orphan(pdf)
    pdf.ln(4)
    label_w2 = sum(widths[:-1])
    value_w = widths[-1]
    pdf.set_draw_color(0, 0, 0)
    y = pdf.get_y()
    pdf.line(pdf.l_margin + label_w2, y, pdf.w - pdf.r_margin, y)
    pdf.set_font(font, "", 10)
    pdf.cell(label_w2, 6, "Summe Netto", align="R")
    pdf.cell(value_w, 6, _money(d["totals"]["untaxed"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    for tb in d["totals"]["tax_breakdown"] or []:
        base = tb.get("base")
        label = (f"{tb['rate']:g}% USt auf {_money(base, d['currency_symbol'])}"
                 if base is not None else f"zzgl. USt {tb['rate']:g}%")
        pdf.cell(label_w2, 6, label, align="R")
        pdf.cell(value_w, 6, _money(tb["amount"], d["currency_symbol"]), align="R",
                 new_x="LMARGIN", new_y="NEXT")
    y = pdf.get_y()
    pdf.line(pdf.l_margin + label_w2, y, pdf.w - pdf.r_margin, y)
    pdf.ln(0.6)
    y2 = pdf.get_y()
    pdf.line(pdf.l_margin + label_w2, y2, pdf.w - pdf.r_margin, y2)
    pdf.ln(1.5)
    pdf.set_font(font, "B", 11)
    pdf.set_text_color(*_ACCENT_CORPORATE)
    pdf.cell(label_w2, 8, "Endsumme", align="R")
    pdf.cell(value_w, 8, _money(d["totals"]["total"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    fi = d["footer_info"]
    days = fi.get("payment_terms_days") or 30
    skonto_pct = fi.get("skonto_percent")
    skonto_days = fi.get("skonto_days")
    terms = (f"{skonto_pct:g}% Skonto bei Zahlung innerhalb {skonto_days} Tagen, {days} Tage netto ohne Abzug"
             if skonto_pct else f"{days} Tage netto")
    y = pdf.get_y()
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(2)
    pdf.set_font(font, "", 7.5)
    pdf.set_text_color(110, 110, 110)
    footer_text = (
        f"{d['supplier_name']} · Bank: {fi.get('bank_name', '-')} · IBAN: {fi.get('iban', '-')} · "
        f"BIC: {fi.get('bic', '-')} · Zahlungsbedingungen: {terms} · "
        f"Kundennummer: {fi.get('customer_number', '-')} · USt-IdNr.: {fi.get('tax_number', '-')}"
    )
    pdf.multi_cell(0, 4, footer_text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


# ---------------------------------------------------------------- variant 1
# Freelancer/agency letter style (Times, monochrome): a real business
# letter rather than a bare invoice form — salutation, a plain (borderless,
# dotted-rule) line-item list, a closing payment request and signature.
def _render_letter_freelance(pdf, d: dict) -> None:
    font = "Times"
    pdf.set_font(font, "", 8)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 4, f"{d['supplier_name']}, {_first_line(d['supplier_address'])}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    top_y = pdf.get_y()
    pdf.set_font(font, "", 11)
    pdf.cell(110, 6, d["buyer_name"], new_x="LMARGIN", new_y="NEXT")
    for line in (d["buyer_address"] or "").splitlines():
        pdf.cell(110, 5, line, new_x="LMARGIN", new_y="NEXT")
    left_bottom = pdf.get_y()

    meta_rows = [
        ("Rechnungsnummer:", d["bill_number"] or "-"),
        ("Datum:", d["bill_date"] or "-"),
        ("Kundennummer:", d["footer_info"].get("customer_number", "-")),
        ("Fällig am:", d["due_date"] or "-"),
    ]
    pdf.set_font(font, "", 9.5)
    for i, (label, value) in enumerate(meta_rows):
        pdf.set_xy(pdf.l_margin + 110, top_y + i * 5)
        pdf.cell(38, 5, label)
        pdf.cell(0, 5, str(value), align="R")
    pdf.set_y(max(left_bottom, top_y + len(meta_rows) * 5))
    pdf.ln(8)

    pdf.set_font(font, "B", 13)
    heading = f"Rechnung Nr. {d['bill_number']}" if d["bill_number"] else "Rechnung"
    pdf.cell(0, 8, heading, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font(font, "", 10.5)
    pdf.cell(0, 5.5, "Sehr geehrte Damen und Herren,", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.multi_cell(0, 5.5,
        "vereinbarungsgemäß erlauben wir uns, Ihnen folgende Leistungen in Rechnung zu stellen:",
        new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    widths = (95, 25, 30, 15, 25)  # sums to 190
    pdf.set_font(font, "B", 9.5)
    for w, label, align in zip(widths, ("Beschreibung", "Menge", "Einzelpreis", "USt%", "Gesamt"),
                               (None, "R", "R", "R", "R")):
        pdf.cell(w, 6, label, align=align or "")
    pdf.ln(6)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(2)

    pdf.set_font(font, "", 10)
    desc_max = 47
    if not d["lines"]:
        pdf.cell(sum(widths), 6, "Keine Positionen", new_x="LMARGIN", new_y="NEXT")
    for l in d["lines"]:
        tax_label = f"{l['tax_rate']:g}%" if l["tax_rate"] is not None else "-"
        pdf.cell(widths[0], 6, l["description"][:desc_max])
        pdf.cell(widths[1], 6, f"{l['qty']:g} {l['uom']}", align="R")
        pdf.cell(widths[2], 6, _money(l["price_unit"], d["currency_symbol"]), align="R")
        pdf.cell(widths[3], 6, tax_label, align="R")
        pdf.cell(widths[4], 6, _money(l["amount_untaxed"], d["currency_symbol"]), align="R",
                 new_x="LMARGIN", new_y="NEXT")
        row_bottom = pdf.get_y()
        pdf.set_draw_color(200, 200, 200)
        pdf.dashed_line(pdf.l_margin, row_bottom, pdf.w - pdf.r_margin, row_bottom,
                        dash_length=0.8, space_length=1)

    _avoid_orphan(pdf)
    pdf.ln(4)
    label_w = sum(widths[:-1])
    value_w = widths[-1]
    pdf.set_font(font, "", 10)
    pdf.cell(label_w, 6, "Nettobetrag", align="R")
    pdf.cell(value_w, 6, _money(d["totals"]["untaxed"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    for tb in d["totals"]["tax_breakdown"] or []:
        pdf.cell(label_w, 6, f"zzgl. {tb['rate']:g}% MwSt.", align="R")
        pdf.cell(value_w, 6, _money(tb["amount"], d["currency_symbol"]), align="R",
                 new_x="LMARGIN", new_y="NEXT")
    y = pdf.get_y()
    pdf.set_draw_color(0, 0, 0)
    pdf.line(pdf.l_margin + label_w, y, pdf.w - pdf.r_margin, y)
    pdf.set_font(font, "B", 11)
    pdf.cell(label_w, 7, "Gesamtbetrag", align="R")
    pdf.cell(value_w, 7, _money(d["totals"]["total"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    _avoid_orphan(pdf, needed_mm=45)
    pdf.set_font(font, "", 10.5)
    pdf.multi_cell(0, 5.5,
        f"Bitte begleichen Sie den Gesamtbetrag von {_money(d['totals']['total'], d['currency_symbol'])} "
        f"bis zum {d['due_date'] or d['bill_date'] or '-'} auf das unten genannte Bankkonto.",
        new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.multi_cell(0, 5.5, "Bei Rückfragen stehen wir Ihnen jederzeit gerne zur Verfügung.",
                  new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.cell(0, 5.5, "Mit freundlichen Grüßen", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5.5, d["supplier_name"] or "", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    _avoid_orphan(pdf, needed_mm=15)
    y = pdf.get_y()
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(2)
    fi = d["footer_info"]
    pdf.set_font(font, "", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 4,
        f"Bank: {fi.get('bank_name', '-')}   IBAN: {fi.get('iban', '-')}   "
        f"BIC: {fi.get('bic', '-')}   USt-IdNr.: {fi.get('tax_number', '-')}",
        new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


# ---------------------------------------------------------------- variant 2
# Bold branded caps style (Helvetica, maroon accent): big caps "RECHNUNG"
# heading, colored caps section labels instead of a plain footer, a colored
# rule under the table header instead of full borders.
_ACCENT_BOLD = (150, 30, 34)


def _render_bold_branded(pdf, d: dict) -> None:
    font = "Helvetica"
    pdf.set_font(font, "B", 9)
    pdf.cell(0, 5, f"{d['supplier_name']} | {_first_line(d['supplier_address'])}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_text_color(*_ACCENT_BOLD)
    pdf.set_font(font, "B", 22)
    pdf.cell(0, 12, "RECHNUNG", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    top_y = pdf.get_y()
    pdf.set_text_color(*_ACCENT_BOLD)
    pdf.set_font(font, "B", 8.5)
    pdf.cell(90, 4.5, "RECHNUNGSADRESSE", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(font, "", 10)
    pdf.cell(90, 5, d["buyer_name"], new_x="LMARGIN", new_y="NEXT")
    for line in (d["buyer_address"] or "").splitlines():
        pdf.cell(90, 5, line, new_x="LMARGIN", new_y="NEXT")
    left_bottom = pdf.get_y()

    meta_rows = [
        ("RECHNUNG NR.", d["bill_number"] or "-"),
        ("RECHNUNGSDATUM", d["bill_date"] or "-"),
        ("KUNDEN-NR.", d["footer_info"].get("customer_number", "-")),
        ("FÄLLIG AM", d["due_date"] or "-"),
        ("LEISTUNGSDATUM", d["bill_date"] or "-"),
    ]
    for i, (label, value) in enumerate(meta_rows):
        pdf.set_xy(pdf.l_margin + 100, top_y + i * 5.5)
        pdf.set_text_color(*_ACCENT_BOLD)
        pdf.set_font(font, "B", 8.5)
        pdf.cell(45, 5, label)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font(font, "", 9.5)
        pdf.cell(0, 5, str(value), align="R")
    pdf.set_y(max(left_bottom, top_y + len(meta_rows) * 5.5))
    pdf.ln(7)

    widths = (10, 82, 20, 25, 13, 40)  # sums to 190
    pdf.set_text_color(*_ACCENT_BOLD)
    pdf.set_font(font, "B", 9)
    for w, label, align in zip(widths, ("POS", "BESCHREIBUNG", "MENGE", "EINZELPREIS", "USt%", "GESAMTPREIS"),
                               (None, None, "R", "R", "R", "R")):
        pdf.cell(w, 6, label, align=align or "")
    pdf.ln(6)
    y = pdf.get_y()
    pdf.set_draw_color(*_ACCENT_BOLD)
    pdf.set_line_width(0.7)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_line_width(0.2)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    pdf.set_font(font, "", 10)
    desc_max = 41
    if not d["lines"]:
        pdf.cell(sum(widths), 6, "Keine Positionen", new_x="LMARGIN", new_y="NEXT")
    for l in d["lines"]:
        tax_label = f"{l['tax_rate']:g}%" if l["tax_rate"] is not None else "-"
        pdf.cell(widths[0], 7, str(l["pos"]), align="R")
        pdf.cell(widths[1], 7, l["description"][:desc_max])
        pdf.cell(widths[2], 7, f"{l['qty']:g} {l['uom']}", align="R")
        pdf.cell(widths[3], 7, _money(l["price_unit"], d["currency_symbol"]), align="R")
        pdf.cell(widths[4], 7, tax_label, align="R")
        pdf.cell(widths[5], 7, _money(l["amount_untaxed"], d["currency_symbol"]), align="R",
                 new_x="LMARGIN", new_y="NEXT")

    _avoid_orphan(pdf)
    pdf.ln(4)
    label_w = sum(widths[:-1])
    value_w = widths[-1]
    pdf.set_font(font, "", 10)
    pdf.cell(label_w, 6, "Summe Netto", align="R")
    pdf.cell(value_w, 6, _money(d["totals"]["untaxed"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    for tb in d["totals"]["tax_breakdown"] or []:
        pdf.cell(label_w, 6, f"MwSt. {tb['rate']:g}%", align="R")
        pdf.cell(value_w, 6, _money(tb["amount"], d["currency_symbol"]), align="R",
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font(font, "B", 13)
    pdf.set_text_color(*_ACCENT_BOLD)
    pdf.cell(label_w, 8, "BETRAG", align="R")
    pdf.cell(value_w, 8, _money(d["totals"]["total"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(10)

    _avoid_orphan(pdf, needed_mm=45)
    fi = d["footer_info"]
    days = fi.get("payment_terms_days") or 30
    pdf.set_font(font, "B", 10.5)
    pdf.set_text_color(*_ACCENT_BOLD)
    pdf.cell(0, 6, "ZAHLUNGSZIEL", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(font, "", 9.5)
    pdf.multi_cell(0, 5,
        f"Bitte begleichen Sie den Rechnungsbetrag innerhalb von {days} Tagen ohne Abzug auf das "
        f"unten genannte Bankkonto.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font(font, "B", 10.5)
    pdf.set_text_color(*_ACCENT_BOLD)
    pdf.cell(0, 6, "BANKVERBINDUNG", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(font, "", 9.5)
    pdf.cell(0, 5, f"{fi.get('bank_name', '-')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"IBAN: {fi.get('iban', '-')}   BIC: {fi.get('bic', '-')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"USt-IdNr.: {fi.get('tax_number', '-')}", new_x="LMARGIN", new_y="NEXT")


# ---------------------------------------------------------------- variant 3
# Generic SaaS export (Times, slate accent): grey-band supplier header,
# zebra-striped line table, two-column footer.
_ACCENT_SLATE = (55, 70, 95)


def _render_grey_zebra(pdf, d: dict) -> None:
    font = "Times"
    pdf.set_fill_color(230, 230, 235)
    pdf.set_font(font, "B", 15)
    pdf.cell(0, 12, d["supplier_name"] or "Lieferant", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.ln(2)
    pdf.set_font(font, "", 10)
    for line in (d["supplier_address"] or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    pdf.set_text_color(*_ACCENT_SLATE)
    pdf.set_font(font, "B", 12)
    pdf.cell(0, 7, f"Rechnung {d['bill_number'] or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, f"Rechnungsdatum: {d['bill_date'] or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    pdf.set_font(font, "B", 11)
    pdf.cell(0, 6, d["buyer_name"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 10)
    for line in (d["buyer_address"] or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font(font, "", 9)
    pdf.cell(0, 5, f"Leistungsdatum: entspricht Rechnungsdatum ({d['bill_date'] or '-'})",
             new_x="LMARGIN", new_y="NEXT")
    if d["due_date"]:
        pdf.cell(0, 5, f"Fällig am: {d['due_date']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    widths = (9, 83, 20, 28, 14, 34)
    desc_max = 41
    pdf.set_font(font, "B", 10)
    for w, label, align in zip(widths, ("Pos.", "Beschreibung", "Menge", "Einzelpreis", "USt%", "Gesamt"),
                               (None, None, "R", "R", "R", "R")):
        pdf.cell(w, 7, label, border="B", align=align or "", new_x="RIGHT", new_y="TOP")
    pdf.ln(7)

    pdf.set_font(font, "", 10)
    pdf.set_fill_color(244, 244, 247)
    if not d["lines"]:
        pdf.cell(sum(widths), 7, "Keine Positionen", new_x="LMARGIN", new_y="NEXT")
    for idx, l in enumerate(d["lines"]):
        shade = idx % 2 == 1
        tax_label = f"{l['tax_rate']:g}%" if l["tax_rate"] is not None else "-"
        pdf.cell(widths[0], 7, str(l["pos"]), align="R", fill=shade)
        pdf.cell(widths[1], 7, l["description"][:desc_max], fill=shade)
        pdf.cell(widths[2], 7, f"{l['qty']:g} {l['uom']}", align="R", fill=shade)
        pdf.cell(widths[3], 7, _money(l["price_unit"], d["currency_symbol"]), align="R", fill=shade)
        pdf.cell(widths[4], 7, tax_label, align="R", fill=shade)
        pdf.cell(widths[5], 7, _money(l["amount_untaxed"], d["currency_symbol"]), align="R", fill=shade,
                 new_x="LMARGIN", new_y="NEXT")

    _avoid_orphan(pdf)
    pdf.ln(3)
    label_w = sum(widths[:-1])
    value_w = widths[-1]
    pdf.set_font(font, "", 10)
    pdf.cell(label_w, 7, "Zwischensumme (netto)", align="R")
    pdf.cell(value_w, 7, _money(d["totals"]["untaxed"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    for tb in d["totals"]["tax_breakdown"] or []:
        pdf.cell(label_w, 6, f"zzgl. USt {tb['rate']:g}%", align="R")
        pdf.cell(value_w, 6, _money(tb["amount"], d["currency_symbol"]), align="R",
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font(font, "B", 11)
    pdf.set_text_color(*_ACCENT_SLATE)
    pdf.cell(label_w, 8, "Rechnungsbetrag (brutto)", align="R")
    pdf.cell(value_w, 8, _money(d["totals"]["total"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    fi = d["footer_info"]
    pdf.set_font(font, "", 9)
    left_w = 95
    top_y = pdf.get_y()
    for line in (
        f"Bank: {fi.get('bank_name', '-')}",
        f"IBAN: {fi.get('iban', '-')}",
        f"BIC: {fi.get('bic', '-')}",
    ):
        pdf.cell(left_w, 5, line, new_x="LMARGIN", new_y="NEXT")
    left_bottom_y = pdf.get_y()
    days = fi.get("payment_terms_days") or 30
    pdf.set_xy(pdf.l_margin + left_w, top_y)
    for line in (
        f"USt-IdNr.: {fi.get('tax_number', '-')}",
        f"Zahlungsziel: {days} Tage netto",
        f"Kundennummer: {fi.get('customer_number', '-')}",
    ):
        pdf.set_x(pdf.l_margin + left_w)
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(max(left_bottom_y, pdf.get_y()))


# ---------------------------------------------------------------- variant 4
# Legacy/mainframe-style export (Courier, monochrome): underlined header,
# borderless header-line table, boxed footer with a page number — the one
# variant that reads as "printed straight out of an old ERP system".
def _render_courier_erp(pdf, d: dict) -> None:
    font = "Courier"
    pdf.set_font(font, "B", 14)
    pdf.cell(0, 8, d["supplier_name"] or "Lieferant", new_x="LMARGIN", new_y="NEXT")
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)
    pdf.set_font(font, "", 10)
    for line in (d["supplier_address"] or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.set_font(font, "B", 11)
    pdf.cell(0, 7, f"Rechnung {d['bill_number'] or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 9)
    pdf.cell(0, 5, f"Rechnungsdatum: {d['bill_date'] or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    pdf.set_font(font, "B", 10)
    pdf.cell(0, 5, d["buyer_name"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "", 9)
    for line in (d["buyer_address"] or "").splitlines():
        pdf.cell(0, 4.5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.cell(0, 4.5, f"Leistungsdatum: entspricht Rechnungsdatum ({d['bill_date'] or '-'})",
             new_x="LMARGIN", new_y="NEXT")
    if d["due_date"]:
        pdf.cell(0, 4.5, f"Fällig am: {d['due_date']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    widths = (9, 83, 20, 28, 14, 34)
    desc_max = 32
    pdf.set_font(font, "B", 9)
    for w, label, align in zip(widths, ("Pos.", "Beschreibung", "Menge", "Preis/Einh.", "USt%", "Gesamt"),
                               (None, None, "R", "R", "R", "R")):
        pdf.cell(w, 6, label, align=align or "", new_x="RIGHT", new_y="TOP")
    pdf.ln(6)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.l_margin + sum(widths), y)
    pdf.ln(2)

    pdf.set_font(font, "", 9)
    if not d["lines"]:
        pdf.cell(sum(widths), 6, "Keine Positionen", new_x="LMARGIN", new_y="NEXT")
    for l in d["lines"]:
        tax_label = f"{l['tax_rate']:g}%" if l["tax_rate"] is not None else "-"
        pdf.cell(widths[0], 6, str(l["pos"]), align="R")
        pdf.cell(widths[1], 6, l["description"][:desc_max])
        pdf.cell(widths[2], 6, f"{l['qty']:g} {l['uom']}", align="R")
        pdf.cell(widths[3], 6, _money(l["price_unit"], d["currency_symbol"]), align="R")
        pdf.cell(widths[4], 6, tax_label, align="R")
        pdf.cell(widths[5], 6, _money(l["amount_untaxed"], d["currency_symbol"]), align="R",
                 new_x="LMARGIN", new_y="NEXT")

    _avoid_orphan(pdf)
    pdf.ln(3)
    label_w = sum(widths[:-1])
    value_w = widths[-1]
    pdf.set_font(font, "", 9)
    pdf.cell(label_w, 6, "Zwischensumme netto", align="R")
    pdf.cell(value_w, 6, _money(d["totals"]["untaxed"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    for tb in d["totals"]["tax_breakdown"] or []:
        pdf.cell(label_w, 6, f"USt {tb['rate']:g}%", align="R")
        pdf.cell(value_w, 6, _money(tb["amount"], d["currency_symbol"]), align="R",
                 new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "B", 10)
    pdf.cell(label_w, 7, "Rechnungsbetrag brutto", align="R")
    pdf.cell(value_w, 7, _money(d["totals"]["total"], d["currency_symbol"]), align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    _avoid_orphan(pdf, needed_mm=25)
    fi = d["footer_info"]
    days = fi.get("payment_terms_days") or 30
    pdf.set_font(font, "", 8.5)
    x0, y0 = pdf.l_margin, pdf.get_y()
    for text in (
        f"USt-IdNr.: {fi.get('tax_number', '-')}",
        f"Bank: {fi.get('bank_name', '-')} | IBAN: {fi.get('iban', '-')} | BIC: {fi.get('bic', '-')}",
        f"Zahlungsziel: {days} Tage netto | Kundennummer: {fi.get('customer_number', '-')}",
    ):
        pdf.cell(0, 4.5, text, new_x="LMARGIN", new_y="NEXT")
    y1 = pdf.get_y()
    pdf.set_draw_color(180, 180, 180)
    pdf.rect(x0 - 2, y0 - 2, (pdf.w - pdf.r_margin - x0) + 4, (y1 - y0) + 2, style="D")


_RENDERERS = {
    "corporate_grid": _render_corporate_grid,
    "letter_freelance": _render_letter_freelance,
    "bold_branded": _render_bold_branded,
    "grey_zebra": _render_grey_zebra,
    "courier_erp": _render_courier_erp,
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

    variant: which of the 5 layout presets to use — index into _VARIANTS
    ("corporate_grid", "letter_freelance", "bold_branded", "grey_zebra",
    "courier_erp"; modeled on a corporate ERP export, a freelancer/agency
    letter-style invoice, a bold branded caps layout, a generic SaaS export,
    and a legacy mainframe-style dump, respectively). None (the default)
    derives it deterministically from supplier_name (see _variant_for),
    which is what every real call site should rely on: the same supplier
    renders the same layout every time, different suppliers render
    differently, and neither fact depends on the caller tracking variant
    numbers.

    footer_info: {"tax_number" (the supplier's USt-IdNr., despite the key
    name), "iban", "bic", "bank_name", "payment_terms_days",
    "customer_number", "skonto_percent", "skonto_days"} — deterministic fake
    data for whichever supplier this is (see
    data_factory.build_vendor_footer_info). None/missing keys fall back to
    placeholder text rather than raising, same as an empty `lines`.

    buyer_name/buyer_address: the invoice recipient — real invoices always
    print a "bill to" block. None falls back to a generic "Kunde"
    placeholder rather than raising.

    due_date: the payment due date shown near the top, independent of the
    "Zahlungsbedingungen: N Tage netto" wording in the footer.

    totals: {"untaxed", "tax", "total", "tax_breakdown": [{"rate", "base",
    "amount"}]} — the authoritative net/tax/gross split, meant to be the
    same amount_untaxed/amount_tax/amount_total the underlying account.move
    itself carries so the rendered PDF can never disagree with the Odoo
    record it's attached to. "base" (the net amount that rate was charged
    on) is optional — when present, the corporate_grid variant uses it for
    an "X% USt auf Y EUR" style label. None (the default, used by callers
    that don't have a real tax-aware document to render) falls back to
    summing the lines' own amounts with no VAT breakdown shown.
    """
    idx = _variant_for(supplier_name) if variant is None else variant % len(_VARIANTS)
    variant_name = _VARIANTS[idx]
    footer_info = footer_info or {}
    normalized_lines = _normalize_lines(lines)
    if totals is None:
        totals = _fallback_totals(normalized_lines)

    pdf = _VendorBillPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.show_page_number = variant_name == "courier_erp"
    pdf.add_page()

    data = {
        "supplier_name": supplier_name,
        "supplier_address": supplier_address,
        "bill_number": bill_number,
        "bill_date": bill_date,
        "buyer_name": buyer_name or "Kunde",
        "buyer_address": buyer_address,
        "due_date": due_date,
        "currency_symbol": currency_symbol,
        "lines": normalized_lines,
        "totals": totals,
        "footer_info": footer_info,
    }
    _RENDERERS[variant_name](pdf, data)

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
