"""NMDPRA operation sheet — download, preview, and the twelve settings.

Download is the whole point: an .xlsx in the regulator's own column order,
one row per truck load, ready to submit or paste into the master workbook.
Preview returns the same rows as JSON so the screen can show what will be in
the file, and which cells are still blank, before anything is downloaded.
"""
from typing import Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_operation_manager, require_roles
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import StandardResponse
from app.services import nmdpra_report_service as svc

router = APIRouter(tags=["NMDPRA Report"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class NmdpraSettingsRequest(BaseModel):
    """Free-form on purpose: the service keeps only the keys it knows."""
    values: Dict[str, str]


@router.get("/nmdpra/settings", response_model=StandardResponse)
async def get_nmdpra_settings(
    current_user: User = Depends(require_operation_manager()),
    db: AsyncSession = Depends(get_db),
):
    """The twelve same-on-every-row columns, plus their labels so the screen
    can render the form without hard-coding them a second time."""
    return StandardResponse.ok(
        data={"fields": svc.SETTING_FIELDS, "values": await svc.get_settings(db)},
        message="Settings retrieved",
    )


@router.put("/nmdpra/settings", response_model=StandardResponse)
async def put_nmdpra_settings(
    body: NmdpraSettingsRequest,
    # Bunker Manager only: these values go onto a regulatory return for every
    # operation, so they are not an operation-level edit.
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    saved = await svc.save_settings(body.values, current_user.id, db)
    await db.commit()
    return StandardResponse.ok(data=saved, message="Settings saved")


@router.get("/operations/{operation_id}/nmdpra-sheet", response_model=StandardResponse)
async def preview_nmdpra_sheet(
    operation_id: UUID,
    current_user: User = Depends(require_operation_manager()),
    db: AsyncSession = Depends(get_db),
):
    """What the download will contain, so nobody has to open a file to find
    out that a column is empty."""
    built = await svc.build_rows(operation_id, db)
    if built["operation"] is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    rows = built["rows"]
    blanks = [
        {"column": letter, "heading": heading}
        for letter, heading in svc.COLUMNS
        if rows and all(str(r.get(letter, "") or "").strip() == "" for r in rows)
    ]
    return StandardResponse.ok(
        data={
            "operation_number": built["operation"]["operation_number"],
            "columns": [{"column": c, "heading": h} for c, h in svc.COLUMNS],
            "rows": rows,
            "truck_count": len(rows),
            "empty_columns": blanks,
        },
        message="Sheet built",
    )


@router.get("/operations/{operation_id}/nmdpra-sheet.xlsx")
async def download_nmdpra_sheet(
    operation_id: UUID,
    current_user: User = Depends(require_operation_manager()),
    db: AsyncSession = Depends(get_db),
):
    built = await svc.build_rows(operation_id, db)
    if built["operation"] is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    op_no = built["operation"]["operation_number"]
    content = svc.to_xlsx(op_no, built["rows"])
    filename = f"NMDPRA-{op_no}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
