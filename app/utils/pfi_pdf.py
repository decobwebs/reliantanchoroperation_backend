"""
PFI (Pro-Forma Invoice) PDF generator.

Produces a professional A4 PDF for Reliant Anchor Logistics Limited,
pulling data directly from the Operation record.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Brand colours ──────────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#1C2B4A")
SKY     = colors.HexColor("#5BA4CF")
LIGHT   = colors.HexColor("#EEF4FB")
BORDER  = colors.HexColor("#C8D8EC")
WHITE   = colors.white
BLACK   = colors.black
GREY    = colors.HexColor("#6B7280")
RED_ACCENT = colors.HexColor("#DC2626")

# ── Layout constants ───────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4                   # 595.27 x 841.89 pts
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "logo.jpeg")

# ── Paragraph styles ───────────────────────────────────────────────────────────
def _style(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


H1     = _style("h1",     fontName="Helvetica-Bold",   fontSize=22, textColor=NAVY, leading=26)
H2     = _style("h2",     fontName="Helvetica-Bold",   fontSize=11, textColor=NAVY, leading=14)
H3     = _style("h3",     fontName="Helvetica-Bold",   fontSize=9,  textColor=NAVY, leading=12)
BODY   = _style("body",   fontName="Helvetica",        fontSize=9,  textColor=BLACK, leading=13)
SMALL  = _style("small",  fontName="Helvetica",        fontSize=8,  textColor=GREY,  leading=11)
LABEL  = _style("label",  fontName="Helvetica-Bold",   fontSize=7.5, textColor=GREY, leading=10, spaceAfter=1)
MONO   = _style("mono",   fontName="Courier-Bold",     fontSize=10, textColor=NAVY, leading=14)
TOTAL_STYLE = _style("total", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY,
                     alignment=TA_RIGHT, leading=14)
RIGHT  = _style("right",  fontName="Helvetica",        fontSize=9,  textColor=BLACK,
                alignment=TA_RIGHT, leading=13)
CENTER = _style("center", fontName="Helvetica",        fontSize=9,  textColor=GREY,
                alignment=TA_CENTER, leading=13)


PRODUCT_LABELS: dict[str, str] = {
    "AGO":     "AGO (Automotive Gas Oil)",
    "DPK":     "DPK (Dual Purpose Kerosene)",
    "PMS":     "PMS (Premium Motor Spirit)",
    "HFO":     "HFO (Heavy Fuel Oil)",
    "VLSFO":   "VLSFO (Very Low Sulphur Fuel Oil)",
    "LSMGO":   "LSMGO (Low Sulphur Marine Gas Oil)",
    "MGO":     "MGO (Marine Gas Oil)",
    "IFO_380": "IFO 380 CST",
    "IFO_180": "IFO 180 CST",
    "ULSFO":   "ULSFO (Ultra Low Sulphur Fuel Oil)",
    "JET_A1":  "Jet A-1",
    "ATK":     "ATK (Aviation Turbine Kerosene)",
    "NAPHTHA": "Naphtha",
    "CRUDE":   "Crude Oil",
    "OTHER":   "Other",
}

OP_TYPE_LABELS: dict[str, str] = {
    "full_operation": "Full Operation",
    "vessel_only":    "Vessel Only",
    "truck_only":     "Truck Only",
}


def _fmt_number(val: float | Decimal, decimals: int = 2) -> str:
    v = float(val)
    if decimals == 0:
        return f"{v:,.0f}"
    return f"{v:,.{decimals}f}"


def _fmt_date(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.strftime("%d %B %Y")


def generate_pfi_pdf(
    *,
    pfi_number: str,
    operation_number: str,
    operation_type: str,
    operation_version: int,
    product_type: Optional[str],
    loading_location: Optional[str],
    discharge_location: Optional[str],
    expected_volume_mt: Optional[Decimal],
    currency: str,
    rate_per_mt: Decimal,
    tax_rate: Decimal,
    exchange_rate: Optional[Decimal],
    validity_days: int,
    issue_date: datetime,
    supplier_name: Optional[str],
    description: Optional[str],
    notes: Optional[str],
    client_name: str,
    client_email: str,
    client_phone: Optional[str],
    prepared_by_name: str,
) -> bytes:
    """Return PDF bytes for a PFI document."""

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )

    # ── computed values ────────────────────────────────────────────────────────
    vol = float(expected_volume_mt or 0)
    rate = float(rate_per_mt)
    subtotal = vol * rate
    tax_pct  = float(tax_rate)
    tax_amt  = subtotal * tax_pct / 100
    total    = subtotal + tax_amt
    valid_until = issue_date + timedelta(days=validity_days)
    product_label = PRODUCT_LABELS.get(product_type or "", product_type or "N/A")
    op_type_label = OP_TYPE_LABELS.get(operation_type, operation_type.replace("_", " ").title())

    story = []

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 1 — HEADER  (logo left | "PRO-FORMA INVOICE" right)
    # ════════════════════════════════════════════════════════════════════════
    logo_cell = ""
    if os.path.exists(LOGO_PATH):
        logo_img = Image(LOGO_PATH, width=22*mm, height=22*mm)
        logo_cell = logo_img

    company_block = [
        Paragraph("RELIANT ANCHOR LOGISTICS LIMITED", H1),
        Paragraph("Marine & Petroleum Logistics Services", _style("sub", fontName="Helvetica",
                  fontSize=9, textColor=SKY, leading=12)),
        Spacer(1, 2*mm),
        Paragraph("Lagos, Nigeria", SMALL),
        Paragraph("info@reliantanchor.com  |  +234 000 000 0000", SMALL),
        Paragraph("RC Number: RC-XXXXXX", SMALL),
    ]

    title_block = [
        Paragraph("PRO-FORMA INVOICE", _style("title",
            fontName="Helvetica-Bold", fontSize=16, textColor=WHITE,
            alignment=TA_CENTER, leading=20)),
        Spacer(1, 1.5*mm),
        Paragraph(pfi_number, _style("pfi_no",
            fontName="Courier-Bold", fontSize=11, textColor=WHITE,
            alignment=TA_CENTER, leading=14)),
    ]

    header_table = Table(
        [[logo_cell, company_block, title_block]],
        colWidths=[24*mm, CONTENT_W - 24*mm - 50*mm, 50*mm],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",       (2, 0), (2, 0),  "CENTER"),
        ("BACKGROUND",  (2, 0), (2, 0),  NAVY),
        ("ROUNDEDCORNERS", [4]),
        ("PADDING",     (2, 0), (2, 0),  8),
        ("LEFTPADDING", (0, 0), (0, 0),  0),
        ("LEFTPADDING", (1, 0), (1, 0),  4*mm),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 2.5*mm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=SKY, spaceAfter=2.5*mm))

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 2 — PFI METADATA  (issue date | validity | currency | exchange)
    # ════════════════════════════════════════════════════════════════════════
    def _meta_cell(label: str, value: str) -> list:
        return [
            Paragraph(label, LABEL),
            Paragraph(value, _style("mv", fontName="Helvetica-Bold", fontSize=9,
                                    textColor=NAVY, leading=12)),
        ]

    exch_display = (f"1 {currency} = {_fmt_number(float(exchange_rate), 4)} NGN"
                    if exchange_rate else "—")

    meta_data = [[
        _meta_cell("DATE ISSUED",       _fmt_date(issue_date)),
        _meta_cell("VALID UNTIL",       _fmt_date(valid_until)),
        _meta_cell("CURRENCY",          currency),
        _meta_cell("EXCHANGE RATE",     exch_display),
    ]]
    meta_table = Table(meta_data, colWidths=[CONTENT_W / 4] * 4)
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), LIGHT),
        ("BOX",          (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 3*mm))

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 3 — BILL TO + OPERATION REFERENCE  (two-column)
    # ════════════════════════════════════════════════════════════════════════
    def _info_section(title: str, rows: list[tuple[str, str]]) -> list:
        items = [Paragraph(title, _style("sect",
            fontName="Helvetica-Bold", fontSize=8, textColor=WHITE,
            leading=11, leftIndent=3))]
        items.append(Spacer(1, 2*mm))
        for lbl, val in rows:
            items.append(Paragraph(lbl, LABEL))
            items.append(Paragraph(val or "—", BODY))
            items.append(Spacer(1, 1*mm))
        return items

    bill_to = _info_section("BILL TO", [
        ("Client Name",    client_name),
        ("Email",          client_email),
        ("Phone",          client_phone or ""),
    ])

    op_ref = _info_section("OPERATION REFERENCE", [
        ("Operation Number", operation_number),
        ("Service Type",     op_type_label),
        ("Version",          f"v{operation_version}"),
        ("Supplier",         supplier_name or "Reliant Anchor Logistics Ltd"),
    ])

    two_col = Table([[bill_to, op_ref]], colWidths=[CONTENT_W / 2 - 3*mm, CONTENT_W / 2 - 3*mm],
                    hAlign="LEFT")
    two_col.setStyle(TableStyle([
        ("VALIGN",      (0, 0), (-1, -1), "TOP"),
        ("BOX",         (0, 0), (0, 0),  0.5, BORDER),
        ("BOX",         (1, 0), (1, 0),  0.5, BORDER),
        ("BACKGROUND",  (0, 0), (0, 0),  LIGHT),
        ("BACKGROUND",  (1, 0), (1, 0),  LIGHT),
        ("TOPPADDING",  (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",(0, 0), (-1, -1), 5),
    ]))

    # Inject header row backgrounds manually by nesting them in their own tables
    def _section_header(title: str) -> Table:
        t = Table([[Paragraph(title, _style("sh",
            fontName="Helvetica-Bold", fontSize=8, textColor=WHITE, leading=11))]],
            colWidths=[CONTENT_W / 2 - 3*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), NAVY),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ]))
        return t

    bill_col = [_section_header("BILL TO")] + [Spacer(1, 2*mm)] + [
        Paragraph("Client Name", LABEL), Paragraph(client_name or "—", BODY), Spacer(1, 1*mm),
        Paragraph("Email", LABEL), Paragraph(client_email or "—", BODY), Spacer(1, 1*mm),
        Paragraph("Phone", LABEL), Paragraph(client_phone or "—", BODY),
    ]

    ref_col = [_section_header("OPERATION REFERENCE")] + [Spacer(1, 2*mm)] + [
        Paragraph("Operation Number", LABEL), Paragraph(operation_number, MONO), Spacer(1, 1*mm),
        Paragraph("Service Type", LABEL), Paragraph(op_type_label, BODY), Spacer(1, 1*mm),
        Paragraph("Version", LABEL), Paragraph(f"v{operation_version}", BODY), Spacer(1, 1*mm),
        Paragraph("Supplier", LABEL), Paragraph(supplier_name or "Reliant Anchor Logistics Ltd", BODY),
    ]

    two_col2 = Table([[bill_col, ref_col]],
                     colWidths=[CONTENT_W / 2 - 2*mm, CONTENT_W / 2 - 2*mm])
    two_col2.setStyle(TableStyle([
        ("VALIGN",      (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",(0, 0), (-1, -1), 0),
        ("TOPPADDING",  (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0,0), (-1,-1),  0),
        ("COLPADDING",  (1, 0), (1, 0),   4*mm),
    ]))
    story.append(two_col2)
    story.append(Spacer(1, 3*mm))

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 4 — SERVICE DESCRIPTION TABLE
    # ════════════════════════════════════════════════════════════════════════
    route = ""
    if loading_location or discharge_location:
        route = f"{loading_location or '—'} → {discharge_location or '—'}"

    service_desc = (description or f"{product_label} supply and delivery services")
    if route:
        service_desc += f"\nRoute: {route}"

    svc_header = [
        Paragraph("DESCRIPTION", _style("th", fontName="Helvetica-Bold", fontSize=8.5,
                                         textColor=WHITE, leading=11)),
        Paragraph("QTY (MT)", _style("th_c", fontName="Helvetica-Bold", fontSize=8.5,
                                     textColor=WHITE, alignment=TA_CENTER, leading=11)),
        Paragraph(f"RATE ({currency}/MT)", _style("th_c", fontName="Helvetica-Bold", fontSize=8.5,
                                                   textColor=WHITE, alignment=TA_RIGHT, leading=11)),
        Paragraph(f"AMOUNT ({currency})", _style("th_c", fontName="Helvetica-Bold", fontSize=8.5,
                                                  textColor=WHITE, alignment=TA_RIGHT, leading=11)),
    ]

    svc_row = [
        Paragraph(service_desc, BODY),
        Paragraph(_fmt_number(vol, 3) if vol else "TBD", _style("qty",
            fontName="Helvetica", fontSize=9, alignment=TA_CENTER, leading=13)),
        Paragraph(_fmt_number(rate) if rate else "TBD", _style("rate",
            fontName="Helvetica", fontSize=9, alignment=TA_RIGHT, leading=13)),
        Paragraph(_fmt_number(subtotal) if (vol and rate) else "TBD", _style("amt",
            fontName="Helvetica-Bold", fontSize=9, alignment=TA_RIGHT, textColor=NAVY, leading=13)),
    ]

    col_w = [CONTENT_W * 0.45, CONTENT_W * 0.18, CONTENT_W * 0.18, CONTENT_W * 0.19]
    svc_table = Table([svc_header, svc_row], colWidths=col_w)
    svc_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
        ("BACKGROUND",    (0, 1), (-1, 1), LIGHT),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("LINEABOVE",     (0, 1), (-1, 1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(svc_table)
    story.append(Spacer(1, 2*mm))

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 5 — FINANCIAL SUMMARY  (right-aligned totals box)
    # ════════════════════════════════════════════════════════════════════════
    spacer_col = CONTENT_W * 0.55
    label_col  = CONTENT_W * 0.25
    value_col  = CONTENT_W * 0.20

    def _total_row(label: str, value: str, bold: bool = False, bg=None) -> list:
        st = _style(f"tl_{label}", fontName="Helvetica-Bold" if bold else "Helvetica",
                    fontSize=9 if not bold else 11, textColor=NAVY if bold else BLACK,
                    alignment=TA_RIGHT, leading=13)
        vs = _style(f"tv_{label}", fontName="Helvetica-Bold" if bold else "Helvetica",
                    fontSize=9 if not bold else 11, textColor=NAVY if bold else BLACK,
                    alignment=TA_RIGHT, leading=13)
        return ["", Paragraph(label, st), Paragraph(value, vs)]

    totals_data = [
        _total_row("Subtotal",       f"{currency} {_fmt_number(subtotal)}"),
        _total_row(f"Tax ({tax_pct:.1f}%)", f"{currency} {_fmt_number(tax_amt)}"),
        _total_row("TOTAL DUE",      f"{currency} {_fmt_number(total)}", bold=True),
    ]
    if exchange_rate and currency != "NGN":
        ngn_total = total * float(exchange_rate)
        totals_data.append(_total_row("≈ NGN Equivalent",
                                       f"NGN {_fmt_number(ngn_total)}"))

    totals_table = Table(totals_data, colWidths=[spacer_col, label_col, value_col])
    totals_table.setStyle(TableStyle([
        ("LINEABOVE",     (1, -1), (-1, -1), 1.5, NAVY),
        ("BACKGROUND",    (1, -1 if not (exchange_rate and currency != "NGN") else -2),
                          (-1, -1 if not (exchange_rate and currency != "NGN") else -2), LIGHT),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 3*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=2*mm))

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 6 — NOTES / PAYMENT TERMS
    # ════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("NOTES & PAYMENT TERMS", H3))
    story.append(Spacer(1, 1.5*mm))
    terms = [
        f"1.  This Pro-Forma Invoice is valid for {validity_days} days from the date of issue ({_fmt_date(valid_until)}).",
        "2.  Payment must be received in full before operations commence.",
        "3.  All prices are in the stated currency and exclude any bank charges.",
        "4.  Quantities are estimates; final billing will be based on actual volumes delivered.",
        "5.  This document is computer-generated and valid without a physical signature.",
    ]
    if notes:
        terms.append(f"6.  {notes}")
    for term in terms:
        story.append(Paragraph(term, SMALL))
        story.append(Spacer(1, 0.5*mm))
    story.append(Spacer(1, 3*mm))

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 7 — APPROVAL / SIGNATURE BLOCK
    # ════════════════════════════════════════════════════════════════════════
    sig_left = [
        Paragraph("PREPARED BY", LABEL),
        Paragraph(prepared_by_name, H2),
        Paragraph("Bunker Manager", SMALL),
        Paragraph("Reliant Anchor Logistics Limited", SMALL),
        Spacer(1, 2*mm),
        Paragraph("Signature: _______________________", SMALL),
        Paragraph(f"Date: {_fmt_date(issue_date)}", SMALL),
    ]

    sig_right = [
        Paragraph("CLIENT ACCEPTANCE", LABEL),
        Spacer(1, 2*mm),
        Paragraph("Name: ___________________________", SMALL),
        Spacer(1, 2*mm),
        Paragraph("Signature: ______________________", SMALL),
        Spacer(1, 2*mm),
        Paragraph("Date: ___________________________", SMALL),
        Spacer(1, 2*mm),
        Paragraph("Company Stamp:", SMALL),
    ]

    sig_table = Table([[sig_left, sig_right]],
                      colWidths=[CONTENT_W / 2 - 3*mm, CONTENT_W / 2 - 3*mm])
    sig_table.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("BOX",          (0, 0), (0, 0),   0.5, BORDER),
        ("BOX",          (1, 0), (1, 0),   0.5, BORDER),
        ("BACKGROUND",   (0, 0), (0, 0),   LIGHT),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5*mm),
        ("TOPPADDING",   (0, 0), (-1, -1), 4*mm),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4*mm),
    ]))
    story.append(sig_table)
    story.append(Spacer(1, 3*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=1.5*mm))

    # ════════════════════════════════════════════════════════════════════════
    # FOOTER
    # ════════════════════════════════════════════════════════════════════════
    story.append(Paragraph(
        f"This document was generated by the Reliant Anchor Operations System on {_fmt_date(issue_date)}. "
        f"Reference: {pfi_number} | Operation: {operation_number}",
        CENTER,
    ))

    doc.build(story)
    return buf.getvalue()
