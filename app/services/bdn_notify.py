"""Who hears about a BDN — one set of rules for all three kinds.

Loading, Truck and Vessel BDNs each built their own recipient list, and the
lists drifted apart. Three gaps came out of that:

  * Nobody told the Ops Supervisor a BDN was waiting, although they can
    approve it. `reviewers()` is everyone who can approve: the Bunker
    Manager and the Ops Supervisor.
  * Recipients were picked by role alone, so a deactivated account would
    keep receiving notices and emails. Every lookup here is active-only.
  * Editing or deleting a BDN told nobody, so a corrected figure could
    reach an invoice without the submitter or Finance knowing it changed.
    `notify_bdn_changed()` covers that.
"""
from typing import Iterable, List, Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.operation import Operation
from app.models.user import User
from app.services.notification_service import notify

# Everyone who can approve or reject a BDN. Kept in step with the approve
# routes, which admit the Bunker Manager and the Ops Supervisor.
REVIEWER_ROLES = (UserRole.bunker_manager, UserRole.ops_supervisor)


async def active_users(db: AsyncSession, roles: Iterable[UserRole]) -> List[User]:
    """Active accounts holding any of `roles`."""
    result = await db.execute(
        select(User).where(and_(User.role.in_(list(roles)), User.is_active == True))  # noqa: E712
    )
    return list(result.scalars().all())


async def reviewers(db: AsyncSession) -> List[User]:
    """Who is told a BDN is waiting for review."""
    return await active_users(db, REVIEWER_ROLES)


async def active_user(db: AsyncSession, user_id: Optional[UUID]) -> Optional[User]:
    """The account behind `user_id`, or None if missing or deactivated."""
    if not user_id:
        return None
    user = await db.get(User, user_id)
    return user if user and user.is_active else None


async def notify_if_active(db: AsyncSession, user_id: Optional[UUID], **kwargs) -> None:
    """notify() for one person, skipped when their account is deactivated —
    otherwise a former employee's phone would keep receiving push notices."""
    if await active_user(db, user_id):
        await notify(db=db, user_id=user_id, **kwargs)


async def notify_bdn_changed(
    db: AsyncSession,
    *,
    kind: str,
    bdn_number: str,
    operation_id: UUID,
    submitter_id: Optional[UUID],
    actor: User,
    change: str,
    reason: Optional[str] = None,
) -> None:
    """Tell the submitter and Finance that a BDN was edited or deleted.

    `kind` is "BDN", "Truck BDN" or "Vessel BDN"; `change` is "edited" or
    "deleted". The person who made the change is never notified about it.
    In-app only — the same restraint as a rejection, which also sends no
    email. Push goes with every in-app notice automatically.
    """
    recipients: dict = {}
    submitter = await active_user(db, submitter_id)
    if submitter:
        recipients[submitter.id] = submitter
    for fm in await active_users(db, (UserRole.finance_manager,)):
        recipients[fm.id] = fm
    recipients.pop(actor.id, None)
    if not recipients:
        return

    op = await db.get(Operation, operation_id)
    op_number = op.operation_number if op else "an operation"
    message = f"{kind} {bdn_number} on operation {op_number} was {change} by {actor.full_name}."
    if reason:
        message += f" Reason: {reason}"

    for user_id in recipients:
        await notify(
            db=db,
            user_id=user_id,
            type_="system",
            title=f"{kind} {change}",
            message=message,
            priority="normal",
            operation_id=operation_id,
            action_url=f"/operations/{operation_id}",
            channels=["in_app"],
        )
