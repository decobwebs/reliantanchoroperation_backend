"""Service for recording vessel-to-vessel discharge events with ROB ledger integration."""
from datetime import datetime
from uuid import UUID, uuid4
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from fastapi import HTTPException, status

from app.models.bdn import VesselDischargeEvent, RobEntry
from app.models.vessel import Vessel
from app.models.operation import Operation
from app.models.user import User
from app.models.enums import RobEntryType, OperationStatus
from app.schemas.truck import VesselDischargeEventCreate


class VesselDischargeService:

    @staticmethod
    async def create_event(
        operation_id: UUID,
        data: VesselDischargeEventCreate,
        current_user: User,
        db: AsyncSession,
    ) -> VesselDischargeEvent:
        # Validate operation exists
        op_result = await db.execute(
            select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
        )
        operation = op_result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        # Validate source vessel exists
        sv_result = await db.execute(select(Vessel).where(Vessel.id == data.source_vessel_id))
        source_vessel = sv_result.scalar_one_or_none()
        if not source_vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source vessel not found")

        # Derive product type from operation if not provided
        product = data.product_type or operation.product_type

        # Create ROB entry for the source vessel (discharge = negative)
        rob_before = source_vessel.current_rob_mt
        qty = abs(data.quantity_mt)
        rob_after = rob_before - qty

        rob_entry = RobEntry(
            vessel_id=source_vessel.id,
            operation_id=operation_id,
            entry_type=RobEntryType.discharge,
            quantity_mt=-qty,
            rob_before_mt=rob_before,
            rob_after_mt=rob_after,
            recorded_by=current_user.id,
            supervisor_id=current_user.id,
            source_vessel_id=None,
            spillage_mt=data.spillage_mt,
            temperature_celsius=data.temperature_celsius,
            source_description=f"Vessel discharge to {'vessel' if data.destination_vessel_id else 'client/shore'}",
            notes=data.notes,
        )
        db.add(rob_entry)

        # Update source vessel ROB
        source_vessel.current_rob_mt = rob_after
        source_vessel.updated_at = datetime.utcnow()

        await db.flush()

        # If destination vessel provided, record ROB replenishment there
        dest_rob_entry_id = None
        if data.destination_vessel_id:
            dv_result = await db.execute(select(Vessel).where(Vessel.id == data.destination_vessel_id))
            dest_vessel = dv_result.scalar_one_or_none()
            if dest_vessel:
                d_rob_before = dest_vessel.current_rob_mt
                d_rob_after = d_rob_before + qty
                dest_rob = RobEntry(
                    vessel_id=dest_vessel.id,
                    operation_id=operation_id,
                    entry_type=RobEntryType.replenishment,
                    quantity_mt=qty,
                    rob_before_mt=d_rob_before,
                    rob_after_mt=d_rob_after,
                    recorded_by=current_user.id,
                    supervisor_id=current_user.id,
                    source_vessel_id=source_vessel.id,
                    spillage_mt=data.spillage_mt,
                    temperature_celsius=data.temperature_celsius,
                    source_description=f"Received from vessel {source_vessel.vessel_name}",
                    notes=data.notes,
                )
                db.add(dest_rob)
                dest_vessel.current_rob_mt = d_rob_after
                dest_vessel.updated_at = datetime.utcnow()
                await db.flush()

        # Create the discharge event record
        event = VesselDischargeEvent(
            id=uuid4(),
            operation_id=operation_id,
            source_vessel_id=data.source_vessel_id,
            destination_vessel_id=data.destination_vessel_id,
            product_type=product,
            quantity_mt=qty,
            spillage_mt=data.spillage_mt,
            temperature_celsius=data.temperature_celsius,
            density=data.density,
            discharge_start_at=data.discharge_start_at,
            discharge_end_at=data.discharge_end_at,
            supervisor_id=current_user.id,
            rob_entry_id=rob_entry.id,
            notes=data.notes,
        )
        db.add(event)
        await db.flush()
        await db.refresh(event)

        # ROB low-threshold alert
        if source_vessel.rob_threshold_mt and rob_after <= source_vessel.rob_threshold_mt:
            from app.services.notification_service import notify
            from app.models.enums import UserRole
            bm_result = await db.execute(
                select(User).where(and_(User.role == UserRole.bunker_manager, User.is_active == True))
            )
            for bm in bm_result.scalars().all():
                await notify(
                    db=db, user_id=bm.id, type_="rob_alert",
                    title=f"Low ROB Alert — {source_vessel.vessel_name}",
                    message=f"{source_vessel.vessel_name} ROB is now {float(rob_after):.1f} L — below threshold of {float(source_vessel.rob_threshold_mt):.1f} L.",
                    priority="urgent",
                    operation_id=operation_id,
                    channels=["in_app", "whatsapp"],
                    wa_template="rob_alert",
                    wa_kwargs={"vessel": source_vessel.vessel_name, "rob": f"{float(rob_after):.1f}", "threshold": f"{float(source_vessel.rob_threshold_mt):.1f}"},
                )

        return event
