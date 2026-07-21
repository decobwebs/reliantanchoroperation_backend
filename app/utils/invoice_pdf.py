from __future__ import annotations

import html
import io
import os
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


NAVY = colors.HexColor("#1C2B4A")
SKY = colors.HexColor("#5BA4CF")
LIGHT = colors.HexColor("#EEF4FB")
BORDER = colors.HexColor("#C8D8EC")
GREY = colors.HexColor("#6B7280")
WHITE = colors.white

PAGE_W, _PAGE_H = A4
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN
LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "logo.jpeg")


def _style(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


H1 = _style("h1", fontName="Helvetica-Bold", fontSize=21, textColor=NAVY, leading=25)
H2 = _style("h2", fontName="Helvetica-Bold", fontSize=10, textColor=NAVY, leading=13)
BODY = _style("body", fontName="Helvetica", fontSize=9, leading=13)
SMALL = _style("small", fontName="Helvetica", fontSize=8, textColor=GREY, leading=11)
LABEL = _style("label", fontName="Helvetica-Bold", fontSize=7.5, textColor=GREY, leading=10)
RIGHT = _style("right", fontName="Helvetica", fontSize=9, alignment=TA_RIGHT, leading=13)
RIGHT_BOLD = _style("right_bold", fontName="Helvetica-Bold", fontSize=10, alignment=TA_RIGHT, textColor=NAVY, leading=13)


PRODUCT_LABELS = {
    "AGO": "AGO (Automotive Gas Oil)",
    "DPK": "DPK (Dual Purpose Kerosene)",
    "PMS": "PMS (Premium Motor Spirit)",
    "HFO": "HFO (Heavy Fuel Oil)",
    "VLSFO": "VLSFO (Very Low Sulphur Fuel Oil)",
    "LSMGO": "LSMGO (Low Sulphur Marine Gas Oil)",
    "MGO": "MGO (Marine Gas Oil)",
    "IFO_380": "IFO 380 CST",
    "IFO_180": "IFO 180 CST",
    "JET_A1": "Jet A-1",
    "OTHER": "Other",
}

OP_TYPE_LABELS = {
    "full_operation": "Full Operation",
    "vessel_only": "Vessel Only",
    "truck_only": "Truck Only",
}


def _safe(value: object) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def _fmt_money(value: Decimal | float | int) -> str:
    return f"{float(value):,.2f}"


def _fmt_date(value: date | datetime | None) -> str:
    if not value:
        return "-"
    return value.strftime("%d %B %Y")


def _cell(label: str, value: object) -> list:
    return [Paragraph(label, LABEL), Paragraph(_safe(value), BODY)]


def generate_invoice_pdf(
    *,
    invoice_number: str,
    issue_date: datetime,
    due_date: Optional[date],
    operation_number: Optional[str],
    operation_type: Optional[str],
    operation_version: Optional[int],
    products: Optional[List[dict]],
    loading_location: Optional[str],
    discharge_location: Optional[str],
    bdn_number: Optional[str],
    quantity_delivered_mt: Optional[Decimal],
    client_name: str,
    client_email: str,
    client_phone: Optional[str],
    generated_by_name: str,
    amount: Decimal,
    tax_amount: Decimal,
    total_amount: Decimal,
    currency: str,
    exchange_rate: Optional[Decimal],
    notes: Optional[str],
    description: Optional[str] = None,   # explicit line item — standalone invoices
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )

    story = []
    logo_cell = Image(LOGO_PATH, width=22 * mm, height=22 * mm) if os.path.exists(LOGO_PATH) else ""

    company_block = [
        Paragraph("RELIANT ANCHOR LOGISTICS LIMITED", H1),
        Paragraph("Marine & Petroleum Logistics Services", _style("sub", fontName="Helvetica", fontSize=9, textColor=SKY, leading=12)),
        Spacer(1, 2 * mm),
        Paragraph("Lagos, Nigeria", SMALL),
        Paragraph("info@reliantanchor.com | +234 000 000 0000", SMALL),
    ]
    title_block = [
        Paragraph("TAX INVOICE", _style("title", fontName="Helvetica-Bold", fontSize=17, textColor=WHITE, alignment=TA_CENTER, leading=20)),
        Spacer(1, 1.5 * mm),
        Paragraph(_safe(invoice_number), _style("inv_no", fontName="Courier-Bold", fontSize=10.5, textColor=WHITE, alignment=TA_CENTER, leading=14)),
    ]
    header = Table([[logo_cell, company_block, title_block]], colWidths=[24 * mm, CONTENT_W - 24 * mm - 48 * mm, 48 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (2, 0), (2, 0), NAVY),
        ("PADDING", (2, 0), (2, 0), 8),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 4 * mm),
    ]))
    story.extend([header, Spacer(1, 2.5 * mm), HRFlowable(width="100%", thickness=1.5, color=SKY, spaceAfter=3 * mm)])

    meta = Table([[
        _cell("ISSUE DATE", _fmt_date(issue_date)),
        _cell("DUE DATE", _fmt_date(due_date)),
        _cell("CURRENCY", currency),
        _cell("EXCHANGE RATE", f"1 {currency} = {_fmt_money(exchange_rate)} NGN" if exchange_rate else "-"),
    ]], colWidths=[CONTENT_W / 4] * 4)
    meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([meta, Spacer(1, 4 * mm)])

    # A standalone (ad-hoc) invoice has no operation: no type label, no operation
    # panel, and the line item comes from the caller-supplied description.
    is_standalone = operation_number is None
    op_type_label = (
        OP_TYPE_LABELS.get(operation_type, operation_type.replace("_", " ").title())
        if operation_type
        else "-"
    )
    product_rows = [
        {
            "label": PRODUCT_LABELS.get(p["product_type"] or "", p["product_type"] or "-"),
            "qty": p.get("quantity_mt"),
        }
        for p in (products or [])
    ]
    product_label = ", ".join(row["label"] for row in product_rows) or "-"

    bill_to_cell = [
        Paragraph("BILL TO", H2),
        Spacer(1, 2 * mm),
        Paragraph(_safe(client_name), BODY),
        Paragraph(_safe(client_email), SMALL),
        Paragraph(_safe(client_phone), SMALL),
    ]

    if is_standalone:
        # Full-width BILL TO — there is no operation to describe.
        parties = Table([[bill_to_cell]], colWidths=[CONTENT_W])
    else:
        product_lines = (
            [Paragraph(f"Product: {_safe(row['label'])} ({_fmt_money(row['qty'])} L)"
                       if row["qty"] is not None else f"Product: {_safe(row['label'])}", BODY)
             for row in product_rows]
            or [Paragraph("Product: -", BODY)]
        )
        parties = Table([[
            bill_to_cell,
            [
                Paragraph("OPERATION DETAILS", H2),
                Spacer(1, 2 * mm),
                Paragraph(f"Operation: {_safe(operation_number)} (v{operation_version})", BODY),
                Paragraph(f"Service: {_safe(op_type_label)}", BODY),
                *product_lines,
                Paragraph(f"BDN: {_safe(bdn_number)}", BODY),
            ],
        ]], colWidths=[CONTENT_W / 2 - 3 * mm, CONTENT_W / 2 - 3 * mm])

    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("PADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([parties, Spacer(1, 4 * mm)])

    if is_standalone:
        line_desc = _safe(description)
    else:
        line_desc = f"{op_type_label} - {product_label}"
        if loading_location or discharge_location:
            line_desc += f"<br/>Route: {_safe(loading_location)} to {_safe(discharge_location)}"
        if quantity_delivered_mt:
            line_desc += f"<br/>Delivered quantity: {_fmt_money(quantity_delivered_mt)} L"

    line_items = Table([
        [Paragraph("DESCRIPTION", H2), Paragraph("AMOUNT", H2)],
        [Paragraph(line_desc, BODY), Paragraph(f"{currency} {_fmt_money(amount)}", RIGHT)],
        [Paragraph("Tax", BODY), Paragraph(f"{currency} {_fmt_money(tax_amount)}", RIGHT)],
        [Paragraph("TOTAL DUE", _style("total_label", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY, leading=14)), Paragraph(f"{currency} {_fmt_money(total_amount)}", RIGHT_BOLD)],
    ], colWidths=[CONTENT_W - 42 * mm, 42 * mm])
    line_items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("BACKGROUND", (0, 3), (-1, 3), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([line_items, Spacer(1, 4 * mm)])

    if notes:
        story.extend([Paragraph("NOTES", H2), Paragraph(_safe(notes), BODY), Spacer(1, 4 * mm)])

    story.extend([
        Spacer(1, 4 * mm),
        Paragraph(f"Generated by {_safe(generated_by_name)}", SMALL),
        Paragraph("This invoice was generated electronically by RAOMS.", SMALL),
    ])

    doc.build(story)
    return buf.getvalue()
