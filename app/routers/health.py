import os
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db
from app.schemas.common import StandardResponse

router = APIRouter(tags=["Health"])

# Render injects the deployed commit here. Surfaced on /health so "has my push
# actually gone live yet?" is answerable without the dashboard — which matters
# when a migration has to be applied the moment a deploy lands, and matters more
# because Render's pre-deploy has been observed NOT running `alembic upgrade`
# (migration 057 shipped code without its migration for ~20 minutes).
_COMMIT = (os.getenv("RENDER_GIT_COMMIT") or "unknown")[:7]


@router.get("/health", response_model=StandardResponse)
async def health_check():
    """Lightweight liveness probe — confirms the process is up (no DB call)."""
    return StandardResponse.ok(
        data={
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0.0",
            "commit": _COMMIT,
        },
        message="RAOMS API is running",
    )


@router.get("/health/deep", response_model=StandardResponse)
async def health_check_deep(db: AsyncSession = Depends(get_db)):
    """Deep health check — verifies API is running and DB is reachable. Used by Render."""
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
        "db": db_status,
    }
    if db_error:
        data["db_error"] = db_error

    return StandardResponse.ok(data=data, message="RAOMS API is running")
