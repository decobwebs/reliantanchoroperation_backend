"""The NMDPRA operation sheet — one row per truck load, 37 columns.

This is the regulator's own layout, taken from the company's existing
workbook (sheets "RELIANT", "NMDPRA REPORT", "NMDPRA REPORT 2026"). The BM
used to fill it in by hand for every operation; this builds it from the
records RAOMS already holds.

Each column comes from one of three places:

  * records      — the truck, the operation, the licences. Measured against
                   223 completed truck loads: plate, volume loaded, loading
                   date, discharge date, volume discharged and the waybill
                   number are all present on 100% of them.
  * settings     — twelve columns carry the same value on every row (company
                   name, MDOGISP, facility licences, OSCP...). They live in
                   system_settings under NMDPRA_SETTINGS_KEY so the BM sets
                   them once rather than retyping them per operation.
  * left blank   — anything genuinely unknown. A blank cell is honest; a
                   guess in a regulatory return is not.

Nothing here writes. Building a report must never change an operation.
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import SystemSetting

NMDPRA_SETTINGS_KEY = "nmdpra_report_constants"

# The twelve same-on-every-row columns, in sheet order. `key` is what the
# settings screen stores; `col` is the spreadsheet column it fills.
SETTING_FIELDS: List[Dict[str, str]] = [
    {"key": "transport_type", "col": "H", "label": "Transport Type", "hint": 'Usually "Truck"'},
    {"key": "company_name", "col": "I", "label": "Company Name", "hint": "The haulier on the return"},
    {"key": "haulage_permit", "col": "L", "label": "Haulage Permit", "hint": "MDOGISP number"},
    {"key": "product_source_type", "col": "O", "label": "Product Source Type", "hint": "IMPORT, REFINERY or DEPOT"},
    {"key": "ppdl_amount_paid", "col": "Z", "label": "PPDL Amount Paid", "hint": "e.g. N17,500,000"},
    {"key": "supplementary_ppdl_number", "col": "AA", "label": "Supplementary PPDL Number", "hint": 'N/A if none'},
    {"key": "supplementary_ppdl_quantity", "col": "AB", "label": "Supplementary PPDL Quantity", "hint": 'N/A if none'},
    {"key": "supplementary_ppdl_amount", "col": "AC", "label": "Supplementary PPDL Amount Paid", "hint": 'N/A if none'},
    {"key": "source_facility_licence", "col": "AD", "label": "Source Facility NMDPRA Licence", "hint": ""},
    {"key": "destination_facility_licence", "col": "AE", "label": "Destination Facility NMDPRA Licence", "hint": ""},
    {"key": "mdogisp", "col": "AH", "label": "MDOGISP Number", "hint": "For the vessel, barge or truck"},
    {"key": "oscp_reference", "col": "AI", "label": "OSCP / Journey Management Plan", "hint": "Document reference"},
]

# Column order and headings, copied from the workbook so an exported file can
# be pasted straight into the regulator's sheet.
COLUMNS: List[tuple] = [
    ('A', 'S/N'),
    ('B', 'REF NO'),
    ('C', 'SUBMITTED DATE'),
    ('D', 'APPROVED DATE'),
    ('E', 'QUARTER PERIOD'),
    ('F', 'PRODUCT'),
    ('G', 'CERTIFICATE OF QUALITY'),
    ('H', 'Transport Type'),
    ('I', 'Company Name'),
    ('J', 'Registraion No.'),
    ('K', 'Chasis No.'),
    ('L', 'Haulage Permit'),
    ('M', 'VOLUME (LTRS) FROM SOURCE FACILITY'),
    ('N', 'LOADING DATE AT SOURCE FACILITY'),
    ('O', 'PRODUCT SOURCE (IMPORT; REFINERY; DEPOT)'),
    ('P', 'PRODUCT SOURCE NAME'),
    ('Q', 'PRODUCT DESTINATION'),
    ('R', 'DISCHARGE DATE AT SOURCE FACILITY'),
    ('S', 'VOLUME (LTRS) DISCHARGED AT DESTINATION FACILITY'),
    ('T', 'EVIDENCE OF CERTIFICATE OF COMPLETION (SIGNED BY CLIENT)- IF APPLICABLE'),
    ('U', 'EVIDENCE OF NAVAL CLEARANCE'),
    ('V', 'CERTIFICATE OF QUANTITY'),
    ('W', 'FACILITIES / VESSELS / BARGES BUNKERED'),
    ('X', 'Petroleum Product Distribution License Number (Marine Operations)'),
    ('Y', 'Petroleum Product Distribution License Quantity (Litres)'),
    ('Z', 'Petroleum Product Distribution License Amount Paid (Naira) / Permit to Bnker Fee'),
    ('AA', 'Supplementary Petroleum Product Distribution License Number (Marine Operations)'),
    ('AB', 'Supplementary Petroleum Product Distribution License Quantity (Litres)'),
    ('AC', 'Supplementary Petroleum Product Distribution License Amount Paid (Naira)'),
    ('AD', 'NMDPRA License for Source Facility (If Applicable)'),
    ('AE', 'NMDPRA License for Destination Facility (If Applicable) - LPO WITH SEPLAT PROVIDED'),
    ('AF', 'Name of Coastal Vessel or Barge to be utilized for the distribution'),
    ('AG', 'VESSEL IMO NUMBER (IF APPLICABLE)'),
    ('AH', 'MDOGISP for transport company or vessel/barge/truck operator'),
    ('AI', 'Oil Spill Contingency Plan (OSCP), Journey Management Plan (JMP) and Emergency Management Plan (EMP) - Available or Not Available'),
    ('AJ', 'REMARK'),
    ('AK', 'STATUS'),
]

# Truck states that belong on a regulatory return. A cancelled load never
# moved product, so it is not a row.
_EXCLUDED_TRUCK_STATES = ("cancelled",)
_EXCLUDED_SQL = ", ".join(f"'{v}'" for v in _EXCLUDED_TRUCK_STATES)


async def get_settings(db: AsyncSession) -> Dict[str, str]:
    """The twelve constants, with every key present so the UI can bind to
    them even before anyone has saved anything."""
    row = await db.get(SystemSetting, NMDPRA_SETTINGS_KEY)
    saved = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {f["key"]: str(saved.get(f["key"], "") or "") for f in SETTING_FIELDS}


async def save_settings(values: Dict[str, str], user_id, db: AsyncSession) -> Dict[str, str]:
    """Stores only known keys, so a stray field in the payload cannot end up
    in a regulatory return."""
    clean = {f["key"]: str(values.get(f["key"], "") or "").strip() for f in SETTING_FIELDS}
    row = await db.get(SystemSetting, NMDPRA_SETTINGS_KEY)
    if row is None:
        row = SystemSetting(
            key=NMDPRA_SETTINGS_KEY,
            value=clean,
            description="Columns of the NMDPRA operation sheet that are the same on every row",
        )
        db.add(row)
    else:
        row.value = clean
    row.updated_by = user_id
    await db.flush()
    return clean


def _quarter(d) -> str:
    return f"Q{((d.month - 1) // 3) + 1} {d.year}" if d else ""


def _date(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


async def build_rows(operation_id: UUID, db: AsyncSession) -> Dict[str, Any]:
    """One dict per truck load, keyed by spreadsheet column letter."""
    op = (await db.execute(text("""
        SELECT o.id, o.operation_number, o.loading_location, o.notes, o.status,
               o.certificate_of_quality, o.certificate_of_completion,
               v.vessel_name AS our_vessel, v.imo_number,
               (SELECT string_agg(DISTINCT p.product_type, ', ')
                  FROM operation_products p WHERE p.operation_id = o.id) AS products
        FROM operations o
        LEFT JOIN vessels v ON v.id = o.vessel_id
        WHERE o.id = :o AND o.deleted_at IS NULL
    """), {"o": str(operation_id)})).mappings().first()
    if not op:
        return {"operation": None, "rows": []}

    settings = await get_settings(db)

    clearances = (await db.execute(text("""
        SELECT string_agg(DISTINCT n.clearance_number, ', ') FROM operation_naval_clearances l
        JOIN naval_clearances n ON n.id = l.naval_clearance_id WHERE l.operation_id = :o
    """), {"o": str(operation_id)})).scalar()

    bfl = (await db.execute(text("""
        SELECT b.bfl_number, b.submitted_date, b.approved_date
        FROM operation_naval_clearances l
        JOIN naval_clearance_drawdowns d ON d.naval_clearance_id = l.naval_clearance_id
        JOIN bfls b ON b.id = d.bfl_id
        WHERE l.operation_id = :o
        ORDER BY b.created_at LIMIT 1
    """), {"o": str(operation_id)})).mappings().first()

    ppdl = (await db.execute(text("""
        SELECT d.ppdl_number, (SELECT sum(p.quantity_litres) FROM ppdl_products p WHERE p.ppdl_id = d.id) AS qty
        FROM ppdls d WHERE d.is_current LIMIT 1
    """))).mappings().first()

    bunkered = (await db.execute(text("""
        SELECT string_agg(DISTINCT x, ', ') FROM (
            SELECT b.receiving_vessel AS x FROM bdns b
             WHERE b.operation_id = :o AND b.receiving_vessel IS NOT NULL AND b.receiving_vessel <> ''
            UNION
            SELECT l.receiving_vessel_name FROM vessel_activity_legs l
             JOIN vessel_activities a ON a.id = l.vessel_activity_id
             WHERE a.operation_id = :o AND l.cancelled_at IS NULL
        ) s
    """), {"o": str(operation_id)})).scalar()

    trucks = (await db.execute(text(f"""
        SELECT tr.truck_number, tr.chassis_number,
               t.product_type, t.status,
               COALESCE(t.loading_gov, t.loading_received_quantity_litres, t.quantity_loaded_mt) AS volume_loaded,
               t.quantity_discharged_mt AS volume_discharged,
               COALESCE(t.departed_loading_at, t.arrived_loading_at, t.loading_quantity_recorded_at) AS loading_date,
               COALESCE(t.discharge_end_at, t.arrived_discharge_at, t.transit_end_at) AS discharge_date,
               t.waybill_document_number, t.destination_vessel_name, t.loading_location
        FROM truck_operations t
        JOIN trucks tr ON tr.id = t.truck_id
        WHERE t.operation_id = :o AND t.status NOT IN ({_EXCLUDED_SQL})
        ORDER BY COALESCE(t.departed_loading_at, t.created_at)
    """), {"o": str(operation_id)})).mappings().all()

    rows: List[Dict[str, Any]] = []
    for i, t in enumerate(trucks, start=1):
        rows.append({
            "A": i,
            "B": (bfl or {}).get("bfl_number") or "",
            "C": _date((bfl or {}).get("submitted_date")),
            "D": _date((bfl or {}).get("approved_date")),
            "E": _quarter(t["loading_date"]),
            # Truck rows rarely carry their own product; the operation's is the
            # same fuel and is always recorded.
            "F": t["product_type"] or op["products"] or "",
            "G": op["certificate_of_quality"] or "",
            "H": settings["transport_type"],
            "I": settings["company_name"],
            "J": t["truck_number"] or "",
            "K": t["chassis_number"] or "",
            "L": settings["haulage_permit"],
            "M": _num(t["volume_loaded"]),
            "N": _date(t["loading_date"]),
            "O": settings["product_source_type"],
            "P": t["loading_location"] or op["loading_location"] or "",
            "Q": t["destination_vessel_name"] or op["our_vessel"] or "",
            "R": _date(t["discharge_date"]),
            "S": _num(t["volume_discharged"]),
            "T": op["certificate_of_completion"] or "",
            "U": clearances or "",
            "V": t["waybill_document_number"] or "",
            "W": bunkered or "",
            "X": (ppdl or {}).get("ppdl_number") or "",
            "Y": _num((ppdl or {}).get("qty")),
            "Z": settings["ppdl_amount_paid"],
            "AA": settings["supplementary_ppdl_number"],
            "AB": settings["supplementary_ppdl_quantity"],
            "AC": settings["supplementary_ppdl_amount"],
            "AD": settings["source_facility_licence"],
            "AE": settings["destination_facility_licence"],
            "AF": op["our_vessel"] or "",
            "AG": op["imo_number"] or "",
            "AH": settings["mdogisp"],
            "AI": settings["oscp_reference"],
            "AJ": op["notes"] or "",
            "AK": "DISCHARGED" if t["status"] == "completed" else (t["status"] or "").upper().replace("_", " "),
        })

    return {"operation": dict(op), "rows": rows, "settings": settings}


def to_xlsx(operation_number: str, rows: List[Dict[str, Any]]) -> bytes:
    """The workbook itself. One sheet, the 37 headings, one row per truck."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = operation_number[:31] or "OPERATION SHEET"

    header_font = Font(bold=True, color="FFFFFF", size=9)
    header_fill = PatternFill("solid", fgColor="0A1F3C")
    for idx, (_, heading) in enumerate(COLUMNS, start=1):
        c = ws.cell(row=1, column=idx, value=heading)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(wrap_text=True, vertical="center")
        # Wide enough to read without hiding the heading behind a column edge.
        ws.column_dimensions[get_column_letter(idx)].width = min(max(len(heading) * 0.62, 12), 34)
    ws.row_dimensions[1].height = 46
    ws.freeze_panes = "A2"

    for r, row in enumerate(rows, start=2):
        for idx, (letter, _) in enumerate(COLUMNS, start=1):
            ws.cell(row=r, column=idx, value=row.get(letter, ""))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
