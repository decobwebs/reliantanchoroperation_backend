from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from uuid import UUID
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.truck import Truck, TruckOperation, TruckSafetyAudit
from app.models.operation import Operation, TaskAssignment, TruckFeedback
from app.models.audit import AuditLog
from app.models.user import User
from app.models.bdn import RobEntry
from app.models.vessel import Vessel
from app.models.enums import (
    UserRole, TruckStatus, TruckOpStatus, FeedbackStatus, OperationStatus, RobEntryType
)
from app.schemas.truck import (
    TruckCreate, TruckUpdate,
    TruckOperationCreate, TruckOperationUpdate,
    TruckFeedbackCreate,
    TruckSafetyAuditCreate,
    TruckWaybillLinkRequest,
)
from app.services.notification_service import notify
from app.services.state_machine import StateMachine, StateMachineError
from app.services.audit_diff import capture_diff
from app.models.operation import OperationStatusHistory
from app.models.truck import TruckWaiver
from app.models.enums import TruckWaiverStatus, AuditPhase


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(
        select(Operation).where(
            and_(Operation.id == operation_id, Operation.deleted_at.is_(None))
        )
    )
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return operation


async def _assert_lo_assigned(operation_id: UUID, current_user: User, db: AsyncSession) -> None:
    """Raise 403 if the logistics officer is not assigned to this operation."""
    if current_user.role in (UserRole.bunker_manager, UserRole.ops_supervisor):
        return
    result = await db.execute(
        select(TaskAssignment).where(
            and_(
                TaskAssignment.operation_id == operation_id,
                TaskAssignment.assigned_to == current_user.id,
            )
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to this operation",
        )


async def _transition_operation(
    operation: Operation,
    to_status: OperationStatus,
    current_user: User,
    db: AsyncSession,
    reason: str = "",
) -> None:
    """Silently transitions an operation status, writing history but not raising on SM errors."""
    try:
        StateMachine.validate_transition(
            operation.type, operation.status, to_status, current_user.role
        )
    except StateMachineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    from_status = operation.status
    operation.status = to_status
    operation.updated_at = datetime.utcnow()

    history = OperationStatusHistory(
        operation_id=operation.id,
        from_status=from_status,
        to_status=to_status,
        changed_by=current_user.id,
        reason=reason,
        metadata_={},
    )
    db.add(history)


class TruckService:

    # ── Truck registry ────────────────────────────────────────────────────────

    @staticmethod
    async def list_trucks(
        db: AsyncSession,
        active_only: bool = True,
    ) -> List[Truck]:
        conditions = []
        if active_only:
            conditions.append(Truck.is_active == True)

        stmt = (
            select(Truck)
            .where(and_(*conditions) if conditions else True)
            .order_by(Truck.created_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_truck(
        data: TruckCreate,
        current_user: User,
        db: AsyncSession,
    ) -> Truck:
        # Check for duplicate truck number
        existing = await db.execute(
            select(Truck).where(Truck.truck_number == data.truck_number)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Truck number '{data.truck_number}' already exists",
            )

        truck = Truck(
            truck_number=data.truck_number,
            capacity_mt=data.capacity_mt,
            chassis_number=data.chassis_number,
            current_location=data.current_location,
            notes=data.notes,
            status=TruckStatus.available,
            is_active=True,
        )
        db.add(truck)
        await db.flush()

        audit = AuditLog(
            user_id=current_user.id,
            action="CREATE_TRUCK",
            entity_type="truck",
            entity_id=truck.id,
            changes={"truck_number": data.truck_number, "capacity_mt": str(data.capacity_mt)},
        )
        db.add(audit)

        await db.flush()
        await db.refresh(truck)
        return truck

    @staticmethod
    async def update_truck(
        truck_id: UUID,
        data: TruckUpdate,
        current_user: User,
        db: AsyncSession,
    ) -> Truck:
        result = await db.execute(select(Truck).where(Truck.id == truck_id))
        truck = result.scalar_one_or_none()
        if not truck:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck not found")

        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        changes = capture_diff(truck, update_data)

        truck.updated_at = datetime.utcnow()

        audit = AuditLog(
            user_id=current_user.id,
            action="UPDATE_TRUCK",
            entity_type="truck",
            entity_id=truck.id,
            changes=changes,
            reason=data.reason,
        )
        db.add(audit)

        await db.flush()
        await db.refresh(truck)
        return truck

    # ── Waivers (regulatory / BFL truck numbers) ──────────────────────────────

    @staticmethod
    async def bulk_add_waivers(
        numbers: List[str],
        current_user: User,
        db: AsyncSession,
    ) -> Dict[str, List[str]]:
        """Bunker Manager (admin) bulk-adds waiver numbers up front, before sourcing starts."""
        existing_result = await db.execute(
            select(TruckWaiver.waybill_truck_number).where(TruckWaiver.waybill_truck_number.in_(numbers))
        )
        existing = {n for (n,) in existing_result.all()}

        created: List[str] = []
        skipped: List[str] = []
        for number in numbers:
            if number in existing:
                skipped.append(number)
                continue
            db.add(TruckWaiver(waybill_truck_number=number, added_by=current_user.id))
            created.append(number)
            existing.add(number)  # guard against duplicates within the same batch

        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id,
            action="BULK_ADD_TRUCK_WAIVERS",
            entity_type="truck_waiver",
            changes={"created_count": len(created), "skipped_count": len(skipped)},
        ))

        await db.commit()
        return {"created": created, "skipped_duplicates": skipped}

    @staticmethod
    async def list_waivers(
        db: AsyncSession,
        status_filter: Optional[TruckWaiverStatus] = None,
    ) -> List[TruckWaiver]:
        stmt = select(TruckWaiver).order_by(TruckWaiver.created_at.desc())
        if status_filter:
            stmt = stmt.where(TruckWaiver.status == status_filter)
        result = await db.execute(stmt)
        waivers = list(result.scalars().all())

        # Attach linked-truck info (at most one truck_op per waiver today — no
        # release/reuse mechanism exists, so "current link" is also "full history").
        waiver_ids = [w.id for w in waivers if w.status == TruckWaiverStatus.linked]
        linked_by_waiver: Dict[Any, TruckOperation] = {}
        if waiver_ids:
            to_result = await db.execute(
                select(TruckOperation)
                .options(selectinload(TruckOperation.truck), selectinload(TruckOperation.operation))
                .where(TruckOperation.waiver_id.in_(waiver_ids))
            )
            for to in to_result.scalars().all():
                linked_by_waiver[to.waiver_id] = to

        for w in waivers:
            to = linked_by_waiver.get(w.id)
            w.linked_truck_number = to.truck.truck_number if to and to.truck else None
            w.linked_operation_id = to.operation_id if to else None
            w.linked_operation_number = to.operation.operation_number if to and to.operation else None
            w.linked_driver_name = to.driver_name if to else None
            w.linked_at = to.waybill_linked_at if to else None

        return waivers

    @staticmethod
    async def link_waybill(
        operation_id: UUID,
        truck_op_id: UUID,
        data: TruckWaybillLinkRequest,
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        """The doc's key moment: waiver number + original plate + driver come
        together, only once the waybill is generated — not during sourcing."""
        await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        result = await db.execute(
            select(TruckOperation)
            .options(selectinload(TruckOperation.truck), selectinload(TruckOperation.safety_audits))
            .where(and_(TruckOperation.id == truck_op_id, TruckOperation.operation_id == operation_id))
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        waiver_result = await db.execute(select(TruckWaiver).where(TruckWaiver.id == data.waiver_id))
        waiver = waiver_result.scalar_one_or_none()
        if not waiver:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Waiver number not found")
        # A waiver already linked to THIS truck_op is fine — editing driver/vendor/
        # doc-number details after the initial link must not be blocked. Only a
        # waiver linked elsewhere (or linked to a different truck_op) is a conflict.
        if waiver.status != TruckWaiverStatus.available and truck_op.waiver_id != waiver.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Waiver number '{waiver.waybill_truck_number}' is already linked to another truck",
            )

        truck_op.waiver_id = waiver.id
        truck_op.driver_name = data.driver_name
        truck_op.driver_phone = data.driver_phone
        truck_op.vendor_name = data.vendor_name
        truck_op.waybill_document_number = data.waybill_document_number
        if data.waybill_number:
            truck_op.waybill_number = data.waybill_number
        truck_op.waybill_linked_at = datetime.utcnow()
        truck_op.updated_at = datetime.utcnow()
        waiver.status = TruckWaiverStatus.linked

        # Mirror onto the Truck row — see the same note in add_truck_to_operation.
        if truck_op.truck:
            truck_op.truck.driver_name = data.driver_name
            truck_op.truck.driver_phone = data.driver_phone

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="LINK_WAYBILL",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes={
                "waiver_number": waiver.waybill_truck_number,
                "driver_name": data.driver_name,
            },
        ))

        await db.commit()
        await db.refresh(truck_op)
        return truck_op

    # ── Truck operations on a specific operation ──────────────────────────────

    @staticmethod
    async def list_truck_operations(
        operation_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> List[TruckOperation]:
        await _get_operation_or_404(operation_id, db)

        stmt = (
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(TruckOperation.operation_id == operation_id)
            .order_by(TruckOperation.created_at.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def add_truck_to_operation(
        operation_id: UUID,
        data: TruckOperationCreate,
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        # Verify truck exists and is active
        truck_result = await db.execute(
            select(Truck).where(and_(Truck.id == data.truck_id, Truck.is_active == True))
        )
        truck = truck_result.scalar_one_or_none()
        if not truck:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck not found or inactive")

        # Prevent duplicate: same truck can only have one non-cancelled record per operation
        dup_result = await db.execute(
            select(TruckOperation).where(
                and_(
                    TruckOperation.operation_id == operation_id,
                    TruckOperation.truck_id == data.truck_id,
                    TruckOperation.status != TruckOpStatus.cancelled,
                )
            )
        )
        if dup_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This truck is already assigned to this operation",
            )

        truck_op = TruckOperation(
            operation_id=operation_id,
            truck_id=data.truck_id,
            logged_by=current_user.id,
            quantity_loaded_mt=data.quantity_loaded_mt,
            loading_location=data.loading_location,
            discharge_location=data.discharge_location,
            destination_vessel_id=data.destination_vessel_id,
            driver_name=data.driver_name,
            driver_phone=data.driver_phone,
            vendor_name=data.vendor_name,
            notes=data.notes,
            status=TruckOpStatus.pending,
        )
        db.add(truck_op)

        # Mirror onto the Truck row as a "last known driver" cache — several
        # existing UI surfaces (fleet list, fleet/truck detail, task list) still
        # display Truck.driver_name directly. The per-assignment value on
        # TruckOperation is authoritative for a given operation; this is just a
        # convenience snapshot so those surfaces don't go silently blank now that
        # driver capture moved off the Truck master.
        if data.driver_name:
            truck.driver_name = data.driver_name
        if data.driver_phone:
            truck.driver_phone = data.driver_phone

        await db.flush()

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="ADD_TRUCK_TO_OPERATION",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes={"truck_id": str(data.truck_id)},
        )
        db.add(audit)

        await db.flush()

        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(TruckOperation.id == truck_op.id)
        )
        return result.scalar_one()

    @staticmethod
    async def update_truck_operation(
        operation_id: UUID,
        truck_op_id: UUID,
        data: TruckOperationUpdate,
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(
                and_(
                    TruckOperation.id == truck_op_id,
                    TruckOperation.operation_id == operation_id,
                )
            )
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        update_data = data.model_dump(exclude_unset=True)
        changes = capture_diff(truck_op, update_data)

        truck_op.updated_at = datetime.utcnow()

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="UPDATE_TRUCK_OPERATION",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes=changes,
        )
        db.add(audit)

        await db.flush()
        return truck_op

    # ── Safety Audit ─────────────────────────────────────────────────────────

    @staticmethod
    async def submit_safety_audit(
        operation_id: UUID,
        truck_op_id: UUID,
        data: TruckSafetyAuditCreate,
        current_user: User,
        db: AsyncSession,
    ) -> TruckSafetyAudit:
        await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        result = await db.execute(
            select(TruckOperation)
            .options(selectinload(TruckOperation.safety_audits))
            .where(
                and_(
                    TruckOperation.id == truck_op_id,
                    TruckOperation.operation_id == operation_id,
                )
            )
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        conducted_at = data.conducted_at or datetime.utcnow()

        existing_for_phase = next(
            (a for a in (truck_op.safety_audits or []) if a.phase == data.phase), None
        )
        if existing_for_phase:
            audit_record = existing_for_phase
            audit_record.conducted_by = current_user.id
            audit_record.conductor_name = current_user.full_name
            audit_record.conducted_at = conducted_at
            audit_record.result = data.result
            audit_record.checklist = data.checklist or []
            audit_record.header = data.header or {}
            audit_record.notes = data.notes
            audit_record.updated_at = datetime.utcnow()
        else:
            audit_record = TruckSafetyAudit(
                truck_op_id=truck_op_id,
                phase=data.phase,
                operation_id=operation_id,
                truck_id=truck_op.truck_id,
                conducted_by=current_user.id,
                conductor_name=current_user.full_name,
                conducted_at=conducted_at,
                result=data.result,
                checklist=data.checklist or [],
                header=data.header or {},
                notes=data.notes,
            )
            db.add(audit_record)

        audit_log = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="SUBMIT_SAFETY_AUDIT",
            entity_type="truck_safety_audit",
            entity_id=truck_op_id,
            changes={"phase": data.phase.value, "result": str(data.result)},
        )
        db.add(audit_log)

        await db.flush()
        await db.refresh(audit_record)
        return audit_record

    @staticmethod
    async def waive_audit_item(
        operation_id: UUID,
        truck_op_id: UUID,
        phase: AuditPhase,
        item: str,
        waiver_notes: Optional[str],
        current_user: User,
        db: AsyncSession,
    ) -> TruckSafetyAudit:
        await _get_operation_or_404(operation_id, db)

        result = await db.execute(
            select(TruckSafetyAudit).where(
                and_(TruckSafetyAudit.truck_op_id == truck_op_id, TruckSafetyAudit.phase == phase)
            )
        )
        audit_record = result.scalar_one_or_none()
        if not audit_record:
            raise HTTPException(status_code=404, detail="Safety audit not found")

        # Only failed items can be waived
        checklist = audit_record.checklist or []
        item_found = any(c.get("item") == item for c in checklist)
        if not item_found:
            raise HTTPException(status_code=422, detail=f"Checklist item '{item}' not found in this audit")
        item_passed = next((c.get("passed", False) for c in checklist if c.get("item") == item), False)
        if item_passed:
            raise HTTPException(status_code=422, detail="Cannot waive a checklist item that already passed")

        # Upsert waiver
        existing = [w for w in (audit_record.waivers or []) if w.get("item") != item]
        existing.append({
            "item": item,
            "waived_by": str(current_user.id),
            "waived_by_name": current_user.full_name,
            "waived_at": datetime.utcnow().isoformat(),
            "notes": waiver_notes,
        })
        audit_record.waivers = existing
        audit_record.updated_at = datetime.utcnow()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="WAIVE_AUDIT_ITEM",
            entity_type="truck_safety_audit",
            entity_id=truck_op_id,
            changes={"item": item, "notes": waiver_notes},
        ))

        await db.flush()
        await db.refresh(audit_record)
        return audit_record

    # ── Lifecycle transitions ─────────────────────────────────────────────────

    @staticmethod
    async def start_transit(
        operation_id: UUID,
        truck_op_id: UUID,
        gps_lat: Optional[Decimal],
        gps_lng: Optional[Decimal],
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(
                and_(
                    TruckOperation.id == truck_op_id,
                    TruckOperation.operation_id == operation_id,
                )
            )
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        if truck_op.status != TruckOpStatus.pending and truck_op.status != TruckOpStatus.loading:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot start transit from status '{truck_op.status.value}'",
            )

        truck_op.transit_start_at = datetime.utcnow()
        truck_op.status = TruckOpStatus.in_transit
        if gps_lat is not None:
            truck_op.gps_start_lat = gps_lat
        if gps_lng is not None:
            truck_op.gps_start_lng = gps_lng
        truck_op.updated_at = datetime.utcnow()

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="START_TRANSIT",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes={"status": "in_transit", "transit_start_at": datetime.utcnow().isoformat()},
        )
        db.add(audit)

        await db.flush()
        return truck_op

    @staticmethod
    async def end_transit(
        operation_id: UUID,
        truck_op_id: UUID,
        gps_lat: Optional[Decimal],
        gps_lng: Optional[Decimal],
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(
                and_(
                    TruckOperation.id == truck_op_id,
                    TruckOperation.operation_id == operation_id,
                )
            )
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        if truck_op.status != TruckOpStatus.in_transit:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot end transit from status '{truck_op.status.value}'",
            )

        truck_op.transit_end_at = datetime.utcnow()
        truck_op.status = TruckOpStatus.arrived
        if gps_lat is not None:
            truck_op.gps_end_lat = gps_lat
        if gps_lng is not None:
            truck_op.gps_end_lng = gps_lng
        truck_op.updated_at = datetime.utcnow()

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="END_TRANSIT",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes={"status": "arrived", "transit_end_at": datetime.utcnow().isoformat()},
        )
        db.add(audit)

        await db.flush()
        return truck_op

    @staticmethod
    async def start_discharge(
        operation_id: UUID,
        truck_op_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(
                and_(
                    TruckOperation.id == truck_op_id,
                    TruckOperation.operation_id == operation_id,
                )
            )
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        if truck_op.status in (TruckOpStatus.discharging, TruckOpStatus.completed):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot start discharge from status '{truck_op.status.value}'",
            )

        truck_op.discharge_start_at = datetime.utcnow()
        truck_op.status = TruckOpStatus.discharging
        truck_op.updated_at = datetime.utcnow()

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="START_DISCHARGE",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes={"status": "discharging", "discharge_start_at": datetime.utcnow().isoformat()},
        )
        db.add(audit)

        await db.flush()
        return truck_op

    @staticmethod
    async def end_discharge(
        operation_id: UUID,
        truck_op_id: UUID,
        body,  # TruckDischargeEndRequest
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(
                and_(
                    TruckOperation.id == truck_op_id,
                    TruckOperation.operation_id == operation_id,
                )
            )
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        if truck_op.status == TruckOpStatus.completed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Discharge already completed",
            )

        quantity_discharged_mt = body.quantity_discharged_mt
        truck_op.discharge_end_at = body.discharge_end_at or datetime.utcnow()
        truck_op.quantity_discharged_mt = quantity_discharged_mt
        if body.quantity_remaining_mt is not None:
            truck_op.quantity_remaining_mt = body.quantity_remaining_mt
        if body.spillage_mt is not None:
            truck_op.spillage_mt = body.spillage_mt
        if body.temperature_celsius is not None:
            truck_op.temperature_celsius = body.temperature_celsius
        if body.notes:
            truck_op.notes = body.notes

        # Update destination vessel (supervisor may change it at discharge time)
        if body.destination_vessel_id is not None:
            truck_op.destination_vessel_id = body.destination_vessel_id
            truck_op.destination_vessel_name = None  # clear free-text if system vessel set
        elif body.destination_vessel_name:
            truck_op.destination_vessel_name = body.destination_vessel_name
            truck_op.destination_vessel_id = None  # clear FK if free-text vessel provided

        truck_op.status = TruckOpStatus.completed
        truck_op.updated_at = datetime.utcnow()

        # Calculate variance
        if truck_op.quantity_loaded_mt is not None:
            truck_op.variance_mt = truck_op.quantity_loaded_mt - quantity_discharged_mt

        # Set discharge approval gate:
        # - vessel specified → False (pending BM approval before ROB is written)
        # - no vessel → None (approval not applicable)
        has_vessel = bool(truck_op.destination_vessel_id or truck_op.destination_vessel_name)
        truck_op.discharge_approved = False if has_vessel else None

        vessel_label = ""
        if truck_op.destination_vessel_id:
            vessel_label = f" → system vessel (ID: {str(truck_op.destination_vessel_id)[:8]})"
        elif truck_op.destination_vessel_name:
            vessel_label = f" → {truck_op.destination_vessel_name}"

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="END_DISCHARGE",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes={
                "status": "completed",
                "quantity_discharged_mt": str(quantity_discharged_mt),
                "variance_mt": str(truck_op.variance_mt) if truck_op.variance_mt is not None else None,
                "destination": vessel_label or "no vessel specified",
                "discharge_approved": truck_op.discharge_approved,
            },
        ))

        await db.flush()
        return truck_op

    @staticmethod
    async def approve_discharge(
        operation_id: UUID,
        truck_op_id: UUID,
        notes: Optional[str],
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        await _get_operation_or_404(operation_id, db)

        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(
                and_(
                    TruckOperation.id == truck_op_id,
                    TruckOperation.operation_id == operation_id,
                )
            )
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        if truck_op.discharge_approved is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Discharge approval not required — no vessel was specified",
            )
        if truck_op.discharge_approved is True:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Discharge already approved",
            )

        truck_op.discharge_approved = True
        truck_op.discharge_approved_by = current_user.id
        truck_op.discharge_approved_at = datetime.utcnow()
        truck_op.updated_at = datetime.utcnow()

        # Write ROB entry now that BM has approved (system vessels only)
        if truck_op.destination_vessel_id:
            dv_result = await db.execute(
                select(Vessel)
                .where(Vessel.id == truck_op.destination_vessel_id)
                .with_for_update()
            )
            dest_vessel = dv_result.scalar_one_or_none()
            if dest_vessel and truck_op.quantity_discharged_mt:
                qty = truck_op.quantity_discharged_mt
                rob_before = dest_vessel.current_rob_mt
                rob_after = rob_before + qty
                dest_vessel.current_rob_mt = rob_after
                truck_label = truck_op.truck.truck_number if truck_op.truck else str(truck_op.id)[:8]
                db.add(RobEntry(
                    vessel_id=dest_vessel.id,
                    operation_id=operation_id,
                    entry_type=RobEntryType.replenishment,
                    quantity_mt=qty,
                    rob_before_mt=rob_before,
                    rob_after_mt=rob_after,
                    recorded_by=current_user.id,
                    truck_operation_id=truck_op.id,
                    source_description=f"Truck delivery (BM approved): {truck_label}",
                    notes=(
                        f"Approved by BM {current_user.full_name}. "
                        f"Discharged {float(qty):.3f} L from truck {truck_label} into vessel {dest_vessel.vessel_name}."
                        + (f" Notes: {notes}" if notes else "")
                    ),
                ))

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="APPROVE_DISCHARGE",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes={
                "approved_by": current_user.full_name,
                "approved_by_role": current_user.role.value,
                "destination_vessel_id": str(truck_op.destination_vessel_id) if truck_op.destination_vessel_id else None,
                "destination_vessel_name": truck_op.destination_vessel_name,
                "quantity_discharged_mt": str(truck_op.quantity_discharged_mt),
                "notes": notes,
            },
        ))

        await db.flush()
        return truck_op

    @staticmethod
    async def edit_discharge_record(
        operation_id: UUID,
        truck_op_id: UUID,
        data,  # DischargeEditRequest
        current_user: User,
        db: AsyncSession,
    ) -> TruckOperation:
        await _get_operation_or_404(operation_id, db)

        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(
                and_(
                    TruckOperation.id == truck_op_id,
                    TruckOperation.operation_id == operation_id,
                )
            )
        )
        truck_op = result.scalar_one_or_none()
        if not truck_op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")

        if truck_op.status != TruckOpStatus.completed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Can only edit discharge records for completed truck operations",
            )

        was_approved = truck_op.discharge_approved is True
        old_vessel_id = truck_op.destination_vessel_id
        old_qty = truck_op.quantity_discharged_mt
        changes: dict = {"edited_by": current_user.full_name, "edited_by_role": "bunker_manager"}

        if data.quantity_discharged_mt is not None:
            changes["quantity_discharged_mt"] = {"from": str(old_qty), "to": str(data.quantity_discharged_mt)}
            truck_op.quantity_discharged_mt = data.quantity_discharged_mt
            if truck_op.quantity_loaded_mt is not None:
                truck_op.variance_mt = truck_op.quantity_loaded_mt - data.quantity_discharged_mt

        if data.spillage_mt is not None:
            changes["spillage_mt"] = str(data.spillage_mt)
            truck_op.spillage_mt = data.spillage_mt

        if data.temperature_celsius is not None:
            changes["temperature_celsius"] = str(data.temperature_celsius)
            truck_op.temperature_celsius = data.temperature_celsius

        vessel_changed = False
        if data.destination_vessel_id is not None:
            changes["destination_vessel_id"] = {"from": str(old_vessel_id), "to": str(data.destination_vessel_id)}
            truck_op.destination_vessel_id = data.destination_vessel_id
            truck_op.destination_vessel_name = None
            vessel_changed = old_vessel_id != data.destination_vessel_id
        elif data.destination_vessel_name is not None:
            changes["destination_vessel_name"] = data.destination_vessel_name
            if truck_op.destination_vessel_id:
                changes["destination_vessel_id_cleared"] = str(truck_op.destination_vessel_id)
            truck_op.destination_vessel_name = data.destination_vessel_name
            truck_op.destination_vessel_id = None
            vessel_changed = True

        if data.notes is not None:
            truck_op.notes = data.notes

        truck_op.updated_at = datetime.utcnow()

        # If discharge was already approved AND vessel/qty changed on a tracked vessel → write correction
        new_qty = truck_op.quantity_discharged_mt
        if was_approved and old_vessel_id:
            qty_changed = data.quantity_discharged_mt is not None and data.quantity_discharged_mt != old_qty
            if vessel_changed or qty_changed:
                # Reverse the old entry on the old vessel
                old_vessel_result = await db.execute(
                    select(Vessel).where(Vessel.id == old_vessel_id).with_for_update()
                )
                old_vessel = old_vessel_result.scalar_one_or_none()
                if old_vessel and old_qty:
                    rob_before_correction = old_vessel.current_rob_mt
                    rob_after_correction = rob_before_correction - old_qty
                    old_vessel.current_rob_mt = rob_after_correction
                    truck_label = truck_op.truck.truck_number if truck_op.truck else str(truck_op.id)[:8]
                    db.add(RobEntry(
                        vessel_id=old_vessel.id,
                        operation_id=operation_id,
                        entry_type=RobEntryType.correction,
                        quantity_mt=-old_qty,
                        rob_before_mt=rob_before_correction,
                        rob_after_mt=rob_after_correction,
                        recorded_by=current_user.id,
                        truck_operation_id=truck_op.id,
                        source_description=f"BM correction — reversed truck delivery: {truck_label}",
                        notes=f"BM {current_user.full_name} edited discharge record. Reversed previous delivery of {float(old_qty):.3f} L.",
                    ))

                # Write new entry if new vessel is in system
                new_vessel_id = truck_op.destination_vessel_id
                if new_vessel_id and new_qty:
                    new_vessel_result = await db.execute(
                        select(Vessel).where(Vessel.id == new_vessel_id).with_for_update()
                    )
                    new_vessel = new_vessel_result.scalar_one_or_none()
                    if new_vessel:
                        rob_before_new = new_vessel.current_rob_mt
                        rob_after_new = rob_before_new + new_qty
                        new_vessel.current_rob_mt = rob_after_new
                        db.add(RobEntry(
                            vessel_id=new_vessel.id,
                            operation_id=operation_id,
                            entry_type=RobEntryType.replenishment,
                            quantity_mt=new_qty,
                            rob_before_mt=rob_before_new,
                            rob_after_mt=rob_after_new,
                            recorded_by=current_user.id,
                            truck_operation_id=truck_op.id,
                            source_description=f"BM correction — updated truck delivery: {truck_label}",
                            notes=f"BM {current_user.full_name} edited discharge record. New delivery: {float(new_qty):.3f} L.",
                        ))
            elif qty_changed and not vessel_changed and old_vessel_id:
                # Same vessel, qty changed only — write a correction for the difference
                same_vessel_result = await db.execute(
                    select(Vessel).where(Vessel.id == old_vessel_id).with_for_update()
                )
                same_vessel = same_vessel_result.scalar_one_or_none()
                if same_vessel and old_qty and new_qty:
                    diff = new_qty - old_qty
                    rob_before_diff = same_vessel.current_rob_mt
                    rob_after_diff = rob_before_diff + diff
                    same_vessel.current_rob_mt = rob_after_diff
                    truck_label = truck_op.truck.truck_number if truck_op.truck else str(truck_op.id)[:8]
                    db.add(RobEntry(
                        vessel_id=same_vessel.id,
                        operation_id=operation_id,
                        entry_type=RobEntryType.correction,
                        quantity_mt=diff,
                        rob_before_mt=rob_before_diff,
                        rob_after_mt=rob_after_diff,
                        recorded_by=current_user.id,
                        truck_operation_id=truck_op.id,
                        source_description=f"BM quantity correction: {truck_label}",
                        notes=f"BM {current_user.full_name} corrected discharge qty from {float(old_qty):.3f} to {float(new_qty):.3f} L.",
                    ))

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="BM_EDITED_DISCHARGE_RECORD",
            entity_type="truck_operation",
            entity_id=truck_op.id,
            changes=changes,
        ))

        await db.flush()
        return truck_op

    @staticmethod
    async def set_trucks_required(
        operation_id: UUID,
        trucks_required: int,
        current_user: User,
        db: AsyncSession,
    ) -> Operation:
        operation = await _get_operation_or_404(operation_id, db)
        old_val = operation.trucks_required
        operation.trucks_required = trucks_required
        operation.updated_at = datetime.utcnow()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="SET_TRUCKS_REQUIRED",
            entity_type="operation",
            entity_id=operation_id,
            changes={"trucks_required": {"from": str(old_val), "to": str(trucks_required)}},
        ))

        await db.commit()
        await db.refresh(operation)
        return operation

    # ── Feedback workflow ─────────────────────────────────────────────────────

    @staticmethod
    async def submit_feedback(
        operation_id: UUID,
        data: TruckFeedbackCreate,
        current_user: User,
        db: AsyncSession,
    ) -> TruckFeedback:
        operation = await _get_operation_or_404(operation_id, db)
        await _assert_lo_assigned(operation_id, current_user, db)

        feedback = TruckFeedback(
            operation_id=operation_id,
            submitted_by=current_user.id,
            truck_ids=[str(tid) for tid in data.truck_ids],
            readiness_summary=data.readiness_summary,
            truck_details=data.truck_details,
            status=FeedbackStatus.pending,
            version=1,
        )
        db.add(feedback)
        await db.flush()

        # Only a submission from one of the states that can actually reach
        # feedback_submitted gates operation activation (per state_machine.py,
        # that's exclusively awaiting_feedback and feedback_rejected — NOT
        # merely "not active": an operation past active (pfi_linked,
        # payment_processing, vessel_operations, ...) also satisfies "!= active"
        # but feedback_submitted is not a valid transition from any of those,
        # so a naive "!= active" guard would 422 on the 3rd/4th incremental
        # batch instead of just recording it). Once the operation has moved
        # on, this is purely an incremental readiness batch — record it, no
        # status transition.
        if operation.status in (OperationStatus.awaiting_feedback, OperationStatus.feedback_rejected):
            await _transition_operation(
                operation, OperationStatus.feedback_submitted, current_user, db,
                reason="Feedback submitted by logistics officer"
            )

        # Notify BM — find all bunker managers
        bm_result = await db.execute(
            select(User).where(User.role == UserRole.bunker_manager)
        )
        bm_users = bm_result.scalars().all()
        for bm in bm_users:
            await notify(
                db=db,
                user_id=bm.id,
                type_="approval_needed",
                title="Truck Feedback Submitted",
                message=f"Logistics officer {current_user.full_name} has submitted truck feedback for operation {operation.operation_number}",
                priority="high",
                operation_id=operation_id,
                action_url=f"/operations/{operation_id}/feedback/{feedback.id}",
            )

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="SUBMIT_FEEDBACK",
            entity_type="truck_feedback",
            entity_id=feedback.id,
            changes={"status": "pending", "truck_count": len(data.truck_ids)},
        )
        db.add(audit)

        await db.flush()
        await db.refresh(feedback)
        return feedback

    @staticmethod
    async def list_feedback(
        operation_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> List[TruckFeedback]:
        await _get_operation_or_404(operation_id, db)

        stmt = (
            select(TruckFeedback)
            .where(TruckFeedback.operation_id == operation_id)
            .order_by(TruckFeedback.submitted_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def approve_feedback(
        operation_id: UUID,
        feedback_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> TruckFeedback:
        operation = await _get_operation_or_404(operation_id, db)

        result = await db.execute(
            select(TruckFeedback).where(
                and_(
                    TruckFeedback.id == feedback_id,
                    TruckFeedback.operation_id == operation_id,
                )
            )
        )
        feedback = result.scalar_one_or_none()
        if not feedback:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback not found")

        # Idempotent: already-approved feedback is a no-op (handles double-clicks
        # and stale UI that still shows a "pending" card after activation).
        if feedback.status == FeedbackStatus.approved:
            return feedback

        if feedback.status == FeedbackStatus.rejected:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This feedback was rejected and cannot be approved. Ask the officer to resubmit.",
            )

        feedback.status = FeedbackStatus.approved
        feedback.reviewed_by = current_user.id
        feedback.reviewed_at = datetime.utcnow()

        if operation.trucks_required is not None:
            operation.trucks_required = max(0, operation.trucks_required - len(feedback.truck_ids))

        # Activate the operation ONLY if it is still awaiting this approval.
        # If it has already advanced (e.g. it was moved via the status modal),
        # just record the approval instead of forcing an illegal transition.
        activated = operation.status == OperationStatus.feedback_submitted
        if activated:
            await _transition_operation(
                operation, OperationStatus.active, current_user, db,
                reason="Feedback approved — operation is now active"
            )

        # Notify the submitter
        await notify(
            db=db,
            user_id=feedback.submitted_by,
            type_="approved",
            title="Feedback Approved" + (" — Operation Active" if activated else ""),
            message=(
                f"Your truck feedback for operation {operation.operation_number} has been approved."
                + (" Operation is now active." if activated else "")
            ),
            priority="high",
            operation_id=operation_id,
            action_url=f"/operations/{operation_id}",
            channels=["in_app", "whatsapp"],
            wa_template="operation_active",
            wa_kwargs={"operation_number": operation.operation_number},
        )

        # Notify all finance managers — only when this approval actually activated
        # the operation (avoids duplicate "now active" alerts if it already advanced).
        if activated:
            from app.models.enums import UserRole as _UserRole
            fm_result = await db.execute(select(User).where(User.role == _UserRole.finance_manager))
            for fm in fm_result.scalars().all():
                await notify(
                    db=db, user_id=fm.id, type_="operation_active",
                    title=f"Operation Active — {operation.operation_number}",
                    message=f"Operation {operation.operation_number} is now active. Finance processing may be required.",
                    priority="normal",
                    operation_id=operation_id,
                )

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="APPROVE_FEEDBACK",
            entity_type="truck_feedback",
            entity_id=feedback.id,
            changes={"status": {"from": "pending", "to": "approved"}},
        )
        db.add(audit)

        await db.flush()
        await db.refresh(feedback)
        return feedback

    @staticmethod
    async def reject_feedback(
        operation_id: UUID,
        feedback_id: UUID,
        reason: str,
        current_user: User,
        db: AsyncSession,
    ) -> TruckFeedback:
        # Enforce minimum length in service layer as well
        if len(reason.strip()) < 10:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Rejection reason must be at least 10 characters",
            )

        operation = await _get_operation_or_404(operation_id, db)

        result = await db.execute(
            select(TruckFeedback).where(
                and_(
                    TruckFeedback.id == feedback_id,
                    TruckFeedback.operation_id == operation_id,
                )
            )
        )
        feedback = result.scalar_one_or_none()
        if not feedback:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback not found")

        if feedback.status not in (FeedbackStatus.pending, FeedbackStatus.resubmitted):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot reject feedback with status '{feedback.status.value}'",
            )

        feedback.status = FeedbackStatus.rejected
        feedback.reviewed_by = current_user.id
        feedback.reviewed_at = datetime.utcnow()
        feedback.rejection_reason = reason.strip()

        # Transition operation back to feedback_rejected
        await _transition_operation(
            operation, OperationStatus.feedback_rejected, current_user, db,
            reason=f"Feedback rejected: {reason}"
        )

        # HIGH priority notification to LO
        await notify(
            db=db,
            user_id=feedback.submitted_by,
            type_="rejected",
            title="Truck Feedback Rejected",
            message=f"Your truck feedback for operation {operation.operation_number} was rejected. Reason: {reason}",
            priority="high",
            operation_id=operation_id,
            action_url=f"/operations/{operation_id}/feedback/{feedback.id}",
        )

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="REJECT_FEEDBACK",
            entity_type="truck_feedback",
            entity_id=feedback.id,
            changes={"status": {"from": "pending", "to": "rejected"}, "reason": reason},
        )
        db.add(audit)

        await db.flush()
        await db.refresh(feedback)
        return feedback

    # ── Truck Profile ─────────────────────────────────────────────────────────

    @staticmethod
    async def get_truck(truck_id: UUID, db: AsyncSession) -> Truck:
        result = await db.execute(select(Truck).where(Truck.id == truck_id))
        truck = result.scalar_one_or_none()
        if not truck:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck not found")
        return truck

    @staticmethod
    async def get_truck_profile(truck_id: UUID, db: AsyncSession) -> Dict[str, Any]:
        """Full truck profile: truck details, computed stats, and operation history."""
        truck_result = await db.execute(select(Truck).where(Truck.id == truck_id))
        truck = truck_result.scalar_one_or_none()
        if not truck:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck not found")

        history_result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.operation),
                selectinload(TruckOperation.logger),
                selectinload(TruckOperation.destination_vessel),
            )
            .where(TruckOperation.truck_id == truck_id)
            .order_by(TruckOperation.created_at.desc())
        )
        truck_ops = list(history_result.scalars().all())

        total_loaded = sum((op.quantity_loaded_mt or Decimal("0")) for op in truck_ops)
        total_discharged = sum((op.quantity_discharged_mt or Decimal("0")) for op in truck_ops)
        total_variance = sum((op.variance_mt or Decimal("0")) for op in truck_ops)
        efficiency_pct: Optional[float] = None
        if total_loaded > 0:
            efficiency_pct = round(float(total_discharged / total_loaded) * 100, 1)

        stats = {
            "total_operations": len(truck_ops),
            "total_loaded_mt": str(total_loaded),
            "total_discharged_mt": str(total_discharged),
            "total_variance_mt": str(total_variance),
            "efficiency_pct": efficiency_pct,
        }

        history = []
        for top in truck_ops:
            op = top.operation
            user = top.logger
            history.append({
                "id": str(top.id),
                "operation_id": str(top.operation_id),
                "operation_number": op.operation_number if op else "—",
                "operation_type": op.type.value if op else "—",
                "operation_status": op.status.value if op else "—",
                "quantity_loaded_mt": str(top.quantity_loaded_mt) if top.quantity_loaded_mt is not None else None,
                "quantity_discharged_mt": str(top.quantity_discharged_mt) if top.quantity_discharged_mt is not None else None,
                "variance_mt": str(top.variance_mt) if top.variance_mt is not None else None,
                "loading_location": top.loading_location,
                "discharge_location": top.discharge_location,
                "destination_vessel_name": top.destination_vessel.vessel_name if top.destination_vessel else None,
                "transit_start_at": top.transit_start_at.isoformat() if top.transit_start_at else None,
                "transit_end_at": top.transit_end_at.isoformat() if top.transit_end_at else None,
                "discharge_start_at": top.discharge_start_at.isoformat() if top.discharge_start_at else None,
                "discharge_end_at": top.discharge_end_at.isoformat() if top.discharge_end_at else None,
                "status": top.status.value,
                "logged_by_id": str(top.logged_by),
                "logged_by_name": user.full_name if user else "Unknown",
                "logged_by_role": user.role.value if user else "unknown",
                "notes": top.notes,
                "created_at": top.created_at.isoformat(),
            })

        total_spillage = sum((op.spillage_mt or Decimal("0")) for op in truck_ops)
        stats["total_spillage_mt"] = str(total_spillage)

        # Enrich history with new telemetry fields
        for item, top in zip(history, truck_ops):
            item.update({
                "product_type": top.product_type,
                "quantity_remaining_mt": str(top.quantity_remaining_mt) if top.quantity_remaining_mt is not None else None,
                "spillage_mt": str(top.spillage_mt) if top.spillage_mt is not None else None,
                "temperature_celsius": str(top.temperature_celsius) if top.temperature_celsius is not None else None,
                "departed_parking_at": top.departed_parking_at.isoformat() if top.departed_parking_at else None,
                "arrived_loading_at": top.arrived_loading_at.isoformat() if top.arrived_loading_at else None,
                "departed_loading_at": top.departed_loading_at.isoformat() if top.departed_loading_at else None,
                "arrived_discharge_at": top.arrived_discharge_at.isoformat() if top.arrived_discharge_at else None,
                "events": top.events or [],
            })

        return {"truck": truck, "stats": stats, "history": history}

    # ── Truck telemetry milestone methods ──────────────────────────────────────

    @staticmethod
    async def _get_truck_op(operation_id: UUID, truck_op_id: UUID, db: AsyncSession) -> TruckOperation:
        result = await db.execute(
            select(TruckOperation)
            .options(
                selectinload(TruckOperation.truck),
                selectinload(TruckOperation.supervisor),
                selectinload(TruckOperation.safety_audits),
            )
            .where(
                and_(TruckOperation.id == truck_op_id, TruckOperation.operation_id == operation_id)
            )
        )
        top = result.scalar_one_or_none()
        if not top:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck operation not found")
        return top

    @staticmethod
    def _append_event(top: TruckOperation, event_type: str, description: str, user_id: UUID, ts: Optional[datetime] = None) -> None:
        events = list(top.events or [])
        events.append({
            "timestamp": (ts or datetime.utcnow()).isoformat(),
            "type": event_type,
            "description": description,
            "user_id": str(user_id),
        })
        top.events = events
        top.updated_at = datetime.utcnow()

    @staticmethod
    async def record_depart_parking(
        operation_id: UUID, truck_op_id: UUID, body, current_user: User, db: AsyncSession
    ) -> TruckOperation:
        from app.schemas.truck import TruckDepartParkingRequest
        top = await TruckService._get_truck_op(operation_id, truck_op_id, db)
        top.departed_parking_at = body.departed_parking_at or datetime.utcnow()
        if body.gps_lat:
            top.gps_start_lat = body.gps_lat
        if body.gps_lng:
            top.gps_start_lng = body.gps_lng
        if top.status == TruckOpStatus.pending:
            top.status = TruckOpStatus.in_transit
        TruckService._append_event(top, "depart_parking", body.notes or "Departed from parking/depot", current_user.id, top.departed_parking_at)
        await db.flush()
        return top

    @staticmethod
    async def record_arrived_loading(
        operation_id: UUID, truck_op_id: UUID, body, current_user: User, db: AsyncSession
    ) -> TruckOperation:
        top = await TruckService._get_truck_op(operation_id, truck_op_id, db)
        top.arrived_loading_at = body.arrived_loading_at or datetime.utcnow()
        if body.loading_location:
            top.loading_location = body.loading_location
        TruckService._append_event(top, "arrived_loading", body.notes or f"Arrived at loading point{': ' + top.loading_location if top.loading_location else ''}", current_user.id, top.arrived_loading_at)
        await db.flush()
        return top

    @staticmethod
    async def record_departed_loading(
        operation_id: UUID, truck_op_id: UUID, body, current_user: User, db: AsyncSession
    ) -> TruckOperation:
        top = await TruckService._get_truck_op(operation_id, truck_op_id, db)
        top.departed_loading_at = body.departed_loading_at or datetime.utcnow()
        top.transit_start_at = top.departed_loading_at
        top.quantity_loaded_mt = body.quantity_loaded_mt
        if body.product_type:
            top.product_type = body.product_type
        if top.status in (TruckOpStatus.pending, TruckOpStatus.in_transit):
            top.status = TruckOpStatus.loading
        TruckService._append_event(top, "departed_loading", f"Departed loading point with {body.quantity_loaded_mt} L loaded", current_user.id, top.departed_loading_at)
        await db.flush()
        return top

    @staticmethod
    async def record_arrived_discharge(
        operation_id: UUID, truck_op_id: UUID, body, current_user: User, db: AsyncSession
    ) -> TruckOperation:
        top = await TruckService._get_truck_op(operation_id, truck_op_id, db)
        top.arrived_discharge_at = body.arrived_discharge_at or datetime.utcnow()
        top.transit_end_at = top.arrived_discharge_at
        if body.discharge_location:
            top.discharge_location = body.discharge_location
        top.status = TruckOpStatus.arrived
        TruckService._append_event(top, "arrived_discharge", body.notes or f"Arrived at discharge location{': ' + top.discharge_location if top.discharge_location else ''}", current_user.id, top.arrived_discharge_at)
        await db.flush()
        return top

    @staticmethod
    async def record_custom_event(
        operation_id: UUID, truck_op_id: UUID, body, current_user: User, db: AsyncSession
    ) -> TruckOperation:
        top = await TruckService._get_truck_op(operation_id, truck_op_id, db)
        TruckService._append_event(top, body.event_type, body.description, current_user.id, body.timestamp)
        await db.flush()
        return top

    @staticmethod
    async def submit_operation_completion(
        operation_id: UUID, body, current_user: User, db: AsyncSession
    ) -> Operation:
        """Supervisor submits completion report → transitions operation to pending_completion."""
        operation = await _get_operation_or_404(operation_id, db)

        # Money-first flow: completion is submitted after payment is confirmed.
        # 'active' is accepted for backward compatibility (delivery-during-active).
        if operation.status not in (OperationStatus.active, OperationStatus.payment_confirmed):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Completion can only be submitted while the operation is Active or "
                    f"after payment is confirmed. Current status: {operation.status.value}"
                ),
            )

        try:
            StateMachine.validate_transition(operation.type, operation.status, OperationStatus.pending_completion, current_user.role)
        except StateMachineError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

        from_status = operation.status
        operation.status = OperationStatus.pending_completion
        operation.completion_notes = body.readiness_summary
        operation.updated_at = datetime.utcnow()

        db.add(OperationStatusHistory(
            operation_id=operation.id,
            from_status=from_status,
            to_status=OperationStatus.pending_completion,
            changed_by=current_user.id,
            reason=body.readiness_summary,
            metadata_={"submitted_by_role": current_user.role.value},
        ))

        # Notify all BMs
        bm_result = await db.execute(
            select(User).where(and_(User.role == UserRole.bunker_manager, User.is_active == True))
        )
        for bm in bm_result.scalars().all():
            await notify(
                db=db, user_id=bm.id, type_="completion_pending",
                title=f"Completion Report — {operation.operation_number}",
                message=f"{current_user.full_name} submitted a completion report for {operation.operation_number}: {body.readiness_summary[:100]}",
                priority="high",
                operation_id=operation.id,
                action_url=f"/operations/{operation.id}",
                channels=["in_app", "whatsapp"],
                wa_template="operation_update",
                wa_kwargs={"operation_number": operation.operation_number, "status": "Completion pending review"},
            )

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id,
            action="SUBMIT_COMPLETION", entity_type="operation", entity_id=operation.id,
            changes={"summary": body.readiness_summary},
        ))

        await db.flush()
        await db.refresh(operation)
        return operation
