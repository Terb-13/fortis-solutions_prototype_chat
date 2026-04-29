"""
Professional PDF generation for packaging estimates (fpdf2).
"""

from __future__ import annotations

import base64
from decimal import Decimal
from io import BytesIO
from typing import BinaryIO

from fpdf import FPDF

from fortis_cs_agent.estimate_models import CreateEstimateResult, EstimateRequest


class _FortisPDF(FPDF):
    def header(self) -> None:
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(25, 45, 85)
        self.cell(0, 10, "Fortis Edge", ln=True)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(80, 80, 80)
        self.cell(0, 6, "Packaging estimate", ln=True)
        self.ln(4)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _money(d: Decimal) -> str:
    return f"${d.quantize(Decimal('0.01')):,.2f}"


def build_estimate_pdf_bytes(result: CreateEstimateResult) -> bytes:
    """Render estimate + pricing into a PDF document."""
    est: EstimateRequest = result.estimate
    pr = result.pricing

    pdf = _FortisPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(30, 30, 30)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Estimate summary", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, result.message)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Customer", ln=True)
    pdf.set_font("Helvetica", "", 10)
    cust = est.customer
    lines = [
        cust.contact_name,
        *( [cust.company] if cust.company else [] ),
        *( [cust.email] if cust.email else [] ),
        *( [cust.phone] if cust.phone else [] ),
    ]
    for line in lines:
        pdf.cell(0, 6, line, ln=True)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(40, 7, "Estimate #", border=0)
    pdf.cell(60, 7, "Date", border=0)
    pdf.cell(0, 7, "Turnaround", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(40, 7, str(est.estimate_id))
    pdf.cell(60, 7, est.estimate_date.isoformat())
    pdf.cell(0, 7, est.turnaround, ln=True)
    if est.ship_to_region:
        pdf.cell(0, 6, f"Ship region (indicative): {est.ship_to_region}", ln=True)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Line items", ln=True)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(240, 242, 247)
    pdf.cell(52, 7, "Item", border=1, fill=True)
    pdf.cell(28, 7, "Type", border=1, fill=True)
    pdf.cell(18, 7, "Qty", border=1, fill=True)
    pdf.cell(44, 7, "Dims (LxWxD in)", border=1, fill=True)
    pdf.cell(28, 7, "Board / print", border=1, fill=True, ln=True)

    pdf.set_font("Helvetica", "", 8)
    for li in est.line_items:
        dims = "—"
        if li.length_in and li.width_in and li.depth_in:
            dims = f"{li.length_in}x{li.width_in}x{li.depth_in}"
        spec = (li.board or "") + ("; " if li.board and li.print_specs else "") + (li.print_specs or "")
        if not spec.strip():
            spec = "—"
        pdf.cell(52, 7, li.name[:30], border=1)
        pdf.cell(28, 7, li.product_type.value[:16], border=1)
        pdf.cell(18, 7, str(li.quantity), border=1)
        pdf.cell(44, 7, dims[:22], border=1)
        pdf.cell(28, 7, spec[:18], border=1, ln=True)
        if li.notes:
            pdf.set_font("Helvetica", "I", 8)
            pdf.cell(0, 5, f"  Note: {li.notes}", ln=True)
            pdf.set_font("Helvetica", "", 8)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Pricing (indicative)", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Subtotal: {_money(pr.subtotal)}", ln=True)
    pdf.cell(0, 6, f"Setup / tooling: {_money(pr.setup_fees)}", ln=True)
    pdf.cell(0, 6, f"Freight (planning): {_money(pr.freight_estimate)}", ln=True)
    pdf.cell(0, 6, f"Turnaround factor: x{pr.rush_multiplier}", ln=True)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, f"Total: {_money(pr.total)} {pr.currency}", ln=True)
    pdf.set_font("Helvetica", "", 9)
    if pr.assumptions:
        pdf.multi_cell(0, 5, "Assumptions:\n- " + "\n- ".join(pr.assumptions))
    pdf.ln(2)

    if est.internal_notes:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Internal notes", ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, est.internal_notes)

    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(90, 90, 90)
    pdf.ln(4)
    pdf.multi_cell(
        0,
        5,
        "This estimate is for planning purposes. Final pricing, structure review, and lead times "
        "are confirmed by Fortis operations. Track orders and share artwork in the Fortis Edge Portal.",
    )

    out = BytesIO()
    pdf.output(out)
    return out.getvalue()


def pdf_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def write_estimate_pdf_binary(result: CreateEstimateResult) -> BinaryIO:
    """Wrap bytes in BytesIO for Response streaming."""
    buf = BytesIO(build_estimate_pdf_bytes(result))
    buf.seek(0)
    return buf
