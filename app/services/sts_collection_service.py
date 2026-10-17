"""Ship-to-ship collections — see app/models/sts_collection.py.

Lifecycle:
    in_progress --submit figures--> pending --approve--> approved
                                            --reject---> rejected --resubmit--> pending
    any status --cancel (manager, reason)--> cancelled

Stages (Cast Off -> Alongside -> Receiving Started -> Receiving Completed)
and the three HSE checks are recorded alongside and never gate anything,
the same rule the vessel runs follow. Only approval touches the barge's ROB.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditLog
from app.models.bdn import RobEntry
from app.models.enums import OperationStatus, OperationType, RobEntryType
from app.models.operation import Operation, OperationStatusHistory
from app.models.sts_collection import STS_STAGES, StsCollection
from app.models.user import User
from app.models.vessel import Vessel
from app.permissions import is_operation_manager
from app.schemas.sts_collection import (
    StsCollectionCreate, StsCollectionDetailsUpdate, StsFigures, StsFiguresCorrection,
    StsHseRecord, StsStageAdvance,
)
from app.services.bdn_notify import notify_if_active, reviewers
from app.services.notification_service import notify
from app.utils.number_generator import generate_sts_receipt_number

_STAGE_COLUMN = {
    "cast_off": "cast_off_at",
    "alongside": "alongside_at",
    "receiving_started": "receiving_started_at",
    "receiving_completed": "receiving_completed_at",
}
_STAGE_LABEL = {
    "cast_off": "Cast Off",
    "alongside": "Alongside",
    "receiving_started": "Receiving Started",
    "receiving_completed": "Receiving Completed",
}


def _rob_marker(c: StsCollection) -> str:
    """Tags the RobEntry an approval writes, so a correction or cancellation
    reverses exactly that entry and nothing else."""
    return f"STS collection {c.receipt_number}"


def _forbid_unless_manager(user: User, action: str) -> None:
    if not is_operation_manager(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only the Bunker Manager or Ops Supervisor can {action}",
        )


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    op = (await db.execute(
        select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
    )).scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return op


def _query():
    return select(StsCollection).execution_options(populate_existing=True).options(
        selectinload(StsCollection.vessel),
        selectinload(StsCollection.creator),
        selectinload(StsCollection.submitter),
        selectinload(StsCollection.reviewer),
    )


async def _get_or_404(collection_id: UUID, db: AsyncSession) -> StsCollection:
    c = (await db.execute(_query().where(StsCollection.id == collection_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship-to-ship collection not found")
    return c


async def _delivery_started_at(operation_id: UUID, db: AsyncSession) -> Optional[datetime]:
    """When the barge started delivering — the first move into Vessel
    Operations. ROB is snapshotted then, so loading recorded later is
    missing from that snapshot."""
    return (await db.execute(
        select(OperationStatusHistory.created_at)
        .where(and_(
            OperationStatusHistory.operation_id == operation_id,
            OperationStatusHistory.to_status == OperationStatus.vessel_operations,
        ))
        .order_by(OperationStatusHistory.created_at)
        .limit(1)
    )).scalar_one_or_none()


def _decorate(c: StsCollection, delivery_started: Optional[datetime]) -> StsCollection:
    c.vessel_name = c.vessel.vessel_name if c.vessel else None
    c.created_by_name = c.creator.full_name if c.creator else None
    c.submitted_by_name = c.submitter.full_name if c.submitter else None
    c.reviewed_by_name = c.reviewer.full_name if c.reviewer else None
    loaded_at = c.receiving_completed_at or c.created_at
    c.recorded_after_delivery_started = bool(
        delivery_started and loaded_at and _aware(loaded_at) > _aware(delivery_started)
    )
    return c


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def _reload(collection_id: UUID, db: AsyncSession) -> StsCollection:
    c = await _get_or_404(collection_id, db)
    return _decorate(c, await _delivery_started_at(c.operation_id, db))


def _assert_live(c: StsCollection) -> None:
    if c.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This collection was cancelled")


async def _credit_rob(c: StsCollection, user: User, db: AsyncSession) -> None:
    """Approval adds the collected MT(vac) to the barge. The vessel row is
    locked for the rest of the transaction, so a figure staff record on the
    same barge at the same moment can't be lost between read and write."""
    vessel = await db.get(Vessel, c.vessel_id, with_for_update=True)
    if not vessel or c.mt_vacuum is None:
        return
    rob_before = vessel.current_rob_mt or Decimal("0")
    rob_after = rob_before + c.mt_vacuum
    vessel.current_rob_mt = rob_after
    db.add(RobEntry(
        vessel_id=c.vessel_id, operation_id=c.operation_id,
        entry_type=RobEntryType.replenishment, quantity_mt=c.mt_vacuum,
        rob_before_mt=rob_before, rob_after_mt=rob_after,
        recorded_by=user.id, source_description=_rob_marker(c),
        notes=f"Collected ship to ship from {c.source_vessel_name}",
        temperature_celsius=c.temperature,
    ))
    await db.flush()


async def _reverse_rob(c: StsCollection, db: AsyncSession) -> None:
    """Delete-and-subtract, the same reversal the Vessel BDN uses
    (vessel_bdn_service._reverse_rob_entry)."""
    vessel = await db.get(Vessel, c.vessel_id, with_for_update=True)
    if not vessel:
        return
    prior = list((await db.execute(
        select(RobEntry).where(and_(RobEntry.vessel_id == c.vessel_id, RobEntry.source_description == _rob_marker(c)))
    )).scalars().all())
    if not prior:
        return
    net = sum((e.quantity_mt for e in prior), Decimal("0"))
    vessel.current_rob_mt = (vessel.current_rob_mt or Decimal("0")) - net
    for e in prior:
        await db.delete(e)
    await db.flush()


def _audit(db: AsyncSession, user: User, c: StsCollection, action: str, changes: dict, reason: Optional[str] = None) -> None:
    db.add(AuditLog(
        user_id=user.id, operation_id=c.operation_id, action=action,
        entity_type="sts_collection", entity_id=c.id, changes=changes, reason=reason,
    ))


def _figures_dict(c: StsCollection) -> dict:
    return {k: (str(getattr(c, k)) if getattr(c, k) is not None else None)
            for k in ("gov", "gsv", "mt_vacuum", "density", "vcf", "temperature")}


def _set_figures(c: StsCollection, data: StsFigures) -> None:
    for k in ("gov", "gsv", "mt_vacuum", "density", "vcf", "temperature"):
        setattr(c, k, getattr(data, k))


class StsCollectionService:

    @staticmethod
    async def list_for_operation(operation_id: UUID, db: AsyncSession) -> List[StsCollection]:
        await _get_operation_or_404(operation_id, db)
        rows = list((await db.execute(
            _query().where(StsCollection.operation_id == operation_id).order_by(StsCollection.created_at)
        )).scalars().all())
        started = await _delivery_started_at(operation_id, db)
        return [_decorate(c, started) for c in rows]

    @staticmethod
    async def create(operation_id: UUID, data: StsCollectionCreate, user: User, db: AsyncSession) -> StsCollection:
        op = await _get_operation_or_404(operation_id, db)
        if op.type == OperationType.truck_only:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A truck-only operation has no barge to collect product with",
            )
        if op.status in (OperationStatus.cancelled, OperationStatus.archived):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Operation is {op.status.value}")
        vessel_id = data.vessel_id or op.vessel_id
        if not vessel_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This operation has no barge set — choose the vessel doing the collection",
            )
        if not await db.get(Vessel, vessel_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")

        c = StsCollection(
            operation_id=op.id, vessel_id=vessel_id,
            source_vessel_name=data.source_vessel_name, source_vessel_imo=data.source_vessel_imo,
            product_type=data.product_type, location=data.location, notes=data.notes,
            status="in_progress", hse={}, created_by=user.id,
        )
        db.add(c)
        await db.flush()
        _audit(db, user, c, "CREATE_STS_COLLECTION", {
            "source_vessel_name": c.source_vessel_name, "vessel_id": str(vessel_id),
        })
        await db.flush()
        return await _reload(c.id, db)

    @staticmethod
    async def update_details(collection_id: UUID, data: StsCollectionDetailsUpdate, user: User, db: AsyncSession) -> StsCollection:
        c = await _get_or_404(collection_id, db)
        _assert_live(c)
        changes = {}
        for k, v in data.model_dump(exclude_unset=True).items():
            if k == "source_vessel_name" and not v:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Source vessel name is required")
            if getattr(c, k) != v:
                changes[k] = {"from": getattr(c, k), "to": v}
                setattr(c, k, v)
        if changes:
            _audit(db, user, c, "UPDATE_STS_COLLECTION", changes)
            await db.flush()
        return await _reload(c.id, db)

    @staticmethod
    async def advance_stage(collection_id: UUID, data: StsStageAdvance, user: User, db: AsyncSession) -> StsCollection:
        c = await _get_or_404(collection_id, db)
        _assert_live(c)
        current = STS_STAGES.index(c.stage) if c.stage else -1
        target = STS_STAGES.index(data.stage)
        if target != current + 1:
            expected = STS_STAGES[current + 1] if current + 1 < len(STS_STAGES) else None
            detail = (f"Next stage is {_STAGE_LABEL[expected]}" if expected
                      else "All stages are already recorded")
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
        occurred = data.occurred_at or datetime.now(timezone.utc)
        if _aware(occurred) > datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Stage time cannot be in the future")
        setattr(c, _STAGE_COLUMN[data.stage], occurred)
        c.stage = data.stage
        _audit(db, user, c, "ADVANCE_STS_STAGE", {"stage": data.stage, "occurred_at": occurred.isoformat()})
        await db.flush()
        return await _reload(c.id, db)

    @staticmethod
    async def record_hse(collection_id: UUID, data: StsHseRecord, user: User, db: AsyncSession) -> StsCollection:
        """Same rules as a vessel run's HSE: recording never blocks; a second
        recording of the same phase is a correction — manager only, reason
        required, and the earlier checklist is kept in the audit entry."""
        c = await _get_or_404(collection_id, db)
        _assert_live(c)
        hse = dict(c.hse or {})
        prior = hse.get(data.phase)
        if prior:
            _forbid_unless_manager(user, "correct a recorded HSE checklist")
            if not data.reason or len(data.reason.strip()) < 5:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A reason is required to correct a recorded HSE checklist")
        hse[data.phase] = {
            "checklist": [i.model_dump() for i in data.checklist],
            "result": data.result.value,
            "notes": data.notes,
            "safety_officer": data.safety_officer,
            "conducted_by": str(user.id),
            "conducted_by_name": user.full_name,
            "conducted_at": datetime.now(timezone.utc).isoformat(),
        }
        c.hse = hse  # reassign so the JSONB change is detected
        _audit(db, user, c, "CORRECT_STS_HSE" if prior else "RECORD_STS_HSE",
               {"phase": data.phase, "result": data.result.value, "items": len(data.checklist), "previous": prior},
               reason=data.reason if prior else None)
        await db.flush()
        return await _reload(c.id, db)

    @staticmethod
    async def submit_figures(collection_id: UUID, data: StsFigures, user: User, db: AsyncSession) -> StsCollection:
        c = await _get_or_404(collection_id, db)
        _assert_live(c)
        if c.status not in ("in_progress", "rejected"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Receipt is already {c.status} — use Correct Figures to change it",
            )
        if not c.receipt_number:
            c.receipt_number = await generate_sts_receipt_number(db)
        _set_figures(c, data)
        resubmitted = c.status == "rejected"
        c.status = "pending"
        c.submitted_by = user.id
        c.submitted_at = datetime.now(timezone.utc)
        c.rejection_reason = None
        _audit(db, user, c, "SUBMIT_STS_RECEIPT", {"receipt_number": c.receipt_number, **_figures_dict(c), "resubmitted": resubmitted})
        await db.flush()

        op = await db.get(Operation, c.operation_id)
        for r in await reviewers(db):
            if r.id == user.id:
                continue
            await notify(
                db=db, user_id=r.id, type_="system",
                title="Ship-to-ship receipt to review",
                message=f"{c.receipt_number} on {op.operation_number}: {c.mt_vacuum} MT(vac) collected from {c.source_vessel_name}.",
                operation_id=c.operation_id, action_url=f"/operations/{c.operation_id}", channels=["in_app"],
            )
        return await _reload(c.id, db)

    @staticmethod
    async def approve(collection_id: UUID, user: User, db: AsyncSession) -> StsCollection:
        _forbid_unless_manager(user, "approve a ship-to-ship receipt")
        c = await _get_or_404(collection_id, db)
        if c.status != "pending":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot approve a receipt that is {c.status}")
        c.status = "approved"
        c.reviewed_by = user.id
        c.reviewed_at = datetime.now(timezone.utc)
        await _credit_rob(c, user, db)
        _audit(db, user, c, "APPROVE_STS_RECEIPT", {"status": {"from": "pending", "to": "approved"}, "mt_vacuum": str(c.mt_vacuum)})
        await db.flush()
        if c.submitted_by and c.submitted_by != user.id:
            await notify_if_active(
                db, c.submitted_by, type_="system", title="Ship-to-ship receipt approved",
                message=f"{c.receipt_number} ({c.mt_vacuum} MT(vac) from {c.source_vessel_name}) was approved by {user.full_name}.",
                operation_id=c.operation_id, action_url=f"/operations/{c.operation_id}", channels=["in_app"],
            )
        return await _reload(c.id, db)

    @staticmethod
    async def reject(collection_id: UUID, reason: str, user: User, db: AsyncSession) -> StsCollection:
        _forbid_unless_manager(user, "reject a ship-to-ship receipt")
        if len(reason) < 10:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Rejection reason must be at least 10 characters")
        c = await _get_or_404(collection_id, db)
        if c.status != "pending":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot reject a receipt that is {c.status}")
        c.status = "rejected"
        c.reviewed_by = user.id
        c.reviewed_at = datetime.now(timezone.utc)
        c.rejection_reason = reason
        _audit(db, user, c, "REJECT_STS_RECEIPT", {"status": {"from": "pending", "to": "rejected"}}, reason=reason)
        await db.flush()
        if c.submitted_by and c.submitted_by != user.id:
            await notify_if_active(
                db, c.submitted_by, type_="system", title="Ship-to-ship receipt rejected",
                message=f"{c.receipt_number} was rejected by {user.full_name}: {reason}",
                operation_id=c.operation_id, action_url=f"/operations/{c.operation_id}", channels=["in_app"],
            )
        return await _reload(c.id, db)

    @staticmethod
    async def correct_figures(collection_id: UUID, data: StsFiguresCorrection, user: User, db: AsyncSession) -> StsCollection:
        """Manager correction of submitted figures, with a reason. On an
        approved receipt the barge's credit is reversed and re-applied with
        the corrected MT(vac), so ROB always matches the figure of record."""
        _forbid_unless_manager(user, "correct ship-to-ship receipt figures")
        c = await _get_or_404(collection_id, db)
        _assert_live(c)
        if c.status == "in_progress":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nothing submitted yet — submit the receipt first")
        before = _figures_dict(c)
        if c.status == "approved":
            await _reverse_rob(c, db)
        _set_figures(c, data)
        if c.status == "approved":
            await _credit_rob(c, user, db)
        _audit(db, user, c, "CORRECT_STS_RECEIPT", {"from": before, "to": _figures_dict(c)}, reason=data.reason)
        await db.flush()
        return await _reload(c.id, db)

    @staticmethod
    async def cancel(collection_id: UUID, reason: str, user: User, db: AsyncSession) -> StsCollection:
        """Cancelled, never deleted — the row and its audit trail stay. If it
        had been approved, its credit to the barge is taken back off."""
        _forbid_unless_manager(user, "cancel a ship-to-ship collection")
        c = await _get_or_404(collection_id, db)
        _assert_live(c)
        was = c.status
        if was == "approved":
            await _reverse_rob(c, db)
        c.status = "cancelled"
        c.cancelled_at = datetime.now(timezone.utc)
        c.cancel_reason = reason
        _audit(db, user, c, "CANCEL_STS_COLLECTION", {"status": {"from": was, "to": "cancelled"}}, reason=reason)
        await db.flush()
        return await _reload(c.id, db)
