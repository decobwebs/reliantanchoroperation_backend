"""Web push subscriptions.

Devices subscribe here; app/services/push_service.py is what actually sends.
See that module's docstring for the after-commit and connection-budget design
this router depends on.
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.schemas.common import StandardResponse
from app.schemas.push import (
    PushRotateIn,
    PushSubscribeIn,
    PushSubscriptionOut,
    PushTestResult,
    PushUnsubscribeIn,
    VapidPublicKeyOut,
)
from app.services import push_service

logger = logging.getLogger("raoms")

router = APIRouter(prefix="/push", tags=["Push"])


@router.get("/vapid-public-key", response_model=StandardResponse)
async def get_vapid_public_key():
    """Public by definition — this is what the browser hands to
    pushManager.subscribe(). Served from here rather than a frontend env var
    because the frontend (Vercel) and backend (Render) have entirely separate
    env configs; if a copied value ever drifted, every subscription would
    sign with a key the server can't use and 403 forever with no visible
    symptom. Serving it removes that failure mode outright.
    """
    return StandardResponse.ok(
        data=VapidPublicKeyOut(
            public_key=settings.VAPID_PUBLIC_KEY,
            configured=push_service._is_configured(),
        ).model_dump()
    )


@router.post("/subscribe", response_model=StandardResponse)
async def subscribe(
    body: PushSubscribeIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Idempotent — safe to call on every app open, which is exactly how the
    frontend's syncPush() uses it.

    `endpoint` is UNIQUE across ALL users, not per-user, because a push
    endpoint identifies a browser profile, not an account. The upsert below
    REASSIGNS user_id on conflict, so if this device was last subscribed by a
    different person (a shared field tablet), that person stops receiving on
    it the moment this call lands. See push_subscription.py and migration 068
    for the full reasoning.

    Uses get_current_user, never an acting-as role — a Bunker Manager acting
    as Finance Manager must still bind this device to the BM's own account.
    """
    user_agent = (request.headers.get("User-Agent") or "")[:400]

    stmt = (
        pg_insert(PushSubscription)
        .values(
            user_id=current_user.id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            vapid_key_id=push_service.vapid_key_id(),
            user_agent=user_agent,
        )
        .on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_={
                "user_id": current_user.id,
                "p256dh": body.keys.p256dh,
                "auth": body.keys.auth,
                "vapid_key_id": push_service.vapid_key_id(),
                "user_agent": user_agent,
                "failure_count": 0,
                "last_error": None,
            },
        )
    )
    await db.execute(stmt)
    return StandardResponse.ok(message="Subscribed")


@router.delete("/subscribe", response_model=StandardResponse)
async def unsubscribe(
    body: PushUnsubscribeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Called from the settings toggle only — NOT from logout. A device keeps
    receiving its user's pushes after sign-out by design (re-engagement is the
    point of push); only an explicit "turn off notifications" removes it.
    Scoped to current_user.id so one user can't unsubscribe another's device
    by guessing an endpoint.
    """
    result = await db.execute(
        select(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint,
            PushSubscription.user_id == current_user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if sub:
        await db.delete(sub)
    return StandardResponse.ok(message="Unsubscribed")


@router.post("/rotate", status_code=status.HTTP_204_NO_CONTENT)
async def rotate(body: PushRotateIn, db: AsyncSession = Depends(get_db)):
    """Called by the service worker's pushsubscriptionchange handler, which
    can fire with no client page open and therefore no reachable access
    token — hence no auth here. Deliberately safe anyway:

      - the only thing `old_endpoint` can do is move a subscription to a NEW
        endpoint the caller's own browser just proved it controls (by
        successfully re-subscribing with the browser's pushManager);
      - it is matched exactly, so it grants nothing without already knowing a
        high-entropy string that only that browser and this server hold;
      - the response never reveals whether old_endpoint was found or is
        rate-limited (see RATE_LIMITS in app/middleware/rate_limit.py), so it
        gives an attacker no oracle to probe with.

    Always returns 204, matched or not.
    """
    if body.old_endpoint:
        result = await db.execute(
            select(PushSubscription).where(PushSubscription.endpoint == body.old_endpoint)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.endpoint = body.endpoint
            existing.p256dh = body.keys.p256dh
            existing.auth = body.keys.auth
            existing.vapid_key_id = push_service.vapid_key_id()
            existing.failure_count = 0
            existing.last_error = None
    return None


@router.get("/subscriptions", response_model=StandardResponse)
async def list_subscriptions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The caller's own devices, for the notification-settings card."""
    result = await db.execute(
        select(PushSubscription)
        .where(PushSubscription.user_id == current_user.id)
        .order_by(PushSubscription.created_at.desc())
    )
    subs = result.scalars().all()
    return StandardResponse.ok(
        data=[PushSubscriptionOut.model_validate(s).model_dump() for s in subs]
    )


@router.post("/test", response_model=StandardResponse)
async def send_test(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sends directly, bypassing the drainer, so the response can report
    per-device success right away instead of firing into the background."""
    if not push_service._is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Push notifications are not configured on the server (VAPID keys missing)",
        )

    result = await db.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == current_user.id,
            PushSubscription.vapid_key_id == push_service.vapid_key_id(),
        )
    )
    subs = result.scalars().all()
    if not subs:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No push subscriptions found for this account on this device",
        )

    payload = {
        "title": "RAOMS",
        "body": f"Hello {current_user.full_name.split(' ')[0]} — push notifications are working.",
        "url": "/notifications",
        "tag": "push-test",
        "priority": "normal",
    }

    loop = asyncio.get_running_loop()
    statuses = await asyncio.gather(
        *(loop.run_in_executor(None, push_service._send_one_blocking, s, payload) for s in subs)
    )
    sent = sum(1 for st in statuses if 200 <= st < 300)
    failed = len(statuses) - sent
    return StandardResponse.ok(
        data=PushTestResult(sent=sent, failed=failed, devices=len(subs)).model_dump()
    )
