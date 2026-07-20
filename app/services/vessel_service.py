from typing import List, Optional, Tuple
from datetime import datetime
from uuid import UUID
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from fastapi import HTTPException, status

from sqlalchemy.orm import selectinload
from app.models.vessel import Vessel
from app.models.bdn import BDN, RobEntry
from app.models.audit import AuditLog
from app.models.user import User
from app.models.operation import Operation
from app.models.truck import TruckOperation, Truck
from app.models.finance import PFI, Invoice
from app.models.document import Document
from app.models.enums import UserRole, RobEntryType
from app.schemas.vessel import VesselCreate, VesselUpdate, RobEntryCreate, RobSummaryOut, RobChartPoint
from app.services.notification_service import notify


class VesselService:

    @staticmethod
    async def list_vessels(db: AsyncSession) -> List[Vessel]:
        stmt = (
            select(Vessel)
            .where(Vessel.is_active == True)
            .order_by(Vessel.created_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_vessel(
        data: VesselCreate,
        current_user: User,
        db: AsyncSession,
    ) -> Vessel:
        # Check for duplicate IMO number
        if data.imo_number:
            existing = await db.execute(
                select(Vessel).where(Vessel.imo_number == data.imo_number)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Vessel with IMO number '{data.imo_number}' already exists",
                )

        vessel_kwargs = {
            "vessel_name": data.vessel_name,
            "imo_number": data.imo_number,
            "vessel_type": data.vessel_type,
            "flag_state": data.flag_state,
            "capacity_mt": data.capacity_mt,
            "current_location": data.current_location,
            "current_rob_mt": Decimal("0"),
            "is_active": True,
        }
        if data.rob_threshold_mt is not None:
            vessel_kwargs["rob_threshold_mt"] = data.rob_threshold_mt

        vessel = Vessel(**vessel_kwargs)
        db.add(vessel)
        await db.flush()

        audit = AuditLog(
            user_id=current_user.id,
            action="CREATE_VESSEL",
            entity_type="vessel",
            entity_id=vessel.id,
            changes={"vessel_name": data.vessel_name, "imo_number": data.imo_number},
        )
        db.add(audit)

        await db.flush()
        await db.refresh(vessel)
        return vessel

    @staticmethod
    async def update_vessel(
        vessel_id: UUID,
        data: VesselUpdate,
        current_user: User,
        db: AsyncSession,
    ) -> Vessel:
        result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
        vessel = result.scalar_one_or_none()
        if not vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")

        update_data = data.model_dump(exclude_unset=True)
        changes = {}
        for field, value in update_data.items():
            old_val = getattr(vessel, field, None)
            changes[field] = {"from": str(old_val), "to": str(value)}
            setattr(vessel, field, value)

        vessel.updated_at = datetime.utcnow()

        audit = AuditLog(
            user_id=current_user.id,
            action="UPDATE_VESSEL",
            entity_type="vessel",
            entity_id=vessel.id,
            changes=changes,
        )
        db.add(audit)

        await db.flush()
        await db.refresh(vessel)
        return vessel

    @staticmethod
    async def get_rob_ledger(
        vessel_id: UUID,
        page: int,
        per_page: int,
        db: AsyncSession,
    ) -> Tuple[List[RobEntry], int]:
        # Verify vessel exists
        vessel_result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
        if not vessel_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")

        count_stmt = (
            select(func.count())
            .select_from(RobEntry)
            .where(RobEntry.vessel_id == vessel_id)
        )
        count_result = await db.execute(count_stmt)
        total = count_result.scalar_one()

        offset = (page - 1) * per_page
        stmt = (
            select(RobEntry)
            .where(RobEntry.vessel_id == vessel_id)
            .order_by(RobEntry.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        result = await db.execute(stmt)
        entries = list(result.scalars().all())

        return entries, total

    @staticmethod
    async def record_rob_entry(
        vessel_id: UUID,
        data: RobEntryCreate,
        current_user: User,
        db: AsyncSession,
    ) -> RobEntry:
        # Lock vessel row for update
        from sqlalchemy import select
        vessel_result = await db.execute(
            select(Vessel).where(Vessel.id == vessel_id).with_for_update()
        )
        vessel = vessel_result.scalar_one_or_none()
        if not vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")

        rob_before = Decimal(str(vessel.current_rob_mt))

        # Calculate stored quantity and rob_after based on entry type
        if data.entry_type == RobEntryType.discharge:
            # Client sends positive value; negate server-side
            stored_quantity = -data.quantity_mt
            rob_after = rob_before + stored_quantity
            if rob_after < Decimal("0"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"ROB cannot go negative. Maximum discharge: {float(rob_before):.3f} L",
                )
        elif data.entry_type == RobEntryType.replenishment or data.entry_type == RobEntryType.initial:
            # Must be positive
            stored_quantity = data.quantity_mt
            rob_after = rob_before + stored_quantity
        else:
            # adjustment / correction — quantity_mt positive from schema, but allow subtraction
            # For adjustment/correction we accept as-is (schema enforces > 0; clients pass positive)
            # To allow negative adjustments, accept the sign as given
            stored_quantity = data.quantity_mt
            rob_after = rob_before + stored_quantity
            if rob_after < Decimal("0"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"ROB cannot go negative. Maximum adjustment: {float(rob_before):.3f} L",
                )

        # Create the immutable ROB entry
        entry = RobEntry(
            vessel_id=vessel_id,
            operation_id=data.operation_id,
            entry_type=data.entry_type,
            quantity_mt=stored_quantity,
            rob_before_mt=rob_before,
            rob_after_mt=rob_after,
            recorded_by=current_user.id,
            source_description=data.source_description,
            notes=data.notes,
        )
        db.add(entry)

        # Update vessel current ROB
        vessel.current_rob_mt = rob_after
        vessel.updated_at = datetime.utcnow()

        await db.flush()

        # ROB threshold alert
        if rob_after < Decimal(str(vessel.rob_threshold_mt)):
            # Notify BM and OS users
            for role in (UserRole.bunker_manager, UserRole.ops_supervisor):
                role_result = await db.execute(
                    select(User).where(User.role == role)
                )
                role_users = role_result.scalars().all()
                for user in role_users:
                    await notify(
                        db=db,
                        user_id=user.id,
                        type_="rob_alert",
                        title="ROB Below Threshold",
                        message=(
                            f"Vessel {vessel.vessel_name} ROB is now {float(rob_after):.3f} L, "
                            f"below threshold of {float(vessel.rob_threshold_mt):.3f} L"
                        ),
                        priority="urgent",
                        operation_id=data.operation_id,
                        action_url=f"/vessels/{vessel_id}/rob",
                        channels=["in_app", "whatsapp"],
                        wa_template="low_rob_alert",
                        wa_kwargs={
                            "vessel_name": vessel.vessel_name,
                            "current_rob": f"{float(rob_after):.1f}",
                            "threshold": f"{float(vessel.rob_threshold_mt):.1f}",
                        },
                    )

        # Audit log
        audit = AuditLog(
            user_id=current_user.id,
            action="RECORD_ROB_ENTRY",
            entity_type="rob_entry",
            entity_id=entry.id,
            changes={
                "entry_type": data.entry_type.value,
                "quantity_mt": str(stored_quantity),
                "rob_before_mt": str(rob_before),
                "rob_after_mt": str(rob_after),
            },
        )
        db.add(audit)

        await db.flush()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def get_rob_summary(
        vessel_id: UUID,
        db: AsyncSession,
    ) -> RobSummaryOut:
        vessel_result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
        vessel = vessel_result.scalar_one_or_none()
        if not vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")

        # Get 10 most recent entries
        stmt = (
            select(RobEntry)
            .where(RobEntry.vessel_id == vessel_id)
            .order_by(RobEntry.created_at.desc())
            .limit(10)
        )
        result = await db.execute(stmt)
        recent_entries = list(result.scalars().all())

        # Build simple trend data from recent entries (chronological)
        chart_data = [
            RobChartPoint(
                date=entry.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                rob_mt=float(entry.rob_after_mt),
            )
            for entry in reversed(recent_entries)
        ]

        current_rob = Decimal(str(vessel.current_rob_mt))
        threshold = Decimal(str(vessel.rob_threshold_mt))

        return RobSummaryOut(
            vessel_id=vessel.id,
            vessel_name=vessel.vessel_name,
            current_rob_mt=current_rob,
            rob_threshold_mt=threshold,
            below_threshold=current_rob < threshold,
            recent_entries=recent_entries,
            chart_data=chart_data,
        )


    @staticmethod
    async def get_vessel(vessel_id: UUID, db: AsyncSession) -> Vessel:
        result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
        vessel = result.scalar_one_or_none()
        if not vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")
        return vessel

    @staticmethod
    async def get_vessel_bdns(vessel_id: UUID, db: AsyncSession) -> list:
        """All BDNs for a vessel with actor names and operation info."""
        result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")

        bdn_result = await db.execute(
            select(BDN)
            .options(
                selectinload(BDN.operation),
                selectinload(BDN.generator),
                selectinload(BDN.reviewer),
            )
            .where(BDN.vessel_id == vessel_id)
            .order_by(BDN.delivery_date.desc())
        )
        bdns = list(bdn_result.scalars().all())

        out = []
        total_delivered = Decimal("0")
        for b in bdns:
            qty = b.quantity_delivered_mt or Decimal("0")
            total_delivered += qty
            out.append({
                "id": str(b.id),
                "bdn_number": b.bdn_number,
                "operation_id": str(b.operation_id),
                "operation_number": b.operation.operation_number if b.operation else "—",
                "status": b.status.value,
                "quantity_delivered_mt": str(qty),
                "product_type": b.product_type,
                "fuel_type": getattr(b, "fuel_type", None),
                "density": str(b.density) if getattr(b, "density", None) is not None else None,
                "temperature": str(b.temperature) if getattr(b, "temperature", None) is not None else None,
                "delivery_date": b.delivery_date.isoformat() if b.delivery_date else None,
                "generated_by_name": b.generator.full_name if b.generator else "Unknown",
                "generated_by_role": b.generator.role.value if b.generator else "unknown",
                "reviewed_by_name": b.reviewer.full_name if b.reviewer else None,
                "approved_at": b.approved_at.isoformat() if b.approved_at else None,
                "rejection_reason": b.rejection_reason,
                "notes": b.notes,
                "created_at": b.created_at.isoformat(),
            })

        return {"bdns": out, "total_delivered_mt": str(total_delivered), "total_count": len(bdns)}

    @staticmethod
    async def get_cargo_ledger(
        vessel_id: UUID,
        page: int,
        per_page: int,
        db: AsyncSession,
    ) -> dict:
        """Enriched ROB ledger with linked operation context for cargo tracking."""
        vessel_result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
        vessel = vessel_result.scalar_one_or_none()
        if not vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")

        count_stmt = select(func.count()).select_from(RobEntry).where(RobEntry.vessel_id == vessel_id)
        total = (await db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * per_page
        rob_stmt = (
            select(RobEntry)
            .options(
                selectinload(RobEntry.recorder),
                selectinload(RobEntry.operation).selectinload(Operation.truck_operations).selectinload(TruckOperation.truck),
                selectinload(RobEntry.operation).selectinload(Operation.bdns),
                selectinload(RobEntry.operation).selectinload(Operation.pfis),
                selectinload(RobEntry.operation).selectinload(Operation.invoices),
                selectinload(RobEntry.operation).selectinload(Operation.documents),
            )
            .where(RobEntry.vessel_id == vessel_id)
            .order_by(RobEntry.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        result = await db.execute(rob_stmt)
        entries = list(result.scalars().all())

        # Compute summary totals across ALL entries
        summary_stmt = select(
            func.sum(RobEntry.quantity_mt).filter(RobEntry.quantity_mt > 0).label("replenished"),
            func.sum(RobEntry.quantity_mt).filter(RobEntry.quantity_mt < 0).label("discharged"),
        ).where(RobEntry.vessel_id == vessel_id)
        summary_row = (await db.execute(summary_stmt)).one()

        out_entries = []
        for entry in entries:
            op = entry.operation
            op_data = None
            if op:
                trucks_data = []
                for truck_op in (op.truck_operations or []):
                    trucks_data.append({
                        "truck_id": str(truck_op.truck_id),
                        "truck_number": truck_op.truck.truck_number if truck_op.truck else "—",
                        "status": truck_op.status.value,
                        "quantity_loaded_mt": str(truck_op.quantity_loaded_mt) if truck_op.quantity_loaded_mt is not None else None,
                        "quantity_discharged_mt": str(truck_op.quantity_discharged_mt) if truck_op.quantity_discharged_mt is not None else None,
                        "variance_mt": str(truck_op.variance_mt) if truck_op.variance_mt is not None else None,
                        "loading_location": truck_op.loading_location,
                        "discharge_location": truck_op.discharge_location,
                    })

                bdns = op.bdns or []
                bdn_data = None
                if bdns:
                    b = bdns[0]
                    bdn_data = {
                        "id": str(b.id),
                        "bdn_number": b.bdn_number,
                        "status": b.status.value,
                        "quantity_delivered_mt": str(b.quantity_delivered_mt),
                        "delivery_date": b.delivery_date.isoformat() if b.delivery_date else None,
                    }

                pfis = op.pfis or []
                invoices = op.invoices or []
                finance_data = {
                    "pfi_status": pfis[0].status.value if pfis else None,
                    "pfi_amount": str(pfis[0].amount) if pfis else None,
                    "pfi_currency": pfis[0].currency if pfis else None,
                    "invoice_status": invoices[0].status.value if invoices else None,
                }

                documents = [d for d in (op.documents or []) if not d.is_deleted]
                products_total = sum((p.quantity_mt for p in op.products), Decimal("0")) if op.products else None
                op_data = {
                    "id": str(op.id),
                    "operation_number": op.operation_number,
                    "type": op.type.value,
                    "status": op.status.value,
                    "product_types": ", ".join(p.product_type for p in op.products) if op.products else None,
                    "expected_volume_mt": str(op.actual_volume_mt or products_total) if (op.actual_volume_mt is not None or products_total is not None) else None,
                    "actual_volume_mt": str(op.actual_volume_mt) if op.actual_volume_mt is not None else None,
                    "notes": op.notes,
                    "trucks": trucks_data,
                    "bdn": bdn_data,
                    "finance": finance_data,
                    "document_count": len(documents),
                }

            out_entries.append({
                "id": str(entry.id),
                "entry_type": entry.entry_type.value,
                "quantity_mt": str(entry.quantity_mt),
                "rob_before_mt": str(entry.rob_before_mt),
                "rob_after_mt": str(entry.rob_after_mt),
                "source_description": entry.source_description,
                "notes": entry.notes,
                "recorded_by_name": entry.recorder.full_name if entry.recorder else "Unknown",
                "recorded_by_role": entry.recorder.role.value if entry.recorder else "unknown",
                "created_at": entry.created_at.isoformat(),
                "operation": op_data,
            })

        return {
            "entries": out_entries,
            "total": total,
            "summary": {
                "total_replenishments_mt": str(summary_row.replenished or Decimal("0")),
                "total_discharges_mt": str(abs(summary_row.discharged or Decimal("0"))),
                "current_rob_mt": str(vessel.current_rob_mt),
                "capacity_mt": str(vessel.capacity_mt) if vessel.capacity_mt else None,
            },
        }
