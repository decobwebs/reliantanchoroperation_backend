from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db
from app.schemas.common import StandardResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=StandardResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Health check endpoint. Verifies API is running and DB is reachable.
    """
    db_status = "ok"
    db_error = None
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = "error"
        db_error = str(exc)

    data = {
        "status": "ok" if db_status == "ok" else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "database": db_status,
    }
    if db_error:
        data["db_error"] = db_error

    return StandardResponse.ok(data=data, message="RAOMS API is running")
