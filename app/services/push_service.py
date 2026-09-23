"""Web push (VAPID) delivery.

Mirrors app/services/whatsapp_service.py: fire-and-forget, degrades to a log
warning when credentials are absent, never raises into the request path, and
keeps strong refs to background tasks so the event loop can't garbage-collect
them mid-flight (create_task only holds a weak ref).

TWO THINGS THIS FILE EXISTS TO GUARANTEE
─────────────────────────────────────────
1. NOTHING IS SENT BEFORE COMMIT. notify() runs inside the request's
   transaction; get_db() commits once, at the end. A push sent mid-transaction
   is unrecallable if anything downstream rolls the row back -- which is
   exactly how BDN-000027 reached the BM as "submitted" while its row was
   rolled away (see vessel_bdn_service._send_submitted_emails). So notify()
   only ENQUEUES into db.info; the SQLAlchemy after_commit event below is what
   actually releases the batch, and after_rollback / after_soft_rollback drop
   it.

2. THE CONNECTION BUDGET IS NOT TOUCHED. app/database.py caps this project at
   2 workers x (2 + 3) = 10 of Supabase's 15 connections. A naive fan-out --
   one session per recipient, or a subscription SELECT inside notify() itself
   -- would either exhaust the pool or add a round trip to Supabase inside
   every request that notifies someone. Instead: notify() issues NO query at
   all. A single process-wide drainer coroutine (never more than one in
   flight) batches every pending push across every concurrent request into
   ONE session and ONE `user_id IN (...)` query, closes that session, sends
   over the network with NO connection held, then opens one more session for a
   single batched prune/update. Peak cost: one extra connection per worker,
   held for two short round trips, not one per recipient.

Pushes queued in memory are lost if a worker restarts before the drain runs.
This is accepted: the `notifications` row is committed before the queue is
ever released, so the in-app bell and inbox are always correct -- only the
banner itself can be missed, and a restart is the only case where that happens.
"""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Optional
from uuid import UUID

from sqlalchemy import delete, event, func, select, update
from sqlalchemy.orm import Session as SyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.push_subscription import PushSubscription

logger = logging.getLogger("raoms")

_SESSION_KEY = "raoms_push_queue"
_DRAIN_DELAY_S = 0.25       # batches concurrent requests into one DB round trip
_MAX_CONCURRENT_SENDS = 8   # bound on the shared default thread-pool executor
_SEND_TIMEOUT_S = 10

# Module-level outbox and task registry -- mirrors whatsapp_service's
# _pending_tasks pattern for the same reason: create_task() only holds a weak
# reference, so something must keep these alive until they finish.
_outbox: list[tuple[UUID, dict]] = []
_drain_task: Optional[asyncio.Task] = None
_pending_tasks: set = set()


def _is_configured() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def vapid_key_id() -> str:
    """First 12 chars of the public key -- enough to tell keys apart, short
    enough to sit in an indexed varchar next to every subscription row."""
    return (settings.VAPID_PUBLIC_KEY or "")[:12]


# ── 1. Enqueue -- called from notify(), still inside the transaction ─────────

def enqueue(db, user_id: Optional[UUID], payload: dict) -> None:
    """Stage a push for `user_id`. Issues NO SQL. Only released on commit."""
    if not _is_configured() or user_id is None:
        return
    db.info.setdefault(_SESSION_KEY, []).append((user_id, payload))


# ── 2. Release on commit / drop on rollback ───────────────────────────────────
# Registered on the SYNC Session class. AsyncSession.info proxies the same
# dict a sync-session event sees, and SQLAlchemy runs these listeners on the
# request's own thread via greenlet, so `asyncio.get_running_loop()` below
# resolves correctly -- the same assumption whatsapp_service.dispatch() makes.

@event.listens_for(SyncSession, "after_commit")
def _release_on_commit(session) -> None:
    queued = session.info.pop(_SESSION_KEY, None)
    if not queued:
        return
    _outbox.extend(queued)
    _schedule_drain()


@event.listens_for(SyncSession, "after_rollback")
def _drop_on_rollback(session) -> None:
    dropped = session.info.pop(_SESSION_KEY, None)
    if dropped:
        logger.info("Push: dropped %d queued notification(s) on rollback", len(dropped))


@event.listens_for(SyncSession, "after_soft_rollback")
def _drop_on_soft_rollback(session, previous_transaction) -> None:
    dropped = session.info.pop(_SESSION_KEY, None)
    if dropped:
        logger.info("Push: dropped %d queued notification(s) on soft rollback", len(dropped))


def _schedule_drain() -> None:
    """Single-flight. A drain already sleeping picks up anything added during
    that sleep; anything added during its send phase re-schedules at the end."""
    global _drain_task
    if _drain_task is not None and not _drain_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop (a script, a test) -- never send, never block. Whatever was
        # queued is dropped; the in-app notification is already committed.
        _outbox.clear()
        return
    _drain_task = loop.create_task(_drain())
    _pending_tasks.add(_drain_task)
    _drain_task.add_done_callback(_pending_tasks.discard)


# ── 3. Drain ───────────────────────────────────────────────────────────────

async def _drain() -> None:
    try:
        await asyncio.sleep(_DRAIN_DELAY_S)
        if not _outbox:
            return
        batch, _outbox[:] = list(_outbox), []

        user_ids = list({uid for uid, _ in batch})

        # ONE session, ONE query -- closed before a single byte hits the network.
        async with AsyncSessionLocal() as s:
            result = await s.execute(
                select(
                    PushSubscription.id,
                    PushSubscription.endpoint,
                    PushSubscription.user_id,
                    PushSubscription.p256dh,
                    PushSubscription.auth,
                ).where(
                    PushSubscription.user_id.in_(user_ids),
                    PushSubscription.vapid_key_id == vapid_key_id(),
                )
            )
            rows = result.all()

        if not rows:
            return

        by_user: dict[UUID, list] = defaultdict(list)
        for r in rows:
            by_user[r.user_id].append(r)

        sem = asyncio.Semaphore(_MAX_CONCURRENT_SENDS)
        loop = asyncio.get_running_loop()

        async def _send(sub, payload):
            async with sem:
                status = await loop.run_in_executor(None, _send_one_blocking, sub, payload)
                return sub.endpoint, status

        jobs = [
            _send(sub, payload)
            for uid, payload in batch
            for sub in by_user.get(uid, ())
        ]
        if not jobs:
            return
        results = await asyncio.gather(*jobs, return_exceptions=True)

        dead: list[str] = []
        ok: list[str] = []
        for res in results:
            if isinstance(res, BaseException):
                logger.error("Push: send task raised: %s", res)
                continue
            endpoint, status = res
            if status in (404, 410, 403, 401):
                # Gone, or signed for a VAPID key we no longer hold -- either
                # way, retrying it is pointless.
                dead.append(endpoint)
            elif 200 <= status < 300:
                ok.append(endpoint)

        if dead or ok:
            async with AsyncSessionLocal() as s:
                if dead:
                    await s.execute(
                        delete(PushSubscription).where(PushSubscription.endpoint.in_(dead))
                    )
                    logger.info("Push: pruned %d dead subscription(s)", len(dead))
                if ok:
                    await s.execute(
                        update(PushSubscription)
                        .where(PushSubscription.endpoint.in_(ok))
                        .values(failure_count=0, last_error=None, last_success_at=func.now())
                    )
                await s.commit()
    except Exception as exc:
        logger.error("Push: drain error: %s", exc, exc_info=True)
    finally:
        if _outbox:
            _schedule_drain()  # anything that arrived during the send phase


# ── 4. Blocking send (pywebpush is requests-based) ────────────────────────────

def _send_one_blocking(sub, payload: dict) -> int:
    from pywebpush import webpush, WebPushException

    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            # A FRESH dict on every call. pywebpush MUTATES vapid_claims in
            # place -- it writes `aud` (derived from the endpoint's host) and
            # `exp` back into whatever dict it's handed. Reuse a shared dict
            # across sends and the second push to a *different* push service
            # carries the first one's `aud`, or an `exp` that's already
            # expired -- and 401s, intermittently and completely silently.
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=settings.PUSH_TTL_SECONDS,
            timeout=_SEND_TIMEOUT_S,
        )
        return 201
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", 0) or 0
        logger.warning("Push failed | endpoint=%s... | status=%s", sub.endpoint[:60], code)
        return code
    except Exception as exc:
        logger.error("Push error | endpoint=%s... | %s", sub.endpoint[:60], exc)
        return 0
