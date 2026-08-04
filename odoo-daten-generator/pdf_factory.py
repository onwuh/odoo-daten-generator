"""Pure-Python PDF rendering for inbound demo documents (vendor bills, CVs).

No Odoo/network calls here — this module only turns already-known structured
data into PDF bytes. Uses fpdf2 (PyPI package name "fpdf2", import name
"fpdf") for pure-Python rendering with no system dependencies (cairo/pango),
keeping Windows compatibility.
"""

from typing import List, Optional

from fpdf import FPDF


def _new_pdf() -> FPDF:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    return pdf


def build_vendor_bill_pdf(
    supplier_name: str,
    supplier_address: Optional[str],
    bill_number: Optional[str],
    bill_date: Optional[str],
    lines: List[dict],
    currency_symbol: str = "EUR",
) -> bytes:
    """Renders a simple vendor-bill-style PDF and returns it as bytes.

    lines: list of {"description": str, "quantity": float, "price_unit": float}.
    Renders a placeholder row instead of raising when lines is empty.
    currency_symbol defaults to the "EUR" string, not "€" — fpdf2's built-in
    core fonts only support latin-1, which excludes the euro sign; a real
    Unicode font would need to be embedded (extra binary asset) just for one
    character, not worth it for a demo document.
    """
    pdf = _new_pdf()

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, supplier_name or "Lieferant", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for line in (supplier_address or "").splitlines():
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Rechnung {bill_number or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Datum: {bill_date or ''}".strip(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 7, "Beschreibung", border=1)
    pdf.cell(25, 7, "Menge", border=1, align="R")
    pdf.cell(35, 7, "Einzelpreis", border=1, align="R")
    pdf.cell(35, 7, "Gesamt", border=1, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    total = 0.0
    if not lines:
        pdf.cell(185, 7, "Keine Positionen", border=1, new_x="LMARGIN", new_y="NEXT")
    for line in lines:
        qty = line.get("quantity") or 0
        price = line.get("price_unit") or 0
        amount = qty * price
        total += amount
        pdf.cell(90, 7, str(line.get("description", ""))[:45], border=1)
        pdf.cell(25, 7, f"{qty:g}", border=1, align="R")
        pdf.cell(35, 7, f"{price:,.2f} {currency_symbol}", border=1, align="R")
        pdf.cell(35, 7, f"{amount:,.2f} {currency_symbol}", border=1, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(150, 7, "Gesamtbetrag", align="R")
    pdf.cell(35, 7, f"{total:,.2f} {currency_symbol}", align="R", new_x="LMARGIN", new_y="NEXT")

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
