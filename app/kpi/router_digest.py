"""Digest endpoints.

`preview` computes and returns the digest without sending anything, so the
content can be checked before a single email leaves the building.

`send` delivers it to the authenticated caller's own address. The recipient is
read from `current_user`, never from the request — there is deliberately no way
to address this at anybody else.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.kpi.digest import DigestService
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import StandardResponse

router = APIRouter(tags=["KPI"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))


@router.get("/kpi/digest/preview", response_model=StandardResponse)
async def preview_digest(
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """What the digest would say right now. Sends nothing."""
    digest = await DigestService.build(db)
    return StandardResponse.ok(data={
        "subject": digest["subject"],
        "html": digest["html"],
        "text": digest["text"],
        "attention_total": digest["attention_total"],
        "critical_total": digest["critical_total"],
        "generated_at": digest["generated_at"].isoformat(),
        "degraded": digest["degraded"],
        "data_warnings": digest["data_warnings"],
    })


@router.post("/kpi/digest/send", response_model=StandardResponse)
async def send_digest(
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Email the digest to yourself.

    Only ever to the caller. No recipient parameter exists, so this cannot be
    turned into a way to mail scores at staff.
    """
    if not current_user.email:
        return StandardResponse.ok(data={
            "sent": False,
            "reason": "Your account has no email address on it.",
        })

    result = await DigestService.send_to_self(db, current_user.email)

    if result["sent"]:
        message = f"Digest sent to {result['recipient']}"
    elif not result["email_configured"]:
        message = ("Email is not configured on this server, so nothing was sent. "
                   "The digest was built successfully.")
    else:
        message = "Email failed to send. Nothing reached your inbox."

    return StandardResponse.ok(data=result, message=message)
