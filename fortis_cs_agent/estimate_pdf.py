"""
Premium estimate PDF - Fortis Edge branding (#00A651), tables, validity, terms (fpdf2 / Latin-1 safe).
"""

from __future__ import annotations

import base64
import os
from decimal import Decimal
from io import BytesIO
from typing import BinaryIO

from fpdf import FPDF

from fortis_cs_agent.estimate_models import CreateEstimateResult, EstimateRequest, ProductType

# Fortis Edge brand green
ACCENT = (0, 166, 81)
CHARCOAL = (38, 40, 44)
MUTED = (95, 99, 104)

_TYPE_LABEL = {
    ProductType.PRESSURE_SENSITIVE_LABELS: "PS labels",
    ProductType.RFID_LABELS_OR_INLAYS: "RFID / inlays",
    ProductType.SHRINK_SLEEVES: "Shrink sleeves",
    ProductType.FLEXIBLE_PACKAGING: "Flexible packaging",
    ProductType.FOLDING_CARTON: "Folding cartons",
    ProductType.RSC: "Corrugated RSC",
    ProductType.TELESCOPING: "Telescoping",
    ProductType.MAILER: "Mailers",
    ProductType.PARTITION: "Partitions",
    ProductType.TRAY_SLEEVE_COMBO: "Tray / sleeve",
    ProductType.BULK_OTHER: "Bulk / other",
}


class _FortisPDF(FPDF):
    def header(self) -> None:
        self.set_fill_color(*ACCENT)
        self.rect(0, 0, 220, 22, style="F")
        self.set_xy(12, 6)
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(255, 255, 255)
        self.cell(0, 7, "FORTIS EDGE", ln=True)
        self.set_x(12)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, "Internal estimate | Packaging & labeling solutions", ln=True)
        self.ln(6)
        self.set_text_color(*CHARCOAL)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*MUTED)
        self.cell(0, 4, f"Confidential | Page {self.page_no()}/{{nb}}", align="C")


def _money(d: Decimal) -> str:
    return f"${d.quantize(Decimal('0.01')):,.2f}"


def _lbl(pt: ProductType) -> str:
    return _TYPE_LABEL.get(pt, pt.value.replace("_", " ")[:24])


def _ascii(s: str | None, maxlen: int = 500) -> str:
    if not s:
        return ""
    t = s.replace("\u2014", "-").replace("\u2013", "-").replace("\u2019", "'").replace("\u201c", '"').replace(
        "\u201d", '"'
    )
    return t[:maxlen]


def build_estimate_pdf_bytes(result: CreateEstimateResult) -> bytes:
    est = result.estimate
    pr = result.pricing
    support_email = os.getenv("FORTIS_SUPPORT_EMAIL", "edge-support@fortisedge.internal")
    support_phone = os.getenv("FORTIS_SUPPORT_PHONE", "(801) 459-0886")

    pdf = _FortisPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*CHARCOAL)
    pdf.cell(0, 7, "INDICATIVE ESTIMATE", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(
        0,
        4.2,
        _ascii(
            "Planning document for internal Fortis CS, commercial, and plant partners. "
            "Not a binding offer until authorized leadership releases a formal quote."
        ),
    )
    pdf.ln(3)

    # Meta strip
    pdf.set_fill_color(245, 248, 246)
    pdf.set_draw_color(220, 225, 222)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*ACCENT)
    pdf.cell(48, 6, "Estimate #", border=1, fill=True, ln=0)
    pdf.set_text_color(*CHARCOAL)
    pdf.cell(62, 6, _ascii(str(est.estimate_id), 40), border=1, fill=True, ln=0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*ACCENT)
    pdf.cell(40, 6, "Issue date", border=1, fill=True, ln=0)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*CHARCOAL)
    pdf.cell(0, 6, est.estimate_date.strftime("%d %b %Y"), border=1, fill=True, ln=True)

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*ACCENT)
    pdf.cell(48, 6, "Valid through", border=1, fill=True, ln=0)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*CHARCOAL)
    pdf.cell(62, 6, est.valid_until.strftime("%d %b %Y"), border=1, fill=True, ln=0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*ACCENT)
    pdf.cell(40, 6, "Urgency band", border=1, fill=True, ln=0)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*CHARCOAL)
    pdf.cell(0, 6, est.turnaround.title(), border=1, fill=True, ln=True)
    pdf.ln(4)

    # Bill to
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 6, "Bill to / attention", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*CHARCOAL)
    c = est.customer
    for line in [_ascii(c.contact_name), _ascii(c.company), _ascii(c.email), _ascii(c.phone)]:
        if line:
            pdf.cell(0, 4.5, line, ln=True)
    if est.ship_to_region:
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 4.5, _ascii(f"Ship / freight bias: {est.ship_to_region}"), ln=True)
    pdf.ln(2)

    if est.customer_notes:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*ACCENT)
        pdf.cell(0, 5, "Scope notes", ln=True)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*CHARCOAL)
        pdf.multi_cell(0, 4, _ascii(est.customer_notes, 2000))
        pdf.ln(2)

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 5, "Summary", ln=True)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*CHARCOAL)
    pdf.multi_cell(0, 4, _ascii(result.message, 1200))
    pdf.ln(3)

    # Line grid
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 6, "Line items", ln=True)
    cols = {
        "sku": 36,
        "cat": 32,
        "qty": 14,
        "wh": 22,
        "mat": 30,
        "fin": 28,
        "col": 26,
    }
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(236, 246, 239)
    pdf.set_text_color(*CHARCOAL)
    pdf.cell(cols["sku"], 5.5, "SKU / desc", border=1, fill=True, ln=0)
    pdf.cell(cols["cat"], 5.5, "Category", border=1, fill=True, ln=0)
    pdf.cell(cols["qty"], 5.5, "Qty", border=1, fill=True, ln=0)
    pdf.cell(cols["wh"], 5.5, "W x H in", border=1, fill=True, ln=0)
    pdf.cell(cols["mat"], 5.5, "Material", border=1, fill=True, ln=0)
    pdf.cell(cols["fin"], 5.5, "Finish", border=1, fill=True, ln=0)
    pdf.cell(cols["col"], 5.5, "Colors", border=1, fill=True, ln=True)

    pdf.set_font("Helvetica", "", 7)
    for li in est.line_items:
        w = li.width_in or "--"
        h = li.length_in or "--"
        wh = f"{w} x {h}" if w != "--" or h != "--" else "--"
        pdf.cell(cols["sku"], 5, _ascii(li.name, 22), border=1, ln=0)
        pdf.cell(cols["cat"], 5, _ascii(_lbl(li.product_type), 18), border=1, ln=0)
        pdf.cell(cols["qty"], 5, f"{li.quantity:,}", border=1, ln=0)
        pdf.cell(cols["wh"], 5, _ascii(str(wh), 14), border=1, ln=0)
        pdf.cell(cols["mat"], 5, _ascii(li.board, 26), border=1, ln=0)
        pdf.cell(cols["fin"], 5, _ascii(li.finish, 24), border=1, ln=0)
        pdf.cell(cols["col"], 5, _ascii(li.colors, 22), border=1, ln=True)
        if li.notes:
            pdf.set_font("Helvetica", "I", 6)
            pdf.multi_cell(0, 3.5, _ascii(f"  Line note: {li.notes}", 400), border="LR")
            pdf.set_font("Helvetica", "", 7)

    pdf.ln(4)
    pdf.set_x(12)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 6, "Investment summary (USD)", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*CHARCOAL)
    pdf.cell(76, 5, "Subtotal (extended)", ln=0)
    pdf.cell(0, 5, _money(pr.subtotal), ln=True)
    pdf.cell(76, 5, "Setup / tooling / program", ln=0)
    pdf.cell(0, 5, _money(pr.setup_fees), ln=True)
    pdf.cell(76, 5, "Freight planning", ln=0)
    pdf.cell(0, 5, _money(pr.freight_estimate), ln=True)
    pdf.cell(76, 5, "Schedule multiplier", ln=0)
    pdf.cell(0, 5, f"x {pr.rush_multiplier}", ln=True)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*ACCENT)
    pdf.cell(76, 6, "Indicative total", ln=0)
    pdf.set_text_color(*CHARCOAL)
    pdf.cell(0, 6, f"{_money(pr.total)} {pr.currency}", ln=True)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 5, "Key assumptions", ln=True)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*MUTED)
    for a in pr.assumptions:
        pdf.set_x(12)
        pdf.multi_cell(0, 3.6, _ascii(f"- {a}", 500))

    if est.internal_notes:
        pdf.ln(2)
        pdf.set_font("Helvetica", "BI", 8)
        pdf.set_text_color(*CHARCOAL)
        pdf.cell(0, 5, "Internal notes (Fortis only)", ln=True)
        pdf.set_font("Helvetica", "", 7)
        pdf.multi_cell(0, 3.6, _ascii(est.internal_notes, 2500))

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 5, "Terms and conditions", ln=True)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*MUTED)
    tc = (
        "1. Validity: Pricing is indicative until the valid-through date above unless extended in writing by "
        "Fortis commercial leadership. "
        "2. Scope: Excludes tariffs, rush overtime not captured, customer-induced holds, rework from non-Portal "
        "artwork issues, and consequential damages. "
        "3. Portal: Fortis Edge remains the system of record for approvals, superseding informal email sign-offs. "
        "4. SBU cadence: Portal-first routing is designed to compress CS cycle time versus unmanaged email threads - "
        "final ship dates still require plant load confirmation. "
        "5. Governing terms: Master supply agreement, NDA, and Fortis sustainability policies apply where executed."
    )
    pdf.multi_cell(0, 3.7, _ascii(tc, 4000))

    pdf.ln(5)
    pdf.set_fill_color(*ACCENT)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 6, " Fortis Edge | Questions & next steps", ln=True)
    pdf.set_text_color(*CHARCOAL)
    pdf.set_font("Helvetica", "", 8)
    body = (
        f"Portal & workflow: Fortis Edge (self-service submissions and approvals). "
        f"CS hotline: {support_phone}. Estimating mailbox: {support_email}. "
        f"Reference this estimate ID on POs and Portal tickets."
    )
    pdf.multi_cell(0, 4, _ascii(body, 800))

    out = BytesIO()
    pdf.output(out)
    return out.getvalue()


def pdf_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def write_estimate_pdf_binary(result: CreateEstimateResult) -> BinaryIO:
    buf = BytesIO(build_estimate_pdf_bytes(result))
    buf.seek(0)
    return buf
