"""The operations dataset — every operation in one spreadsheet, in the
layout management asked for. See app/services/dataset_report_service.py.

Bunker Manager only: it spans every operation, like the global BDN register.
"""
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.enums import UserRole
from app.models.user import User
from app.services import dataset_report_service as svc

router = APIRouter(tags=["Operations Dataset"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/reports/operations-dataset.xlsx")
async def download_operations_dataset(
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    content = svc.to_xlsx(await svc.build_rows(db))
    filename = f"RAOMS-DATA-SET-{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
